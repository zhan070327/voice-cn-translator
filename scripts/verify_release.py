"""Fail the Windows build if UI and installer versions drift apart."""
from __future__ import annotations

import re
import sys
from pathlib import Path

from valorant_translator.version import APP_VERSION
from valorant_translator.updates.domestic import load_channel
from valorant_translator.updates.github import UpdateError

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    source = (ROOT / "installer" / "ValorantTranslator.iss").read_text(encoding="utf-8")
    match = re.search(r'^#define MyAppVersion "([^"]+)"$', source, re.MULTILINE)
    if not match or match.group(1) != APP_VERSION:
        raise SystemExit(f"版本不一致：程序 {APP_VERSION}，安装脚本 {match.group(1) if match else '缺失'}")
    if "--require-update-channel" in sys.argv[1:]:
        try:
            channel = load_channel()
        except UpdateError as exc:
            raise SystemExit(f"无法制作带更新功能的公开测试安装包：{exc}") from exc
        print(f"Update channel OK: {channel['channel']}")
    print(f"Release version OK: {APP_VERSION}")


if __name__ == "__main__":
    main()
