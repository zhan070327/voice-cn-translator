from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock

APP_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".valorant-translator")) / "ValorantTranslator"
MODEL_DIR = APP_DIR / "models"
CONFIG_FILE = APP_DIR / "config.json"
LOCAL_ASR = "whisper"
LOCAL_TRANSLATION = "m2m100_418M"
ONLINE_ASR = "samefreq_cloud_asr"
ONLINE_TRANSLATION = "samefreq_cloud_translation"
DEFAULT_CLOUD_URL = "https://cloud.samefreq.example"
_SAVE_LOCK = Lock()
SPEED_MODES = {
    "extreme": (ONLINE_ASR, ONLINE_TRANSLATION, True),
    "balanced": (LOCAL_ASR, LOCAL_TRANSLATION, True),
    "precise": (LOCAL_ASR, LOCAL_TRANSLATION, False),
}


@dataclass
class Settings:
    device_id: str = ""
    language: str = "auto"
    target_language: str = "zh"
    asr_provider: str = LOCAL_ASR
    whisper_model: str = "tiny"
    fast_mode: bool = True
    translation_model: str = LOCAL_TRANSLATION
    speed_mode: str = "balanced"
    cloud_url: str = DEFAULT_CLOUD_URL
    cloud_access_token: str = field(default="", repr=False)
    cloud_refresh_token: str = field(default="", repr=False)
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
    model_dir: str = ""
    game: str = "valorant"
    game_terms: bool = True
    official_lines: bool = True
    asr_correction: bool = True
    teammate_only: bool = True
    routing_intro_seen: bool = False
    routing_verified_id: str = ""
    accent: str = "#FF4655"
    game_accents: dict[str, str] = field(default_factory=dict)
    reduce_motion: bool = False
    background_theme: str = "default"
    background_color: str = ""
    ptt_hotkey: str = "<ctrl>+<shift>+z"
    microphone_id: str = ""
    onboarding_done: bool = False
    hotkey: str = "<ctrl>+<shift>+t"
    vad_level: int = 1
    max_task_age: float = 9.0
    translation_timeout: float = 4.0
    subtitle_seconds: float = 5.0

    @property
    def source_language(self) -> str:
        """Legacy `language` remains the persisted source-language setting."""
        return self.language

    def select_speed_mode(self, mode: str) -> bool:
        self.asr_provider, self.translation_model, self.fast_mode = SPEED_MODES[mode]
        self.speed_mode = mode
        if mode == "precise":
            self.game_terms = self.official_lines = self.asr_correction = True
        return True

    @classmethod
    def load(cls) -> "Settings":
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            settings = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
            if settings.speed_mode not in SPEED_MODES:
                settings.speed_mode = "balanced"
            if settings.speed_mode == "extreme" and settings.asr_provider == ONLINE_ASR:
                settings.asr_provider, settings.translation_model = ONLINE_ASR, ONLINE_TRANSLATION
            else:
                if settings.speed_mode == "extreme":
                    settings.select_speed_mode("balanced")
                settings.asr_provider, settings.translation_model = LOCAL_ASR, LOCAL_TRANSLATION
            if any(key.startswith("azure_") for key in data):
                try:
                    settings.save()
                except OSError:
                    pass
            return settings
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self) -> None:
        with _SAVE_LOCK:
            APP_DIR.mkdir(parents=True, exist_ok=True)
            temporary = NamedTemporaryFile(mode="w", encoding="utf-8", dir=CONFIG_FILE.parent, delete=False)
            tmp = Path(temporary.name)
            try:
                with temporary:
                    json.dump(asdict(self), temporary, ensure_ascii=False, indent=2)
                tmp.replace(CONFIG_FILE)
            finally:
                tmp.unlink(missing_ok=True)


def get_model_dir(settings: Settings | None = None, *, default: Path | None = None) -> Path:
    """Resolve the saved model location, keeping the existing default for old settings."""
    settings = settings if settings is not None else Settings.load()
    location = settings.model_dir
    if isinstance(location, str) and location.strip():
        return Path(location).expanduser().resolve()
    return default if default is not None else MODEL_DIR
