"""Local M2M100 translation for the five supported speech languages."""
from __future__ import annotations

import gc
import logging

from valorant_translator.config import MODEL_DIR

LOG = logging.getLogger(__name__)
M2M_REPO = "facebook/m2m100_418M"
M2M_DIR = MODEL_DIR / "m2m100_418M"
M2M_FILES = ("pytorch_model.bin", "config.json", "sentencepiece.bpe.model",
             "tokenizer_config.json", "vocab.json")
SUPPORTED_LANGUAGES = frozenset(("en", "ms", "hi", "ta", "th"))


class LocalM2M:
    """Load once, prefer CUDA, and fall back to CPU on CUDA memory errors."""

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

    def translate(self, source: str, language: str) -> str:
        if language not in SUPPORTED_LANGUAGES:
            raise ValueError("无法确认语种，不生成翻译。")
        self.tokenizer.src_lang = language
        try:
            return self._translate(source)
        except RuntimeError as exc:
            if self.device != "cuda" or "out of memory" not in str(exc).lower():
                raise
            LOG.warning("M2M100 CUDA OOM; falling back to CPU")
            self.model.to("cpu")
            self.device = "cpu"
            self.torch.cuda.empty_cache()
            return self._translate(source)

    def _translate(self, source: str) -> str:
        batch = self.tokenizer(source, return_tensors="pt", truncation=True,
                               max_length=128).to(self.device)
        with self.torch.inference_mode():
            tokens = self.model.generate(
                **batch,
                forced_bos_token_id=self.tokenizer.get_lang_id("zh"),
                max_new_tokens=48 if self.fast_mode else 64,
                num_beams=1 if self.fast_mode else 2)
        chinese = self.tokenizer.batch_decode(tokens, skip_special_tokens=True)[0].strip()
        return self.converter.convert(chinese)
