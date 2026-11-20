"""Shared styling and Qt-only dialogs for VoiceTrans."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QInputDialog, QLabel, QMessageBox)

from valorant_translator.ui.glass import DEFAULT_ACCENT, background_palette, glass_styles


_BUTTON_TEXT = {"Ok": "确定", "Cancel": "取消", "Save": "保存", "Close": "关闭", "Open": "打开",
                "Yes": "是", "No": "否", "Apply": "应用", "Retry": "重试"}


def localize_buttons(dialog):
    for box in dialog.findChildren(QDialogButtonBox):
        for button in box.buttons():
            label = _BUTTON_TEXT.get(box.standardButton(button).name)
            if label:
                button.setText(label)


def apply_dialog_theme(dialog, settings=None, *, painted_surface=False):
    parent = dialog.parentWidget()
    while settings is None and parent is not None:
        settings = getattr(parent, "settings", None)
        parent = parent.parentWidget()
    accent = getattr(settings, "accent", DEFAULT_ACCENT)
    colors = background_palette(getattr(settings, "background_theme", "default"),
                                getattr(settings, "background_color", ""))
    palette = dialog.palette()
    for role, color in (
        (QPalette.ColorRole.Window, colors.top),
        (QPalette.ColorRole.Base, "#181b2a"),
        (QPalette.ColorRole.AlternateBase, "#222737"),
        (QPalette.ColorRole.Button, "#30374a"),
        (QPalette.ColorRole.WindowText, "#f0edf2"),
        (QPalette.ColorRole.Text, "#f0edf2"),
        (QPalette.ColorRole.ButtonText, "#f0edf2"),
        (QPalette.ColorRole.PlaceholderText, "#bdc3d2"),
        (QPalette.ColorRole.Highlight, "#475b7a"),
        (QPalette.ColorRole.HighlightedText, "#fff9fe"),
        (QPalette.ColorRole.ToolTipBase, "#202436"),
        (QPalette.ColorRole.ToolTipText, "#f0edf2"),
    ):
        palette.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor("#a0a4ad"))
    dialog.setPalette(palette)
    dialog.setAutoFillBackground(True)

    style = glass_styles(accent)
    if not painted_surface:
        dialog.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # QSS foreground colors alone make text disappear against Windows' light default dialog surface.
        style += f"""
            QDialog, QMessageBox, QColorDialog {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {colors.top},stop:1 {colors.bottom});
            }}
            QPlainTextEdit, QTextEdit, QAbstractItemView {{
                background: #181b2a; alternate-background-color: #222737;
                color: #f0edf2; selection-background-color: #475b7a; selection-color: #fff9fe;
                border: 1px solid #535c72; border-radius: 8px;
            }}
            QAbstractItemView::item {{
                padding: 3px;
            }}
            QPushButton:default {{
                border-color: #a3b4cf;
            }}
            QToolTip {{
                background: #202436; color: #f0edf2; border: 1px solid #535c72;
            }}
        """
    dialog.setStyleSheet(style)
    localize_buttons(dialog)


class GlassMessageBox(QMessageBox):
    def __init__(self, parent):
        super().__init__(parent)
        self.setOption(QMessageBox.Option.DontUseNativeDialog)
        self.setTextFormat(Qt.TextFormat.PlainText)
        apply_dialog_theme(self)

    def setWindowTitle(self, title):
        super().setWindowTitle(title)
        self.setAccessibleName(title)

    def setStandardButtons(self, buttons):
        super().setStandardButtons(buttons)
        localize_buttons(self)

    @classmethod
    def _message(cls, parent, title, text, icon, buttons, default):
        box = cls(parent)
        box.setIcon(icon)
        box.setWindowTitle(title)
        box.setText(text)
        box.setStandardButtons(buttons)
        box.setDefaultButton(default)
        try:
            return box.exec()
        finally:
            box.deleteLater()

    @classmethod
    def information(cls, parent, title, text):
        return cls._message(parent, title, text, cls.Icon.Information, cls.StandardButton.Ok, cls.StandardButton.Ok)

    @classmethod
    def warning(cls, parent, title, text):
        return cls._message(parent, title, text, cls.Icon.Warning, cls.StandardButton.Ok, cls.StandardButton.Ok)

    @classmethod
    def question(cls, parent, title, text, buttons=None):
        buttons = buttons or (cls.StandardButton.Yes | cls.StandardButton.No)
        return cls._message(parent, title, text, cls.Icon.Question, buttons, cls.StandardButton.No)


class GlassInputDialog(QInputDialog):
    @classmethod
    def _prompt(cls, parent, title, label):
        dialog = cls(parent)
        dialog.setWindowTitle(title)
        dialog.setLabelText(label)
        for widget in dialog.findChildren(QLabel):
            widget.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setOkButtonText("确定")
        dialog.setCancelButtonText("取消")
        apply_dialog_theme(dialog)
        return dialog

    @classmethod
    def getMultiLineText(cls, parent, title, label, text=""):
        dialog = cls._prompt(parent, title, label)
        dialog.setOption(cls.InputDialogOption.UsePlainTextEditForTextInput)
        dialog.setTextValue(text)
        dialog.resize(620, 315)
        return cls._value(dialog)

    @classmethod
    def getItem(cls, parent, title, label, items, *, editable=False):
        dialog = cls._prompt(parent, title, label)
        dialog.setComboBoxItems(items)
        dialog.setComboBoxEditable(editable)
        dialog.setTextValue(items[0] if items else "")
        return cls._value(dialog)

    @staticmethod
    def _value(dialog):
        try:
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
            return (dialog.textValue() if accepted else "", accepted)
        finally:
            dialog.deleteLater()


class GlassFileDialog(QFileDialog):
    @classmethod
    def _choose(cls, parent, title, initial, filters, mode, *, save=False):
        picker = cls(parent)
        picker.setOption(cls.Option.DontUseNativeDialog)
        picker.setWindowTitle(title)
        picker.setAcceptMode(cls.AcceptMode.AcceptSave if save else cls.AcceptMode.AcceptOpen)
        picker.setFileMode(mode)
        picker.setOption(cls.Option.ShowDirsOnly, mode == cls.FileMode.Directory)
        picker.setOption(cls.Option.DontConfirmOverwrite, save)
        if initial:
            if Path(initial).is_dir():
                picker.setDirectory(initial)
            else:
                picker.selectFile(initial)
        if filters:
            picker.setNameFilters(filters.split(";;"))
        apply_dialog_theme(picker)
        for name, label in (("LookIn", "位置"), ("FileName", "文件名"), ("FileType", "文件类型"),
                            ("Accept", "保存" if save else "选择"), ("Reject", "取消")):
            picker.setLabelText(cls.DialogLabel[name], label)
        try:
            while picker.exec() == QDialog.DialogCode.Accepted:
                selected = picker.selectedFiles()
                if not selected:
                    break
                path = selected[0]
                if not save or not Path(path).exists() or GlassMessageBox.question(
                    parent, "覆盖已有文件", "所选文件已存在。是否覆盖？"
                ) == QMessageBox.StandardButton.Yes:
                    return path, picker.selectedNameFilter()
            return "", ""
        finally:
            picker.deleteLater()

    @classmethod
    def getOpenFileName(cls, parent, title, initial="", filters=""):
        return cls._choose(parent, title, initial, filters, cls.FileMode.ExistingFile)

    @classmethod
    def getSaveFileName(cls, parent, title, initial="", filters=""):
        return cls._choose(parent, title, initial, filters, cls.FileMode.AnyFile, save=True)

    @classmethod
    def getExistingDirectory(cls, parent, title):
        return cls._choose(parent, title, "", "", cls.FileMode.Directory)[0]
