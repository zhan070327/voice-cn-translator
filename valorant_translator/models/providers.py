"""Replaceable local ASR and machine-translation providers."""
from __future__ import annotations

from typing import Callable, Protocol

from valorant_translator.config import Settings, get_model_dir


class ASRProvider(Protocol):
    device: str

    def transcribe(self, audio, language: str): ...

    def transcribe_chinese_callout(self, audio): ...


class TranslationProvider(Protocol):
    device: str

    def translate(self, source: str, language: str, target: str = "zh") -> str: ...

    def translate_to_english_details(self, source: str) -> tuple[str, str | None, str]: ...


class ModelManager:
    def __init__(self):
        self._asr: dict[str, Callable[[Settings], ASRProvider]] = {}
        self._translation: dict[str, Callable[[Settings], TranslationProvider]] = {}

    def register_asr(self, name: str, factory: Callable[[Settings], ASRProvider]) -> None:
        self._asr[name] = factory

    def register_translation(self, name: str, factory: Callable[[Settings], TranslationProvider]) -> None:
        self._translation[name] = factory

    def create_asr(self, settings: Settings) -> ASRProvider:
        try:
            return self._asr[settings.asr_provider](settings)
        except KeyError as exc:
            raise ValueError(f"未知识别模型接口：{settings.asr_provider}") from exc

    def create_translation(self, settings: Settings) -> TranslationProvider:
        try:
            return self._translation[settings.translation_model](settings)
        except KeyError as exc:
            raise ValueError(f"未知翻译模型接口：{settings.translation_model}") from exc


def create_local_model(factory, settings: Settings, *args):
    if settings.model_dir:
        return factory(*args, model_dir=get_model_dir(settings))
    return factory(*args)


def default_model_manager(asr_factory=None, translation_factory=None) -> ModelManager:
    if asr_factory is None:
        from valorant_translator.recognition.whisper import LocalWhisper
        asr_factory = lambda s: create_local_model(LocalWhisper, s, s.whisper_model, s.compute_device, s.fast_mode)
    if translation_factory is None:
        from valorant_translator.translation.m2m import LocalM2M
        translation_factory = lambda s: create_local_model(LocalM2M, s, s.compute_device, s.fast_mode)
    manager = ModelManager()
    manager.register_asr("whisper", asr_factory)
    manager.register_translation("m2m100_418M", translation_factory)
    return manager


def translate_pair(provider: TranslationProvider, text: str, source: str, target: str) -> str:
    # The two-argument call preserves the installed M2M provider and older adapters.
    return provider.translate(text, source) if target == "zh" else provider.translate(text, source, target)
