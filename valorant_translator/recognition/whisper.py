from __future__ import annotations

import logging
import gc
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from valorant_translator.config import MODEL_DIR
from valorant_translator.translation.glossary import Glossary

LOG = logging.getLogger(__name__)
LANGUAGES = {"en": "英语", "ms": "马来语", "hi": "印地语", "ta": "泰米尔语",
             "th": "泰语", "zh": "中文（直接显示）"}
WHISPER_REPOS = {
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
}


@dataclass(frozen=True)
class Recognition:
    text: str
    language: str
    probability: float


def cuda_available() -> bool:
    try:
        import ctranslate2
        return bool(ctranslate2.get_supported_compute_types("cuda"))
    except Exception:
        return False


class LocalWhisper:
    def __init__(self, model_name: str, requested_device: str, fast_mode: bool = True):
        from faster_whisper import WhisperModel

        path = MODEL_DIR / f"whisper-{model_name}"
        if not path.is_dir():
            raise FileNotFoundError(f"识别模型缺失：{path}。请先点击“下载模型”。")
        self.path = path
        self.fast_mode = fast_mode
        self.glossary = Glossary()
        self.device = "cuda" if requested_device != "cpu" and cuda_available() else "cpu"
        compute_type = "float16" if self.device == "cuda" else "int8"
        try:
            self.model = WhisperModel(str(path), device=self.device, compute_type=compute_type,
                                      local_files_only=True, num_workers=1)
        except Exception as exc:
            if self.device != "cuda":
                raise RuntimeError(f"识别模型加载失败：{exc}") from exc
            LOG.warning("Whisper CUDA load failed: %s", exc)
            sizes = ("tiny", "base", "small", "medium")
            for smaller in reversed(sizes[:sizes.index(model_name)]):
                smaller_path = MODEL_DIR / f"whisper-{smaller}"
                if smaller != model_name and smaller_path.is_dir():
                    try:
                        self.model = WhisperModel(str(smaller_path), device="cuda", compute_type="int8_float16",
                                                  local_files_only=True, num_workers=1)
                        self.path = smaller_path
                        LOG.warning("Using smaller Whisper model: %s", smaller)
                        return
                    except Exception:
                        LOG.exception("Smaller GPU model failed")
            LOG.warning("Falling back to CPU")
            self.device = "cpu"
            self.model = WhisperModel(str(path), device="cpu", compute_type="int8",
                                      local_files_only=True, num_workers=1)

    def transcribe(self, audio: np.ndarray, language: str, *, use_hotwords: bool = True) -> Recognition | None:
        try:
            return (self._transcribe(audio, language) if use_hotwords else self._transcribe(audio, language, use_hotwords=False))
        except RuntimeError as exc:
            if self.device != "cuda":
                raise
            from faster_whisper import WhisperModel
            LOG.warning("Whisper CUDA inference failed; reloading on CPU: %s", exc)
            del self.model
            gc.collect()
            self.device = "cpu"
            self.model = WhisperModel(str(self.path), device="cpu", compute_type="int8",
                                      local_files_only=True, num_workers=1)
            return (self._transcribe(audio, language) if use_hotwords else self._transcribe(audio, language, use_hotwords=False))

    def _transcribe(self, audio: np.ndarray, language: str, *, use_hotwords: bool = True) -> Recognition | None:
        locked = None if language == "auto" else language
        segments, info = self.model.transcribe(audio, language=locked,
                                               beam_size=2 if getattr(self, "fast_mode", True) else 3,
                                               best_of=1 if getattr(self, "fast_mode", True) else 3,
                                               temperature=0.0 if getattr(self, "fast_mode", True) else (0.0, 0.2, 0.4),
                                               max_new_tokens=64 if getattr(self, "fast_mode", True) else 96,
                                               vad_filter=False, condition_on_previous_text=False,
                                               without_timestamps=getattr(self, "fast_mode", True),
                                               no_speech_threshold=0.5, hallucination_silence_threshold=None,
                                               hotwords=getattr(getattr(self, "glossary", None), "hotwords", None) if use_hotwords else None)
        reliable = [s for s in segments if s.no_speech_prob < 0.55 and s.avg_logprob > -1.1]
        parts = [s.text.strip() for s in reliable]
        text = " ".join(p for p in parts if p).strip()
        lang = locked or info.language
        confidence = float(info.language_probability) if locked is None else 1.0
        han_count = sum("\u4e00" <= char <= "\u9fff" for char in text)
        if locked is None and han_count >= 2 and han_count > sum(
                char.isascii() and char.isalpha() for char in text):
            lang = "zh"
        if not text or lang not in LANGUAGES:
            return None
        if lang == "zh" and han_count < 2:
            return None
        if text.casefold().strip(" .,!?。！？") in {"a", "b", "ay", "aye", "ayy", "ayay"}:
            # A single vowel or letter cannot reliably distinguish a site callout from filler.
            return None
        # Whisper is unreliable on very short clips; suppress uncertain automatic guesses.
        glossary = getattr(self, "glossary", None)
        if glossary and glossary.is_uncertain_fragment(text):
            return None
        known_callout = bool(glossary and glossary.callout(text, lang))
        threshold = 0.55 if len(text) >= 12 else (0.55 if known_callout else 0.68)
        if locked is None and confidence < threshold:
            return None
        if len(text) < (2 if lang == "zh" else 3) or not any(c.isalnum() for c in text):
            return None
        if locked is None and known_callout and text.isascii():
            lang = "en"
        return Recognition(text, lang, confidence)
