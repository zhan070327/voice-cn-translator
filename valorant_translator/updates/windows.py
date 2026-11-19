"""Launch the existing Inno Setup installer after the main EXE exits."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from valorant_translator.config import APP_DIR
from valorant_translator.updates.github import (INSTALLER_NAME, Release, UpdateError,
                                                _verified_file)

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "windows_updater.ps1"
RESULT = APP_DIR / "updates" / "last_result.txt"


def start_silent_installer(path: Path, release: Release) -> None:
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        raise UpdateError("自动安装只适用于 Windows 安装版 EXE。")
    if path.name != INSTALLER_NAME or not _verified_file(path, release):
        raise UpdateError("安装包在启动前校验失败。")
    if not SCRIPT.is_file():
        raise UpdateError("独立更新脚本缺失，请重新安装完整测试包。")
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    RESULT.unlink(missing_ok=True)
    try:
        subprocess.Popen([
            "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(SCRIPT), "-Installer", str(path), "-AppExe", sys.executable,
            "-ParentPid", str(os.getpid()), "-ResultPath", str(RESULT),
        ], creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True)
    except OSError as exc:
        raise UpdateError(f"无法启动独立更新程序：{exc}") from exc
