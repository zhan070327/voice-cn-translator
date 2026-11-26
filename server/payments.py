"""Domestic payment adapters. The order store only receives verified notices."""
from base64 import b64decode, b64encode
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
import re
import time
from typing import Protocol
from urllib.parse import parse_qsl, urlsplit
from uuid import uuid4

import httpx
from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .store import SHANGHAI


class PaymentError(Exception):
    pass


@dataclass(frozen=True)
class Notice:
    order_id: str
    transaction_id: str
    cents: int


class Channel(Protocol):
    async def create(self, order_id: str, name: str, cents: int) -> str: ...
    def verify(self, headers, body: bytes, now: int) -> Notice: ...


def _key(env, name, *, private=False):
    try:
        pem = env[name].replace("\\n", "\n").encode()
        key = (serialization.load_pem_private_key(pem, password=None) if private
               else serialization.load_pem_public_key(pem))
        key_type = rsa.RSAPrivateKey if private else rsa.RSAPublicKey
        if not isinstance(key, key_type) or key.key_size < 2048:
            raise ValueError
        return key
    except (ValueError, TypeError, KeyError):
        raise ValueError(f"Invalid {name}: expected an RSA PEM key with at least 2048 bits") from None


def _sign(key, message):
    return b64encode(key.sign(message, padding.PKCS1v15(), hashes.SHA256())).decode()


def _verify(key, message, signature):
    key.verify(b64decode(signature, validate=True), message, padding.PKCS1v15(), hashes.SHA256())


def _json(data):
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _notice(order, transaction, cents):
    if (not isinstance(order, str) or not re.fullmatch(r"[a-f0-9]{32}", order)
            or not isinstance(transaction, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", transaction)
            or type(cents) is not int or not 0 < cents <= 100_000_000):
        raise ValueError
    return Notice(order, transaction, cents)


def _qr(value, schemes):
    if (not isinstance(value, str) or not 0 < len(value) <= 512
            or urlsplit(value).scheme not in schemes):
        raise PaymentError
    return value


class WeChat:
    path = "/v3/pay/transactions/native"

    def __init__(self, env, client):
        self.client = client
        self.app_id = env["WECHAT_APP_ID"]
        self.merchant_id = env["WECHAT_MERCHANT_ID"]
        self.serial = env["WECHAT_MERCHANT_SERIAL"]
        self.platform_serial = env["WECHAT_PLATFORM_SERIAL"]
        self.private = _key(env, "WECHAT_MERCHANT_PRIVATE_KEY", private=True)
        self.public = _key(env, "WECHAT_PLATFORM_PUBLIC_KEY")
        self.cipher = AESGCM(env["WECHAT_API_V3_KEY"].encode())
        self.notify_url = env["PUBLIC_BASE_URL"] + "/v1/payments/wechat/callback"

    def _verify_message(self, headers, body, now):
        timestamp, nonce = headers["wechatpay-timestamp"], headers["wechatpay-nonce"]
        if headers["wechatpay-serial"] != self.platform_serial or abs(now - int(timestamp)) > 300:
            raise ValueError
        _verify(self.public, f"{timestamp}\n{nonce}\n".encode() + body + b"\n", headers["wechatpay-signature"])

    async def create(self, order_id, name, cents):
        data = _json({
            "appid": self.app_id, "mchid": self.merchant_id, "description": name,
            "out_trade_no": order_id, "notify_url": self.notify_url, "amount": {"total": cents, "currency": "CNY"},
        })
        timestamp, nonce = int(time.time()), uuid4().hex
        signature = _sign(self.private, f"POST\n{self.path}\n{timestamp}\n{nonce}\n{data}\n".encode())
        auth = (f'WECHATPAY2-SHA256-RSA2048 mchid="{self.merchant_id}",nonce_str="{nonce}",'
                f'timestamp="{timestamp}",serial_no="{self.serial}",signature="{signature}"')
        try:
            response = await self.client.post(
                "https://api.mch.weixin.qq.com" + self.path, content=data.encode(), timeout=8,
                headers={"Authorization": auth, "Wechatpay-Serial": self.platform_serial,
                         "Content-Type": "application/json", "Accept": "application/json"},
            )
            response.raise_for_status()
            self._verify_message(response.headers, response.content, int(time.time()))
            return _qr(response.json()["code_url"], {"weixin"})
        except (httpx.HTTPError, ValueError, KeyError, TypeError, InvalidSignature):
            raise PaymentError from None

    def verify(self, headers, body, now):
        try:
            self._verify_message(headers, body, now)
            event = json.loads(body)
            resource = event["resource"]
            if event["event_type"] != "TRANSACTION.SUCCESS" or resource["algorithm"] != "AEAD_AES_256_GCM":
                raise ValueError
            aad = resource.get("associated_data", "").encode()
            data = json.loads(self.cipher.decrypt(resource["nonce"].encode(),
                                                 b64decode(resource["ciphertext"], validate=True), aad))
            if (data["mchid"] != self.merchant_id or data["appid"] != self.app_id
                    or data["trade_state"] != "SUCCESS" or data["amount"]["currency"] != "CNY"):
                raise ValueError
            return _notice(data["out_trade_no"], data["transaction_id"], data["amount"]["total"])
        except (KeyError, ValueError, TypeError, AttributeError, InvalidTag, InvalidSignature):
            raise PaymentError from None


def _ali_message(params):
    return "&".join(f"{key}={value}" for key, value in sorted(params.items())).encode()


def _ali_response(raw):
    """Read the exact JSON substring signed by the gateway."""
    decoder = json.JSONDecoder()
    remaining = raw.strip()
    if not remaining.startswith("{"):
        raise ValueError
    remaining = remaining[1:].lstrip()
    fields = {}
    while not remaining.startswith("}"):
        name, key_end = decoder.raw_decode(remaining)
        separator = remaining[key_end:].lstrip()
        if not separator.startswith(":") or name in fields:
            raise ValueError
        value = separator[1:].lstrip()
        post, length = decoder.raw_decode(value)
        fields[name] = (post, value[:length])
        remaining = value[length:].lstrip()
        if remaining.startswith(","):
            remaining = remaining[1:].lstrip()
        elif not remaining.startswith("}"):
            raise ValueError
    return fields


class Alipay:
    def __init__(self, env, client):
        self.client = client
        self.app_id = env["ALIPAY_APP_ID"]
        self.seller_id = env["ALIPAY_SELLER_ID"]
        self.private = _key(env, "ALIPAY_APP_PRIVATE_KEY", private=True)
        self.public = _key(env, "ALIPAY_PUBLIC_KEY")
        self.notify_url = env["PUBLIC_BASE_URL"] + "/v1/payments/alipay/callback"

    async def create(self, order_id, name, cents):
        params = {
            "app_id": self.app_id, "method": "alipay.trade.precreate", "charset": "utf-8",
            "sign_type": "RSA2", "timestamp": datetime.now(SHANGHAI).strftime("%Y-%m-%d %H:%M:%S"),
            "version": "1.0", "format": "JSON", "notify_url": self.notify_url,
            "biz_content": _json({"out_trade_no": order_id, "subject": name,
                                  "total_amount": f"{cents // 100}.{cents % 100:02d}"}),
        }
        params["sign"] = _sign(self.private, _ali_message(params))
        try:
            response = await self.client.post("https://openapi.alipay.com/gateway.do", data=params, timeout=8)
            response.raise_for_status()
            fields = _ali_response(response.content.decode("utf-8"))
            data, raw = fields["alipay_trade_precreate_response"]
            _verify(self.public, raw.encode(), fields["sign"][0])
            if data["code"] != "10000" or data["out_trade_no"] != order_id:
                raise ValueError
            return _qr(data["qr_code"], {"https"})
        except (httpx.HTTPError, ValueError, KeyError, TypeError, InvalidSignature):
            raise PaymentError from None

    def verify(self, headers, body, now):
        try:
            pairs = parse_qsl(body.decode("utf-8"), keep_blank_values=True, strict_parsing=True,
                              encoding="utf-8", errors="strict", max_num_fields=64)
            data = dict(pairs)
            if len(data) != len(pairs) or data.pop("sign_type") != "RSA2":
                raise ValueError
            signature = data.pop("sign")
            _verify(self.public, _ali_message(data), signature)
            if (data["app_id"] != self.app_id or data["seller_id"] != self.seller_id
                    or data["trade_status"] not in ("TRADE_SUCCESS", "TRADE_FINISHED")):
                raise ValueError
            amount = data["total_amount"]
            if not re.fullmatch(r"[0-9]{1,8}(?:\.[0-9]{1,2})?", amount):
                raise ValueError
            return _notice(data["out_trade_no"], data["trade_no"], int(Decimal(amount) * 100))
        except (KeyError, ValueError, TypeError, InvalidOperation, InvalidSignature):
            raise PaymentError from None
