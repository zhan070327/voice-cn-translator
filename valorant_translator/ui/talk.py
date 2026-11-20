"""Hold-to-talk microphone capture. Audio stays in memory and is never saved."""
from __future__ import annotations

import threading
import time
from dataclasses import replace

import numpy as np

from valorant_translator.audio.capture import SAMPLE_RATE, AudioCapture
from valorant_translator.recognition.whisper import LocalWhisper
from valorant_translator.translation.enhancement import GameEnhancer
from valorant_translator.translation.glossary import game_glossary
from valorant_translator.translation.m2m import LocalM2M

MAX_SECONDS = 20
MIN_SAMPLES = SAMPLE_RATE // 4
SILENT_RMS = 0.0005


class PushToTalk:
    def __init__(self, status, result, capture=AudioCapture, *, level=None, details=None, custom_rules=None):
        self.status = status
        self.result = result
        self.level = level or (lambda _: None)
        self.details = details
        self.custom_rules = custom_rules
        self.capture_factory = capture
        self.capture = None
        self._busy = False
        self._closed = False

    def begin(self, settings, session=None, *, diagnostic_session=None):
        if self._busy or self._closed:
            return False
        if not settings.microphone_id:
            self.status("未检测到麦克风。请连接后重试。")
            return False
        self._busy = True
        self._frames = []
        self._samples = 0
        self._peak = 0.0
        self._capture_error = ""
        self._settings = replace(settings)
        self._session = session
        self._diagnostic_session = diagnostic_session

        def frame(audio, _):
            remaining = MAX_SECONDS * SAMPLE_RATE - self._samples
            if remaining > 0:
                audio = audio[:remaining]
                rms = float(np.sqrt(np.mean(audio * audio))) if len(audio) else 0.0
                self._peak = max(self._peak, rms)
                self.level(rms)
                self._frames.append(audio.copy())
                self._samples += len(audio)

        def capture_status(message):
            if "失败" in message or "断开" in message:
                self._capture_error = message
                self.status(message)
            elif message.startswith("音频设备已连接"):
                self._capture_error = ""

        self.status("正在收听你的麦克风，松开后翻译。")
        try:
            self.capture = self.capture_factory(settings.microphone_id, frame, lambda _: None, capture_status)
            self.capture.start()
        except Exception as exc:
            self.capture = None
            self._busy = False
            self.status(f"麦克风无法启动：{exc}")
            return False
        return True

    def end(self):
        capture, self.capture = self.capture, None
        if capture is None:
            return
        audio_end = time.monotonic()

        def transcribe():
            rejection = None

            def rejected(reason, detail):
                nonlocal rejection
                rejection = reason, detail

            try:
                capture.stop()
                self.level(-1.0)
                if self._closed:
                    return
                if not self._frames and self._capture_error:
                    self.status(self._capture_error)
                    return
                if self._frames and self._peak < SILENT_RMS:
                    self.status("没录到声音：麦克风没有收到声音，请检查麦克风选择。")
                    return
                if not self._frames:
                    self.status("没录到声音：请按住快捷键，等待麦克风开启后说话。")
                    return
                if self._samples < MIN_SAMPLES:
                    self.status(f"语音太短：录到 {self._samples / SAMPLE_RATE:.2f} 秒，"
                                f"至少需要 {MIN_SAMPLES / SAMPLE_RATE:.2f} 秒。")
                    return
                audio = np.concatenate(self._frames)
                self.status("正在将中文语音翻译成英文…")
                started = time.monotonic()
                if self._diagnostic_session:
                    recognized, english, evidence = self._diagnostic_session(audio, rejected)
                elif self._session:
                    recognized, english = self._session(audio)
                    evidence = {}
                else:
                    settings = self._settings
                    asr = LocalWhisper(settings.whisper_model, settings.compute_device, settings.fast_mode)
                    asr.on_rejection = rejected
                    recognized = asr.transcribe_chinese_callout(audio)
                    asr_seconds = time.monotonic() - started
                    english, raw, rule, corrected = "", None, "", ""
                    if recognized:
                        glossary = game_glossary(settings.game)
                        match = GameEnhancer(settings.game, glossary, self.custom_rules).match(
                            recognized.text, "zh", "en", settings, filter_speech=False
                        )
                        corrected = match.text if match.text != recognized.text else ""
                        if match.translation:
                            english, rule = match.translation, match.rules
                        else:
                            translator = LocalM2M(settings.compute_device, settings.fast_mode)
                            english, raw, fallback_rule = translator.translate_to_english_details(match.text)
                            rule = " · ".join(filter(None, (match.rules, fallback_rule)))
                    evidence = dict(asr_seconds=asr_seconds,
                                    translation_seconds=time.monotonic() - started - asr_seconds,
                                    machine_translation=raw, glossary_rules=rule, corrected_original=corrected)
                if self._closed:
                    return
                if recognized and english:
                    self.result((recognized.text, english))
                    if self.details:
                        self.details((recognized.text, english, self._settings.game, audio_end, evidence))
                elif recognized:
                    self.status("已识别中文，但没有生成英文译文。")
                elif rejection:
                    self.status(f"中文识别未通过：{rejection[0]}。{rejection[1]}")
                else:
                    self.status("中文识别没有返回可用文字。")
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
