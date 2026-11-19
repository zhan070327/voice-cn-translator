"""Build tiny/base ZIPs from pinned public Systran repositories and optionally publish to COS."""
from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import Request, urlopen
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

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


def upload_models(client, output: Path, catalog: dict) -> None:
    for size, data in catalog["models"].items():
        filename = f"whisper-{size}.zip"
        key = f"models/{filename}"
        expected = data["sha256"]
        head = cos._head_existing(client, key)
        if head is None:
            client.upload_file(Bucket=cos.BUCKET, Key=key, LocalFilePath=str(output / filename),
                               PartSize=8, MAXThread=3, EnableMD5=True,
                               ContentType="application/zip",
                               Metadata={"x-cos-meta-sha256": expected})
            head = client.head_object(Bucket=cos.BUCKET, Key=key)
        if int(head.get("Content-Length", 0)) != data["size"] or head.get("x-cos-meta-sha256") != expected:
            raise ValueError(f"COS object differs from the verified archive: {key}")
        with urlopen(Request(data["urls"][0], method="HEAD"), timeout=20) as response:
            if response.geturl() != data["urls"][0] or response.headers.get("Content-Length") != str(data["size"]):
                raise ValueError(f"Model unavailable for public HTTPS download: {key}")
    try:
        old = client.get_object(Bucket=cos.BUCKET, Key="models/models.json")
    except Exception as exc:
        if getattr(exc, "get_status_code", lambda: None)() != 404:
            raise
    else:
        stream = old["Body"].get_raw_stream()
        try:
            previous = json.loads(stream.read(64 * 1024 + 1))
            if previous.get("schema") != 1 or not isinstance(previous.get("models"), dict):
                raise ValueError("Existing COS model catalog is invalid.")
            catalog["models"] = {**previous["models"], **catalog["models"]}
        finally:
            stream.close()
    payload = (json.dumps(catalog, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    client.put_object(Bucket=cos.BUCKET, Key="models/models.json", Body=payload,
                      ContentType="application/json; charset=utf-8",
                      CacheControl=cos.MANIFEST_CACHE, EnableMD5=True)
    (output / "models.preview.json").write_bytes(payload)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("dist/model-assets"))
    parser.add_argument("--local-dir", type=Path)
    parser.add_argument("--publish-if-configured", action="store_true")
    args = parser.parse_args()
    catalog = package(args.output, args.local_dir)
    (args.output / "models.preview.json").write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
    shutil.copyfile(cos.ROOT / "THIRD_PARTY_NOTICES_CN.md", args.output / "MODEL_LICENSES.md")
    if args.publish_if_configured and os.getenv("COS_SECRET_ID") and os.getenv("COS_SECRET_KEY"):
        upload_models(cos._make_client(), args.output, catalog)
        print("Verified COS model objects and published models/models.json.")
    else:
        print("Model artifacts ready; no COS upload was performed.")


if __name__ == "__main__":
    main()
