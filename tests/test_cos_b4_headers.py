from __future__ import annotations

import unittest
import tempfile
import inspect
from pathlib import Path

from scripts.upload_b4_cos import (
    local_crc64, protected_client_class, require_same_object, upload_or_reuse,
)
from unittest.mock import patch


class TestB4CosUpload(unittest.TestCase):
    def test_completion_always_forbids_overwrite(self):
        class FakeSdkClient:
            def complete_multipart_upload(self, *args, **kwargs):
                return kwargs

        client = protected_client_class(FakeSdkClient)()
        result = client.complete_multipart_upload(
            Bucket="bucket", Key="key", UploadId="id", MultipartUpload={"Part": []},
            ForbidOverwrite="false",
        )
        self.assertEqual(result["ForbidOverwrite"], "true")

    def test_actual_part_bytes_are_counted(self):
        class FakeSdkClient:
            def upload_part(self, *args, **kwargs):
                return {"ETag": "test"}

        client = protected_client_class(FakeSdkClient)()
        seen = []
        client.on_part_uploaded = seen.append
        client.upload_part("bucket", "key", b"12345", 1, "id")
        self.assertEqual(seen, [5])

    def test_existing_object_must_match_all_three_checks(self):
        head = {
            "Content-Length": "5",
            "x-cos-meta-sha256": "a" * 64,
            "x-cos-hash-crc64ecma": "123",
        }
        require_same_object(head, 5, "a" * 64, "123")
        for changed in (
            {"Content-Length": "6"},
            {"x-cos-meta-sha256": "b" * 64},
            {"x-cos-hash-crc64ecma": "456"},
        ):
            with self.subTest(changed=changed):
                with self.assertRaisesRegex(RuntimeError, "refusing overwrite"):
                    require_same_object({**head, **changed}, 5, "a" * 64, "123")

    def test_existing_matching_object_skips_upload(self):
        head = {
            "Content-Length": "5", "x-cos-meta-sha256": "a" * 64,
            "x-cos-hash-crc64ecma": "123",
        }
        with patch("scripts.upload_b4_cos.head_or_none", return_value=head):
            outcome, _ = upload_or_reuse(object(), 5, "a" * 64, "123")
        self.assertEqual(outcome, "reused")

    def test_multipart_path_passes_guard_and_parallel_settings(self):
        class FakeClient:
            def list_multipart_uploads(self, **kwargs):
                return {}

            def upload_file(self, **kwargs):
                self.kwargs = kwargs
                return {"x-cos-hash-crc64ecma": "123"}

        client = FakeClient()
        with patch("scripts.upload_b4_cos.head_or_none", return_value=None):
            outcome, _ = upload_or_reuse(client, 17 * 1024**2, "a" * 64, "123")
        self.assertEqual(outcome, "uploaded")
        self.assertEqual(client.kwargs["ForbidOverwrite"], "true")
        self.assertEqual(client.kwargs["PartSize"], 16)
        self.assertEqual(client.kwargs["MAXThread"], 6)

    def test_crc64_matches_tencent_reference_vector(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "vector.txt"
            path.write_bytes(b"123456789")
            self.assertEqual(local_crc64(path), "11051210869376104954")

    def test_pinned_sdk_routes_multipart_completion_through_override(self):
        from qcloud_cos import CosS3Client
        from qcloud_cos.cos_comm import maplist

        self.assertEqual(maplist["ForbidOverwrite"], "x-cos-forbid-overwrite")
        source = inspect.getsource(CosS3Client.upload_file)
        self.assertIn("self.create_multipart_upload", source)
        self.assertIn("self._get_resumable_uploadid", source)
        self.assertIn("self.complete_multipart_upload", source)

    def test_workflow_remains_manual_and_uses_pinned_artifact(self):
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github/workflows/cos-b4-upload-once.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("\n  push:", workflow)
        self.assertIn("windows-domestic-44478207822-1", workflow)
        self.assertIn("python release-tools/scripts/upload_b4_cos.py", workflow)
        self.assertNotIn("scripts.publish_cos --upload", workflow)
        upload_code = (Path(__file__).resolve().parents[1] / "scripts/upload_b4_cos.py").read_text()
        self.assertNotIn("get_object(", upload_code)


if __name__ == "__main__":
    unittest.main()
