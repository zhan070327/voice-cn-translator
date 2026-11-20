"""Read trusted COS model packages and retain the built-in Small + M2M bundle."""
from __future__ import annotations

import json
from pathlib import Path
from threading import Event
from typing import Callable

from valorant_translator.models import cos
from valorant_translator.models.download import M2M_FILES, WHISPER_FILES, model_ready
from valorant_translator.recognition.whisper import WHISPER_REPOS
from valorant_translator.updates.domestic import validate_url

MAX_CATALOG = 64 * 1024


def fetch_catalog(channel: dict) -> dict[str, cos.Bundle]:
    url = validate_url(channel["model_manifest_url"], channel["download_hosts"])
    with cos._open(url, timeout=8) as response:
        raw = response.read(MAX_CATALOG + 1)
    if len(raw) > MAX_CATALOG:
        raise ValueError("模型清单过大。")
    catalog = json.loads(raw)
    if not isinstance(catalog, dict) or catalog.get("schema") != 1 or not isinstance(catalog.get("models"), dict):
        raise ValueError("不支持的模型清单。")
    bundles = {}
    for key, info in catalog["models"].items():
        if key not in WHISPER_REPOS or not isinstance(info, dict):
            continue
        name = f"whisper-{key}"
        addresses = info.get("urls", [])
        if not addresses or any(not isinstance(url, str) or not url.endswith(f"/{name}.zip") for url in addresses):
            continue
        bundles[key] = cos.Bundle.from_entry(info, channel["download_hosts"], (name,))
    return bundles


def required_downloads(channel: dict, catalog: dict[str, cos.Bundle], size: str,
                       folder: Path) -> list[tuple[cos.Bundle, tuple[str, ...]]]:
    whisper = f"whisper-{size}"
    missing_whisper = not model_ready(folder / whisper, WHISPER_FILES)
    missing_translation = not model_ready(folder / "m2m100_418M", M2M_FILES)
    if not (missing_whisper or missing_translation):
        return []
    plan = []
    combined = []
    if missing_whisper:
        bundle = catalog.get(size)
        if bundle:
            plan.append((bundle, (whisper,)))
        elif size == "small":
            combined.append(whisper)
        else:
            raise ValueError(f"COS 清单暂未提供 {size}。可选择 Small 或从文件夹导入。")
    if missing_translation:
        combined.append("m2m100_418M")
    if combined:
        plan.append((cos.Bundle.from_channel(channel), tuple(combined)))
    return plan


def install_selected(channel: dict, catalog: dict[str, cos.Bundle], size: str, folder: Path,
                     status: Callable, progress: Callable, pause: Event) -> None:
    plan = required_downloads(channel, catalog, size, folder)
    total = sum(bundle.size for bundle, _ in plan)
    completed = 0
    for bundle, selected in plan:
        status("正在下载 " + "、".join(selected) + "…")
        cos.install_models(bundle, folder, status,
                           lambda received, _, speed: progress(completed + received, total, speed),
                           pause, selected=selected)
        completed += bundle.size
        progress(completed, total, 0.0)
