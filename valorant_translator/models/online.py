"""Keep transport deadlines and local fallback outside the audio pipeline."""
from __future__ import annotations

from concurrent.futures import Future
import json
import logging
from threading import Lock, Thread
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, build_opener

LOG = logging.getLogger(__name__)
MAX_RESPONSE = 1024 * 1024


class OnlineError(RuntimeError):
    """A safe description of a request failure, without headers or response bodies."""


class RequestRejected(OnlineError):
    def __init__(self, status: int, code: str = ""):
        self.status = status
        self.code = code
        super().__init__(f"HTTP {status}")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class JsonRequest:
    def __init__(self, timeout: float):
        self.timeout = timeout
        self._opener = build_opener(_NoRedirect())
        self._pending: Future | None = None
        self._admission = Lock()

    def __call__(self, request, *, timeout=None):
        timeout = self.timeout if timeout is None else min(timeout, self.timeout)
        with self._admission:
            if self._pending and not self._pending.done():
                raise OnlineError("previous request still pending")
            result = self._pending = Future()

        def send():
            try:
                with self._opener.open(request, timeout=timeout) as response:
                    payload = response.read(MAX_RESPONSE + 1)
                if len(payload) > MAX_RESPONSE:
                    raise OnlineError("response too large")
                result.set_result(json.loads(payload))
            except HTTPError as exc:
                code = ""
                try:
                    payload = json.loads(exc.read(4096))
                    if isinstance(payload, dict) and isinstance(payload.get("code"), str):
                        code = payload["code"]
                except (OSError, ValueError):
                    pass
                finally:
                    exc.close()
                result.set_exception(RequestRejected(exc.code, code))
            except OnlineError as exc:
                result.set_exception(exc)
            except (URLError, OSError, ValueError, TypeError):
                result.set_exception(OnlineError("transport or response error"))
            except Exception:
                result.set_exception(OnlineError("request failed"))

        Thread(target=send, daemon=True, name="online-request").start()
        try:
            return result.result(timeout=timeout)
        except TimeoutError:
            raise OnlineError("deadline exceeded") from None


class _Fallback:
    def __init__(self, online, make_local, stage: str):
        self._online = online
        self._make_local = make_local
        self._local = None
        self._stage = stage
        self._active = None
        self.glossary = None
        self.languages = None
        self.on_rejection = None
        self.on_fallback = None
        self.fallback_seconds = 0.0
        self._notified = False

    @property
    def device(self):
        return self._active.device if self._active is not None else "pending"

    @property
    def path(self):
        return getattr(self._active, "path", None)

    def _run(self, method, *args):
        self.fallback_seconds = 0.0
        started = monotonic()
        try:
            result = getattr(self._online, method)(*args)
        except OnlineError as exc:
            self.fallback_seconds = monotonic() - started
            if not self._notified:
                self._notified = True
                LOG.warning("Online %s fell back to local: %s", self._stage, exc)
                if self.on_fallback is not None:
                    self.on_fallback(self._stage)
        else:
            self._active = self._online
            return result

        if self._local is None:
            self._local = self._make_local()
            self.fallback_seconds = monotonic() - started
        self._active = self._local
        if self.glossary is not None:
            self._local.glossary = self.glossary
        if self.languages is not None:
            self._local.languages = self.languages
        if self._stage == "asr":
            self._local.on_rejection = self.on_rejection
        return getattr(self._local, method)(*args)


class FallbackASR(_Fallback):
    def __init__(self, online, make_local):
        super().__init__(online, make_local, "asr")

    def transcribe(self, audio, language):
        return self._run("transcribe", audio, language)

    def transcribe_chinese_callout(self, audio):
        return self._run("transcribe_chinese_callout", audio)


class FallbackTranslation(_Fallback):
    def __init__(self, online, make_local):
        super().__init__(online, make_local, "translation")

    def translate(self, source, language, target="zh"):
        return self._run("translate", source, language, target)

    def translate_to_english_details(self, source):
        return self._run("translate_to_english_details", source)
