"""Hold-to-talk microphone capture. Audio stays in memory and is never saved."""
from __future__ import annotations
from valorant_translator.ui.texts import tr

import threading
import time
from dataclasses import replace

import numpy as np

from valorant_translator.audio.capture import SAMPLE_RATE, AudioCapture
from valorant_translator.models.providers import default_model_manager
from valorant_translator.translation.enhancement import GameEnhancer
from valorant_translator.translation.glossary import game_glossary

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
            self.status(tr("talk.0001"))
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
            if tr("talk.0002") in message or tr("talk.0003") in message:
                self._capture_error = message
                self.status(message)
            elif message.startswith(tr("talk.0004")):
                self._capture_error = ""

        self.status(tr("talk.0005"))
        try:
            self.capture = self.capture_factory(settings.microphone_id, frame, lambda _: None, capture_status)
            self.capture.start()
        except Exception as exc:
            self.capture = None
            self._busy = False
            self.status(tr("talk.0006", p0=exc))
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
                    self.status(tr("talk.0007"))
                    return
                if not self._frames:
                    self.status(tr("talk.0008"))
                    return
                if self._samples < MIN_SAMPLES:
                    self.status(tr("talk.0009", p0=self._samples / SAMPLE_RATE, p1=MIN_SAMPLES / SAMPLE_RATE))
                    return
                audio = np.concatenate(self._frames)
                self.status(tr("talk.0010"))
                started = time.monotonic()
                if self._diagnostic_session:
                    recognized, english, evidence = self._diagnostic_session(audio, rejected)
                elif self._session:
                    recognized, english = self._session(audio)
                    evidence = {}
                else:
                    settings = self._settings
                    models = default_model_manager()
                    asr = models.create_asr(settings)
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
                            translator = models.create_translation(settings)
                            english, raw, fallback_rule = translator.translate_to_english_details(
                                glossary.prepare_zh_en(match.text))
                            english = glossary.finish_zh_en(english, match.text)
                            rule = " · ".join(filter(None, (match.rules, fallback_rule)))
                    evidence = dict(asr_seconds=asr_seconds,
                                    translation_seconds=time.monotonic() - started - asr_seconds,
                                    machine_translation=raw, glossary_rules=rule, corrected_original=corrected,
                                    confidence=recognized.probability if recognized else None)
                if self._closed:
                    return
                if recognized and english:
                    self.result((recognized.text, english))
                    if self.details:
                        self.details((recognized.text, english, self._settings.game, audio_end, evidence))
                elif recognized:
                    self.status(tr("talk.0011"))
                elif rejection:
                    self.status(tr("talk.0012", p0=rejection[0], p1=rejection[1]))
                else:
                    self.status(tr("talk.0013"))
            except Exception as exc:
                if not self._closed:
                    self.status(tr("talk.0014", p0=exc))
            finally:
                self._frames.clear()
                self._busy = False

        threading.Thread(target=transcribe, name="push-to-talk", daemon=True).start()

    def close(self):
        self._closed = True
        self.end()
