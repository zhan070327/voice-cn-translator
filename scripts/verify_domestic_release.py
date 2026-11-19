"""Gate GitHub publication on the same publicly available COS release."""
from __future__ import annotations

import argparse
from pathlib import Path
from urllib.request import Request, urlopen

from scripts.publish_cos import DOMESTIC_CONFIG, installer_url
from valorant_translator.updates.domestic import find_update, load_channel, validate_url
from valorant_translator.updates.github import UpdateError
from valorant_translator.version import APP_VERSION


def verify(notes_file: Path) -> None:
    channel = load_channel(DOMESTIC_CONFIG)
    release = find_update(channel, current="0")
    if release is None or str(release.version) != APP_VERSION:
        raise UpdateError(f"COS 未发布当前版本 {APP_VERSION}；GitHub 发布已停止。")
    if release.url != installer_url():
        raise UpdateError("COS 安装包地址与当前版本路径不一致。")
    notes = notes_file.read_text(encoding="utf-8").strip()
    if not notes or release.notes != notes:
        raise UpdateError("国内外更新日志不一致。")
    with urlopen(Request(release.url, method="HEAD"), timeout=20) as response:
        validate_url(response.geturl(), channel["download_hosts"])
        if int(response.headers.get("Content-Length", "-1")) != release.size:
            raise UpdateError("COS 公开安装包大小与清单不一致。")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--notes-file", type=Path, required=True)
    args = parser.parse_args()
    try:
        verify(args.notes_file)
    except Exception as exc:
        raise SystemExit(f"双通道发布校验失败：{exc}") from None
    print(f"COS version {APP_VERSION} is public and matches release notes; GitHub may publish.")


if __name__ == "__main__":
    main()
