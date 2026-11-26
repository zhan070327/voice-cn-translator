from valorant_translator.ui.texts import tr
import psutil

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QProgressBar, QSizePolicy, QVBoxLayout

from valorant_translator.ui.dialogs import apply_dialog_theme
from valorant_translator.ui.glass import GAP, GUTTER, INLINE, CompactLabel, GlassButton, GlassSurface, tint_icons
from valorant_translator.ui.icons import LineIcon


class Onboarding(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.step = 0
        self.destination = window.quick_page
        self.test_started = False
        self._loading = False
        self._device_refresh_failed = False
        window.devices_refreshed.connect(self._devices_refreshed)
        self.setWindowTitle(tr("onboarding.0001"))
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMinimumWidth(525)
        container = QVBoxLayout(self)
        container.setContentsMargins(0, 0, 0, 0)
        self.surface = GlassSurface()
        container.addWidget(self.surface)
        layout = QVBoxLayout(self.surface)
        layout.setContentsMargins(GUTTER, GUTTER, GUTTER, GUTTER)
        layout.setSpacing(GAP)
        steps = QFrame()
        steps.setObjectName("glassCard")
        step_layout = QVBoxLayout(steps)
        step_layout.setContentsMargins(GAP, GUTTER, GAP, GUTTER)
        step_layout.setSpacing(INLINE)
        step_row = QHBoxLayout()
        step_row.setSpacing(GAP)
        self.step_icons = []
        for name, label in (("chat", tr("onboarding.0002")), ("download", tr("onboarding.0003")),
                            ("mic", tr("onboarding.0004"))):
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
        step_layout.addLayout(step_row)
        self.step_status = QLabel()
        self.step_status.setObjectName("statLabel")
        self.step_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        step_layout.addWidget(self.step_status)
        layout.addWidget(steps)
        panel = QFrame()
        panel.setObjectName("glassCard")
        panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
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
        self.context = QLabel()
        self.context.setObjectName("muted")
        self.context.setTextFormat(Qt.TextFormat.PlainText)
        self.context.setWordWrap(True)
        card.addWidget(self.context)
        self.progress = QProgressBar()
        card.addWidget(self.progress)
        self.transfer = CompactLabel()
        self.transfer.setObjectName("modelTransfer")
        self.transfer.hide()
        card.addWidget(self.transfer)
        self.action = GlassButton(icon="next")
        self.action.setObjectName("guideAction")
        self.action.setProperty("ghost", True)
        self.action.clicked.connect(self._action)
        card.addWidget(self.action, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.finish = GlassButton(tr("onboarding.0005"), icon="play")
        self.finish.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        self.skip = GlassButton(tr("onboarding.0006"))
        self.skip.clicked.connect(self.reject)
        buttons.addWidget(self.skip)
        buttons.addStretch()
        self.talk_test = GlassButton(tr("onboarding.0020"), icon="mic")
        self.talk_test.setToolTip(tr("onboarding.0019"))
        self.talk_test.clicked.connect(self._test_microphone)
        buttons.addWidget(self.talk_test)
        buttons.addWidget(self.finish)
        layout.addLayout(buttons)
        layout.addStretch()
        self.apply_theme()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(200)
        self._refresh()

    def apply_theme(self):
        settings = self.window.settings
        self.surface.set_background(settings.background_theme, settings.background_color, settings.game)
        apply_dialog_theme(self, settings, painted_surface=True)
        tint_icons(self, settings.accent)
        for button in self.findChildren(GlassButton):
            button.set_reduced_motion(settings.reduce_motion)
        for icon in self.step_icons:
            icon.set_reduced_motion(settings.reduce_motion)

    def _update_step_icons(self):
        self.step_status.setText(tr("onboarding.step_progress", p0=self.step + 1, p1=len(self.step_icons)))
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
        self.talk_test.setVisible(self.step == 2)
        if self.step == 0:
            self.transfer.hide()
            self.context.hide()
            self.action.set_line_icon("next")
            self.title.setText(tr("onboarding.0007"))
            self._set_details(tr("onboarding.purpose"))
            self.action.setText(tr("onboarding.0010"))
            self.action.setEnabled(True)
        elif self.step == 1:
            self.title.setText(tr("onboarding.0011"))
            self._set_details(tr("onboarding.download_instructions"))
            sizes = w.download_sizes()
            models = [f"Whisper {w.model_combo.currentData()}", "M2M100 418M"]
            models = [f"{model} · {sizes[model] / 1024**2:,.1f} MiB" if sizes.get(model) else model
                      for model in models]
            self.context.setText("\n".join((" · ".join(models), tr("onboarding.recommend_model", p0=self._recommendation()))))
            self.context.setToolTip(w.model_status_label.toolTip())
            self.context.show()
            status = w.model_transfer.text() if w._model_busy and not w.model_transfer.isHidden() else w._model_error
            show_transfer = bool(status)
            if show_transfer:
                self.transfer.setText(status.removeprefix(tr("onboarding.0013")))
            self.transfer.setVisible(show_transfer)
            self.progress.setRange(w.model_progress.minimum(), w.model_progress.maximum())
            self.progress.setValue(w.model_progress.value())
            ready = w._models_ready()
            self.action.set_line_icon("next" if ready else "pause" if w._model_busy else "download")
            self.action.setText(tr("onboarding.0014") if ready else tr("onboarding.0015") if w._model_busy else tr("onboarding.0016"))
            self.action.setEnabled(not w._model_busy or w.model_pause_button.isEnabled() and w._uses_cos_models())
        else:
            self.transfer.hide()
            self.title.setText(tr("onboarding.0017"))
            self._set_details(tr("onboarding.0018"))
            self.action.set_line_icon("speaker")
            self.action.setText(tr("onboarding.configure_audio"))
            self.action.setEnabled(True)
            self.talk_test.setEnabled(not w.talk._busy and bool(w.mic_combo.currentData()))
            lines = [tr("onboarding.device_summary", label=tr(label), name=combo.currentText())
                     for label, combo in (("onboarding.device_input", w.device_combo),
                                          ("onboarding.microphone", w.mic_combo)) if combo.currentData()]
            if self._device_refresh_failed:
                lines.append(tr("onboarding.device_refresh_failed"))
            if self.test_started:
                lines.append(w.talk_state.text())
            self.context.setText("\n".join(lines))
            self.context.setVisible(bool(lines))

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
        else:
            self.destination = self.window.audio_page
            self.accept()
            QTimer.singleShot(0, self.window, self.window._open_microphone_wizard)
            return
        self._refresh()

    def _test_microphone(self):
        if self.window._begin_talk():
            self.test_started = True
            QTimer.singleShot(4000, self, self.window.talk.end)
        self._refresh()

    def _set_loading(self):
        self._loading = True
        self._update_step_icons()
        self.action.setText(tr("onboarding.0021"))
        self.action.setEnabled(False)

    def _devices_refreshed(self, success):
        if self.step == 0 and self._loading:
            self._loading = False
            self._device_refresh_failed = not success
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
        if self.test_started:
            self.window.talk.end()
        super().done(result)
