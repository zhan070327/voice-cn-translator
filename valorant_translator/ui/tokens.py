"""Shared visual tokens for the desktop window, dialogs, and subtitles."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Skin:
    background: str
    panel: str
    accent: str
    light_origin: tuple[float, float]


SKINS = {
    "valorant": Skin("#111214", "#232328", "#FF706A", (0.95, 0.88)),
    "cs2": Skin("#141310", "#25221C", "#E9BA64", (0.56, 0.06)),
    "apex": Skin("#151210", "#29211D", "#F29D6A", (0.06, 0.46)),
    "overwatch2": Skin("#101418", "#1F2830", "#87B8DC", (0.95, 0.44)),
    "general": Skin("#111416", "#22282C", "#A6BBC5", (0.12, 0.75)),
}

# Legacy background preferences remain independent of the current game's skin.
BACKGROUND_PRESETS = {
    "default": ("默认深色", ("#131729", "#101323", "#347a9d", "#984257", "#56548a")),
    "crimson": ("瓦红", ("#251921", "#17121e", "#8f4751", "#9c493e", "#5b4e83")),
    "indigo": ("蓝紫", ("#121b2d", "#171329", "#367ea3", "#68449d", "#4e65a3")),
}
DEFAULT_BACKGROUND_COLOR = "#47768f"

TEXT = "#F5F5F5"
MUTED = "#B8C0C9"
EDGE = "#4C5563"
SUCCESS = "#8ED6A3"
WARNING = "#F1BF66"
ERROR = "#FF8585"
INFO = "#82BAEA"

BODY_FONT = "Microsoft YaHei UI"
NUMBER_FONT = "Consolas"
CAPTION, BODY, SECTION, TITLE, OVERLAY, SUBTITLE = 12, 16, 20, 24, 30, 36
INLINE, INSET, GAP, SPACE, WIDE = 8, 16, 24, 32, 48
TIGHT = 4
SIDEBAR_WIDTH, VOLUME_WIDTH, UPDATE_WIDTH = 176, 96, 152
GLOSSARY_LOAD_MS, HISTORY_LOAD_MS, TOAST_MS = 40, 40, 2400
RADIUS, CONTROL_RADIUS = 16, 8
INTERACTION_MS, SKIN_MS, PAGE_MS, SUCCESS_MS = 200, 300, 360, 300
PRESS_MS = 80  # Retain the existing physical push-button feedback.
LEGACY_ACCENT = "#ff4655"
DEFAULT_ACCENT = SKINS["valorant"].accent


def skin_for(game):
    return SKINS.get(game, SKINS["general"])
