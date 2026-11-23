"""Upload immutable model assets with the SDK's verified multipart resume."""
from __future__ import annotations

import time
from pathlib import Path
from urllib.request import Request

from qcloud_cos import CosConfig, CosS3Client
from qcloud_cos.cos_exception import CosClientError, CosServiceError
from requests.exceptions import ConnectionError, Timeout

from scripts import publish_cos as cos
from scripts.upload_b4_cos import local_crc64, protected_client_class, require_same_object
from valorant_translator.models.cos import _open

PART_MIB = 8
WORKERS = 2
ATTEMPTS = 3


def clients(secret_id: str, secret_key: str, session_token: str | None = None):
    common = dict(Region=cos.REGION, SecretId=secret_id, SecretKey=secret_key,
                  Token=session_token, Scheme="https")
    control = CosS3Client(CosConfig(**common, Timeout=(10, 60)), retry=1)
    protected = protected_client_class(CosS3Client, control)
    writer = protected(CosConfig(**common, Endpoint="cos.accelerate.myqcloud.com", Timeout=300), retry=1)
    return control, writer


def require_unversioned(client) -> None:
    if client.get_bucket_versioning(Bucket=cos.BUCKET).get("Status") not in (None, ""):
        raise ValueError("COS no-overwrite guard requires bucket versioning never enabled")


def public_head(url: str, size: int, sha: str, crc: str) -> None:
    with _open(Request(url, method="HEAD"), timeout=30) as response:
        if response.status != 200 or response.url != url:
            raise ValueError(f"Unexpected public model response: {url}")
        require_same_object(dict(response.headers), size, sha, crc)


def upload_immutable(control, writer, path: Path, entry: dict, *, license_file=False) -> None:
    key = f"models/{path.name}"
    size, sha, crc = entry["size"], entry["sha256"], local_crc64(path)
    if size != path.stat().st_size or not size or sha != cos.digest_file(path):
        raise ValueError(f"Local size/SHA-256 mismatch: {key}")
    if entry.get("crc64_ecma", crc) != crc:
        raise ValueError(f"Local CRC64 mismatch: {key}")
    for attempt in range(1, ATTEMPTS + 1):
        head = cos._head_existing(control, key)
        if head is not None:
            require_same_object(head, size, sha, crc)
            break
        last_percent = -10
        started = time.monotonic()

        def progress(done, total):
            nonlocal last_percent
            percent = int(done * 100 / total)
            if percent >= last_percent + 10 or done == total:
                last_percent = percent
                print(f"{key}: {done}/{total} bytes, {percent}%, includes resumed parts", flush=True)

        kwargs = dict(Bucket=cos.BUCKET, Key=key, EnableMD5=True,
                      ContentType="text/markdown; charset=utf-8" if license_file else "application/zip",
                      CacheControl=cos.MANIFEST_CACHE, Metadata={"x-cos-meta-sha256": sha},
                      ForbidOverwrite="true")
        print(f"{key}: upload attempt {attempt}/{ATTEMPTS}", flush=True)
        try:
            if license_file:
                writer.put_object(Body=path.read_bytes(), **kwargs)
            else:
                writer.upload_file(LocalFilePath=str(path), PartSize=PART_MIB, MAXThread=WORKERS,
                                   progress_callback=progress, **kwargs)
            print(f"{key}: transfer completed in {time.monotonic() - started:.1f}s", flush=True)
            break
        except (CosClientError, CosServiceError, Timeout, ConnectionError) as exc:
            head = cos._head_existing(control, key)
            if head is not None:
                require_same_object(head, size, sha, crc)
                break
            status = getattr(exc, "get_status_code", lambda: None)()
            if attempt == ATTEMPTS or status in (400, 401, 403, 404, 409, 412):
                raise
            print(f"{key}: retrying with multipart resume ({type(exc).__name__}, HTTP {status})", flush=True)
            time.sleep(2**attempt)
    require_same_object(cos._head_existing(control, key) or {}, size, sha, crc)
    for url in entry["urls"]:
        public_head(url, size, sha, crc)
    print(f"{key}: verified; bytes={size}; sha256={sha}; CRC64={crc}", flush=True)
