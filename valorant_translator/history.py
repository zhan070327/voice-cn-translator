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
                 asr_seconds, translation_seconds, total_seconds)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (datetime.now().astimezone().isoformat(timespec="seconds"),
                  result.original, result.chinese, result.language,
                  result.asr_seconds, result.translation_seconds, result.total_seconds))
            database.execute("DELETE FROM history WHERE id <= (SELECT MAX(id) - 1000 FROM history)")

    def recent(self, limit: int = 100) -> list[HistoryEntry]:
        with self._connect() as database:
            rows = database.execute("""
                SELECT id, created_at, original, chinese, corrected_chinese, language,
                       asr_seconds, translation_seconds, total_seconds
                FROM history ORDER BY id DESC LIMIT ?
            """, (limit,)).fetchall()
        return [HistoryEntry(*row) for row in rows]

    def lookup_correction(self, source_key: str, language: str) -> str | None:
        with self._connect() as database:
            row = database.execute("""
                SELECT chinese FROM corrections WHERE source_key = ? AND language = ?
            """, (source_key, language)).fetchone()
        return row[0] if row else None

    def correct(self, entry_id: int, chinese: str) -> None:
        chinese = chinese.strip()
        if not re.search(r"[\u4e00-\u9fff]", chinese):
            raise ValueError("修正译文需要包含中文")
        with self._connect() as database:
            row = database.execute("SELECT original, language FROM history WHERE id = ?",
                                   (entry_id,)).fetchone()
            if row is None:
                raise ValueError("这条历史记录已不存在")
            source_key = Glossary.normalize(row[0])
            database.execute("UPDATE history SET corrected_chinese = ? WHERE id = ?",
                             (chinese, entry_id))
            database.execute("""
                INSERT INTO corrections (source_key, language, chinese) VALUES (?, ?, ?)
                ON CONFLICT (source_key, language) DO UPDATE SET chinese = excluded.chinese
            """, (source_key, row[1], chinese))

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
                       asr_seconds, translation_seconds, total_seconds
                FROM history ORDER BY id
            """).fetchall()
        with path.open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(("时间", "识别原文", "机器译文", "修正译文", "语言",
                             "识别秒数", "翻译秒数", "音频结束至显示秒数"))
            writer.writerows(rows)
