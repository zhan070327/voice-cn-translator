from base64 import b64decode, b64encode
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from io import BytesIO
import json
import secrets
import time
from urllib.parse import parse_qs, urlencode
import wave

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi.testclient import TestClient
import httpx
import jwt
import pytest

from server.app import create_app
from server.payments import _ali_message, _sign
from server.settings import REQUIRED, Plan, Settings
from server.store import Denied, Store, TRIAL_MS

EMAIL = "tester@example.invalid"
PASSWORD = "only-a-test-password"
PLAN = Plan("monthly", "Test fixture plan", 321)


def sign(key, message):
    return b64encode(key.sign(message, padding.PKCS1v15(), hashes.SHA256())).decode()


def verify(key, message, signature):
    key.public_key().verify(b64decode(signature), message, padding.PKCS1v15(), hashes.SHA256())


@pytest.fixture(scope="module")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def env(key):
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    public = key.public_key().public_bytes(serialization.Encoding.PEM,
                                           serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return dict(
        JWT_SECRET=secrets.token_hex(32), AZURE_SPEECH_KEY=secrets.token_hex(16),
        AZURE_SPEECH_RESOURCE="example", AZURE_TRANSLATOR_KEY=secrets.token_hex(16),
        AZURE_TRANSLATOR_REGION="chinaeast2", PUBLIC_BASE_URL="https://payments.example.invalid",
        WECHAT_APP_ID="wx-test", WECHAT_MERCHANT_ID="merchant-test", WECHAT_MERCHANT_SERIAL="merchant-serial",
        WECHAT_MERCHANT_PRIVATE_KEY=private, WECHAT_API_V3_KEY=secrets.token_hex(16),
        WECHAT_PLATFORM_SERIAL="platform-serial", WECHAT_PLATFORM_PUBLIC_KEY=public,
        ALIPAY_APP_ID="ali-test", ALIPAY_SELLER_ID="seller-test",
        ALIPAY_APP_PRIVATE_KEY=private, ALIPAY_PUBLIC_KEY=public,
    )


@pytest.fixture
def cfg(tmp_path, env):
    return Settings(tmp_path / "samefreq.sqlite3", env, {"monthly": PLAN},
                    auth_per_minute=50, text_ms_per_character=1000)


def signed_wechat(body, cfg, key):
    timestamp = str(int(time.time()))
    nonce = "test-nonce"
    return {
        "Wechatpay-Timestamp": timestamp, "Wechatpay-Nonce": nonce,
        "Wechatpay-Serial": cfg.secrets["WECHAT_PLATFORM_SERIAL"],
        "Wechatpay-Signature": sign(key, f"{timestamp}\n{nonce}\n".encode() + body + b"\n"),
    }


@pytest.fixture
def gateway(cfg, key):
    requests = []
    fail = set()

    def reply(request):
        requests.append(request)
        host = request.url.host
        if host in fail:
            return httpx.Response(503)
        if host == "example.cognitiveservices.azure.cn":
            assert request.url.path == "/speechtotext/transcriptions:transcribe"
            assert request.url.params["api-version"] == cfg.speech_api_version
            assert request.headers["Ocp-Apim-Subscription-Key"] == cfg.secrets["AZURE_SPEECH_KEY"]
            assert b'name="audio"; filename="speech.wav"' in request.content
            assert b"RIFF" in request.content
            return httpx.Response(200, json={"phrases": [{"text": "Reloading.", "locale": "en-US"}]})
        if host == "api.translator.azure.cn":
            assert request.url.params["to"] == "zh-Hans"
            assert request.headers["Ocp-Apim-Subscription-Key"] == cfg.secrets["AZURE_TRANSLATOR_KEY"]
            assert request.headers["Ocp-Apim-Subscription-Region"] == "chinaeast2"
            return httpx.Response(200, json=[{"translations": [{"text": "换弹。"}]}])
        if host == "api.mch.weixin.qq.com":
            assert request.url.path == "/v3/pay/transactions/native"
            fields = dict(item.split("=", 1) for item in request.headers["authorization"].split(" ", 1)[1].split(","))
            fields = {k: v.strip('"') for k, v in fields.items()}
            message = f"POST\n{request.url.path}\n{fields['timestamp']}\n{fields['nonce_str']}\n".encode()
            verify(key, message + request.content + b"\n", fields["signature"])
            data = json.loads(request.content)
            assert data["amount"] == {"total": PLAN.price_cents, "currency": "CNY"}
            assert data["notify_url"].endswith("/v1/payments/wechat/callback")
            content = b'{"code_url":"weixin://wxpay/bizpayurl?pr=fixture"}'
            return httpx.Response(200, content=content, headers=signed_wechat(content, cfg, key))
        if host == "openapi.alipay.com":
            params = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            signature = params.pop("sign")
            verify(key, _ali_message(params), signature)
            assert params["method"] == "alipay.trade.precreate"
            biz = json.loads(params["biz_content"])
            assert biz["total_amount"] == f"{PLAN.price_cents / 100:.2f}"
            raw = json.dumps({"code": "10000", "out_trade_no": biz["out_trade_no"],
                              "qr_code": "https://qr.alipay.com/fixture"}, indent=1)
            return httpx.Response(200, content='{"alipay_trade_precreate_response":' + raw
                                  + ',"sign":"' + sign(key, raw.encode()) + '"}')
        raise AssertionError(f"Unexpected upstream host {host}")

    return httpx.MockTransport(reply), requests, fail


@pytest.fixture
def client(cfg, gateway):
    with TestClient(create_app(cfg, transport=gateway[0])) as http:
        yield http


def register(client):
    res = client.post("/v1/auth/register", json={"email": EMAIL, "password": PASSWORD})
    assert res.status_code == 201, res.text
    return res.json()


def headers(tokens):
    return {"Authorization": "Bearer " + tokens["access_token"]}


def entitlement(client, authorization):
    result = client.get("/v1/entitlement", headers=authorization)
    assert result.status_code == 200
    return result.json()


def wav(frames=1600):
    output = BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        stream.writeframes(b"\0\0" * frames)
    return output.getvalue()


def translate(client, authorization, text="reload"):
    return client.post("/v1/translate", headers=authorization,
                       json={"text": text, "source": "en", "target": "zh"})


def notice(channel, cfg, key, order, *, cents=PLAN.price_cents):
    if channel == "wechat":
        payload = {
            "mchid": cfg.secrets["WECHAT_MERCHANT_ID"], "appid": cfg.secrets["WECHAT_APP_ID"],
            "trade_state": "SUCCESS", "out_trade_no": order, "transaction_id": "wx-transaction",
            "amount": {"total": cents, "currency": "CNY"},
        }
        nonce, aad = b"nonce-bytes!", b"transaction"
        encrypted = AESGCM(cfg.secrets["WECHAT_API_V3_KEY"].encode()).encrypt(nonce, json.dumps(payload).encode(), aad)
        body = json.dumps({"event_type": "TRANSACTION.SUCCESS", "resource": {
            "algorithm": "AEAD_AES_256_GCM", "nonce": nonce.decode(),
            "associated_data": aad.decode(), "ciphertext": b64encode(encrypted).decode(),
        }}).encode()
        return body, signed_wechat(body, cfg, key), "Wechatpay-Signature"
    params = {
        "app_id": cfg.secrets["ALIPAY_APP_ID"], "seller_id": cfg.secrets["ALIPAY_SELLER_ID"],
        "trade_status": "TRADE_SUCCESS", "out_trade_no": order, "trade_no": "alipay-transaction",
        "total_amount": f"{cents / 100:.2f}",
    }
    body = urlencode(dict(params, sign_type="RSA2", sign=sign(key, _ali_message(params)))).encode()
    return body, {"Content-Type": "application/x-www-form-urlencoded"}, None


def test_configuration_reports_missing_variables(monkeypatch, env):
    for name in REQUIRED:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError, match="JWT_SECRET.*ALIPAY_PUBLIC_KEY"):
        create_app()
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    cfg = Settings.from_env()
    assert cfg.plans == {}
    monkeypatch.setenv("WECHAT_API_V3_KEY", "invalid")
    with pytest.raises(ValueError, match="WECHAT_API_V3_KEY"):
        create_app()


def test_account_login_jwt_refresh_revocation(client, cfg, caplog):
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/v1/entitlement").json() == {"code": "unauthenticated"}
    tokens = register(client)
    assert entitlement(client, headers(tokens))["trial_remaining_ms"] == TRIAL_MS
    stored = Store(cfg.database).credentials(EMAIL)["password"]
    assert PASSWORD not in stored
    assert client.post("/v1/auth/register", json={"email": EMAIL.upper(), "password": PASSWORD}).status_code == 409
    assert client.post("/v1/auth/login", json={"email": EMAIL, "password": "wrong"}).status_code == 401
    auth = client.post("/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert auth.status_code == 200
    access = jwt.decode(tokens["access_token"], cfg.secrets["JWT_SECRET"], algorithms=["HS256"])
    access["exp"] = int(time.time()) - 1
    expired = dict(tokens, access_token=jwt.encode(access, cfg.secrets["JWT_SECRET"], algorithm="HS256"))
    assert client.get("/v1/entitlement", headers=headers(expired)).status_code == 401
    res = client.post("/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert res.status_code == 200
    assert res.json()["refresh_token"] != tokens["refresh_token"]
    assert client.post("/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401
    assert client.post("/v1/auth/logout", json={"refresh_token": res.json()["refresh_token"]}).status_code == 200
    assert client.get("/v1/subscription", headers=headers(res.json())).status_code == 401
    assert PASSWORD not in caplog.text and tokens["refresh_token"] not in caplog.text


def test_measures_pcm_and_characters_ignores_failed_requests(client, cfg, gateway):
    auth = headers(register(client))
    res = client.post("/v1/speech?language=auto&duration=0", content=wav(),
                      headers=dict(auth, **{"Content-Type": "audio/wav"}))
    assert res.json() == {"text": "Reloading.", "language": "en"}
    assert entitlement(client, auth)["trial_remaining_ms"] == TRIAL_MS - 100
    assert translate(client, auth, " go  ").json() == {"text": "换弹。"}
    expected = TRIAL_MS - 100 - 2 * cfg.text_ms_per_character
    assert entitlement(client, auth)["trial_remaining_ms"] == expected
    assert client.post("/v1/speech", content=wav()[:-1], headers=dict(auth, **{"Content-Type": "audio/wav"})).status_code == 422
    assert client.post("/v1/translate", headers=auth,
                       json={"text": "reload", "source": "en", "target": "zh", "usage": 0}).status_code == 422
    gateway[2].add("api.translator.azure.cn")
    assert translate(client, auth).status_code == 503
    assert entitlement(client, auth)["trial_remaining_ms"] == expected
    with Store(cfg.database).transaction(write=False) as db:
        assert [tuple(row) for row in db.execute("SELECT kind,units FROM usage ORDER BY rowid")] == [
            ("asr", 1600), ("translation", 2)]


@pytest.mark.parametrize("channel", ["wechat", "alipay"])
def test_exhaust_five_hours_then_pay_idempotently(client, cfg, key, gateway, channel):
    auth = headers(register(client))
    text = "a" * 2000
    for _ in range(9):
        assert translate(client, auth, text).status_code == 200
    assert entitlement(client, auth)["trial_remaining_ms"] == 0
    request_count = len(gateway[1])
    exhausted = translate(client, auth)
    assert exhausted.status_code == 402 and exhausted.json() == {"code": "quota_exhausted"}
    assert len(gateway[1]) == request_count

    assert client.get("/v1/plans", headers=auth).json()[0]["price_cents"] == PLAN.price_cents
    checkout = client.post("/v1/orders", headers=auth, json={"plan_id": "monthly", "channel": channel})
    assert checkout.status_code == 201, checkout.text
    assert b64decode(checkout.json()["qr_png_base64"]).startswith(b"\x89PNG\r\n\x1a\n")
    order = checkout.json()["id"]
    path = "/v1/payments/" + channel + "/callback"
    body, callback_headers, signature_key = notice(channel, cfg, key, order)
    corrupted_headers = dict(callback_headers)
    if signature_key:
        corrupted_headers[signature_key] = _sign(key, b"unrelated")
        corrupt = body
    else:
        corrupt = body.replace(b"TRADE_SUCCESS", b"TRADE_FINISHED")
    assert client.post(path, content=corrupt, headers=corrupted_headers).status_code == 400
    mismatched, mismatched_headers, _ = notice(channel, cfg, key, order, cents=PLAN.price_cents + 1)
    assert client.post(path, content=mismatched, headers=mismatched_headers).status_code == 400
    assert not client.get("/v1/subscription", headers=auth).json()["subscribed"]
    assert client.get("/v1/orders/" + order, headers=auth).json()["paid_at"] is None
    assert client.post(path, content=body, headers=callback_headers).status_code == (204 if channel == "wechat" else 200)
    status = client.get("/v1/subscription", headers=auth).json()
    assert status["subscribed"] and status["subscription_expires_at"] > time.time()
    assert client.post(path, content=body, headers=callback_headers).is_success
    assert client.get("/v1/subscription", headers=auth).json() == status
    assert translate(client, auth).is_success
    assert entitlement(client, auth)["trial_remaining_ms"] == 0
    with Store(cfg.database).transaction(write=False) as db:
        assert db.execute("SELECT count(*) FROM subscription_periods").fetchone()[0] == 1
        assert db.execute("SELECT period FROM usage ORDER BY rowid DESC LIMIT 1").fetchone()[0] == order


def test_reservation_is_atomic_and_limit_blocks_upstream(cfg, gateway):
    cfg = replace(cfg, online_per_minute=1)
    with TestClient(create_app(cfg, transport=gateway[0])) as client:
        auth = headers(register(client))
        assert translate(client, auth).is_success
        assert translate(client, auth).status_code == 429
        assert len(gateway[1]) == 1
    store = Store(cfg.database)
    account = store.credentials(EMAIL)["id"]
    with store.transaction() as db:
        db.execute("UPDATE accounts SET trial_ms=100 WHERE id=?", (account,))

    def allocate(_):
        try:
            return store.reserve(account, "asr", 1600, 100, int(time.time()))
        except Denied as exc:
            assert exc.code == "quota_exhausted"
            return None

    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(allocate, range(2)))
    assert sum(value is not None for value in outcomes) == 1
    with store.transaction() as db:
        db.execute("UPDATE accounts SET blocked=1 WHERE id=?", (account,))
    with pytest.raises(Denied, match="account_blocked"):
        store.reserve(account, "asr", 1, 1, int(time.time()))


def test_subscription_preserves_unused_trial_time(client, cfg, key):
    auth = headers(register(client))
    assert translate(client, auth).is_success
    remaining = entitlement(client, auth)["trial_remaining_ms"]
    assert 0 < remaining < TRIAL_MS
    order = client.post("/v1/orders", headers=auth, json={"plan_id": "monthly", "channel": "alipay"}).json()["id"]
    body, callback_headers, _ = notice("alipay", cfg, key, order)
    assert client.post("/v1/payments/alipay/callback", content=body, headers=callback_headers).is_success
    assert translate(client, auth).is_success
    assert entitlement(client, auth)["trial_remaining_ms"] == remaining
