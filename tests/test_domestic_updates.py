from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import threading

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
    monkeypatch.setattr(windows.subprocess, "Popen", lambda *a, **k: calls.append((a, k)))
    windows.start_silent_installer(installer, release)
    assert calls and str(installer) in calls[0][0][0]
    monkeypatch.setattr(windows.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(OSError("blocked")))
    with pytest.raises(github.UpdateError, match="无法启动"):
        windows.start_silent_installer(installer, release)


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


@pytest.mark.skipif(sys.platform != "win32", reason="需要 Windows PowerShell")
def test_windows_updater_reports_installer_exit_failure(tmp_path):
    result = tmp_path / "result.txt"
    missing_app = tmp_path / "missing.exe"
    completed = subprocess.run([
        "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", str(windows.SCRIPT), "-Installer", sys.executable,
        "-AppExe", str(missing_app), "-ParentPid", "99999999",
        "-ResultPath", str(result),
    ], capture_output=True, text=True, timeout=30)
    assert completed.returncode != 0
    assert "更新失败" in result.read_text(encoding="utf-8-sig")
