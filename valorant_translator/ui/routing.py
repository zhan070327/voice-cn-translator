"""Guide users through routing game chat via a Windows playback endpoint."""
from __future__ import annotations

from threading import Event, Thread

from PySide6.QtCore import QSignalBlocker, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QProgressBar, QScrollArea, QVBoxLayout, QWidget)

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


def probe_source(device_id: str) -> str:
    """Try the existing loopback capture before committing a device change."""
    ready = Event()
    results = []

    def status(message):
        if not ready.is_set():
            results.append("" if message.startswith("音频设备已连接") else message)
            ready.set()

    capture = AudioCapture(device_id, lambda *_: None, lambda *_: None, status)
    try:
        capture.start()
        if not ready.wait(5):
            return "等候设备响应超时。请检查设备是否已启用或被其他应用独占。"
        return results[0]
    finally:
        capture.stop()


class RoutingWizard(QDialog):
    level = Signal(float)
    capture_status = Signal(str)
    source_probed = Signal(object, str)

    def __init__(self, window):
        super().__init__(window)
        self.setWindowTitle("语音分流设置")
        apply_dialog_theme(self, window.settings)
        self.resize(625, 685)
        self._source_request = None
        self._hidden_overlay = None
        layout = QVBoxLayout(self)
        self.state = QLabel()
        self.state.setWordWrap(True)
        layout.addWidget(self.state)

        links = QHBoxLayout()
        for label, url in (("Sonar 官方说明", SONAR_GUIDE), ("VB-CABLE 官方下载", VB_CABLE)):
            button = GlassButton(label)
            button.clicked.connect(lambda checked=False, link=url: self._open_link(link))
            links.addWidget(button)
        self.refresh_button = GlassButton("刷新设备", icon="refresh")
        self.refresh_button.clicked.connect(self._refresh_devices)
        links.addWidget(self.refresh_button)
        layout.addLayout(links)

        self.devices = QListWidget()
        self.devices.setAccessibleName("可选虚拟声音设备")
        self.devices.setMaximumHeight(104)
        self.devices.currentItemChanged.connect(self._device_changed)
        layout.addWidget(self.devices)

        self.select_button = GlassButton("使用此声音来源")
        self.select_button.clicked.connect(self._select)
        layout.addWidget(self.select_button)
        self.action_status = QLabel()
        self.action_status.setWordWrap(True)
        self.action_status.setTextFormat(Qt.TextFormat.PlainText)
        self.action_status.hide()
        layout.addWidget(self.action_status)

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
            ("3  选择同一个系统声音", "先确认耳机还能听到队友和游戏，再从上方选设备并点击「使用此声音来源」。"),
            ("4  分开测游戏和队友语音", "先在没有队友说话时播放游戏音效，再请队友说话。"
             "观察两次采样，确认队友语音有信号且单独游戏音效无信号后再确认。"),
        ):
            heading = QLabel(title)
            heading.setObjectName("translation")
            label = QLabel(content)
            label.setWordWrap(True)
            steps.addWidget(heading)
            steps.addWidget(label)
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
        self.source_probed.connect(self._apply_source)
        window.devices_refreshed.connect(self.refresh)
        self.refresh(True)

    def showEvent(self, event):
        overlay = self.parentWidget().overlay
        if overlay.isVisible() and not overlay.settings.click_through:
            # An independent topmost overlay can otherwise eat clicks over the dialog on Windows.
            self._hidden_overlay = overlay
            overlay.hide()
        super().showEvent(event)

    def hideEvent(self, event):
        overlay, self._hidden_overlay = self._hidden_overlay, None
        if overlay is not None and self.parentWidget() and overlay is self.parentWidget().overlay:
            overlay.show()
        super().hideEvent(event)

    def _report(self, message):
        self.action_status.setText(message)
        self.action_status.setVisible(bool(message))

    def _refresh_devices(self):
        self._report("正在查找虚拟声音设备…")
        self.parentWidget()._request_devices()

    def _selected_device(self):
        item = self.devices.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _device_changed(self, *_):
        self._report("")
        self._update_state()

    def refresh(self, success=True):
        if not success:
            self._report("设备刷新失败，请检查音频驱动。")
            return
        selected = self._selected_device()
        window = self.parentWidget()
        preferred_id = selected.id if selected else window.settings.device_id
        with QSignalBlocker(self.devices):
            self.devices.clear()
            match = None
            for device in routing_devices(window.audio_devices):
                item = QListWidgetItem(device.name, self.devices)
                item.setData(Qt.ItemDataRole.UserRole, device)
                if device.id == preferred_id:
                    match = item
            if match is not None:
                self.devices.setCurrentItem(match)
            elif not selected and self.devices.count():
                self.devices.setCurrentRow(0)
        if selected and not match:
            self._report(f"「{selected.name}」已断开或已禁用。请重新连接后刷新，或另选一个设备。")
        elif self.action_status.text() == "正在查找虚拟声音设备…":
            self._report("已刷新虚拟声音设备。")
        self._update_state()

    def _update_state(self):
        device = self._selected_device()
        device_id = device.id if device else None
        settings = self.parentWidget().settings
        selected = bool(device_id and device_id == settings.device_id)
        verified = selected and device_id == settings.routing_verified_id
        running = self._capture is not None or self._source_request is not None
        self.devices.setEnabled(not running)
        self.refresh_button.setEnabled(not running)
        self.select_button.setEnabled(not running)
        for button in (self.game_sample, self.voice_sample):
            button.setEnabled(selected and not running)
        game = self._peaks.get((device_id, "游戏音效"))
        voice = self._peaks.get((device_id, "队友语音"))
        self.verify_button.setEnabled(selected and not running and game is not None
                                      and voice is not None and voice > max(game, 0.0005))
        if self.devices.count() == 0:
            self.state.setText("未检测到 Sonar Chat 或 VB-CABLE。安装并启用播放设备后刷新。")
        elif not device:
            self.state.setText("请选择一个虚拟声音设备。")
        elif verified:
            self.state.setText(f"已选中：{device.name} · 当前来源 · 上次已确认分流")
        elif selected:
            self.state.setText(f"已选中：{device.name} · 当前分流来源（待验证）")
        else:
            self.state.setText(f"已选中：{device.name} · 尚未设为当前声音来源")

    def _open_link(self, url):
        if QDesktopServices.openUrl(QUrl(url)):
            self._report("已请求在默认浏览器中打开官方网页。")
            return
        self._report("系统未能打开浏览器。请复制链接后手动打开。")
        dialog = QDialog(self)
        dialog.setWindowTitle("无法打开官方网页")
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.resize(565, 145)
        apply_dialog_theme(dialog)
        box = QVBoxLayout(dialog)
        box.addWidget(QLabel("请复制下面的官方网址，再粘贴到浏览器。"))
        address = QLineEdit(url)
        address.setReadOnly(True)
        box.addWidget(address)
        actions = QHBoxLayout()
        copy = GlassButton("复制链接")
        close = GlassButton("关闭")

        def copy_link():
            QApplication.clipboard().setText(url)
            self._report("官方链接已复制，可粘贴到浏览器打开。")
            dialog.accept()

        copy.clicked.connect(copy_link)
        close.clicked.connect(dialog.reject)
        actions.addWidget(copy)
        actions.addWidget(close)
        box.addLayout(actions)
        dialog.open()
        address.setFocus()
        address.selectAll()

    def _find_source(self, device):
        combo = self.parentWidget().device_combo
        index = combo.findData(device.id)
        return index if index >= 0 and combo.itemData(index, Qt.ItemDataRole.UserRole + 1) else -1

    def _select(self):
        device = self._selected_device()
        if not device:
            self._report("请先选一个虚拟声音设备。若列表为空，请安装或启用设备后刷新。")
            return
        if self._find_source(device) < 0:
            self._report(f"「{device.name}」已断开或已禁用，请刷新后重试。")
            return
        request = (object(), device)
        self._source_request = request
        self._report(f"正在检查「{device.name}」能否采集…")
        self._update_state()

        def task():
            try:
                error = probe_source(device.id)
            except Exception as exc:
                error = str(exc) or type(exc).__name__
            try:
                self.source_probed.emit(request, error)
            except RuntimeError:
                pass  # The wizard has been destroyed while the check was running.

        Thread(target=task, name="routing-device-probe", daemon=True).start()

    def _apply_source(self, request, error):
        if request is not self._source_request:
            return
        self._source_request = None
        _, device = request
        window = self.parentWidget()
        combo = window.device_combo
        index = self._find_source(device)
        if error:
            self._report(f"无法使用「{device.name}」：{error}。请检查设备是否启用、已连接或被独占。")
        elif index < 0:
            self._report(f"「{device.name}」已断开或已禁用，请刷新后重试。")
        else:
            previous_index, previous_id = combo.currentIndex(), window.settings.device_id
            with QSignalBlocker(combo):
                combo.setCurrentIndex(index)
                try:
                    window._save_settings()
                except OSError as exc:
                    combo.setCurrentIndex(previous_index)
                    window.settings.device_id = previous_id
                    window._update_device_hint()
                    self._report(f"无法保存声音来源：{exc}")
                else:
                    self._report(f"当前声音来源：{device.name}。请采样确认分流。")
        self._update_state()

    def _sample(self, category):
        device = self._selected_device()
        if not device or self._capture is not None:
            return
        device_id = device.id
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
            self.parentWidget().settings.routing_verified_id = self._selected_device().id
            self.parentWidget().settings.save()
            self.parentWidget()._update_device_hint()
            self._update_state()

    def done(self, result):
        self._source_request = None
        if self._capture is not None:
            self._stop()
        self._update_state()
        super().done(result)
