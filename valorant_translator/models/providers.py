"""Replaceable local ASR and machine-translation providers."""
from __future__ import annotations

from typing import Callable, Protocol

from valorant_translator.config import (
    LOCAL_ASR, LOCAL_TRANSLATION, ONLINE_ASR, ONLINE_TRANSLATION, Settings, get_model_dir,
)


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
    manager.register_asr(LOCAL_ASR, asr_factory)
    manager.register_translation(LOCAL_TRANSLATION, translation_factory)

    def online_asr(settings):
        from valorant_translator.models.azure import AzureSpeech
        from valorant_translator.models.online import FallbackASR

        _require_online(settings)
        return FallbackASR(AzureSpeech(settings), lambda: asr_factory(settings))

    def online_translation(settings):
        from valorant_translator.models.azure import AzureTranslator
        from valorant_translator.models.online import FallbackTranslation

        _require_online(settings)
        return FallbackTranslation(AzureTranslator(settings), lambda: translation_factory(settings))

    manager.register_asr(ONLINE_ASR, online_asr)
    manager.register_translation(ONLINE_TRANSLATION, online_translation)
    return manager


def _require_online(settings: Settings) -> None:
    if settings.speed_mode != "extreme" or not settings.online_configured():
        from valorant_translator.ui.texts import tr

        raise ValueError(tr("engine.credentials_required"))


def configure_fallback_status(provider, on_status) -> None:
    if hasattr(provider, "on_fallback"):
        from valorant_translator.ui.texts import tr

        provider.on_fallback = lambda stage: on_status(tr(f"engine.fallback_{stage}"))


def translate_pair(provider: TranslationProvider, text: str, source: str, target: str) -> str:
    # The two-argument call preserves the installed M2M provider and older adapters.
    return provider.translate(text, source) if target == "zh" else provider.translate(text, source, target)
