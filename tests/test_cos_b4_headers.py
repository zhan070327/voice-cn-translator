from __future__ import annotations

import unittest
import tempfile
import inspect
import io
import time
from contextlib import redirect_stdout
from pathlib import Path
from urllib.error import HTTPError

from scripts.upload_b4_cos import (
    FIRST_PART_DEADLINE_SECONDS, Progress, diagnose_network, local_crc64, protected_client_class,
    require_same_object, upload_or_reuse,
)
from scripts.probe_b4_cos_part import ACCELERATE_ENDPOINT, probe
from unittest.mock import MagicMock, patch


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

    def test_phase_logs_and_multipart_initialization_guard(self):
        class FakeSdkClient:
            def create_multipart_upload(self, **kwargs):
                return kwargs

            def upload_part(self, *args, **kwargs):
                return {"ETag": "test"}

            def complete_multipart_upload(self, **kwargs):
                return kwargs

        client = protected_client_class(FakeSdkClient)()
        output = io.StringIO()
        with redirect_stdout(output):
            initiated = client.create_multipart_upload(ForbidOverwrite="false")
            client.upload_part("bucket", "key", b"12345", 1, "id")
            completed = client.complete_multipart_upload(ForbidOverwrite="false")
        self.assertEqual(initiated["ForbidOverwrite"], "true")
        self.assertEqual(completed["ForbidOverwrite"], "true")
        for phase in ("initialize multipart upload", "upload part 1", "complete multipart upload"):
            self.assertIn(f"{phase}: start", output.getvalue())
            self.assertIn(f"{phase}: success in", output.getvalue())

    def test_stalled_upload_exits_after_five_minutes_without_progress(self):
        progress = Progress(100)
        now = time.monotonic()
        with progress.lock:
            progress.last_activity = now - FIRST_PART_DEADLINE_SECONDS
        self.assertTrue(progress.stalled(now))
        with patch.object(progress.stop_event, "wait", return_value=False), \
             patch("scripts.upload_b4_cos.os._exit", side_effect=RuntimeError("watchdog exit")) as exit_process, \
             redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "watchdog exit"):
                progress._heartbeat()
        exit_process.assert_called_once_with(124)
        progress.transferred_callback(5)
        self.assertFalse(progress.stalled(time.monotonic()))

    def test_read_only_network_probe_accepts_missing_object(self):
        connection = MagicMock()
        tls = MagicMock()
        tls.version.return_value = "TLSv1.3"
        context = MagicMock()
        context.wrap_socket.return_value = tls
        with patch("scripts.upload_b4_cos.socket.getaddrinfo", return_value=[object()]), \
             patch("scripts.upload_b4_cos.socket.create_connection", return_value=connection), \
             patch("scripts.upload_b4_cos.ssl.create_default_context", return_value=context), \
             patch("scripts.upload_b4_cos.urlopen", side_effect=HTTPError("url", 404, "absent", {}, None)):
            output = io.StringIO()
            with redirect_stdout(output):
                diagnose_network()
        self.assertIn("DNS: success", output.getvalue())
        self.assertIn("TCP 443: success", output.getvalue())
        self.assertIn("TLS handshake: success", output.getvalue())
        self.assertIn("HTTPS HEAD: HTTP 404", output.getvalue())

    def test_controlled_part_probe_aborts_on_success_and_failure(self):
        class FakeClient:
            def __init__(self, fail=False):
                self.fail = fail
                self.aborted = False

            def create_multipart_upload(self, **kwargs):
                self.guard = kwargs["ForbidOverwrite"]
                return {"UploadId": "probe-id"}

            def upload_part(self, **kwargs):
                if self.fail:
                    raise TimeoutError("simulated part stall")
                return {"ETag": "example"}

            def abort_multipart_upload(self, **kwargs):
                self.aborted = True

            def list_multipart_uploads(self, **kwargs):
                return {"Upload": []}

        for fail in (False, True):
            with self.subTest(fail=fail), redirect_stdout(io.StringIO()):
                client = FakeClient(fail)
                control = FakeClient()
                if fail:
                    with self.assertRaises(TimeoutError):
                        probe(client, "releases/diagnostics/test.bin", control)
                else:
                    self.assertGreater(probe(client, "releases/diagnostics/test.bin", control), 0)
                self.assertEqual(client.guard, "true")
                self.assertTrue(control.aborted)

    def test_accelerate_endpoint_preserves_bucket_and_region(self):
        from qcloud_cos import CosConfig
        from scripts.upload_b4_cos import BUCKET

        regional = CosConfig(Region="ap-guangzhou", SecretId="id", SecretKey="key", Scheme="https")
        accelerated = CosConfig(Region="ap-guangzhou", SecretId="id", SecretKey="key",
                                Scheme="https", Endpoint=ACCELERATE_ENDPOINT)
        self.assertEqual(regional.uri(bucket=BUCKET, path="releases/probe.bin"),
                         f"https://{BUCKET}.cos.ap-guangzhou.myqcloud.com/releases/probe.bin")
        self.assertEqual(accelerated.uri(bucket=BUCKET, path="releases/probe.bin"),
                         f"https://{BUCKET}.cos.accelerate.myqcloud.com/releases/probe.bin")
        self.assertEqual(accelerated._region, regional._region)

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
        diagnostic = (
            Path(__file__).resolve().parents[1]
            / ".github/workflows/cos-b4-network-diagnostic.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", diagnostic)
        self.assertIn("--diagnose-network", diagnostic)
        self.assertNotIn("COS_SECRET", diagnostic)
        self.assertNotIn("upload-installer", diagnostic)
        probe_workflow = (
            Path(__file__).resolve().parents[1]
            / ".github/workflows/cos-b4-part-probe.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", probe_workflow)
        self.assertIn("- accelerate", probe_workflow)
        self.assertIn("COS_UPLOAD_ENDPOINT: ${{ inputs.endpoint }}", probe_workflow)
        self.assertNotIn("windows-domestic-", probe_workflow)
        self.assertNotIn("latest.json", probe_workflow)


if __name__ == "__main__":
    unittest.main()
