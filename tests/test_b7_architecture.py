from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from valorant_translator import pipeline
from valorant_translator.audio.segmenter import SpeechChunk
from valorant_translator.config import Settings
from valorant_translator.languages import DEFAULT_LANGUAGES, LanguageCatalog
from valorant_translator.models.providers import ModelManager
from valorant_translator.recognition.whisper import Recognition
from valorant_translator.translation.game_catalog import GameCatalog
from valorant_translator.ui.texts import tr

FIXTURES = Path(__file__).parent / "fixtures"


def test_b7_legacy_config_and_independent_language_pair(tmp_path, monkeypatch):
    from valorant_translator import config
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    config.CONFIG_FILE.write_text('{"language":"en","whisper_model":"small"}', encoding="utf-8")
    old = Settings.load()
    assert old.source_language == "en" and old.target_language == "zh"
    catalog = LanguageCatalog.from_file(FIXTURES / "languages_test.json")
    assert catalog.source("xx") == "xx" and catalog.target("en") == "en"
    assert DEFAULT_LANGUAGES.source("auto") == "auto"


def test_b7_test_only_language_and_game_flow_through_live_pipeline(monkeypatch):
    catalog = LanguageCatalog.from_file(FIXTURES / "languages_test.json")
    games = GameCatalog(FIXTURES)
    assert games.names()["arena_test"] == "测试竞技场"
    assert "xx" not in DEFAULT_LANGUAGES.names()
    assert "arena_test" not in GameCatalog().names()
    calls = []

    class ASR:
        device = "cpu"
        def transcribe(self, audio, language):
            calls.append(("asr", language))
            return Recognition("blue gate" if len([x for x in calls if x[0] == "asr"]) == 1 else "go left", "xx", .99)

    class MT:
        device = "cpu"
        def translate(self, text, source, target="zh"):
            calls.append(("mt", text, source, target))
            return "向左走。"

    class Capture:
        def __init__(self, *_): pass
        def start(self): pass
        def stop(self): pass

    monkeypatch.setattr(pipeline, "AudioCapture", Capture)
    models = ModelManager()
    models.register_asr("test_asr", lambda _: ASR())
    models.register_translation("test_mt", lambda _: MT())
    results = []
    settings = Settings(language="xx", target_language="zh", asr_provider="test_asr",
                        translation_model="test_mt", game="arena_test", max_task_age=10)
    worker = pipeline.Pipeline(settings, lambda _: None, lambda _: None, results.append,
                               languages=catalog, models=models, games=games)
    worker.start()
    try:
        for _ in range(2):
            now = time.monotonic()
            worker.enqueue(SpeechChunk(np.ones(8000, np.float32), now - .5, now))
        deadline = time.monotonic() + 3
        while len(results) < 2 and time.monotonic() < deadline:
            time.sleep(.01)
        assert [item.chinese for item in results] == ["蓝门。", "向左走。"]
        assert all(item.target_language == "zh" for item in results)
        assert ("mt", "go left", "xx", "zh") in calls
    finally:
        worker.stop()


def test_b7_ui_copy_keeps_chinese_default():
    assert tr("app.0001") == "同频 / SameFreq"


def test_chinese_speech_keeps_raw_history_and_displays_english(monkeypatch, tmp_path):
    from valorant_translator.history import HistoryStore

    class ASR:
        device = "cpu"
        def transcribe(self, _audio, _language):
            return Recognition("我有钱", "zh", .99)

    class MT:
        device = "cpu"
        def translate_to_english_details(self, _source):
            return "I have money", "I have money", ""

    class Capture:
        def __init__(self, *_): pass
        def start(self): pass
        def stop(self): pass

    monkeypatch.setattr(pipeline, "AudioCapture", Capture)
    models = ModelManager()
    models.register_asr("test_asr", lambda _: ASR())
    models.register_translation("test_mt", lambda _: MT())
    results = []
    history = HistoryStore(tmp_path / "history.sqlite3")
    worker = pipeline.Pipeline(
        Settings(language="zh", asr_provider="test_asr", translation_model="test_mt"),
        lambda _: None, lambda _: None, results.append, models=models, history=history,
    )
    worker.start()
    try:
        now = time.monotonic()
        worker.enqueue(SpeechChunk(np.ones(16000, np.float32), now - 1, now))
        deadline = now + 3
        while not results and time.monotonic() < deadline:
            time.sleep(.01)
        assert len(results) == 1
        assert (results[0].original, results[0].chinese, results[0].display_original) == (
            "我有钱", "我有钱", "I have money")
        entry = history.recent(1)[0]
        assert (entry.original, entry.display_original) == ("我有钱", "I have money")
    finally:
        worker.stop()
