from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QTimer, Qt
from PySide6.QtGui import QColor, QEnterEvent, QHideEvent, QPalette, QShowEvent, QTextLayout
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QGroupBox, QLabel, QVBoxLayout, QWidget

from valorant_translator.config import Settings
from valorant_translator.pipeline import SubtitleResult, TaskTrace
from valorant_translator.ui.glass import (BACKGROUND_THEMES, DEFAULT_ACCENT, CompactLabel, GlassButton, accent_ink,
                                        GlassSection, GlassSurface, _background_pixmap, background_palette)
from valorant_translator.ui.icons import DONE_PHASE, LineIcon, button_icon


def _wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(10)
    return predicate()
from valorant_translator.ui.overlay import SubtitleOverlay


def test_overlay_keeps_two_and_expires(qapp):
    overlay = SubtitleOverlay(Settings(subtitle_seconds=0.01))
    assert overlay.autoFillBackground()
    assert overlay.palette().color(QPalette.ColorRole.Window).lightness() < 60
    for i in range(3):
        overlay.add_subtitle(f"字幕 {i}")
    assert len(overlay.entries) == 2
    time.sleep(0.03)
    overlay.expire()
    assert overlay.entries == []
    overlay.close()


def test_long_subtitles_do_not_resize_overlay(qapp):
    overlay = SubtitleOverlay(Settings(normal_window=False, font_size=60,
                                       show_original=True))
    overlay.show()
    qapp.processEvents()
    original_size = overlay.size()
    overlay.add_subtitle("很长的字幕" * 35, "long original text " * 30)
    overlay.add_subtitle("第二条很长的字幕" * 30, "another long original " * 30)
    qapp.processEvents()
    assert overlay.size() == original_size
    assert overlay.label.textFormat() == Qt.TextFormat.PlainText
    assert overlay.label.text()
    overlay.close()


def test_chinese_subtitle_remains_bilingual_when_originals_are_hidden(qapp):
    overlay = SubtitleOverlay(Settings(show_original=False))
    overlay.show()
    try:
        overlay.add_subtitle("我有钱", "I have money", always_bilingual=True)
        qapp.processEvents()
        assert overlay.rows[-1][2].text() == "I have money"
        assert overlay.rows[-1][3].text() == "我有钱"
    finally:
        overlay.close()


def test_main_window_starts_without_models(qapp, monkeypatch, tmp_path):
    import valorant_translator.config as config
    import valorant_translator.ui.app as ui
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(ui, "list_devices", lambda: [])
    monkeypatch.setattr(ui, "MODEL_DIR", tmp_path / "models")
    monkeypatch.setattr(ui.MainWindow, "_start_hotkey", lambda self: None)
    window = ui.MainWindow()
    assert "待机" in window.status_label.text()
    assert window.start_button.isEnabled()
    assert "没有可用设备" in window.device_hint.text()
    from valorant_translator.audio.capture import AudioDevice
    monkeypatch.setattr(ui, "list_devices", lambda: [
        AudioDevice("mic", "测试麦克风", False),
        AudioDevice("speakers", "测试耳机", True),
    ])
    window.refresh_devices()
    assert window.device_combo.currentData() == "speakers"
    window.device_combo.setCurrentIndex(0)
    assert "当前选的是麦克风" in window.device_hint.text()
    window.device_combo.setCurrentIndex(1)
    assert "正在听系统播放声音" in window.device_hint.text()
    assert [window.tabs.tabText(index) for index in range(window.tabs.count())] == [
        "快速开始", "声音与字幕", "详细设置", "翻译历史", "运行详情",
    ]
    window.history.add_result(SubtitleResult("Go rush A", "快攻 A。", "en", 0.2, 0.1, 0.3))
    window._refresh_history()
    assert window.history_table.item(0, 1).text() == "Go rush A"
    assert window.fast_check.isChecked()
    assert window.update_button.text() == "检查更新"
    assert window.settings_update_button.text() == "检查更新"
    assert "当前版本" in window.update_version_label.text()
    window._runtime("识别 CUDA · 翻译 CPU · 低延迟模式")
    assert "翻译 CPU" in window.runtime_label.text()
    monkeypatch.setattr(ui.QMessageBox, "information", lambda *args: None)
    window._copy_feedback()
    feedback = QApplication.clipboard().text()
    assert "识别 CUDA · 翻译 CPU" in feedback
    assert "实际听到的话：请填写" in feedback
    window._record_trace(TaskTrace(42, "识别出队", "排队 0.2 秒", timings={"识别排队": 0.2}))
    window._record_trace(TaskTrace(42, "结果丢弃", "语种置信度 51% 低于 68%", discarded=True,
                                   timings={"识别": 0.7}))
    window._diagnostic("继续监听")
    assert "51% 低于 68%" in window.last_discard.text()
    window._copy_feedback()
    feedback = QApplication.clipboard().text()
    assert "#42 结果丢弃" in feedback
    assert "#42 识别排队 0.200 秒 · 识别 0.700 秒" in feedback
    window._result(SubtitleResult("Two A Main.", "A 大两个。", "en", 0.2, 0.1, 0.3, task_id=43))
    assert window._recent_trace[-1].stage == "界面已更新"
    assert window._recent_trace[-1].task_id == 43
    assert "Two A Main." in window.original_label.text()
    warnings = []
    monkeypatch.setattr(ui.QMessageBox, "warning", lambda *args: warnings.append(args))
    window.start_button.click()
    assert warnings and "下载模型" in warnings[0][1] and "从文件夹导入" in warnings[0][2]
    window.bridge.model_download_progress.emit(2_200_000_000, 2_428_144_996, 1_000_000.0)
    assert window.model_progress.value() == 90
    assert "剩余" in window.model_transfer.text()
    window.overlay.add_subtitle("测试", "test")
    assert "测试" in window.overlay.label.text()
    window.overlay.show()
    qapp.processEvents()
    window._toggle_overlay()
    assert not window.overlay.isVisible()
    window.mode_combo.setCurrentIndex(1)
    assert not window.settings.normal_window
    assert window.settings.click_through
    assert window.overlay.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    window._toggle_position_edit()
    assert not window.settings.click_through
    window.mode_combo.setCurrentIndex(0)
    assert window.settings.normal_window
    settings_groups = window.settings_page.findChildren(QGroupBox)
    assert [group.title() for group in settings_groups] == [
        "软件更新", "本地模型", "翻译引擎", "识别和翻译", "字幕", "历史和隐私", "游戏词库",
    ]
    assert all(isinstance(group, GlassSection) and group.icon.width() == 20 for group in settings_groups)
    assert window.advanced_fields.isHidden()
    previous = (window.vad_spin.value(), window.timeout_spin.value(), window.age_spin.value())
    window.advanced_button.click()
    assert not window.advanced_fields.isHidden()
    window.advanced_button.click()
    assert window.advanced_fields.isHidden()
    assert previous == (window.vad_spin.value(), window.timeout_spin.value(), window.age_spin.value())
    for button in (*window.nav.buttons(), window.download_button, window.import_button, window.refresh_button,
                   window.test_button, window.start_button, window.stop_button, window.talk_button, window.reverse_copy):
        assert not button.icon().isNull()

    window.pipeline = SimpleNamespace(failures=0)
    window._show_audio_level(0.41)
    assert window.audio_icon.mode == "pulse"
    window._record_trace(TaskTrace(2, "推理开始", ""))
    window._record_trace(TaskTrace(3, "翻译出队", ""))
    assert window.run_icon.mode == "spin"
    window._record_trace(TaskTrace(2, "推理结束", ""))
    assert window.run_icon.mode == "spin"
    window._record_trace(TaskTrace(3, "结果丢弃", "", discarded=True))
    assert window.run_icon.mode == "idle"
    window._show_audio_level(0)
    assert window.audio_icon.mode == "idle"
    window.pipeline = None
    window._talk_status("正在收听你的麦克风，松开后翻译。")
    assert window.microphone_icon.mode == "pulse"
    window._talk_status("正在将中文语音翻译成英文…")
    assert window.microphone_icon.mode == "idle" and window.reverse_icon.mode == "spin"
    window._reverse_result(("走 A。", "Push A."))
    assert window.reverse_icon.mode == "done"
    assert window.result_icon.mode == "done"
    window._set_update_busy(True)
    assert window.update_button.text() == window.settings_update_button.text() == "检查中…"
    assert not window.update_button.isEnabled()
    window._set_update_busy(False)
    window._select_game("cs2")
    assert window.game_notice.text() == "已切换至 CS2"
    window._show_onboarding()
    window.onboarding.action.click()
    assert window.onboarding.action.text() == window.refresh_button.text() == "加载中…"
    until = time.monotonic() + 2
    while window.onboarding.step == 0 and time.monotonic() < until:
        QTest.qWait(20)
    assert window.onboarding.step == 1
    assert window.refresh_button.text() == "刷新设备"
    window.onboarding.close()
    window.close()


def test_glass_button_animates_without_resizing(qapp):
    button = GlassButton("测试", icon="mic")
    button.show()
    hit_target = button.geometry()
    point = QPointF(4, 4)
    qapp.sendEvent(button, QEnterEvent(point, point, point))
    assert _wait_for(lambda: abs(button.visualHover - 1.0) < 0.001)
    qapp.sendEvent(button, QEvent(QEvent.Type.Leave))
    assert _wait_for(lambda: abs(button.visualHover) < 0.001)
    QTest.mousePress(button, Qt.MouseButton.LeftButton)
    QTest.qWait(110)
    assert abs(button.visualScale - 0.97) < 0.001
    assert button.geometry() == hit_target
    QTest.mouseRelease(button, Qt.MouseButton.LeftButton)
    QTest.qWait(110)
    assert abs(button.visualScale - 1.0) < 0.001
    button.close()


def test_live_icon_stops_while_hidden_or_minimized_and_completes_once(qapp):
    host = QWidget()
    layout = QVBoxLayout(host)
    icon = LineIcon("mic")
    layout.addWidget(icon)
    label = CompactLabel("这是一条完整的诊断日志，完整内容可以在悬停时查看。")
    label.setFixedWidth(60)
    layout.addWidget(label)
    host.show()
    assert QLabel.text(label).endswith("…")
    assert label.toolTip() == label.text()
    assert button_icon("missing", "#ffffff").isNull()
    icon.set_mode("spin", highlight=True)
    assert _wait_for(lambda: icon.phase > 0)
    host.showMinimized()
    assert _wait_for(lambda: not icon._timer.isActive())
    stationary = icon.phase
    QTest.qWait(100)
    assert icon.phase == stationary and not icon._timer.isActive()
    host.showNormal()
    assert _wait_for(lambda: icon.phase > stationary)
    host.hide()
    stationary = icon.phase
    QTest.qWait(70)
    assert icon.phase == stationary and not icon._timer.isActive()
    host.show()
    icon.set_mode("done", restart=True)
    assert _wait_for(lambda: icon.phase == DONE_PHASE and not icon._timer.isActive())
    QTest.qWait(100)
    assert icon.phase == DONE_PHASE and not icon._timer.isActive()
    host.close()


def test_live_icon_handles_events_before_timer_creation(qapp):
    class IconReceivingResizeEvents(LineIcon):
        def setFixedSize(self, *args):
            assert self._timer is not None
            self.hideEvent(QHideEvent())
            return super().setFixedSize(*args)

    early = IconReceivingResizeEvents("mic")
    early.close()
    partial = LineIcon.__new__(LineIcon)
    QWidget.__init__(partial)
    partial.hideEvent(QHideEvent())
    partial.showEvent(QShowEvent())
    partial.eventFilter(partial, QEvent(QEvent.Type.WindowStateChange))
    partial.close()


def test_checkmark_uses_visible_elapsed_time_when_timer_ticks_arrive_late(qapp, monkeypatch):
    from valorant_translator.ui import icons

    clock = [12.0]
    monkeypatch.setattr(icons, "monotonic", lambda: clock[0])
    host = QWidget()
    layout = QVBoxLayout(host)
    icon = LineIcon("mic")
    layout.addWidget(icon)
    host.show()
    try:
        icon.set_mode("done", restart=True)
        clock[0] += 0.100
        icon._tick()
        before = icon.phase
        assert 0 < before < DONE_PHASE
        host.hide()
        clock[0] += 5
        host.show()
        icon._tick()
        assert icon.phase == before
        clock[0] += 0.500
        icon._tick()
        assert icon.phase == DONE_PHASE and not icon._timer.isActive()
    finally:
        host.close()


def test_background_presets_draw_distinct_cached_gradients(qapp):
    surface = GlassSurface()
    surface.resize(320, 208)
    surface.show()
    _background_pixmap.cache_clear()
    samples = []
    for theme in BACKGROUND_THEMES:
        surface.set_background(theme, "")
        image = surface.grab().toImage()
        samples.append((image.pixelColor(18, 65).name(), image.pixelColor(307, 196).name()))
        misses = _background_pixmap.cache_info().misses
        surface.repaint()
        surface.grab()
        assert _background_pixmap.cache_info().misses == misses
    assert len(set(samples)) == len(BACKGROUND_THEMES)
    left, right = map(QColor, samples[0])
    assert left.blue() > left.red() and right.red() > right.blue()
    assert _background_pixmap.cache_info().misses == len(BACKGROUND_THEMES)
    surface.close()


def test_theme_switches_are_independent_saved_and_shared_with_onboarding(qapp, monkeypatch, tmp_path):
    from valorant_translator import config
    from valorant_translator.ui import app as ui

    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(ui, "MODEL_DIR", tmp_path / "models")
    monkeypatch.setattr(ui, "list_devices", lambda: [])
    monkeypatch.setattr(ui, "load_channel", lambda: {})
    monkeypatch.setattr(ui.MainWindow, "_start_hotkey", lambda self: None)
    Settings(font_size=34).save()
    window = ui.MainWindow()
    try:
        assert window.background_combo.currentData() == "default"
        assert window.start_button.objectName() == "primary"
        assert window.update_cancel_button.property("ghost")
        assert window.model_pause_button.property("ghost")
        assert all(button.property("gamePill") for button in window.game_group.buttons())
        style = window.styleSheet()
        window.background_combo.setCurrentIndex(window.background_combo.findData("indigo"))
        assert window.styleSheet() == style
        assert Settings.load().background_theme == "indigo"

        window._show_onboarding()
        guide = window.onboarding
        assert guide.surface._background == window.surface._background
        assert guide.styleSheet() == style
        assert guide.action.property("ghost")

        def pick_color(_initial, title):
            return QColor("#ded342" if title == "自定义背景色" else "#00ac78")

        monkeypatch.setattr(window, "_pick_theme_color", pick_color)
        window.background_color_button.click()
        custom = background_palette("custom", "#ded342")
        assert guide.surface._background == window.surface._background == custom
        assert window.background_combo.currentData() == "custom"
        assert window.styleSheet() == style
        window._choose_accent()
        assert window.surface._background == custom
        assert window.styleSheet() != style
        assert guide.styleSheet() == window.styleSheet()
        assert guide.step_icons[1].accent == accent_ink("#00ac78")
        saved = Settings.load()
        assert (saved.background_theme, saved.background_color, saved.accent) == ("custom", "#ded342", "#00ac78")

        window._model_busy = True
        guide.step = 1
        window._model_download_progress(260_000_000, 500_000_000, 4_900_000)
        guide._refresh()
        qapp.processEvents()
        assert guide.action.text() == "暂停下载"
        assert guide.action.width() < guide.width() / 3
        assert guide.action.objectName() != "primary"
        assert "离线翻译" in guide.details.text()
        assert "M2M100 418M" in guide.context.text()
        assert "推荐 Whisper" in guide.context.text()
        assert guide.transfer.isVisible() and "4.7 MiB/s" in guide.transfer.text()
        assert guide.transfer.fontMetrics().height() <= guide.transfer.height()
        assert "预计" in guide.transfer.toolTip()
        window._model_busy = False
        guide.close()
    finally:
        window.close()

    reopened = ui.MainWindow()
    try:
        assert reopened.surface._background == custom
        assert reopened.settings.accent == "#00ac78"
        reopened.theme_reset_button.click()
        saved = Settings.load()
        assert saved.background_theme == "default" and saved.accent == DEFAULT_ACCENT
        assert saved.background_color == "" and saved.font_size == 34
        assert reopened.surface._background == background_palette("default", "", "valorant")
    finally:
        reopened.close()


def test_gui_detects_incompatible_torch_version(monkeypatch):
    import valorant_translator.ui.app as ui
    monkeypatch.setattr(ui, "version", lambda name: "2.5.1" if name == "torch" else "0")
    assert not ui.MainWindow._torch_version_ok()
    monkeypatch.setattr(ui, "version", lambda name: "2.6.0" if name == "torch" else "0")
    assert ui.MainWindow._torch_version_ok()


def test_b6_both_color_pickers_use_readable_non_native_glass(qapp, monkeypatch, tmp_path):
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QColorDialog, QDialog
    from valorant_translator import config
    from valorant_translator.ui import app as ui

    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(ui, "list_devices", lambda: [])
    monkeypatch.setattr(ui, "load_channel", lambda: None)
    monkeypatch.setattr(ui.MainWindow, "_start_hotkey", lambda self: None)
    window = ui.MainWindow()
    seen = []

    def inspect_and_accept():
        pickers = [widget for widget in qapp.topLevelWidgets()
                   if isinstance(widget, QColorDialog) and widget.isVisible()]
        for picker in pickers:
            seen.append((picker.windowTitle(), picker.testOption(QColorDialog.ColorDialogOption.DontUseNativeDialog),
                         "QColorDialog" in picker.styleSheet(),
                         picker.palette().color(QPalette.ColorRole.Window).lightness(),
                         picker.palette().color(QPalette.ColorRole.WindowText).lightness()))
            picker.setCurrentColor(QColor("#22778c"))
            picker.done(QDialog.DialogCode.Accepted)

    try:
        for action in (window._choose_accent, window._choose_background):
            QTimer.singleShot(0, inspect_and_accept)
            # Prevent a modal timeout from hanging CI if the expected widget never appears.
            watchdog = QTimer(window)
            watchdog.setSingleShot(True)
            watchdog.timeout.connect(lambda: [w.reject() for w in window.findChildren(QColorDialog)])
            watchdog.start(1500)
            action()
            watchdog.stop()
        assert [item[0] for item in seen] == ["选择点缀色", "自定义背景色"]
        assert all(native_disabled and styled and background < foreground
                   for _, native_disabled, styled, background, foreground in seen)
        assert Settings.load().accent == "#22778c"
        assert Settings.load().background_color == "#22778c"
    finally:
        window.close()


def test_b6_routing_and_automatic_ptt_do_not_share_device_setting(qapp, monkeypatch, tmp_path):
    from valorant_translator import config
    from valorant_translator.audio.capture import AudioDevice
    from valorant_translator.ui import app as ui, routing

    monkeypatch.setattr(routing, "probe_source", lambda _id: "")
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "settings.json")
    old_mic = AudioDevice("old", "旧麦克风", False)
    new_mic = AudioDevice("new", "新默认麦克风", False)
    team = AudioDevice("speakers", "耳机", True)
    sonar = AudioDevice("chat", "SteelSeries Sonar - Chat", True)
    monkeypatch.setattr(ui, "list_devices", lambda: [old_mic, team, sonar])
    monkeypatch.setattr(ui, "load_channel", lambda: None)
    monkeypatch.setattr(ui.MainWindow, "_start_hotkey", lambda self: None)
    monkeypatch.setattr(ui.MainWindow, "_models_ready", lambda self: True)
    window = ui.MainWindow()
    try:
        assert window.settings.device_id == "speakers"
        monkeypatch.setattr(ui, "list_devices", lambda: [new_mic, team, sonar])
        seen = []
        monkeypatch.setattr(window.talk, "begin", lambda settings, **kw: seen.append(settings.microphone_id) or True)
        assert window._begin_talk()
        assert seen == ["new"]
        assert window.settings.device_id == "speakers"
        assert Settings.load().microphone_id == "new"
        assert "新默认麦克风" in window.microphone_stat.text()
        monkeypatch.setattr(window, "_request_devices", lambda: None)
        window._open_routing()
        wizard = window.routing_wizard
        assert wizard.devices.count() == 1 and wizard.devices.currentItem().text() == sonar.name
        assert "尚未设为当前声音来源" in wizard.state.text()
        wizard.select_button.click()
        assert _wait_for(lambda: Settings.load().device_id == "chat")
        assert Settings.load().microphone_id == "new"
        assert "待验证" in wizard.state.text()
        assert not wizard.verify_button.isEnabled()
        wizard.close()
    finally:
        window.close()


def test_capture_failure_and_virtual_device_guidance(qapp, monkeypatch, tmp_path):
    from valorant_translator import config
    from valorant_translator.audio.capture import AudioDevice
    from valorant_translator.ui import app as ui

    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(ui, "list_devices", lambda: [
        AudioDevice("cable", "CABLE Output", True),
        AudioDevice("mic", "CABLE Output", False),
    ])
    monkeypatch.setattr(ui, "load_channel", lambda: None)
    monkeypatch.setattr(ui.MainWindow, "_start_hotkey", lambda self: None)
    window = ui.MainWindow()
    try:
        assert "系统声音请选择 CABLE Output" in window.device_hint.text()
        assert window.device_hint.isVisible() or not window.isVisible()
        assert "真实麦克风" in window.microphone_stat.text()
        window._status("音频采集失败：设备断开")
        assert "设备断开" in window.caption_hint.text()
        assert "自动重试" in window.diagnostic_label.text()
    finally:
        window.close()


def test_b6_history_correction_requires_explicit_scope(qapp, monkeypatch, tmp_path):
    from valorant_translator import config
    from valorant_translator.ui import app as ui

    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(ui, "list_devices", lambda: [])
    monkeypatch.setattr(ui, "load_channel", lambda: None)
    monkeypatch.setattr(ui.MainWindow, "_start_hotkey", lambda self: None)
    window = ui.MainWindow()
    try:
        window.history.add_result(SubtitleResult("Go A", "去 A。", "en", 0.1, 0.1, 0.2))
        window._refresh_history()
        window.history_table.selectRow(0)
        monkeypatch.setattr(ui.QInputDialog, "getMultiLineText", lambda *_: ("全队去 A。", True))
        monkeypatch.setattr(ui.QInputDialog, "getItem", lambda *_, **__: ("仅修正本条历史", True))
        window._correct_history()
        assert window.history.recent()[0].corrected_chinese == "全队去 A。"
        assert not window.custom_rules.rules
        assert window.history.lookup_correction("go a", "en") is None
        window.history_table.selectRow(0)
        monkeypatch.setattr(ui.QInputDialog, "getItem", lambda *_, **__: ("保存到我的词库", True))
        window._correct_history()
        [rule] = window.custom_rules.rules
        assert (rule.original, rule.translated, rule.game, rule.source, rule.target) == (
            "Go A", "全队去 A。", "valorant", "en", "zh")
    finally:
        window.close()


def _launch_upgrade_preview(monkeypatch, tmp_path):
    from valorant_translator import config
    from valorant_translator.ui import app as ui

    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(ui, "MODEL_DIR", tmp_path / "models")
    monkeypatch.setattr(ui, "list_devices", lambda: [])
    monkeypatch.setattr(ui, "load_channel", lambda: {})
    monkeypatch.setattr(ui.MainWindow, "_start_hotkey", lambda self: None)
    window = ui.MainWindow()
    window.show()
    return window


def test_engine_presets_gate_online_and_sync_actual_settings(qapp, monkeypatch, tmp_path):
    from valorant_translator.config import ONLINE_TRANSLATION, Settings
    from valorant_translator.ui.texts import tr

    window = _launch_upgrade_preview(monkeypatch, tmp_path)
    try:
        extreme = window.speed_combo.findData("extreme")
        window._select_speed_mode(extreme)
        assert window.speed_combo.currentData() == "balanced"
        assert window.statusBar().currentMessage() == tr("engine.sign_in_required")
        assert window._cloud_prompt.buttons()[0].text() == tr("engine.sign_in")
        assert not window._cloud_prompt.isModal()
        window._cloud_prompt.close()
        window._select_speed_mode(window.speed_combo.findData("precise"))
        assert window.model_combo.currentData() == "small"
        assert not window.fast_check.isChecked()
        assert Settings.load().speed_mode == "precise"
        window.settings.cloud_access_token = "test-session"
        monkeypatch.setattr(window.cloud, "entitlement", lambda: {
            "trial_remaining_ms": 2_100_000, "subscribed": False, "online_allowed": True,
        })
        window._select_speed_mode(extreme)
        assert _wait_for(lambda: window.speed_combo.currentData() == "extreme")
        assert window.cloud_balance.text() == "试用剩余 0 小时 35 分 0 秒"
        assert window.model_combo.currentData() == "tiny"
        assert Settings.load().translation_model == ONLINE_TRANSLATION

        monkeypatch.setattr(window.cloud, "entitlement", lambda: {
            "trial_remaining_ms": 0, "subscribed": False, "online_allowed": False,
        })
        window._select_speed_mode(extreme)
        assert _wait_for(lambda: window._cloud_prompt is not None)
        assert window.speed_combo.currentData() == "balanced"
        assert window.cloud_balance.text() == tr("engine.balance_exhausted")
        assert window._cloud_prompt.buttons()[0].text() == tr("engine.subscribe")
        assert not window._cloud_prompt.isModal()
        window._cloud_prompt.close()
    finally:
        window.close()


def test_online_mode_ignores_entitlement_after_local_selection(qapp, monkeypatch, tmp_path):
    window = _launch_upgrade_preview(monkeypatch, tmp_path)
    try:
        window.settings.cloud_access_token = "test-session"
        replies = []
        monkeypatch.setattr(window, "_request_cloud", lambda request, completed: replies.append(completed))
        window._select_speed_mode(window.speed_combo.findData("extreme"))
        window._select_speed_mode(window.speed_combo.findData("balanced"))
        [reply] = replies
        reply({"trial_remaining_ms": 900_000, "subscribed": False, "online_allowed": True}, "")
        assert window.settings.speed_mode == "balanced"
    finally:
        window.close()


def test_cloud_account_authentication_and_subscription_checkout(qapp, monkeypatch, tmp_path):
    import base64

    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QLineEdit
    from valorant_translator.models.cloud import CloudError
    from valorant_translator.ui.cloud_account import PaymentDialog, PlanDialog
    from valorant_translator.ui.texts import tr

    window = _launch_upgrade_preview(monkeypatch, tmp_path)
    try:
        calls = []

        def login(email, password):
            calls.append(("login", email))
            raise CloudError("unauthenticated")

        def register(email, password):
            calls.append(("register", email))
            window.settings.cloud_access_token = "test-session"

        def logout():
            calls.append(("logout",))
            window.settings.cloud_access_token = ""

        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image = QPixmap(10, 10)
        image.fill(Qt.GlobalColor.white)
        assert image.save(buffer, "PNG")
        encoded = base64.b64encode(buffer.data()).decode()

        def order(plan, channel):
            calls.append((plan, channel))
            return {"qr_png_base64": encoded, "pay_url": "weixin://checkout-fixture"}

        monkeypatch.setattr(window.cloud, "login", login)
        monkeypatch.setattr(window.cloud, "register", register)
        monkeypatch.setattr(window.cloud, "logout", logout)
        monkeypatch.setattr(window.cloud, "entitlement", lambda: {
            "trial_remaining_ms": 143_000, "subscribed": False, "online_allowed": True,
        })
        monkeypatch.setattr(window.cloud, "plans", lambda: [{
            "id": "test-monthly", "name": "测试套餐", "price_cents": 123,
        }])
        monkeypatch.setattr(window.cloud, "create_order", order)

        window._select_speed_mode(window.speed_combo.findData("extreme"))
        window._cloud_prompt.buttons()[0].click()
        account = window._cloud_account_dialog
        assert account.password.echoMode() == QLineEdit.EchoMode.Password
        account.email.setText("test@example.test")
        account.password.setText("test-password")
        login_button = next(b for b in account.findChildren(GlassButton) if b.text() == tr("engine.login"))
        login_button.click()
        assert _wait_for(lambda: account.message.text() == tr("engine.login_failed"))
        register_button = next(b for b in account.findChildren(GlassButton) if b.text() == tr("engine.register"))
        register_button.click()
        assert _wait_for(lambda: window._cloud_account_dialog is None)
        assert _wait_for(lambda: "2 分 23 秒" in window.cloud_balance.text())

        window._open_cloud_account()
        account = window._cloud_account_dialog
        next(b for b in account.findChildren(GlassButton) if b.text() == tr("engine.subscribe")).click()
        plan = window.findChild(PlanDialog)
        assert _wait_for(plan.pay.isEnabled)
        assert "测试套餐 · ¥1.23 / 月" == plan.plans.currentText()
        plan.channels_combo.setCurrentIndex(plan.channels_combo.findData("wechat"))
        plan.pay.click()
        assert _wait_for(lambda: window.findChild(PaymentDialog) is not None)
        payment = window.findChild(PaymentDialog)
        assert any(child.accessibleName() == tr("engine.payment_qr") for child in payment.findChildren(QLabel))
        payment.accept()
        next(b for b in account.findChildren(GlassButton) if b.text() == tr("engine.sign_out")).click()
        assert _wait_for(lambda: window._cloud_account_dialog is None)
        assert window.cloud_balance.text() == tr("engine.signed_out")
        assert calls == [("login", "test@example.test"), ("register", "test@example.test"),
                         ("test-monthly", "wechat"), ("logout",)]
    finally:
        window.close()


def test_leaving_online_mode_discards_held_microphone_audio(qapp, monkeypatch, tmp_path):
    import numpy as np

    from valorant_translator.ui import talk as talk_ui
    from valorant_translator.ui.texts import tr

    window = _launch_upgrade_preview(monkeypatch, tmp_path)
    try:
        requests = []
        monkeypatch.setattr(talk_ui, "ready_engine_settings", lambda *args: requests.append(args))

        class Capture:
            def __init__(self, device, frame, *_):
                self.frame = frame

            def start(self):
                self.frame(np.full(8000, .04, dtype=np.float32), 0)

            def stop(self):
                pass

        window.talk.capture_factory = Capture
        window.settings.microphone_id = "mic"
        window.settings.select_speed_mode("extreme")
        assert window.talk.begin(window.settings, cloud_client=window.cloud)
        window._select_speed_mode(window.speed_combo.findData("balanced"))
        window.talk.end()
        assert _wait_for(lambda: not window.talk._busy)
        assert window.talk.capture is None
        assert window.talk._frames == []
        assert window.talk_state.text() == tr("talk.capture_cancelled")
        assert not requests
    finally:
        window.close()


def test_upgrade_switches_five_skins_twice_and_persists_override(qapp, monkeypatch, tmp_path):
    from valorant_translator.ui.tokens import SKINS

    window = _launch_upgrade_preview(monkeypatch, tmp_path)
    try:
        qapp.processEvents()
        position = window.hero.geometry()
        monkeypatch.setattr(window, "_pick_theme_color", lambda *_: QColor("#117788"))
        window._choose_accent()
        buttons = dict(zip(SKINS, window.game_group.buttons()))
        for game in list(SKINS) * 2:
            buttons[game].click()
            qapp.processEvents()
            assert window.settings.game == game
            assert buttons[game].isChecked()
            assert window.hero.geometry() == position
            assert window.surface._background.top == SKINS[game].background
            expected = "#117788" if game == "valorant" else SKINS[game].accent
            assert window.settings.accent == expected
        assert _wait_for(lambda: window.game_capsule.target is buttons["general"]
                         and not window.skin_snapshot.isVisible())
        window.game_accent_reset_button.click()
        assert Settings.load().game_accents == {"valorant": "#117788"}
        window.reduce_motion_check.setChecked(True)
        buttons["valorant"].click()
        assert Settings.load().reduce_motion
        assert window.settings.accent == "#117788"
        assert not window.skin_snapshot.isVisible()
        assert not window.game_capsule.animation.state().value
    finally:
        window.close()


def test_upgrade_truthful_loading_empty_and_errors(qapp, monkeypatch, tmp_path):
    from valorant_translator.ui import app as ui
    window = _launch_upgrade_preview(monkeypatch, tmp_path)
    try:
        assert window.chinese_label.isHidden()
        assert window.caption_hint.text() == "开始翻译后显示字幕"
        assert window.caption_loading.isHidden()
        assert window.volume.isHidden()
        window._caption_state("loading", "正在加载本地模型。")
        assert window.caption_loading.isVisible() and "正在加载" in window.chinese_label.text()
        assert window.caption_loading.pulse.state().value
        window._status("运行失败：设备已断开")
        assert not window.caption_loading.isVisible()
        assert "设备已断开" in window.caption_hint.text()
        assert window.latency_stat.isHidden()
        window._result(SubtitleResult("A main", "A 大。", "en", 0.2, 0.1, 0.35))
        assert window.chinese_label.text() == "A 大。"
        assert window.language_label.width() > 0
        assert window.latency_stat.isVisible() and "0.35" in window.latency_stat.text()
        assert window.confidence_label.isHidden()
        window.tabs.setCurrentWidget(window.runtime_page)
        window.detail_toggle.click()
        assert window.details_panel.isVisible()
        assert "识别 0.20s" in window.latency_label.text()
        window.detail_toggle.click()
        assert _wait_for(window.details_panel.isHidden)
        window.tabs.setCurrentWidget(window.settings_page)
        window._set_model_busy(True)
        assert window.model_loading.isVisible()
        window._status("模型下载失败：网络中断")
        window._download_done()
        assert not window.model_loading.isVisible() and "网络中断" in window.model_state.text()
        window.nav.button(window.tabs.indexOf(window.history_page)).click()
        assert window.history_loading.isVisible()
        assert _wait_for(lambda: "暂无翻译历史" in window.history_state.text())
        monkeypatch.setattr(window.history, "recent", lambda *_: (_ for _ in ()).throw(OSError("读写失败")))
        window._request_history()
        assert _wait_for(lambda: "读写失败" in window.history_state.text())
        window.game_group.buttons()[1].click()
        window.nav.button(window.tabs.indexOf(window.settings_page)).click()
        assert window.glossary_loading.isVisible()
        assert _wait_for(lambda: window.glossary_status.text() == "该游戏词库建设中，暂用通用翻译")
        monkeypatch.setattr(ui, "read_default_glossary", lambda *_: (_ for _ in ()).throw(ValueError("词库损坏")))
        window._refresh_glossary()
        assert _wait_for(lambda: "读取失败" in window.glossary_status.text())
    finally:
        window.close()


def test_upgrade_dock_uses_existing_actions_and_motion_can_be_reduced(qapp, monkeypatch, tmp_path):
    window = _launch_upgrade_preview(monkeypatch, tmp_path)
    try:
        qapp.processEvents()
        window.tabs.setCurrentWidget(window.runtime_page)
        item, neighbor, _ = window.dock.buttons
        assert item.accessibleName() == "测试字幕"
        assert not item.text() and not item.icon().isNull()
        point = QPointF(4, 4)
        qapp.sendEvent(item, QEnterEvent(point, point, point))
        assert _wait_for(lambda: item.dockOpacity >= 0.99 and neighbor.dockOpacity >= 0.44)
        window.reduce_motion_check.setChecked(True)
        assert item.dockOpacity == neighbor.dockOpacity == 0
        item.click()
        assert window.overlay.entries
        window._caption_state("loading")
        assert not window.caption_loading.pulse.state().value
        window.result_icon.set_mode("done")
        assert window.result_icon.phase == DONE_PHASE
        assert not window.result_icon._timer.isActive()
    finally:
        window.close()



def test_main_subtitle_card_shows_only_two_real_results(qapp, monkeypatch, tmp_path):
    window = _launch_upgrade_preview(monkeypatch, tmp_path)
    try:
        assert window.previous_caption.isHidden()
        lines = ("中路两人。", "守住 B 点。", "转 A 点，安全。")
        for index, line in enumerate(lines):
            window._result(SubtitleResult("callout", line, "en", 0.2, 0.1, 0.35))
            assert window.chinese_label.text() == line
            if index:
                assert window.previous_caption.isVisible()
                assert window.previous_caption.text() == lines[index - 1]
            else:
                assert window.previous_caption.isHidden()
    finally:
        window.close()



def test_bundled_display_fonts_and_equal_width_latency_digits(qapp, monkeypatch, tmp_path):
    window = _launch_upgrade_preview(monkeypatch, tmp_path)
    try:
        window._result(SubtitleResult("Go A", "转 A 点 123", "en", 0.2, 0.1, 0.35))
        for label in (window.chinese_label, window.overlay.label):
            text = QTextLayout(label.text(), label.font())
            text.beginLayout()
            text.createLine()
            text.endLayout()
            faces = {(run.rawFont().familyName(), run.rawFont().styleName()) for run in text.glyphRuns()}
            assert ("Rajdhani", "Bold") in faces
            assert ("Noto Sans CJK SC", "Black") in faces
        metrics = window.latency_stat.fontMetrics()
        assert len({metrics.horizontalAdvance(str(digit)) for digit in range(10)}) == 1
    finally:
        window.close()


def test_quick_start_uses_persisted_chinese_history_and_opens_existing_page(qapp, monkeypatch, tmp_path):
    window = _launch_upgrade_preview(monkeypatch, tmp_path)
    try:
        assert "暂无翻译历史" in window.recent_hint.text()
        for index in range(6):
            window.history.add_result(SubtitleResult("callout", f"队友报点 {index}", "en",
                                                     0.1, 0.1, index / 10))
        latest = window.history.recent(1)[0]
        window.history.correct(latest.id, "已修正的译文", apply_future=False)
        window.history.add_result(SubtitleResult("转 A", "Rotate A", "zh", 0.1, 0.1, 0.2,
                                                 target_language="en"))
        window._refresh_history()
        assert window.chinese_label.text() == "已修正的译文"
        assert window.latency_stat.text() == "0.50 秒"
        assert window.previous_caption.text() == "队友报点 4"
        assert window.previous_latency.text() == "0.40 秒"
        assert window.recent_list.topLevelItemCount() == 3
        assert [window.recent_list.topLevelItem(i).text(0) for i in range(3)] == [
            "已修正的译文", "队友报点 4", "队友报点 3",
        ]
        assert window.history_table.rowCount() == 7
        assert window.recent_list.topLevelItem(0).text(1) == latest.created_at[11:19]
        item = window.recent_list.topLevelItem(0)
        window.recent_list.itemClicked.emit(item, 0)
        assert window.tabs.currentWidget() is window.history_page
        assert window._selected_history_id() == latest.id
        window.history.clear_history()
        window._refresh_history()
        assert window.chinese_label.isHidden() and window.caption_loading.isHidden()
        assert window.caption_hint.text() == "开始翻译后显示字幕"
        assert window.recent_list.isHidden()
    finally:
        window.close()


def test_quick_start_has_no_dialog_before_first_live_caption(qapp, monkeypatch, tmp_path):
    from valorant_translator.audio.capture import AudioDevice
    from valorant_translator.ui import app as ui

    window = _launch_upgrade_preview(monkeypatch, tmp_path)
    warnings = []
    monkeypatch.setattr(ui.QMessageBox, "warning", lambda *args: warnings.append(args))
    monkeypatch.setattr(window, "_models_ready", lambda: True)
    monkeypatch.setattr(window, "_torch_version_ok", lambda: True)
    try:
        window.start_button.click()
        assert "刷新设备" in window.caption_hint.text()
        assert not warnings
        window._apply_devices([AudioDevice("speaker", "默认回环", True)])

        class TestPipeline:
            diagnostics = None
            failures = 0

            def __init__(self, settings, on_status, on_level, on_result, *args, **kwargs):
                self.on_result = on_result

            def start(self):
                self.on_result(SubtitleResult("A main", "A 大。", "en", 0.2, 0.1, 0.35))

            def stop(self):
                pass

        monkeypatch.setattr(ui, "Pipeline", TestPipeline)
        window.start_button.click()
        assert not warnings
        assert window.chinese_label.text() == "A 大。"
        assert window.latency_stat.text() == "0.35 秒" and window.previous_latency.isHidden()
        assert not window.caption_loading.isVisible()
        assert window.caption_light.animation.duration() == 200
        assert window.caption_light.opacity < 1
        assert _wait_for(lambda: window.caption_light.opacity == 1)
    finally:
        window.close()


def test_onboarding_first_run_skip_reopen_and_finish(qapp, monkeypatch, tmp_path):
    from valorant_translator.audio.capture import AudioDevice
    from valorant_translator.config import Settings
    from valorant_translator.ui import app as ui

    window = _launch_upgrade_preview(monkeypatch, tmp_path)
    monkeypatch.setattr(window, "_show_routing_intro", lambda: pytest.fail("Welcome must open first"))
    try:
        assert not window.settings.onboarding_done
        QTimer.singleShot(0, window._show_first_run)
        assert _wait_for(lambda: window.onboarding is not None and window.onboarding.isVisible())
        guide = window.onboarding
        assert guide.step_status.text() == "第 1 步，共 3 步"
        assert "队友的语音实时翻成字幕" in guide.details.text()
        assert guide.context.isHidden()
        guide.skip.click()
        assert window.onboarding is None and Settings.load().onboarding_done
    finally:
        window.close()

    reopened = ui.MainWindow()
    try:
        reopened.show()
        reopened._show_first_run()
        qapp.processEvents()
        assert reopened.onboarding is None
        reopened.tabs.setCurrentWidget(reopened.settings_page)
        reopened.intro_button.click()
        guide = reopened.onboarding
        assert guide.step_status.text() == "第 1 步，共 3 步"
        monkeypatch.setattr(reopened, "_request_devices", lambda: reopened.devices_refreshed.emit(True))
        monkeypatch.setattr(reopened, "_models_ready", lambda: True)
        guide.action.click()
        assert guide.step_status.text() == "第 2 步，共 3 步"
        guide.action.click()
        assert _wait_for(lambda: guide.step == 2)
        assert guide.step_status.text() == "第 3 步，共 3 步"
        assert "输入：" not in guide.context.text()
        assert "麦克风：" not in guide.context.text()
        reopened._apply_devices([AudioDevice("speaker", "扬声器", True)])
        guide._refresh()
        assert "扬声器" in guide.context.text()
        assert "麦克风：" not in guide.context.text()
        assert guide.skip.isVisible() and guide.finish.isVisible()
        guide.finish.click()
        assert reopened.onboarding is None
        assert reopened.tabs.currentWidget() is reopened.quick_page
        assert reopened.start_button.isVisible()
    finally:
        reopened.close()



def test_quick_start_fits_two_bilingual_captions_and_run_controls_at_default_size(qapp, monkeypatch, tmp_path):
    from valorant_translator.audio.capture import AudioDevice
    from valorant_translator.ui import app as ui

    monkeypatch.setattr(ui, "model_ready", lambda *_: True)
    window = _launch_upgrade_preview(monkeypatch, tmp_path)
    try:
        window._apply_devices([AudioDevice("speaker", "系统回环播放设备", True)])
        scroll = window.quick_page
        viewport = scroll.viewport()

        def check_quick_start():
            qapp.processEvents()
            assert scroll.verticalScrollBar().maximum() == 0
            assert scroll.horizontalScrollBar().maximum() == 0
            assert not scroll.verticalScrollBar().isVisible()
            for control in (window.hero, window.quick_stats, window.quick_actions,
                            window.start_button, window.stop_button):
                assert control.isVisible()
                assert viewport.rect().contains(QRect(control.mapTo(viewport, QPoint()), control.size()))
            assert not window.hero.geometry().intersects(window.quick_stats.geometry())
            assert not window.quick_stats.geometry().intersects(window.quick_actions.geometry())

        assert window.size().toTuple() == (1160, 840)
        check_quick_start()
        assert window.quick_count.text() == "0"
        assert window.quick_latency.text() == "—"
        assert window.quick_mode.text() == "平衡"
        window.pipeline = SimpleNamespace(failures=0, stop=lambda: None)
        window._status("正在翻译（识别 cpu，翻译 cpu）")
        window._runtime("识别 CPU · 翻译 CPU · 低延迟模式 · Whisper small")
        window._diagnostic("翻译已显示在字幕窗口")
        window._show_audio_level(0.2)
        assert window.quick_volume.value() == 20
        assert window.quick_audio_icon.mode == "pulse"
        window._show_audio_level(.001)
        assert window.quick_volume.value() == 0
        assert window.quick_audio_icon.mode == "idle"
        assert not window.quick_audio_icon._timer.isActive()
        for original, line in (
            ("I heard footsteps behind us near the doorway. Hold B and don't rotate yet.",
             "我听到我们身后门口有脚步声。守住 B 点，先别转点。"),
            ("There are three enemies at the back of A and one at mid stairs. Don't peek from the left.",
             "A 后点有三个敌人，中路楼梯还有一个。别从左边露头。"),
        ):
            result = SubtitleResult(original, line, "en", 0.2, 0.1, 0.5)
            window.history.add_result(result)
            window._result(result)
        window._record_trace(TaskTrace(5, "结果丢弃", "语音太短", discarded=True))
        check_quick_start()
        assert "mid stairs" in window.original_label.text()
        assert "footsteps behind us" in window.previous_original.text()
        assert window.previous_original.isVisible()
        assert window.quick_count.text() == "2"
        assert window.quick_latency.text() == "0.50 秒"
        window._trace_token = token = object()
        window.bridge.effective_mode.emit(token, "local_fallback")
        assert window.quick_mode.text() == "本地回退"
        window.bridge.effective_mode.emit(object(), "extreme")
        assert window.quick_mode.text() == "本地回退"
        for page, widgets in (
            (window.audio_page, (window.device_combo, window.refresh_button, window.mode_combo,
                                 window.test_button, window.show_button, window.position_button,
                                 window.talk_button, window.reverse_copy)),
            (window.settings_page, (window.download_button, window.import_button,
                                    window.settings_update_button, window.add_term_button)),
            (window.history_page, (window.recent_list, window.history_table)),
            (window.runtime_page, (window.volume, window.feedback_button, window.dock,
                                   window.detail_toggle, window.runtime_label)),
        ):
            assert all(page.isAncestorOf(widget) and not widget.isVisible() for widget in widgets)
            window.tabs.setCurrentWidget(page)
            qapp.processEvents()
            assert all(widget.isVisible() for widget in widgets)
            window.tabs.setCurrentWidget(scroll)
    finally:
        window.close()


def test_runtime_progress_and_discard_have_separate_wrapping_rows(qapp, monkeypatch, tmp_path):
    window = _launch_upgrade_preview(monkeypatch, tmp_path)
    try:
        window.tabs.setCurrentWidget(window.runtime_page)
        window._runtime("识别 CUDA · 翻译 CUDA · 低延迟模式 · Whisper tiny")
        window._diagnostic("正在识别上一句，队列约 4 句；处理较慢，请等待本句识别返回并查看详细诊断。")
        window._record_trace(TaskTrace(28, "结果丢弃", "识别完成已过期：距音频结束 17.72 秒，本类 28 次",
                                       discarded=True))
        qapp.processEvents()
        labels = (window.runtime_label, window.diagnostic_label, window.last_discard)
        assert all(label.isVisible() and label.wordWrap() for label in labels)
        assert all(label.textFormat() == Qt.TextFormat.PlainText for label in labels)
        for upper, lower in zip(labels, labels[1:]):
            assert upper.width() == lower.width()
            assert lower.mapTo(window, QPoint()).y() - (upper.mapTo(window, QPoint()).y() + upper.height()) >= 8
        assert "真实麦克风" in window.mic_combo.toolTip()
    finally:
        window.close()


def test_quick_start_shows_live_pipeline_progress_without_changing_tabs(qapp, monkeypatch, tmp_path):
    from threading import Event

    import numpy as np

    from valorant_translator import pipeline
    from valorant_translator.audio.capture import AudioDevice
    from valorant_translator.audio.segmenter import SpeechChunk
    from valorant_translator.recognition.whisper import LocalWhisper
    from valorant_translator.ui import app as ui

    finish_recognition = Event()
    finish_translation = Event()
    second_recognition_started = Event()
    finish_second_recognition = Event()

    class Decoder:
        def __init__(self):
            self.calls = 0

        def transcribe(self, *_args, **_kwargs):
            self.calls += 1

            def segments():
                if self.calls == 1:
                    yield SimpleNamespace(text="Wait near", no_speech_prob=0.1, avg_logprob=-0.2)
                    assert finish_recognition.wait(3)
                    yield SimpleNamespace(text="the entrance", no_speech_prob=0.1, avg_logprob=-0.2)
                else:
                    yield SimpleNamespace(text="A", no_speech_prob=0.1, avg_logprob=-0.2)
                    second_recognition_started.set()
                    assert finish_second_recognition.wait(5)
            return segments(), SimpleNamespace(language="en", language_probability=0.99)

    class Translator:
        device = "cpu"

        def translate(self, text, language):
            assert (text, language) == ("Wait near the entrance", "en")
            assert finish_translation.wait(3)
            return "在入口附近等。"

    class Capture:
        def __init__(self, device_id, on_audio, on_level, on_status):
            self.on_level = on_level
            self.on_status = on_status

        def start(self):
            self.on_status("音频设备已连接，正在监听")

        def stop(self):
            pass

    asr = LocalWhisper.__new__(LocalWhisper)
    asr.device, asr.model = "cpu", Decoder()
    manager = SimpleNamespace(create_asr=lambda _: asr, create_translation=lambda _: Translator())
    monkeypatch.setattr(pipeline, "AudioCapture", Capture)
    monkeypatch.setattr(pipeline, "default_model_manager", lambda *_args, **_kwargs: manager)
    monkeypatch.setattr(ui.MainWindow, "_torch_version_ok", lambda self: True)
    monkeypatch.setattr(ui.MainWindow, "_models_ready", lambda self: True)
    window = _launch_upgrade_preview(monkeypatch, tmp_path)
    try:
        window._apply_devices([AudioDevice("mic", "测试麦克风", False)])
        qapp.processEvents()
        stats_geometry = window.quick_stats.geometry()
        audio_geometry = window.quick_audio_icon.geometry()
        assert window.live_phase.text() == "空闲"
        assert window.live_sentence.text() == "等待说话"
        assert window.live_audio.isHidden()
        assert window.live_backlog.isHidden()

        window.start_button.click()
        assert _wait_for(lambda: window.live_phase.text() == "正在听")
        active = window.pipeline
        active.capture.on_level(0.34)
        assert window.live_audio.value() == 34 and window.live_audio.isVisible()
        assert window.live_audio.accessibleName() == "麦克风音量"

        now = time.monotonic()
        chunk = SpeechChunk(np.zeros(16000, dtype=np.float32), now - 1, now)
        active.enqueue(chunk)
        assert _wait_for(lambda: window.live_phase.text() == "正在识别"
                         and window.live_sentence.text() == "Wait near")
        active.enqueue(chunk)
        assert _wait_for(lambda: window.live_backlog.text() == "待处理 1 段"
                         and window.live_backlog.isVisible())
        finish_recognition.set()
        assert _wait_for(lambda: second_recognition_started.is_set()
                         and window.live_phase.text() == "正在翻译"
                         and window.live_sentence.text() == "Wait near the entrance")
        assert window.live_backlog.isHidden()
        finish_translation.set()
        assert _wait_for(lambda: window.live_phase.text() == "已出字幕"
                         and window.live_sentence.text() == "在入口附近等。")
        assert window.live_backlog.isHidden()
        assert window.tabs.currentWidget() is window.quick_page
        assert window.quick_page.verticalScrollBar().maximum() == 0
        assert window.quick_stats.geometry() == stats_geometry
        assert window.quick_audio_icon.geometry() == audio_geometry
        window.resize(920, 640)
        qapp.processEvents()
        viewport = window.quick_page.viewport()
        for label in (window.live_phase, window.live_sentence, window.live_audio):
            assert viewport.rect().contains(QRect(label.mapTo(viewport, QPoint()), label.size()))
        assert _wait_for(lambda: window.live_phase.text() == "正在识别" and window.live_sentence.text() == "A")
        finish_second_recognition.set()
        assert _wait_for(lambda: window.live_phase.text() == "正在听")
        assert window.live_sentence.text() == "等待说话"

        token = window._trace_token
        window.stop_button.click()
        window.bridge.live_activity.emit(token, 9, "recognizing", "previous run")
        window.bridge.level.emit(token, 0.99)
        assert window.live_phase.text() == "空闲"
        assert window.live_sentence.text() == "等待说话"
        assert window.live_audio.isHidden()
        assert window.live_backlog.isHidden()
    finally:
        finish_recognition.set()
        finish_second_recognition.set()
        finish_translation.set()
        window.close()


def test_startup_waits_for_finished_update_and_offers_recovery(qapp, monkeypatch, tmp_path):
    import psutil

    from valorant_translator.ui import app as ui
    from valorant_translator.updates import github, windows

    log = tmp_path / "updates" / "last_result.txt"
    installer = log.parent / "0.6.1" / github.CPU_INSTALLER_NAME
    installer.parent.mkdir(parents=True)
    installer.touch()
    pending = log.with_suffix(".pending.txt")
    pending.write_text(f"安装器路径：{installer}\n准备启动更新器", encoding="utf-8")
    marker = log.with_suffix(".lock")
    process = psutil.Process()
    marker.write_text(f"{process.pid} {process.create_time()}", encoding="ascii")
    monkeypatch.setattr(windows, "RESULT", log)

    opened, retries, dialogs, warnings = [], [], [], []
    monkeypatch.setattr(ui.os, "startfile", opened.append, raising=False)
    monkeypatch.setattr(ui.MainWindow, "_check_update", lambda self: retries.append(True))
    monkeypatch.setattr(ui.QMessageBox, "warning", lambda _, title, message: warnings.append(message))
    choice = ["打开安装器"]
    exec_box = ui.QMessageBox.exec

    def exec_dialog(box):
        dialogs.append((box.text(), box.detailedText()))
        assert box.textFormat() == Qt.TextFormat.PlainText
        buttons = {button.text(): button for button in box.buttons()}
        assert {"打开安装器", "重试更新"} <= buttons.keys()
        QTimer.singleShot(0, box, buttons[choice[0]].click)
        QTimer.singleShot(1000, box, box.reject)
        return exec_box(box)

    monkeypatch.setattr(ui.QMessageBox, "exec", exec_dialog)
    window = _launch_upgrade_preview(monkeypatch, tmp_path)
    try:
        qapp.processEvents()
        assert not dialogs
        assert "准备启动更新器" in pending.read_text(encoding="utf-8")
        assert marker.is_file()
        with pending.open("a", encoding="utf-8") as output:
            output.write("\n更新失败：安装器返回错误代码 7")
        pending.replace(log)
        marker.unlink()

        assert _wait_for(lambda: bool(opened))
        summary, details = dialogs[0]
        assert "安装器返回错误代码 7" in summary
        assert str(installer) in summary
        assert details == log.read_text(encoding="utf-8")
        assert opened == [installer]

        choice[0] = "重试更新"
        window._show_update_result()
        assert retries == [True]

        choice[0] = "打开安装器"
        installer.unlink()
        window._show_update_result()
        assert len(warnings) == 1 and "找不到安装器" in warnings[0]
        assert opened == [installer]
        assert "安装器返回错误代码 7" in log.read_text(encoding="utf-8")
    finally:
        window.close()
