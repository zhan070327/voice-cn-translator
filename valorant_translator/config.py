from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

APP_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".valorant-translator")) / "ValorantTranslator"
MODEL_DIR = APP_DIR / "models"
CONFIG_FILE = APP_DIR / "config.json"


@dataclass
class Settings:
    device_id: str = ""
    language: str = "auto"
    whisper_model: str = "small"
    fast_mode: bool = True
    translation_model: str = "m2m100_418M"
    compute_device: str = "auto"
    font_size: int = 28
    overlay_x: int = 80
    overlay_y: int = 80
    overlay_opacity: int = 65
    overlay_screen: int = 0
    show_original: bool = True
    save_history: bool = True
    click_through: bool = False
    normal_window: bool = True
    model_source: str = "cos"
    game: str = "valorant"
    game_terms: bool = True
    official_lines: bool = True
    asr_correction: bool = True
    teammate_only: bool = True
    routing_intro_seen: bool = False
    routing_verified_id: str = ""
    accent: str = "#FF4655"
    background_theme: str = "default"
    background_color: str = ""
    ptt_hotkey: str = "<ctrl>+<shift>+z"
    microphone_id: str = ""
    onboarding_done: bool = False
    hotkey: str = "<ctrl>+<shift>+t"
    vad_level: int = 1
    max_task_age: float = 6.0
    translation_timeout: float = 4.0
    subtitle_seconds: float = 5.0

    @classmethod
    def load(cls) -> "Settings":
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self) -> None:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(CONFIG_FILE)
