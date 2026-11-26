"""Local text history and user corrections. Raw audio is never stored here."""
from __future__ import annotations

import csv
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from valorant_translator import config
from valorant_translator.translation.glossary import Glossary

if TYPE_CHECKING:
    from valorant_translator.pipeline import SubtitleResult


@dataclass(frozen=True)
class HistoryEntry:
    id: int
    created_at: str
    original: str
    chinese: str
    corrected_chinese: str | None
    language: str
    asr_seconds: float
    translation_seconds: float
    total_seconds: float
    corrected_original: str = ""
    machine_translation: str | None = None
    game: str = "valorant"
    target_language: str = "zh"
    glossary_rules: str = ""
    display_original: str = ""


_DETAILS = {
    "corrected_original": "TEXT NOT NULL DEFAULT ''",
    "machine_translation": "TEXT",
    "game": "TEXT NOT NULL DEFAULT 'valorant'",
    "target_language": "TEXT NOT NULL DEFAULT 'zh'",
    "glossary_rules": "TEXT NOT NULL DEFAULT ''",
    "display_original": "TEXT NOT NULL DEFAULT ''",
}


class HistoryStore:
    def __init__(self, path: Path | None = None):
        self.path = path or config.APP_DIR / "history.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as database:
            database.execute("""
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    original TEXT NOT NULL,
                    chinese TEXT NOT NULL,
                    corrected_chinese TEXT,
                    language TEXT NOT NULL,
                    asr_seconds REAL NOT NULL,
                    translation_seconds REAL NOT NULL,
                    total_seconds REAL NOT NULL
                )
            """)
            present = {column[1] for column in database.execute("PRAGMA table_info(history)")}
            for column, schema in _DETAILS.items():
                if column not in present:
                    database.execute(f"ALTER TABLE history ADD COLUMN {column} {schema}")
            database.execute("""
                CREATE TABLE IF NOT EXISTS corrections (
                    source_key TEXT NOT NULL,
                    language TEXT NOT NULL,
                    chinese TEXT NOT NULL,
                    PRIMARY KEY (source_key, language)
                )
            """)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=3)

    def add_result(self, result: SubtitleResult) -> None:
        with self._connect() as database:
            database.execute("""
                INSERT INTO history
                (created_at, original, chinese, language,
                 asr_seconds, translation_seconds, total_seconds, corrected_original,
                 machine_translation, game, target_language, glossary_rules, display_original)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (datetime.now().astimezone().isoformat(timespec="seconds"),
                  result.original, result.chinese, result.language,
                  result.asr_seconds, result.translation_seconds, result.total_seconds,
                  result.corrected_original, result.machine_translation, result.game,
                  result.target_language, result.glossary_rules, result.display_original))
            database.execute("DELETE FROM history WHERE id <= (SELECT MAX(id) - 1000 FROM history)")

    def _entries(self, condition, parameters) -> list[HistoryEntry]:
        with self._connect() as database:
            rows = database.execute("""
                SELECT id, created_at, original, chinese, corrected_chinese, language,
                       asr_seconds, translation_seconds, total_seconds, corrected_original,
                       machine_translation, game, target_language, glossary_rules, display_original
                FROM history
            """ + condition, parameters).fetchall()
        return [HistoryEntry(*row) for row in rows]

    def recent(self, limit: int = 100, *, target_language: str | None = None) -> list[HistoryEntry]:
        if target_language is not None:
            return self._entries(" WHERE target_language = ? ORDER BY id DESC LIMIT ?", (target_language, limit))
        return self._entries(" ORDER BY id DESC LIMIT ?", (limit,))

    def entry(self, entry_id: int) -> HistoryEntry | None:
        return next(iter(self._entries(" WHERE id = ?", (entry_id,))), None)

    def lookup_correction(self, source_key: str, language: str) -> str | None:
        with self._connect() as database:
            row = database.execute("""
                SELECT chinese FROM corrections WHERE source_key = ? AND language = ?
            """, (source_key, language)).fetchone()
        return row[0] if row else None

    def correct(self, entry_id: int, chinese: str, *, apply_future: bool = True) -> None:
        chinese = chinese.strip()
        with self._connect() as database:
            row = database.execute("SELECT original, language, target_language FROM history WHERE id = ?",
                                   (entry_id,)).fetchone()
            if row is None:
                raise ValueError("这条历史记录已不存在")
            if not chinese or (row[2] == "zh" and not re.search(r"[\u4e00-\u9fff]", chinese)):
                raise ValueError("请输入有效的译文")
            database.execute("UPDATE history SET corrected_chinese = ? WHERE id = ?", (chinese, entry_id))
            if apply_future and row[2] == "zh":  # Preserve the b4/b5 programmatic API.
                database.execute("""
                    INSERT INTO corrections (source_key, language, chinese) VALUES (?, ?, ?)
                    ON CONFLICT (source_key, language) DO UPDATE SET chinese = excluded.chinese
                """, (Glossary.normalize(row[0]), row[1], chinese))

    def clear_history(self) -> None:
        with self._connect() as database:
            database.execute("DELETE FROM history")

    def clear_all(self) -> None:
        with self._connect() as database:
            database.execute("DELETE FROM history")
            database.execute("DELETE FROM corrections")

    def export_csv(self, path: Path) -> None:
        with self._connect() as database:
            rows = database.execute("""
                SELECT created_at, original, chinese, corrected_chinese, language,
                       asr_seconds, translation_seconds, total_seconds, corrected_original,
                       machine_translation, game, target_language, glossary_rules
                FROM history ORDER BY id
            """).fetchall()
        with path.open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(("时间", "识别原文", "机器译文", "修正译文", "语言",
                             "识别秒数", "翻译秒数", "音频结束至显示秒数",
                             "纠错后原文", "原始模型译文", "游戏", "目标语言", "词库规则"))
            writer.writerows((*row[:5], *(value if value >= 0 else "" for value in row[5:8]),
                              *row[8:]) for row in rows)

    def import_csv(self, path: Path) -> tuple[int, int]:
        """Import archived subtitles as history only, including older Chinese CSV headers."""
        if path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("CSV 文件过大")
        rows, skipped = [], 0
        with path.open(encoding="utf-8-sig", newline="") as source:
            for row in csv.DictReader(source):
                try:
                    original, translated = row.get("识别原文"), row.get("机器译文") or row.get("中文字幕")
                    if not original or not translated:
                        raise ValueError
                    measurements = tuple(float(row[key]) if row.get(key) else -1.0 for key in
                                         ("识别秒数", "翻译秒数", "音频结束至显示秒数"))
                    if any(not -1 <= value < float("inf") for value in measurements):
                        raise ValueError
                    rows.append((row.get("时间") or "", original, translated, row.get("修正译文"),
                                 row.get("语言") or "", *measurements, row.get("纠错后原文") or "",
                                 row.get("原始模型译文"), row.get("游戏") or "valorant",
                                 row.get("目标语言") or "zh", row.get("词库规则") or ""))
                except (TypeError, ValueError):
                    skipped += 1
        with self._connect() as database:
            database.executemany("""
                INSERT INTO history (created_at, original, chinese, corrected_chinese, language, asr_seconds,
                translation_seconds, total_seconds, corrected_original, machine_translation, game,
                target_language, glossary_rules) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, rows[-1000:])
            database.execute("DELETE FROM history WHERE id <= (SELECT MAX(id) - 1000 FROM history)")
        return min(1000, len(rows)), skipped + max(0, len(rows) - 1000)
