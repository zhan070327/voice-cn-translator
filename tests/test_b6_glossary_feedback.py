from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from valorant_translator import config, pipeline
from valorant_translator.audio.segmenter import SpeechChunk
from valorant_translator.config import Settings
from valorant_translator.feedback import FeedbackStore
from valorant_translator.history import HistoryStore
from valorant_translator.pipeline import Pipeline, SubtitleResult
from valorant_translator.recognition.whisper import Recognition, filter_segments
from valorant_translator.translation.enhancement import GameEnhancer
from valorant_translator.translation.glossary import Glossary, game_glossary
from valorant_translator.translation.rules import Rule, RuleStore
from valorant_translator.ui.talk import PushToTalk


@pytest.mark.parametrize(("original", "expected"), [
    ("MY ULTIMUS, READY!", "我的大招好了。"), ("My Ultimates Ready!", "我的大招好了。"),
    ("Ultimus.", "大招。"), ("ULTIMATES!", "大招。"),
    ("WATCH YOUR EYES!", "当心眼睛。"), ("Watch your eyes—Flash!", "当心眼睛，有闪。"),
    ("Watch your eyes, careful now!", "当心眼睛，小心。"), ("My ultimate is ready!", "我的大招好了。"),
])
def test_b6_real_glossary_case_and_punctuation(original, expected):
    assert Glossary().exact(original) == expected
    match = GameEnhancer("valorant", Glossary()).match(original, "en", "zh", Settings())
    assert match.translation == expected


def test_scoped_matching_clause_boundaries_and_optional_enhancement(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    engine = GameEnhancer("valorant", Glossary())
    settings = Settings()
    original = "My Ultimus, Ready!"
    assert engine.match(original, "en", "zh", settings).text == "My ultimate is ready."
    assert engine.match(original, "en", "de", settings).text == original
    assert engine.match(original, "ja", "zh", settings).translation is None
    assert engine.match(original, "en", "zh", replace(settings, asr_correction=False)).text == original
    other = GameEnhancer("cs2", game_glossary("cs2"))
    assert other.match(original, "en", "zh", settings).translation is None
    assert engine.match("Rotate B. Watch your eyes!", "en", "zh", settings).translation == "转点去 B。当心眼睛。"
    mixed = engine.match("My Ultimus, Ready! Let's try another plan.", "en", "zh", settings)
    assert mixed.translation is None and mixed.text == "My ultimate is ready. Let's try another plan."
    assert engine.match("Rotate the planet.", "en", "zh", settings).translation is None
    assert engine.match("The flash went out.", "en", "zh", settings).translation is None
    assert engine.match("One shot of coffee.", "en", "zh", settings).translation is None
    assert engine.match("Watch your eyes! Come on, let's go!", "en", "zh", settings).translation is None
    assert engine.match("Watch your eyes!", "en", "zh", replace(settings, game_terms=False)).translation is None
    glossary = engine.glossary
    assert glossary.postprocess("看你的眼睛！我们走。", "Watch your eyes! Let's go.") == "当心眼睛！我们走。"
    assert glossary.postprocess("请看你的眼睛。", "Look at your eyes.") == "请看你的眼睛。"
    assert glossary.callout("Heaven") == "天台。"


def test_verified_official_and_teammate_warnings_are_distinct():
    engine = GameEnhancer("valorant", Glossary())
    settings = Settings()
    official = engine.match("Fire in the hole!", "en", "zh", settings)
    assert official.translation == "小心炸弹！"
    assert official.rules.startswith("official_line:")
    assert not engine.match("Watch your eyes!", "en", "zh", settings).suppressed
    hero = engine.glossary.data["suppressed_lines"][0]["original"]
    assert engine.match(hero.upper(), "en", "zh", settings).suppressed
    assert not engine.match(hero, "en", "zh", replace(settings, teammate_only=False)).suppressed
    assert not engine.match(hero.replace("girl", "woman", 1), "en", "zh", settings).suppressed
    assert not engine.match(hero, "ja", "zh", settings).suppressed
    for phrase in ("Going up", "I'm out of here", "Alright, all's ready", "There we go"):
        assert not engine.match(phrase, "en", "zh", settings).suppressed


def test_custom_rules_priority_verification_time_and_recovery(tmp_path):
    store = RuleStore(tmp_path)
    chosen = Rule("valorant", "en", "de", "full buy", "Kauf alles", priority=10)
    store.save([chosen])
    verified = replace(chosen, translated="Vollkauf", verified=True, updated="2026-11-19T12:00:00+00:00")
    newest = replace(verified, translated="Vollausrüstung", updated="2026-11-20T12:00:00+00:00")
    assert store.add(verified)
    assert store.add(newest)
    store.add(replace(chosen, priority=11, translated="ausgeschaltet", enabled=False))
    engine = GameEnhancer("valorant", Glossary(), store)
    assert engine.match("FULL BUY!", "en", "de", Settings()).translation == "Vollausrüstung"
    assert engine.match("full buy", "en", "zh", Settings()).translation == "全起。"
    assert engine.match("full buy", "de", "zh", Settings()).translation is None
    exported = tmp_path / "export.json"
    store.export(exported)
    other = RuleStore(tmp_path / "other")
    assert not other.import_file(exported)
    assert other.import_file(exported) == len(store.rules)
    store.path.write_text("{corrupt", encoding="utf-8")
    assert RuleStore(tmp_path).rules
    default = tmp_path / "builtin.json"
    backup = tmp_path / "backup.json"
    default.write_text('{"exact_phrases":{"mid":"中路。"}}', encoding="utf-8")
    assert Glossary(default, backup=backup).exact("mid") == "中路。"
    default.write_text('{"exact_phrases":[]}', encoding="utf-8")
    assert Glossary(default, backup=backup).exact("mid") == "中路。"
    backup.unlink()
    assert not Glossary(default, backup=backup).data


def test_feedback_is_local_reviewed_candidates_only_and_deduplicates(tmp_path):
    history = HistoryStore(tmp_path / "history.sqlite3")
    history.add_result(SubtitleResult("My Ultimus, Ready!", "我的最终准备好了。", "en", .1, .2, .3,
                                     corrected_original="My ultimate is ready.", machine_translation="原译",
                                     glossary_rules="asr_correction:my ultimus ready"))
    entry = history.recent()[0]
    history.correct(entry.id, "我的大招好了。", apply_future=False)
    assert history.entry(entry.id).corrected_chinese == "我的大招好了。"
    assert history.lookup_correction("my ultimus ready", "en") is None
    author = FeedbackStore(tmp_path / "sender.json")
    assert author.add(entry, "我的大招好了。", "语音识别错误", "没有上传音频")
    assert not author.add(entry, "我的大招好了。", "语音识别错误", "重复备注")
    assert author.add(entry, "准备就绪。", "普通翻译错误", "")
    exported = tmp_path / "shared.json"
    author.export(exported)
    review = FeedbackStore(tmp_path / "review.json")
    assert review.import_file(exported) == (2, 0)
    assert review.import_file(exported) == (0, 2)
    assert not review.approved_candidates()
    first, second = review.entries
    review.review(first, "Approved")
    review.review(second, "Rejected")
    reopened = FeedbackStore(review.path)
    assert [item["status"] for item in reopened.entries.values()] == ["Approved", "Rejected"]
    [candidate] = reopened.approved_candidates()
    assert candidate.original == "My ultimate is ready."
    assert candidate.translated == "我的大招好了。"
    assert not candidate.verified
    assert not RuleStore(tmp_path).rules
    reopened.export(exported)
    assert author._read(exported)[0]["status"] == "Pending"


def test_migrates_b5_sqlite_and_imports_csv_with_missing_and_broken_rows(tmp_path):
    path = tmp_path / "b5.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE history (id INTEGER PRIMARY KEY, created_at TEXT, original TEXT, chinese TEXT,
                   corrected_chinese TEXT, language TEXT, asr_seconds REAL, translation_seconds REAL,
                   total_seconds REAL)""")
        db.execute("INSERT INTO history VALUES (1,'2026-11-20','Two A main','A 大两个。',NULL,'en',0.2,0.1,0.3)")
    history = HistoryStore(path)
    assert history.entry(1).target_language == "zh"
    assert history.entry(1).machine_translation is None
    archive = tmp_path / "older.csv"
    archive.write_text("时间,识别原文,机器译文,语言,识别秒数\n日期,Rotate B,转 B,en,0.5\n损坏,,,,x\n",
                       encoding="utf-8-sig")
    assert history.import_csv(archive) == (1, 1)
    assert history.recent()[0].asr_seconds == 0.5
    assert history.recent()[0].total_seconds == -1
    assert history.entry(1).original == "Two A main"
    exported = tmp_path / "export.csv"
    history.export_csv(exported)
    assert "纠错后原文" in exported.read_text(encoding="utf-8-sig")


@pytest.mark.parametrize(("language", "text"), [("ja", "次は移動します。"), ("ko", "다음 장소로 이동해요.")])
def test_asian_language_isolation_reaches_supported_translation(language, text):
    from valorant_translator.translation.m2m import LocalM2M
    result = filter_segments([SimpleNamespace(text=text, no_speech_prob=.04, avg_logprob=-.2)],
                             SimpleNamespace(language=language, language_probability=.91), None, Glossary())
    assert result and result.language == language
    mt = LocalM2M.__new__(LocalM2M)
    mt._translate_with_fallback = lambda source, src, target: (source, src, target)
    assert mt.translate(result.text, result.language) == (text, language, "zh")


@pytest.mark.parametrize(("audio", "mode", "expected"), [
    (np.zeros(8000, dtype=np.float32), "unused", "没录到声音"),
    (np.full(1600, .04, dtype=np.float32), "unused", "语音太短"),
    (np.full(8000, .04, dtype=np.float32), "bad", "no_speech_prob=0.890"),
    (np.full(8000, .04, dtype=np.float32), "good", "Wait for teammates."),
])
def test_ptt_real_rms_and_specific_rejection(audio, mode, expected):
    complete = threading.Event()
    messages, levels = [], []

    class Capture:
        def __init__(self, device, frame, *_):
            assert device == "microphone"
            self.frame = frame
        def start(self):
            self.frame(audio, 0)
        def stop(self):
            pass

    def status(message):
        messages.append(message)
        if expected in message:
            complete.set()

    def result(pair):
        assert pair == ("等队友。", expected)
        complete.set()

    def recognize(_audio, rejected):
        assert mode != "unused"
        if mode == "bad":
            info = SimpleNamespace(language_probability=.99)
            segment = SimpleNamespace(text="等队友。", no_speech_prob=.89, avg_logprob=-1.2)
            assert filter_segments([segment], info, "zh", None, rejected) is None
            return None, "", {}
        return Recognition("等队友。", "zh", 1.0), expected, {}

    talk = PushToTalk(status, result, Capture, level=levels.append)
    assert talk.begin(Settings(device_id="loopback", microphone_id="microphone"),
                      diagnostic_session=recognize)
    talk.end()
    assert complete.wait(3), messages
    assert levels[0] == pytest.approx(float(np.sqrt(np.mean(audio * audio))))
    assert levels[-1] == -1


def test_live_pipeline_scoped_rules_suppression_history_and_queue_counters(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    hero = Glossary().data["suppressed_lines"][0]["original"]
    texts = iter(["My Ultimates Ready!", hero, "Watch your eyes!",
                  "Watch your eyes! Come on, let's go!", "One went down, Cypher."])
    translated = {"Watch your eyes! Come on, let's go!": "看你的眼睛！快，我们走！",
                  "One went down, Cypher.": "有一个倒了，Cypher。"}
    calls = []

    class ASR:
        device = "cpu"
        def __init__(self, *_):
            pass
        def transcribe(self, audio, language):
            return Recognition(next(texts), "en", .95)

    class MT:
        device = "cpu"
        def __init__(self, *_):
            pass
        def translate(self, original, language):
            calls.append(original)
            return translated[original]

    class Capture:
        def __init__(self, *_):
            pass
        def start(self):
            pass
        def stop(self):
            pass

    for name, value in (("LocalWhisper", ASR), ("LocalM2M", MT), ("AudioCapture", Capture)):
        monkeypatch.setattr(pipeline, name, value)
    results, traces = [], []
    history = HistoryStore(tmp_path / "pipeline.sqlite3")
    pipe = Pipeline(Settings(), lambda _: None, lambda _: None, results.append, history=history, on_trace=traces.append)
    pipe.start()
    try:
        # All four subsequent clips arrive in succession within the existing bounded queue.
        now = time.monotonic()
        pipe.enqueue(SpeechChunk(np.ones(16000, np.float32), now - 1, now))
        deadline = time.monotonic() + 2
        while not results and time.monotonic() < deadline:
            time.sleep(.005)
        for _ in range(4):
            now = time.monotonic()
            pipe.enqueue(SpeechChunk(np.ones(16000, np.float32), now - 1, now))
        deadline = time.monotonic() + 2
        while len(results) < 4 and time.monotonic() < deadline:
            time.sleep(.005)
        assert [r.chinese for r in results] == ["我的大招好了。", "当心眼睛。", "当心眼睛！快，我们走！", "有一个倒了，Cypher。"]
        assert results[0].original == "My Ultimates Ready!"
        assert results[0].corrected_original == "My ultimate is ready."
        assert results[2].machine_translation == "看你的眼睛！快，我们走！"
        assert calls == list(translated)
        assert len(history.recent()) == 4
        assert pipe.diagnostics.snapshot()["drops"] == {"已抑制英雄台词": 1}
        assert any(hero in trace.detail for trace in traces)
    finally:
        pipe.stop()


def test_queue_limit_is_unchanged_and_drop_is_measured():
    pipe = Pipeline(Settings(), lambda _: None, lambda _: None, lambda _: None)
    for _ in range(6):
        now = time.monotonic()
        pipe.enqueue(SpeechChunk(np.zeros(16000), now - 1, now))
    snapshot = pipe.diagnostics.snapshot()
    assert snapshot["counts"]["语音片段"] == 6
    assert snapshot["queues"]["识别队列"] == (4, 4)
    assert snapshot["drops"]["识别队列已满"] == 2
    assert "33.3%" in pipe.diagnostics.summary()



def test_b6_benchmark_separates_asr_errors_from_missing_subtitles():
    from scripts.benchmark_game_glossary import english_word_error_rates

    cases = [
        {"text": "Hold B!", "recognized_asr": "hold b", "asr": "hold b", "final": "守住 B。"},
        {"text": "Go A.", "recognized_asr": "go C", "discarded": "翻译排队过期"},
        {"text": "Hold mid.", "discarded": "无可用识别结果"},
        {"language_ref": "ja", "text": "omitted from the English metric"},
    ]
    assert english_word_error_rates(cases) == {
        "teammate_english_wer": pytest.approx(4 / 6),
        "teammate_english_asr_wer": pytest.approx(3 / 6),
    }
    assert all(rate is None for rate in english_word_error_rates(cases[-1:]).values())
