from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QGroupBox

from valorant_translator.config import Settings
from valorant_translator.pipeline import SubtitleResult, TaskTrace
from valorant_translator.ui.glass import GlassButton
from valorant_translator.ui.overlay import SubtitleOverlay


def test_overlay_keeps_two_and_expires(qapp):
    overlay = SubtitleOverlay(Settings(subtitle_seconds=0.01))
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
    assert not window.start_button.isEnabled()
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
    assert window.tabs.tabText(2) == "翻译历史"
    window.history.add_result(SubtitleResult("Go rush A", "快攻 A。", "en", 0.2, 0.1, 0.3))
    window._refresh_history()
    assert window.history_table.item(0, 1).text() == "Go rush A"
    assert window.fast_check.isChecked()
    assert window.update_button.text() == "检查更新"
    assert window.settings_update_button.text() == "检查更新"
    assert "当前版本" in window.update_label.text()
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
    window.start_translation()
    assert warnings and "下载模型" in warnings[0][1]
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
    settings_groups = window.tabs.widget(1).findChildren(QGroupBox)
    assert [group.title() for group in settings_groups] == ["软件更新", "识别和翻译", "字幕", "历史和隐私"]
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
    button = GlassButton("测试")
    button.show()
    hit_target = button.geometry()
    QTest.mousePress(button, Qt.MouseButton.LeftButton)
    QTest.qWait(110)
    assert abs(button.visualScale - 0.97) < 0.001
    assert button.geometry() == hit_target
    QTest.mouseRelease(button, Qt.MouseButton.LeftButton)
    QTest.qWait(110)
    assert abs(button.visualScale - 1.0) < 0.001
    button.close()


def test_gui_detects_incompatible_torch_version(monkeypatch):
    import valorant_translator.ui.app as ui
    monkeypatch.setattr(ui, "version", lambda name: "2.5.1" if name == "torch" else "0")
    assert not ui.MainWindow._torch_version_ok()
    monkeypatch.setattr(ui, "version", lambda name: "2.6.0" if name == "torch" else "0")
    assert ui.MainWindow._torch_version_ok()
