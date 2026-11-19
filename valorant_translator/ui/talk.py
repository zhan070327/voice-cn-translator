"""Hold-to-talk microphone capture. Audio stays in memory and is never saved."""
from __future__ import annotations

import threading
from dataclasses import replace

import numpy as np

from valorant_translator.audio.capture import SAMPLE_RATE, AudioCapture
from valorant_translator.recognition.whisper import LocalWhisper
from valorant_translator.translation.m2m import LocalM2M

MAX_SECONDS = 20


class PushToTalk:
    def __init__(self, status, result, capture=AudioCapture):
        self.status = status
        self.result = result
        self.capture_factory = capture
        self.capture = None
        self._busy = False
        self._closed = False

    def begin(self, settings, session=None):
        if self._busy or self._closed:
            return False
        if not settings.microphone_id:
            self.status("请先连接麦克风并在详细设置中选择。")
            return False
        self._busy = True
        self._frames = []
        self._samples = 0
        self._settings = replace(settings)
        self._session = session

        def frame(audio, _):
            remaining = MAX_SECONDS * SAMPLE_RATE - self._samples
            if remaining > 0:
                self._frames.append(audio[:remaining].copy())
                self._samples += min(remaining, len(audio))

        try:
            self.capture = self.capture_factory(settings.microphone_id, frame, lambda _: None, self.status)
            self.capture.start()
        except Exception as exc:
            self.capture = None
            self._busy = False
            self.status(f"麦克风无法启动：{exc}")
            return False
        self.status("正在收听你的麦克风，松开后翻译。")
        return True

    def end(self):
        capture, self.capture = self.capture, None
        if capture is None:
            return

        def transcribe():
            try:
                capture.stop()
                if self._closed:
                    return
                if not self._frames or self._samples < SAMPLE_RATE // 4:
                    self.status("未录到足够语音。请按住快捷键后说话。")
                    return
                audio = np.concatenate(self._frames)
                self.status("正在将中文语音翻译成英文…")
                if self._session:
                    recognized, english = self._session(audio)
                else:
                    settings = self._settings
                    asr = LocalWhisper(settings.whisper_model, settings.compute_device, settings.fast_mode)
                    recognized = asr.transcribe(audio, "zh", use_hotwords=False)
                    english = (LocalM2M(settings.compute_device, settings.fast_mode)
                               .translate_to_english(recognized.text)) if recognized else ""
                if recognized and english and not self._closed:
                    self.result((recognized.text, english))
                elif not self._closed:
                    self.status("没有识别到可靠的中文语音，请重试。")
            except Exception as exc:
                if not self._closed:
                    self.status(f"英文翻译失败：{exc}")
            finally:
                self._frames.clear()
                self._busy = False

        threading.Thread(target=transcribe, name="push-to-talk", daemon=True).start()

    def close(self):
        self._closed = True
        self.end()
