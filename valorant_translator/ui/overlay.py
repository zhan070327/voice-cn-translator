from __future__ import annotations

import sys
import time

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from valorant_translator.config import Settings


class SubtitleOverlay(QWidget):
    position_changed = Signal(int, int, int)

    def __init__(self, settings: Settings):
        flags = (Qt.WindowType.Window if settings.normal_window else
                 Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                 Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        super().__init__(None, flags)
        self.settings = settings
        self.entries: list[tuple[str, float]] = []
        self.drag_anchor: QPoint | None = None
        self.setWindowTitle("VALORANT 翻译字幕")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, not settings.normal_window)
        self.label = QLabel("")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        layout = QVBoxLayout(self)
        layout.addWidget(self.label)
        self.resize(760, 140)
        self.apply_settings()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.expire)
        self.timer.start(250)
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

    def _style_label(self, font_size: int) -> None:
        alpha = max(0, min(255, round(self.settings.overlay_opacity * 2.55)))
        self.label.setStyleSheet(f"QLabel {{ color: white; background: rgba(12,16,24,{alpha}); "
                                 f"font-size: {font_size}px; font-weight: 700; "
                                 "border-radius: 12px; padding: 10px; }")

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

    def add_subtitle(self, chinese: str, original: str = "", duration: float | None = None) -> None:
        line = chinese + (f"\n{original}" if self.settings.show_original and original else "")
        self.entries.append((line, time.monotonic() + (duration or self.settings.subtitle_seconds)))
        self.entries = self.entries[-2:]
        self._render()

    def expire(self) -> None:
        before = len(self.entries)
        self.entries = [(s, t) for s, t in self.entries if t > time.monotonic()]
        if len(self.entries) != before:
            self._render()

    def _render(self) -> None:
        lines = [s for s, _ in self.entries]
        self.label.setVisible(bool(self.entries) or self.settings.normal_window)
        self._style_label(self.settings.font_size)

        def height_for(text_lines: list[str]) -> int:
            self.label.setText("\n".join(text_lines))
            return self.label.heightForWidth(max(120, self.width() - 24))

        available_height = max(60, self.height() - 24)
        needed_height = height_for(lines)
        if needed_height > available_height and len(lines) > 1:
            lines = lines[-1:]  # Keep the newest subtitle readable.
            needed_height = height_for(lines)
        size = self.settings.font_size
        while needed_height > available_height and size > 14:
            size = max(14, size - 2)
            self._style_label(size)
            needed_height = height_for(lines)
        if needed_height > available_height and lines:
            original = lines[-1]
            visible = original
            while needed_height > available_height and len(visible) > 8:
                visible = visible[:max(8, int(len(visible) * 0.8))].rstrip("… ") + "…"
                needed_height = height_for([visible])
            self.label.setToolTip(original)
        else:
            self.label.setToolTip("")

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
