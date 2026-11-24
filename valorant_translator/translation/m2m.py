"""Local M2M100 translation for the supported speech languages."""
from __future__ import annotations

import gc
import logging

from valorant_translator.config import MODEL_DIR
from valorant_translator.languages import DEFAULT_LANGUAGES, LanguageCatalog
from valorant_translator.translation.reverse_callouts import translate_callout

LOG = logging.getLogger(__name__)
M2M_REPO = "facebook/m2m100_418M"
M2M_DIR = MODEL_DIR / "m2m100_418M"
M2M_FILES = ("pytorch_model.bin", "config.json", "sentencepiece.bpe.model",
             "tokenizer_config.json", "vocab.json")
SUPPORTED_LANGUAGES = frozenset(code for code in DEFAULT_LANGUAGES.names() if code != "zh")


class LocalM2M:
    """Load once, prefer CUDA, and fall back to CPU on CUDA runtime errors."""

    def __init__(self, requested_device: str, fast_mode: bool = True):
        import torch
        from opencc import OpenCC
        from transformers import M2M100ForConditionalGeneration, M2M100Tokenizer

        if not all((M2M_DIR / name).is_file() for name in M2M_FILES):
            raise FileNotFoundError("M2M100 翻译模型缺失；请在首页点击“一键下载模型”。")
        self.torch = torch
        self.fast_mode = fast_mode
        self.converter = OpenCC("t2s")
        self.device = "cuda" if requested_device != "cpu" and torch.cuda.is_available() else "cpu"
        self.tokenizer = M2M100Tokenizer.from_pretrained(str(M2M_DIR), local_files_only=True)
        self.model_type = M2M100ForConditionalGeneration
        self.model = self._load_model(M2M100ForConditionalGeneration)

    def _load_model(self, model_type):
        torch = self.torch
        model = None
        try:
            model = model_type.from_pretrained(
                str(M2M_DIR), local_files_only=True,
                torch_dtype=torch.float16 if self.device == "cuda" else torch.float32)
            return model.to(self.device).eval()
        except (RuntimeError, MemoryError) as exc:
            if self.device != "cuda":
                raise
            LOG.warning("M2M100 CUDA load failed; falling back to CPU: %s", exc)
            del model
            gc.collect()
            torch.cuda.empty_cache()
            self.device = "cpu"
            model = model_type.from_pretrained(str(M2M_DIR), local_files_only=True,
                                               torch_dtype=torch.float32)
            return model.to("cpu").eval()

    def translate(self, source: str, language: str, target: str = "zh") -> str:
        languages: LanguageCatalog = getattr(self, "languages", DEFAULT_LANGUAGES)
        if not languages.supports_translation(language):
            raise ValueError("无法确认语种，不生成翻译。")
        languages.target(target)
        if language == target:
            return self.converter.convert(source) if target == "zh" else source
        return self._translate_with_fallback(source, language, target)

    def translate_to_english(self, source: str) -> str:
        return self.translate_to_english_details(source)[0]

    def translate_to_english_details(self, source: str) -> tuple[str, str | None, str]:
        callout = translate_callout(self.converter.convert(source))
        if callout is not None:
            return callout, None, "reverse_callout"
        raw = self._translate_with_fallback(source, "zh", "en")
        return raw, raw, ""

    def _translate_with_fallback(self, source: str, language: str, target: str) -> str:
        self.tokenizer.src_lang = language
        try:
            return self._translate(source) if target == "zh" else self._translate(source, target)
        except RuntimeError as exc:
            if self.device != "cuda":
                raise
            LOG.warning("M2M100 CUDA inference failed; reloading on CPU: %s", exc)
            del self.model
            gc.collect()
            try:
                self.torch.cuda.empty_cache()
            except RuntimeError:
                LOG.warning("Could not clear CUDA cache after inference failure")
            self.device = "cpu"
            self.model = self._load_model(self.model_type)
            return self._translate(source) if target == "zh" else self._translate(source, target)

    def _translate(self, source: str, target: str = "zh") -> str:
        batch = self.tokenizer(source, return_tensors="pt", truncation=True,
                               max_length=128).to(self.device)
        generation = {"num_beams": 1 if self.fast_mode else 2}
        if target == "en":
            generation.update(num_beams=3, no_repeat_ngram_size=3)
        with self.torch.inference_mode():
            tokens = self.model.generate(
                **batch,
                forced_bos_token_id=self.tokenizer.get_lang_id(target),
                max_new_tokens=48 if self.fast_mode else 64,
                **generation)
        translated = self.tokenizer.batch_decode(tokens, skip_special_tokens=True)[0].strip()
        return self.converter.convert(translated) if target == "zh" else translated
