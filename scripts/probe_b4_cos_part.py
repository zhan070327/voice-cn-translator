"""Manual 1 MiB COS UploadPart probe; never completes an object."""

from __future__ import annotations

import os
import signal
import sys
import time
from uuid import uuid4

from scripts.upload_b4_cos import ACCELERATE_ENDPOINT, BUCKET, log, thread_timeout, timed_call


def bounded(label, seconds, operation, *args, **kwargs):
    """Bound an entire SDK call, including any slow trickle or retry."""
    if sys.platform == "win32" or not hasattr(signal, "SIGALRM"):
        return timed_call(label, thread_timeout, seconds, f"{label} exceeded {seconds} seconds",
                          operation, *args, **kwargs)
    previous = signal.getsignal(signal.SIGALRM)
    def expired(signum, frame):
        raise TimeoutError(f"{label} exceeded {seconds} seconds")
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(seconds)
    try:
        return timed_call(label, operation, *args, **kwargs)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def probe(client, key: str, control_client=None) -> float:
    control_client = control_client or client
    upload_id = None
    primary_error = None
    part_seconds = None
    try:
        result = bounded("probe initialize multipart", 30, client.create_multipart_upload,
                         Bucket=BUCKET, Key=key, ForbidOverwrite="true",
                         ContentType="application/octet-stream")
        upload_id = result["UploadId"]
        started = time.monotonic()
        result = bounded("probe upload 1 MiB part", 90, client.upload_part,
                         Bucket=BUCKET, Key=key, Body=b"x" * 1024**2,
                         PartNumber=1, UploadId=upload_id, EnableMD5=True)
        part_seconds = time.monotonic() - started
        if not any(name.lower() == "etag" for name in result):
            raise RuntimeError("Probe UploadPart returned no ETag")
        log(f"probe part accepted in {part_seconds:.1f}s; "
            f"speed {1 / max(part_seconds, .001):.3f} MiB/s; object will not be completed")
    except Exception as exc:
        primary_error = exc
    finally:
        if upload_id is not None:
            for attempt in range(1, 4):
                try:
                    bounded(f"probe abort multipart {attempt}/3", 20,
                            control_client.abort_multipart_upload,
                            Bucket=BUCKET, Key=key, UploadId=upload_id)
                    upload_id = None
                    break
                except Exception:
                    if attempt < 3:
                        time.sleep(attempt)
            if upload_id is not None:
                log(f"probe cleanup failed for key={key}, upload_id={upload_id}")
                raise RuntimeError("Probe cleanup failed; inspect the logged diagnostic upload") from primary_error
    listed = bounded("probe verify cleanup", 20, control_client.list_multipart_uploads,
                     Bucket=BUCKET, Prefix=key, MaxUploads=10)
    if any(item.get("Key") == key for item in (listed.get("Upload") or [])):
        raise RuntimeError("Probe multipart upload remains after abort")
    log("probe cleanup verified; no completed object or multipart upload remains")
    if primary_error is not None:
        raise primary_error
    return part_seconds


def main() -> None:
    from qcloud_cos import CosConfig, CosS3Client

    secret_id = os.environ["COS_SECRET_ID"]
    secret_key = os.environ["COS_SECRET_KEY"]
    run_id = os.environ["GITHUB_RUN_ID"]
    endpoint = os.environ.get("COS_UPLOAD_ENDPOINT", "direct")
    if endpoint not in {"direct", "accelerate"}:
        raise ValueError("COS_UPLOAD_ENDPOINT must be direct or accelerate")
    key = f"releases/diagnostics/b4-part-probe-{run_id}-{uuid4().hex}.bin"
    common = dict(Region="ap-guangzhou", SecretId=secret_id,
                  SecretKey=secret_key, Scheme="https")
    control_client = CosS3Client(CosConfig(**common, Timeout=(10, 20)), retry=0)
    upload_client = CosS3Client(CosConfig(
        **common, Endpoint=ACCELERATE_ENDPOINT if endpoint == "accelerate" else None,
        Timeout=(60, 60)), retry=0)
    checked = bounded("regional bucket versioning check", 20,
                      control_client.get_bucket_versioning, Bucket=BUCKET)
    if checked.get("Status") not in (None, ""):
        raise RuntimeError("Bucket versioning prevents reliable no-overwrite")
    log(f"controlled 1 MiB probe started: endpoint={endpoint}; "
        "configuration and cleanup use regional COS")
    probe(upload_client, key, control_client)


if __name__ == "__main__":
    main()
