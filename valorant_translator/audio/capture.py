from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np

LOG = logging.getLogger(__name__)
SAMPLE_RATE = 16000
CAPTURE_RATE = 48000


@dataclass(frozen=True)
class AudioDevice:
    id: str
    name: str
    loopback: bool


def list_devices() -> list[AudioDevice]:
    import soundcard as sc

    result = []
    for mic in sc.all_microphones(include_loopback=True):
        result.append(AudioDevice(str(mic.id), str(mic.name), bool(mic.isloopback)))
    try:
        default = sc.default_microphone()
    except (AttributeError, OSError, RuntimeError):
        default = None
    if default is not None:
        result.sort(key=lambda device: device.loopback or device.id != str(default.id))
    return result


def preferred_microphone(devices, saved_id: str) -> AudioDevice | None:
    inputs = [device for device in devices if not device.loopback]
    return next((device for device in inputs if device.id == saved_id), inputs[0] if inputs else None)


def to_mono_16k(frames: np.ndarray, source_rate: int = CAPTURE_RATE) -> np.ndarray:
    frames = np.asarray(frames, dtype=np.float32)
    if frames.ndim == 2:
        frames = frames.mean(axis=1)
    if source_rate == SAMPLE_RATE:
        return frames
    from scipy.signal import resample_poly
    from math import gcd

    divisor = gcd(source_rate, SAMPLE_RATE)
    return resample_poly(frames, SAMPLE_RATE // divisor, source_rate // divisor).astype(np.float32)


class AudioCapture:
    """SoundCard uses WASAPI loopback on Windows; recording never mutes playback."""

    def __init__(self, device_id: str, on_audio: Callable[[np.ndarray, float], None],
                 on_level: Callable[[float], None], on_error: Callable[[str], None]):
        self.device_id = device_id
        self.on_audio = on_audio
        self.on_level = on_level
        self.on_error = on_error
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="audio-capture", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)

    def _run(self) -> None:
        import soundcard as sc

        while not self._stop.is_set():
            try:
                microphones = sc.all_microphones(include_loopback=True)
                mic = next((m for m in microphones if str(m.id) == self.device_id), None)
                if mic is None:
                    raise RuntimeError("音频设备已断开；等待设备重新连接")
                last_error = None
                for rate in (48000, 44100, 16000):
                    try:
                        block = rate // 10
                        with mic.recorder(samplerate=rate, blocksize=block) as recorder:
                            self.on_error("音频设备已连接，正在监听")
                            while not self._stop.is_set():
                                frames = recorder.record(numframes=block)
                                stamp = time.monotonic()
                                audio = to_mono_16k(frames, rate)
                                rms = float(np.sqrt(np.mean(audio * audio))) if len(audio) else 0.0
                                self.on_level(min(1.0, rms * 8))
                                self.on_audio(audio, stamp)
                        break
                    except Exception as exc:
                        last_error = exc
                        LOG.warning("Audio rate %s failed: %s", rate, exc)
                else:
                    raise RuntimeError(f"设备无法采集：{last_error}")
            except Exception as exc:
                LOG.warning("Audio capture failed: %s", exc)
                self.on_error(f"音频采集失败：{exc}")
                self._stop.wait(2.0)
