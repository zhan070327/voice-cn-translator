import hashlib
import io

import pytest
from requests.exceptions import Timeout

from scripts import cos_installer_multipart as cos
from valorant_translator.updates.github import UpdateError


class Missing(Exception):
    def get_status_code(self):
        return 404


class FakeCOS:
    def __init__(self):
        self.parts = {}
        self.objects = {}
        self.try_count = {}
        self.aborted = False
        self.corrupt_part = False

    def get_bucket_versioning(self, **_):
        return {}

    def head_object(self, *, Key, **_):
        if Key not in self.objects:
            raise Missing()
        return self.objects[Key]

    def create_multipart_upload(self, *, Metadata, **_):
        self.metadata = Metadata
        return {"UploadId": "upload1"}

    def upload_part(self, *, Key, PartNumber, Body, ContentLength, EnableMD5, **_):
        assert type(ContentLength) is str and ContentLength == str(len(Body))
        assert EnableMD5 is True
        self.try_count[PartNumber] = self.try_count.get(PartNumber, 0) + 1
        if PartNumber == 1 and self.try_count[1] == 1:
            raise Timeout("simulated network reset")
        self.parts[PartNumber] = Body
        crc = "wrong" if self.corrupt_part else str(cos.CRC64(Body))
        return {"ETag": str(PartNumber), "x-cos-hash-crc64ecma": crc}

    def complete_multipart_upload(self, *, Key, MultipartUpload, ForbidOverwrite, **_):
        assert ForbidOverwrite == "true"
        parts = [int(item["PartNumber"]) for item in MultipartUpload["Part"]]
        assert parts == sorted(self.parts)
        content = b"".join(self.parts[n] for n in parts)
        self.objects[Key] = {**self.metadata, "Content-Length": str(len(content)),
                             "x-cos-hash-crc64ecma": str(cos.CRC64(content))}

    def abort_multipart_upload(self, **_):
        self.aborted = True


@pytest.fixture
def sample(tmp_path, monkeypatch):
    path = tmp_path / "test.zip"
    data = b"123456789"
    path.write_bytes(data)
    monkeypatch.setattr(cos, "PART_BYTES", 4)
    monkeypatch.setattr(cos.time, "sleep", lambda _: None)
    return path, {"size": len(data), "sha": hashlib.sha256(data).hexdigest(),
                  "content_type": "application/zip", "cache_control": "public"}


def test_multipart_retries_checks_crc_and_reuses_completed_bytes(sample, monkeypatch):
    assert cos.CRC64(b"123456789") == 0x995DC9BBDF1939FA
    client = FakeCOS()
    path, info = sample
    key = "models/test.zip"

    class Head(io.BytesIO):
        status = 200

        def geturl(self):
            return f"{cos.cos.ORIGIN}/{key}"

        @property
        def headers(self):
            return client.objects[key]

    monkeypatch.setattr(cos, "urlopen", lambda req, **kw: Head())
    cos.require_unversioned(client)
    assert cos.upload_immutable(client, path, key, **info) == "uploaded"
    assert client.try_count[1] == 2
    assert cos.upload_immutable(client, path, key, **info) == "reused"
    client.objects[key]["x-cos-hash-crc64ecma"] = "different"
    with pytest.raises(UpdateError, match="CRC64"):
        cos.upload_immutable(client, path, key, **info)


def test_crc_failure_aborts_without_publishing_object(sample):
    client = FakeCOS()
    client.corrupt_part = True
    path, info = sample
    with pytest.raises(UpdateError, match="part CRC64"):
        cos.upload_immutable(client, path, "models/test.zip", **info)
    assert client.aborted and not client.objects
