"""Build and verify the COS model catalog, including the exact b4/b5 legacy ZIP."""
from __future__ import annotations

import argparse
import json
import shutil
import stat
import time
from http.client import HTTPException
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.request import urlopen
from zipfile import ZIP_STORED, ZipFile, ZipInfo

from scripts import publish_cos as cos
from valorant_translator.models.cos import MAX_BUNDLE_BYTES, MAX_EXTRACTED_BYTES, OPTIONAL, REQUIRED

REVISIONS = {
    "tiny": "d90ca5fe260221311c53c58e660288d3deb8d356",
    "base": "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66",
    "small": "536b0662742c02347bc0e980a01041f333bce120",
    "medium": "08e178d48790749d25932bbc082711ddcfdfbc4f",
}
M2M_REPO = "facebook/m2m100_418M"
M2M_REVISION = "55c2e61bbf05dfb8d7abccdc3fae6fc8512fd636"
FILES = ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt")
BUNDLE_NAME = "VoiceCNTranslator_Models_small_M2M100.zip"
CATALOG_NAME = "models.json"
BUNDLE_RECEIPT = "model-bundle.json"
COPY_BYTES = 1024**2

# The installed b4/b5 binaries pin the archive hash. Its member order, Unix
# attributes, timestamps and ZIP_STORED bytes must match the original Mac ZIP.
LEGACY_MEMBERS = {
    "whisper-small/config.json": (13, 20, 0),
    "whisper-small/model.bin": (13, 21, 0),
    "whisper-small/tokenizer.json": (13, 20, 0),
    "whisper-small/vocabulary.txt": (13, 20, 0),
    "m2m100_418M/config.json": (13, 21, 0),
    "m2m100_418M/generation_config.json": (13, 21, 0),
    "m2m100_418M/pytorch_model.bin": (13, 22, 30),
    "m2m100_418M/sentencepiece.bpe.model": (13, 21, 2),
    "m2m100_418M/special_tokens_map.json": (13, 21, 2),
    "m2m100_418M/tokenizer_config.json": (13, 21, 2),
    "m2m100_418M/vocab.json": (13, 21, 2),
}


def model_urls(filename: str) -> list[str]:
    return [f"https://{host}/models/{filename}" for host in cos.DOWNLOAD_HOSTS]


def download(repo: str, revision: str, name: str, target: Path) -> None:
    url = f"https://huggingface.co/{repo}/resolve/{revision}/{name}"
    for attempt in range(1, 4):
        try:
            with urlopen(url, timeout=90) as response, target.open("wb") as stream:
                shutil.copyfileobj(response, stream, COPY_BYTES)
                expected = response.headers.get("Content-Length")
            if not target.stat().st_size or (expected and target.stat().st_size != int(expected)):
                raise OSError("Incomplete upstream model file")
            return
        except (OSError, HTTPException) as exc:
            if attempt == 3 or isinstance(exc, HTTPError) and exc.code not in (408, 429, 500, 502, 503, 504):
                raise
            print(f"{repo}/{name}: download retry {attempt}/2", flush=True)
            time.sleep(2**attempt)


def write_zip(path: Path, source: Path, members: dict[str, tuple]) -> None:
    with ZipFile(path, "w", compression=ZIP_STORED) as archive:
        for name, timestamp in members.items():
            item = source / name
            info = ZipInfo(name, timestamp)
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.file_size = item.stat().st_size
            with item.open("rb") as stream, archive.open(info, "w") as out:
                shutil.copyfileobj(stream, out, COPY_BYTES)


def verify_zip(path: Path, members: set[str]) -> None:
    with ZipFile(path) as archive:
        entries = archive.infolist()
        if ({entry.filename for entry in entries} != members or len(entries) != len(members)
                or any(entry.file_size < 1 or stat.S_IFMT(entry.external_attr >> 16) != stat.S_IFREG
                       for entry in entries)
                or sum(entry.file_size for entry in entries) > MAX_EXTRACTED_BYTES
                or not 0 < path.stat().st_size <= MAX_BUNDLE_BYTES
                or archive.testzip() is not None):
            raise ValueError(f"Invalid model ZIP: {path.name}")


def file_entry(path: Path) -> dict:
    return dict(urls=model_urls(path.name), size=path.stat().st_size, sha256=cos.digest_file(path))


def expected_bundle() -> dict:
    pinned = cos._channel()["model_bundle"]
    if pinned["urls"] != model_urls(BUNDLE_NAME):
        raise ValueError("Legacy bundle URL does not match the pinned client configuration")
    return pinned


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def package(output: Path, local_dir: Path | None = None) -> tuple[dict, dict]:
    output.mkdir(parents=True, exist_ok=True)
    models = {}
    with TemporaryDirectory(prefix="cos-models-") as work:
        source = local_dir or Path(work)
        for size, revision in REVISIONS.items():
            name = f"whisper-{size}"
            repo = f"Systran/faster-{name}"
            archive = output / f"{name}.zip"
            members = {f"{name}/{file}": (1980, 1, 1, 0, 0, 0) for file in FILES}
            if local_dir is None:
                (source / name).mkdir()
                for file in FILES:
                    print(f"Download {repo}/{file}", flush=True)
                    download(repo, revision, file, source / name / file)
            write_zip(archive, source, members)
            verify_zip(archive, set(members))
            models[size] = {**file_entry(archive), "upstream": repo, "revision": revision}
            if local_dir is None and size != "small":
                shutil.rmtree(source / name)

        if local_dir is None:
            (source / "m2m100_418M").mkdir()
            for name in LEGACY_MEMBERS:
                folder, file = name.split("/")
                if folder == "m2m100_418M":
                    print(f"Download {M2M_REPO}/{file}", flush=True)
                    download(M2M_REPO, M2M_REVISION, file, source / name)
        archive = output / BUNDLE_NAME
        write_zip(archive, source, {name: (2026, 11, 18, *clock) for name, clock in LEGACY_MEMBERS.items()})
        members = {f"{folder}/{file}" for folder in REQUIRED for file in (*REQUIRED[folder], *OPTIONAL[folder])}
        verify_zip(archive, members)
        bundle = file_entry(archive)
        pinned = expected_bundle()
        if any(bundle[key] != pinned[key] for key in bundle):
            raise ValueError("Legacy ZIP differs from the exact bytes pinned by installed b4/b5 clients")
        bundle["crc64_ecma"] = pinned["crc64_ecma"]
        bundle["component_compressed_bytes"] = {}
        with ZipFile(archive) as zipped:
            for folder in REQUIRED:
                bundle["component_compressed_bytes"][folder] = sum(
                    item.compress_size for item in zipped.infolist() if item.filename.startswith(f"{folder}/")
                )

    catalog = {"schema": 1, "models": models}
    write_json(output / CATALOG_NAME, catalog)
    write_json(output / BUNDLE_RECEIPT, bundle)
    shutil.copyfile(cos.ROOT / "THIRD_PARTY_NOTICES_CN.md", output / "MODEL_LICENSES.md")
    for name, entry in [*models.items(), (BUNDLE_NAME, bundle)]:
        print(f"{name}: bytes={entry['size']}; sha256={entry['sha256']}", flush=True)
    return catalog, bundle


def load_verified_assets(output: Path) -> tuple[dict, dict]:
    catalog = json.loads((output / CATALOG_NAME).read_bytes())
    if catalog.get("schema") != 1 or set(catalog["models"]) != set(REVISIONS):
        raise ValueError("Invalid model catalog artifact")
    for size, revision in REVISIONS.items():
        name = f"whisper-{size}"
        path = output / f"{name}.zip"
        entry = {**file_entry(path), "upstream": f"Systran/faster-{name}", "revision": revision}
        if entry != catalog["models"][size]:
            raise ValueError(f"Model artifact failed verification: {name}")
    bundle = json.loads((output / BUNDLE_RECEIPT).read_bytes())
    if any(bundle[key] != value for key, value in expected_bundle().items()):
        raise ValueError("Legacy bundle receipt does not match installed clients")
    if any(bundle[key] != value for key, value in file_entry(output / BUNDLE_NAME).items()):
        raise ValueError("Legacy ZIP does not match its receipt")
    return catalog, bundle


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("dist/model-assets"))
    parser.add_argument("--local-dir", type=Path)
    args = parser.parse_args()
    package(args.output, args.local_dir)


if __name__ == "__main__":
    main()
