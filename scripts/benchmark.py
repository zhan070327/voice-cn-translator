"""Offline model benchmark over user supplied, consented 16 kHz PCM WAV files."""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import time
import wave
from pathlib import Path

import numpy as np
import psutil
from opencc import OpenCC

from valorant_translator.recognition.whisper import LocalWhisper
from valorant_translator.translation.m2m import LocalM2M


def gpu_memory_mib() -> float | None:
    try:
        output = subprocess.check_output(
            ["nvidia-smi", "--query-compute-apps=pid,used_gpu_memory", "--format=csv,noheader,nounits"],
            text=True, timeout=2)
        values = []
        for line in output.splitlines():
            pid, _, used = line.partition(",")
            if pid.strip() == str(os.getpid()):
                values.append(float(used.strip()))
        return sum(values) if values else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wav:
        if wav.getframerate() != 16000 or wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            raise ValueError(f"{path}: 需要 16 kHz/单声道/16-bit PCM WAV")
        return np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.float32) / 32768


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path, help="JSON list: file, language, reference_text, reference_chinese")
    parser.add_argument("--output", type=Path, default=Path("benchmark_results.csv"))
    parser.add_argument("--whisper", default="small")
    parser.add_argument("--device", default="auto")
    args = parser.parse_args()
    entries = json.loads(args.manifest.read_text(encoding="utf-8"))
    asr = LocalWhisper(args.whisper, args.device)
    translator = LocalM2M(args.device)
    chinese_converter = OpenCC("t2s")
    process = psutil.Process()
    rows = []
    for item in entries:
        audio = read_wav(args.manifest.parent / item["file"])
        cpu_before = process.cpu_times()
        t0 = time.perf_counter()
        error = ""
        try:
            recognized = asr.transcribe(audio, item.get("language", "auto"))
        except Exception as exc:
            recognized = None
            error = f"ASR: {exc}"
        t1 = time.perf_counter()
        try:
            translation = (chinese_converter.convert(recognized.text)
                           if recognized and recognized.language == "zh" else
                           translator.translate(recognized.text, recognized.language) if recognized else "")
        except Exception as exc:
            translation = ""
            error = f"翻译: {exc}"
        t2 = time.perf_counter()
        cpu_after = process.cpu_times()
        cpu_seconds = (cpu_after.user + cpu_after.system) - (cpu_before.user + cpu_before.system)
        rows.append({
            "file": item["file"], "expected_language": item["language"],
            "detected_language": recognized.language if recognized else "",
            "reference_text": item["reference_text"],
            "recognized_text": recognized.text if recognized else "",
            "reference_chinese": item["reference_chinese"], "translated_chinese": translation,
            "asr_s": round(t1-t0, 3), "translation_s": round(t2-t1, 3),
            "total_s": round(t2-t0, 3), "ram_mib": round(process.memory_info().rss/1048576, 1),
            "cpu_percent_one_core": round(100 * cpu_seconds / max(0.001, t2-t0), 1),
            "gpu_memory_mib": gpu_memory_mib(),
            "failed": int(not recognized or not translation), "error": error,
        })
    if not rows:
        raise ValueError("清单没有测试音频")
    with args.output.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"已写入 {args.output}；请人工对照参考文本审核识别和译文。")


if __name__ == "__main__":
    main()
