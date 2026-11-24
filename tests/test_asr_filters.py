from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from valorant_translator.recognition.whisper import LocalWhisper, filter_segments
from valorant_translator.translation.glossary import Glossary


class FakeModel:
    def __init__(self, text: str, language: str, confidence: float):
        self.text = text
        self.language = language
        self.confidence = confidence

    def transcribe(self, *_args, **_kwargs):
        self.last_kwargs = _kwargs
        segments = [SimpleNamespace(text=self.text, no_speech_prob=0.1, avg_logprob=-0.2)]
        info = SimpleNamespace(language=self.language, language_probability=self.confidence)
        return segments, info


def recognize(text: str, language: str, confidence: float, lock: str = "auto"):
    asr = LocalWhisper.__new__(LocalWhisper)
    asr.model = FakeModel(text, language, confidence)
    asr.glossary = Glossary()
    return asr.transcribe(np.zeros(16000, dtype=np.float32), lock)


def test_five_languages_are_accepted():
    for language in ("en", "ms", "hi", "ta", "th"):
        assert recognize("test speech", language, 0.95).language == language


def test_chinese_speech_is_kept_as_chinese():
    assert recognize("我们去 B 点", "zh", 0.92).language == "zh"
    assert recognize("我们去 B 点", "en", 0.92).language == "zh"
    assert recognize("gibberish", "zh", 0.92) is None


def test_unsupported_and_uncertain_speech_is_suppressed():
    assert recognize("Bonjour", "fr", 0.99) is None
    assert recognize("B", "en", 0.4) is None
    assert recognize("", "en", 0.99) is None


def test_language_lock_overrides_detector():
    assert recognize("பி தளம்", "en", 0.2, "ta").language == "ta"


def test_short_known_callout_has_cautious_auto_threshold():
    assert recognize("Mid", "en", 0.60).text == "Mid"
    assert recognize("Can you drop", "ms", 0.60).language == "en"
    assert recognize("Mid", "en", 0.40) is None
    assert recognize("A", "en", 0.99) is None
    assert recognize("Aye", "en", 0.99) is None
    assert recognize("They won't be", "en", 0.99) is None


def test_whisper_receives_game_term_hotwords():
    asr = LocalWhisper.__new__(LocalWhisper)
    asr.model = FakeModel("Can you drop", "en", 0.9)
    asr.glossary = Glossary()
    assert asr.transcribe(np.zeros(16000, dtype=np.float32), "auto")
    assert "A Main" in asr.model.last_kwargs["hotwords"]
    assert "Drop" in asr.model.last_kwargs["hotwords"]
    assert asr.model.last_kwargs["without_timestamps"] is True
    assert asr.model.last_kwargs["temperature"] == 0.0
    assert asr.model.last_kwargs["best_of"] == 1
    assert asr.model.last_kwargs["max_new_tokens"] == 64


def test_chinese_microphone_hotwords_are_isolated_from_team_audio():
    asr = LocalWhisper.__new__(LocalWhisper)
    asr.glossary = Glossary()
    asr.model = FakeModel("我们快攻 A 点", "zh", 0.95)
    audio = np.zeros(16000, dtype=np.float32)
    assert asr.transcribe_chinese_callout(audio).text == "我们快攻 A 点"
    assert asr.model.last_kwargs["language"] == "zh"
    assert "快攻" in asr.model.last_kwargs["hotwords"]
    assert "A Main" not in asr.model.last_kwargs["hotwords"]

    asr.model = FakeModel("Can you drop", "en", 0.95)
    assert asr.transcribe(audio, "auto").text == "Can you drop"
    assert asr.model.last_kwargs["hotwords"] == asr.glossary.hotwords


@pytest.mark.parametrize(("text", "language", "confidence", "no_speech", "logprob", "reason"), [
    ("Two A Main.", "en", 0.9, 0.55, -0.2, "段落未达标"),
    ("Two A Main.", "en", 0.9, 0.1, -1.1, "段落未达标"),
    ("", "en", 0.9, 0.1, -0.2, "识别文字为空"),
    ("bonjour", "fr", 0.9, 0.1, -0.2, "不支持的语种"),
    ("你", "zh", 0.9, 0.1, -0.2, "中文仅 1 字"),
    ("Aye", "en", 0.9, 0.1, -0.2, "语气词"),
    ("They won't be", "en", 0.9, 0.1, -0.2, "不确定片段"),
    ("test", "en", 0.67, 0.1, -0.2, "67% 低于 68%"),
    ("Hi", "en", 0.9, 0.1, -0.2, "文本过短"),
])
def test_filter_reports_unchanged_rejection_reason(text, language, confidence, no_speech, logprob, reason):
    segment = SimpleNamespace(text=text, no_speech_prob=no_speech, avg_logprob=logprob)
    info = SimpleNamespace(language=language, language_probability=confidence)
    events = []
    assert filter_segments([segment], info, None, Glossary(),
                           lambda name, detail: events.append((name, detail))) is None
    assert reason in events[0][0]
    assert repr(text) in events[0][1]
    assert f"no_speech_prob={no_speech:.3f}" in events[0][1]
    assert f"avg_logprob={logprob:.3f}" in events[0][1]


def test_chinese_hotwords_and_rejection_survive_cuda_retry(monkeypatch, tmp_path):
    import faster_whisper

    class FailingCuda:
        def transcribe(self, *args, **kwargs):
            def segments():
                raise RuntimeError("CUDA inference unavailable")
                yield
            return segments(), SimpleNamespace(language="zh", language_probability=0.96)

    cpu = FakeModel("啊", "zh", 0.96)
    monkeypatch.setattr(faster_whisper, "WhisperModel", lambda *args, **kwargs: cpu)
    asr = LocalWhisper.__new__(LocalWhisper)
    asr.device, asr.path, asr.model = "cuda", tmp_path, FailingCuda()
    rejections = []
    asr.on_rejection = lambda reason, detail: rejections.append((reason, detail))

    assert asr.transcribe_chinese_callout(np.zeros(16000, dtype=np.float32)) is None
    assert asr.device == "cpu"
    assert cpu.last_kwargs["language"] == "zh" and "队友" in cpu.last_kwargs["hotwords"]
    assert "中文仅 1 字" in rejections[0][0]
    assert "no_speech_prob=0.100" in rejections[0][1]
