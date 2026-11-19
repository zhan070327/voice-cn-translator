"""Create the update manifest from the exact installer that will be uploaded."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from scripts.configure_domestic_updates import configure
from valorant_translator.updates.domestic import parse_manifest
from valorant_translator.updates.github import INSTALLER_NAME
from valorant_translator.version import APP_VERSION


def generate(installer: Path, manifest_url: str, download_url: str, notes: str) -> dict:
    channel = configure(manifest_url, download_url)
    if installer.name != INSTALLER_NAME or not installer.is_file():
        raise ValueError(f"找不到安装包 {INSTALLER_NAME}")
    digest = hashlib.sha256()
    with installer.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    data = {"version": APP_VERSION, "download_url": download_url,
            "sha256": digest.hexdigest(), "size": installer.stat().st_size,
            "release_notes": notes}
    parse_manifest(json.dumps(data).encode(), channel["download_hosts"], "0")
    return data


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--installer", type=Path, default=Path("dist") / INSTALLER_NAME)
    parser.add_argument("--manifest-url", required=True)
    parser.add_argument("--download-url", required=True)
    parser.add_argument("--notes-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("dist") / "latest.json")
    args = parser.parse_args()
    notes = args.notes_file.read_text(encoding="utf-8").strip()
    data = generate(args.installer, args.manifest_url, args.download_url, notes)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Manifest ready: {args.output}")


if __name__ == "__main__":
    main()
