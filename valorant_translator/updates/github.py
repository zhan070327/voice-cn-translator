"""Check public GitHub Releases and verify an installer before running it.

Only the manual update action uses the network. Audio, transcripts, and local
history are never sent to GitHub. The publisher must build with a configured
repository and attach the exact installer filename to each release.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, replace
from http.client import HTTPException
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from packaging.version import InvalidVersion, Version

from valorant_translator.config import APP_DIR
from valorant_translator.version import APP_VERSION

CHANNEL_FILE = Path(__file__).resolve().parents[2] / "config" / "update_channel.json"
INSTALLER_NAME = "VoiceCNTranslator_Beta_Setup.exe"
CPU_INSTALLER_NAME = "VoiceCNTranslator_CPU_Beta_Setup.exe"
INSTALLERS = {"gpu": INSTALLER_NAME, "cpu": CPU_INSTALLER_NAME}
MAX_INSTALLER_BYTES = 4 * 1024**3
MAX_RESPONSE_BYTES = 2 * 1024**2
SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\Z")
DIGEST = re.compile(r"sha256:([0-9a-fA-F]{64})\Z")


class UpdateError(RuntimeError):
    """An update cannot be checked or installed safely."""


class UpdateCancelled(UpdateError):
    """The user cancelled an update download."""


@dataclass(frozen=True)
class Release:
    version: Version
    tag: str
    notes: str
    url: str
    sha256: str
    size: int
    allowed_hosts: tuple[str, ...] = ()
    fallback_urls: tuple[str, ...] = ()
    installer_name: str = INSTALLER_NAME
    source: str = ""


def validate_repository(repository: str) -> str:
    parts = repository.strip().split("/")
    if len(parts) != 2 or not all(SLUG.fullmatch(part) and part not in {".", ".."}
                                  for part in parts):
        raise UpdateError("GitHub 仓库地址无效，应为 用户名/仓库名。")
    return "/".join(parts)


def load_repository(path: Path = CHANNEL_FILE) -> str:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))["github_repository"]
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise UpdateError("更新通道配置缺失，请安装包含更新功能的正式测试包。") from exc
    if not value:
        raise UpdateError("尚未配置 GitHub 更新仓库。请让发布者在打包前设置仓库地址。")
    if not isinstance(value, str):
        raise UpdateError("更新仓库配置格式不正确。")
    return validate_repository(value)


def _request(url: str) -> Request:
    return Request(url, headers={"Accept": "application/vnd.github+json",
                                 "User-Agent": f"VoiceCNTranslator/{APP_VERSION}"})


def _release_from_json(data: dict, repository: str, installer_name: str = INSTALLER_NAME) -> Release | None:
    if data.get("draft"):
        return None
    tag = data.get("tag_name")
    if not isinstance(tag, str) or not re.fullmatch(r"v?[0-9][A-Za-z0-9.+_-]{0,60}", tag):
        return None
    try:
        release_version = Version(tag.removeprefix("v"))
    except InvalidVersion:
        return None
    for asset in data.get("assets", []):
        if not isinstance(asset, dict) or asset.get("name") != installer_name:
            continue
        if asset.get("state") != "uploaded":
            continue
        match = DIGEST.fullmatch(str(asset.get("digest", "")))
        size = asset.get("size")
        url = asset.get("browser_download_url")
        if not match or type(size) is not int or not (0 < size <= MAX_INSTALLER_BYTES):
            continue
        if not isinstance(url, str):
            continue
        parsed = urlparse(url)
        expected_path = f"/{repository}/releases/download/{tag}/{installer_name}"
        if (parsed.scheme != "https" or parsed.hostname != "github.com"
                or parsed.username or parsed.password or parsed.port
                or parsed.path.casefold() != expected_path.casefold()
                or parsed.query or parsed.fragment):
            continue
        return Release(release_version, tag, str(data.get("body") or "")[:800], url,
                       match.group(1).lower(), size, installer_name=installer_name)
    return None


def find_update(repository: str, current: str = APP_VERSION, *, edition: str = "gpu") -> Release | None:
    repository = validate_repository(repository)
    try:
        current_version = Version(current)
    except InvalidVersion as exc:
        raise UpdateError("当前软件版本号无效。") from exc
    url = f"https://api.github.com/repos/{repository}/releases?per_page=30"
    try:
        with urlopen(_request(url), timeout=15) as response:
            payload = response.read(MAX_RESPONSE_BYTES + 1)
    except Exception as exc:
        raise UpdateError(f"无法连接 GitHub 检查更新：{exc}") from exc
    if len(payload) > MAX_RESPONSE_BYTES:
        raise UpdateError("GitHub 更新信息过大。")
    try:
        releases = json.loads(payload)
    except ValueError as exc:
        raise UpdateError("GitHub 更新信息格式错误。") from exc
    if not isinstance(releases, list):
        raise UpdateError("未收到 GitHub Release 列表；请确认仓库已公开。")
    published: list[tuple[Version, Release | None]] = []
    for item in releases:
        if not isinstance(item, dict) or item.get("draft"):
            continue
        tag = item.get("tag_name")
        if not isinstance(tag, str):
            continue
        try:
            release_version = Version(tag.removeprefix("v"))
        except InvalidVersion:
            continue
        if release_version > current_version:
            published.append((release_version, _release_from_json(item, repository, INSTALLERS[edition])))
    if not published:
        return None
    latest_version, latest_release = max(published, key=lambda item: item[0])
    if latest_release is None:
        raise UpdateError(f"发现版本 {latest_version}，但没有可验证的 Windows 安装包；请联系发布者。")
    return latest_release


def _verified_file(path: Path, release: Release) -> bool:
    if not path.is_file() or path.stat().st_size != release.size:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return hmac.compare_digest(digest.hexdigest(), release.sha256)


def download_installer(release: Release, progress: Callable[[int], None],
                       directory: Path | None = None,
                       cancel: threading.Event | None = None,
                       speed: Callable[[float], None] | None = None) -> Path:
    urls = (release.url, *release.fallback_urls)
    for index, url in enumerate(urls):
        try:
            return _download_installer(replace(release, url=url), progress, directory, cancel, speed)
        except UpdateCancelled:
            raise
        except (OSError, HTTPException, UpdateError):
            if index == len(urls) - 1:
                raise


def _download_installer(release: Release, progress: Callable[[int], None],
                        directory: Path | None, cancel: threading.Event | None,
                        speed: Callable[[float], None] | None) -> Path:
    folder = directory or APP_DIR / "updates" / str(release.version)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / release.installer_name
    if _verified_file(path, release):
        progress(100)
        return path
    partial = path.with_suffix(".exe.part")
    digest = hashlib.sha256()
    received = 0
    started = last_speed = time.monotonic()
    try:
        if cancel and cancel.is_set():
            raise UpdateCancelled("下载已取消。")
        with urlopen(_request(release.url), timeout=30) as response, partial.open("wb") as output:
            if release.allowed_hosts:
                from valorant_translator.updates.domestic import validate_url
                validate_url(response.geturl(), release.allowed_hosts)
            while chunk := response.read(1024 * 1024):
                if cancel and cancel.is_set():
                    raise UpdateCancelled("下载已取消。")
                received += len(chunk)
                if received > release.size:
                    raise UpdateError("下载文件大于更新清单公布的大小。")
                output.write(chunk)
                digest.update(chunk)
                progress(min(99, int(received * 100 / release.size)))
                now = time.monotonic()
                if speed and now - last_speed >= 0.5:
                    speed(received / max(0.001, now - started))
                    last_speed = now
        if cancel and cancel.is_set():
            raise UpdateCancelled("下载已取消。")
        if received != release.size or not hmac.compare_digest(digest.hexdigest(), release.sha256):
            raise UpdateError("安装包大小或 SHA-256 校验失败，已拒绝运行。")
        partial.replace(path)
        progress(100)
        return path
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def start_installer(path: Path, release: Release) -> None:
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        raise UpdateError("自动安装只适用于 Windows 安装版 EXE。")
    if path.name != release.installer_name or not _verified_file(path, release):
        raise UpdateError("安装包在启动前校验失败，已拒绝运行。")
    os.startfile(str(path))
