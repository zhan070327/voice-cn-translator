"""Register the display faces privately for this process and keep system fallbacks."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication

from valorant_translator.ui import tokens as t

FONT_DIR = Path(__file__).resolve().parents[2] / "assets" / "fonts"
DISPLAY_FILES = ("Rajdhani-Bold.ttf", "NotoSansCJKsc-Black.otf")


def load_display_fonts() -> tuple[str, ...]:
    app = QCoreApplication.instance()
    if not isinstance(app, QGuiApplication):
        return ()
    if not hasattr(app, "_voice_display_families"):
        families = []
        for filename in DISPLAY_FILES:
            path = FONT_DIR / filename
            font_id = QFontDatabase.addApplicationFont(str(path)) if path.is_file() else -1
            families.extend(QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else ())
        app._voice_display_families = tuple(families)
    return app._voice_display_families


def display_style() -> str:
    bundled = load_display_fonts()
    fallback = "PingFang SC" if sys.platform == "darwin" else t.BODY_FONT
    families = ", ".join(f'"{family}"' for family in (*bundled, fallback))
    weight = 900 if "Noto Sans CJK SC" in bundled else 700
    return f"font-family: {families}; font-weight: {weight};"


def track_caption(label) -> None:
    font = label.font()
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, t.CAPTION_TRACKING)
    label.setFont(font)
