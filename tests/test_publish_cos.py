from __future__ import annotations

import io
import json
import subprocess
import sys

import pytest

from scripts import publish_cos as pub
from valorant_translator.updates.github import INSTALLER_NAME, UpdateError


class MissingObject(Exception):
    def get_status_code(self):
        return 404


class FakeBody:
    def __init__(self, payload):
        self.payload = payload

    def get_raw_stream(self):
        return io.BytesIO(self.payload)


class FakeCos:
    def __init__(self):
        self.objects = {}
        self.calls = []
        self.fail_upload = False

    def get_object(self, *, Bucket, Key):
        self.calls.append(("get", Key))
        if Key not in self.objects:
            raise MissingObject()
        return {"Body": FakeBody(self.objects[Key]["body"])}

    def head_object(self, *, Bucket, Key):
        self.calls.append(("head", Key))
        if Key not in self.objects:
            raise MissingObject()
        return self.objects[Key]["headers"]

    def upload_file(self, *, Bucket, Key, LocalFilePath, **kwargs):
        self.calls.append(("upload", Key))
        if self.fail_upload:
            raise OSError("network failed")
        payload = open(LocalFilePath, "rb").read()
        self.objects[Key] = {"body": payload, "headers": {
            "Content-Length": str(len(payload)),
            "x-cos-meta-sha256": kwargs["Metadata"]["x-cos-meta-sha256"],
        }}

    def put_object(self, *, Bucket, Key, Body, **kwargs):
        self.calls.append(("put", Key))
        self.objects[Key] = {"body": Body, "headers": {
            "Content-Length": str(len(Body)), "Content-Type": kwargs["ContentType"],
            "Cache-Control": kwargs["CacheControl"],
        }}


def built_files(tmp_path):
    installer = tmp_path / INSTALLER_NAME
    installer.write_bytes(b"MZ real built installer")
    bundle_config = tmp_path / "ValorantTranslator" / "_internal" / "config" / "update_channel.json"
    bundle_config.parent.mkdir(parents=True)
    bundle_config.write_bytes(pub.DOMESTIC_CONFIG.read_bytes())
    receipt = tmp_path / "domestic_build.json"
    pub.seal_build(installer, receipt)
    notes = tmp_path / "notes.txt"
    notes.write_text("修复字幕延迟。", encoding="utf-8")
    output = tmp_path / "latest.json"
    data = pub.prepare(installer, receipt, notes, output)
    return installer, receipt, notes, output, data


def test_formal_cos_config_and_dry_run_without_credentials(tmp_path, monkeypatch):
    installer, receipt, notes, output, data = built_files(tmp_path)
    assert pub._channel()["manifest_url"] == pub.MANIFEST_URL
    assert data["download_url"] == pub.installer_url()
    assert data["sha256"] == pub.digest_file(installer)
    assert data["size"] == installer.stat().st_size
    assert json.loads(output.read_text(encoding="utf-8")) == data
    monkeypatch.delenv("COS_SECRET_ID", raising=False)
    monkeypatch.delenv("COS_SECRET_KEY", raising=False)
    completed = subprocess.run([
        sys.executable, "-m", "scripts.publish_cos", "--dry-run", "--installer", str(installer),
        "--receipt", str(receipt), "--notes-file", str(notes), "--output", str(output),
    ], capture_output=True, text=True, timeout=20)
    assert completed.returncode == 0 and "未联网或上传" in completed.stdout


def test_rejects_old_or_changed_installer(tmp_path):
    installer, receipt, notes, output, _ = built_files(tmp_path)
    installer.write_bytes(b"MZ different installer")
    with pytest.raises(UpdateError, match="不一致"):
        pub.prepare(installer, receipt, notes, output)
    with pytest.raises(UpdateError, match="缺少正确"):
        pub.seal_build(installer, receipt, tmp_path / "missing_bundle")


def test_public_model_probe_verifies_crc64(monkeypatch):
    channel = pub._channel()
    model = channel["model_bundle"]

    class Head(io.BytesIO):
        headers = {"Content-Length": str(model["size"]), "x-cos-hash-crc64ecma": model["crc64_ecma"]}

        def geturl(self):
            return model["urls"][0]

    def probe(request, **_kwargs):
        assert request.get_method() == "HEAD"
        return Head()

    monkeypatch.setattr(pub, "urlopen", probe)
    pub._probe_public_model(channel)
    Head.headers["x-cos-hash-crc64ecma"] = "0"
    with pytest.raises(UpdateError, match="CRC64"):
        pub._probe_public_model(channel)


def test_upload_installer_before_manifest_and_set_headers(tmp_path):
    installer, _, _, output, data = built_files(tmp_path)
    client = FakeCos()
    probes = []
    pub.publish(client, installer, data, output.read_bytes(), lambda url, size: probes.append((url, size)))
    keys = [event for event, _ in client.calls]
    assert keys.index("upload") < keys.index("put")
    assert probes == [(data["download_url"], data["size"])]
    assert client.objects["latest.json"]["headers"]["Content-Type"].startswith("application/json")
    assert "max-age=60" in client.objects["latest.json"]["headers"]["Cache-Control"]


def test_duplicate_version_and_existing_key_protection(tmp_path):
    installer, _, _, output, data = built_files(tmp_path)
    client = FakeCos()
    client.put_object(Bucket=pub.BUCKET, Key="latest.json", Body=output.read_bytes(),
                      ContentType="application/json", CacheControl="max-age=60")
    with pytest.raises(UpdateError, match="重复"):
        pub.publish(client, installer, data, output.read_bytes(), lambda *_: None)
    assert not any(event == "upload" for event, _ in client.calls)
    client.objects.pop("latest.json")
    client.objects[pub.installer_key()] = {"body": b"old", "headers": {"Content-Length": "3"}}
    with pytest.raises(UpdateError, match="拒绝覆盖"):
        pub.publish(client, installer, data, output.read_bytes(), lambda *_: None)


def test_network_or_remote_size_failure_keeps_old_manifest(tmp_path):
    installer, _, _, output, data = built_files(tmp_path)
    client = FakeCos()
    client.fail_upload = True
    with pytest.raises(OSError, match="network failed"):
        pub.publish(client, installer, data, output.read_bytes(), lambda *_: None)
    assert "latest.json" not in client.objects
    client.fail_upload = False
    def corrupt_head(*, Bucket, Key):
        if Key == pub.installer_key() and Key in client.objects:
            return {"Content-Length": "1", "x-cos-meta-sha256": data["sha256"]}
        return FakeCos.head_object(client, Bucket=Bucket, Key=Key)
    client.head_object = corrupt_head
    with pytest.raises(UpdateError, match="元数据校验失败"):
        pub.publish(client, installer, data, output.read_bytes(), lambda *_: None)
    assert "latest.json" not in client.objects


def test_retry_after_public_probe_failure_reuses_matching_installer(tmp_path):
    installer, _, _, output, data = built_files(tmp_path)
    client = FakeCos()
    with pytest.raises(OSError, match="public network failed"):
        pub.publish(client, installer, data, output.read_bytes(),
                    lambda *_: (_ for _ in ()).throw(OSError("public network failed")))
    assert "latest.json" not in client.objects
    pub.publish(client, installer, data, output.read_bytes(), lambda *_: None)
    assert sum(event == "upload" for event, _ in client.calls) == 1
    assert "latest.json" in client.objects
