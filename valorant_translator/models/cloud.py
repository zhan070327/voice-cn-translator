"""SameFreq Cloud API client and speech/translation adapters."""
from __future__ import annotations

from dataclasses import replace
import json
import logging
from io import BytesIO
from threading import Lock
from time import monotonic
from urllib.parse import urlencode, urlsplit
from urllib.request import Request
import wave

import numpy as np

from valorant_translator.audio.capture import SAMPLE_RATE
from valorant_translator.models.online import JsonRequest, OnlineError, RequestRejected
from valorant_translator.recognition.whisper import Recognition

LOG = logging.getLogger(__name__)
_SESSION_LOCK = Lock()
_BLOCKED = {"unauthenticated", "quota_exhausted", "account_blocked"}
_CODES = _BLOCKED | {"email_in_use", "invalid_input", "rate_limited", "unavailable"}


class CloudError(OnlineError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _wav(audio) -> bytes:
    pcm = np.rint(np.clip(audio, -1, 1) * 32767).astype("<i2")
    output = BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
        stream.writeframes(pcm.tobytes())
    return output.getvalue()


class CloudClient:
    def __init__(self, settings, *, save_session=None):
        self.settings = settings
        self._save_session = save_session
        self._speech = JsonRequest(2.0)
        self._translation = JsonRequest(max(.1, min(2.0, settings.translation_timeout / 2)))
        self._account = JsonRequest(6.0)
        self._entitlement = JsonRequest(6.0)
        self._plans = JsonRequest(6.0)
        self._order = JsonRequest(12.0)
        self._refresh = JsonRequest(1.0)
        self._state_lock = Lock()
        self._disabled = False
        self._blocked = None
        self._retry_at = 0.0
        self.on_blocked = lambda _code: None

    def _url(self, path: str) -> str:
        base = self.settings.cloud_url.rstrip("/")
        parsed = urlsplit(base)
        local = parsed.hostname in ("localhost", "127.0.0.1", "::1")
        if (not parsed.hostname or parsed.username is not None or parsed.query or parsed.fragment
                or not (parsed.scheme == "https" or (parsed.scheme == "http" and local))):
            raise CloudError("unavailable")
        return base + "/v1" + path

    def _request(self, send, path, payload=None, *, token="", audio=None, deadline=None):
        remaining = deadline - monotonic() if deadline is not None else None
        if remaining is not None and remaining <= 0:
            raise CloudError("unavailable")
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        body = audio
        if audio is not None:
            headers["Content-Type"] = "audio/wav"
        elif payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload, ensure_ascii=False).encode()
        try:
            request = Request(self._url(path), body, headers=headers)
            return send(request) if remaining is None else send(request, timeout=remaining)
        except RequestRejected as exc:
            code = exc.code
            if code not in _CODES:
                code = {401: "unauthenticated", 429: "rate_limited", 422: "invalid_input"}.get(
                    exc.status, "unavailable")
            raise CloudError(code) from None
        except CloudError:
            raise
        except (OnlineError, ValueError):
            raise CloudError("unavailable") from None

    def _session(self, access, refresh):
        self.settings.cloud_access_token = access
        self.settings.cloud_refresh_token = refresh
        if self._save_session is not None:
            try:
                self._save_session()
            except OSError:
                LOG.warning("Could not persist SameFreq Cloud session")

    def _set_tokens(self, data):
        if not isinstance(data, dict) or not all(
                isinstance(data.get(key), str) and data[key] for key in ("access_token", "refresh_token")):
            raise CloudError("unavailable")
        self._session(data["access_token"], data["refresh_token"])

    def _auth(self, action, email, password):
        with _SESSION_LOCK:
            self._set_tokens(self._request(self._account, "/auth/" + action, {
                "email": email, "password": password,
            }))
            self._blocked = None
            self._retry_at = 0.0

    def register(self, email, password):
        self._auth("register", email, password)

    def login(self, email, password):
        self._auth("login", email, password)

    def logout(self):
        with _SESSION_LOCK:
            token = self.settings.cloud_refresh_token
            try:
                if token:
                    self._request(self._account, "/auth/logout", {"refresh_token": token})
            finally:
                self._session("", "")

    def disable_online(self):
        with self._state_lock:
            self._disabled = True

    def _check_online(self):
        with self._state_lock:
            if self._disabled:
                raise CloudError("unavailable")
            if self._blocked:
                raise CloudError(self._blocked)
            if monotonic() < self._retry_at:
                raise CloudError("unavailable")

    def _authorized(self, send, path, payload=None, *, audio=None, runtime=False):
        deadline = monotonic() + send.timeout if runtime else None

        def issue(token):
            if runtime:
                self._check_online()
            return self._request(send, path, payload, token=token, audio=audio, deadline=deadline)

        token = self.settings.cloud_access_token
        if token:
            try:
                return issue(token)
            except CloudError as exc:
                if exc.code != "unauthenticated":
                    raise
        remaining = max(0.0, deadline - monotonic()) if deadline is not None else 6.0
        if not _SESSION_LOCK.acquire(timeout=remaining):
            raise CloudError("unavailable")
        try:
            if token == self.settings.cloud_access_token:
                refresh = self.settings.cloud_refresh_token
                if not refresh:
                    raise CloudError("unauthenticated")
                try:
                    self._set_tokens(self._request(self._refresh, "/auth/refresh", {"refresh_token": refresh},
                                                   deadline=deadline))
                except CloudError as exc:
                    if exc.code == "unauthenticated":
                        self._session("", "")
                    raise
            token = self.settings.cloud_access_token
        finally:
            _SESSION_LOCK.release()
        if not token:
            raise CloudError("unauthenticated")
        return issue(token)

    def entitlement(self, *, runtime=False):
        data = self._authorized(self._speech if runtime else self._entitlement, "/entitlement", runtime=runtime)
        if (not isinstance(data, dict) or type(data.get("trial_remaining_ms")) is not int
                or data["trial_remaining_ms"] < 0 or type(data.get("subscribed")) is not bool
                or type(data.get("online_allowed")) is not bool):
            raise CloudError("unavailable")
        return data

    def plans(self):
        data = self._authorized(self._plans, "/plans")
        if not isinstance(data, list) or not all(
                isinstance(plan, dict) and isinstance(plan.get("id"), str) and isinstance(plan.get("name"), str)
                and type(plan.get("price_cents")) is int and plan["price_cents"] > 0 for plan in data):
            raise CloudError("unavailable")
        return data

    def create_order(self, plan_id, channel):
        data = self._authorized(self._order, "/orders", {"plan_id": plan_id, "channel": channel})
        if not isinstance(data, dict) or not isinstance(data.get("pay_url"), str):
            raise CloudError("unavailable")
        return data

    def _inference(self, send, path, payload=None, *, audio=None):
        self._check_online()
        try:
            return self._authorized(send, path, payload, audio=audio, runtime=True)
        except CloudError as exc:
            notify = False
            with self._state_lock:
                if not self._disabled:
                    if exc.code in _BLOCKED and not self._blocked:
                        self._blocked, notify = exc.code, True
                    if exc.code in {"unavailable", "rate_limited"}:
                        self._retry_at = monotonic() + 5
            if notify:
                self.on_blocked(exc.code)
            raise

    def transcribe(self, audio, language):
        return self._inference(self._speech, "/speech?" + urlencode({"language": language}), audio=_wav(audio))

    def translate(self, text, source, target):
        return self._inference(self._translation, "/translate", {"text": text, "source": source, "target": target})


class CloudASR:
    device = "online"

    def __init__(self, client: CloudClient):
        self._client = client

    def transcribe_chinese_callout(self, audio):
        return self.transcribe(audio, "zh")

    def transcribe(self, audio, language):
        data = self._client.transcribe(audio, language)
        if isinstance(data, dict):
            text, code = data.get("text"), data.get("language")
            if text == "":
                return None
            if isinstance(text, str) and isinstance(code, str):
                return Recognition(text, code, None)
        raise CloudError("unavailable")


class CloudTranslation:
    device = "online"

    def __init__(self, client: CloudClient):
        self._client = client

    def translate(self, source, language, target="zh"):
        data = self._client.translate(source, language, target)
        if isinstance(data, dict) and isinstance(data.get("text"), str) and data["text"].strip():
            return data["text"].strip()
        raise CloudError("unavailable")

    def translate_to_english_details(self, source):
        translation = self.translate(source, "zh", "en")
        return translation, translation, ""


def ready_engine_settings(settings, client: CloudClient, on_blocked, on_status):
    if settings.speed_mode != "extreme":
        return settings
    client.on_blocked = on_blocked
    try:
        entitlement = client.entitlement(runtime=True)
        if not entitlement["online_allowed"]:
            code = "account_blocked" if entitlement.get("blocked") else "quota_exhausted"
            raise CloudError(code)
        return settings
    except CloudError as exc:
        LOG.warning("SameFreq Cloud entitlement unavailable: %s", exc.code)
        if exc.code in _BLOCKED:
            on_blocked(exc.code)
        else:
            on_status("在线服务暂不可用，已继续使用本地翻译。")
        local = replace(settings)
        local.select_speed_mode("balanced")
        return local
