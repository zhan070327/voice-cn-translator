"""Azure REST adapters. Azure credentials and raw upstream errors stay on the VM."""
from io import BytesIO
import json
import logging
import wave

import httpx

LOG = logging.getLogger(__name__)
SAMPLE_RATE = 16000
MAX_AUDIO_BYTES = 32 * SAMPLE_RATE * 2 + 8192
SPEECH_LOCALES = {
    "en": "en-US", "zh": "zh-CN", "ms": "ms-MY", "hi": "hi-IN", "ta": "ta-IN",
    "th": "th-TH", "ja": "ja-JP", "ko": "ko-KR", "vi": "vi-VN", "tl": "fil-PH", "id": "id-ID",
}
APP_LANGUAGES = {locale.split("-")[0].lower(): code for code, locale in SPEECH_LOCALES.items()}
TRANSLATOR_LANGUAGES = {"zh": "zh-Hans", "tl": "fil"}


class UpstreamError(Exception):
    pass


def validated_audio(payload):
    """Read the PCM that will actually be sent, and recreate the WAV envelope."""
    try:
        with wave.open(BytesIO(payload), "rb") as stream:
            if (stream.getnchannels(), stream.getsampwidth(), stream.getframerate()) != (1, 2, SAMPLE_RATE):
                raise ValueError
            frames = stream.getnframes()
            if not 0 < frames <= 32 * SAMPLE_RATE:
                raise ValueError
            audio = stream.readframes(frames)
            if len(audio) != frames * 2:
                raise ValueError
        output = BytesIO()
        with wave.open(output, "wb") as stream:
            stream.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
            stream.writeframes(audio)
        return output.getvalue(), frames
    except (EOFError, wave.Error, ValueError):
        raise ValueError("Expected 16 kHz mono signed 16-bit PCM WAV") from None


class Azure:
    def __init__(self, settings, client):
        secrets = settings.secrets
        self.client = client
        self.timeout = settings.upstream_timeout
        domain = "azure.cn" if settings.azure_cloud == "china" else "azure.com"
        resource = secrets["AZURE_SPEECH_RESOURCE"]
        self.speech_url = settings.speech_url or (
            f"https://{resource}.cognitiveservices.{domain}/speechtotext/transcriptions:transcribe")
        self.speech_version = settings.speech_api_version
        self.speech_headers = {"Ocp-Apim-Subscription-Key": secrets["AZURE_SPEECH_KEY"]}
        self.translation_url = (
            "https://api.translator.azure.cn/translate" if settings.azure_cloud == "china"
            else "https://api.cognitive.microsofttranslator.com/translate"
        )
        self.translation_headers = {"Ocp-Apim-Subscription-Key": secrets["AZURE_TRANSLATOR_KEY"]}
        region = secrets["AZURE_TRANSLATOR_REGION"]
        if region != "global":
            self.translation_headers["Ocp-Apim-Subscription-Region"] = region

    async def _post(self, stage, url, **kwargs):
        try:
            response = await self.client.post(url, **kwargs, timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            LOG.warning("Azure %s failed with HTTP %s", stage, exc.response.status_code)
            raise UpstreamError from None
        except (httpx.RequestError, ValueError):
            LOG.warning("Azure %s request failed", stage)
            raise UpstreamError from None

    async def transcribe(self, audio, language):
        locale = SPEECH_LOCALES.get(language)
        payload = await self._post(
            "speech", self.speech_url, params={"api-version": self.speech_version},
            headers=self.speech_headers, data={"definition": json.dumps({"locales": [locale]} if locale else {})},
            files={"audio": ("speech.wav", audio, "audio/wav")},
        )
        try:
            phrases = payload["phrases"]
            if not isinstance(phrases, list):
                raise TypeError
            if not phrases:
                return {"text": "", "language": language}
            text = " ".join(phrase["text"].strip() for phrase in phrases).strip()
            code = language if locale else APP_LANGUAGES[phrases[0]["locale"].split("-")[0].lower()]
            return {"text": text if any(c.isalnum() for c in text) else "", "language": code}
        except (KeyError, IndexError, AttributeError, TypeError):
            raise UpstreamError from None

    async def translate(self, text, source, target):
        payload = await self._post(
            "translation", self.translation_url,
            params={"api-version": "3.0", "from": TRANSLATOR_LANGUAGES.get(source, source),
                    "to": TRANSLATOR_LANGUAGES.get(target, target)},
            headers=self.translation_headers, json=[{"Text": text}],
        )
        try:
            translated = payload[0]["translations"][0]["text"].strip()
            if translated:
                return {"text": translated}
        except (KeyError, IndexError, AttributeError, TypeError):
            pass
        raise UpstreamError
