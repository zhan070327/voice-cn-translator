"""Manual 1 MiB COS UploadPart probe; never completes an object."""

from __future__ import annotations

import os
import signal
import time
from uuid import uuid4

from scripts.upload_b4_cos import BUCKET, log, timed_call


def bounded(label, operation, *args, **kwargs):
    """Bound an entire SDK call, including any slow trickle or retry."""
    previous = signal.getsignal(signal.SIGALRM)
    def expired(signum, frame):
        raise TimeoutError(f"{label} exceeded 90 seconds")
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(90)
    try:
        return timed_call(label, operation, *args, **kwargs)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def probe(client, key: str) -> None:
    upload_id = None
    primary_error = None
    try:
        result = bounded("probe initialize multipart", client.create_multipart_upload,
                         Bucket=BUCKET, Key=key, ForbidOverwrite="true",
                         ContentType="application/octet-stream")
        upload_id = result["UploadId"]
        result = bounded("probe upload 1 MiB part", client.upload_part,
                         Bucket=BUCKET, Key=key, Body=b"x" * 1024**2,
                         PartNumber=1, UploadId=upload_id, EnableMD5=True)
        if not any(name.lower() == "etag" for name in result):
            raise RuntimeError("Probe UploadPart returned no ETag")
        log("probe part accepted; object will not be completed")
    except Exception as exc:
        primary_error = exc
    finally:
        if upload_id is not None:
            for attempt in range(1, 4):
                try:
                    bounded(f"probe abort multipart {attempt}/3", client.abort_multipart_upload,
                            Bucket=BUCKET, Key=key, UploadId=upload_id)
                    upload_id = None
                    break
                except Exception:
                    if attempt < 3:
                        time.sleep(attempt)
            if upload_id is not None:
                log(f"probe cleanup failed for key={key}, upload_id={upload_id}")
                raise RuntimeError("Probe cleanup failed; inspect the logged diagnostic upload") from primary_error
    listed = bounded("probe verify cleanup", client.list_multipart_uploads,
                     Bucket=BUCKET, Prefix=key, MaxUploads=10)
    if any(item.get("Key") == key for item in (listed.get("Upload") or [])):
        raise RuntimeError("Probe multipart upload remains after abort")
    log("probe cleanup verified; no completed object or multipart upload remains")
    if primary_error is not None:
        raise primary_error


def main() -> None:
    from qcloud_cos import CosConfig, CosS3Client

    secret_id = os.environ["COS_SECRET_ID"]
    secret_key = os.environ["COS_SECRET_KEY"]
    run_id = os.environ["GITHUB_RUN_ID"]
    key = f"releases/diagnostics/b4-part-probe-{run_id}-{uuid4().hex}.bin"
    client = CosS3Client(CosConfig(Region="ap-guangzhou", SecretId=secret_id,
                                   SecretKey=secret_key, Scheme="https", Timeout=(60, 60)),
                         retry=0)
    log("controlled 1 MiB multipart probe started on a unique diagnostic key")
    probe(client, key)


if __name__ == "__main__":
    main()
