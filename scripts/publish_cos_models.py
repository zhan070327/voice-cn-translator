"""Publish only model objects and the two model fields in the pinned b5 manifest."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from threading import Event
from tempfile import TemporaryDirectory
from urllib.parse import urlparse
from urllib.request import Request

from packaging.version import Version

from scripts import cos_multipart as upload
from scripts import publish_cos as cos
from scripts.package_whisper_models import (BUNDLE_NAME, BUNDLE_RECEIPT, CATALOG_NAME, REVISIONS,
                                            file_entry, load_verified_assets)
from valorant_translator.models import cos as model_client
from valorant_translator.updates.domestic import HASH, MAX_MANIFEST_BYTES, parse_manifest, validate_url
from valorant_translator.updates.github import MAX_INSTALLER_BYTES

CATALOG_KEY = f"models/{CATALOG_NAME}"
CATALOG_URL = f"{cos.ORIGIN}/{CATALOG_KEY}"
EXPECTED_RELEASE = Version("0.3.0b5")


def _unique(items):
    result = dict(items)
    if len(result) != len(items):
        raise ValueError("Duplicate JSON keys in COS manifest")
    return result


def add_model_fields(previous: bytes | None, bundle: dict) -> bytes:
    if previous is None:
        raise ValueError("The existing b5 latest.json is missing; refusing to create a new installer manifest")
    data = json.loads(previous, object_pairs_hook=_unique)
    release = parse_manifest(previous, cos.DOWNLOAD_HOSTS, "0")
    if release is None or release.version != EXPECTED_RELEASE:
        raise ValueError("The COS installer pin is not b5; stopping before writing")
    editions = data.get("editions")
    cpu = editions.get("cpu") if isinstance(editions, dict) else None
    if (not isinstance(cpu, dict) or cpu.get("version") != str(EXPECTED_RELEASE)
            or type(cpu.get("size")) is not int or not 0 < cpu["size"] <= MAX_INSTALLER_BYTES
            or not isinstance(cpu.get("sha256"), str) or not HASH.fullmatch(cpu["sha256"])
            or urlparse(validate_url(cpu.get("download_url"), cos.DOWNLOAD_HOSTS)).path.rsplit("/", 1)[-1]
            != "VoiceCNTranslator_CPU_Beta_Setup.exe"):
        raise ValueError("The pinned b5 CPU edition is missing or invalid")

    wanted = {"model_manifest_url": CATALOG_URL, "model_bundle": bundle}
    for key, value in wanted.items():
        if key in data and data[key] != value:
            raise ValueError(f"Existing {key} differs; refusing to overwrite it")
    missing = {key: value for key, value in wanted.items() if key not in data}
    if not missing:
        return previous

    # Keep the existing bytes, including all installer fields and formatting.
    offset = len(previous) - len(previous.lstrip()) + 1
    fields = json.dumps(missing, ensure_ascii=False, separators=(",", ":"))[1:-1].encode("utf-8")
    updated = previous[:offset] + b"\n  " + fields + b"," + previous[offset:]
    if len(updated) > MAX_MANIFEST_BYTES or json.loads(updated, object_pairs_hook=_unique) != {**data, **wanted}:
        raise ValueError("Model-only latest.json failed validation")
    return updated


def _remote_catalog(client) -> bytes | None:
    try:
        obj = client.get_object(Bucket=cos.BUCKET, Key=CATALOG_KEY)
    except Exception as exc:
        if getattr(exc, "get_status_code", lambda: None)() == 404:
            return None
        raise
    with obj["Body"].get_raw_stream() as stream:
        data = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(data) > MAX_MANIFEST_BYTES:
        raise ValueError("Existing COS model catalog is too large")
    return data


def _public_json(url: str) -> bytes:
    with model_client._open(Request(url, headers={"Cache-Control": "no-cache"}), timeout=30) as response:
        if response.status != 200 or response.url != url or response.headers.get_content_type() != "application/json":
            raise ValueError(f"Public JSON check failed: {url}")
        payload = response.read(MAX_MANIFEST_BYTES + 1)
    if len(payload) > MAX_MANIFEST_BYTES:
        raise ValueError(f"Public JSON exceeds the client limit: {url}")
    return payload


def publish(control, writer, output: Path, catalog: dict, bundle: dict) -> None:
    upload.require_unversioned(control)
    previous = cos._read_remote_manifest(control)
    updated = add_model_fields(previous, bundle)
    old_catalog = _remote_catalog(control)
    catalog_bytes = (output / CATALOG_NAME).read_bytes()
    if old_catalog is not None and json.loads(old_catalog, object_pairs_hook=_unique) != catalog:
        raise ValueError("Existing models.json differs from the staged catalog")

    license_path = output / "MODEL_LICENSES.md"
    if license_path.read_bytes() != (cos.ROOT / "THIRD_PARTY_NOTICES_CN.md").read_bytes():
        raise ValueError("Model license artifact differs from the release source")
    upload.upload_immutable(control, writer, license_path, file_entry(license_path), license_file=True)
    for size, entry in catalog["models"].items():
        upload.upload_immutable(control, writer, output / f"whisper-{size}.zip", entry)
    upload.upload_immutable(control, writer, output / BUNDLE_NAME, bundle)

    if _remote_catalog(control) != old_catalog or cos._read_remote_manifest(control) != previous:
        raise ValueError("COS manifests changed while staging model ZIPs; stopping before writing")
    if old_catalog is None:
        control.put_object(Bucket=cos.BUCKET, Key=CATALOG_KEY, Body=catalog_bytes, EnableMD5=True,
                           ContentType="application/json; charset=utf-8", CacheControl=cos.MANIFEST_CACHE,
                           ForbidOverwrite="true")
    if json.loads(_remote_catalog(control)) != catalog or json.loads(_public_json(CATALOG_URL)) != catalog:
        raise ValueError("COS models.json read-back failed; latest.json was left untouched")
    if cos._read_remote_manifest(control) != previous:
        raise ValueError("COS latest.json changed before publishing; refusing to overwrite it")
    if updated != previous:
        control.put_object(Bucket=cos.BUCKET, Key="latest.json", Body=updated, EnableMD5=True,
                           ContentType="application/json; charset=utf-8", CacheControl=cos.MANIFEST_CACHE)
    if cos._read_remote_manifest(control) != updated:
        raise ValueError("COS latest.json read-back failed")
    (output / "latest.published.json").write_bytes(updated)
    print(f"Published {CATALOG_URL}; original b5 manifest bytes retained", flush=True)


def smoke() -> None:
    raw = _public_json(cos.MANIFEST_URL)
    manifest = json.loads(raw, object_pairs_hook=_unique)
    catalog = json.loads(_public_json(CATALOG_URL), object_pairs_hook=_unique)
    if (manifest.get("model_manifest_url") != CATALOG_URL or catalog.get("schema") != 1
            or set(catalog.get("models", {})) != set(REVISIONS)):
        raise ValueError("Public model catalog is incomplete or not linked")
    channel = cos._channel()
    legacy = model_client.Bundle.from_channel(channel)
    if model_client.Bundle.from_channel({**channel, "model_bundle": manifest["model_bundle"]}) != legacy:
        raise ValueError("Public model bundle does not match the installed domestic channel")
    # Run the actual legacy domestic download/verification/extraction code with an empty model directory.
    last_percent = -10

    def progress(done, total, speed):
        nonlocal last_percent
        percent = int(done * 100 / total)
        if percent >= last_percent + 10 or done == total:
            last_percent = percent
            print(f"Clean download: {done}/{total} bytes ({percent}%), {speed / 1024**2:.1f} MiB/s", flush=True)

    with TemporaryDirectory(prefix="cos-model-smoke-") as directory:
        folder = Path(directory) / "models"
        model_client.install_models(legacy, folder, lambda message: print(message, flush=True), progress, Event())
        for name, files in model_client.REQUIRED.items():
            if not model_client.model_ready(folder / name, files):
                raise ValueError(f"Clean installation did not verify {name}")
    print("Clean download and installation of Small + M2M100 passed.", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("dist/model-assets"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--smoke", action="store_true")
    mode.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    if args.smoke:
        smoke()
        return
    if args.preflight:
        bundle = json.loads((args.output / BUNDLE_RECEIPT).read_bytes())
        add_model_fields(_public_json(cos.MANIFEST_URL), bundle)
        print("Current public latest.json has the original b5 GPU and CPU pins.", flush=True)
        return
    secret_id, secret_key = os.getenv("COS_SECRET_ID"), os.getenv("COS_SECRET_KEY")
    if not secret_id or not secret_key:
        parser.error("COS_SECRET_ID and COS_SECRET_KEY must be supplied by cos-production")
    catalog, bundle = load_verified_assets(args.output)
    control, writer = upload.clients(secret_id, secret_key, os.getenv("COS_SESSION_TOKEN") or None)
    publish(control, writer, args.output, catalog, bundle)


if __name__ == "__main__":
    main()
