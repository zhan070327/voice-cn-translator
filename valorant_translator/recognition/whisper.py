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
             "th": "泰语", "ja": "日语", "ko": "韩语", "zh": "中文（直接显示）"}
WHISPER_REPOS = {
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
}
_CHINESE_CALLOUT_HOTWORDS = (
    "无畏契约 A点 B点 C点 A大 B大 中路 中门 水烟 "
    "快攻 转点 集合 撤退 守住 封烟 扔闪 闪光 下包 拆包 队友"
)


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


def transcribe_segments(model, audio, locked, *, fast_mode: bool, hotwords: str | None):
    """Use the production decoder settings without applying application filters."""
    return model.transcribe(
        audio, language=locked, beam_size=2 if fast_mode else 3,
        best_of=1 if fast_mode else 3,
        temperature=0.0 if fast_mode else (0.0, 0.2, 0.4),
        max_new_tokens=64 if fast_mode else 96,
        vad_filter=False, condition_on_previous_text=False,
        without_timestamps=fast_mode, no_speech_threshold=0.5,
        hallucination_silence_threshold=None, hotwords=hotwords,
    )


def filter_segments(segments, info, locked, glossary, on_rejection=None):
    reliable = [s for s in segments if s.no_speech_prob < 0.55 and s.avg_logprob > -1.1]
    parts = [s.text.strip() for s in reliable]
    text = " ".join(p for p in parts if p).strip()
    lang = locked or info.language
    confidence = float(info.language_probability) if locked is None else 1.0
    han_count = sum("\u4e00" <= char <= "\u9fff" for char in text)
    if locked is None and lang not in {"ja", "ko"} and han_count >= 2 and han_count > sum(
            char.isascii() and char.isalpha() for char in text):
        lang = "zh"

    def reject(reason):
        if on_rejection:
            metrics = ", ".join(
                f"no_speech_prob={s.no_speech_prob:.3f}, avg_logprob={s.avg_logprob:.3f}"
                for s in segments[:8]
            )
            raw = " ".join(s.text.strip() for s in segments)
            probability = f"{confidence:.3f}" if locked is None else "手动"
            on_rejection(reason, f"原始文本={raw[:250]!r}；语言={lang}；置信度={probability}；"
                                 f"段落 {len(segments)} 条 [{metrics}]")
        return None

    if not segments:
        return reject("没有识别段落")
    if not reliable:
        return reject("段落未达标（no_speech_prob < 0.55、avg_logprob > -1.1）")
    if not text:
        return reject("识别文字为空")
    if lang not in LANGUAGES:
        return reject(f"不支持的语种 {lang}")
    if lang == "zh" and han_count < 2:
        return reject(f"中文仅 {han_count} 字，至少 2 字")
    if text.casefold().strip(" .,!?。！？") in {"a", "b", "ay", "aye", "ayy", "ayay"}:
        # A single vowel or letter cannot reliably distinguish a site callout from filler.
        return reject("单字母或语气词，无法确认报点")
    # Whisper is unreliable on very short clips; suppress uncertain automatic guesses.
    if glossary and glossary.is_uncertain_fragment(text):
        return reject("命中词库中的不确定片段")
    known_callout = bool(glossary and glossary.callout(text, lang))
    threshold = 0.55 if len(text) >= 12 else (0.55 if known_callout else 0.68)
    if locked is None and confidence < threshold:
        return reject(f"语种置信度 {confidence:.0%} 低于 {threshold:.0%}")
    minimum = 2 if lang == "zh" else 3
    if len(text) < minimum or not any(c.isalnum() for c in text):
        return reject(f"文本过短（{len(text)} 字符，至少 {minimum}）或无有效字符")
    if locked is None and known_callout and lang not in {"ja", "ko"} and text.isascii():
        lang = "en"
    return Recognition(text, lang, confidence)


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

    def transcribe_chinese_callout(self, audio: np.ndarray) -> Recognition | None:
        return self.transcribe(audio, "zh", hotwords=_CHINESE_CALLOUT_HOTWORDS)

    def transcribe(self, audio: np.ndarray, language: str, *, use_hotwords: bool = True,
                   hotwords: str | None = None) -> Recognition | None:
        options = {} if use_hotwords else {"use_hotwords": False}
        if hotwords is not None:
            options["hotwords"] = hotwords
        try:
            return self._transcribe(audio, language, **options)
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
            return self._transcribe(audio, language, **options)

    def _transcribe(self, audio: np.ndarray, language: str, *, use_hotwords: bool = True,
                    hotwords: str | None = None) -> Recognition | None:
        locked = None if language == "auto" else language
        glossary = getattr(self, "glossary", None)
        if hotwords is None and use_hotwords:
            hotwords = getattr(glossary, "hotwords", None)
        segments, info = transcribe_segments(self.model, audio, locked,
                                             fast_mode=getattr(self, "fast_mode", True), hotwords=hotwords)
        return filter_segments(list(segments), info, locked, glossary, getattr(self, "on_rejection", None))
