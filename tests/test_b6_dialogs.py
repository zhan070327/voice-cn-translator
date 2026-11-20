from collections import deque

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QFileDialog, QFormLayout, QInputDialog,
                               QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QTableWidget)

from valorant_translator import config
from valorant_translator.audio.capture import AudioDevice
from valorant_translator.feedback import FeedbackStore
from valorant_translator.pipeline import SubtitleResult
from valorant_translator.translation import glossary
from valorant_translator.translation.rules import RuleStore
from valorant_translator.ui import app as ui


@pytest.fixture
def window(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(ui, "MODEL_DIR", tmp_path / "models")
    monkeypatch.setattr(ui, "load_channel", lambda: None)
    monkeypatch.setattr(ui, "list_devices", lambda: [
        AudioDevice("system", "测试耳机", True), AudioDevice("sonar", "SteelSeries Sonar - Chat", True),
    ])
    monkeypatch.setattr(ui.MainWindow, "_start_hotkey", lambda self: None)
    monkeypatch.setattr(ui.MainWindow, "_request_devices", lambda self: None)
    config.Settings(routing_intro_seen=True).save()
    widget = ui.MainWindow()
    try:
        yield widget
    finally:
        widget.close()
        widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.processEvents()


def assert_dark(dialog):
    assert dialog.isVisible()
    assert dialog.grab().toImage().pixelColor(4, 4).lightness() < 75
    assert "QDialog" in dialog.styleSheet()
    palette = dialog.palette()
    for background, foreground in ((QPalette.ColorRole.Window, QPalette.ColorRole.WindowText),
                                   (QPalette.ColorRole.Base, QPalette.ColorRole.Text)):
        assert palette.color(background).lightness() < 75
        assert palette.color(foreground).lightness() > 182


def button(window, text):
    control = next(b for b in window.findChildren(QPushButton) if b.text() == text and b.isVisible())
    assert control.isEnabled()
    return control


def labels(window):
    return "\n".join(label.text() for label in window.findChildren(QLabel) if label.isVisible())


def run_dialogs(action, *steps):
    """Drive real Qt dialogs, including nested modal loops, with a bounded failure path."""
    remaining = deque(steps)
    errors = []
    tick = QTimer()
    tick.setSingleShot(True)
    watchdog = QTimer()
    watchdog.setSingleShot(True)

    def abort(error):
        errors.append(error)
        tick.stop()
        for dialog in QApplication.topLevelWidgets():
            if isinstance(dialog, QDialog) and dialog.isVisible():
                dialog.reject()

    def advance():
        if not remaining:
            return
        title = remaining[0][0]
        matches = [widget for widget in QApplication.topLevelWidgets()
                   if isinstance(widget, QDialog) and widget.isVisible()
                   and title in (widget.windowTitle(), widget.accessibleName())]
        tick.start(15)
        if not matches:
            return
        _, callback = remaining.popleft()
        try:
            assert_dark(matches[0])
            callback(matches[0])
        except Exception as exc:
            abort(exc)

    tick.timeout.connect(advance)
    watchdog.timeout.connect(lambda: abort(AssertionError(f"Dialog timed out: {[title for title, _ in remaining]}")))
    watchdog.start(4500)
    tick.start(0)
    try:
        action()
    finally:
        tick.stop()
        watchdog.stop()
    if errors:
        raise errors[0]
    assert not remaining, [title for title, _ in remaining]


def test_default_catalog_sections_scroll_and_show_read_error(window, monkeypatch, tmp_path):
    def verify(dialog):
        editor = dialog.findChild(QPlainTextEdit)
        content = editor.toPlainText()
        assert editor.isReadOnly() and editor.verticalScrollBar().maximum() > 0
        for text in ("整句译法", "地图位置", "识别纠错与台词规则", "two a main", "A 大两个。"):
            assert text in content
        button(dialog, "关闭").click()

    run_dialogs(lambda: ui.default_glossary(window), ("默认词库", verify))

    missing = tmp_path / "missing-glossary.json"
    monkeypatch.setattr(glossary, "GLOSSARY_FILE", missing)

    def verify_missing(dialog):
        content = dialog.findChild(QPlainTextEdit).toPlainText()
        assert "默认词库无法读取" in content and missing.name in content
        dialog.reject()

    run_dialogs(lambda: ui.default_glossary(window), ("默认词库", verify_missing))


def test_manage_rules_empty_add_edit_and_delete(window):
    def empty(dialog):
        assert "暂无自定义词条" in labels(dialog)
        assert dialog.findChild(QTableWidget).rowCount() == 0
        button(dialog, "添加").click()

    def fill_rule(dialog, original, translated):
        form = dialog.findChild(QFormLayout)
        values = {"原文": original, "译文": translated}
        for row in range(form.rowCount()):
            label = form.itemAt(row, QFormLayout.ItemRole.LabelRole).widget()
            if label.text() in values:
                form.itemAt(row, QFormLayout.ItemRole.FieldRole).widget().setText(values[label.text()])
        button(dialog, "保存").click()

    def edit_rule(dialog):
        table = dialog.findChild(QTableWidget)
        assert table.item(0, 2).text() == "smoke A"
        assert table.item(0, 3).text() == "往 A 点封烟。"
        table.selectRow(0)
        button(dialog, "编辑").click()

    def delete_rule(dialog):
        table = dialog.findChild(QTableWidget)
        assert table.item(0, 3).text() == "A 点给烟。"
        table.selectRow(0)
        button(dialog, "删除").click()

    def deleted(dialog):
        assert dialog.findChild(QTableWidget).rowCount() == 0
        assert "暂无自定义词条" in labels(dialog)
        dialog.reject()

    run_dialogs(lambda: ui.manage_rules(window, window.custom_rules),
                ("自定义词库", empty),
                ("编辑词条", lambda w: fill_rule(w, "smoke A", "往 A 点封烟。")),
                ("自定义词库", edit_rule),
                ("编辑词条", lambda w: fill_rule(w, "smoke A", "A 点给烟。")),
                ("自定义词库", delete_rule),
                ("删除词条", lambda w: button(w, "是").click()),
                ("自定义词库", deleted))
    assert not window.custom_rules.rules


def test_feedback_import_filter_review_and_export_to_glossary(window, tmp_path):
    window.history.add_result(SubtitleResult("Guard B", "守卫 B。", "en", .1, .1, .2))
    entry = window.history.recent()[0]
    sender = FeedbackStore(tmp_path / "sender.json")
    sender.add(entry, "守住 B 点。", "游戏术语错误", "")
    sender.add(entry, "守 B。", "普通翻译错误", "")
    incoming = tmp_path / "feedback.json"
    candidates = tmp_path / "approved.json"
    sender.export(incoming)

    def empty(dialog):
        assert "暂无反馈" in labels(dialog)
        assert dialog.findChild(QTableWidget).rowCount() == 0
        button(dialog, "导入反馈").click()

    def select_path(dialog, path):
        assert isinstance(dialog, QFileDialog)
        assert dialog.testOption(QFileDialog.Option.DontUseNativeDialog)
        dialog.setDirectory(str(path.parent))
        dialog.findChild(QLineEdit, "fileNameEdit").setText(path.name)
        button(dialog, "保存" if dialog.acceptMode() == QFileDialog.AcceptMode.AcceptSave else "选择").click()

    def review(dialog):
        table = dialog.findChild(QTableWidget)
        assert table.rowCount() == 2
        category = dialog.findChild(QComboBox)
        category.setCurrentText("游戏术语错误")
        assert table.rowCount() == 1 and table.item(0, 2).text() == "Guard B"
        assert table.item(0, 3).text() == "守住 B 点。"
        table.selectRow(0)
        button(dialog, "通过").click()
        assert table.item(0, 0).text() == "Approved"
        category.setCurrentText("普通翻译错误")
        table.selectRow(0)
        button(dialog, "拒绝").click()
        assert table.item(0, 0).text() == "Rejected"
        button(dialog, "导出已审核词库候选").click()

    def check_export(dialog):
        content = dialog.findChild(QPlainTextEdit).toPlainText()
        assert "守住 B 点。" in content and "守 B。" not in content
        button(dialog, "确定").click()

    run_dialogs(window._review_feedback, ("本地反馈与审核", empty),
                ("导入反馈", lambda d: select_path(d, incoming)), ("本地反馈与审核", review),
                ("分享前检查", check_export), ("导出文件", lambda d: select_path(d, candidates)),
                ("本地反馈与审核", lambda d: d.reject()))
    custom = RuleStore(tmp_path / "approved-user-terms")
    custom.import_file(candidates)
    assert [rule.translated for rule in custom.rules] == ["守住 B 点。"]
    assert not window.custom_rules.rules


def test_history_details_submit_feedback_and_save_scoped_correction(window):
    window.history.add_result(SubtitleResult("My Ultimus, Ready!", "我的大招好了。", "en", .2, .1, .3,
        corrected_original="My ultimate is ready.", machine_translation="我的最终准备好了。",
        glossary_rules="asr_correction:my ultimus ready"))
    window._refresh_history()
    window.history_table.selectRow(0)

    def full_details(dialog):
        text = dialog.findChild(QPlainTextEdit).toPlainText()
        for expected in ("时间：", "Valorant", "en → zh", "My Ultimus, Ready!", "My ultimate is ready.",
                         "我的最终准备好了。", "我的大招好了。", "asr_correction:my ultimus ready"):
            assert expected in text
        dialog.reject()

    run_dialogs(window._history_details, ("翻译详情", full_details))

    def send(dialog):
        assert "My Ultimus, Ready!" in labels(dialog) and "我的大招好了。" in labels(dialog)
        dialog.findChild(QLineEdit).setText("我有大了。")
        dialog.findChild(QComboBox).setCurrentText("游戏术语错误")
        dialog.findChild(QPlainTextEdit).setPlainText("测试备注")
        button(dialog, "保存").click()

    run_dialogs(window._feedback_history, ("反馈翻译错误", send))
    [feedback] = window.feedback_store.entries.values()
    assert (feedback["suggested"], feedback["status"], feedback["note"]) == ("我有大了。", "Pending", "测试备注")

    def correction(dialog):
        assert isinstance(dialog, QInputDialog)
        assert "当前译文：我的大招好了。" in dialog.labelText()
        dialog.findChild(QPlainTextEdit).setPlainText("我的大招就绪。")
        button(dialog, "确定").click()

    def scope(dialog):
        assert set(dialog.comboBoxItems()) == {"仅修正本条历史", "保存到我的词库"}
        button(dialog, "确定").click()

    run_dialogs(window._correct_history, ("修正译文", correction), ("修正范围", scope))
    assert window.history.recent()[0].corrected_chinese == "我的大招就绪。"
    assert not window.custom_rules.rules


def test_routing_invitation_and_wizard_have_readable_steps(window, qapp):
    window.settings.routing_intro_seen = False
    window._show_routing_intro()
    qapp.processEvents()
    prompt = next(d for d in window.findChildren(QMessageBox) if d.isVisible())
    assert_dark(prompt)
    assert prompt.testOption(QMessageBox.Option.DontUseNativeDialog)
    assert "单独送到翻译器" in prompt.text()
    button(prompt, "打开分流设置").click()
    qapp.processEvents()
    wizard = window.routing_wizard
    assert_dark(wizard)
    for text in ("Voice Chat", "CABLE Output", "分开测游戏和队友语音"):
        assert text in labels(wizard)
    assert "SteelSeries Sonar - Chat" in wizard.devices.currentText()
    button(wizard, "使用此声音来源").click()
    assert config.Settings.load().device_id == "sonar"
    button(wizard, "完成或跳过").click()


def test_update_prompt_and_model_folder_picker_use_same_theme(window, tmp_path):
    from types import SimpleNamespace

    def decline(dialog):
        assert isinstance(dialog, QMessageBox)
        assert dialog.testOption(QMessageBox.Option.DontUseNativeDialog)
        assert "0.3.0b7" in dialog.text() and "测试更新说明" in dialog.text()
        button(dialog, "否").click()

    run_dialogs(lambda: window._update_checked(
        SimpleNamespace(version="0.3.0b7", source="domestic", notes="测试更新说明")),
        ("发现新版", decline))
    assert not window._update_busy

    chosen = []
    folder = tmp_path / "models"
    folder.mkdir()

    def choose(dialog):
        assert dialog.fileMode() == QFileDialog.FileMode.Directory
        assert dialog.testOption(QFileDialog.Option.DontUseNativeDialog)
        dialog.setDirectory(str(folder))
        dialog.findChild(QLineEdit, "fileNameEdit").clear()
        button(dialog, "选择").click()

    run_dialogs(lambda: chosen.append(ui.QFileDialog.getExistingDirectory(window, "选择模型文件夹")),
                ("选择模型文件夹", choose))
    assert chosen == [str(folder)]
