"""Offline glossary and speech benchmarks. Use the same input WAVs for comparisons."""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import threading
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

import numpy as np


def quantiles(values):
    values = sorted(values)
    if not values:
        return {}
    upper = statistics.quantiles(values, n=100, method="inclusive")[94] if len(values) > 1 else values[0]
    return {"n": len(values), "p50": statistics.median(values), "p95": upper}


def match_benchmark(repeat, legacy):
    from valorant_translator.config import Settings
    from valorant_translator.translation.glossary import Glossary

    glossary = Glossary()
    if not legacy:
        from valorant_translator.translation.enhancement import GameEnhancer
        enhancer = GameEnhancer("valorant", glossary)
        settings = Settings()
    cases = (
        ("Smoke heaven.", ""), ("My Ultimus, Ready!", "我的最后，准备好了！"),
        ("Watch your eyes!", "看你的眼睛！"), ("Watch your eyes! Come on, let's go!", "看你的眼睛！快走。"),
        ("We should try another plan for the next round.", "我们下回合应该试试别的计划。"),
        ("One went down, Cypher.", "一个下去了，西弗。"), ("Two B long, one is low.", "B 长两个，另一个血少。"),
    )
    timings = []
    start_cpu = time.process_time()
    for _ in range(repeat):
        for text, sample_mt in cases:
            start = time.perf_counter_ns()
            result = glossary.callout(text) if legacy else enhancer.match(text, "en", "zh", settings).translation
            if not result or legacy:
                glossary.postprocess(result or sample_mt, text)
            timings.append((time.perf_counter_ns() - start) / 1e6)
    return {"milliseconds": quantiles(timings), "process_cpu_seconds": time.process_time() - start_cpu}


def load_audio(path):
    with wave.open(str(path), "rb") as wav:
        if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (16000, 1, 2):
            raise ValueError(f"{path} must be mono 16 kHz 16-bit PCM")
        return np.frombuffer(wav.readframes(wav.getnframes()), "<i2").astype(np.float32) / 32768


def run_speech(args):
    from valorant_translator.audio.segmenter import SpeechChunk
    from valorant_translator.config import Settings
    from valorant_translator import pipeline
    from valorant_translator.recognition import whisper
    from valorant_translator.translation import m2m

    whisper.MODEL_DIR = args.model_dir.resolve()
    m2m.M2M_DIR = whisper.MODEL_DIR / "m2m100_418M"
    # Capture is replaced by reproducible manual input. All inference and queues are real.
    class NoLiveCapture:
        def __init__(self, *_):
            pass
        def start(self):
            pass
        def stop(self):
            pass

    rows, traces, statuses, displayed = {}, [], [], {}
    notes = []
    terminal = threading.Event()

    def result(subtitle):
        displayed[subtitle.task_id] = time.monotonic()
        rows[subtitle.task_id] = {
            "asr": subtitle.original, "final": subtitle.chinese, "language": subtitle.language,
            "asr_seconds": subtitle.asr_seconds, "translation_seconds": subtitle.translation_seconds,
            "audio_end_to_display_seconds": time.monotonic() - subtitle.audio_end_time,
            "corrected": getattr(subtitle, "corrected_original", None),
            "raw_model_translation": getattr(subtitle, "machine_translation", None),
        }
        if hasattr(pipe, "diagnostics"):
            pipe.diagnostics.add("字幕已显示")
        terminal.set()

    def trace(event):
        traces.append(event)
        if event.discarded:
            rows[event.task_id] = {"discarded": event.detail}
            terminal.set()

    pipeline.AudioCapture = NoLiveCapture
    settings = Settings(device_id="benchmark", compute_device=args.device, whisper_model=args.model,
                        fast_mode=not args.accurate)
    pipe = pipeline.Pipeline(settings, statuses.append, lambda _: None, result, notes.append, on_trace=trace)
    items = json.loads(args.manifest.read_text(encoding="utf-8"))
    pipe.start()
    try:
        deadline = time.monotonic() + 120
        while pipe.model_session is None and time.monotonic() < deadline and not pipe.stop_event.is_set():
            time.sleep(.03)
        if pipe.model_session is None:
            raise RuntimeError(statuses[-1] if statuses else "Model initialization failed")
        actual = [model.device for model in pipe.model_session]
        if args.device == "cuda" and actual != ["cuda", "cuda"]:
            raise RuntimeError(f"CUDA requested; real runtime is {actual}")
        if args.no_hotwords:
            pipe.model_session[0].glossary.hotwords = ""

        if args.match_wav:
            return replay_match(pipe, load_audio(args.match_wav), items, rows, displayed, actual, traces, notes)
        output = []
        for item in items:
            audio = load_audio(args.manifest.parent / item["file"])
            started = time.monotonic()
            if item.get("direction") == "reverse":
                if hasattr(pipe, "translate_microphone_details"):
                    recognized, english, detail = pipe.translate_microphone_details(audio)
                else:
                    recognized, english = pipe.translate_microphone(audio)
                    detail = {}
                row = {"asr": recognized.text if recognized else "", "final": english,
                       "audio_end_to_display_seconds": time.monotonic() - started, **detail}
            else:
                terminal.clear()
                pipe.enqueue(SpeechChunk(audio, started - len(audio) / 16000, started))
                if not terminal.wait(15):
                    raise RuntimeError("No terminal result for " + item["file"])
                task = next(event.task_id for event in reversed(traces) if event.stage == "VAD 分段完成")
                row = rows[task]
            output.append({**item, **row})
        return {"actual_devices": actual, "rows": output,
                "end_to_end_seconds": quantiles([row["audio_end_to_display_seconds"]
                                                for row in output if row.get("final")]),
                "diagnostics": pipe.diagnostics.snapshot() if hasattr(pipe, "diagnostics") else None}
    finally:
        pipe.stop()



def edit_distance(reference, actual):
    previous = list(range(len(actual) + 1))
    for i, reference_word in enumerate(reference, 1):
        current = [i]
        for j, word in enumerate(actual, 1):
            current.append(min(previous[j] + 1, current[-1] + 1, previous[j - 1] + (word != reference_word)))
        previous = current
    return previous[-1]


def replay_match(pipe, audio, reference, rows, displayed, actual, traces, notes):
    from collections import Counter

    limit = len(audio) / 16000
    for item in reference:
        if not (0 <= item["start"] < item["end"] <= limit
                and item["kind"] in ("teammate", "hero", "effects")
                and (item["kind"] != "teammate" or item["text"].strip())):
            raise ValueError("Invalid match annotation")
    chunks = []
    origin = time.monotonic()

    def record_chunk(chunk):
        chunks.append((max(0, chunk.start_time - origin), chunk.end_time - origin))
        pipe.enqueue(chunk)

    pipe.segmenter.on_chunk = record_chunk
    if hasattr(pipe, "diagnostics"):
        real_stats = pipe.diagnostics
    else:
        real_stats = None
    for start in range(0, len(audio), 1600):
        frame = audio[start:start + 1600]
        end = origin + (start + len(frame)) / 16000
        time.sleep(max(0, end - time.monotonic()))
        pipe.segmenter.feed(frame, end)
    pipe.segmenter.flush(origin + limit)
    deadline = time.monotonic() + 15
    while len(rows) < len(chunks) and time.monotonic() < deadline:
        time.sleep(.03)

    assignments = {}
    candidates = []
    for task_id, (start, end) in enumerate(chunks, 1):
        for index, item in enumerate(reference):
            overlap = min(end, item["end"]) - max(start, item["start"])
            if overlap > 0:
                candidates.append((overlap, task_id, index))
    visited = set()
    for _, task, index in sorted(candidates, reverse=True):
        if task not in visited and index not in assignments:
            assignments[index] = task
            visited.add(task)

    result = []
    teammate_latencies = []
    for index, item in enumerate(reference):
        task = assignments.get(index)
        row = rows.get(task, {})
        sample = audio[round(item["start"] * 16000):round(item["end"] * 16000)]
        frame_rms = [float(np.sqrt(np.mean(sample[i:i + 320] ** 2))) for i in range(0, len(sample), 320)]
        output = {**item, "task_id": task, "rms": quantiles(frame_rms), **row}
        if task in displayed:
            output["annotated_audio_end_to_display_seconds"] = displayed[task] - origin - item["end"]
            if item["kind"] == "teammate":
                teammate_latencies.append(output["annotated_audio_end_to_display_seconds"])
        result.append(output)

    teammates = [row for row in result if row["kind"] == "teammate"]
    english = [row for row in teammates if row.get("language_ref", "en") == "en"]
    # Missed utterances count as deletions, so lowering recall cannot reduce this WER.
    def tokens(text):
        return re.findall(r"[a-z0-9]+", text.lower())
    total_words = sum(len(tokens(item["text"])) for item in english)
    word_errors = sum(edit_distance(tokens(item["text"]), tokens(item.get("asr", ""))) for item in english)
    observed = Counter(event.detail.split("；", 1)[0] for event in traces if event.discarded)
    observed["VAD 语音过短"] = sum(note.startswith("检测到的语音过短") for note in notes)
    for diagnostic in traces:
        if diagnostic.stage in ("识别入队", "翻译入队"):
            match = re.search(r"队列约 (\d+) 句", diagnostic.detail)
            if match:
                key = diagnostic.stage[:2]
                observed[f"{key}队列峰值"] = max(observed[f"{key}队列峰值"], int(match.group(1)))

    # Unattributed subtitle output and effect-only matches may be inspected offline.
    unassigned = {task: row for task, row in rows.items() if task not in visited}
    return {"actual_devices": actual, "rows": result, "chunks": len(chunks),
            "teammate_references": len(teammates),
            "teammate_subtitles": sum(bool(item.get("final")) for item in teammates),
            "teammate_audio_end_to_display_seconds": quantiles(teammate_latencies),
            "teammate_english_wer": word_errors / total_words if total_words else None,
            "discard_and_queue_trace": dict(observed), "unassigned_results": unassigned,
            "diagnostics": real_stats.snapshot() if real_stats else None}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=30)
    parser.add_argument("--legacy", action="store_true", help="Run the matching path in a b5 checkout")
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--match-wav", type=Path, help="Replay an annotated match recording at real speed")
    parser.add_argument("--model", default="small", choices=("tiny", "base", "small", "medium"))
    parser.add_argument("--device", default="cpu", choices=("cpu", "cuda"))
    parser.add_argument("--accurate", action="store_true")
    parser.add_argument("--no-hotwords", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.match_wav is not None and args.manifest is None:
        parser.error("--match-wav requires timestamped --manifest")
    if args.repeat < 1:
        parser.error("--repeat must be positive")
    if args.manifest is not None and args.model_dir is None:
        parser.error("--manifest requires --model-dir")
    output = {"matching": match_benchmark(args.repeat, args.legacy)}
    if args.manifest:
        started = time.process_time()
        output["speech"] = run_speech(args)
        output["speech"]["process_cpu_seconds"] = time.process_time() - started
    if sys.platform != "win32":
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        output["peak_rss_bytes"] = peak if sys.platform == "darwin" else peak * 1024
    args.report.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
