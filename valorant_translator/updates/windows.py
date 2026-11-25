"""Launch the existing Inno Setup installer after the main EXE exits."""
from __future__ import annotations

import os
import subprocess
import sys
from base64 import b64encode
from pathlib import Path

from valorant_translator.config import APP_DIR
from valorant_translator.updates.github import (Release, UpdateError,
                                                _verified_file)

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "windows_updater.ps1"
RESULT = APP_DIR / "updates" / "last_result.txt"
APP_IDS = {"cpu": "{0357A036-BE6C-4799-881E-8E8CB660EB3A}",
           "gpu": "{32A11F0B-7D49-4C23-846A-D7CAFA048E12}"}

# The caller can report parser and parameter-binding errors before the script runs.
_RUN_UPDATER = """
$ErrorActionPreference = 'Stop'
try {
    $arguments = @{
        Installer = $env:VOICECN_UPDATE_INSTALLER
        AppExe = $env:VOICECN_UPDATE_APP
        ParentPid = [int]$env:VOICECN_UPDATE_PARENT
        ResultPath = $env:VOICECN_UPDATE_RESULT
        AppId = $env:VOICECN_UPDATE_APP_ID
    }
    & $env:VOICECN_UPDATE_SCRIPT @arguments
    exit $LASTEXITCODE
} catch {
    $reason = "更新脚本无法运行：$($_.Exception.Message)"
    try {
        Set-Content -LiteralPath $env:VOICECN_UPDATE_RESULT -Value $reason -Encoding UTF8
    } catch {
        [Console]::Error.WriteLine($_.Exception.Message)
    }
    [Console]::Error.WriteLine($reason)
    exit 1
}
"""


def _powershell_invocation(installer: Path, app: str, parent_pid: int,
                           app_id: str = "") -> tuple[list[str], dict[str, str]]:
    # PowerShell 5.1 expects UTF-16LE for EncodedCommand. Paths travel as data in the environment.
    command = b64encode(_RUN_UPDATER.encode("utf-16-le")).decode("ascii")
    return (["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-EncodedCommand", command],
            {**os.environ, "VOICECN_UPDATE_SCRIPT": str(SCRIPT),
             "VOICECN_UPDATE_INSTALLER": str(installer), "VOICECN_UPDATE_APP": app,
             "VOICECN_UPDATE_PARENT": str(parent_pid), "VOICECN_UPDATE_RESULT": str(RESULT),
             "VOICECN_UPDATE_APP_ID": app_id})


def start_silent_installer(path: Path, release: Release) -> None:
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        raise UpdateError("自动安装只适用于 Windows 安装版 EXE。")
    if path.name != release.installer_name or not _verified_file(path, release):
        raise UpdateError("安装包在启动前校验失败。")
    if not SCRIPT.is_file():
        raise UpdateError("独立更新脚本缺失，请重新安装完整测试包。")
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    with RESULT.open("a", encoding="utf-8") as log:
        log.write(f"已下载并校验：{path}\n准备启动更新器\n")
    edition = "cpu" if "_CPU_" in release.installer_name else "gpu"
    command, env = _powershell_invocation(path, sys.executable, os.getpid(), APP_IDS[edition])
    try:
        subprocess.Popen(command, env=env, close_fds=True,
                         creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    except OSError as exc:
        with RESULT.open("a", encoding="utf-8") as log:
            log.write(f"更新失败：无法启动独立更新程序：{exc}\n")
        raise UpdateError(f"无法启动独立更新程序：{exc}") from exc
