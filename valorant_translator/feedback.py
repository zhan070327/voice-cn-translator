"""Offline feedback inbox. Entries are candidates until explicitly reviewed."""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from valorant_translator import config
from valorant_translator.translation.glossary import Glossary
from valorant_translator.translation.rules import Rule, now, read_document, write_document

LOG = logging.getLogger(__name__)
ERROR_TYPES = ("语音识别错误", "游戏术语错误", "官方台词错误", "普通翻译错误", "其他问题")
STATUSES = ("Pending", "Approved", "Rejected")
_FIELDS = ("original", "corrected_original", "machine_translation", "displayed", "suggested",
           "game", "source", "target", "rules", "category", "note", "created_at")


def _parse(entry: dict, *, preserve_status=False) -> dict:
    if not isinstance(entry, dict):
        raise ValueError("反馈条目格式无效")
    result = {key: entry.get(key, "") for key in _FIELDS}
    if any(not isinstance(value, str) or len(value) > 2000 for value in result.values()):
        raise ValueError("反馈字段格式无效")
    if not all(result[key].strip() for key in ("original", "suggested", "game", "source", "target")):
        raise ValueError("反馈缺少原文、建议译文、游戏或语言")
    if result["category"] not in ERROR_TYPES:
        raise ValueError("未知的反馈类型")
    Rule.parse(dict(game=result["game"], source=result["source"], target=result["target"],
                    original=result["original"], translated=result["suggested"]))
    keys = tuple(Glossary.normalize(result[key]) for key in ("original", "suggested"))
    identity = (*keys, result["game"], result["source"], result["target"], result["category"])
    result["id"] = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
    result["status"] = entry.get("status", "Pending") if preserve_status else "Pending"
    if result["status"] not in STATUSES:
        raise ValueError("未知的审核状态")
    reviewed = entry.get("reviewed_at", "") if preserve_status else ""
    result["reviewed_at"] = reviewed if isinstance(reviewed, str) and len(reviewed) < 100 else ""
    return result


class FeedbackStore:
    def __init__(self, path: Path | None = None):
        self.path = path or config.APP_DIR / "translation-feedback.json"
        self.entries: dict[str, dict] = {}
        if self.path.is_file():
            try:
                self.entries = {item["id"]: item for item in self._read(self.path, preserve_status=True)}
            except (OSError, ValueError):
                LOG.warning("Could not read local feedback", exc_info=True)
                self.unreadable = True
                return
        self.unreadable = False

    @staticmethod
    def _read(path, *, preserve_status=False):
        document = read_document(path)
        items = document.get("feedback")
        if not isinstance(items, list) or len(items) > 5000:
            raise ValueError("无效的反馈文件")
        return [_parse(item, preserve_status=preserve_status) for item in items]

    def _save(self, entries):
        if self.unreadable:
            raise ValueError("反馈文件读取失败，请先备份原文件再恢复。")
        if len(entries) > 5000:
            raise ValueError("反馈记录达到上限")
        write_document(self.path, {"version": 1, "feedback": list(entries.values())})
        self.entries = entries

    def add(self, history, suggested, category, note) -> bool:
        record = _parse({"original": history.original, "corrected_original": history.corrected_original,
                         "machine_translation": history.machine_translation or "",
                         "displayed": history.chinese, "suggested": suggested.strip(), "category": category,
                         "note": note, "game": history.game, "source": history.language,
                         "target": history.target_language, "rules": history.glossary_rules or "",
                         "created_at": history.created_at})
        if record["id"] in self.entries:
            return False
        self._save({**self.entries, record["id"]: record})
        return True

    def import_file(self, path: Path) -> tuple[int, int]:
        items = self._read(path)
        entries = dict(self.entries)
        duplicates = 0
        for entry in items:
            if entry["id"] in entries:
                duplicates += 1
                continue
            entries[entry["id"]] = entry
        self._save(entries)
        return len(items) - duplicates, duplicates

    def review(self, key: str, status: str) -> None:
        if status not in STATUSES[1:]:
            raise ValueError("请选择通过或拒绝")
        record = {**self.entries[key], "status": status, "reviewed_at": now()}
        self._save({**self.entries, key: record})

    def export(self, path: Path, entries=None) -> None:
        records = list(self.entries.values()) if entries is None else list(entries)
        write_document(path, {"version": 1, "feedback": records})

    def approved_candidates(self) -> list[Rule]:
        return [Rule(game=entry["game"], source=entry["source"], target=entry["target"],
                     original=entry["corrected_original"] or entry["original"], translated=entry["suggested"],
                     origin="已审核反馈 " + entry["id"][:12], updated=entry["reviewed_at"])
                for entry in self.entries.values() if entry["status"] == "Approved"]
