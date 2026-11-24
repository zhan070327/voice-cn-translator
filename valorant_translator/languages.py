"""Shared language catalog. Source and target validation are independent."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

LANGUAGE_FILE = Path(__file__).resolve().parents[1] / "config" / "languages.json"


@dataclass(frozen=True)
class Language:
    code: str
    name: str
    asr: bool = True
    translation: bool = True


class LanguageCatalog:
    def __init__(self, languages=()):
        self._languages: dict[str, Language] = {}
        for language in languages:
            self.register(language)

    @classmethod
    def from_file(cls, path: Path) -> "LanguageCatalog":
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Invalid language catalog")
        return cls(Language(code, **entry) for code, entry in data.items())

    def register(self, language: Language) -> None:
        if not language.code or not language.name or language.code == "auto":
            raise ValueError("Invalid language registration")
        self._languages[language.code] = language

    def source(self, code: str) -> str:
        if code != "auto" and not self.supports_asr(code):
            raise ValueError(f"不支持的识别语言：{code}")
        return code

    def target(self, code: str) -> str:
        if not self.supports_translation(code):
            raise ValueError(f"不支持的目标语言：{code}")
        return code

    def supports_asr(self, code: str) -> bool:
        return bool(self._languages.get(code) and self._languages[code].asr)

    def supports_translation(self, code: str) -> bool:
        return bool(self._languages.get(code) and self._languages[code].translation)

    def names(self) -> dict[str, str]:
        return {code: item.name for code, item in self._languages.items() if item.asr}


DEFAULT_LANGUAGES = LanguageCatalog.from_file(LANGUAGE_FILE)
