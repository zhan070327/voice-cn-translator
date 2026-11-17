"""Print local device and GPU readiness without downloading anything."""
from __future__ import annotations

import platform
import subprocess
import sys

from valorant_translator.audio.capture import list_devices
from valorant_translator.recognition.whisper import cuda_available


def main() -> None:
    print("平台：", platform.platform())
    print("Python：", sys.version.split()[0])
    try:
        output = subprocess.check_output(["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
                                          "--format=csv,noheader"], text=True, timeout=3)
        print("NVIDIA：", output.strip())
    except (OSError, subprocess.SubprocessError):
        print("NVIDIA：未检测到 nvidia-smi")
    print("CTranslate2 CUDA：", "可用" if cuda_available() else "不可用")
    try:
        import torch
        print("PyTorch CUDA：", "可用" if torch.cuda.is_available() else "不可用")
    except Exception as exc:
        print("PyTorch：加载失败", exc)
    try:
        for device in list_devices():
            print("设备：", "系统声音" if device.loopback else "麦克风", device.name, device.id)
    except Exception as exc:
        print("设备枚举失败：", exc)


if __name__ == "__main__":
    main()
