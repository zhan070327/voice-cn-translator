"""Seal a Windows domestic build, prepare latest.json, then publish to COS.

The COS SDK is a publisher-only dependency. Dry-run never loads credentials or
contacts COS. A build receipt prevents relabeling an old GitHub installer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

from packaging.version import Version

from scripts.generate_domestic_manifest import generate
from valorant_translator.updates.domestic import (MAX_MANIFEST_BYTES, load_channel,
                                                  parse_manifest, validate_url)
from valorant_translator.updates.github import INSTALLER_NAME, UpdateError
from valorant_translator.version import APP_VERSION

ROOT = Path(__file__).resolve().parents[1]
BUCKET = "valorant-translator-update-1535709511"
REGION = "ap-guangzhou"
ORIGIN = f"https://{BUCKET}.cos.{REGION}.myqcloud.com"
DOWNLOAD_HOSTS = (f"{BUCKET}.cos.{REGION}.myqcloud.com",
                  f"{BUCKET}.cos.accelerate.myqcloud.com")
MANIFEST_URL = f"{ORIGIN}/latest.json"
DOMESTIC_CONFIG = ROOT / "config" / "update_channel_domestic.json"
DEFAULT_INSTALLER = ROOT / "dist" / INSTALLER_NAME
DEFAULT_RECEIPT = ROOT / "dist" / "domestic_build.json"
DEFAULT_MANIFEST = ROOT / "dist" / "latest.json"
INSTALLER_CACHE = "public, max-age=31536000, immutable"
MANIFEST_CACHE = "public, max-age=60, must-revalidate"


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _channel(path: Path = DOMESTIC_CONFIG) -> dict:
    data = load_channel(path)
    if (data["channel"] != "domestic" or data["manifest_url"] != MANIFEST_URL or
            data["download_hosts"] != DOWNLOAD_HOSTS):
        raise UpdateError("国内版配置与指定 COS 存储桶不一致。")
    return data


def installer_key(version: str = APP_VERSION) -> str:
    return f"releases/{Version(version)}/{INSTALLER_NAME}"


def installer_url(version: str = APP_VERSION) -> str:
    return f"{ORIGIN}/{installer_key(version)}"


def seal_build(installer: Path = DEFAULT_INSTALLER, receipt: Path = DEFAULT_RECEIPT,
               bundle: Path | None = None) -> dict:
    """Run only after Inno Setup built the real domestic installer."""
    if installer.name != INSTALLER_NAME or not installer.is_file():
        raise UpdateError("找不到 Windows 安装包。")
    with installer.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise UpdateError("安装包不是 Windows EXE 文件。")
    bundle_root = bundle or installer.parent / "ValorantTranslator"
    packaged = next((path for path in (
        bundle_root / "_internal" / "config" / "update_channel.json",
        bundle_root / "config" / "update_channel.json") if path.is_file()), None)
    if packaged is None or load_channel(packaged) != _channel():
        raise UpdateError("打包后的 EXE 缺少正确的国内更新配置；拒绝生成发布凭据。")
    data = {"version": APP_VERSION, "channel": "domestic", "sha256": digest_file(installer),
            "size": installer.stat().st_size, "manifest_url": MANIFEST_URL}
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return data


def prepare(installer: Path, receipt: Path, notes_file: Path,
            output: Path = DEFAULT_MANIFEST) -> dict:
    if installer.name != INSTALLER_NAME or not installer.is_file():
        raise UpdateError(f"找不到真实安装包 {INSTALLER_NAME}。")
    with installer.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise UpdateError("安装包不是 Windows EXE 文件。")
    try:
        sealed = json.loads(receipt.read_text(encoding="utf-8"))
        notes = notes_file.read_text(encoding="utf-8").strip()
    except (OSError, ValueError) as exc:
        raise UpdateError("缺少国内版构建凭据或更新日志。") from exc
    if (not isinstance(sealed, dict) or sealed.get("version") != APP_VERSION or
            sealed.get("channel") != "domestic" or sealed.get("manifest_url") != MANIFEST_URL or
            sealed.get("size") != installer.stat().st_size or
            sealed.get("sha256") != digest_file(installer)):
        raise UpdateError("安装包与国内版构建凭据不一致；禁止发布旧安装包。")
    if not notes:
        raise UpdateError("更新日志不能为空。")
    if len(notes) > 800:
        raise UpdateError("更新日志最多 800 字，以便国内外显示一致。")
    if output.resolve() in {installer.resolve(), receipt.resolve()}:
        raise UpdateError("清单输出路径不能覆盖安装包或构建凭据。")
    configured = _channel()
    data = generate(installer, MANIFEST_URL, installer_url(), notes)
    fallback_hosts = configured.get("installer_fallback_hosts", [])
    data["fallback_download_urls"] = [f"https://{host}/{installer_key()}" for host in fallback_hosts]
    payload = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if parse_manifest(payload, configured["download_hosts"], "0") is None:
        raise UpdateError("生成的更新清单无效。")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".json.tmp")
    temporary.write_bytes(payload)
    temporary.replace(output)
    return data


def _read_remote_manifest(client) -> bytes | None:
    try:
        response = client.get_object(Bucket=BUCKET, Key="latest.json")
    except Exception as exc:
        if getattr(exc, "get_status_code", lambda: None)() == 404:
            return None
        raise
    stream = response["Body"].get_raw_stream()
    try:
        payload = stream.read(MAX_MANIFEST_BYTES + 1)
    finally:
        stream.close()
    if len(payload) > MAX_MANIFEST_BYTES:
        raise UpdateError("COS 上现有更新清单过大；已停止发布。")
    return payload


def _remote_version(client) -> Version | None:
    payload = _read_remote_manifest(client)
    if payload is None:
        return None
    release = parse_manifest(payload, _channel()["download_hosts"], "0")
    return release.version if release else None


def _head_existing(client, key: str) -> dict | None:
    try:
        return client.head_object(Bucket=BUCKET, Key=key)
    except Exception as exc:
        if getattr(exc, "get_status_code", lambda: None)() == 404:
            return None
        raise


def _probe_public_installer(url: str, size: int) -> None:
    validate_url(url, _channel()["download_hosts"])
    with urlopen(Request(url, method="HEAD"), timeout=20) as response:
        validate_url(response.geturl(), _channel()["download_hosts"])
        if int(response.headers.get("Content-Length", "-1")) != size:
            raise UpdateError("公开 HTTPS 安装包大小与本地不一致；未发布清单。")


def _probe_public_model(channel: dict) -> None:
    model = channel["model_bundle"]
    url = validate_url(model["urls"][0], channel["download_hosts"])
    with urlopen(Request(url, method="HEAD"), timeout=20) as response:
        validate_url(response.geturl(), channel["download_hosts"])
        if (response.headers.get("Content-Length") != str(model["size"]) or
                response.headers.get("x-cos-hash-crc64ecma") != model["crc64_ecma"]):
            raise UpdateError("COS 模型大小或 CRC64 与已验证的模型包不一致；未发布清单。")


def publish(client, installer: Path, manifest: dict, payload: bytes,
            public_probe=_probe_public_installer) -> None:
    """Mutating step. Never overwrite a versioned EXE or a same/newer release."""
    version = Version(manifest["version"])
    current = _remote_version(client)
    if current is not None and current >= version:
        raise UpdateError(f"COS 已发布 {current}，拒绝重复或降级发布。")
    key = installer_key(str(version))
    existing = _head_existing(client, key)
    if existing is not None and (int(existing.get("Content-Length", "-1")) != manifest["size"] or
                                 existing.get("x-cos-meta-sha256", "").lower() != manifest["sha256"]):
        raise UpdateError(f"COS 已存在不同的 {key}，拒绝覆盖版本安装包。")
    if existing is None:
        client.upload_file(Bucket=BUCKET, Key=key, LocalFilePath=str(installer),
                           PartSize=16, MAXThread=4, EnableMD5=True,
                           ContentType="application/octet-stream", CacheControl=INSTALLER_CACHE,
                           Metadata={"x-cos-meta-sha256": manifest["sha256"]})
    head = client.head_object(Bucket=BUCKET, Key=key)
    if (int(head.get("Content-Length", "-1")) != manifest["size"] or
            head.get("x-cos-meta-sha256", "").lower() != manifest["sha256"]):
        raise UpdateError("COS 安装包元数据校验失败；未发布 latest.json。")
    public_probe(manifest["download_url"], manifest["size"])
    current = _remote_version(client)
    if current is not None and current >= version:
        raise UpdateError("上传期间清单已被其他发布者更新；未覆盖 latest.json。")
    client.put_object(Bucket=BUCKET, Key="latest.json", Body=payload, EnableMD5=True,
                      ContentType="application/json; charset=utf-8", CacheControl=MANIFEST_CACHE)
    if _read_remote_manifest(client) != payload:
        raise UpdateError("latest.json 上传后读取校验失败，请立即检查 COS。")


def _make_client():
    secret_id, secret_key = os.environ.get("COS_SECRET_ID"), os.environ.get("COS_SECRET_KEY")
    if not secret_id or not secret_key:
        raise UpdateError("上传需要 COS_SECRET_ID 和 COS_SECRET_KEY 环境变量；dry-run 不需要。")
    from qcloud_cos import CosConfig, CosS3Client
    config = CosConfig(Region=REGION, SecretId=secret_id, SecretKey=secret_key,
                       Token=os.environ.get("COS_SESSION_TOKEN"), Scheme="https", Timeout=30)
    return CosS3Client(config)


def main() -> None:
    parser = argparse.ArgumentParser(description="腾讯云 COS 国内版更新发布")
    parser.add_argument("--installer", type=Path, default=DEFAULT_INSTALLER)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--notes-file", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--seal-build", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--upload", action="store_true")
    args = parser.parse_args()
    try:
        if args.seal_build:
            if args.upload or args.dry_run:
                parser.error("--seal-build 不能与 --upload/--dry-run 同时使用")
            seal_build(args.installer, args.receipt)
            print(f"国内版构建凭据已生成：{args.receipt}")
            return
        if args.notes_file is None:
            parser.error("发布或 dry-run 必须提供 --notes-file")
        data = prepare(args.installer, args.receipt, args.notes_file, args.output)
        if not args.upload:
            print(f"dry-run 通过：{data['version']}，安装包 {data['size']} 字节；清单：{args.output}；未联网或上传。")
            return
        client = _make_client()
        _probe_public_model(_channel())
        payload = args.output.read_bytes()
        publish(client, args.installer, data, payload)
        print(f"发布成功：{data['download_url']}，然后更新 {MANIFEST_URL}")
    except Exception as exc:
        raise SystemExit(f"发布失败：{exc}") from None


if __name__ == "__main__":
    main()
