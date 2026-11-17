from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from valorant_translator.config import MODEL_DIR
from valorant_translator.recognition.whisper import WHISPER_REPOS
from valorant_translator.translation.m2m import M2M_REPO, M2M_FILES

WHISPER_FILES = ("model.bin", "config.json", "tokenizer.json")


def model_ready(folder: Path, required: tuple[str, ...]) -> bool:
    return all((folder / name).is_file() and (folder / name).stat().st_size > 0
               for name in required)


def models_ready(whisper_size: str) -> bool:
    return (model_ready(MODEL_DIR / f"whisper-{whisper_size}", WHISPER_FILES)
            and model_ready(MODEL_DIR / "m2m100_418M", M2M_FILES))


def download_models(whisper_size: str, on_status: Callable[[str], None]) -> None:
    from huggingface_hub import snapshot_download

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    if whisper_size not in WHISPER_REPOS:
        raise ValueError("不支持的识别模型")
    on_status("正在下载识别模型…")
    whisper_dir = MODEL_DIR / f"whisper-{whisper_size}"
    snapshot_download(repo_id=WHISPER_REPOS[whisper_size], local_dir=whisper_dir,
                      allow_patterns=[*WHISPER_FILES, "vocabulary.txt"])
    if not model_ready(whisper_dir, WHISPER_FILES):
        raise RuntimeError("识别模型下载不完整；再次点击下载可续传。")
    on_status("正在下载 M2M100 翻译模型（约 2 GB）…")
    m2m_dir = MODEL_DIR / "m2m100_418M"
    snapshot_download(repo_id=M2M_REPO, local_dir=m2m_dir,
                      allow_patterns=[*M2M_FILES, "special_tokens_map.json",
                                      "generation_config.json"])
    if not model_ready(m2m_dir, M2M_FILES):
        raise RuntimeError("翻译模型下载不完整；再次点击下载可续传。")
    on_status("模型下载完成。现在可以断网运行。")


def import_models(source: Path, whisper_size: str, on_status: Callable[[str], None]) -> None:
    """Copy a downloaded model folder or a parent containing both model folders."""
    source = source.resolve()
    if not source.is_dir():
        raise ValueError("请选择模型文件夹，不是压缩包或单个文件。")
    candidates = [
        (source / f"whisper-{whisper_size}", MODEL_DIR / f"whisper-{whisper_size}", WHISPER_FILES),
        (source / "m2m100_418M", MODEL_DIR / "m2m100_418M", M2M_FILES),
    ]
    if model_ready(source, WHISPER_FILES):
        candidates.append((source, MODEL_DIR / f"whisper-{whisper_size}", WHISPER_FILES))
    if model_ready(source, M2M_FILES):
        candidates.append((source, MODEL_DIR / "m2m100_418M", M2M_FILES))
    valid = [(src, dst, files) for src, dst, files in candidates if model_ready(src, files)]
    if not valid:
        raise ValueError("文件夹里找不到完整模型。需要 model.bin 或 pytorch_model.bin 等文件。")
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for src, dst, files in valid:
        if src == dst.resolve():
            continue
        on_status(f"正在导入 {dst.name}；请勿关闭软件…")
        shutil.copytree(src, dst, dirs_exist_ok=True)
        if not model_ready(dst, files):
            raise RuntimeError(f"{dst.name} 导入不完整，请检查磁盘空间后重试。")
    on_status("模型导入完成。")
