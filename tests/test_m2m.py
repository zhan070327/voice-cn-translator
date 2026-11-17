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
        assert language == "zh"
        return 123

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
    with pytest.raises(ValueError, match="无法确认语种"):
        translator.translate("rotate B", "unknown")
