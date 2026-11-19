from __future__ import annotations

import pytest
import torch
from opencc import OpenCC

from valorant_translator.translation.m2m import LocalM2M, SUPPORTED_LANGUAGES


class FakeBatch(dict):
    def to(self, device):
        return self


class FakeTokenizer:
    src_lang = None

    def __call__(self, text, **kwargs):
        return FakeBatch(input_ids=text)

    def get_lang_id(self, language):
        return {"zh": 123, "en": 456}[language]

    def batch_decode(self, tokens, **kwargs):
        return ["轉點去 B。"]


class FakeModel:
    def __init__(self):
        self.kwargs = None

    def generate(self, **kwargs):
        self.kwargs = kwargs
        return [1]


def test_five_languages_use_chinese_target_and_simplified_output():
    translator = LocalM2M.__new__(LocalM2M)
    translator.torch = torch
    translator.fast_mode = True
    translator.converter = OpenCC("t2s")
    translator.device = "cpu"
    translator.tokenizer = FakeTokenizer()
    translator.model = FakeModel()
    for language in sorted(SUPPORTED_LANGUAGES):
        assert translator.translate("rotate B", language) == "转点去 B。"
        assert translator.tokenizer.src_lang == language
        assert translator.model.kwargs["forced_bos_token_id"] == 123
        assert translator.model.kwargs["num_beams"] == 1
        assert "no_repeat_ngram_size" not in translator.model.kwargs
    with pytest.raises(ValueError, match="无法确认语种"):
        translator.translate("rotate B", "unknown")


def test_reverse_fully_matched_callout_skips_neural_decoding():
    translator = LocalM2M.__new__(LocalM2M)
    translator.converter = OpenCC("t2s")
    assert translator.translate_to_english("去B點集合，等隊友。") == "Regroup at B site. Wait for teammates."


def test_reverse_fallback_keeps_full_source_and_uses_english_decoding(monkeypatch):
    translator = LocalM2M.__new__(LocalM2M)
    translator.torch = torch
    translator.fast_mode = True
    translator.converter = OpenCC("t2s")
    translator.device = "cpu"
    translator.tokenizer = FakeTokenizer()
    translator.model = FakeModel()
    monkeypatch.setattr(translator.tokenizer, "batch_decode", lambda *args, **kwargs: ["I'll join you."])

    source = "去B点集合，然后我从中门绕过去"
    assert translator.translate_to_english(source) == "I'll join you."
    assert translator.tokenizer.src_lang == "zh"
    assert translator.model.kwargs["input_ids"] == source
    assert translator.model.kwargs["forced_bos_token_id"] == 456
    assert translator.model.kwargs["num_beams"] == 3
    assert translator.model.kwargs["no_repeat_ngram_size"] == 3
