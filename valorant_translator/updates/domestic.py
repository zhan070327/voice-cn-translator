"""HTTPS manifest for the domestic installer channel; no server credentials in clients."""
from __future__ import annotations

import ipaddress
import json
import re
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from packaging.version import InvalidVersion, Version

from valorant_translator.updates.github import (CHANNEL_FILE, INSTALLERS,
                                                MAX_INSTALLER_BYTES, Release,
                                                UpdateError)
from valorant_translator.version import APP_VERSION

MAX_MANIFEST_BYTES = 64 * 1024
HASH = re.compile(r"[0-9a-fA-F]{64}\Z")


def validate_url(url: str, hosts: tuple[str, ...]) -> str:
    if not isinstance(url, str) or len(url) > 2048 or not url.isascii():
        raise UpdateError("更新地址无效。")
    try:
        parsed = urlparse(url)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise UpdateError("更新地址格式错误。") from exc
    if (parsed.scheme != "https" or not host or host not in hosts or
            host in {"localhost"} or host.endswith((".local", ".internal")) or
            parsed.username or parsed.password or port or parsed.query or parsed.fragment or
            not parsed.path.startswith("/") or "\\" in url):
        raise UpdateError("更新地址不是受信任的 HTTPS 地址。")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise UpdateError("更新地址不能使用 IP 地址。")
    return url


def load_channel(path: Path = CHANNEL_FILE) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UpdateError("更新通道配置缺失或格式错误。") from exc
    if not isinstance(data, dict) or data.get("channel") not in ("github", "domestic", "dual"):
        raise UpdateError("更新通道必须是 github 或 domestic。")
    if data.get("edition", "gpu") not in ("gpu", "cpu"):
        raise UpdateError("安装包类型无效。")
    if data["channel"] in ("domestic", "dual"):
        url, hosts = data.get("manifest_url"), data.get("download_hosts")
        if not isinstance(hosts, list) or not hosts or not all(
                isinstance(host, str) and re.fullmatch(r"[a-z0-9.-]+", host)
                for host in hosts):
            raise UpdateError("国内下载域名白名单未配置。")
        allowed = tuple(hosts)
        try:
            manifest_host = urlparse(url).hostname if isinstance(url, str) else None
        except ValueError as exc:
            raise UpdateError("国内更新清单地址格式错误。") from exc
        validate_url(url, (manifest_host,) if manifest_host else ())
        data["download_hosts"] = allowed
    if data["channel"] in ("github", "dual"):
        from valorant_translator.updates.github import validate_repository
        data["github_repository"] = validate_repository(str(data.get("github_repository", "")))
    return data


def parse_manifest(payload: bytes, hosts: tuple[str, ...], current: str = APP_VERSION, *,
                   edition: str = "gpu") -> Release | None:
    if len(payload) > MAX_MANIFEST_BYTES:
        raise UpdateError("国内更新清单过大。")
    try:
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError("not an object")
        if edition == "cpu":
            editions = data.get("editions", {})
            if not isinstance(editions, dict):
                raise ValueError("安装包类型清单无效")
            if edition not in editions:
                return None
            version_text = data["version"]
            data = editions[edition]
            if not isinstance(data, dict) or data.get("version") != version_text:
                raise ValueError("安装包类型版本不一致")
        version = Version(data["version"])
        current_version = Version(current)
        url = validate_url(data["download_url"], hosts)
        alternatives = data.get("fallback_download_urls", [])
        if not isinstance(alternatives, list) or len(alternatives) > 2:
            raise ValueError("备用下载地址无效")
        fallback = tuple(dict.fromkeys(validate_url(item, hosts) for item in alternatives if item != url))
        size, digest, notes = data["size"], data["sha256"], data["release_notes"]
    except (ValueError, TypeError, KeyError, InvalidVersion, UpdateError) as exc:
        raise UpdateError(f"国内更新清单无效：{exc}") from exc
    if (type(size) is not int or not 0 < size <= MAX_INSTALLER_BYTES or
            not isinstance(digest, str) or not HASH.fullmatch(digest) or
            not isinstance(notes, str) or len(notes) > 4000 or
            any(urlparse(item).path.rsplit("/", 1)[-1] != INSTALLERS[edition]
                for item in (url, *fallback))):
        raise UpdateError("国内更新清单的安装包信息无效。")
    if version <= current_version:
        return None
    return Release(version, f"v{version}", notes[:800], url, digest.lower(), size, hosts, fallback, installer_name=INSTALLERS[edition])


def find_update(channel: dict, current: str = APP_VERSION) -> Release | None:
    url = channel["manifest_url"]
    try:
        with urlopen(Request(url, headers={"Accept": "application/json",
                                           "User-Agent": f"VoiceCNTranslator/{APP_VERSION}"}),
                     timeout=15) as response:
            validate_url(response.geturl(), (urlparse(url).hostname,))
            payload = response.read(MAX_MANIFEST_BYTES + 1)
    except UpdateError:
        raise
    except Exception as exc:
        raise UpdateError(f"无法获取国内更新清单：{exc}") from exc
    return parse_manifest(payload, channel["download_hosts"], current, edition=channel.get("edition", "gpu"))
