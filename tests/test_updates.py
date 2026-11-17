from __future__ import annotations

import hashlib
import io
import json

import pytest
from packaging.version import Version

from valorant_translator.updates import github


def release_json(tag: str, content: bytes, *, host: str = "github.com", digest: str | None = None):
    return {
        "tag_name": tag,
        "draft": False,
        "prerelease": True,
        "body": "修复了字幕。",
        "assets": [{
            "name": github.INSTALLER_NAME,
            "state": "uploaded",
            "size": len(content),
            "digest": digest or "sha256:" + hashlib.sha256(content).hexdigest(),
            "browser_download_url": (
                f"https://{host}/tester/voice-cn/releases/download/{tag}/{github.INSTALLER_NAME}"
            ),
        }],
    }


def test_prerelease_update_found_and_untrusted_assets_rejected(monkeypatch):
    content = b"installer bytes"
    data = [release_json("v0.3.0b2", content)]
    monkeypatch.setattr(github, "urlopen", lambda request, timeout: io.BytesIO(json.dumps(data).encode()))
    found = github.find_update("tester/voice-cn", "0.3.0b1")
    assert found is not None and found.version == Version("0.3.0b2")
    assert github.find_update("tester/voice-cn", "0.3.0b2") is None
    data.append(release_json("v0.3.0b3", content, host="example.com"))
    with pytest.raises(github.UpdateError, match="没有可验证"):
        github.find_update("tester/voice-cn", "0.3.0b1")
    data[-1] = release_json("v0.3.0b4", content, digest="sha256:invalid")
    with pytest.raises(github.UpdateError, match="没有可验证"):
        github.find_update("tester/voice-cn", "0.3.0b1")
    with pytest.raises(github.UpdateError):
        github.validate_repository("tester/../../other")


def test_update_download_checks_hash_and_size(monkeypatch, tmp_path):
    content = b"a verified Windows setup file"
    release = github._release_from_json(release_json("v0.3.0b2", content), "tester/voice-cn")
    assert release is not None
    monkeypatch.setattr(github, "urlopen", lambda request, timeout: io.BytesIO(content))
    progress = []
    path = github.download_installer(release, progress.append, tmp_path)
    assert path.read_bytes() == content and progress[-1] == 100
    monkeypatch.setattr(github, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError()))
    assert github.download_installer(release, progress.append, tmp_path) == path

    path.unlink()
    monkeypatch.setattr(github, "urlopen", lambda request, timeout: io.BytesIO(b"corrupt contents"))
    with pytest.raises(github.UpdateError, match="校验失败"):
        github.download_installer(release, progress.append, tmp_path)
    assert not path.exists() and not path.with_suffix(".exe.part").exists()


def test_no_repository_configured_and_installer_guard(tmp_path, monkeypatch):
    file = tmp_path / "update_channel.json"
    file.write_text('{"github_repository": ""}', encoding="utf-8")
    with pytest.raises(github.UpdateError, match="尚未配置"):
        github.load_repository(file)
    monkeypatch.setattr(github.sys, "platform", "darwin")
    with pytest.raises(github.UpdateError, match="Windows"):
        github.start_installer(tmp_path / github.INSTALLER_NAME, None)
