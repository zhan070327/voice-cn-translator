"""Check whether an existing PyTorch can run this app's CUDA translation.

Exit 0: reuse; 2: wheel missing/CPU/unsupported version, install official CUDA wheel;
exit 3: CUDA driver/runtime unavailable; 4: GPU kernel unsupported, stop without auto-download.
"""
from __future__ import annotations

import argparse
import re


def check(torch, *, package_only: bool = False) -> tuple[int, str]:
    raw_version = str(getattr(torch, "__version__", ""))
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:\+[^\s]+)?$", raw_version)
    if not match or not (2, 6) <= (int(match.group(1)), int(match.group(2))) < (3, 0):
        return 2, f"PyTorch {raw_version or '未知'} 不满足项目要求 2.6 至 2.x。"
    cuda_version = getattr(getattr(torch, "version", None), "cuda", None)
    if not cuda_version:
        return 2, f"PyTorch {raw_version} 是 CPU 版，需安装 NVIDIA CUDA 版。"
    if package_only:
        return 0, f"CUDA wheel OK: PyTorch {raw_version}, CUDA {cuda_version}. GPU execution not tested."
    try:
        if not torch.cuda.is_available() or torch.cuda.device_count() < 1:
            return 3, f"PyTorch {raw_version} 带 CUDA {cuda_version}，但 GPU/驱动不可用；重新下载不能解决。"
        with torch.inference_mode():
            sample = torch.ones((2, 2), device="cuda", dtype=torch.float16)
            result = (sample @ sample).sum().item()
        torch.cuda.synchronize()
        if result != 8.0:
            return 3, "GPU 运算结果异常，构建已停止。"
        name = torch.cuda.get_device_name(0)
        return 0, f"复用 PyTorch {raw_version}（CUDA {cuda_version}，{name}）；GPU 运算通过。"
    except Exception as exc:
        message = str(exc)
        detail = message.splitlines()[0] if message.splitlines() else type(exc).__name__
        if "no kernel image" in message.lower() or "invalid device function" in message.lower():
            return 4, f"现有 PyTorch 不支持此显卡内核：{detail}"
        return 3, f"GPU/驱动或 CUDA 运行库错误：{detail}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-only", action="store_true",
                        help="Require a CUDA wheel without executing GPU kernels on the build runner.")
    parser.add_argument("--cpu-package", action="store_true", help="Require the CPU wheel.")
    args = parser.parse_args()
    try:
        import torch
    except Exception as exc:
        print(f"PyTorch 未安装或无法导入：{exc}")
        return 2
    if args.cpu_package:
        if torch.version.cuda or not re.fullmatch(r"2\.(?:[6-9]|[1-9][0-9]+)\.\d+(?:\+cpu)?", torch.__version__):
            print("A supported CPU-only PyTorch wheel is required.")
            return 2
        print(f"CPU wheel OK: {torch.__version__}")
        return 0
    code, message = check(torch, package_only=args.package_only)
    print(message)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
