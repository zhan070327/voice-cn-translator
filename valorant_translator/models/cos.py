"""Resume and install the verified domestic Small + M2M100 archive."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import stat
import time
from dataclasses import dataclass
from http.client import HTTPException
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from typing import Callable
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zipfile import ZipFile

from filelock import FileLock, Timeout

from valorant_translator.models.download import MODEL_INVENTORY, M2M_FILES, WHISPER_FILES, model_ready
from valorant_translator.updates.domestic import HASH, validate_url

CHUNK = 1024 * 1024
MAX_BUNDLE_BYTES = 4 * 1024**3
MAX_EXTRACTED_BYTES = 3 * 1024**3
REQUIRED = {"whisper-small": WHISPER_FILES, "m2m100_418M": M2M_FILES}
OPTIONAL = {"whisper-small": ("vocabulary.txt",),
            "m2m100_418M": ("generation_config.json", "special_tokens_map.json")}
NOTICE = Path(__file__).resolve().parents[2] / "THIRD_PARTY_NOTICES_CN.md"


class ModelPaused(Exception):
    pass


class DownloadError(ValueError):
    pass


class DirectDownload(HTTPRedirectHandler):
    # COS object URLs are direct. Don't follow a redirect to another origin.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_open = build_opener(DirectDownload()).open


@dataclass(frozen=True)
class Bundle:
    urls: tuple[str, ...]
    size: int
    sha256: str
    directories: tuple[str, ...] = tuple(REQUIRED)

    @classmethod
    def from_channel(cls, channel: dict) -> Bundle:
        data = channel.get("model_bundle")
        if not isinstance(data, dict):
            raise ValueError("国内更新通道缺少模型下载配置。")
        return cls.from_entry(data, channel["download_hosts"])

    @classmethod
    def from_entry(cls, data: dict, allowed: tuple[str, ...],
                   directories: tuple[str, ...] = tuple(REQUIRED)) -> Bundle:
        urls, size, digest = data.get("urls"), data.get("size"), data.get("sha256")
        if (not isinstance(urls, list) or not 1 <= len(urls) <= 3
                or type(size) is not int or not 0 < size <= MAX_BUNDLE_BYTES
                or not isinstance(digest, str) or not HASH.fullmatch(digest)):
            raise ValueError("国内模型下载配置无效。")
        return cls(tuple(validate_url(url, allowed) for url in urls), size, digest.lower(), directories)


def _check_pause(pause: Event) -> None:
    if pause.is_set():
        raise ModelPaused()


def _transfer(url: str, bundle: Bundle, partial: Path, pause: Event,
              progress: Callable[[int, int, float], None]) -> None:
    offset = partial.stat().st_size if partial.exists() else 0
    headers = {"Accept-Encoding": "identity"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    _check_pause(pause)
    with _open(Request(url, headers=headers), timeout=15) as response:
        if response.headers.get("Content-Encoding", "identity").lower() != "identity":
            raise DownloadError("服务器返回了不支持的压缩编码。")
        if response.status == 206 and offset:
            if response.headers.get("Content-Range") != f"bytes {offset}-{bundle.size - 1}/{bundle.size}":
                raise DownloadError("服务器返回的续传范围无效。")
        elif response.status == 200:
            offset = 0  # A mirror may ignore Range and send the full object.
        else:
            raise DownloadError(f"意外的下载状态：HTTP {response.status}")
        if response.headers.get("Content-Length") != str(bundle.size - offset):
            raise DownloadError("服务器公布的模型大小与配置不一致。")
        _check_pause(pause)
        started = last_update = time.monotonic()
        received = offset
        progress(received, bundle.size, 0.0)
        with partial.open("ab" if offset else "wb") as output:
            while received < bundle.size:
                _check_pause(pause)
                chunk = response.read(min(CHUNK, bundle.size - received))
                if not chunk:
                    raise DownloadError("下载提前中断。")
                output.write(chunk)
                received += len(chunk)
                now = time.monotonic()
                if now - last_update >= 0.25 or received == bundle.size:
                    progress(received, bundle.size, (received - offset) / max(0.001, now - started))
                    last_update = now


def _download(bundle: Bundle, partial: Path, pause: Event,
              progress: Callable[[int, int, float], None]) -> None:
    if partial.exists() and partial.stat().st_size > bundle.size:
        partial.unlink()
    if partial.exists() and partial.stat().st_size == bundle.size:
        return
    errors = []
    for url in bundle.urls:
        try:
            _transfer(url, bundle, partial, pause, progress)
            return
        except (OSError, HTTPException, DownloadError) as exc:
            errors.append(str(exc))
    _check_pause(pause)
    raise DownloadError("模型下载失败，已保留进度。可点击重试：" + " / ".join(errors))


def required_files(directory: str) -> tuple[str, ...]:
    return REQUIRED.get(directory, (*WHISPER_FILES, "vocabulary.txt"))


def _extract(archive: Path, staging: Path, missing: set[str], pause: Event,
             directories: tuple[str, ...] = tuple(REQUIRED)) -> None:
    with ZipFile(archive) as bundle:
        allowed = {f"{folder}/{name}" for folder in directories
                   for name in (*required_files(folder), *OPTIONAL.get(folder, ()))}
        required = {f"{folder}/{name}" for folder in directories for name in required_files(folder)}
        seen = set()
        entries = []
        for member in bundle.infolist():
            name = member.filename
            kind = stat.S_IFMT(member.external_attr >> 16)
            if member.is_dir() and name in {f"{folder}/" for folder in directories}:
                if kind not in (0, stat.S_IFDIR) or name in seen:
                    raise ValueError(f"模型 ZIP 包含异常目录：{name}")
                seen.add(name)
                continue
            if (name not in allowed or name in seen or kind not in (0, stat.S_IFREG)
                    or member.file_size < 1):
                raise ValueError(f"模型 ZIP 包含异常文件：{name}")
            seen.add(name)
            if name.split("/", 1)[0] in missing:
                entries.append(member)
        if not required <= seen or sum(item.file_size for item in entries) > MAX_EXTRACTED_BYTES:
            raise ValueError("模型 ZIP 不完整或解压大小超过限制。")
        for member in entries:
            target = staging / member.filename
            target.parent.mkdir(exist_ok=True)
            with bundle.open(member) as source, target.open("xb") as output:
                while chunk := source.read(CHUNK):
                    _check_pause(pause)
                    output.write(chunk)


def install_models(bundle: Bundle, folder: Path, status: Callable[[str], None],
                   progress: Callable[[int, int, float], None], pause: Event,
                   *, selected: tuple[str, ...] | None = None) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(folder / ".cos-models.lock", timeout=0):
            _install_models(bundle, folder, status, progress, pause, selected or bundle.directories)
    except Timeout as exc:
        raise DownloadError("另一个软件窗口正在安装模型，请稍后重试。") from exc


def _install_models(bundle: Bundle, folder: Path, status: Callable[[str], None],
                    progress: Callable[[int, int, float], None], pause: Event, selected: tuple[str, ...]) -> None:
    if not set(selected) <= set(bundle.directories):
        raise ValueError("所选模型与 ZIP 不匹配。")
    missing = {name for name in selected if not model_ready(folder / name, required_files(name))}
    if not missing:
        status("本地模型已齐全，可离线使用。")
        return
    partial = folder / f".cos-{bundle.sha256}.zip.part"
    _download(bundle, partial, pause, progress)
    status("正在校验模型 SHA-256…")
    digest = hashlib.sha256()
    with partial.open("rb") as source:
        while chunk := source.read(CHUNK):
            _check_pause(pause)
            digest.update(chunk)
    if digest.hexdigest() != bundle.sha256:
        partial.unlink()
        raise ValueError("模型 SHA-256 校验失败，已清除损坏文件。请重试。")
    status("正在解压安装模型…")
    with TemporaryDirectory(prefix=".cos-stage-", dir=folder) as temporary:
        stage = Path(temporary)
        _extract(partial, stage, missing, pause, bundle.directories)
        for name in missing:
            model = stage / name
            sizes = {file: (model / file).stat().st_size for file in required_files(name)}
            (model / MODEL_INVENTORY).write_text(json.dumps(sizes), encoding="utf-8")
            shutil.copyfile(NOTICE, model / "MODEL_LICENSES.md")
        _check_pause(pause)
        for name in sorted(missing):
            destination = folder / name
            if model_ready(destination, required_files(name)):
                continue
            # Preserve incomplete folders for recovery rather than mixing their contents.
            backup = stage / f"{name}.previous"
            if destination.exists() or destination.is_symlink():
                destination.rename(backup)
            try:
                (stage / name).rename(destination)
            except OSError:
                if backup.exists() or backup.is_symlink():
                    backup.rename(destination)
                raise
    partial.unlink(missing_ok=True)
    status("模型下载并安装完成。现在可以断网运行。")
