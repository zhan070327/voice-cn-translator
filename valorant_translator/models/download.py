from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Callable

from valorant_translator.config import MODEL_DIR, get_model_dir
from valorant_translator.models.location import folder_bytes, require_model_space
from valorant_translator.recognition.whisper import WHISPER_REPOS
from valorant_translator.translation.m2m import M2M_REPO, M2M_FILES

WHISPER_FILES = ("model.bin", "config.json", "tokenizer.json")
MODEL_INVENTORY = ".cos-files.json"
_WHISPER_DOWNLOAD = (*WHISPER_FILES, "vocabulary.txt")
_M2M_DOWNLOAD = (*M2M_FILES, "special_tokens_map.json", "generation_config.json")


def model_ready(folder: Path, required: tuple[str, ...]) -> bool:
    try:
        sizes = {name: (folder / name).stat().st_size
                 for name in required if (folder / name).is_file()}
        if len(sizes) != len(required) or not all(sizes.values()):
            return False
        inventory = folder / MODEL_INVENTORY
        if not inventory.exists():
            return True
        recorded = json.loads(inventory.read_text(encoding="utf-8"))
        return isinstance(recorded, dict) and all(recorded.get(name) == size for name, size in sizes.items())
    except (OSError, ValueError):
        return False


def models_ready(whisper_size: str, *, folder: Path | None = None) -> bool:
    folder = folder if folder is not None else get_model_dir(default=MODEL_DIR)
    return (model_ready(folder / f"whisper-{whisper_size}", WHISPER_FILES)
            and model_ready(folder / "m2m100_418M", M2M_FILES))


def check_huggingface_space(whisper_size: str, folder: Path) -> None:
    """Estimate required space from the same upstream files used for download."""
    from huggingface_hub import HfApi

    api = HfApi()
    required = 0
    for repo, files, essential in (
        (WHISPER_REPOS[whisper_size], _WHISPER_DOWNLOAD, WHISPER_FILES),
        (M2M_REPO, _M2M_DOWNLOAD, M2M_FILES),
    ):
        model = api.model_info(repo, files_metadata=True, timeout=15)
        sizes = {file.rfilename: file.size for file in model.siblings if file.rfilename in files}
        if set(essential) - sizes.keys() or any(size is None for size in sizes.values()):
            raise OSError(f"Cannot determine model download size: {repo}")
        required += sum(sizes.values())
    require_model_space(folder, required)


def download_models(whisper_size: str, on_status: Callable[[str], None],
                    *, folder: Path | None = None) -> None:
    from huggingface_hub import snapshot_download

    folder = folder if folder is not None else get_model_dir(default=MODEL_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    if whisper_size not in WHISPER_REPOS:
        raise ValueError("不支持的识别模型")
    on_status("正在下载识别模型…")
    whisper_dir = folder / f"whisper-{whisper_size}"
    snapshot_download(repo_id=WHISPER_REPOS[whisper_size], local_dir=whisper_dir,
                      allow_patterns=list(_WHISPER_DOWNLOAD))
    (whisper_dir / MODEL_INVENTORY).unlink(missing_ok=True)
    if not model_ready(whisper_dir, WHISPER_FILES):
        raise RuntimeError("识别模型下载不完整；再次点击下载可续传。")
    on_status("正在下载 M2M100 418M 翻译模型…")
    m2m_dir = folder / "m2m100_418M"
    snapshot_download(repo_id=M2M_REPO, local_dir=m2m_dir,
                      allow_patterns=list(_M2M_DOWNLOAD))
    (m2m_dir / MODEL_INVENTORY).unlink(missing_ok=True)
    if not model_ready(m2m_dir, M2M_FILES):
        raise RuntimeError("翻译模型下载不完整；再次点击下载可续传。")
    on_status("模型下载完成。现在可以断网运行。")


def import_models(source: Path, whisper_size: str, on_status: Callable[[str], None],
                  *, folder: Path | None = None) -> None:
    """Copy a downloaded model folder or a parent containing both model folders."""
    folder = folder if folder is not None else get_model_dir(default=MODEL_DIR)
    source = source.resolve()
    if not source.is_dir():
        raise ValueError("请选择模型文件夹，不是压缩包或单个文件。")
    candidates = [
        (source / f"whisper-{whisper_size}", folder / f"whisper-{whisper_size}", WHISPER_FILES),
        (source / "m2m100_418M", folder / "m2m100_418M", M2M_FILES),
    ]
    if model_ready(source, WHISPER_FILES):
        candidates.append((source, folder / f"whisper-{whisper_size}", WHISPER_FILES))
    if model_ready(source, M2M_FILES):
        candidates.append((source, folder / "m2m100_418M", M2M_FILES))
    valid = [(src, dst, files) for src, dst, files in candidates if model_ready(src, files)]
    if not valid:
        raise ValueError("文件夹里找不到完整模型。需要 model.bin 或 pytorch_model.bin 等文件。")
    copies = [(src, dst, files) for src, dst, files in valid if src.resolve() != dst.resolve()]
    if any(src.resolve() in dst.resolve().parents or dst.resolve() in src.resolve().parents
           for src, dst, _ in copies):
        raise ValueError("Source and destination model folders cannot be nested.")
    require_model_space(folder, sum(folder_bytes(src) for src, _, _ in copies))
    folder.mkdir(parents=True, exist_ok=True)
    for src, dst, files in copies:
        on_status(f"正在导入 {dst.name}；请勿关闭软件…")
        shutil.copytree(src, dst, dirs_exist_ok=True)
        if not (src / MODEL_INVENTORY).is_file():
            (dst / MODEL_INVENTORY).unlink(missing_ok=True)
        if not model_ready(dst, files):
            raise RuntimeError(f"{dst.name} 导入不完整，请检查磁盘空间后重试。")
    on_status("模型导入完成。")
