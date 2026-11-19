from __future__ import annotations

import sys
import time
from dataclasses import dataclass

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QApplication, QFrame, QGraphicsOpacityEffect, QHBoxLayout,
                               QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from valorant_translator.config import Settings


@dataclass(frozen=True)
class Caption:
    translated: str
    original: str
    speaker: str
    expires: float
    reverse: bool


class SubtitleOverlay(QWidget):
    position_changed = Signal(int, int, int)

    def __init__(self, settings: Settings):
        flags = (Qt.WindowType.Window if settings.normal_window else
                 Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                 Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        super().__init__(None, flags)
        self.settings = settings
        self.entries: list[Caption] = []
        self.drag_anchor: QPoint | None = None
        self.setWindowTitle("语音翻译字幕")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, not settings.normal_window)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, not settings.normal_window)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignmentFlag.AlignBottom)
        self.rows = [self._row(layout), self._row(layout)]
        self.label = self.rows[-1][3]
        self.resize(760, 140)
        self.apply_settings()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.expire)
        self.timer.start(100)
        self._render()

    def apply_settings(self) -> None:
        self._style_label(self.settings.font_size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, self.settings.click_through)
        self._native_click_through(self.settings.click_through)
        self._native_topmost()
        screens = QGuiApplication.screens()
        if screens:
            screen = screens[min(self.settings.overlay_screen, len(screens) - 1)]
            available = screen.availableGeometry()
            self.setFixedSize(min(760, max(320, int(available.width() * 0.8))),
                              min(160, max(100, int(available.height() * 0.2))))
            self.move(screen.geometry().topLeft() + QPoint(self.settings.overlay_x, self.settings.overlay_y))
        self._render()

    def _row(self, host):
        panel = QFrame()
        panel.setObjectName("captionCard")
        panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        box = QVBoxLayout(panel)
        box.setContentsMargins(14, 4, 14, 5)
        box.setSpacing(0)
        header = QHBoxLayout()
        badge = QLabel()
        badge.setTextFormat(Qt.TextFormat.PlainText)
        badge.setStyleSheet("color:#b9e5ea; font-size:11px;")
        header.addWidget(badge)
        header.addStretch()
        copy = QPushButton("复制英文")
        copy.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        copy.setMaximumHeight(22)
        copy.clicked.connect(lambda: QApplication.clipboard().setText(copy.property("textToCopy") or ""))
        header.addWidget(copy)
        box.addLayout(header)
        original, translated = QLabel(), QLabel()
        for label in (original, translated):
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.PlainText)
            box.addWidget(label)
        original.setStyleSheet("color:#dbe1ef; font-size:12px;")
        opacity = QGraphicsOpacityEffect(panel)
        panel.setGraphicsEffect(opacity)
        host.addWidget(panel)
        return panel, badge, original, translated, copy, opacity

    def _style_label(self, font_size: int) -> None:
        alpha = max(0, min(255, round(self.settings.overlay_opacity * 2.55)))
        for panel, _, _, label, _, _ in self.rows:
            panel.setStyleSheet(f"QFrame#captionCard {{ background: rgba(31,36,47,{alpha}); "
                                "border: 1px solid rgba(188,235,246,155); border-radius: 17px; }")
            label.setStyleSheet(f"color:white; background:transparent; font-size:{font_size}px; font-weight:700;")

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._native_click_through(self.settings.click_through)
        self._native_topmost()

    def _native_topmost(self) -> None:
        if sys.platform != "win32" or self.settings.normal_window:
            return
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        user32.SetWindowPos.restype = wintypes.BOOL
        # Ordinary Windows topmost window; no game process hooks or injection.
        user32.SetWindowPos(int(self.winId()), -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)

    def _native_click_through(self, enabled: bool) -> None:
        if sys.platform != "win32" or self.settings.normal_window:
            return
        import ctypes
        from ctypes import wintypes
        hwnd = int(self.winId())
        user32 = ctypes.windll.user32
        user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongW.restype = ctypes.c_long
        user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
        user32.SetWindowLongW.restype = ctypes.c_long
        GWL_EXSTYLE, WS_EX_TRANSPARENT, WS_EX_LAYERED = -20, 0x20, 0x80000
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                              (style | WS_EX_TRANSPARENT | WS_EX_LAYERED) if enabled else (style & ~WS_EX_TRANSPARENT))

    def add_subtitle(self, chinese: str, original: str = "", duration: float | None = None,
                     *, speaker: str = "队友", reverse: bool = False) -> None:
        deadline = time.monotonic() + (self.settings.subtitle_seconds if duration is None else duration)
        self.entries = [*self.entries, Caption(chinese, original, speaker, deadline, reverse)][-2:]
        self._render()

    def expire(self) -> None:
        now = time.monotonic()
        self.entries = [entry for entry in self.entries if entry.expires > now]
        self._render()

    def _render(self) -> None:
        if not self.entries:
            for panel, *_ in self.rows:
                panel.hide()
            self.label.clear()
            return
        self._style_label(self.settings.font_size)
        available = max(75, self.height() - 18)
        entries = self.entries
        # Reserve one card when a large user font cannot fit two captions.
        if len(entries) == 2 and 2 * (self.settings.font_size + 38) > available:
            entries = entries[-1:]
        paired = [None] * (len(self.rows) - len(entries)) + entries
        for row, entry in zip(self.rows, paired):
            panel, badge, original, label, copy, opacity = row
            panel.setVisible(entry is not None)
            if entry is None:
                continue
            badge.setText(entry.speaker)
            original.setText(entry.original if self.settings.show_original or entry.reverse else "")
            original.setVisible(bool(original.text()))
            if original.isVisibleTo(panel):
                original.setText(original.fontMetrics().elidedText(
                    original.text(), Qt.TextElideMode.ElideRight, max(120, self.width() - 65)))
            label.setText(label.fontMetrics().elidedText(
                entry.translated, Qt.TextElideMode.ElideRight, max(120, self.width() - 65)))
            label.setToolTip(entry.translated)
            copy.setProperty("textToCopy", entry.translated)
            copy.setVisible(entry.reverse and not self.settings.click_through)
            opacity.setOpacity(max(0.0, min(1.0, (entry.expires - time.monotonic()) / 0.6)))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self.settings.click_through:
            self.drag_anchor = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event):
        if self.drag_anchor and not self.settings.click_through:
            self.move(event.globalPosition().toPoint() - self.drag_anchor)

    def mouseReleaseEvent(self, event):
        if self.drag_anchor is not None and not self.settings.click_through:
            for index, screen in enumerate(QGuiApplication.screens()):
                if screen.geometry().contains(self.frameGeometry().center()):
                    self.settings.overlay_screen = index
                    self.settings.overlay_x = self.x() - screen.geometry().x()
                    self.settings.overlay_y = self.y() - screen.geometry().y()
                    self.settings.save()
                    self.position_changed.emit(index, self.settings.overlay_x, self.settings.overlay_y)
                    break
        self.drag_anchor = None
