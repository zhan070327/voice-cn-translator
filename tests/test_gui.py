from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from valorant_translator.config import Settings
from valorant_translator.pipeline import SubtitleResult
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
    assert "当前版本" in window.update_label.text()
    window._runtime("识别 CUDA · 翻译 CPU · 低延迟模式")
    assert "翻译 CPU" in window.runtime_label.text()
    monkeypatch.setattr(ui.QMessageBox, "information", lambda *args: None)
    window._copy_feedback()
    feedback = QApplication.clipboard().text()
    assert "识别 CUDA · 翻译 CPU" in feedback
    assert "实际听到的话：请填写" in feedback
    warnings = []
    monkeypatch.setattr(ui.QMessageBox, "warning", lambda *args: warnings.append(args))
    window.start_translation()
    assert warnings and "下载模型" in warnings[0][1]
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
    window.close()


def test_gui_detects_incompatible_torch_version(monkeypatch):
    import valorant_translator.ui.app as ui
    monkeypatch.setattr(ui, "version", lambda name: "2.5.1" if name == "torch" else "0")
    assert not ui.MainWindow._torch_version_ok()
    monkeypatch.setattr(ui, "version", lambda name: "2.6.0" if name == "torch" else "0")
    assert ui.MainWindow._torch_version_ok()
