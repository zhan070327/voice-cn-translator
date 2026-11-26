"""Launch the existing Inno Setup installer after the main EXE exits."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from base64 import b64encode
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import psutil
from filelock import FileLock, Timeout
from packaging.version import Version

from valorant_translator.config import APP_DIR
from valorant_translator.updates.github import INSTALLERS, Release, UpdateError, _verified_file

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "windows_updater.ps1"
RESULT = APP_DIR / "updates" / "last_result.txt"
APP_IDS = {"cpu": "{0357A036-BE6C-4799-881E-8E8CB660EB3A}",
           "gpu": "{32A11F0B-7D49-4C23-846A-D7CAFA048E12}"}

# The caller can report parser and parameter-binding errors before the script runs.
_RUN_UPDATER = """
$ErrorActionPreference = 'Stop'
$exitCode = 1
try {
    $arguments = @{
        Installer = $env:VOICECN_UPDATE_INSTALLER
        AppExe = $env:VOICECN_UPDATE_APP
        ParentPid = [int]$env:VOICECN_UPDATE_PARENT
        ResultPath = $env:VOICECN_UPDATE_RESULT
        AppId = $env:VOICECN_UPDATE_APP_ID
        ExpectedVersion = $env:VOICECN_UPDATE_VERSION
    }
    & $env:VOICECN_UPDATE_SCRIPT @arguments
    $exitCode = $LASTEXITCODE
} catch {
    $reason = "更新脚本无法运行：$($_.Exception.Message)"
    try {
        if ($env:VOICECN_UPDATE_LOCK) {
            $reason = "$(Get-Date -Format o) $reason"
        }
        Add-Content -LiteralPath $env:VOICECN_UPDATE_RESULT -Value $reason -Encoding UTF8
    } catch {
        [Console]::Error.WriteLine($_.Exception.Message)
    }
    [Console]::Error.WriteLine($reason)
} finally {
    if ($env:VOICECN_UPDATE_LOCK) {
        try {
            $pending = $env:VOICECN_UPDATE_RESULT
            $completed = $env:VOICECN_UPDATE_COMPLETED
            if ([IO.File]::Exists($completed)) {
                [IO.File]::Replace($pending, $completed, [NullString]::Value)
            } else {
                [IO.File]::Move($pending, $completed)
            }
            Remove-Item -LiteralPath $env:VOICECN_UPDATE_LOCK -ErrorAction SilentlyContinue
        } catch {
            [Console]::Error.WriteLine($_.Exception.Message)
            $exitCode = 1
        }
    }
}
exit $exitCode
"""

_LOG_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\S+ ")
_INSTALLER_PREFIXES = ("安装器路径：", "已下载并校验：", "启动安装器：", "更新器已启动：")


@dataclass(frozen=True)
class UpdateResult:
    message: str
    summary: str
    installer: Path | None
    succeeded: bool


def _state_lock() -> FileLock:
    # This lock serializes Python's launcher hand-off and recovery on startup.
    return FileLock(RESULT.with_suffix(".state.lock"), timeout=0)


def _marker_has_live_updater(marker: Path) -> bool:
    try:
        pid, created_at = marker.read_text(encoding="ascii").split()
        return psutil.Process(int(pid)).create_time() == float(created_at)
    except (ValueError, FileNotFoundError, psutil.NoSuchProcess):
        return False
    except psutil.AccessDenied:
        return True


def _updater_running(marker: Path, pending: Path) -> bool:
    if not marker.is_file():
        return False
    if _marker_has_live_updater(marker):
        return True
    if pending.is_file():
        if _read_report(pending) is None:
            _write_update_step(pending, "更新失败：更新器意外退出，安装结果未知。")
        pending.replace(RESULT)
    marker.unlink(missing_ok=True)
    return False


def update_in_progress() -> bool:
    marker = RESULT.with_suffix(".lock")
    if not marker.is_file():
        return False
    try:
        with _state_lock():
            return _updater_running(marker, RESULT.with_suffix(".pending.txt"))
    except (OSError, Timeout):
        return marker.is_file()


def read_update_result() -> UpdateResult | None:
    if not RESULT.parent.is_dir():
        return None
    try:
        with _state_lock():
            if _updater_running(RESULT.with_suffix(".lock"), RESULT.with_suffix(".pending.txt")):
                return None
            return _read_report(RESULT)
    except (OSError, Timeout):
        return None


def _read_report(path: Path) -> UpdateResult | None:
    try:
        message = path.read_text(encoding="utf-8-sig", errors="replace").strip()
    except OSError:
        return None

    installer = None
    succeeded = None
    summary = ""
    for line in message.splitlines():
        step = _LOG_TIMESTAMP.sub("", line)
        for prefix in _INSTALLER_PREFIXES:
            if step.startswith(prefix) and step[len(prefix):]:
                installer = Path(step[len(prefix):])
                break
        if step.startswith("更新成功；已重开新程序"):
            succeeded = True
            summary = step
        elif step.startswith(("更新失败：", "更新脚本无法运行：")):
            succeeded = False
            summary = step
    if succeeded is None:
        return None
    return UpdateResult(message, summary, installer, succeeded)


def is_cached_installer(path: Path) -> bool:
    """Check the recorded path still points to an installer in the update cache."""
    try:
        installer = path.resolve(strict=True)
        version, name = installer.relative_to(RESULT.parent.resolve()).parts
        return name in INSTALLERS.values() and str(Version(version)) == version and installer.is_file()
    except (OSError, RuntimeError, ValueError):
        return False


def _powershell_invocation(installer: Path, app: str, parent_pid: int,
                           app_id: str = "", *, expected_version: str = "",
                           pending: Path | None = None) -> tuple[list[str], dict[str, str]]:
    # PowerShell 5.1 expects UTF-16LE for EncodedCommand. Paths travel as data in the environment.
    command = b64encode(_RUN_UPDATER.encode("utf-16-le")).decode("ascii")
    return (["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-EncodedCommand", command],
            {**os.environ, "VOICECN_UPDATE_SCRIPT": str(SCRIPT),
             "VOICECN_UPDATE_INSTALLER": str(installer), "VOICECN_UPDATE_APP": app,
             "VOICECN_UPDATE_PARENT": str(parent_pid), "VOICECN_UPDATE_RESULT": str(pending or RESULT),
             "VOICECN_UPDATE_APP_ID": app_id, "VOICECN_UPDATE_VERSION": expected_version,
             "VOICECN_UPDATE_COMPLETED": str(RESULT),
             "VOICECN_UPDATE_LOCK": str(RESULT.with_suffix(".lock")) if pending is not None else ""})


def _write_update_step(path: Path, message: str, *, mode: str = "a") -> None:
    with path.open(mode, encoding="utf-8") as log:
        log.write(f"{datetime.now().astimezone().isoformat()} {message}\n")


def start_silent_installer(path: Path, release: Release) -> None:
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        raise UpdateError("自动安装只适用于 Windows 安装版 EXE。")
    if path.name != release.installer_name or not _verified_file(path, release):
        raise UpdateError("安装包在启动前校验失败。")
    path = path.resolve()
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    marker = RESULT.with_suffix(".lock")
    pending = RESULT.with_suffix(".pending.txt")
    try:
        with _state_lock():
            if _updater_running(marker, pending):
                raise UpdateError("更新器正在运行，请等待更新完成。")
            marker.touch(exist_ok=False)
            _launch_updater(path, release, marker, pending)
    except Timeout as exc:
        raise UpdateError("更新器正在运行，请等待更新完成。") from exc


def _launch_updater(path: Path, release: Release, marker: Path, pending: Path) -> None:
    try:
        _write_update_step(pending, f"安装器路径：{path}", mode="w")
        _write_update_step(pending, "已下载并校验，准备启动更新器")
        if not SCRIPT.is_file():
            raise UpdateError("独立更新脚本缺失，请重新安装完整测试包。")
        edition = "cpu" if "_CPU_" in release.installer_name else "gpu"
        command, env = _powershell_invocation(
            path, sys.executable, os.getpid(), APP_IDS[edition],
            expected_version=str(release.version), pending=pending,
        )
        updater = subprocess.Popen(command, env=env, close_fds=True,
                                   creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    except (OSError, UpdateError) as exc:
        reason = f"无法启动独立更新程序：{exc}"
        _write_update_step(pending, f"更新失败：{reason}")
        pending.replace(RESULT)
        marker.unlink(missing_ok=True)
        raise UpdateError(reason) from exc

    try:
        created_at = psutil.Process(updater.pid).create_time()
        # Do not recreate the marker if the updater has already published its result.
        with marker.open("r+", encoding="ascii") as stream:
            stream.write(f"{updater.pid} {created_at}\n")
    except (FileNotFoundError, psutil.NoSuchProcess):
        pass
