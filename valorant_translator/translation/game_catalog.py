"""Discover game dictionaries from files, without a core game switch."""
from __future__ import annotations

import json
from pathlib import Path

DEFAULT_DIR = Path(__file__).resolve().parents[2] / "config"
LEGACY_NAMES = {"valorant": "Valorant", "cs2": "CS2", "apex": "Apex",
                "overwatch2": "Overwatch 2", "general": "通用"}


class GameCatalog:
    def __init__(self, directory: Path = DEFAULT_DIR):
        self.directory = Path(directory)

    def paths(self) -> dict[str, Path]:
        found = {}
        original = self.directory / "glossary.json"
        if original.is_file():
            found["valorant"] = original
        for path in sorted(self.directory.glob("glossary_*.json")):
            game = path.stem.removeprefix("glossary_")
            if game != "legacy_b4" and game:
                found[game] = path
        return found

    def names(self) -> dict[str, str]:
        names = {}
        for game, path in self.paths().items():
            label = LEGACY_NAMES.get(game, game)
            if game not in LEGACY_NAMES:
                try:
                    metadata = json.loads(path.read_text(encoding="utf-8-sig")).get("game_name")
                    if isinstance(metadata, str) and metadata.strip():
                        label = metadata.strip()
                except (OSError, ValueError, AttributeError):
                    pass
            names[game] = label
        return names

    def path(self, game: str) -> Path:
        paths = self.paths()
        return paths.get(game, paths.get("valorant", self.directory / "glossary.json"))
