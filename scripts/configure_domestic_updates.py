"""Configure a domestic build; URLs are public, no cloud API credentials are stored."""
from __future__ import annotations

import json
import sys
from urllib.parse import urlparse

from scripts.configure_updates import CHANNEL
from valorant_translator.updates.domestic import validate_url
from valorant_translator.updates.github import INSTALLER_NAME, UpdateError


def configure(manifest_url: str, download_url: str) -> dict:
    try:
        manifest_host = urlparse(manifest_url).hostname
        download_host = urlparse(download_url).hostname
    except ValueError as exc:
        raise UpdateError("国内更新地址格式错误。") from exc
    if not manifest_host or not download_host:
        raise UpdateError("请填写真实的 HTTPS 清单和安装包地址。")
    validate_url(manifest_url, (manifest_host,))
    validate_url(download_url, (download_host,))
    if urlparse(download_url).path.rsplit("/", 1)[-1] != INSTALLER_NAME:
        raise UpdateError(f"安装包 URL 必须以 {INSTALLER_NAME} 结尾。")
    return {"channel": "domestic", "manifest_url": manifest_url,
            "download_hosts": [download_host]}


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("用法：python -m scripts.configure_domestic_updates 清单HTTPS地址 安装包HTTPS地址")
    data = configure(sys.argv[1], sys.argv[2])
    CHANNEL.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Domestic update channel configured: {data['manifest_url']}")


if __name__ == "__main__":
    main()
