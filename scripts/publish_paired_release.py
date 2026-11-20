"""Stage the exact same tested CPU/GPU EXEs on COS and GitHub, then publish both."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from packaging.version import Version

from scripts import cos_multipart as upload
from scripts import publish_cos as cos
from valorant_translator.updates.domestic import parse_manifest
from valorant_translator.updates.github import INSTALLERS, UpdateError, validate_repository
from valorant_translator.version import APP_VERSION


def prepare(gpu: Path, cpu: Path, output: Path, *, expected_commit=None, expected_run=None, expected_attempt=None) -> tuple[dict, str, str]:
    entries = {}
    notes = set()
    origins = set()
    for edition, folder in (("gpu", gpu), ("cpu", cpu)):
        exe = folder / INSTALLERS[edition]
        with exe.open("rb") as handle:
            if handle.read(2) != b"MZ":
                raise UpdateError(f"{edition}: not a Windows installer.")
        metadata = json.loads((folder / "windows_build.json").read_text(encoding="utf-8-sig"))
        size, digest = exe.stat().st_size, cos.digest_file(exe)
        must_match = {"version": APP_VERSION, "channel": "dual", "edition": edition,
                      "size": size, "sha256": digest, "install_self_test": "passed", "arch": "x64"}
        if any(metadata.get(k) != v for k, v in must_match.items()) or not 0 < size < 2**31:
            raise UpdateError(f"{edition}: build metadata or GitHub size limit failed.")
        origin = (metadata["commit"], str(metadata["run_id"]), str(metadata["run_attempt"]))
        for actual, required in zip(origin, (expected_commit, expected_run, expected_attempt)):
            if required is not None and actual != required:
                raise UpdateError(f"{edition}: artifact differs from the pinned build run.")
        origins.add(origin)
        release_notes = (folder / "release_notes.txt").read_text(encoding="utf-8-sig").strip()
        notes.add(release_notes)
        entries[edition] = {
            "version": APP_VERSION, "size": size, "sha256": digest, "release_notes": release_notes,
            "download_url": f"{cos.ORIGIN}/releases/{APP_VERSION}/{exe.name}",
        }
    if len(notes) != 1 or len(origins) != 1:
        raise UpdateError("CPU/GPU packages must come from the same successful build attempt and carry identical notes.")
    note = notes.pop()
    commit, _, _ = origins.pop()
    if not isinstance(commit, str) or len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
        raise UpdateError("Invalid source commit in build metadata.")
    if not note or len(note) > 800:
        raise UpdateError("Release notes must contain 1–800 characters.")
    if entries["gpu"]["size"] - entries["cpu"]["size"] <= 1_000_000_000:
        raise UpdateError("CPU installer must be more than 1 GB smaller than the GPU installer.")
    manifest = {**entries["gpu"], "editions": {"cpu": entries["cpu"]}}
    payload = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    for edition in INSTALLERS:
        if not parse_manifest(payload, cos.DOWNLOAD_HOSTS, "0", edition=edition):
            raise UpdateError(f"Invalid update entry: {edition}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(payload)
    return manifest, note, commit


def _gh(*args: str) -> str:
    return subprocess.check_output(["gh", *args], text=True)


def _find_github_release(repo: str, tag: str) -> dict | None:
    # Listing with write credentials also finds drafts left by an interrupted attempt.
    pages = json.loads(_gh("api", "--paginate", "--slurp", f"repos/{repo}/releases?per_page=100"))
    return next((release for page in pages for release in page if release["tag_name"] == tag), None)


def verify_github_assets(assets: list[dict], manifest: dict) -> None:
    expected = {INSTALLERS["gpu"]: manifest, INSTALLERS["cpu"]: manifest["editions"]["cpu"]}
    by_name = {asset["name"]: asset for asset in assets}
    for name, entry in expected.items():
        asset = by_name.get(name, {})
        if (asset.get("digest") != f"sha256:{entry['sha256']}" or asset.get("size") != entry["size"]
                or asset.get("state") != "uploaded"):
            raise UpdateError(f"GitHub asset was not verified: {name}.")


def _stage_installer(client, path: Path, entry: dict) -> None:
    key = f"releases/{APP_VERSION}/{path.name}"
    upload.upload_immutable(client, path, key, size=entry["size"], sha=entry["sha256"],
                            content_type="application/octet-stream", cache_control=cos.INSTALLER_CACHE)


def _stage_cos(gpu: Path, cpu: Path, manifest: dict, payload: bytes):
    client = cos._make_client()
    upload.require_unversioned(client)
    previous = cos._read_remote_manifest(client)
    if previous:
        previous_version = Version(json.loads(previous)["version"])
        if previous_version > Version(APP_VERSION) or previous_version == Version(APP_VERSION) and previous != payload:
            raise UpdateError("COS already contains this version with different content or a newer version.")
    cos._probe_public_model(cos._channel())
    _stage_installer(client, gpu / INSTALLERS["gpu"], manifest)
    _stage_installer(client, cpu / INSTALLERS["cpu"], manifest["editions"]["cpu"])
    return client, previous


def publish(repo: str, gpu: Path, cpu: Path, manifest: dict, notes: str, commit: str, payload: bytes) -> None:
    repo = validate_repository(repo)
    tag = f"v{APP_VERSION}"
    client, previous = _stage_cos(gpu, cpu, manifest, payload)
    release = _find_github_release(repo, tag)
    if release is None:
        with TemporaryDirectory() as temporary:
            file = Path(temporary) / "notes.txt"
            file.write_text(notes, encoding="utf-8")
            _gh("release", "create", tag, "--repo", repo, "--draft", "--prerelease", "--target", commit,
                "--title", f"VoiceTrans {tag} · GPU / CPU", "--notes-file", str(file))
        release = _find_github_release(repo, tag)
    if release is None or release["target_commitish"] != commit or (release.get("body") or "").strip() != notes:
        raise UpdateError("Existing GitHub release does not match the built commit and release notes.")
    path = f"repos/{repo}/releases/{int(release['id'])}/assets"
    assets = json.loads(_gh("api", path))
    existing = {item["name"] for item in assets}
    for edition, folder in (("gpu", gpu), ("cpu", cpu)):
        if release["draft"] and INSTALLERS[edition] not in existing:
            _gh("release", "upload", tag, str(folder / INSTALLERS[edition]), "--repo", repo)
    verify_github_assets(json.loads(_gh("api", path)), manifest)
    if cos._read_remote_manifest(client) != previous:
        raise UpdateError("COS latest.json changed during staging. No manifest was written.")
    if release["draft"]:
        _gh("release", "edit", tag, "--repo", repo, "--draft=false", "--prerelease")
    _write_cos_manifest(client, previous, payload)


def _write_cos_manifest(client, previous: bytes | None, payload: bytes) -> None:
    if previous != payload:
        client.put_object(Bucket=cos.BUCKET, Key="latest.json", Body=payload, EnableMD5=True,
                          ContentType="application/json; charset=utf-8", CacheControl=cos.MANIFEST_CACHE)
    if cos._read_remote_manifest(client) != payload:
        raise UpdateError("COS final read-back failed. Check the production manifest before retrying.")



def publish_b5_cos_only(gpu: Path, cpu: Path, output: Path) -> None:
    if APP_VERSION != "0.3.0b5":
        raise UpdateError("This COS-only path is locked to 0.3.0b5.")
    manifest, _, _ = prepare(
        gpu, cpu, output,
        expected_commit="aac7062ce6cff3bb65aac7c5ad2ab1f83ee042d8",
        expected_run="44689887810", expected_attempt="1",
    )
    approved = (
        (manifest, 1_623_751_846, "a9a788002ab354611c01932041c2afcadfbab1999e8325724b70d25b7f273a8d"),
        (manifest["editions"]["cpu"], 216_024_614, "d40232ea5edc272d6a984a11d891d1abb66badaf7916189acd7c557d8a1e5122"),
    )
    if any((entry["size"], entry["sha256"]) != (size, sha) for entry, size, sha in approved):
        raise UpdateError("The installer SHA-256 or size differs from the approved b5 RC.")
    payload = output.read_bytes()
    client, previous = _stage_cos(gpu, cpu, manifest, payload)
    if cos._read_remote_manifest(client) != previous:
        raise UpdateError("COS latest.json changed during staging. No manifest was written.")
    _write_cos_manifest(client, previous, payload)
    print("Published both approved 0.3.0b5 installers and verified latest.json on COS.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gpu", type=Path, required=True)
    parser.add_argument("--cpu", type=Path, required=True)
    parser.add_argument("--repo", default="zhan070327/voice-cn-translator")
    parser.add_argument("--output", type=Path, default=Path("dist/paired-latest.json"))
    parser.add_argument("--confirm", help=f'Type "PUBLISH {APP_VERSION}" only after real Windows testing.')
    parser.add_argument("--cos-only-b5", action="store_true", help="Publish only the pinned b5 RC to COS.")
    parser.add_argument("--expected-commit")
    parser.add_argument("--expected-run")
    parser.add_argument("--expected-attempt")
    args = parser.parse_args()
    if args.cos_only_b5:
        if args.confirm != "PUBLISH 0.3.0b5":
            parser.error('COS-only publication requires "PUBLISH 0.3.0b5".')
        if any((args.expected_commit, args.expected_run, args.expected_attempt)):
            parser.error("The COS-only candidate pins cannot be overridden.")
        publish_b5_cos_only(args.gpu, args.cpu, args.output)
        return
    manifest, notes, commit = prepare(args.gpu, args.cpu, args.output,
                                      expected_commit=args.expected_commit, expected_run=args.expected_run,
                                      expected_attempt=args.expected_attempt)
    if args.confirm is None:
        print(f"Verified both installers. Local preview: {args.output}")
        for label, item in (("GPU", manifest), ("CPU", manifest["editions"]["cpu"])):
            print(f"{label} {item['version']}: {item['size']} bytes, SHA-256 {item['sha256']}")
        return
    if args.confirm != f"PUBLISH {APP_VERSION}":
        raise SystemExit("Publication confirmation mismatch.")
    publish(args.repo, args.gpu, args.cpu, manifest, notes, commit, args.output.read_bytes())
    print(f"Published verified GPU and CPU installers on both channels: {APP_VERSION}")


if __name__ == "__main__":
    main()
