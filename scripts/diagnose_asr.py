"""Run the app's Whisper Small decoder directly, without Qt or model downloads."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np

from valorant_translator.config import MODEL_DIR, Settings
from valorant_translator.recognition.whisper import filter_segments, transcribe_segments
from valorant_translator.translation.glossary import GAMES, game_glossary

SAMPLE_RATE = 16000
REQUIRED = ("model.bin", "config.json", "tokenizer.json")
SAPI = """
$ErrorActionPreference = 'Stop'
$voice = New-Object -ComObject SAPI.SpVoice
$tokens = $voice.GetVoices()
$english = $null
for ($index = 0; $index -lt $tokens.Count; $index++) {
    $token = $tokens.Item($index)
    $englishLanguage = ($token.GetAttribute('Language') -split ';') | Where-Object {
        ([Convert]::ToInt32($_, 16) -band 1023) -eq 9
    }
    if ($englishLanguage) {
        $english = $token
        break
    }
}
if ($null -eq $english) { throw 'No English SAPI voice installed.' }
$voice.Voice = $english
$voice.Rate = -2
$stream = New-Object -ComObject SAPI.SpFileStream
$stream.Format.Type = 18
$stream.Open($env:VOICECN_DIAGNOSTIC_WAV, 3, $false)
try {
    $voice.AllowAudioOutputFormatChangesOnNextSet = $false
    $voice.AudioOutputStream = $stream
    [void]$voice.Speak($env:VOICECN_DIAGNOSTIC_TEXT)
} finally {
    $stream.Close()
}
"""


def find_clip(explicit: Path | None, report: Path) -> tuple[Path, str, str]:
    folder = ROOT / "test_audio"
    entries = json.loads((folder / "manifest.example.json").read_text(encoding="utf-8"))
    entry = next(item for item in entries if item["reference_text"] == "Two A main")
    reference = entry["reference_text"]
    recorded = folder / entry["file"]
    if explicit is not None:
        return explicit, reference, "user_wav"
    if recorded.is_file():
        return recorded, reference, "manifest_wav"
    generated = report.with_name("diagnose_asr_input.wav")
    if sys.platform == "win32":
        if not generated.is_file():
            try:
                subprocess.run(
                    ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", SAPI],
                    env={**os.environ, "VOICECN_DIAGNOSTIC_WAV": str(generated),
                         "VOICECN_DIAGNOSTIC_TEXT": reference},
                    check=True, timeout=30, capture_output=True, text=True, errors="replace",
                )
            except (OSError, subprocess.SubprocessError) as exc:
                generated.unlink(missing_ok=True)
                detail = getattr(exc, "stderr", "") or str(exc)
                raise RuntimeError(f"Windows 英文 TTS 失败：{detail.strip()}。请录制 “{reference}” 并用 --audio 指定。") from exc
        return generated, reference, "windows_sapi"
    raise FileNotFoundError(f"未找到 {recorded}。请录制 “{reference}”，以 --audio 指定 16 kHz 单声道 WAV。")


def load_clip(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as handle:
        if (handle.getframerate(), handle.getnchannels(), handle.getsampwidth()) != (SAMPLE_RATE, 1, 2):
            raise ValueError(f"{path} 必须为 16 kHz 单声道 16-bit PCM WAV。")
        pcm = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2")
    if not pcm.size or not pcm.any():
        raise ValueError(f"{path} 没有可用音频。")
    audio = pcm.astype(np.float32) / 32768
    # Match the reported case length with silence, without dropping any spoken audio.
    if len(audio) < 3 * SAMPLE_RATE:
        audio = np.pad(audio, (0, 3 * SAMPLE_RATE - len(audio)))
    return audio


def matches_reference(text: str, reference: str) -> bool:
    def normalize(value):
        tokens = re.findall(r"[a-z0-9]+", value.casefold())
        return ["two" if token in {"2", "two"} else token for token in tokens]
    return normalize(text) == normalize(reference)


def run_device(model_dir, audio, reference, device, glossary, fast_mode):
    from faster_whisper import WhisperModel

    compute_type = "float16" if device == "cuda" else "int8"
    result = {"device": device, "compute_type": compute_type, "runs": [], "passed": False}
    try:
        start = time.perf_counter()
        model = WhisperModel(str(model_dir), device=device, compute_type=compute_type,
                             local_files_only=True, num_workers=1)
        result["load_seconds"] = time.perf_counter() - start
        result["actual_device"] = str(model.model.device)
        result["actual_compute_type"] = str(model.model.compute_type)
        for hotwords in (glossary.hotwords, None):
            started = time.perf_counter()
            lazy_segments, info = transcribe_segments(model, audio, None, fast_mode=fast_mode, hotwords=hotwords)
            segments = list(lazy_segments)
            elapsed = time.perf_counter() - started
            rejections = []
            accepted = filter_segments(segments, info, None, glossary,
                                       lambda reason, detail: rejections.append({"reason": reason, "detail": detail}))
            raw = " ".join(s.text.strip() for s in segments).strip()
            row = {
                "mode": "app_hotwords" if hotwords is not None else "no_hotwords",
                "text": raw, "language": info.language, "confidence": float(info.language_probability),
                "inference_seconds": elapsed, "accepted_text": accepted.text if accepted else "",
                "accepted_language": accepted.language if accepted else None, "filter_rejections": rejections,
                "raw_matches_reference": (info.language == "en" and matches_reference(raw, reference)),
                "accepted_matches_reference": bool(accepted and accepted.language == "en"
                                                  and matches_reference(accepted.text, reference)),
                "segments": [{"text": s.text, "start": s.start, "end": s.end, "no_speech_prob": s.no_speech_prob,
                              "avg_logprob": s.avg_logprob} for s in segments],
            }
            result["runs"].append(row)
        app = result["runs"][0]
        if not app["raw_matches_reference"]:
            result["verdict"] = "model_output_failed"
        elif not app["accepted_matches_reference"]:
            result["verdict"] = "filtered_by_app"
        elif device == "cpu":
            result["verdict"] = "recognition_passed"
        elif len(audio) != 3 * SAMPLE_RATE:
            result["verdict"] = "cuda_not_three_seconds"
        elif app["inference_seconds"] < 1:
            result["verdict"] = "cuda_passed"
        elif app["inference_seconds"] > 2:
            result["verdict"] = "model_inference_slow"
        else:
            result["verdict"] = "cuda_inconclusive_1_to_2_seconds"
        result["passed"] = result["verdict"] in {"recognition_passed", "cuda_passed"}
    except Exception as exc:
        result["verdict"] = "device_or_model_error"
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


VERDICTS = {
    "model_output_failed": "模型原始输出为空或与参考句不符，先检查模型/输入。",
    "filtered_by_app": "模型原始输出正确，但被应用过滤，原因见表。",
    "recognition_passed": "CPU 识别正确；CPU 耗时作为对照。",
    "cuda_not_three_seconds": "识别正确；音频长度超过 3 秒，不能判定 RTX 延迟门槛。",
    "cuda_passed": "CUDA 模型层通过：3 秒音频正确识别且低于 1 秒，继续核对管线日志。",
    "model_inference_slow": "CUDA 推理超过 2 秒，先检查模型/推理层。",
    "cuda_inconclusive_1_to_2_seconds": "CUDA 耗时为 1–2 秒，未达到目标，尚不能确定根因。",
    "device_or_model_error": "设备或模型初始化/推理失败，未尝试切换运行设备。",
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", choices=["cuda", "cpu", "both"], default="both")
    parser.add_argument("--model-dir", type=Path, default=MODEL_DIR, help="模型根目录或 whisper-small 目录")
    parser.add_argument("--audio", type=Path, help="录有 Two A main 的 16 kHz 单声道 PCM WAV")
    parser.add_argument("--game", choices=GAMES, default=None, help="默认使用应用当前游戏词库")
    parser.add_argument("--report", type=Path, default=Path("diagnose_asr_report.json"))
    args = parser.parse_args(argv)
    report_path = args.report.resolve()
    if args.audio is not None and args.audio.resolve() == report_path:
        parser.error("--report 和 --audio 必须使用不同文件")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    model_dir = args.model_dir.resolve()
    if model_dir.name != "whisper-small":
        model_dir /= "whisper-small"
    output = {"model": "whisper-small", "model_dir": str(model_dir), "devices": [], "passed": False}
    try:
        missing = [str(model_dir / file) for file in REQUIRED
                   if not (model_dir / file).is_file() or (model_dir / file).stat().st_size == 0]
        if missing:
            raise FileNotFoundError("模型缺少文件或文件为空：" + "；".join(missing))
        path, reference, source = find_clip(args.audio, report_path)
        audio = load_clip(path)
        settings = Settings.load()
        game = args.game or settings.game
        glossary = game_glossary(game)
        output.update(audio=str(path.resolve()), audio_source=source, reference=reference,
                      audio_seconds=len(audio) / SAMPLE_RATE, game=game, fast_mode=settings.fast_mode)
        devices = ("cuda", "cpu") if args.device == "both" else (args.device,)
        print(f"参考句：{reference}；输入 {output['audio_seconds']:.2f}s；模型 {model_dir}")
        for device in devices:
            result = run_device(model_dir, audio, reference, device, glossary, settings.fast_mode)
            output["devices"].append(result)
            print(f"\n{device.upper()} / {result.get('actual_compute_type', result['compute_type'])}")
            for row in result["runs"]:
                print(f"  {row['mode']}: {row['text']!r} 语言={row['language']} "
                      f"置信度={row['confidence']:.3f} 推理={row['inference_seconds']:.3f}s "
                      f"通过过滤={row['accepted_text']!r}")
                for s in row["segments"]:
                    print(f"    {s['start']:.2f}–{s['end']:.2f}s {s['text'].strip()!r} "
                          f"no_speech_prob={s['no_speech_prob']:.3f} avg_logprob={s['avg_logprob']:.3f}")
                for rejection in row["filter_rejections"]:
                    print(f"    过滤：{rejection['reason']}")
            print(f"  结论：{VERDICTS[result['verdict']]}")
            if "error" in result:
                print(f"  {result['error']}")
        output["passed"] = all(device["passed"] for device in output["devices"])
    except (OSError, ValueError, RuntimeError, wave.Error) as exc:
        output["error"] = str(exc)
        print(f"诊断无法完成：{exc}", file=sys.stderr)
    report_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"诊断报告：{report_path}")
    return 0 if output["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
