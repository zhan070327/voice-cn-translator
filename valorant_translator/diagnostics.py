"""Bounded, text-free counters for playback and real-match diagnostics."""
from __future__ import annotations

from collections import Counter
from threading import Lock


class Diagnostics:
    def __init__(self):
        self._lock = Lock()
        self._counts = Counter()
        self._drops = Counter()
        self._queues = {}

    def add(self, stage: str, reason: str = "") -> int:
        with self._lock:
            target, key = (self._drops, reason) if stage == "discard" else (self._counts, stage)
            target[key] += 1
            return target[key]

    def depth(self, name: str, size: int) -> None:
        with self._lock:
            previous = self._queues.get(name, (0, 0))
            self._queues[name] = (size, max(size, previous[1]))

    def snapshot(self) -> dict:
        with self._lock:
            counts, drops, queues = dict(self._counts), dict(self._drops), dict(self._queues)
        return {"counts": counts, "drops": drops, "queues": queues}

    def summary(self) -> str:
        data = self.snapshot()
        counts = data["counts"]
        total = counts.get("语音片段", 0)
        shown = counts.get("字幕已显示", 0)
        lines = [f"语音片段 {total}，字幕已显示 {shown}"]
        lines.extend(f"{key} {count} 次" + (f"（占片段 {count / total:.1%}）" if total else "")
                     for key, count in sorted(data["drops"].items()))
        lines.extend(f"{key}当前 {depth}，峰值 {peak}" for key, (depth, peak) in data["queues"].items())
        return "\n".join(lines)
