from __future__ import annotations

import codecs
import hashlib
import io
import json
import os
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from scripts.configure_domestic_updates import configure
from scripts.generate_domestic_manifest import generate
from valorant_translator.updates import channel, domestic, github, windows
from valorant_translator.translation import glossary

MANIFEST_URL = "https://updates.example.cn/voice-cn/latest.json"
INSTALLER_URL = f"https://files.example.cn/voice-cn/{github.INSTALLER_NAME}"
HOSTS = ("files.example.cn",)


class Response(io.BytesIO):
    def __init__(self, data: bytes, url: str):
        super().__init__(data)
        self.url = url

    def geturl(self):
        return self.url


def manifest(content=b"installer", version="0.3.0b3", url=INSTALLER_URL):
    return {"version": version, "download_url": url,
            "sha256": hashlib.sha256(content).hexdigest(), "size": len(content),
            "release_notes": "修复了翻译。"}


def test_domestic_check_new_and_current(monkeypatch):
    config = configure(MANIFEST_URL, INSTALLER_URL)
    response = lambda *_args, **_kwargs: Response(json.dumps(manifest()).encode(), MANIFEST_URL)
    monkeypatch.setattr(domestic, "urlopen", response)
    found = domestic.find_update({**config, "download_hosts": HOSTS}, "0.3.0b2")
    assert str(found.version) == "0.3.0b3" and found.notes == "修复了翻译。"
    assert domestic.find_update({**config, "download_hosts": HOSTS}, "0.3.0b3") is None
    monkeypatch.setattr(channel.domestic, "find_update", lambda _config: found)
    assert channel.check_for_update({"channel": "domestic"}) == found
    monkeypatch.setattr(channel.github, "find_update", lambda repo: "github result")
    assert channel.check_for_update({"channel": "github", "github_repository": "a/b"}) == "github result"


@pytest.mark.parametrize("change", [
    {"download_url": "http://files.example.cn/a.exe"},
    {"download_url": "https://evil.example.cn/voice-cn/VoiceCNTranslator_Beta_Setup.exe"},
    {"download_url": INSTALLER_URL + "?token=secret"},
    {"fallback_download_urls": ["https://evil.example.cn/VoiceCNTranslator_Beta_Setup.exe"]},
    {"sha256": "bad"}, {"size": 0}, {"size": True},
    {"version": "bad version"}, {"release_notes": 5},
])
def test_rejects_invalid_manifest(change):
    data = {**manifest(), **change}
    with pytest.raises(github.UpdateError):
        domestic.parse_manifest(json.dumps(data).encode(), HOSTS, "0.3.0b2")


def test_rejects_untrusted_manifest_redirect_and_oversize(monkeypatch):
    config = configure(MANIFEST_URL, INSTALLER_URL)
    monkeypatch.setattr(domestic, "urlopen", lambda *_a, **_k: Response(b"{}", "https://evil.example.cn/latest.json"))
    with pytest.raises(github.UpdateError, match="受信任"):
        domestic.find_update({**config, "download_hosts": HOSTS})
    with pytest.raises(github.UpdateError, match="过大"):
        domestic.parse_manifest(b"x" * (domestic.MAX_MANIFEST_BYTES + 1), HOSTS)


def test_manifest_and_installer_network_timeouts(monkeypatch, tmp_path):
    config = configure(MANIFEST_URL, INSTALLER_URL)
    timeouts = []
    def timeout(_request, *, timeout):
        timeouts.append(timeout)
        raise TimeoutError("timed out")
    monkeypatch.setattr(domestic, "urlopen", timeout)
    with pytest.raises(github.UpdateError, match="无法获取"):
        domestic.find_update({**config, "download_hosts": HOSTS})
    release = domestic.parse_manifest(json.dumps(manifest()).encode(), HOSTS, "0.3.0b2")
    monkeypatch.setattr(github, "urlopen", timeout)
    with pytest.raises(TimeoutError):
        github.download_installer(release, lambda _: None, tmp_path)
    assert timeouts == [15, 30]
    assert not list(tmp_path.iterdir())


def test_download_failure_hash_cancel_and_retry(monkeypatch, tmp_path):
    content = b"valid setup bytes"
    release = domestic.parse_manifest(json.dumps(manifest(content)).encode(), HOSTS, "0.3.0b2")
    assert release
    monkeypatch.setattr(github, "urlopen", lambda *_a, **_k: (_ for _ in ()).throw(OSError("offline")))
    with pytest.raises(OSError, match="offline"):
        github.download_installer(release, lambda _: None, tmp_path)
    assert not list(tmp_path.iterdir())
    monkeypatch.setattr(github, "urlopen", lambda *_a, **_k: Response(b"corrupt contents", INSTALLER_URL))
    with pytest.raises(github.UpdateError, match="校验失败"):
        github.download_installer(release, lambda _: None, tmp_path)
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(github.UpdateCancelled):
        github.download_installer(release, lambda _: None, tmp_path, cancelled)
    monkeypatch.setattr(github, "urlopen", lambda *_a, **_k: Response(content, INSTALLER_URL))
    progress = []
    path = github.download_installer(release, progress.append, tmp_path)
    assert path.read_bytes() == content and progress[-1] == 100
    path.unlink()
    monkeypatch.setattr(github, "urlopen", lambda *_a, **_k: Response(content, "https://evil.example.cn/setup.exe"))
    with pytest.raises(github.UpdateError, match="受信任"):
        github.download_installer(release, lambda _: None, tmp_path)


def test_domestic_installer_fallback_still_checks_sha256(monkeypatch, tmp_path):
    content = b"b4 setup"
    backup = INSTALLER_URL.replace("files.example.cn", "backup.example.cn")
    data = {**manifest(content, "0.3.0b4"), "fallback_download_urls": [backup]}
    release = domestic.parse_manifest(json.dumps(data).encode(), (*HOSTS, "backup.example.cn"), "0.3.0b3")
    seen = []

    def request(req, **_kwargs):
        seen.append(req.full_url)
        answer = b"damaged" if req.full_url == INSTALLER_URL else content
        return Response(answer, req.full_url)

    monkeypatch.setattr(github, "urlopen", request)
    assert github.download_installer(release, lambda _: None, tmp_path).read_bytes() == content
    assert seen == [INSTALLER_URL, backup]


def test_b4_domestic_client_accepts_canonical_b5_manifest(monkeypatch, tmp_path):
    from scripts import publish_cos

    channel_config = domestic.load_channel(publish_cos.DOMESTIC_CONFIG)
    b5_url = publish_cos.installer_url("0.3.0b5")
    content = b"MZ update payload"
    payload = json.dumps(manifest(content, "0.3.0b5", b5_url)).encode()
    assert channel_config["manifest_url"] == publish_cos.MANIFEST_URL
    release = domestic.parse_manifest(payload, channel_config["download_hosts"], "0.3.0b4")
    assert str(release.version) == "0.3.0b5" and release.url == b5_url
    monkeypatch.setattr(github, "urlopen", lambda *_a, **_k: Response(content, b5_url))
    assert github.download_installer(release, lambda _: None, tmp_path).read_bytes() == content


def test_installer_launch_failure_and_success(monkeypatch, tmp_path):
    content = b"installer"
    installer = tmp_path / github.INSTALLER_NAME
    installer.write_bytes(content)
    release = domestic.parse_manifest(json.dumps(manifest(content)).encode(), HOSTS, "0.3.0b2")
    script = tmp_path / "windows_updater.ps1"
    script.write_text("# test")
    monkeypatch.setattr(windows, "SCRIPT", script)
    monkeypatch.setattr(windows, "RESULT", tmp_path / "last_result.txt")
    monkeypatch.setattr(windows.sys, "platform", "win32")
    monkeypatch.setattr(windows.sys, "frozen", True, raising=False)
    monkeypatch.setattr(windows.subprocess, "DETACHED_PROCESS", 8, raising=False)
    monkeypatch.setattr(windows.subprocess, "CREATE_NEW_PROCESS_GROUP", 512, raising=False)
    calls = []

    def spawn(*args, **kwargs):
        calls.append((args, kwargs))
        assert windows.update_in_progress()
        assert windows.read_update_result() is None
        return SimpleNamespace(pid=os.getpid())

    monkeypatch.setattr(windows.subprocess, "Popen", spawn)
    windows.start_silent_installer(installer, release)
    args, kwargs = calls[0]
    assert args[0][:1] == ["powershell.exe"] and args[0][-2] == "-EncodedCommand"
    assert kwargs["env"]["VOICECN_UPDATE_INSTALLER"] == str(installer)
    assert kwargs["env"]["VOICECN_UPDATE_SCRIPT"] == str(script)
    assert kwargs["env"]["VOICECN_UPDATE_VERSION"] == str(release.version)
    # The launched PowerShell wrapper releases the marker when it completes.
    (tmp_path / "last_result.lock").unlink()
    monkeypatch.setattr(windows.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(OSError("blocked")))
    with pytest.raises(github.UpdateError, match="无法启动"):
        windows.start_silent_installer(installer, release)
    finished = windows.read_update_result()
    assert finished is not None and not finished.succeeded
    assert finished.installer == installer
    assert "blocked" in finished.summary and not windows.update_in_progress()


def test_in_progress_update_keeps_previous_and_pending_logs(monkeypatch, tmp_path):
    result = tmp_path / "last_result.txt"
    pending = tmp_path / "last_result.pending.txt"
    marker = tmp_path / "last_result.lock"
    installer = tmp_path / github.INSTALLER_NAME
    monkeypatch.setattr(windows, "RESULT", result)
    legacy_report = f"启动安装器：{installer}\n更新成功；已重开新程序"
    result.write_text(legacy_report, encoding="utf-8")
    pending.write_text(f"安装器路径：{installer}\n准备启动更新器", encoding="utf-8")
    marker.write_text(f"{os.getpid()} {windows.psutil.Process().create_time()}", encoding="ascii")

    assert windows.read_update_result() is None
    assert result.read_text(encoding="utf-8") == legacy_report
    assert pending.is_file()

    failure_report = pending.read_text(encoding="utf-8") + "\n更新失败：安装器返回错误代码 5"
    pending.write_text(failure_report, encoding="utf-8")
    pending.replace(result)
    marker.unlink()
    finished = windows.read_update_result()
    assert finished is not None
    assert not finished.succeeded
    assert finished.installer == installer
    assert finished.message == failure_report
    assert finished.summary == "更新失败：安装器返回错误代码 5"
    assert result.read_text(encoding="utf-8") == failure_report
    assert windows.read_update_result() == finished


def test_abandoned_update_reports_failure_and_releases_marker(monkeypatch, tmp_path):
    result = tmp_path / "last_result.txt"
    pending = result.with_suffix(".pending.txt")
    marker = result.with_suffix(".lock")
    installer = tmp_path / github.INSTALLER_NAME
    monkeypatch.setattr(windows, "RESULT", result)
    # The process ID is live, but its creation time belongs to a different process.
    marker.write_text(f"{os.getpid()} 0", encoding="ascii")
    pending.write_text(f"安装器路径：{installer}\n更新器已启动：{installer}\n", encoding="utf-8")

    finished = windows.read_update_result()

    assert finished is not None and not finished.succeeded
    assert "更新器意外退出" in finished.message
    assert finished.installer == installer
    assert result.is_file() and not pending.exists() and not marker.exists()


def test_manual_installer_is_in_the_versioned_cache(monkeypatch, tmp_path):
    cache = tmp_path / "updates"
    monkeypatch.setattr(windows, "RESULT", cache / "last_result.txt")
    version = cache / "0.6.1"
    version.mkdir(parents=True)
    cached = version / github.CPU_INSTALLER_NAME
    cached.touch()
    outside = tmp_path / github.CPU_INSTALLER_NAME
    outside.touch()
    other_exe = version / "other.exe"
    other_exe.touch()

    assert windows.is_cached_installer(cached)
    assert not windows.is_cached_installer(outside)
    assert not windows.is_cached_installer(other_exe)
    assert not windows.is_cached_installer(version / github.INSTALLER_NAME)


def test_legacy_partial_update_log_is_not_a_final_result(monkeypatch, tmp_path):
    result = tmp_path / "last_result.txt"
    result.write_text("准备启动更新器\n", encoding="utf-8")
    monkeypatch.setattr(windows, "RESULT", result)

    assert windows.read_update_result() is None
    assert result.is_file()


def test_manifest_generation_from_actual_installer(tmp_path):
    installer = tmp_path / github.INSTALLER_NAME
    installer.write_bytes(b"built package")
    data = generate(installer, MANIFEST_URL, INSTALLER_URL, "新版")
    assert data["sha256"] == hashlib.sha256(b"built package").hexdigest()
    assert data["size"] == len(b"built package")


def test_custom_glossary_survives_builtin_upgrade(monkeypatch, tmp_path):
    custom = tmp_path / "glossary_custom.json"
    custom.write_text(json.dumps({"exact_phrases": {"go rush a": "一起快攻 A。"}}), encoding="utf-8")
    monkeypatch.setattr(glossary, "CUSTOM_GLOSSARY_FILE", custom)
    assert glossary.Glossary().exact("go rush a") == "一起快攻 A。"


def test_invalid_domestic_channel_config(tmp_path):
    config = tmp_path / "channel.json"
    config.write_text(json.dumps({"channel": "domestic", "manifest_url": "",
                                  "download_hosts": ["files.example.cn"]}), encoding="utf-8")
    with pytest.raises(github.UpdateError):
        domestic.load_channel(config)
    config.write_text(json.dumps(configure(MANIFEST_URL, INSTALLER_URL)), encoding="utf-8")
    loaded = domestic.load_channel(config)
    assert loaded["channel"] == "domestic" and loaded["download_hosts"] == HOSTS


def test_windows_updater_script_uses_utf8_bom():
    contents = windows.SCRIPT.read_bytes()
    assert contents.startswith(codecs.BOM_UTF8)
    assert "更新失败" in contents.decode("utf-8-sig")
    script = contents.decode("utf-8-sig")
    assert "安装器返回码" in script
    assert "更新成功；已重开新程序" in script
    assert "InstallLocation" in script
    assert windows._powershell_invocation(windows.SCRIPT, "old.exe", 123,
                                           windows.APP_IDS["cpu"])[1]["VOICECN_UPDATE_APP_ID"] == windows.APP_IDS["cpu"]


def _powershell_diagnostics(run: subprocess.CompletedProcess) -> str:
    return f"PowerShell returncode={run.returncode}\nstdout:\n{run.stdout}\nstderr:\n{run.stderr}"


@pytest.mark.skipif(sys.platform != "win32", reason="需要 Windows PowerShell")
def test_windows_updater_reports_installer_exit_failure(tmp_path):
    result = tmp_path / "result.txt"
    missing_app = tmp_path / "missing.exe"
    completed = subprocess.run([
        "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", str(windows.SCRIPT), "-Installer", sys.executable,
        "-AppExe", str(missing_app), "-ParentPid", "99999999",
        "-ResultPath", str(result),
    ], capture_output=True, text=True, errors="replace", timeout=30)
    diagnostics = _powershell_diagnostics(completed)
    assert completed.returncode == 1, diagnostics
    assert result.is_file(), diagnostics
    message = result.read_text(encoding="utf-8-sig")
    assert "更新失败" in message and "安装器返回错误代码" in message, f"{message}\n{diagnostics}"


@pytest.mark.skipif(sys.platform != "win32", reason="需要 Windows PowerShell")
def test_windows_updater_launcher_records_parse_failure(tmp_path, monkeypatch):
    result = tmp_path / "last_result.txt"
    pending = result.with_suffix(".pending.txt")
    marker = result.with_suffix(".lock")
    installer = tmp_path / github.INSTALLER_NAME
    broken = tmp_path / "invalid updater.ps1"
    broken.write_text("param(\n", encoding="ascii")
    result.write_text("更新失败：上次更新失败\n", encoding="utf-8")
    pending.write_text(f"安装器路径：{installer}\n准备启动更新器\n", encoding="utf-8")
    marker.touch()
    monkeypatch.setattr(windows, "SCRIPT", broken)
    monkeypatch.setattr(windows, "RESULT", result)
    command, env = windows._powershell_invocation(
        installer, sys.executable, 99999999, expected_version="0.6.1", pending=pending,
    )
    completed = subprocess.run(command, env=env, capture_output=True, text=True,
                               errors="replace", timeout=30)
    diagnostics = _powershell_diagnostics(completed)
    assert completed.returncode == 1, diagnostics
    assert result.is_file() and not pending.exists() and not marker.exists(), diagnostics
    finished = windows.read_update_result()
    assert finished is not None and not finished.succeeded
    assert finished.installer == installer and finished.summary.startswith("更新脚本无法运行："), diagnostics
    assert "准备启动更新器" in finished.message and "上次更新失败" not in finished.message


def _run_windows_updater(tmp_path, *, parent_pid=99999999, linger=False, version_matches=True):
    result = tmp_path / "result.txt"
    harness = tmp_path / "test_updater.ps1"
    harness.write_text(r"""
param([string]$Script, [string]$Result, [string]$Exe, [int]$ParentPid,
      [switch]$Linger, [switch]$WrongVersion)
[Console]::OutputEncoding = [Text.Encoding]::UTF8
if ($Linger) {
    $global:UpdaterClockCalls = 0
    function Get-Date {
        param([string]$Format)
        if ($Format) { return Microsoft.PowerShell.Utility\Get-Date -Format $Format }
        $global:UpdaterClockCalls++
        $clock = [datetime]'2026-01-01'
        if ($global:UpdaterClockCalls -gt 1) { return $clock.AddSeconds(46) }
        return $clock
    }
}
function Start-Process {
    param([string]$FilePath, [string]$ArgumentList, [switch]$Wait, [switch]$PassThru)
    if ($ArgumentList -and (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue)) {
        throw "Test assertion: parent process still running during installation."
    }
    if ($ArgumentList) { Write-Host "installed" }
    else { Write-Host "restarted $FilePath" }
    return [pscustomobject]@{ ExitCode = 0; Id = 123 }
}
$editionWithoutRegistry = '{E73471F2-EA17-4E9D-84A8-1CAF803A0DF2}'
$version = (Get-Item -LiteralPath $Exe).VersionInfo.ProductVersion
if ($WrongVersion) { $version = '0.0.0.0' }
& $Script -Installer $Exe -AppExe $Exe -ParentPid $ParentPid -ResultPath $Result `
    -AppId $editionWithoutRegistry -ExpectedVersion $version
exit $LASTEXITCODE
""", encoding="ascii")
    completed = subprocess.run([
        "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", str(harness), "-Script", str(windows.SCRIPT), "-Result", str(result),
        "-Exe", sys.executable, "-ParentPid", str(parent_pid),
        *(["-Linger"] if linger else []), *(["-WrongVersion"] if not version_matches else []),
    ], capture_output=True, encoding="utf-8", errors="replace", timeout=30)
    diagnostics = _powershell_diagnostics(completed)
    assert result.is_file(), diagnostics
    return completed, result.read_text(encoding="utf-8-sig"), diagnostics


@pytest.mark.skipif(sys.platform != "win32", reason="需要 Windows PowerShell")
def test_windows_updater_ends_lingering_parent_and_installs(tmp_path):
    with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"]) as parent:
        try:
            completed, message, diagnostics = _run_windows_updater(tmp_path, parent_pid=parent.pid, linger=True)
            assert completed.returncode == 0, diagnostics
            assert parent.poll() is not None, diagnostics
            assert "旧程序未在 45 秒内退出，已强制结束" in message
            assert "installed" in completed.stdout
        finally:
            if parent.poll() is None:
                parent.kill()


@pytest.mark.skipif(sys.platform != "win32", reason="需要 Windows PowerShell")
def test_windows_updater_restarts_verified_app_without_registry_entry(tmp_path):
    completed, message, diagnostics = _run_windows_updater(tmp_path)
    assert completed.returncode == 0, diagnostics
    assert f"restarted {sys.executable}" in completed.stdout
    assert "更新成功；已重开新程序" in message


@pytest.mark.skipif(sys.platform != "win32", reason="需要 Windows PowerShell")
def test_windows_updater_does_not_restart_mismatched_executable(tmp_path):
    completed, message, diagnostics = _run_windows_updater(tmp_path, version_matches=False)
    assert completed.returncode == 1, diagnostics
    assert "restarted" not in completed.stdout
    assert "更新失败" in message and "版本" in message
