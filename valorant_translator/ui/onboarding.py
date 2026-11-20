import psutil

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QProgressBar, QSizePolicy, QVBoxLayout

from valorant_translator.ui.glass import GAP, GUTTER, INLINE, CompactLabel, GlassButton, GlassSurface, tint_icons
from valorant_translator.ui.icons import LineIcon


class Onboarding(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.step = 0
        self.test_started = False
        self._loading = False
        self._device_refresh_failed = False
        window.devices_refreshed.connect(self._devices_refreshed)
        self.setWindowTitle("首次使用 · VoiceTrans")
        self.setMinimumSize(525, 380)
        container = QVBoxLayout(self)
        container.setContentsMargins(0, 0, 0, 0)
        self.surface = GlassSurface()
        container.addWidget(self.surface)
        layout = QVBoxLayout(self.surface)
        layout.setContentsMargins(GUTTER, GUTTER, GUTTER, GUTTER)
        layout.setSpacing(GAP)
        steps = QFrame()
        steps.setObjectName("glassCard")
        step_row = QHBoxLayout(steps)
        step_row.setContentsMargins(GAP, GAP, GAP, GAP)
        step_row.setSpacing(GAP)
        self.step_icons = []
        for name, label in (("chip", "设备"), ("download", "模型"), ("mic", "试音")):
            group = QVBoxLayout()
            group.setSpacing(4)
            icon = LineIcon(name)
            self.step_icons.append(icon)
            group.addWidget(icon, alignment=Qt.AlignmentFlag.AlignHCenter)
            text = QLabel(label)
            text.setObjectName("statLabel")
            text.setAlignment(Qt.AlignmentFlag.AlignCenter)
            group.addWidget(text)
            step_row.addLayout(group, 1)
        layout.addWidget(steps)
        panel = QFrame()
        panel.setObjectName("glassCard")
        layout.addWidget(panel)
        card = QVBoxLayout(panel)
        card.setContentsMargins(GUTTER, GUTTER, GUTTER, GUTTER)
        card.setSpacing(INLINE)
        self.title = QLabel()
        self.title.setObjectName("title")
        card.addWidget(self.title)
        self.details = QLabel()
        self.details.setWordWrap(True)
        self.details.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        card.addWidget(self.details)
        self.progress = QProgressBar()
        card.addWidget(self.progress)
        self.transfer = CompactLabel()
        self.transfer.setObjectName("muted")
        self.transfer.hide()
        card.addWidget(self.transfer)
        self.action = GlassButton(icon="next")
        self.action.setProperty("ghost", True)
        self.action.clicked.connect(self._action)
        card.addWidget(self.action, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.finish = GlassButton("完成")
        self.finish.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        later = GlassButton("稍后")
        later.clicked.connect(self.reject)
        buttons.addWidget(later)
        buttons.addStretch()
        buttons.addWidget(self.finish)
        layout.addLayout(buttons)
        self.apply_theme()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(200)
        self._refresh()

    def apply_theme(self):
        settings = self.window.settings
        self.surface.set_background(settings.background_theme, settings.background_color)
        stylesheet = self.window.styleSheet()
        if stylesheet != self.styleSheet():
            self.setStyleSheet(stylesheet)
        tint_icons(self, settings.accent)

    def _update_step_icons(self):
        for index, icon in enumerate(self.step_icons):
            current = index == self.step
            mode = "idle"
            if index < self.step:
                mode = "done"
            elif current and (self._loading or (index == 1 and self.window._model_busy)):
                mode = "spin"
            elif current and index == 2 and self.window.talk._busy:
                mode = self.window.reverse_icon.mode if self.window.reverse_icon.mode == "spin" else "pulse"
            elif current and index == 2 and self.test_started and self.window.reverse_icon.mode == "done":
                mode = "done"
            icon.set_mode(mode, highlight=index <= self.step)

    def _set_details(self, text):
        self.details.setText(text)
        width = max(self.details.width(), self.width() - 4 * GUTTER)
        line_height = self.details.fontMetrics().lineSpacing()
        height = max(self.details.heightForWidth(width), line_height * (text.count("\n") + 1))
        self.details.setMinimumHeight(height)

    def _refresh(self):
        self._update_step_icons()
        if self._loading:
            return
        w = self.window
        self.progress.setVisible(self.step == 1 and w._model_busy)
        self.finish.setVisible(self.step == 2)
        if self.step == 0:
            self.transfer.hide()
            self.action.set_line_icon("next")
            self.title.setText("检测设备")
            self._set_details(f"输入：{w.device_combo.currentText() or '未检测到设备'}\n"
                              f"麦克风：{w.mic_combo.currentText() or '未检测到麦克风'}\n"
                              f"推荐 Whisper {self._recommendation()}，可在详细设置更改。"
                              + ("\n设备检测失败，可重试。" if self._device_refresh_failed else ""))
            self.action.setText("下一步")
            self.action.setEnabled(True)
        elif self.step == 1:
            self.title.setText("下载模型")
            sizes = w.download_sizes()
            models = [f"Whisper {w.model_combo.currentData()}", "M2M100 418M"]
            models = [f"{model} · {sizes[model] / 1024**2:,.1f} MiB" if sizes.get(model) else model
                      for model in models]
            status = w.model_transfer.text() if w._model_busy and not w.model_transfer.isHidden() else w.status_label.text()
            self._set_details("\n".join([*models, w.model_status_label.text()]))
            show_transfer = status != "当前状态：待机"
            if show_transfer:
                self.transfer.setText(status.removeprefix("当前状态："))
            self.transfer.setVisible(show_transfer)
            self.progress.setRange(w.model_progress.minimum(), w.model_progress.maximum())
            self.progress.setValue(w.model_progress.value())
            ready = w._models_ready()
            self.action.set_line_icon("next" if ready else "pause" if w._model_busy else "download")
            self.action.setText("下一步" if ready else "暂停下载" if w._model_busy else "一键下载模型")
            self.action.setEnabled(not w._model_busy or w.model_pause_button.isEnabled() and w._uses_cos_models())
        else:
            self.transfer.hide()
            self.title.setText("语音试播")
            self.action.set_line_icon("mic")
            self._set_details(w.talk_state.text() if self.test_started else "点击录音测试，说一句中文。")
            self.details.setToolTip("识别后会把中文翻译成英文并显示在字幕中。")
            self.action.setText("录音测试")
            self.action.setEnabled(not w.talk._busy and bool(w.mic_combo.currentData()))

    def _recommendation(self):
        available = psutil.virtual_memory().available
        choices = self.window.catalog if self.window._uses_cos_models() else ("tiny", "base", "small")
        if available < 3 * 1024**3 and "tiny" in choices:
            return "tiny"
        if available < 5 * 1024**3 and "base" in choices:
            return "base"
        return "small"

    def _action(self):
        if self.step == 0:
            self._set_loading()
            self.window._request_devices()
            return
        elif self.step == 1:
            if self.window._models_ready():
                self._set_loading()
                QTimer.singleShot(0, self, self._show_test)
                return
            elif self.window._model_busy:
                self.window._pause_model_download()
            else:
                self.window._download()
        elif self.window._begin_talk():
            self.test_started = True
            QTimer.singleShot(4000, self.window.talk.end)
        self._refresh()

    def _set_loading(self):
        self._loading = True
        self._update_step_icons()
        self.action.setText("加载中…")
        self.action.setEnabled(False)

    def _devices_refreshed(self, success):
        if self.step == 0 and self._loading:
            self._loading = False
            self._device_refresh_failed = not success
            if success:
                self.step = 1
            self._refresh()

    def _show_test(self):
        if self._loading:
            self._loading = False
            self.step = 2
            self._refresh()

    def done(self, result):
        self._loading = False
        self.timer.stop()
        if self.step == 2:
            self.window.talk.end()
        super().done(result)
