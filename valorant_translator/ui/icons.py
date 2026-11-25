"""Small line icons. Live states repaint only their own 20 px widget."""
from __future__ import annotations

import math
from functools import lru_cache
from time import monotonic
from weakref import ref

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QWidget
from shiboken6 import isValid

from valorant_translator.ui import tokens

SIZE = 20
NEUTRAL = tokens.MUTED
FRAME_MS = 33
DONE_PHASE = 10

# Each shape is drawn on the same 20 px grid with the same stroke.
_LINES = {
    "chat": (((3, 4), (17, 4), (17, 13), (9, 13), (5, 16), (5, 13), (3, 13), (3, 4)),
             ((6, 8.5), (8, 8.5)), ((10, 8.5), (12, 8.5))),
    "settings": (((3, 5), (17, 5)), ((3, 10), (17, 10)), ((3, 15), (17, 15)),
                 ((7, 3), (7, 7)), ((13, 8), (13, 12)), ((8, 13), (8, 17))),
    "clock": (((10, 5.5), (10, 10), (13, 12)),),
    "book": (((10, 5), (7, 3.5), (3, 3.5), (3, 14), (7, 14), (10, 16),
              (13, 14), (17, 14), (17, 3.5), (13, 3.5), (10, 5), (10, 16)),),
    "layers": (((3, 6.5), (10, 3), (17, 6.5), (10, 10), (3, 6.5)),
               ((3, 10), (10, 13.5), (17, 10)), ((3, 13.5), (10, 17), (17, 13.5))),
    "speaker": (((3, 8), (6, 8), (10, 4.5), (10, 15.5), (6, 12), (3, 12), (3, 8)),
                ((13, 7), (14, 10), (13, 13)), ((15.5, 5), (17, 10), (15.5, 15))),
    "screen": (((6, 13), (9, 13)), ((11, 13), (14, 13))),
    "play": (((7, 4), (15, 10), (7, 16), (7, 4)),),
    "chip": (((7, 2.7), (7, 5)), ((13, 2.7), (13, 5)), ((7, 15), (7, 17.3)), ((13, 15), (13, 17.3)),
             ((2.7, 7), (5, 7)), ((2.7, 13), (5, 13)), ((15, 7), (17.3, 7)), ((15, 13), (17.3, 13))),
    "mic": (((5, 9), (5, 11), (7, 13.5), (10, 14.5), (13, 13.5), (15, 11), (15, 9)),
            ((10, 14.5), (10, 17)), ((7.5, 17), (12.5, 17))),
    "download": (((10, 3), (10, 12)), ((6.5, 8.5), (10, 12), (13.5, 8.5)),
                 ((4, 13), (4, 16), (16, 16), (16, 13))),
    "folder": (((3, 15.5), (3, 5), (8, 5), (10, 7.5), (17, 7.5), (17, 15.5), (3, 15.5)),),
    "refresh": (((15.5, 3.5), (15.5, 7), (12, 7)), ((4.5, 16.5), (4.5, 13), (8, 13))),
    "stop": (),
    "pause": (((7, 5), (7, 15)), ((13, 5), (13, 15))),
    "next": (((8, 5), (13, 10), (8, 15)),),
    "copy": (((6, 13.5), (3.5, 13.5), (3.5, 3.5), (13, 3.5), (13, 6)),),
    "shield": (((10, 3), (16, 5.5), (16, 11), (14, 14), (10, 17), (6, 14),
                (4, 11), (4, 5.5), (10, 3)), ((7.5, 10), (9.4, 12), (13, 8))),
    "activity": (((3, 10), (6, 10), (8, 5), (11, 15), (13, 10), (17, 10)),),
}
_BOXES = {"screen": (3, 5, 14, 10, 2), "chip": (5, 5, 10, 10, 2),
          "mic": (8, 3, 4, 9, 2), "stop": (5, 5, 10, 10, 2), "copy": (7, 6.5, 9, 10, 1.5)}


def _lines(painter, strokes):
    for stroke in strokes:
        painter.drawPolyline(QPolygonF([QPointF(x, y) for x, y in stroke]))


def _draw(painter, name, color, mode="idle", phase=0, level=0):
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(color), 1.45, Qt.PenStyle.SolidLine,
                         Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    if mode == "spin":
        painter.drawArc(QRectF(4, 4, 12, 12), -phase * 4, 16 * 250)
    elif mode == "done":
        first = min(1, phase / 4)
        second = max(0, min(1, (phase - 4) / (DONE_PHASE - 4)))
        stroke = [(4.5, 10), (4.5 + 3.5 * first, 10 + 3.5 * first)]
        if second > 0:
            stroke.append((8 + 7.5 * second, 13.5 - 7.5 * second))
        _lines(painter, (stroke,))
    elif mode == "pulse" and name == "activity":
        for i in range(5):
            h = 1.5 + 5.3 * level * abs(math.sin(phase / 13 + i * 0.75))
            x = 4 + i * 3
            painter.drawLine(QPointF(x, 10 - h), QPointF(x, 10 + h))
    else:
        if mode == "pulse":
            scale = 0.94 + 0.08 * math.sin(phase / 15)
            painter.translate(10, 10)
            painter.scale(scale, scale)
            painter.translate(-10, -10)
        _lines(painter, _LINES.get(name, ()))
        if name in _BOXES:
            x, y, w, h, radius = _BOXES[name]
            painter.drawRoundedRect(QRectF(x, y, w, h), radius, radius)
        if name == "clock":
            painter.drawEllipse(QRectF(3, 3, 14, 14))
        elif name == "refresh":
            painter.drawArc(QRectF(4, 4, 12, 12), 16 * 25, 16 * 155)
            painter.drawArc(QRectF(4, 4, 12, 12), 16 * 205, 16 * 155)


@lru_cache(maxsize=128)
def button_icon(name, color, frame=0):
    if name not in _LINES:
        return QIcon()
    pixmap = QPixmap(SIZE * 2, SIZE * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    with QPainter(pixmap) as painter:
        painter.scale(2, 2)
        scale = 0.90 + 0.025 * frame
        painter.translate(10 * (1 - scale), 10 * (1 - scale))
        painter.scale(scale, scale)
        _draw(painter, name, color)
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


class LineIcon(QWidget):
    def __init__(self, name, *, accented=False):
        super().__init__()
        self.name = name
        self.accented = accented
        self.mode = "idle"
        self.accent = NEUTRAL
        self.highlighted = False
        self.phase = 0
        self._reduced = False
        self.level = 0.0
        self._host_ref = None
        self._elapsed = 0.0
        self._running_since = None
        self._timer = QTimer(self, interval=FRAME_MS, timerType=Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self.setFixedSize(QSize(SIZE, SIZE))
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        if name not in _LINES:
            self.setFixedSize(0, 0)

    def set_accent(self, color):
        if color != self.accent:
            self.accent = color
            self.update()

    def set_mode(self, mode, *, highlight=False, restart=False):
        if mode != self.mode or restart:
            self._pause_animation()
            self.mode = mode
            self.phase = 0
            self._elapsed = 0.0
        elif highlight == self.highlighted:
            return
        self.highlighted = highlight
        self._sync_animation()
        self.update()

    def set_level(self, level):
        self.level = max(0.0, min(1.0, level))

    def set_reduced_motion(self, value):
        if self._reduced != value:
            self._reduced = value
            self._sync_animation()
            self.update()

    def _sync_animation(self):
        timer = getattr(self, "_timer", None)
        if timer is None or not isValid(timer):
            return
        if self._reduced:
            self._pause_animation()
            self.phase = DONE_PHASE if self.mode == "done" else 0
            return
        moving = self.mode in ("spin", "pulse") or (self.mode == "done" and self.phase < DONE_PHASE)
        if moving and self.isVisible() and not self.window().isMinimized():
            if not timer.isActive():
                self._running_since = monotonic()
                timer.start()
        else:
            self._pause_animation()

    def _pause_animation(self):
        timer = getattr(self, "_timer", None)
        if timer is None or not isValid(timer):
            return
        timer.stop()
        started = getattr(self, "_running_since", None)
        if started is not None:
            self._elapsed += monotonic() - started
            self._running_since = None

    def showEvent(self, event):
        super().showEvent(event)
        # Qt may send show/hide events during construction or wrapper teardown.
        timer = getattr(self, "_timer", None)
        if timer is None or not isValid(timer):
            return
        host = self.window()
        host_ref = getattr(self, "_host_ref", None)
        previous = host_ref() if host_ref is not None else None
        if host is not previous:
            if previous is not None and isValid(previous):
                previous.removeEventFilter(self)
            self._host_ref = ref(host)
            host.installEventFilter(self)
        self._sync_animation()

    def hideEvent(self, event):
        self._pause_animation()
        super().hideEvent(event)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Hide:
            self._pause_animation()
        elif event.type() in (QEvent.Type.WindowStateChange, QEvent.Type.Show):
            self._sync_animation()
        return super().eventFilter(watched, event)

    def _tick(self):
        if not self.isVisible() or self.window().isMinimized():
            self._pause_animation()
            return
        started = self._running_since
        if started is None:
            return
        frame = int((self._elapsed + monotonic() - started) * 1000 / FRAME_MS)
        if self.mode == "done":
            # QTimer can coalesce ticks under load. Always render the complete checkmark.
            self.phase = min(frame, DONE_PHASE)
            if self.phase == DONE_PHASE:
                self._pause_animation()
        else:
            self.phase = frame * (24 if self.mode == "spin" else 1)
        self.update()

    def paintEvent(self, event):
        if getattr(self, "name", None) in _LINES:
            with QPainter(self) as painter:
                _draw(painter, self.name, self.accent if self.highlighted or self.accented else NEUTRAL,
                      self.mode, self.phase, self.level)
