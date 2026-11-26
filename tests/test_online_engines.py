from concurrent.futures import Future
from email import policy
from email.parser import BytesParser
import io
import json
from threading import Event
import time
from urllib.parse import parse_qs, urlparse
from urllib.request import Request
import wave

import numpy as np
import pytest

from valorant_translator import config, pipeline
from valorant_translator.audio.segmenter import SpeechChunk
from valorant_translator.models.azure import AzureSpeech, AzureTranslator
from valorant_translator.models.online import JsonRequest, OnlineError
from valorant_translator.models.providers import default_model_manager
from valorant_translator.recognition.whisper import Recognition
from valorant_translator.ui.texts import tr


def online_settings():
    settings = config.Settings(
        azure_cloud="china", azure_speech_resource="test-speech", azure_speech_key="test-speech-key",
        azure_translator_region="chinaeast2", azure_translator_key="test-translation-key",
    )
    assert settings.select_speed_mode("extreme")
    return settings


def test_preset_consent_and_legacy_config(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", path)
    path.write_text('{"whisper_model":"medium","fast_mode":false}', encoding="utf-8")
    loaded = config.Settings.load()
    assert loaded.speed_mode == "balanced"
    assert loaded.whisper_model == "medium" and not loaded.fast_mode
    assert not loaded.select_speed_mode("extreme")
    assert loaded.asr_provider == config.LOCAL_ASR
    assert loaded.select_speed_mode("balanced")
    assert (loaded.whisper_model, loaded.fast_mode) == ("tiny", True)

    loaded.asr_correction = loaded.official_lines = loaded.game_terms = False
    assert loaded.select_speed_mode("precise")
    assert (loaded.whisper_model, loaded.fast_mode) == ("small", False)
    assert loaded.asr_correction and loaded.official_lines and loaded.game_terms

    settings = online_settings()
    settings.save()
    assert config.Settings.load() == settings
    assert settings.azure_speech_key not in repr(settings)
    assert settings.azure_translator_key not in repr(settings)
    settings.azure_speech_key = ""
    settings.save()
    recovered = config.Settings.load()
    assert recovered.speed_mode == "balanced"
    assert recovered.asr_provider == config.LOCAL_ASR
    assert recovered.translation_model == config.LOCAL_TRANSLATION

    settings = online_settings()
    settings.speed_mode = "balanced"
    manager = default_model_manager(lambda _: None, lambda _: None)
    with pytest.raises(ValueError, match=tr("engine.credentials_required")):
        manager.create_asr(settings)
    with pytest.raises(ValueError, match=tr("engine.credentials_required")):
        manager.create_translation(settings)


def test_azure_request_contracts_and_detection(monkeypatch):
    captured = []

    def reply(_client, request):
        captured.append(request)
        if "/speechtotext/" in request.full_url:
            return {"phrases": [{"locale": "en-US", "text": "Reloading."}]}
        return [{"translations": [{"text": "我在换弹。", "to": "zh-Hans"}]}]

    monkeypatch.setattr(JsonRequest, "__call__", reply)
    settings = online_settings()
    speech, translator = AzureSpeech(settings), AzureTranslator(settings)
    audio = np.array([0.0, 0.5, -0.5], dtype=np.float32)
    assert speech.transcribe(audio, "auto") == Recognition("Reloading.", "en", None)
    assert speech.transcribe_chinese_callout(audio).language == "zh"
    assert translator.translate("Reloading.", "en") == "我在换弹。"

    first, second, third = captured
    assert urlparse(first.full_url).hostname == "test-speech.cognitiveservices.azure.cn"
    assert urlparse(third.full_url).hostname == "api.translator.azure.cn"
    assert dict(third.header_items())["Ocp-apim-subscription-region"] == "chinaeast2"
    assert parse_qs(urlparse(third.full_url).query) == {"api-version": ["3.0"], "from": ["en"], "to": ["zh-Hans"]}
    assert json.loads(third.data) == [{"Text": "Reloading."}]
    for request, definition in ((first, {}), (second, {"locales": ["zh-CN"]})):
        mime = b"Content-Type: " + dict(request.header_items())["Content-type"].encode()
        parsed = BytesParser(policy=policy.default).parsebytes(mime + b"\r\n\r\n" + request.data)
        config_part, audio_part = list(parsed.iter_parts())
        assert json.loads(config_part.get_payload(decode=True)) == definition
        assert audio_part.get_param("name", header="content-disposition") == "audio"
        with wave.open(io.BytesIO(audio_part.get_payload(decode=True))) as wav:
            assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()) == (1, 2, 16000, 3)


def test_deadline_returns_despite_blocking_reader_and_does_not_queue_another_request():
    client = JsonRequest(.05)
    release = Event()
    requests = []

    class Response(io.BytesIO):
        def read(self, *args):
            release.wait(3)
            return super().read(*args)

    class Opener:
        def open(self, request, *, timeout):
            requests.append((request, timeout))
            return Response(b"{}")

    client._opener = Opener()
    request = Request("https://example.invalid")
    try:
        start = time.monotonic()
        with pytest.raises(OnlineError, match="deadline exceeded"):
            client(request)
        assert time.monotonic() - start < .5
        with pytest.raises(OnlineError, match="previous request"):
            client(request)
        assert requests == [(request, .05)]
    finally:
        release.set()
        pending: Future = client._pending
        assert pending.result(timeout=1) == {}


def test_pipeline_delivers_online_and_same_utterance_local_fallback(monkeypatch, caplog):
    using_cloud = True
    auth_headers = []

    def reply(_client, request):
        auth_headers.append(dict(request.header_items())["Ocp-apim-subscription-key"])
        if not using_cloud:
            raise OnlineError("deadline exceeded")
        if "/speechtotext/" in request.full_url:
            return {"phrases": [{"locale": "en-US", "text": "Wait near the other garage until I reload."}]}
        return [{"translations": [{"text": "在另一个车库附近等我换弹。"}]}]

    class Capture:
        def __init__(self, *_):
            pass

        def start(self):
            pass

        def stop(self):
            pass

    class LocalASR:
        device = "cpu"

        def transcribe(self, _audio, _language):
            return Recognition("There is fresh smoke beside the garage door.", "en", .9)

    class LocalMT:
        device = "cpu"

        def translate(self, _text, _source, _target="zh"):
            return "车库门旁边有新烟。"

    monkeypatch.setattr(JsonRequest, "__call__", reply)
    monkeypatch.setattr(pipeline, "AudioCapture", Capture)
    settings = online_settings()
    manager = default_model_manager(lambda _: LocalASR(), lambda _: LocalMT())
    results, statuses, runtimes = [], [], []
    done = Event()

    def received(result):
        results.append(result)
        done.set()

    worker = pipeline.Pipeline(settings, statuses.append, lambda _: None, received,
                               on_runtime=runtimes.append, models=manager)
    worker.start()
    try:
        for using_cloud in (True, False):
            done.clear()
            now = time.monotonic()
            worker.enqueue(SpeechChunk(np.ones(8000, np.float32), now - .5, now))
            assert done.wait(3), statuses
        assert [item.chinese for item in results] == ["在另一个车库附近等我换弹。", "车库门旁边有新烟。"]
        assert results[0].confidence is None
        assert "识别 Azure 在线 · 翻译 Azure 在线" in runtimes[-3]
        assert "识别 CPU · 翻译 CPU" in runtimes[-1]
        for stage in ("asr", "translation"):
            assert statuses.count(tr(f"engine.fallback_{stage}")) == 1
        assert len(auth_headers) == 4
        assert not any(key in caplog.text + "".join(statuses) for key in auth_headers)
    finally:
        worker.stop()
