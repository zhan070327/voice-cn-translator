from __future__ import annotations

import io
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from scripts import cos_multipart, package_whisper_models as assets, publish_cos as cos
from scripts import publish_cos_models as release


def manifest():
    return (b' {\n "version" : "0.3.0b5", "sha256": "' + b'a' * 64 + b'", "size": 123,'
            + f'"download_url": "{cos.ORIGIN}/releases/0.3.0b5/VoiceCNTranslator_Beta_Setup.exe",'.encode()
            + b'"release_notes":"b5", "editions":{ "cpu": { "version": "0.3.0b5", "size": 32, "sha256":"'
            + b'b' * 64 + f'", "download_url":"{cos.ORIGIN}/releases/0.3.0b5/'
                           'VoiceCNTranslator_CPU_Beta_Setup.exe"}}}\n'.encode())


def test_model_fields_preserve_every_original_manifest_byte():
    previous = manifest()
    bundle = {"urls": assets.model_urls(assets.BUNDLE_NAME), "size": 42, "sha256": "0" * 64}
    payload = release.add_model_fields(previous, bundle)
    assert payload[:2] + payload[payload.index(b',\n "version"') + 1:] == previous
    details = json.loads(payload)
    assert details.pop("model_manifest_url") == release.CATALOG_URL
    assert details.pop("model_bundle") == bundle
    assert details == json.loads(previous)
    assert release.add_model_fields(payload, bundle) == payload


def test_manifest_rejects_other_release_duplicate_or_different_bundle():
    with pytest.raises(ValueError, match="not b5"):
        release.add_model_fields(manifest().replace(b"0.3.0b5", b"0.3.0b6"), {})
    with pytest.raises(ValueError, match="Duplicate"):
        release.add_model_fields(manifest().replace(b'"size": 123', b'"size": 123,"size": 123'), {})
    with pytest.raises(ValueError, match="differs"):
        release.add_model_fields(release.add_model_fields(manifest(), {"size": 123}), {"size": 124})
    with pytest.raises(ValueError, match="missing"):
        release.add_model_fields(None, {})


def test_assets_include_all_models_and_reject_changed_zip(tmp_path, monkeypatch):
    source = tmp_path / "source"
    output = tmp_path / "output"
    names = {f"whisper-{size}/{file}" for size in assets.REVISIONS for file in assets.FILES}
    for name in names | set(assets.LEGACY_MEMBERS):
        path = source / name
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_bytes(name.encode())
    legacy = tmp_path / assets.BUNDLE_NAME
    assets.write_zip(legacy, source, {name: (2026, 11, 18, *clock) for name, clock in assets.LEGACY_MEMBERS.items()})
    monkeypatch.setattr(assets, "expected_bundle", lambda: {**assets.file_entry(legacy), "crc64_ecma": "123"})

    catalog, bundle = assets.package(output, source)
    assert set(catalog["models"]) == {"tiny", "base", "small", "medium"}
    assert assets.load_verified_assets(output) == (catalog, bundle)
    for model in catalog["models"]:
        with ZipFile(output / f"whisper-{model}.zip") as z:
            assert sorted(z.namelist()) == sorted(name for name in names if name.startswith(f"whisper-{model}/"))
    assert (output / assets.BUNDLE_NAME).read_bytes() == legacy.read_bytes()
    (output / "whisper-small.zip").write_bytes(b"bad")
    with pytest.raises(ValueError, match="whisper-small"):
        assets.load_verified_assets(output)


class MissingObject(Exception):
    def get_status_code(self):
        return 404


class ModelBucket:
    def __init__(self):
        self.objects = {"latest.json": manifest()}
        self.writes = []

    def get_bucket_versioning(self, **_kwargs):
        return {}

    def get_object(self, Key, **_kwargs):
        if Key not in self.objects:
            raise MissingObject
        stream = io.BytesIO(self.objects[Key])
        return {"Body": type("Response", (), {"get_raw_stream": lambda self: stream})()}

    def put_object(self, Key, Body, **_kwargs):
        self.objects[Key] = Body
        self.writes.append(Key)


def test_publish_all_assets_first_and_abort_if_installer_pin_changes(tmp_path, monkeypatch):
    client = ModelBucket()
    catalog = {"schema": 1, "models": {size: {} for size in assets.REVISIONS}}
    bundle = dict(size=42)
    assets.write_json(tmp_path / assets.CATALOG_NAME, catalog)
    (tmp_path / "MODEL_LICENSES.md").write_bytes((cos.ROOT / "THIRD_PARTY_NOTICES_CN.md").read_bytes())
    monkeypatch.setattr(release, "_public_json", lambda url: client.objects[release.CATALOG_KEY])

    def stage(control, writer, path, *_a, **_kw):
        control.writes.append(path.name)

    monkeypatch.setattr(cos_multipart, "upload_immutable", stage)
    release.publish(client, client, tmp_path, catalog, bundle)
    assert client.writes == ["MODEL_LICENSES.md", "whisper-tiny.zip", "whisper-base.zip",
                             "whisper-small.zip", "whisper-medium.zip", assets.BUNDLE_NAME,
                             release.CATALOG_KEY, "latest.json"]
    assert client.objects["latest.json"] == release.add_model_fields(manifest(), bundle)

    def change_pin(control, writer, path, *_a, **_kw):
        if path.name == assets.BUNDLE_NAME:
            control.objects["latest.json"] += b" "

    client = ModelBucket()
    monkeypatch.setattr(cos_multipart, "upload_immutable", change_pin)
    with pytest.raises(ValueError, match="changed"):
        release.publish(client, client, tmp_path, catalog, bundle)
    assert client.writes == []


def test_multipart_retries_resume_and_verify_both_public_hosts(tmp_path, monkeypatch):
    path = tmp_path / "model.zip"
    path.write_bytes(b"model fixture")
    entry = assets.file_entry(path)
    crc = cos_multipart.local_crc64(path)
    transferred, probes = [], []
    exact = {"Content-Length": str(entry["size"]), "x-cos-meta-sha256": entry["sha256"],
             "x-cos-hash-crc64ecma": crc}

    class Client:
        def head_object(self, **kwargs):
            if len(transferred) < 2:
                raise MissingObject
            return exact

        def upload_file(self, **kwargs):
            transferred.append(kwargs)
            if len(transferred) == 1:
                raise cos_multipart.CosClientError("connection closed")
            kwargs["progress_callback"](entry["size"], entry["size"])

    monkeypatch.setattr(cos_multipart.time, "sleep", lambda _: None)
    monkeypatch.setattr(cos_multipart, "public_head", lambda *values: probes.append(values))
    client = Client()
    cos_multipart.upload_immutable(client, client, path, entry)
    assert len(transferred) == 2
    assert all(call["PartSize"] == 8 and call["EnableMD5"] and call["ForbidOverwrite"] == "true"
               for call in transferred)
    assert probes == [(url, entry["size"], entry["sha256"], crc) for url in entry["urls"]]
