from __future__ import annotations

import io

import pytest
from packaging.version import Version

from scripts import verify_domestic_release as dual
from scripts.publish_cos import installer_url
from valorant_translator.updates.github import Release, UpdateError
from valorant_translator.version import APP_VERSION


class HeadResponse(io.BytesIO):
    def __init__(self, size: int):
        super().__init__(b"")
        self.headers = {"Content-Length": str(size)}

    def geturl(self):
        return installer_url()


def test_github_gate_requires_matching_cos_release(monkeypatch, tmp_path):
    notes = tmp_path / "notes.txt"
    notes.write_text("同一份更新说明", encoding="utf-8")
    release = Release(Version(APP_VERSION), f"v{APP_VERSION}", "同一份更新说明",
                      installer_url(), "a" * 64, 123,
                      ("valorant-translator-update-1535709511.cos.ap-guangzhou.myqcloud.com",))
    monkeypatch.setattr(dual, "find_update", lambda *_args, **_kwargs: release)
    monkeypatch.setattr(dual, "urlopen", lambda *_args, **_kwargs: HeadResponse(123))
    dual.verify(notes)
    notes.write_text("另一份说明", encoding="utf-8")
    with pytest.raises(UpdateError, match="更新日志不一致"):
        dual.verify(notes)
    notes.write_text("同一份更新说明", encoding="utf-8")
    monkeypatch.setattr(dual, "urlopen", lambda *_args, **_kwargs: HeadResponse(1))
    with pytest.raises(UpdateError, match="大小"):
        dual.verify(notes)
    monkeypatch.setattr(dual, "find_update", lambda *_args, **_kwargs: None)
    with pytest.raises(UpdateError, match="未发布"):
        dual.verify(notes)
