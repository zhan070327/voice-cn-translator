"""Audit Windows CUDA DLLs and prune them only in an explicit CPU build."""
from __future__ import annotations

import argparse
import hashlib
from collections import defaultdict
from pathlib import Path

CUDA_NAMES = ("cuda", "cudnn", "cublas", "cufft", "curand", "cusparse", "nvrtc",
              "nvjitlink", "nvblas", "nvjpeg", "cusolver", "cufile")


def cuda_dlls(folder: Path):
    if not folder.is_dir():
        raise FileNotFoundError(f"打包目录不存在：{folder}")
    for path in folder.rglob("*"):
        if path.is_file() and path.suffix.lower() == ".dll" and any(
                name in path.name.lower() for name in CUDA_NAMES):
            yield path


def prune_cpu_runtime(folder: Path) -> list[Path]:
    candidates = list(cuda_dlls(folder))
    if any(path.name.lower().startswith(("torch_cuda", "c10_cuda")) for path in candidates):
        raise ValueError("CPU 打包意外使用 CUDA PyTorch，已停止。")
    for path in candidates:
        path.unlink()
    return candidates


def audit(folder: Path) -> tuple[int, int, list[list[Path]]]:
    groups: dict[tuple[int, str], list[Path]] = defaultdict(list)
    total = 0
    for path in cuda_dlls(folder):
        size = path.stat().st_size
        total += size
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        groups[(size, digest.hexdigest())].append(path)
    duplicate_groups = [paths for paths in groups.values() if len(paths) > 1]
    duplicate_bytes = sum((len(paths) - 1) * paths[0].stat().st_size for paths in duplicate_groups)
    return total, duplicate_bytes, duplicate_groups


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    parser.add_argument("--max-duplicate-mib", type=int, default=256)
    parser.add_argument("--prune-cpu-only", action="store_true")
    args = parser.parse_args()
    if args.prune_cpu_only:
        removed = prune_cpu_runtime(args.folder)
        for path in removed:
            print(f"CPU bundle excludes {path.relative_to(args.folder)}")
    total, duplicates, groups = audit(args.folder)
    print(f"CUDA DLL 总计 {total / 1024**2:.1f} MiB；精确重复 {duplicates / 1024**2:.1f} MiB。")
    for paths in groups[:8]:
        print("重复：" + " | ".join(str(path.relative_to(args.folder)) for path in paths))
    if duplicates > args.max_duplicate_mib * 1024**2:
        print("重复 CUDA DLL 过多；已停止打包，请先检查 PyInstaller 收集结果，勿直接删除 DLL。")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
