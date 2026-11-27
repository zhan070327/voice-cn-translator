"""Fail the Windows build if UI and installer versions drift apart."""
from __future__ import annotations

import re
import sys
from pathlib import Path

from packaging.version import Version

from valorant_translator.version import APP_VERSION
from valorant_translator.updates.domestic import load_channel
from valorant_translator.updates.github import UpdateError

ROOT = Path(__file__).resolve().parents[1]


def write_executable_version(path: Path) -> None:
    """Create the Windows version resource used by PyInstaller."""
    release = Version(APP_VERSION).release
    version = (*release, *(0,) * (4 - len(release)))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"""VSVersionInfo(
    ffi=FixedFileInfo(filevers={version!r}, prodvers={version!r},
                      mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1,
                      subtype=0x0, date=(0, 0)),
    kids=[
        StringFileInfo([StringTable('040904B0', [
            StringStruct('FileVersion', {APP_VERSION!r}),
            StringStruct('ProductVersion', {APP_VERSION!r}),
        ])]),
        VarFileInfo([VarStruct('Translation', [1033, 1200])]),
    ],
)
""", encoding="utf-8")


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
    if "--write-exe-version" in sys.argv[1:]:
        write_executable_version(ROOT / ".venv" / "windows-version.txt")
    print(f"Release version OK: {APP_VERSION}")


if __name__ == "__main__":
    main()
