"""Detect exact duplicate CUDA DLL payloads in the Windows onedir bundle."""
from __future__ import annotations

import argparse
import hashlib
from collections import defaultdict
from pathlib import Path

CUDA_NAMES = ("cuda", "cudnn", "cublas", "cufft", "curand", "cusparse", "nvrtc",
              "nvjitlink", "nvblas", "nvjpeg")


def audit(folder: Path) -> tuple[int, int, list[list[Path]]]:
    if not folder.is_dir():
        raise FileNotFoundError(f"打包目录不存在：{folder}")
    groups: dict[tuple[int, str], list[Path]] = defaultdict(list)
    total = 0
    for path in folder.rglob("*"):
        if not path.is_file() or path.suffix.lower() != ".dll" or not any(
                name in path.name.lower() for name in CUDA_NAMES):
            continue
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
    args = parser.parse_args()
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
