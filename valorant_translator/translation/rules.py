"""Validated, locale-scoped glossary rules and their local storage."""
from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path

from valorant_translator import config
from valorant_translator.translation.glossary import Glossary

LOG = logging.getLogger(__name__)
RULE_TYPES = ("official_line", "asr_correction", "term", "generic")
MAX_RULES = 5000
MAX_JSON = 8 * 1024 * 1024


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_document(path: Path) -> dict:
    with path.open("rb") as handle:
        payload = handle.read(MAX_JSON + 1)
    if len(payload) > MAX_JSON:
        raise ValueError("文件过大")
    data = json.loads(payload.decode("utf-8-sig"))
    if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
        raise ValueError("不支持的文件版本")
    return data


def write_document(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if len(payload.encode("utf-8")) > MAX_JSON:
        raise ValueError("文件过大")
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)


@dataclass(frozen=True)
class Rule:
    game: str
    source: str
    target: str
    original: str
    translated: str = ""
    corrected: str = ""
    kind: str = "term"
    priority: int = 0
    confidence: float = 1.0
    enabled: bool = True
    origin: str = ""
    verified: bool = False
    updated: str = ""
    character: str = ""
    localized: str = ""

    @classmethod
    def parse(cls, raw: dict) -> Rule:
        if not isinstance(raw, dict):
            raise ValueError("无效词条")
        keys = {field.name for field in fields(cls)}
        try:
            rule = cls(**{key: value for key, value in raw.items() if key in keys})
            if any(type(value) is not str or len(value) > 2000 for value in (
                rule.original, rule.corrected, rule.translated, rule.origin, rule.localized, rule.character
            )):
                raise ValueError
            if not re.fullmatch(r"[a-z0-9_-]{1,32}", rule.game) or any(
                not re.fullmatch(r"[a-z]{2,3}(?:-[a-z0-9]{2,8})?", locale) for locale in (rule.source, rule.target)
            ):
                raise ValueError
            if not Glossary.normalize(rule.original) or rule.kind not in RULE_TYPES:
                raise ValueError
            if rule.kind == "asr_correction":
                if not Glossary.normalize(rule.corrected):
                    raise ValueError
            elif not rule.translated.strip():
                raise ValueError
            if (type(rule.priority) is not int or not -1000 <= rule.priority <= 1000
                    or type(rule.confidence) not in (int, float) or not 0 <= rule.confidence <= 1
                    or type(rule.enabled) is not bool or type(rule.verified) is not bool):
                raise ValueError
            if rule.updated:
                stamp = datetime.fromisoformat(rule.updated.replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    raise ValueError
            if rule.kind == "official_line" and rule.verified and not (rule.character and rule.origin):
                raise ValueError
            return rule
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("无效词条字段") from exc

    @property
    def key(self):
        return self.game, self.source, self.target, self.kind, Glossary.normalize(self.original)

    @property
    def rank(self):
        timestamp = datetime.fromisoformat(self.updated.replace("Z", "+00:00")).timestamp() if self.updated else 0
        return self.priority, self.verified, timestamp


def parse_rules(data: dict) -> list[Rule]:
    items = data.get("rules", [])
    if not isinstance(items, list) or len(items) > MAX_RULES:
        raise ValueError("无效词条列表")
    return [Rule.parse(item) for item in items]


class RuleStore:
    """Edits publish a new immutable runtime snapshot. Files are read only at startup/import."""
    def __init__(self, directory: Path | None = None):
        directory = directory or config.APP_DIR
        self.path = directory / "glossary_rules.json"
        self.backup = directory / "glossary_rules.last-good.json"
        self._lock = threading.RLock()
        self.rules: tuple[Rule, ...] = ()
        self.index: dict = {}
        for path in (self.path, self.backup):
            if not path.is_file():
                continue
            try:
                self._publish(parse_rules(read_document(path)))
                break
            except (OSError, ValueError):
                LOG.warning("Invalid user glossary: %s", path, exc_info=True)

    def _publish(self, rules) -> None:
        index = {}
        for rule in rules:
            if rule.enabled and (rule.key not in index or rule.rank >= index[rule.key].rank):
                index[rule.key] = rule
        self.rules, self.index = tuple(rules), index

    def save(self, rules) -> None:
        with self._lock:
            validated = parse_rules({"rules": [asdict(rule) for rule in rules]})
            write_document(self.backup, {"version": 1, "rules": [asdict(rule) for rule in self.rules]})
            write_document(self.path, {"version": 1, "rules": [asdict(rule) for rule in validated]})
            self._publish(validated)

    def add(self, rule: Rule) -> bool:
        with self._lock:
            conflict = any(item.key == rule.key for item in self.rules)
            self.save(dict.fromkeys((*self.rules, rule)))
            return conflict

    def export(self, path: Path) -> None:
        write_document(path, {"version": 1, "rules": [asdict(rule) for rule in self.rules]})

    def import_file(self, path: Path) -> int:
        incoming = parse_rules(read_document(path))
        with self._lock:
            existing = {rule.key for rule in self.rules}
            appended = tuple(dict.fromkeys((*self.rules, *incoming)))
            self.save(appended)
            return sum(rule.key in existing for rule in incoming)
