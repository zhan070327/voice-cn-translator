"""Build tiny/base ZIPs from pinned public Systran repositories and optionally publish to COS."""
from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from scripts import cos_multipart as upload
from scripts import publish_cos as cos

REVISIONS = {"tiny": "d90ca5fe260221311c53c58e660288d3deb8d356",
             "base": "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66"}
FILES = ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt")


def package(output: Path, local_dir: Path | None = None) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    models = {}
    for size, revision in REVISIONS.items():
        name = f"whisper-{size}"
        repo = f"Systran/faster-{name}"
        with TemporaryDirectory() as cache:
            source = local_dir / name if local_dir else Path(cache)
            if local_dir is None:
                for file in FILES:
                    url = f"https://huggingface.co/{repo}/resolve/{revision}/{file}"
                    with urlopen(url, timeout=60) as response, (source / file).open("wb") as stream:
                        shutil.copyfileobj(response, stream)
            archive_path = output / f"{name}.zip"
            with ZipFile(archive_path, "w", compression=ZIP_DEFLATED, compresslevel=3) as archive:
                for file in FILES:
                    if not (source / file).stat().st_size:
                        raise ValueError(f"Empty model file: {name}/{file}")
                    info = ZipInfo(f"{name}/{file}")
                    info.compress_type = ZIP_DEFLATED
                    info.external_attr = 0o100644 << 16
                    with (source / file).open("rb") as stream, archive.open(info, "w") as dest:
                        shutil.copyfileobj(stream, dest)
            with ZipFile(archive_path) as archive:
                if archive.namelist() != [f"{name}/{f}" for f in FILES] or archive.testzip():
                    raise ValueError(f"Invalid model ZIP: {archive_path}")
            models[size] = {"urls": [f"{cos.ORIGIN}/models/{archive_path.name}"],
                            "size": archive_path.stat().st_size,
                            "sha256": cos.digest_file(archive_path),
                            "upstream": repo, "revision": revision}
    return {"schema": 1, "models": models}


def load_verified_catalog(output: Path) -> dict:
    result = json.loads((output / "models.preview.json").read_text(encoding="utf-8"))
    if result.get("schema") != 1 or set(result["models"]) != set(REVISIONS):
        raise ValueError("Model artifact catalog does not match the expected sizes.")
    for size, revision in REVISIONS.items():
        entry = result["models"][size]
        name = f"whisper-{size}"
        path = output / f"{name}.zip"
        if (entry["urls"] != [f"{cos.ORIGIN}/models/{path.name}"]
                or entry["revision"] != revision or entry["upstream"] != f"Systran/faster-{name}"
                or entry["size"] != path.stat().st_size or entry["sha256"] != cos.digest_file(path)):
            raise ValueError(f"Model artifact failed verification: {size}")
        with ZipFile(path) as archive:
            if set(archive.namelist()) != {f"{name}/{f}" for f in FILES} or len(archive.namelist()) != len(FILES):
                raise ValueError(f"Model package contains unexpected entries: {size}")
            if archive.testzip() is not None:
                raise ValueError(f"Model ZIP member integrity failed: {size}")
    return result


def _read_remote_catalog(client) -> bytes | None:
    try:
        obj = client.get_object(Bucket=cos.BUCKET, Key="models/models.json")
    except Exception as exc:
        if getattr(exc, "get_status_code", lambda: None)() == 404:
            return None
        raise
    stream = obj["Body"].get_raw_stream()
    try:
        data = stream.read(64 * 1024 + 1)
        if len(data) > 64 * 1024:
            raise ValueError("Existing COS model catalog is too large.")
        return data
    finally:
        stream.close()


def upload_models(client, output: Path, catalog: dict) -> None:
    upload.require_unversioned(client)
    previous = _read_remote_catalog(client)
    if previous is not None:
        existing = json.loads(previous)
        if existing.get("schema") != 1 or not isinstance(existing.get("models"), dict):
            raise ValueError("Existing COS model catalog is invalid.")
    else:
        existing = {"models": {}}
    for size, entry in catalog["models"].items():
        path = output / f"whisper-{size}.zip"
        upload.upload_immutable(client, path, f"models/{path.name}", size=entry["size"],
                                sha=entry["sha256"], content_type="application/zip",
                                cache_control=cos.MANIFEST_CACHE)
    if _read_remote_catalog(client) != previous:
        raise ValueError("Existing COS model catalog changed during staging.")
    merged = {**catalog, "models": {**existing["models"], **catalog["models"]}}
    payload = (json.dumps(merged, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(payload) > 64 * 1024:
        raise ValueError("Merged COS model catalog is too large.")
    if payload != previous:
        client.put_object(Bucket=cos.BUCKET, Key="models/models.json", Body=payload,
                          ContentType="application/json; charset=utf-8",
                          CacheControl=cos.MANIFEST_CACHE, EnableMD5=True)
    if _read_remote_catalog(client) != payload:
        raise ValueError("COS model catalog read-back failed.")
    (output / "models.preview.json").write_bytes(payload)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("dist/model-assets"))
    parser.add_argument("--local-dir", type=Path)
    parser.add_argument("--publish-if-configured", action="store_true")
    parser.add_argument("--publish-existing", action="store_true",
                        help="Publish only after verifying a previously prepared artifact directory.")
    args = parser.parse_args()
    if args.publish_existing:
        catalog = load_verified_catalog(args.output)
        upload_models(cos._make_client(), args.output, catalog)
        print("Verified COS model objects and published models/models.json.")
        return
    catalog = package(args.output, args.local_dir)
    (args.output / "models.preview.json").write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
    shutil.copyfile(cos.ROOT / "THIRD_PARTY_NOTICES_CN.md", args.output / "MODEL_LICENSES.md")
    if args.publish_if_configured and os.getenv("COS_SECRET_ID") and os.getenv("COS_SECRET_KEY"):
        upload_models(cos._make_client(), args.output, load_verified_catalog(args.output))
        print("Verified COS model objects and published models/models.json.")
    else:
        print("Model artifacts ready; no COS upload was performed.")


if __name__ == "__main__":
    main()
