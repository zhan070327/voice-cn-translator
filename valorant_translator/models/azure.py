"""Azure Fast Transcription and Translator Text REST clients."""
from __future__ import annotations

from io import BytesIO
import json
import re
from urllib.parse import urlencode
from urllib.request import Request
from uuid import uuid4
import wave

import numpy as np

from valorant_translator.audio.capture import SAMPLE_RATE
from valorant_translator.models.online import JsonRequest, OnlineError
from valorant_translator.recognition.whisper import Recognition

SPEECH_LOCALES = {
    "en": "en-US", "ms": "ms-MY", "hi": "hi-IN", "ta": "ta-IN", "th": "th-TH",
    "ja": "ja-JP", "ko": "ko-KR", "vi": "vi-VN", "tl": "fil-PH", "id": "id-ID", "zh": "zh-CN",
}
APP_LANGUAGES = {locale.split("-")[0].lower(): code for code, locale in SPEECH_LOCALES.items()}
TRANSLATOR_LANGUAGES = {"zh": "zh-Hans", "tl": "fil"}


def valid_resource(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", value))


def _wav(audio) -> bytes:
    pcm = np.rint(np.clip(audio, -1, 1) * 32767).astype("<i2")
    output = BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
        stream.writeframes(pcm.tobytes())
    return output.getvalue()


class AzureSpeech:
    device = "online"

    def __init__(self, settings):
        self._resource = settings.azure_speech_resource.strip().lower()
        self._key = settings.azure_speech_key.strip()
        self._domain = "azure.cn" if settings.azure_cloud == "china" else "azure.com"
        self._send = JsonRequest(2.0)

    def transcribe_chinese_callout(self, audio):
        return self.transcribe(audio, "zh")

    def transcribe(self, audio, language):
        if not valid_resource(self._resource) or (language != "auto" and language not in SPEECH_LOCALES):
            raise OnlineError("unsupported speech configuration")
        locale = SPEECH_LOCALES.get(language)
        definition = json.dumps({"locales": [locale]} if locale else {})
        boundary = uuid4().hex
        body = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"definition\"\r\n\r\n"
            f"{definition}\r\n--{boundary}\r\n"
            'Content-Disposition: form-data; name="audio"; filename="speech.wav"\r\n'
            "Content-Type: audio/wav\r\n\r\n"
        ).encode() + _wav(audio) + f"\r\n--{boundary}--\r\n".encode()
        url = (f"https://{self._resource}.cognitiveservices.{self._domain}"
               "/speechtotext/transcriptions:transcribe?api-version=2025-10-15")
        payload = self._send(Request(url, body, headers={
            "Ocp-Apim-Subscription-Key": self._key,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }))
        try:
            phrases = payload["phrases"]
            if not isinstance(phrases, list):
                raise TypeError("Expected a phrase list")
            if not phrases:
                return None
            text = " ".join(phrase["text"].strip() for phrase in phrases).strip()
            detected = phrases[0]["locale"].split("-")[0].lower() if language == "auto" else None
            code = APP_LANGUAGES[detected] if detected is not None else language
        except (KeyError, ValueError, TypeError, AttributeError, IndexError):
            raise OnlineError("invalid speech result or unsupported detected locale") from None
        if not text or not any(char.isalnum() for char in text):
            return None
        # Azure does not report language confidence in this response.
        return Recognition(text, code, None)


class AzureTranslator:
    device = "online"

    def __init__(self, settings):
        self._region = settings.azure_translator_region.strip().lower()
        self._key = settings.azure_translator_key.strip()
        self._host = ("api.translator.azure.cn" if settings.azure_cloud == "china"
                      else "api.cognitive.microsofttranslator.com")
        self._send = JsonRequest(max(0.1, min(2.0, settings.translation_timeout / 2)))

    def translate(self, source, language, target="zh"):
        params = urlencode({
            "api-version": "3.0", "from": TRANSLATOR_LANGUAGES.get(language, language),
            "to": TRANSLATOR_LANGUAGES.get(target, target),
        })
        if not valid_resource(self._region):
            raise OnlineError("unsupported translation region")
        headers = {
            "Ocp-Apim-Subscription-Key": self._key,
            "Content-Type": "application/json; charset=utf-8",
        }
        if self._region != "global":
            headers["Ocp-Apim-Subscription-Region"] = self._region
        body = json.dumps([{"Text": source}], ensure_ascii=False).encode("utf-8")
        result = self._send(Request(f"https://{self._host}/translate?{params}",
                                    body, headers=headers))
        try:
            translated = result[0]["translations"][0]["text"].strip()
            if translated:
                return translated
        except (KeyError, TypeError, AttributeError, IndexError):
            pass
        raise OnlineError("invalid translation result")

    def translate_to_english_details(self, source):
        translation = self.translate(source, "zh", "en")
        return translation, translation, ""
