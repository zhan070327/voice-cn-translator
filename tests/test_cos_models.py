import hashlib
import io
from threading import Event
from urllib.error import URLError
from zipfile import ZipFile

import pytest

from valorant_translator.models import cos
from valorant_translator.updates.domestic import load_channel


def bundle_zip(extra=None):
    output = io.BytesIO()
    with ZipFile(output, "w") as archive:
        for folder, files in cos.REQUIRED.items():
            for name in files:
                archive.writestr(f"{folder}/{name}", b"model")
        if extra:
            archive.writestr(extra, b"unsafe")
    content = output.getvalue()
    return cos.Bundle(("https://example.cn/model.zip",), len(content), hashlib.sha256(content).hexdigest()), content


class Reply(io.BytesIO):
    def __init__(self, body, total, offset=0, content_range=None):
        super().__init__(body)
        self.status = 206 if offset else 200
        self.headers = {"Content-Length": str(total - offset)}
        if offset:
            self.headers["Content-Range"] = content_range or f"bytes {offset}-{total - 1}/{total}"


def install(bundle, folder, pause=None, progress=lambda *_: None):
    cos.install_models(bundle, folder, lambda _: None, progress, pause or Event())


def test_domestic_bundle_is_pinned_to_cos():
    channel = load_channel(cos.NOTICE.parent / "config" / "update_channel_domestic.json")
    bundle = cos.Bundle.from_channel(channel)
    assert ".ap-guangzhou." in bundle.urls[0]
    assert ".accelerate." in bundle.urls[1]
    assert bundle.size > 2**31


def test_interrupted_model_download_resumes_and_keeps_existing_models(tmp_path, monkeypatch):
    bundle, content = bundle_zip()
    existing = tmp_path / "whisper-small"
    existing.mkdir()
    for name in cos.WHISPER_FILES:
        (existing / name).write_bytes(b"existing")
    progress = []
    offset = len(content) // 2
    monkeypatch.setattr(cos, "_open", lambda *_args, **_kw: Reply(content[:offset], len(content)))
    with pytest.raises(cos.DownloadError, match="中断"):
        install(bundle, tmp_path)
    assert next(tmp_path.glob("*.part")).stat().st_size == offset

    def resume(request, **_kwargs):
        assert request.get_header("Range") == f"bytes={offset}-"
        return Reply(content[offset:], len(content), offset)

    monkeypatch.setattr(cos, "_open", resume)
    install(bundle, tmp_path, progress=lambda *args: progress.append(args))
    assert progress[0][:2] == (offset, bundle.size)
    assert progress[-1][:2] == (bundle.size, bundle.size)
    assert (existing / "model.bin").read_bytes() == b"existing"
    assert (tmp_path / "m2m100_418M/pytorch_model.bin").read_bytes() == b"model"
    assert (tmp_path / "m2m100_418M/MODEL_LICENSES.md").is_file()
    assert not list(tmp_path.glob("*.part"))
    install(bundle, tmp_path)  # Would fail if a complete installation made another request.
    translation = tmp_path / "m2m100_418M"
    (translation / "pytorch_model.bin").write_bytes(b"x")
    assert not cos.model_ready(translation, cos.M2M_FILES)


def test_model_pause_retains_partial_file(tmp_path, monkeypatch):
    bundle, content = bundle_zip()
    pause = Event()
    monkeypatch.setattr(cos, "CHUNK", 64)

    class PausingReply(Reply):
        def read(self, *args):
            pause.set()
            return super().read(*args)

    monkeypatch.setattr(cos, "_open", lambda *_a, **_kw: PausingReply(content, len(content)))
    with pytest.raises(cos.ModelPaused):
        install(bundle, tmp_path, pause)
    assert next(tmp_path.glob("*.part")).stat().st_size == 64


def test_model_fallback_and_offline_error(tmp_path, monkeypatch):
    bundle, content = bundle_zip()
    bundle = cos.Bundle((*bundle.urls, "https://backup.cn/model.zip"), bundle.size, bundle.sha256)
    calls = []

    def offline(request, **_kw):
        calls.append(request.full_url)
        raise URLError("offline")

    monkeypatch.setattr(cos, "_open", offline)
    with pytest.raises(cos.DownloadError, match="offline"):
        install(bundle, tmp_path)
    assert tuple(calls) == bundle.urls
    assert not (tmp_path / "whisper-small").exists()
    monkeypatch.setattr(cos, "_open", lambda request, **kw: (
        offline(request, **kw) if request.full_url == bundle.urls[0] else Reply(content, len(content))))
    install(bundle, tmp_path)


def test_wrong_resume_range_is_rejected_without_losing_partial(tmp_path, monkeypatch):
    bundle, content = bundle_zip()
    partial = tmp_path / f".cos-{bundle.sha256}.zip.part"
    partial.write_bytes(content[:10])
    monkeypatch.setattr(cos, "_open", lambda *_a, **_kw: Reply(content[10:], len(content), 10, "bytes 0-10/20"))
    with pytest.raises(cos.DownloadError, match="续传范围"):
        install(bundle, tmp_path)
    assert partial.read_bytes() == content[:10]


def test_corrupt_model_is_discarded(tmp_path, monkeypatch):
    bundle, content = bundle_zip()
    corrupted = b"!" + content[1:]
    monkeypatch.setattr(cos, "_open", lambda *_a, **_kw: Reply(corrupted, len(content)))
    with pytest.raises(ValueError, match="SHA-256"):
        install(bundle, tmp_path)
    assert not list(tmp_path.glob("*.part"))
    assert not (tmp_path / "whisper-small").exists()


@pytest.mark.parametrize("unsafe", ["../outside", "whisper-small/../outside", "whisper-small\\model.bin"])
def test_model_extraction_rejects_unsafe_paths(tmp_path, monkeypatch, unsafe):
    bundle, content = bundle_zip(unsafe)
    monkeypatch.setattr(cos, "_open", lambda *_a, **_kw: Reply(content, len(content)))
    with pytest.raises(ValueError, match="异常文件"):
        install(bundle, tmp_path)
    assert not (tmp_path / "whisper-small").exists()
