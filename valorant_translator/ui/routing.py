"""Guide users through routing game chat via a Windows playback endpoint."""
from __future__ import annotations

from PySide6.QtCore import QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QProgressBar, QScrollArea, QVBoxLayout, QWidget

from valorant_translator.audio.capture import AudioCapture
from valorant_translator.ui.dialogs import apply_dialog_theme
from valorant_translator.ui.glass import GlassButton

SONAR_GUIDE = "https://support.steelseries.com/hc/en-us/articles/5785460374797-How-do-I-Setup-Sonar-to-use-Valorant-voice-chat-"
VB_CABLE = "https://vb-audio.com/Cable/"


def routing_devices(devices):
    return [device for device in devices if device.loopback and any(
        marker in device.name.casefold() for marker in ("sonar - chat", "sonar – chat", "sonar chat", "cable input",
                                                         "vb-audio virtual cable")
    )]


class RoutingWizard(QDialog):
    level = Signal(float)
    capture_status = Signal(str)

    def __init__(self, window):
        super().__init__(window)
        self.setWindowTitle("语音分流设置")
        apply_dialog_theme(self, window.settings)
        self.resize(595, 470)
        layout = QVBoxLayout(self)
        self.state = QLabel()
        layout.addWidget(self.state)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body.setObjectName("pageBody")
        steps = QVBoxLayout(body)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)
        for title, content in (
            ("1  准备虚拟声音设备", "任选 SteelSeries Sonar Chat 或 VB-CABLE。装好后点击刷新。"),
            ("2  在 VALORANT 设置语音输出", "Audio → Voice Chat → Output Device。选择 Sonar Chat 或 CABLE Input。"
             "游戏的主输出保持原样。Sonar 将 Game 与 Chat 输出到耳机。"
             "使用 VB-CABLE 时，在 Windows 声音控制面板的录制设备中打开 CABLE Output → 属性 → 侦听，"
             "选择原来的耳机或扬声器，避免听不到队友。Discord 可在语音设置中选择同一个输出。"),
            ("3  选择同一个系统声音", "先确认耳机还能听到队友和游戏，再把下方设备设为翻译器声音来源。"),
            ("4  分开测游戏和队友语音", "先在没有队友说话时播放游戏音效，再请队友说话。"
             "观察两次采样，确认队友语音有信号且单独游戏音效无信号后再确认。"),
        ):
            heading = QLabel(title)
            heading.setObjectName("translation")
            label = QLabel(content)
            label.setWordWrap(True)
            steps.addWidget(heading)
            steps.addWidget(label)
            if title.startswith("1"):
                links = QHBoxLayout()
                for label, url in (("Sonar 官方说明", SONAR_GUIDE), ("VB-CABLE 官方下载", VB_CABLE)):
                    button = GlassButton(label)
                    button.clicked.connect(lambda checked=False, link=url: QDesktopServices.openUrl(QUrl(link)))
                    links.addWidget(button)
                self.refresh_button = GlassButton("刷新设备", icon="refresh")
                self.refresh_button.clicked.connect(window._request_devices)
                links.addWidget(self.refresh_button)
                steps.addLayout(links)
            if title.startswith("3"):
                self.devices = QComboBox()
                self.devices.currentIndexChanged.connect(self._update_state)
                device_row = QHBoxLayout()
                device_row.addWidget(self.devices, 1)
                self.select_button = GlassButton("使用此声音来源")
                self.select_button.clicked.connect(self._select)
                device_row.addWidget(self.select_button)
                steps.addLayout(device_row)

        steps.addStretch()

        samples = QHBoxLayout()
        self.game_sample = GlassButton("采样游戏音效")
        self.voice_sample = GlassButton("采样队友语音")
        self.verify_button = GlassButton("我已确认分流")
        self.game_sample.clicked.connect(lambda: self._sample("游戏音效"))
        self.voice_sample.clicked.connect(lambda: self._sample("队友语音"))
        self.verify_button.clicked.connect(self._verify)
        for button in (self.game_sample, self.voice_sample, self.verify_button):
            samples.addWidget(button)
        layout.addLayout(samples)
        self.meter = QProgressBar()
        self.meter.setRange(0, 100)
        self.meter.hide()
        layout.addWidget(self.meter)
        self.sample_status = QLabel()
        self.sample_status.setWordWrap(True)
        layout.addWidget(self.sample_status)
        close = GlassButton("完成或跳过")
        close.clicked.connect(self.accept)
        layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)

        self._capture = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._stop)
        self._peaks = {}
        self.level.connect(self._on_level)
        self.capture_status.connect(self._capture_status)
        window.devices_refreshed.connect(self.refresh)
        self.refresh(True)

    def refresh(self, success=True):
        if not success:
            self.sample_status.setText("设备刷新失败，请检查音频驱动。")
            return
        self.devices.blockSignals(True)
        self.devices.clear()
        for device in routing_devices(self.parentWidget().audio_devices):
            self.devices.addItem(device.name, device.id)
        index = self.devices.findData(self.parentWidget().settings.device_id)
        self.devices.setCurrentIndex(max(0, index))
        self.devices.blockSignals(False)
        self._update_state()

    def _update_state(self):
        if not hasattr(self, "game_sample"):
            return
        device_id = self.devices.currentData()
        selected = bool(device_id and device_id == self.parentWidget().settings.device_id)
        verified = selected and device_id == self.parentWidget().settings.routing_verified_id
        running = self._capture is not None
        self.devices.setEnabled(not running)
        self.refresh_button.setEnabled(not running)
        self.select_button.setEnabled(bool(device_id) and not running)
        for button in (self.game_sample, self.voice_sample):
            button.setEnabled(selected and not running)
        game = self._peaks.get((device_id, "游戏音效"))
        voice = self._peaks.get((device_id, "队友语音"))
        self.verify_button.setEnabled(selected and not running and game is not None
                                      and voice is not None and voice > max(game, 0.0005))
        if not device_id:
            self.state.setText("未检测到 Sonar Chat 或 VB-CABLE 播放端点")
        elif verified:
            self.state.setText(f"上次已确认分流 · {self.devices.currentText()}")
        elif selected:
            self.state.setText(f"已选择 · {self.devices.currentText()}（待验证）")
        else:
            self.state.setText(f"检测到 {self.devices.currentText()}，尚未设为当前声音来源")

    def _select(self):
        index = self.parentWidget().device_combo.findData(self.devices.currentData())
        if index >= 0:
            self.parentWidget().device_combo.setCurrentIndex(index)
        self._update_state()

    def _sample(self, category):
        device_id = self.devices.currentData()
        if not device_id or self._capture is not None:
            return
        self._sample_key = device_id, category
        self._peaks.pop(self._sample_key, None)
        self._peak = 0.0
        self._frames = 0
        self._error = ""
        self.meter.setValue(0)
        self.meter.show()
        self.sample_status.setText(f"正在采样 {category}，请在接下来的 5 秒播放对应声音。")
        self._capture = AudioCapture(device_id, lambda *_: None, self.level.emit, self.capture_status.emit)
        try:
            self._capture.start()
        except (OSError, RuntimeError) as exc:
            self._error = str(exc)
            self._stop()
            return
        self._timer.start(5000)
        self._update_state()

    def _capture_status(self, message):
        if self._capture is not None and ("失败" in message or "断开" in message):
            self._error = message

    def _on_level(self, level):
        if self._capture is not None:
            self._frames += 1
            self._peak = max(level, self._peak)
            self.meter.setValue(round(level * 100))

    def _stop(self):
        self._timer.stop()
        capture, self._capture = self._capture, None
        if capture:
            capture.stop()
        if not self._error and self._frames:
            self._peaks[self._sample_key] = self._peak
        readings = [f"{title}音量峰值 {self._peaks[(self._sample_key[0], title)]:.1%}"
                    for title in ("游戏音效", "队友语音") if (self._sample_key[0], title) in self._peaks]
        self.sample_status.setText(self._error or " · ".join(readings) or "未收到采样帧，请检查设备后重试。")
        self._update_state()

    def _verify(self):
        if self.verify_button.isEnabled():
            self.parentWidget().settings.routing_verified_id = self.devices.currentData()
            self.parentWidget().settings.save()
            self.parentWidget()._update_device_hint()
            self._update_state()

    def done(self, result):
        if self._capture is not None:
            self._stop()
        super().done(result)
