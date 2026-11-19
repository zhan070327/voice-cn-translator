import psutil

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QProgressBar, QVBoxLayout

from valorant_translator.ui.glass import GlassButton, GlassSurface, glass_styles


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
        self.setMinimumSize(525, 325)
        container = QVBoxLayout(self)
        container.setContentsMargins(0, 0, 0, 0)
        surface = GlassSurface()
        container.addWidget(surface)
        self.setStyleSheet(glass_styles(window.settings.accent))
        layout = QVBoxLayout(surface)
        layout.setContentsMargins(22, 22, 22, 22)
        panel = QFrame()
        panel.setObjectName("glassCard")
        layout.addWidget(panel)
        card = QVBoxLayout(panel)
        card.setContentsMargins(18, 18, 18, 18)
        self.title = QLabel()
        self.title.setObjectName("title")
        card.addWidget(self.title)
        self.details = QLabel()
        self.details.setWordWrap(True)
        card.addWidget(self.details)
        self.progress = QProgressBar()
        card.addWidget(self.progress)
        self.action = GlassButton()
        self.action.setObjectName("primary")
        self.action.clicked.connect(self._action)
        card.addWidget(self.action)
        self.finish = GlassButton("完成")
        self.finish.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        later = GlassButton("稍后")
        later.clicked.connect(self.reject)
        buttons.addWidget(later)
        buttons.addStretch()
        buttons.addWidget(self.finish)
        layout.addLayout(buttons)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(200)
        self._refresh()

    def _refresh(self):
        if self._loading:
            return
        w = self.window
        self.progress.setVisible(self.step == 1 and w._model_busy)
        self.finish.setVisible(self.step == 2)
        if self.step == 0:
            self.title.setText("1 / 3 检测设备")
            self.details.setText(f"输入：{w.device_combo.currentText() or '未检测到设备'}\n"
                                 f"麦克风：{w.mic_combo.currentText() or '未检测到麦克风'}\n"
                                 f"推荐 Whisper {self._recommendation()}，可在详细设置更改。"
                                 + ("\n设备检测失败，可重试。" if self._device_refresh_failed else ""))
            self.action.setText("下一步")
            self.action.setEnabled(True)
        elif self.step == 1:
            self.title.setText("2 / 3 下载模型")
            sizes = w.download_sizes()
            models = [f"Whisper {w.model_combo.currentData()}", "M2M100 418M"]
            models = [f"{model} · {sizes[model] / 1024**2:,.1f} MiB" if sizes.get(model) else model
                      for model in models]
            self.details.setText("\n".join([*models, w.model_status_label.text(),
                                 w.model_transfer.text() if w._model_busy and w.model_transfer.isVisibleTo(w) else
                                 w.status_label.text()]))
            self.progress.setRange(w.model_progress.minimum(), w.model_progress.maximum())
            self.progress.setValue(w.model_progress.value())
            self.action.setText("下一步" if w._models_ready() else "暂停下载" if w._model_busy else "一键下载模型")
            self.action.setEnabled(not w._model_busy or w.model_pause_button.isEnabled() and w._uses_cos_models())
        else:
            self.title.setText("3 / 3 说一句话测试")
            self.details.setText("点击测试后说一句中文。程序会将识别结果翻成英文，显示在字幕中。\n" +
                                 (w.talk_state.text() if self.test_started else "等待录音测试。"))
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
