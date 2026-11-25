from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

import numpy as np
import webrtcvad


@dataclass
class SpeechChunk:
    audio: np.ndarray
    start_time: float
    end_time: float
    task_id: int = 0
    queued_at: float = 0.0


class SpeechSegmenter:
    """20 ms VAD frames with 300 ms pre-roll and a bounded speech tail."""

    MAX_FRAMES = 150  # 3 seconds; bound Whisper work even under continuous noise.

    def __init__(self, on_chunk: Callable[[SpeechChunk], None], level: int = 2,
                 on_event: Callable[[str], None] | None = None):
        self.on_chunk = on_chunk
        self.on_event = on_event or (lambda _: None)
        self.vad = webrtcvad.Vad(level)
        self.pending = np.empty(0, dtype=np.float32)
        self.frames: list[np.ndarray] = []
        self.preroll: deque[np.ndarray] = deque(maxlen=15)
        self.active = False
        self.speech_frames = 0
        self.silence_frames = 0
        self.started = 0.0
        self.last_voice = 0.0

    def feed(self, audio: np.ndarray, captured_at: float) -> None:
        self.pending = np.concatenate((self.pending, audio))
        while len(self.pending) >= 320:
            frame, self.pending = self.pending[:320], self.pending[320:]
            frame_time = captured_at - len(self.pending) / 16000
            pcm = (np.clip(frame, -1, 1) * 32767).astype("<i2").tobytes()
            voiced = self.vad.is_speech(pcm, 16000) and float(np.sqrt(np.mean(frame * frame))) > 0.004
            if voiced:
                if not self.active:
                    self.active = True
                    self.started = frame_time - len(self.preroll) * 0.02
                    self.frames.extend(self.preroll)
                    self.preroll.clear()
                    self.on_event("检测到可能的语音，等待一句话结束…")
                self.speech_frames += 1
                self.silence_frames = 0
                self.last_voice = frame_time
            elif self.active:
                self.silence_frames += 1
            if self.active:
                self.frames.append(frame.copy())
                if self.silence_frames >= 18:
                    self.flush(frame_time)
                elif len(self.frames) >= self.MAX_FRAMES:
                    tail = [part.copy() for part in self.frames[-10:]]
                    self.flush(frame_time)
                    if voiced:
                        self.active = True
                        self.frames = tail
                        self.started = frame_time - len(tail) * 0.02
                        self.speech_frames = len(tail)
                        self.last_voice = frame_time
            else:
                self.preroll.append(frame.copy())

    def flush(self, end_time: float | None = None) -> None:
        if self.active and self.speech_frames >= 5:
            chunk = SpeechChunk(np.concatenate(self.frames), self.started, end_time or time.monotonic())
            self.on_chunk(chunk)
        elif self.active:
            self.on_event(f"检测到的语音过短，已过滤（语音 {self.speech_frames * 0.02:.2f} 秒，至少 0.10 秒）")
        self.frames.clear()
        self.active = False
        self.speech_frames = 0
        self.silence_frames = 0
