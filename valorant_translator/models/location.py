"""Disk checks and a reversible move of the application's model files."""
from __future__ import annotations

import logging
import shutil
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from filelock import FileLock

from valorant_translator.config import Settings, get_model_dir
from valorant_translator.recognition.whisper import WHISPER_REPOS

LOG = logging.getLogger(__name__)
_MODEL_DIRS = {"m2m100_418M", *(f"whisper-{size}" for size in WHISPER_REPOS)}


class InsufficientModelSpace(OSError):
    def __init__(self, folder: Path, required: int, available: int):
        super().__init__(f"{folder}: {available} bytes free, {required} required")
        self.folder = folder
        self.required = required
        self.available = available


def require_model_space(folder: Path, required: int) -> None:
    disk = folder
    while not disk.exists() and disk != disk.parent:
        disk = disk.parent
    available = shutil.disk_usage(disk).free
    if required > available:
        raise InsufficientModelSpace(folder, required, available)


def folder_bytes(folder: Path) -> int:
    return sum(path.stat().st_size for path in folder.rglob("*") if path.is_file())


def move_model_dir(settings: Settings, destination: Path, *, default: Path | None = None) -> Path:
    source = get_model_dir(settings, default=default).resolve()
    destination = destination.resolve()
    if destination == source:
        return source
    if not destination.is_dir():
        raise NotADirectoryError(destination)
    if source in destination.parents or destination in source.parents:
        raise ValueError("Model folders cannot be nested.")
    with ExitStack() as locks:
        for folder in sorted({source, destination}):
            if folder.exists():
                locks.enter_context(FileLock(folder / ".cos-models.lock", timeout=0))
        entries = []
        if source.is_dir():
            entries = [path for path in source.iterdir()
                       if ((path.name in _MODEL_DIRS and path.is_dir())
                           or (path.match(".cos-*.zip.part") and path.is_file()))]
        for path in entries:
            if path.is_symlink() or (path.is_dir() and any(item.is_symlink() for item in path.rglob("*"))):
                raise OSError(f"Cannot move a model containing links: {path}")
            if (destination / path.name).exists() or (destination / path.name).is_symlink():
                raise FileExistsError(destination / path.name)
        if entries:
            require_model_space(destination, sum(folder_bytes(p) if p.is_dir() else p.stat().st_size for p in entries))
            with TemporaryDirectory(prefix=".model-move-", dir=destination) as staging:
                _move_entries(settings, source, destination, Path(staging), entries, default)
        else:
            _save_location(settings, destination, default)
    return destination


def _save_location(settings: Settings, destination: Path, default: Path | None) -> None:
    previous = settings.model_dir
    default_folder = get_model_dir(Settings(), default=default).resolve()
    settings.model_dir = "" if destination == default_folder else str(destination)
    try:
        settings.save()
    except Exception:
        settings.model_dir = previous
        raise


def _move_entries(settings: Settings, source: Path, destination: Path, staging: Path,
                  entries: list[Path], default: Path | None) -> None:
    # Copy all data first. Source renames are on the same volume and can be undone.
    for path in entries:
        if path.is_dir():
            shutil.copytree(path, staging / path.name)
        else:
            shutil.copy2(path, staging / path.name)

    backup = source / f".model-move-backup-{uuid4().hex}"
    backup.mkdir()
    installed, original = [], []
    try:
        for path in entries:
            target = destination / path.name
            if target.exists() or target.is_symlink():
                raise FileExistsError(target)
            (staging / path.name).rename(target)
            installed.append(target)
        for path in entries:
            path.rename(backup / path.name)
            original.append(path)
        _save_location(settings, destination, default)
    except Exception:
        try:
            for path in reversed(original):
                (backup / path.name).rename(path)
            for target in installed:
                target.rename(staging / target.name)
            backup.rmdir()
        except OSError:
            LOG.exception("Model move rollback was interrupted; recover originals from %s", backup)
        raise
    try:
        shutil.rmtree(backup)
    except OSError:
        LOG.warning("Models moved, but the backup could not be fully removed: %s", backup, exc_info=True)
