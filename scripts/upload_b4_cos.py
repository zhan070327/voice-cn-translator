"""One-time b4 installer upload. The caller validates the source SHA-256 first."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen

BUCKET = "valorant-translator-update-1535709511"
KEY = "releases/0.3.0b4/VoiceCNTranslator_Beta_Setup.exe"
URL = f"https://{BUCKET}.cos.ap-guangzhou.myqcloud.com/{KEY}"
INSTALLER = Path("dist/VoiceCNTranslator_Beta_Setup.exe")
PART_MIB = 16
THREADS = 6


def log(message: str) -> None:
    print(f"[COS b4] {message}", flush=True)


def header(headers: dict, name: str) -> str | None:
    return next((str(value) for key, value in headers.items() if key.lower() == name.lower()), None)


def require_same_object(head: dict, size: int, sha: str, crc: str) -> None:
    """Fail closed: metadata, size and COS's stored CRC64 must all match."""
    if (
        header(head, "Content-Length") != str(size)
        or header(head, "x-cos-meta-sha256") != sha
        or header(head, "x-cos-hash-crc64ecma") != crc
    ):
        raise RuntimeError("COS target exists but size/SHA-256 metadata/CRC64 differs; refusing overwrite")


def local_crc64(path: Path) -> str:
    import crcmod

    checksum = crcmod.Crc(
        0x142F0E1EBA9EA3693, initCrc=0, xorOut=0xFFFFFFFFFFFFFFFF, rev=True
    )
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(16 * 1024 * 1024), b""):
            checksum.update(block)
    return str(checksum.crcValue)


def protected_client_class(base):
    """The SDK's upload_file() omits ForbidOverwrite at multipart completion."""

    class ProtectedClient(base):
        def complete_multipart_upload(self, *args, **kwargs):
            kwargs["ForbidOverwrite"] = "true"
            return super().complete_multipart_upload(*args, **kwargs)

        def upload_part(self, Bucket, Key, Body, PartNumber, UploadId, EnableMD5=False, **kwargs):
            result = super().upload_part(
                Bucket, Key, Body, PartNumber, UploadId, EnableMD5, **kwargs
            )
            callback = getattr(self, "on_part_uploaded", None)
            if callback is not None:
                callback(len(Body))
            return result

    return ProtectedClient


class Progress:
    def __init__(self, total: int):
        self.total = total
        self.completed = 0  # Includes resumed parts, as reported by the SDK.
        self.transferred = 0  # Bytes actually sent in this attempt.
        self.started = time.monotonic()
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._heartbeat, daemon=True)

    def start(self) -> None:
        self.thread.start()
        self.report()

    def completed_callback(self, consumed: int, total: int) -> None:
        with self.lock:
            self.completed = consumed

    def transferred_callback(self, amount: int) -> None:
        with self.lock:
            self.transferred += amount

    def report(self) -> None:
        with self.lock:
            completed, transferred = self.completed, self.transferred
        elapsed = max(time.monotonic() - self.started, 0.001)
        speed = transferred / 1024**2 / elapsed
        log(
            f"upload heartbeat: completed {completed / 1024**2:.1f}/{self.total / 1024**2:.1f} MiB "
            f"(includes resumed parts); sent {transferred / 1024**2:.1f} MiB; "
            f"actual average {speed:.2f} MiB/s; elapsed {elapsed:.0f}s"
        )

    def _heartbeat(self) -> None:
        while not self.stop_event.wait(20):
            self.report()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=2)
        self.report()


def head_or_none(client):
    from qcloud_cos.cos_exception import CosServiceError

    try:
        return client.head_object(Bucket=BUCKET, Key=KEY)
    except CosServiceError as exc:
        if exc.get_status_code() == 404:
            return None
        raise


def verify_public(size: int) -> None:
    with urlopen(Request(URL, method="HEAD"), timeout=30) as response:
        if response.status != 200 or not response.url.startswith("https://"):
            raise RuntimeError("Public HTTPS address is unavailable")
        if response.headers.get("Content-Length") != str(size):
            raise RuntimeError("Public HTTPS file size differs")


def upload_or_reuse(client, size: int, sha: str, crc: str) -> tuple[str, float]:
    from qcloud_cos.cos_exception import CosClientError, CosServiceError

    started = time.monotonic()
    log("checking COS target with HEAD")
    existing = head_or_none(client)
    if existing is not None:
        require_same_object(existing, size, sha, crc)
        log("existing COS object matches size, SHA-256 metadata and CRC64; reusing")
        return "reused", time.monotonic() - started
    log(f"target absent; HEAD took {time.monotonic() - started:.1f}s")

    # Required for SDK resume; fail before transfer if the scoped CAM key lacks it.
    preflight = time.monotonic()
    client.list_multipart_uploads(Bucket=BUCKET, Prefix=KEY, MaxUploads=1)
    log(f"multipart resume permission checked in {time.monotonic() - preflight:.1f}s")
    if size <= PART_MIB * 1024**2:
        raise RuntimeError("Unexpected small installer; refusing to use single PUT")

    for attempt in range(1, 4):
        existing = head_or_none(client)
        if existing is not None:
            require_same_object(existing, size, sha, crc)
            return "reused", time.monotonic() - started
        progress = Progress(size)
        client.on_part_uploaded = progress.transferred_callback
        log(f"multipart attempt {attempt}/3: {PART_MIB} MiB parts, {THREADS} workers")
        progress.start()
        try:
            result = client.upload_file(
                Bucket=BUCKET, Key=KEY, LocalFilePath=str(INSTALLER),
                PartSize=PART_MIB, MAXThread=THREADS, EnableMD5=True,
                progress_callback=progress.completed_callback,
                ContentType="application/octet-stream",
                ContentDisposition='attachment; filename="VoiceCNTranslator_Beta_Setup.exe"',
                Metadata={"x-cos-meta-sha256": sha},
                ForbidOverwrite="true",  # Initiate Multipart Upload guard.
            )
            returned_crc = header(result, "x-cos-hash-crc64ecma")
            if returned_crc != crc:
                raise RuntimeError("COS completion CRC64 is missing or differs")
            log(f"multipart completion CRC64 verified in {time.monotonic() - started:.1f}s")
            return "uploaded", time.monotonic() - started
        except (CosClientError, CosServiceError) as exc:
            # A lost completion response may still have created the object.
            existing = head_or_none(client)
            if existing is not None:
                require_same_object(existing, size, sha, crc)
                log("object completed despite a lost response; reusing")
                return "reused", time.monotonic() - started
            status = exc.get_status_code() if isinstance(exc, CosServiceError) else None
            log(f"attempt {attempt} failed ({type(exc).__name__}, HTTP {status}); no completed object")
            if attempt == 3 or status in (400, 401, 403, 404, 409, 412):
                raise
            time.sleep(2**attempt)
        finally:
            client.on_part_uploaded = None
            progress.stop()
    raise RuntimeError("Multipart upload exhausted retries")


def main() -> None:
    from qcloud_cos import CosConfig, CosS3Client

    started = time.monotonic()
    size = int(os.environ["EXPECTED_SIZE"])
    sha = os.environ["EXPECTED_SHA256"]
    if not INSTALLER.is_file() or INSTALLER.stat().st_size != size or len(sha) != 64:
        raise RuntimeError("Validated installer is missing or changed")
    secret_id = os.environ.get("COS_SECRET_ID", "")
    secret_key = os.environ.get("COS_SECRET_KEY", "")
    if not secret_id or not secret_key:
        raise RuntimeError("COS Environment Secrets are missing")

    log("local CRC64 calculation started; source SHA-256 was validated earlier")
    crc = local_crc64(INSTALLER)
    log(f"local CRC64 ready in {time.monotonic() - started:.1f}s")
    Client = protected_client_class(CosS3Client)
    client = Client(CosConfig(
        Region="ap-guangzhou", SecretId=secret_id, SecretKey=secret_key,
        Token=os.environ.get("COS_SESSION_TOKEN") or None,
        Scheme="https", Timeout=180,
    ))
    checked = time.monotonic()
    versioning = client.get_bucket_versioning(Bucket=BUCKET)
    if versioning.get("Status") not in (None, ""):
        raise RuntimeError("Bucket versioning prevents reliable server-side no-overwrite")
    log(f"bucket versioning checked in {time.monotonic() - checked:.1f}s")

    outcome, upload_seconds = upload_or_reuse(client, size, sha, crc)
    checked = time.monotonic()
    require_same_object(head_or_none(client) or {}, size, sha, crc)
    log(f"COS HEAD size/SHA-256 metadata/CRC64 verified in {time.monotonic() - checked:.1f}s")
    checked = time.monotonic()
    verify_public(size)
    log(f"public HTTPS HEAD verified in {time.monotonic() - checked:.1f}s")
    total = time.monotonic() - started
    log(f"{outcome}: {URL}; upload phase {upload_seconds:.1f}s; total {total:.1f}s")
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
        summary.write(
            f"b4 installer {outcome}; size: {size} bytes; SHA-256: `{sha}`; "
            f"CRC64: `{crc}`; upload phase: {upload_seconds:.1f}s; total: {total:.1f}s\n\n"
            f"Public HTTPS: {URL}\n\nNo model ZIP or latest.json was uploaded.\n"
        )


if __name__ == "__main__":
    main()
