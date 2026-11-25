"""Upload immutable release objects with bounded retries and COS integrity checks."""
from __future__ import annotations

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import crcmod
from qcloud_cos.cos_exception import CosClientError
from requests.exceptions import ConnectionError, Timeout

from scripts import publish_cos as cos
from valorant_translator.updates.github import UpdateError

# Use the part size and concurrency that succeeded on the previous accelerated release.
PART_BYTES = 8 * 1024**2
WORKERS = 2
ATTEMPTS = 4
CRC64 = crcmod.mkCrcFun(0x142F0E1EBA9EA3693, initCrc=0, rev=True, xorOut=2**64-1)


def fingerprint(path: Path) -> tuple[int, str, str]:
    sha, crc, size = hashlib.sha256(), 0, 0
    with path.open("rb") as stream:
        for data in iter(lambda: stream.read(8 * 1024**2), b""):
            sha.update(data)
            crc = CRC64(data, crc)
            size += len(data)
    return size, sha.hexdigest(), str(crc)


def _retry(operation, label: str):
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return operation()
        except Exception as exc:
            status = getattr(exc, "get_status_code", lambda: getattr(exc, "code", None))()
            retryable = (status in (408, 429, 500, 502, 503, 504)
                         or isinstance(exc, (CosClientError, ConnectionError, Timeout, TimeoutError))
                         or (isinstance(exc, URLError) and not isinstance(exc, HTTPError)))
            if not retryable or attempt == ATTEMPTS:
                raise
            reason = status or type(exc).__name__
            # SDK messages may include request details, so log only a safe timeout hint.
            if isinstance(exc, CosClientError):
                message = str(exc).lower()
                for hint in ("read timed out", "connect timed out", "write timed out"):
                    if hint in message:
                        reason = f"{reason}: {hint}"
                        break
            print(f"{label}: retry {attempt}/{ATTEMPTS - 1} ({reason})", flush=True)
            time.sleep(2**(attempt - 1))


def require_unversioned(client) -> None:
    info = _retry(lambda: client.get_bucket_versioning(Bucket=cos.BUCKET), "Bucket versioning")
    if info.get("Status") not in (None, ""):
        raise UpdateError("COS forbid-overwrite requires bucket versioning never enabled.")


def _verify_head(headers, size: int, sha: str, crc: str, label: str) -> None:
    headers = {k.lower(): v for k, v in headers.items()}
    if (headers.get("content-length") != str(size)
            or headers.get("x-cos-meta-sha256", "").lower() != sha
            or headers.get("x-cos-hash-crc64ecma") != crc):
        raise UpdateError(f"Size, SHA-256 metadata or COS CRC64 mismatch: {label}.")


def upload_immutable(client, path: Path, key: str, *, size: int, sha: str,
                     content_type: str, cache_control: str,
                     require_public_header=True, part_client=None) -> str:
    if part_client is None:
        part_client = client
    actual_size, actual_sha, crc = fingerprint(path)
    if not size or (actual_size, actual_sha) != (size, sha):
        raise UpdateError(f"Local artifact failed size/SHA-256 verification: {path.name}")

    def head():
        return _retry(lambda: cos._head_existing(client, key), f"HEAD {key}")

    def verify_final():
        result = head()
        if result is None:
            raise UpdateError(f"Completed COS object is missing: {key}")
        _verify_head(result, size, sha, crc, key)

    existing = head()
    if existing is not None:
        _verify_head(existing, size, sha, crc, key)
        outcome = "reused"
    else:
        upload_id = client.create_multipart_upload(
            Bucket=cos.BUCKET, Key=key, ContentType=content_type,
            CacheControl=cache_control, ContentDisposition=f'attachment; filename="{path.name}"',
            Metadata={"x-cos-meta-sha256": sha})["UploadId"]

        def upload_part(number: int, offset: int):
            with path.open("rb") as stream:
                stream.seek(offset)
                data = stream.read(min(PART_BYTES, size - offset))
            result = _retry(lambda: part_client.upload_part(
                Bucket=cos.BUCKET, Key=key, UploadId=upload_id,
                PartNumber=number, Body=data, ContentLength=str(len(data)), EnableMD5=True),
                f"{path.name} part {number}")
            headers = {k.lower(): v for k, v in result.items()}
            if headers.get("x-cos-hash-crc64ecma") != str(CRC64(data)):
                raise UpdateError(f"COS part CRC64 mismatch: {key}, part {number}")
            return {"PartNumber": str(number), "ETag": headers["etag"]}, len(data)

        start, transferred = time.monotonic(), 0
        try:
            with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                futures = [pool.submit(upload_part, number, offset)
                           for number, offset in enumerate(range(0, size, PART_BYTES), 1)]
                parts = []
                try:
                    for future in as_completed(futures):
                        part, count = future.result()
                        parts.append(part)
                        transferred += count
                        rate = transferred / max(time.monotonic() - start, 0.001) / 1024**2
                        print(f"{path.name}: {len(parts)}/{len(futures)} parts, {transferred}/{size} bytes ({rate:.1f} MiB/s)",
                              flush=True)
                except Exception:
                    for future in futures:
                        future.cancel()
                    raise
            parts.sort(key=lambda part: int(part["PartNumber"]))
            try:
                client.complete_multipart_upload(Bucket=cos.BUCKET, Key=key, UploadId=upload_id,
                                                 MultipartUpload={"Part": parts}, ForbidOverwrite="true")
            except (CosClientError, Timeout, ConnectionError):
                # A lost response can follow a successful commit. Reuse only exact bytes.
                verify_final()
            verify_final()
        except Exception:
            try:
                _retry(lambda: client.abort_multipart_upload(Bucket=cos.BUCKET, Key=key, UploadId=upload_id),
                       f"Abort {key}")
            except Exception as error:
                print(f"Could not abort unfinished upload for {key}: {type(error).__name__}", flush=True)
            raise
        outcome = "uploaded"

    def public_head():
        url = f"{cos.ORIGIN}/{key}"
        with urlopen(Request(url, method="HEAD"), timeout=20) as response:
            if response.status != 200 or response.geturl() != url:
                raise UpdateError(f"Public HTTPS verification failed: {key}")
            if require_public_header:
                _verify_head(response.headers, size, sha, crc, key)
            elif response.headers.get("Content-Length") != str(size):
                raise UpdateError(f"Public HTTPS size mismatch: {key}")
    _retry(public_head, f"Public HEAD {key}")
    print(f"{key}: {outcome}; bytes={size}; sha256={sha}; CRC64={crc}", flush=True)
    return outcome
