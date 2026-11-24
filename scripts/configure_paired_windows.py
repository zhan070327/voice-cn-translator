"""Embed one routing configuration for both mirrors of each Windows edition."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from valorant_translator.updates.domestic import load_channel
from valorant_translator.updates.github import CHANNEL_FILE, validate_repository


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("repository")
    parser.add_argument("edition", choices=("gpu", "cpu"))
    args = parser.parse_args()
    channel = load_channel(Path("config/update_channel_domestic.json"))
    channel.update(channel="dual", edition=args.edition,
                   github_repository=validate_repository(args.repository))
    CHANNEL_FILE.write_text(json.dumps(channel, indent=2) + "\n", encoding="utf-8")
    load_channel()


if __name__ == "__main__":
    main()
