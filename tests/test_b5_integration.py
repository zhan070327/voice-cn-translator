from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import numpy as np
from opencc import OpenCC
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from valorant_translator import config, pipeline
from valorant_translator.audio.capture import AudioDevice
from valorant_translator.audio.segmenter import SpeechChunk
from valorant_translator.history import HistoryStore
from valorant_translator.models import download
from valorant_translator.recognition.whisper import LocalWhisper
from valorant_translator.translation import glossary
from valorant_translator.translation.m2m import LocalM2M, M2M_FILES
from valorant_translator.ui import app as ui


def _profile(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(ui, "MODEL_DIR", tmp_path / "models")
    monkeypatch.setattr(ui.MainWindow, "_start_hotkey", lambda self: None)
    monkeypatch.setattr(ui, "load_channel", lambda: None)
    monkeypatch.setattr(ui, "list_devices", lambda: [
        AudioDevice("mic", "Microphone", False), AudioDevice("team", "Loopback", True),
    ])
    monkeypatch.setattr(glossary, "CUSTOM_GLOSSARY_FILE", tmp_path / "glossary_custom.json")


def _wait_for(predicate):
    deadline = time.monotonic() + 4
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(10)
    assert predicate()


def test_overlapping_team_and_microphone_keep_separate_routes_and_feedback(qapp, monkeypatch, tmp_path):
    _profile(monkeypatch, tmp_path)
    monkeypatch.setattr(ui.MainWindow, "_models_ready", lambda self: True)
    monkeypatch.setattr(ui.MainWindow, "_torch_version_ok", staticmethod(lambda: True))
    monkeypatch.setattr(ui.QMessageBox, "information", lambda *_: None)
    config.Settings(microphone_id="mic", compute_device="cpu", subtitle_seconds=30).save()

    team_started = threading.Event()
    release_team = threading.Event()
    mic_stopped = threading.Event()
    mic_recognized = threading.Event()
    team_translation_started = threading.Event()
    release_translation = threading.Event()
    inputs = []
    calls_lock = threading.Lock()

    class Decoder:
        def transcribe(self, audio, *, language, hotwords, **_):
            assert calls_lock.acquire(blocking=False), "Whisper used concurrently"
            try:
                inputs.append((language, hotwords))
                if language == "zh":
                    assert team_translation_started.wait(5)
                    text = "我们去B点集合等队友一起行动。"
                    mic_recognized.set()
                elif audio[0] > 0:
                    team_started.set()
                    assert release_team.wait(5)
                    text = "We could try a new plan next round."
                else:
                    text = "Aye"
                segment = SimpleNamespace(text=text, no_speech_prob=0.01, avg_logprob=-0.2)
                return [segment], SimpleNamespace(language=language or "en", language_probability=0.93)
            finally:
                calls_lock.release()

    class Capture:
        def __init__(self, device, on_audio, *_):
            self.device, self.on_audio = device, on_audio

        def start(self):
            if self.device == "mic":
                self.on_audio(np.ones(8000, dtype=np.float32), time.monotonic())

        def stop(self):
            if self.device == "mic":
                mic_stopped.set()

    asr = LocalWhisper.__new__(LocalWhisper)
    asr.device, asr.model = "cpu", Decoder()
    mt = LocalM2M.__new__(LocalM2M)
    mt.device, mt.converter = "cpu", OpenCC("t2s")

    def translate_team(source, language, target):
        assert (source, language, target) == ("We could try a new plan next round.", "en", "zh")
        team_translation_started.set()
        assert release_translation.wait(5)
        return "我们下回合可以换个打法。"

    monkeypatch.setattr(mt, "_translate_with_fallback", translate_team)
    monkeypatch.setattr(pipeline, "LocalWhisper", lambda *_: asr)
    monkeypatch.setattr(pipeline, "LocalM2M", lambda *_: mt)
    monkeypatch.setattr(pipeline, "AudioCapture", Capture)
    window = ui.MainWindow()
    window.talk.capture_factory = Capture

    try:
        window.start_translation()
        _wait_for(lambda: window.pipeline.model_session is not None)
        now = time.monotonic()
        window.pipeline.enqueue(SpeechChunk(np.ones(16000, dtype=np.float32), now - 1, now))
        assert team_started.wait(4)
        window.bridge.talk_begin.emit()
        window.bridge.talk_end.emit()
        assert mic_stopped.wait(4)
        assert len(inputs) == 1
        release_team.set()
        assert mic_recognized.wait(4)
        assert team_translation_started.wait(4)
        release_translation.set()
        _wait_for(lambda: len(window.overlay.entries) == 2)
        by_direction = {entry.reverse: entry for entry in window.overlay.entries}
        assert by_direction[False].translated == "我们下回合可以换个打法。"
        assert by_direction[True].translated == "Let's regroup at B site. Wait for teammates before making a move."
        assert [language for language, _ in inputs] == [None, "zh"]
        assert "A Main" in inputs[0][1] and "快攻" in inputs[1][1]
        assert "A Main" not in inputs[1][1]
        window.reverse_copy.click()
        assert QApplication.clipboard().text() == by_direction[True].translated
        window.bridge.toggle.emit()
        assert not window.overlay.isVisible()
        window.bridge.toggle.emit()
        assert window.overlay.isVisible()

        now = time.monotonic()
        window.pipeline.enqueue(SpeechChunk(np.zeros(16000, dtype=np.float32), now - 1, now))
        _wait_for(lambda: "语气词" in window.last_discard.text())
        assert len(window.overlay.entries) == 2
        window._copy_feedback()
        feedback = QApplication.clipboard().text()
        assert "#2 结果过滤" in feedback and "no_speech_prob=0.010" in feedback
        assert "#1 识别排队" in feedback and "翻译 " in feedback
    finally:
        release_team.set()
        release_translation.set()
        window.close()


def test_reads_existing_b4_profile(monkeypatch, tmp_path, qapp):
    _profile(monkeypatch, tmp_path)
    previous = {"device_id": "team", "language": "en", "whisper_model": "small",
                "font_size": 34, "overlay_x": 100, "show_original": False}
    config.CONFIG_FILE.write_text(json.dumps(previous), encoding="utf-8")
    custom = tmp_path / "glossary_custom.json"
    custom.write_text(json.dumps({"exact_phrases": {"go rush a": "一起快攻 A。"}}), encoding="utf-8")
    history = HistoryStore()
    history.add_result(pipeline.SubtitleResult("Go rush A", "快攻 A。", "en", 0.2, 0.1, 0.3))
    history.correct(history.recent()[0].id, "现在快攻 A。")
    for directory, names in (("whisper-small", download.WHISPER_FILES), ("m2m100_418M", M2M_FILES)):
        model = ui.MODEL_DIR / directory
        model.mkdir(parents=True)
        for name in names:
            (model / name).write_bytes(b"existing model file")

    window = ui.MainWindow()
    try:
        assert window.device_combo.currentData() == "team"
        assert window.language_combo.currentData() == "en"
        assert window.font_spin.value() == 34 and window.x_spin.value() == 100
        assert not window.original_check.isChecked()
        assert window._models_ready() and not window.download_button.isEnabled()
        assert glossary.Glossary().exact("go rush a") == "一起快攻 A。"
        assert window.history.recent()[0].corrected_chinese == "现在快攻 A。"
        assert window.history.lookup_correction("go rush a", "en") == "现在快攻 A。"
    finally:
        window.close()
