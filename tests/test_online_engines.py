from concurrent.futures import Future
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
from valorant_translator.models.cloud import CloudASR, CloudClient, CloudTranslation
from valorant_translator.models.online import JsonRequest, OnlineError, RequestRejected
from valorant_translator.models.providers import default_model_manager
from valorant_translator.recognition.whisper import Recognition
from valorant_translator.ui.texts import tr


def online_settings():
    settings = config.Settings(
        cloud_url="http://localhost:8000", cloud_access_token="test-access-token",
        cloud_refresh_token="test-refresh-token",
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
    assert settings.cloud_access_token not in repr(settings)
    assert settings.cloud_refresh_token not in repr(settings)
    path.write_text('{"speed_mode":"extreme","asr_provider":"azure_speech","azure_speech_key":"old"}',
                    encoding="utf-8")
    recovered = config.Settings.load()
    assert recovered.speed_mode == "balanced"
    assert recovered.asr_provider == config.LOCAL_ASR
    assert recovered.translation_model == config.LOCAL_TRANSLATION
    assert "azure_" not in path.read_text()

    settings.speed_mode = "balanced"
    manager = default_model_manager(lambda _: None, lambda _: None)
    with pytest.raises(ValueError, match="极速"):
        manager.create_asr(settings)
    with pytest.raises(ValueError, match="极速"):
        manager.create_translation(settings)


def test_cloud_contracts_and_token_refresh(monkeypatch):
    captured = []

    def reply(_client, request, **_kwargs):
        captured.append(request)
        path = urlparse(request.full_url).path
        if path == "/v1/auth/refresh":
            assert json.loads(request.data) == {"refresh_token": "test-refresh-token"}
            return {"access_token": "new-access", "refresh_token": "new-refresh"}
        if request.get_header("Authorization") == "Bearer test-access-token":
            raise RequestRejected(401)
        if path == "/v1/speech":
            language = parse_qs(urlparse(request.full_url).query)["language"][0]
            return {"text": "Reloading.", "language": "zh" if language == "zh" else "en"}
        return {"text": "我在换弹。"}

    monkeypatch.setattr(JsonRequest, "__call__", reply)
    settings = online_settings()
    saved = []
    client = CloudClient(settings, save_session=lambda: saved.append(settings.cloud_refresh_token))
    speech, translator = CloudASR(client), CloudTranslation(client)
    audio = np.array([0.0, 0.5, -0.5], dtype=np.float32)
    assert speech.transcribe(audio, "auto") == Recognition("Reloading.", "en", None)
    assert speech.transcribe_chinese_callout(audio).language == "zh"
    assert translator.translate("Reloading.", "en") == "我在换弹。"
    assert saved == ["new-refresh"]

    first, refresh, retry, chinese, translation = captured
    assert first.full_url == retry.full_url == "http://localhost:8000/v1/speech?language=auto"
    assert chinese.full_url.endswith("?language=zh")
    assert refresh.get_header("Authorization") is None
    assert translation.get_header("Authorization") == "Bearer new-access"
    assert json.loads(translation.data) == {"text": "Reloading.", "source": "en", "target": "zh"}
    assert retry.get_header("Content-type") == "audio/wav"
    with wave.open(io.BytesIO(retry.data)) as wav:
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

    def reply(_client, request, **_kwargs):
        if request.full_url.endswith("/v1/entitlement"):
            return {"trial_remaining_ms": 5000, "subscribed": False, "online_allowed": True}
        auth_headers.append(request.get_header("Authorization"))
        if not using_cloud:
            raise OnlineError("deadline exceeded")
        if "/v1/speech" in request.full_url:
            return {"language": "en", "text": "Wait near the other garage until I reload."}
        return {"text": "在另一个车库附近等我换弹。"}

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
    client = CloudClient(settings)
    manager = default_model_manager(lambda _: LocalASR(), lambda _: LocalMT(), cloud_client=client)
    results, statuses, runtimes, modes = [], [], [], []
    done = Event()

    def received(result):
        results.append(result)
        done.set()

    worker = pipeline.Pipeline(settings, statuses.append, lambda _: None, received,
                               on_runtime=runtimes.append, models=manager, cloud_client=client, on_effective_mode=modes.append)
    worker.start()
    try:
        for using_cloud in (True, False):
            done.clear()
            now = time.monotonic()
            worker.enqueue(SpeechChunk(np.ones(8000, np.float32), now - .5, now))
            assert done.wait(3), statuses
        assert [item.chinese for item in results] == ["在另一个车库附近等我换弹。", "车库门旁边有新烟。"]
        assert results[0].confidence is None
        route = tr("engine.route_online")
        assert any(f"识别 {route} · 翻译 {route}" in item for item in runtimes)
        assert "识别 CPU · 翻译 CPU" in runtimes[-1]
        assert modes[-1] == "local_fallback"
        assert statuses.count(tr("engine.fallback_asr")) == 1
        assert statuses.count(tr("engine.fallback_translation")) == 0
        assert len(auth_headers) == 3
        assert not any(key in caplog.text + "".join(statuses) for key in auth_headers)
    finally:
        worker.stop()


@pytest.mark.parametrize("signed_in", [False, True])
def test_preflight_blocks_upload_and_uses_both_local_factories(monkeypatch, signed_in):
    class Capture:
        def __init__(self, *_):
            pass

        def start(self):
            pass

        def stop(self):
            pass

    class Local:
        device = "cpu"

    def reply(_client, request, **_kwargs):
        assert request.full_url.endswith("/v1/entitlement")
        return {"trial_remaining_ms": 0, "subscribed": False, "online_allowed": False}

    monkeypatch.setattr(JsonRequest, "__call__", reply)
    monkeypatch.setattr(pipeline, "AudioCapture", Capture)
    settings = online_settings()
    if not signed_in:
        settings.cloud_access_token = settings.cloud_refresh_token = ""
    blocked, modes = [], []
    created = Event()

    def local_translation(local_settings):
        assert local_settings.translation_model == config.LOCAL_TRANSLATION
        created.set()
        return Local()

    manager = default_model_manager(lambda _: Local(), local_translation)
    worker = pipeline.Pipeline(settings, lambda _: None, lambda _: None, lambda _: None,
                               models=manager, on_cloud_blocked=blocked.append, on_effective_mode=modes.append)
    worker.start()
    try:
        assert created.wait(2)
    finally:
        worker.stop()
    assert blocked == ["quota_exhausted" if signed_in else "unauthenticated"]
    assert modes == ["local_fallback"]


def test_stopped_cloud_client_does_not_send_audio_or_text(monkeypatch):
    def unexpected(*_args, **_kwargs):
        pytest.fail("an inactive session attempted an online request")

    monkeypatch.setattr(JsonRequest, "__call__", unexpected)
    client = CloudClient(online_settings())
    client.disable_online()
    with pytest.raises(OnlineError, match="unavailable"):
        client.transcribe(np.ones(1600, np.float32), "en")
    with pytest.raises(OnlineError, match="unavailable"):
        client.translate("Reload.", "en", "zh")
