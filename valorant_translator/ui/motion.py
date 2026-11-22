"""Small, interruptible Qt effects. Widgets and hit targets keep their geometry."""
from PySide6.QtCore import Property, QEasingCurve, QEvent, QPoint, QPropertyAnimation, QRectF, Qt
from PySide6.QtGui import QColor, QConicalGradient, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QToolTip, QWidget

from valorant_translator.ui import tokens as t
from valorant_translator.ui.glass import GlassButton

OUT = QEasingCurve.Type.OutCubic
IN = QEasingCurve.Type.InCubic


class Effect(QWidget):
    def __init__(self, host):
        super().__init__(host)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._progress = 1.0
        self.animation = QPropertyAnimation(self, b"opacity", self)
        self.reduced = False

    @Property(float)
    def opacity(self):
        return self._progress

    @opacity.setter
    def opacity(self, value):
        self._progress = value
        self.update()

    def animate(self, duration=t.INTERACTION_MS, start=0.0, *, easing=OUT):
        self.animation.stop()
        if self.reduced or not self.isVisible():
            self.opacity = 1.0
            return
        self.animation.setDuration(duration)
        self.animation.setEasingCurve(easing)
        self.animation.setStartValue(start)
        self.animation.setEndValue(1.0)
        self.animation.start()

    def set_reduced(self, value):
        self.reduced = value
        if value:
            self.animation.stop()
            self.opacity = 1.0

    def hideEvent(self, event):
        self.animation.stop()
        self.opacity = 1.0
        super().hideEvent(event)


class Snapshot(Effect):
    """Fade between already laid-out pages or skins over an opaque window."""

    def __init__(self, host):
        super().__init__(host)
        self.image = None
        self.animation.finished.connect(self.hide)
        self.hide()

    def capture(self):
        if self.reduced or not self.parentWidget().isVisible():
            return None
        return self.parentWidget().grab()

    def reveal(self, image, duration):
        if image is None:
            return
        self.image = image
        self.setGeometry(self.parentWidget().rect())
        self.raise_()
        self.show()
        self.animate(duration)

    def paintEvent(self, event):
        if self.image is not None:
            with QPainter(self) as painter:
                painter.setOpacity(1 - self.opacity)
                painter.drawPixmap(self.rect(), self.image)

    def hideEvent(self, event):
        self.image = None
        super().hideEvent(event)

    def set_reduced(self, value):
        super().set_reduced(value)
        if value:
            self.hide()


class SelectionCapsule(Effect):
    """A glass rim that slides and stretches in paint above flat menu buttons."""

    def __init__(self, host):
        super().__init__(host)
        self.source = QRectF()
        self.target = None
        self.accent = t.DEFAULT_ACCENT
        host.installEventFilter(self)

    def select(self, button, *, animated=True):
        if button is None:
            return
        self.source = self.position()
        self.target = button
        if animated and not self.source.isEmpty():
            self.animate(t.SKIN_MS, easing=QEasingCurve.Type.OutBack)
        else:
            self.animation.stop()
            self.opacity = 1.0

    def position(self):
        if self.target is None:
            return QRectF()
        destination = QRectF(self.target.geometry()).adjusted(1, 1, -1, -1)
        if self.source.isEmpty() or self.opacity == 1.0:
            return destination
        progress = self.opacity
        delta = destination.center() - self.source.center()
        destination.translate(delta * (progress - 1))
        stretch = max(0.0, progress * (1 - progress)) * 18
        if abs(delta.x()) >= abs(delta.y()):
            destination.adjust(-stretch, 0, stretch, 0)
        else:
            destination.adjust(0, -stretch, 0, stretch)
        return destination

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.LayoutRequest):
            self.setGeometry(watched.rect())
            self.raise_()
        return False

    def paintEvent(self, event):
        rect = self.position()
        if not rect.isEmpty():
            with QPainter(self) as painter:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                color = QColor(self.accent)
                painter.setPen(QPen(color, 1))
                color.setAlpha(23)
                painter.setBrush(color)
                painter.drawRoundedRect(rect, t.RADIUS, t.RADIUS)


class CaptionLight(Effect):
    """A restrained pointer glow at the rim, plus one beam on new subtitles."""

    def __init__(self, host):
        super().__init__(host)
        self.accent = t.DEFAULT_ACCENT
        self.pointer = None
        self.tracking = False
        self._glow = 0.0
        self.track = QPropertyAnimation(self, b"hoverOpacity", self)
        host.setMouseTracking(True)
        host.installEventFilter(self)

    @Property(float)
    def hoverOpacity(self):
        return self._glow

    @hoverOpacity.setter
    def hoverOpacity(self, value):
        self._glow = value
        self.update()

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind in (QEvent.Type.Resize, QEvent.Type.Show):
            self.setGeometry(watched.rect())
            self.raise_()
        elif kind == QEvent.Type.MouseMove and not self.reduced:
            self.pointer = event.position()
            if not self.tracking:
                self.tracking = True
                self._fade_glow(1.0)
            self.update()
        elif kind == QEvent.Type.Leave and self.tracking:
            self.tracking = False
            self._fade_glow(0.0)
        return False

    def _fade_glow(self, value):
        self.track.stop()
        self.track.setDuration(t.INTERACTION_MS)
        self.track.setEasingCurve(OUT if value else IN)
        self.track.setStartValue(self.hoverOpacity)
        self.track.setEndValue(value)
        self.track.start()

    def set_reduced(self, value):
        super().set_reduced(value)
        if value:
            self.track.stop()
            self.tracking = False
            self.hoverOpacity = 0.0

    def hideEvent(self, event):
        self.track.stop()
        self.tracking = False
        self.hoverOpacity = 0.0
        super().hideEvent(event)

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            bounds = QRectF(self.rect()).adjusted(2, 2, -2, -2)
            if self.pointer is not None and self.hoverOpacity:
                # Tilt the lit rim only, keeping the solid text backing stationary.
                offset = (self.pointer - bounds.center()).x() / max(1.0, bounds.width())
                painter.save()
                painter.translate(bounds.center())
                painter.rotate(max(-4, min(4, offset * 8)) * self.hoverOpacity)
                painter.translate(-bounds.center())
                light = QRadialGradient(self.pointer, t.WIDE * 2)
                color = QColor(self.accent)
                color.setAlpha(round(150 * self.hoverOpacity))
                light.setColorAt(0, color)
                color.setAlpha(0)
                light.setColorAt(1, color)
                pen = QPen(color, 2)
                pen.setBrush(light)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(bounds, t.RADIUS, t.RADIUS)
                painter.restore()
            if self.opacity < 1.0:
                beam = QConicalGradient(bounds.center(), 180 + self.opacity * 360)
                color = QColor(self.accent)
                color.setAlpha(0)
                beam.setColorAt(0, color)
                beam.setColorAt(0.22, color)
                beam.setColorAt(1, color)
                color.setAlphaF(1 - self.opacity)
                beam.setColorAt(0.08, color)
                pen = QPen(color, 2)
                pen.setBrush(beam)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(bounds, t.RADIUS, t.RADIUS)


class LoadingSkeleton(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("skeleton")
        self.setFixedHeight(t.INLINE)
        self.setAccessibleName("正在加载")
        self.reduced = False
        effect = QGraphicsOpacityEffect(self, opacity=1.0)
        self.setGraphicsEffect(effect)
        self.pulse = QPropertyAnimation(effect, b"opacity", self)
        self.pulse.setDuration(t.PAGE_MS)
        self.pulse.setStartValue(0.5)
        self.pulse.setKeyValueAt(0.5, 1.0)
        self.pulse.setEndValue(0.5)
        self.pulse.setLoopCount(-1)
        self._window = None
        self.hide()

    def set_reduced(self, value):
        self.reduced = value
        self._sync()

    def _sync(self):
        if self.isVisible() and not self.window().isMinimized() and not self.reduced:
            self.pulse.start()
        else:
            self.pulse.stop()
            self.graphicsEffect().setOpacity(1.0)

    def showEvent(self, event):
        super().showEvent(event)
        window = self.window()
        if window is not self._window:
            if self._window is not None:
                self._window.removeEventFilter(self)
            self._window = window
            window.installEventFilter(self)
        self._sync()

    def hideEvent(self, event):
        self.pulse.stop()
        self.graphicsEffect().setOpacity(1.0)
        super().hideEvent(event)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.WindowStateChange:
            self._sync()
        return False


class Toast(QLabel):
    def __init__(self, host):
        super().__init__(host)
        self.setObjectName("toast")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.slide = QPropertyAnimation(self, b"pos", self)
        self._reduced = False
        self._leaving = False
        self.slide.finished.connect(self._finish)
        self.hide()

    def announce(self, text, *, reduced=False, success=False):
        self.slide.stop()
        self._reduced = reduced
        self._leaving = False
        self.setProperty("success", success)
        self.style().unpolish(self)
        self.style().polish(self)
        self.setText(text)
        self.adjustSize()
        host = self.parentWidget()
        end = QPoint(max(t.INSET, host.width() - self.width() - t.GAP), host.height() - self.height() - t.GAP)
        start = self.pos() if self.isVisible() else end + QPoint(t.GAP, 0)
        self.raise_()
        self.show()
        self._move(start, end, OUT)

    def set_reduced(self, value):
        self._reduced = value
        if value and self.slide.state() == QPropertyAnimation.State.Running:
            self.slide.stop()
            if self._leaving:
                self.hide()
            else:
                self.move(self.slide.endValue())

    def dismiss(self):
        self._leaving = True
        if self._reduced:
            self.hide()
        else:
            self._move(self.pos(), self.pos() + QPoint(t.GAP, 0), IN)

    def _move(self, start, end, easing):
        self.slide.stop()
        if self._reduced:
            self.move(end)
            return
        self.slide.setDuration(t.INTERACTION_MS)
        self.slide.setEasingCurve(easing)
        self.slide.setStartValue(start)
        self.slide.setEndValue(end)
        self.slide.start()

    def _finish(self):
        if self._leaving:
            self.hide()


class DetailPanel(Effect):
    """The shell grows from the trigger without changing button geometry."""

    def __init__(self, host):
        super().__init__(host)
        self.anchor = QRectF()
        self.outline = False
        self.animation.finished.connect(self.hide)

    def show_from(self, button, target, *, opening):
        self.setGeometry(self.parentWidget().rect())
        origin = button.mapTo(self.parentWidget(), QPoint())
        self.anchor = QRectF(origin.x(), origin.y(), button.width(), button.height())
        self.outline = False
        self._play(target, opening)

    def show_from_center(self):
        rect = QRectF(self.parentWidget().rect())
        self.setGeometry(self.parentWidget().rect())
        self.anchor = QRectF(rect)
        self.anchor.setSize(rect.size() * 0.98)
        self.anchor.moveCenter(rect.center())
        self.outline = True
        self._play(rect, True)

    def _play(self, target, opening):
        self.target = QRectF(target)
        self.animation.stop()
        if self.reduced or not self.parentWidget().isVisible():
            self.hide()
            return
        self.raise_()
        self.show()
        self.animation.setDuration(t.INTERACTION_MS)
        self.animation.setEasingCurve(OUT if opening else IN)
        self.animation.setStartValue(0.0 if opening else 1.0)
        self.animation.setEndValue(1.0 if opening else 0.0)
        self.animation.start()

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            progress = self.opacity
            rect = QRectF(self.anchor.topLeft() * (1 - progress) + self.target.topLeft() * progress,
                          self.anchor.bottomRight() * (1 - progress) + self.target.bottomRight() * progress)
            painter.setPen(QPen(QColor(t.EDGE), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush if self.outline else QColor(t.skin_for("general").panel))
            painter.setOpacity(0.8 * (1 - progress))
            painter.drawRoundedRect(rect, t.CONTROL_RADIUS, t.CONTROL_RADIUS)


class ActionDock(QFrame):
    """Icon-only access to existing subtitle actions, with adjacent icon lift."""

    def __init__(self, actions):
        super().__init__()
        self.setObjectName("dock")
        self.setAccessibleName("字幕快捷操作")
        self.buttons = []
        self.hovered = None
        self.reduced = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(t.INLINE, t.TIGHT, t.INLINE, t.TIGHT)
        layout.setSpacing(t.TIGHT)
        self.animations = []
        for name, icon, callback in actions:
            button = GlassButton(icon=icon)
            button.setToolTip(name)
            button.setAccessibleName(name)
            button.setFixedSize(t.WIDE, t.WIDE)
            button.clicked.connect(callback)
            button.installEventFilter(self)
            layout.addWidget(button)
            self.buttons.append(button)
            animation = QPropertyAnimation(button, b"dockOpacity", self)
            animation.setDuration(t.INTERACTION_MS)
            self.animations.append(animation)

    def set_reduced(self, value):
        self.reduced = value
        self._magnify()

    def eventFilter(self, watched, event):
        if watched in self.buttons:
            if event.type() == QEvent.Type.Enter:
                self.hovered = self.buttons.index(watched)
                self._magnify()
            elif event.type() in (QEvent.Type.Leave, QEvent.Type.Hide):
                self.hovered = None
                self._magnify()
            elif event.type() == QEvent.Type.ToolTip:
                QToolTip.showText(watched.mapToGlobal(QPoint(0, -t.SPACE)), watched.toolTip(), watched)
                return True
        return False

    def _magnify(self):
        for index, (button, animation) in enumerate(zip(self.buttons, self.animations)):
            animation.stop()
            distance = abs(index - self.hovered) if self.hovered is not None else len(self.buttons)
            opacity = 1.0 if distance == 0 else 0.45 if distance == 1 else 0.0
            if self.reduced or not self.isVisible():
                button.dockOpacity = 0.0
            else:
                animation.setEasingCurve(OUT if opacity else IN)
                animation.setStartValue(button.dockOpacity)
                animation.setEndValue(opacity)
                animation.start()
