from __future__ import annotations

import json
import queue
import threading
import time

import numpy as np

from valorant_translator.audio.capture import to_mono_16k
from valorant_translator.audio.segmenter import SpeechChunk, SpeechSegmenter
from valorant_translator.config import Settings
from valorant_translator.history import HistoryStore
from valorant_translator.pipeline import Pipeline, SubtitleResult
from valorant_translator.recognition.whisper import Recognition
from valorant_translator.translation.glossary import Glossary


def test_silence_makes_no_subtitle():
    chunks = []
    vad = SpeechSegmenter(chunks.append)
    for _ in range(50):
        vad.feed(np.zeros(1600, dtype=np.float32), time.monotonic())
    vad.flush()
    assert chunks == []


def test_voice_segment_reports_progress_and_emits_audio(monkeypatch):
    chunks, events = [], []
    vad = SpeechSegmenter(chunks.append, on_event=events.append)
    monkeypatch.setattr(vad, "vad", type("FakeVad", (), {"is_speech": lambda *_: True})())
    vad.feed(np.full(320 * 16, 0.05, dtype=np.float32), time.monotonic())
    vad.flush()
    assert len(chunks) == 1
    assert chunks[0].audio.size == 320 * 16
    assert any("检测到可能的语音" in event for event in events)


def test_short_callout_keeps_audio_before_vad_trigger(monkeypatch):
    chunks = []
    vad = SpeechSegmenter(chunks.append)

    class DelayedVad:
        calls = 0
        def is_speech(self, *_):
            self.calls += 1
            return self.calls > 10

    monkeypatch.setattr(vad, "vad", DelayedVad())
    audio = np.full(320 * 19, 0.05, dtype=np.float32)
    vad.feed(audio, time.monotonic())
    vad.flush()
    assert len(chunks) == 1
    assert np.array_equal(chunks[0].audio, audio)


def test_tiny_voice_burst_is_filtered(monkeypatch):
    chunks = []
    vad = SpeechSegmenter(chunks.append)
    monkeypatch.setattr(vad, "vad", type("FakeVad", (), {"is_speech": lambda *_: True})())
    vad.feed(np.full(320 * 3, 0.05, dtype=np.float32), time.monotonic())
    vad.flush()
    assert not chunks


def test_five_voiced_frames_can_make_a_short_callout(monkeypatch):
    chunks = []
    vad = SpeechSegmenter(chunks.append)
    monkeypatch.setattr(vad, "vad", type("FakeVad", (), {"is_speech": lambda *_: True})())
    vad.feed(np.full(320 * 5, 0.05, dtype=np.float32), time.monotonic())
    vad.flush()
    assert len(chunks) == 1


def test_resample_and_mono():
    data = np.zeros((4800, 2), dtype=np.float32)
    result = to_mono_16k(data)
    assert result.shape == (1600,)


def test_glossary_does_not_add_facts():
    glossary = Glossary()
    assert glossary.exact("Two A main") == "A 大两个。"
    assert glossary.exact("Rotate B!") == "转点去 B。"
    assert glossary.exact("Three A main") is None
    assert glossary.postprocess("Spike at A Main") == "爆能器 at A 大"
    assert glossary.callout("Can you drop?", "en") == "能给我发一把吗？"
    assert glossary.callout("Can you drop me a Vandal?", "en") == "能给我发一把 Vandal 吗？"
    assert glossary.callout("Can you drop me an Operator?", "en") == "能给我发一把 Operator 吗？"
    assert glossary.callout("Can you drop a Bandit?", "en") == "能给我发一把 Bandit 吗？"
    assert glossary.callout("Can you drop an OP?", "en") == "能给我发一把 Operator 吗？"
    assert glossary.callout("He has an Operator", "en") == "他拿着 Operator。"
    assert glossary.callout("Jett is low", "en") == "Jett 残血。"
    assert glossary.callout("Jet is low", "en") == "Jett 残血。"
    assert glossary.callout("Sage at B main", "en") == "Sage 在 B 大。"
    assert glossary.callout("Reyna is flanking", "en") == "Reyna 在绕后。"
    assert glossary.callout("Rayna is flanking", "en") == "Reyna 在绕后。"
    assert glossary.callout("They smoke B", "en") == "他们在往 B 点封烟。"
    assert glossary.callout("They won't be", "en") is None
    assert glossary.postprocess("飞机低", "Jett is low") == "Jett低"
    assert glossary.postprocess("他有一个运营商", "He has an Operator") == "他有一个Operator"
    assert "Bandit" in glossary.hotwords and "Jett" in glossary.hotwords
    assert glossary.callout("two at A Main", "en") == "A 大两个。"
    assert glossary.callout("Rotate to B", "en") == "转点去 B。"
    assert glossary.callout("We need to rotate B", "en") == "我们需要转点去 B。"
    assert glossary.callout("Go rush A", "en") == "快攻 A。"
    assert glossary.callout("Let's rush B", "en") == "快攻 B。"
    assert glossary.callout("A", "en") is None
    assert glossary.callout("two at A Main", "ms") == "A 大两个。"


def test_conversational_chinese_is_natural_without_changing_meaning():
    glossary = Glossary()
    assert glossary.callout("Give me a moment", "en") == "等我一下。"
    assert glossary.callout("Give me a sec, please!", "en") == "等我一下。"
    assert glossary.callout("Could you give me a moment?", "en") == "能等我一下吗？"
    assert glossary.callout("Hold on a second", "en") == "等一下。"
    assert glossary.callout("I'm reloading", "en") == "我在换弹。"
    assert glossary.callout("Wait for me", "en") == "等我一下。"
    assert glossary.callout("My bad", "en") == "我的锅。"
    assert glossary.callout("Give me a moment of silence", "en") is None
    assert glossary.postprocess("给我一瞬间，我正在重新加载。",
                               "Give me a moment, I'm reloading.") == "等我一下，我在换弹。"
    assert glossary.postprocess("给我一瞬间的沉默", "Give me a moment of silence") == "给我一瞬间的沉默"


def test_queue_drops_oldest():
    diagnostics = []
    pipe = Pipeline(Settings(), lambda _: None, lambda _: None, lambda _: None,
                    diagnostics.append)
    for i in range(5):
        pipe.enqueue(SpeechChunk(np.zeros(10), float(i), float(i)))
    assert pipe.jobs.qsize() == 4
    assert pipe.jobs.get_nowait().start_time == 1
    pipe.asr_busy.set()
    pipe.enqueue(SpeechChunk(np.zeros(16000), 1, 2))
    assert "正在识别上一句" in diagnostics[-1]


def test_pipeline_with_local_model_doubles(monkeypatch, tmp_path):
    import valorant_translator.pipeline as module

    class ASR:
        device = "cpu"
        def __init__(self, *_):
            pass
        def transcribe(self, audio, language):
            return Recognition("Two A main", "en", 0.9)

    class Translator:
        device = "cpu"
        def __init__(self, *_):
            pass
        def translate(self, text, language):
            return "不应调用"

    class Capture:
        def __init__(self, *_):
            pass
        def start(self):
            pass
        def stop(self):
            pass

    monkeypatch.setattr(module, "LocalWhisper", ASR)
    monkeypatch.setattr(module, "LocalM2M", Translator)
    monkeypatch.setattr(module, "AudioCapture", Capture)
    results = []
    traces = []
    history = HistoryStore(tmp_path / "history.sqlite3")
    pipe = Pipeline(Settings(device_id="dummy"), lambda _: None, lambda _: None,
                    results.append, history=history, on_trace=traces.append)
    pipe.start()
    pipe.enqueue(SpeechChunk(np.zeros(16000, dtype=np.float32), time.monotonic()-1, time.monotonic()))
    deadline = time.monotonic() + 2
    while not results and time.monotonic() < deadline:
        time.sleep(0.01)
    pipe.stop()
    assert len(results) == 1
    assert results[0].chinese == "A 大两个。"
    assert results[0].task_id == 1
    assert {trace.task_id for trace in traces} == {1}
    assert {"VAD 分段完成", "识别入队", "识别出队", "推理开始", "推理结束", "翻译结束"} <= {
        trace.stage for trace in traces
    }
    timings = {name: seconds for trace in traces for name, seconds in trace.timings.items()}
    assert all(timings[name] >= 0 for name in ("识别排队", "识别等锁", "识别", "翻译排队", "翻译"))
    assert history.recent()[0].original == "Two A main"


def test_chinese_caption_bypasses_foreign_translation(monkeypatch):
    import valorant_translator.pipeline as module

    class ASR:
        device = "cpu"
        def __init__(self, *_):
            pass
        def transcribe(self, *_):
            return Recognition("我們轉 B 點", "zh", 0.9)

    class Translator:
        device = "cpu"
        def __init__(self, *_):
            pass
        def translate(self, *_):
            raise AssertionError("Chinese must not enter M2M100")

    class Capture:
        def __init__(self, *_):
            pass
        def start(self):
            pass
        def stop(self):
            pass

    monkeypatch.setattr(module, "LocalWhisper", ASR)
    monkeypatch.setattr(module, "LocalM2M", Translator)
    monkeypatch.setattr(module, "AudioCapture", Capture)
    results = []
    pipe = Pipeline(Settings(device_id="dummy"), lambda _: None, lambda _: None, results.append)
    pipe.start()
    stamp = time.monotonic()
    pipe.enqueue(SpeechChunk(np.zeros(16000, dtype=np.float32), stamp - 1, stamp))
    deadline = time.monotonic() + 2
    while not results and time.monotonic() < deadline:
        time.sleep(0.01)
    pipe.stop()
    assert results and results[0].chinese == "我们转 B 点"


def test_history_correction_export_and_clear(tmp_path):
    history = HistoryStore(tmp_path / "history.sqlite3")
    history.add_result(SubtitleResult("Go rush A", "去 A", "en", 0.4, 0.2, 0.7))
    entry = history.recent()[0]
    history.correct(entry.id, "快攻 A。")
    assert history.lookup_correction("go rush a", "en") == "快攻 A。"
    assert history.recent()[0].corrected_chinese == "快攻 A。"
    output = tmp_path / "history.csv"
    history.export_csv(output)
    assert "快攻 A。" in output.read_text(encoding="utf-8-sig")
    history.clear_history()
    assert history.recent() == []
    assert history.lookup_correction("go rush a", "en") == "快攻 A。"
    history.clear_all()
    assert history.lookup_correction("go rush a", "en") is None


def test_stale_translation_is_discarded(monkeypatch):
    import valorant_translator.pipeline as module

    class ASR:
        device = "cpu"
        def __init__(self, *_):
            pass
        def transcribe(self, *_):
            return Recognition("Attack now", "en", 0.9)

    class Translator:
        device = "cpu"
        def __init__(self, *_):
            pass
        def translate(self, *_):
            time.sleep(0.08)
            return "现在进攻"

    class Capture:
        def __init__(self, *_):
            pass
        def start(self):
            pass
        def stop(self):
            pass

    monkeypatch.setattr(module, "LocalWhisper", ASR)
    monkeypatch.setattr(module, "LocalM2M", Translator)
    monkeypatch.setattr(module, "AudioCapture", Capture)
    results = []
    diagnostics = []
    settings = Settings(device_id="dummy", translation_timeout=0.01)
    pipe = Pipeline(settings, lambda _: None, lambda _: None, results.append, diagnostics.append)
    pipe.start()
    pipe.enqueue(SpeechChunk(np.zeros(16000), time.monotonic()-1, time.monotonic()))
    time.sleep(0.2)
    pipe.stop()
    assert results == []
    assert pipe.failures == 1
    assert any("字幕已过期" in message for message in diagnostics)


def test_nonsense_latin_translation_is_not_displayed(monkeypatch):
    import valorant_translator.pipeline as module

    class ASR:
        device = "cuda"
        def __init__(self, *_):
            pass
        def transcribe(self, *_):
            return Recognition("unknown speech", "en", 0.9)

    class Translator:
        device = "cuda"
        def __init__(self, *_):
            pass
        def translate(self, *_):
            return "ayay"

    class Capture:
        def __init__(self, *_):
            pass
        def start(self):
            pass
        def stop(self):
            pass

    monkeypatch.setattr(module, "LocalWhisper", ASR)
    monkeypatch.setattr(module, "LocalM2M", Translator)
    monkeypatch.setattr(module, "AudioCapture", Capture)
    results, diagnostics = [], []
    pipe = Pipeline(Settings(device_id="dummy"), lambda _: None, lambda _: None,
                    results.append, diagnostics.append)
    pipe.start()
    pipe.enqueue(SpeechChunk(np.zeros(16000), time.monotonic() - 1, time.monotonic()))
    deadline = time.monotonic() + 2
    while pipe.failures == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    pipe.stop()
    assert results == []
    assert any("未生成中文" in message for message in diagnostics)


def test_repeated_callout_is_allowed_after_short_interval(monkeypatch):
    import valorant_translator.pipeline as module

    class ASR:
        device = "cuda"
        def __init__(self, *_):
            pass
        def transcribe(self, *_):
            return Recognition("Rotate B", "en", 0.9)

    class Translator:
        device = "cuda"
        def __init__(self, *_):
            pass
        def translate(self, *_):
            raise AssertionError("known callout should not reach M2M100")

    class Capture:
        def __init__(self, *_):
            pass
        def start(self):
            pass
        def stop(self):
            pass

    monkeypatch.setattr(module, "LocalWhisper", ASR)
    monkeypatch.setattr(module, "LocalM2M", Translator)
    monkeypatch.setattr(module, "AudioCapture", Capture)
    results = []
    pipe = Pipeline(Settings(device_id="dummy"), lambda _: None, lambda _: None, results.append)
    pipe.start()

    def send_and_wait(expected):
        stamp = time.monotonic()
        pipe.enqueue(SpeechChunk(np.zeros(16000), stamp - 1, stamp))
        deadline = time.monotonic() + 2
        while len(results) < expected and time.monotonic() < deadline:
            time.sleep(0.01)

    send_and_wait(1)
    assert len(results) == 1
    pipe.last_result_time -= 2
    send_and_wait(2)
    pipe.stop()
    assert len(results) == 2


def test_next_sentence_is_recognized_while_previous_translation_runs(monkeypatch):
    import valorant_translator.pipeline as module

    first_translation_started = threading.Event()
    release_first_translation = threading.Event()
    second_recognized = threading.Event()
    third_recognized = threading.Event()

    class ASR:
        device = "cuda"
        def __init__(self, *_):
            self.calls = 0
        def transcribe(self, *_):
            self.calls += 1
            if self.calls == 2:
                second_recognized.set()
            if self.calls == 3:
                third_recognized.set()
            return Recognition(f"sentence {self.calls}", "en", 0.9)

    class Translator:
        device = "cuda"
        def __init__(self, *_):
            pass
        def translate(self, text, _language):
            if text == "sentence 1":
                first_translation_started.set()
                assert release_first_translation.wait(2)
            return {"sentence 1": "第一句。", "sentence 2": "第二句。",
                    "sentence 3": "第三句。"}[text]

    class Capture:
        def __init__(self, *_):
            pass
        def start(self):
            pass
        def stop(self):
            pass

    monkeypatch.setattr(module, "LocalWhisper", ASR)
    monkeypatch.setattr(module, "LocalM2M", Translator)
    monkeypatch.setattr(module, "AudioCapture", Capture)
    results = []
    pipe = Pipeline(Settings(device_id="dummy"), lambda _: None, lambda _: None, results.append)
    pipe.start()
    stamp = time.monotonic()
    pipe.enqueue(SpeechChunk(np.zeros(16000), stamp - 1, stamp))
    try:
        assert first_translation_started.wait(2)
        stamp = time.monotonic()
        pipe.enqueue(SpeechChunk(np.zeros(16000), stamp - 1, stamp))
        assert second_recognized.wait(1)
        stamp = time.monotonic()
        pipe.enqueue(SpeechChunk(np.zeros(16000), stamp - 1, stamp))
        assert third_recognized.wait(1)
    finally:
        release_first_translation.set()
    deadline = time.monotonic() + 2
    while len(results) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    pipe.stop()
    assert [item.chinese for item in results] == ["第一句。", "第二句。", "第三句。"]
