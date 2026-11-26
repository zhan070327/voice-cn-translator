"""Verify the two app audio inputs without changing playback or OS defaults."""
from __future__ import annotations

from threading import Event, Thread

import numpy as np
from PySide6.QtCore import QSignalBlocker, QTimer, Qt, Signal
from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QProgressBar, QVBoxLayout

from valorant_translator.audio.capture import AudioCapture
from valorant_translator.audio.segmenter import SpeechSegmenter
from valorant_translator.ui.dialogs import apply_dialog_theme
from valorant_translator.ui.glass import GAP, GUTTER, INLINE, GlassButton
from valorant_translator.ui.texts import tr

_VIRTUAL_NAMES = ("cable", "virtual", "sonar", "voicemeeter", "blackhole", "loopback", "soundflower", "fxsound")
_ROUTED_NAMES = ("cable output", "cable input", "sonar - chat", "sonar – chat", "sonar chat", "vb-audio virtual cable")
_SAMPLE_MS = 4500
_AUDIBLE_RMS = 0.004


def _is_microphone(device):
    return not device.loopback and not any(name in device.name.casefold() for name in _VIRTUAL_NAMES)


def is_routed_source(device):
    return any(name in device.name.casefold() for name in _ROUTED_NAMES)


class MicrophoneWizard(QDialog):
    enum_finished = Signal(object, object, str)
    sample_frames = Signal(object, object, float)
    sample_error = Signal(object, str)
    capture_stopped = Signal()

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.devices = list(window.audio_devices)
        self.step = 0
        self._request = None
        self._capture = None
        self._active = None
        self._token = None
        self._stopping = False
        self._segmenter = None
        self._has_frames = False
        self._heard_sound = False
        self._heard_speech = False
        self._mic_verified = False
        self._voice_verified = False
        self._hidden_overlay = None

        self.setWindowTitle(tr("mic_setup.title"))
        self.setMinimumWidth(560)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        apply_dialog_theme(self, window.settings)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(GAP, GAP, GAP, GAP)
        layout.setSpacing(INLINE)
        self.progress = QLabel()
        self.progress.setObjectName("statLabel")
        layout.addWidget(self.progress)
        self.heading = QLabel()
        self.heading.setObjectName("title")
        layout.addWidget(self.heading)
        self.instructions = QLabel()
        self.instructions.setWordWrap(True)
        layout.addWidget(self.instructions)

        self.mic = QComboBox()
        self.source = QComboBox()
        self.mic.setAccessibleName(tr("mic_setup.step_0_title"))
        self.source.setAccessibleName(tr("mic_setup.step_2_title"))
        for picker in (self.mic, self.source):
            layout.addWidget(picker)
        self.sample_button = GlassButton(tr("mic_setup.sample"), icon="mic")
        self.sample_button.clicked.connect(self._start_sample)
        layout.addWidget(self.sample_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.meter = QProgressBar()
        self.meter.setRange(0, 100)
        self.meter.setTextVisible(False)
        self.meter.setAccessibleName(tr("mic_setup.meter"))
        layout.addWidget(self.meter)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.confirm_button = GlassButton(tr("mic_setup.confirm_voice"))
        self.confirm_button.clicked.connect(self._confirm)
        layout.addWidget(self.confirm_button, alignment=Qt.AlignmentFlag.AlignLeft)

        controls = QHBoxLayout()
        self.cancel_button = GlassButton(tr("mic_setup.cancel"))
        self.cancel_button.clicked.connect(self.reject)
        controls.addWidget(self.cancel_button)
        self.refresh_button = GlassButton(tr("app.0152"), icon="refresh")
        self.refresh_button.clicked.connect(self._refresh_devices)
        controls.addWidget(self.refresh_button)
        controls.addStretch()
        self.back_button = GlassButton(tr("mic_setup.back"))
        self.back_button.clicked.connect(lambda: self._change_step(-1))
        controls.addWidget(self.back_button)
        self.next_button = GlassButton(tr("mic_setup.next"), icon="next")
        self.next_button.clicked.connect(self._next)
        controls.addWidget(self.next_button)
        layout.addSpacing(GUTTER)
        layout.addLayout(controls)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._finish_sample)
        self.enum_finished.connect(self._apply_devices, Qt.ConnectionType.QueuedConnection)
        self.sample_frames.connect(self._receive_frames, Qt.ConnectionType.QueuedConnection)
        self.sample_error.connect(self._failed, Qt.ConnectionType.QueuedConnection)
        self.capture_stopped.connect(self._capture_has_stopped, Qt.ConnectionType.QueuedConnection)
        self._fill_pickers(window.settings.microphone_id, window.settings.device_id)
        self.mic.currentIndexChanged.connect(self._selection_changed)
        self.source.currentIndexChanged.connect(self._selection_changed)
        self._render_step()

    @staticmethod
    def _emit(signal, *args):
        try:
            signal.emit(*args)
        except RuntimeError:
            # The dialog may close before the capture or device worker exits.
            pass

    def showEvent(self, event):
        overlay = self.window.overlay
        if overlay.isVisible() and not overlay.settings.click_through:
            self._hidden_overlay = overlay
            overlay.hide()
        super().showEvent(event)

    def hideEvent(self, event):
        overlay, self._hidden_overlay = self._hidden_overlay, None
        if overlay is not None and overlay is self.window.overlay:
            overlay.show()
        super().hideEvent(event)

    def _fill_pickers(self, mic_id, source_id):
        candidates = [device for device in self.devices
                      if device.loopback or is_routed_source(device) or _is_microphone(device)]
        candidates.sort(key=lambda device: (not is_routed_source(device), not device.loopback))
        with QSignalBlocker(self.mic), QSignalBlocker(self.source):
            self.mic.clear()
            self.source.clear()
            for device in self.devices:
                if _is_microphone(device):
                    self.mic.addItem(device.name, device.id)
            for device in candidates:
                self.source.addItem(device.name, device.id)
            self.mic.setCurrentIndex(max(0, self.mic.findData(mic_id)))
            self.source.setCurrentIndex(max(0, self.source.findData(source_id)))

    def _selection_changed(self):
        if self.sender() is self.mic:
            self._mic_verified = False
        else:
            self._voice_verified = False
        self._render_step()

    def _refresh_devices(self):
        request = self._request = object()
        self._set_status(tr("routing.0023"))
        self._update_actions()
        notify = self.enum_finished
        enumerate_devices = self.window._enumerate_audio_devices

        def refresh():
            try:
                devices = enumerate_devices()
            except Exception as exc:
                self._emit(notify, request, None, str(exc) or type(exc).__name__)
            else:
                self._emit(notify, request, devices, "")

        Thread(target=refresh, name="mic-setup-enumerate", daemon=True).start()

    def _apply_devices(self, request, devices, error):
        if request is not self._request:
            return
        self._request = None
        if error:
            self._set_status(tr("mic_setup.unavailable", detail=error))
            self._update_actions()
            return
        mic_id, source_id = self.mic.currentData(), self.source.currentData()
        self.devices = list(devices)
        self._fill_pickers(mic_id, source_id)
        if mic_id != self.mic.currentData():
            self._mic_verified = False
        if source_id != self.source.currentData():
            self._voice_verified = False
        self._render_step()

    def _render_step(self):
        self.progress.setText(tr("onboarding.step_progress", p0=self.step + 1, p1=4))
        self.heading.setText(tr(f"mic_setup.step_{self.step}_title"))
        self.instructions.setText(tr(f"mic_setup.step_{self.step}_help"))
        self.mic.setVisible(self.step == 0)
        self.source.setVisible(self.step == 2)
        testing = self.step in (1, 3)
        self.sample_button.setVisible(testing)
        self.meter.setVisible(testing)
        self.meter.setValue(0)
        self.confirm_button.hide()
        if self.step <= 1 and not self.mic.count():
            self._set_status(tr("mic_setup.no_microphone"))
        elif self.step >= 2 and not self.source.count():
            self._set_status(tr("mic_setup.no_source"))
        elif self.step == 1 and self._mic_verified:
            self._set_status(tr("mic_setup.mic_heard"))
        elif self.step == 3 and self._voice_verified:
            self._set_status(tr("mic_setup.teammate_heard"))
        else:
            self._set_status("")
        self._update_actions()

    def _update_actions(self):
        busy = self._capture is not None or self._stopping or self._request is not None
        self.refresh_button.setEnabled(not busy)
        self.back_button.setEnabled(bool(self.step) and not busy)
        self.sample_button.setEnabled(not busy and bool((self.mic if self.step == 1 else self.source).currentData()))
        self.confirm_button.setEnabled(not busy)
        choices = (bool(self.mic.currentData()), self._mic_verified, bool(self.source.currentData()),
                   self._mic_verified and self._voice_verified)
        self.next_button.setText(tr("mic_setup.finish" if self.step == 3 else "mic_setup.next"))
        self.next_button.setEnabled(not busy and choices[self.step])

    def _set_status(self, message):
        self.status.setText(message)
        self.status.setVisible(bool(message))

    def _change_step(self, amount):
        self.step += amount
        self._render_step()

    def _next(self):
        if self.step < 3:
            self._change_step(1)
            return
        if not self._mic_verified or not self._voice_verified:
            return
        if self.window._model_moving:
            self._set_status(tr("app.0323"))
            return
        mic_id, source_id = self.mic.currentData(), self.source.currentData()
        source = next(device for device in self.devices if source_id == device.id)
        try:
            self.window._save_audio_setup(self.devices, mic_id, source)
        except OSError as exc:
            self._set_status(tr("mic_setup.save_error", detail=exc))
            return
        self.accept()

    def _start_sample(self):
        if not self.sample_button.isEnabled():
            return
        mic = self.step == 1
        device_id = (self.mic if mic else self.source).currentData()
        if mic:
            self._mic_verified = False
        else:
            self._voice_verified = False
        self._has_frames = self._heard_sound = self._heard_speech = False
        self._segmenter = None if mic else SpeechSegmenter(
            self._mark_speech, self.window.settings.vad_level)
        self.confirm_button.hide()
        self.meter.setValue(0)
        token = self._token = object()
        active = self._active = Event()
        active.set()
        frame_signal = self.sample_frames
        error_signal = self.sample_error
        connected_prefix = tr("routing.0001")

        def frames(audio, timestamp):
            if active.is_set():
                self._emit(frame_signal, token, audio, timestamp)

        def status(message):
            if active.is_set() and not message.startswith(connected_prefix):
                self._emit(error_signal, token, message)

        self._set_status(tr("mic_setup.sampling"))
        try:
            self._capture = AudioCapture(device_id, frames, lambda _: None, status)
            self._capture.start()
        except Exception as exc:
            self._failed(token, str(exc) or type(exc).__name__)
            return
        self._timer.start(_SAMPLE_MS)
        self._update_actions()

    def _receive_frames(self, token, audio, timestamp):
        if token is not self._token or not len(audio):
            return
        rms = float(np.sqrt(np.mean(audio * audio)))
        if not np.isfinite(rms):
            return
        self._has_frames = True
        self._heard_sound |= rms >= _AUDIBLE_RMS
        self.meter.setValue(round(min(1.0, rms * 8) * 100))
        if self._segmenter is not None:
            self._segmenter.feed(audio, timestamp)

    def _mark_speech(self, _):
        self._heard_speech = True

    def _finish_sample(self):
        if self._token is None:
            return
        if self._segmenter is not None:
            self._segmenter.flush()
        self._stop_capture()
        if not self._has_frames:
            message = tr("mic_setup.no_frames")
        elif self.step == 1:
            self._mic_verified = self._heard_sound
            message = tr("mic_setup.mic_heard" if self._heard_sound else "mic_setup.mic_silent")
        elif self._heard_speech:
            message = tr("mic_setup.confirm_prompt")
            self.confirm_button.show()
        else:
            message = tr("mic_setup.teammate_silent")
        self._set_status(message)
        self._update_actions()

    def _failed(self, token, detail):
        if token != self._token:
            return
        self._stop_capture()
        self.meter.setValue(0)
        self._set_status(tr("mic_setup.unavailable", detail=detail))
        self._update_actions()

    def _confirm(self):
        if self.step == 3 and self._heard_speech and self.confirm_button.isVisible():
            self._voice_verified = True
            self.confirm_button.hide()
            self._set_status(tr("mic_setup.teammate_heard"))
            self._update_actions()

    def _stop_capture(self):
        self._timer.stop()
        self._token = None
        self._segmenter = None
        if self._active is not None:
            self._active.clear()
            self._active = None
        if self._capture is None:
            return
        capture, self._capture = self._capture, None
        self._stopping = True
        stopped = self.capture_stopped

        def stop():
            try:
                capture.stop()
            finally:
                self._emit(stopped)

        Thread(target=stop, name="mic-setup-stop", daemon=True).start()

    def _capture_has_stopped(self):
        self._stopping = False
        self._update_actions()

    def done(self, result):
        self._request = None
        self._stop_capture()
        super().done(result)
