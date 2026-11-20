from dataclasses import dataclass
from functools import lru_cache

from PySide6.QtCore import Property, QEasingCurve, QEvent, QPropertyAnimation, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPixmap, QRadialGradient
from PySide6.QtWidgets import (QGroupBox, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QStyle, QStyleOptionButton, QStyleOptionGroupBox,
                               QStylePainter, QVBoxLayout, QWidget)

from valorant_translator.ui.icons import NEUTRAL, SIZE, LineIcon, button_icon

DEFAULT_ACCENT = "#FF4655"
DEFAULT_BACKGROUND_COLOR = "#47768f"
GAP = 12
GUTTER = 16
INLINE = 8


@dataclass(frozen=True)
class BackgroundPalette:
    top: str
    bottom: str
    left_glow: str
    right_glow: str
    upper_glow: str


BACKGROUND_THEMES = {
    "default": ("默认深色 · 红蓝", BackgroundPalette("#131729", "#101323", "#347a9d", "#984257", "#56548a")),
    "crimson": ("瓦红", BackgroundPalette("#251921", "#17121e", "#8f4751", "#9c493e", "#5b4e83")),
    "indigo": ("蓝紫", BackgroundPalette("#121b2d", "#171329", "#367ea3", "#68449d", "#4e65a3")),
}


def background_palette(theme: str, custom_color: str) -> BackgroundPalette:
    if theme == "custom":
        color = QColor(custom_color if isinstance(custom_color, str) else "")
        if not color.isValid():
            color = QColor(DEFAULT_BACKGROUND_COLOR)
        hue, saturation, lightness, _ = color.getHslF()
        hue = max(0.0, hue)

        def tint(shift, strength, light):
            color = QColor.fromHslF((hue + shift) % 1, saturation * strength, light)
            red, green, blue, _ = color.getRgbF()
            brightness = 0.2126 * red + 0.7152 * green + 0.0722 * blue
            if brightness > 0.42:
                scale = 0.42 / brightness
                color = QColor.fromRgbF(red * scale, green * scale, blue * scale)
            return color.name()

        # Limit luminance for legibility, including for colors picked near white.
        base = 0.09 + 0.05 * lightness
        glow = 0.29 + 0.11 * lightness
        return BackgroundPalette(
            tint(-0.03, 0.45, base), tint(0.06, 0.49, base - 0.025),
            tint(0.0, 0.66, glow), tint(0.13, 0.60, glow - 0.05),
            tint(-0.11, 0.56, glow - 0.045),
        )
    key = theme if isinstance(theme, str) and theme in BACKGROUND_THEMES else "default"
    return BACKGROUND_THEMES[key][1]


def accent_color(value: str) -> str:
    color = QColor(value if isinstance(value, str) else "")
    return color.name() if color.isValid() else DEFAULT_ACCENT


def accent_ink(value):
    hue, saturation, lightness, _ = QColor(accent_color(value)).getHslF()
    return QColor.fromHslF(max(0.0, hue), min(saturation, 0.62), max(0.57, lightness)).name()


class GlassButton(QPushButton):
    """Animate the painting only, keeping the layout and hit target stable."""

    def __init__(self, *args, icon=None):
        super().__init__(*args)
        self._line_icon = icon
        self._icon_accent = accent_ink(DEFAULT_ACCENT)
        self._hover = 0.0
        self._scale = 1.0
        self._press_animation = QPropertyAnimation(self, b"visualScale", self)
        self._press_animation.setDuration(80)
        self._press_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.pressed.connect(lambda: self._animate_to(0.97))
        self.released.connect(lambda: self._animate_to(1.0))
        self._hover_animation = QPropertyAnimation(self, b"visualHover", self)
        self._hover_animation.setDuration(100)
        self._hover_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.setIconSize(QSize(SIZE, SIZE))
        self.toggled.connect(self._refresh_icon)
        self.objectNameChanged.connect(self._refresh_icon)
        self._refresh_icon()

    def set_line_icon(self, name):
        if name != self._line_icon:
            self._line_icon = name
            self._refresh_icon()

    def set_accent(self, color):
        if self._line_icon and color != self._icon_accent:
            self._icon_accent = color
            self._refresh_icon()

    def _refresh_icon(self):
        if getattr(self, "_line_icon", None):
            self.setIcon(button_icon(self._line_icon, self._line_color()))

    def _line_color(self):
        if not self.isEnabled():
            return "#a0a4ad"
        return self._icon_accent if self.isChecked() or self.objectName() == "primary" else NEUTRAL

    @Property(float)
    def visualHover(self):
        return self._hover

    @visualHover.setter
    def visualHover(self, value):
        if round(self._hover * 4) != round(value * 4):
            option = QStyleOptionButton()
            self.initStyleOption(option)
            inner = self.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, self)
            span = self.fontMetrics().horizontalAdvance(self.text()) + SIZE + 4
            left = inner.left() if self.property("navigation") else inner.center().x() - span // 2
            region = QRect(left, inner.center().y() - SIZE // 2, SIZE, SIZE)
            self.update(region.adjusted(-7, -3, 7, 3))
        self._hover = value

    def _animate_hover(self, target):
        if self._line_icon and self.isEnabled() and self.isVisible() and not self.window().isMinimized():
            self._hover_animation.stop()
            self._hover_animation.setStartValue(self._hover)
            self._hover_animation.setEndValue(target)
            self._hover_animation.start()

    def enterEvent(self, event):
        super().enterEvent(event)
        self._animate_hover(1.0)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._animate_hover(0.0)

    def hideEvent(self, event):
        self._hover_animation.stop()
        self._hover = 0.0
        super().hideEvent(event)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange:
            self._refresh_icon()

    @Property(float)
    def visualScale(self):
        return self._scale

    @visualScale.setter
    def visualScale(self, value):
        self._scale = value
        self.update()

    def _animate_to(self, scale):
        animation = self._press_animation
        animation.stop()
        animation.setStartValue(self._scale)
        animation.setEndValue(scale)
        animation.start()

    def paintEvent(self, event):
        painter = QStylePainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        scale = self._scale if self.isEnabled() else 1.0
        center = QRectF(self.rect()).center()
        painter.translate(center)
        painter.scale(scale, scale)
        painter.translate(-center)
        option = QStyleOptionButton()
        self.initStyleOption(option)
        if self._line_icon and self.isEnabled() and self._hover:
            option.icon = button_icon(self._line_icon, self._line_color(), round(self._hover * 4))
        painter.drawControl(QStyle.ControlElement.CE_PushButton, option)
        if scale < 1:
            painter.setPen(QColor("transparent"))
            painter.setBrush(QColor(0, 0, 0, round((1 - scale) * 1600)))
            pill = self.objectName() == "primary" or self.property("gamePill") or self.property("ghost")
            radius = 17 if pill else 10
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), radius, radius)


def tint_icons(parent, value):
    accent = accent_ink(value)
    for kind in (GlassButton, LineIcon):
        for item in parent.findChildren(kind):
            item.set_accent(accent)


class GlassSection(QGroupBox):
    def __init__(self, title, icon):
        super().__init__(title)
        self.setObjectName("glassSection")
        self.setAccessibleName(title)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(GUTTER, GAP, GUTTER, GAP)
        self.body.setSpacing(INLINE)
        header = QHBoxLayout()
        header.setSpacing(INLINE)
        self.icon = LineIcon(icon)
        header.addWidget(self.icon)
        label = QLabel(title)
        label.setObjectName("sectionTitle")
        header.addWidget(label, 1)
        self.body.addLayout(header)

    def paintEvent(self, event):
        with QStylePainter(self) as painter:
            option = QStyleOptionGroupBox()
            self.initStyleOption(option)
            option.text = ""
            option.subControls = QStyle.SubControl.SC_GroupBoxFrame
            painter.drawComplexControl(QStyle.ComplexControl.CC_GroupBox, option)


class CompactLabel(QLabel):
    """One visible line, with full text available to tooltips and feedback."""

    def __init__(self, text=""):
        super().__init__()
        self._full_text = ""
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setText(text)

    def text(self):
        return self._full_text

    def setText(self, text):
        if text == self._full_text:
            return
        self._full_text = text
        self.setToolTip(text)
        self._elide()

    def _elide(self):
        super().setText(self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideRight, self.contentsRect().width()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange and hasattr(self, "_full_text"):
            self._elide()


@lru_cache(maxsize=6)
def _background_pixmap(palette: BackgroundPalette) -> QPixmap:
    """Paint the soft lights once per palette and share the result across windows."""
    width, height = 1280, 800
    pixmap = QPixmap(width, height)
    with QPainter(pixmap) as painter:
        rect = pixmap.rect()
        base = QLinearGradient(0, 0, width * 0.75, height)
        base.setColorAt(0, QColor(palette.top))
        base.setColorAt(1, QColor(palette.bottom))
        painter.fillRect(rect, base)
        lights = (
            (0.055, 0.31, 0.59, palette.left_glow, 208),
            (0.61, -0.10, 0.62, palette.upper_glow, 154),
            (0.98, 0.95, 0.63, palette.right_glow, 215),
        )
        for x, y, radius, value, opacity in lights:
            gradient = QRadialGradient(width * x, height * y, width * radius)
            color = QColor(value)
            color.setAlpha(opacity)
            gradient.setColorAt(0, color)
            color.setAlpha(opacity // 3)
            gradient.setColorAt(0.55, color)
            color.setAlpha(0)
            gradient.setColorAt(1, color)
            painter.fillRect(rect, gradient)
    return pixmap


class GlassSurface(QWidget):
    def __init__(self, theme="default", custom_color="", parent=None):
        super().__init__(parent)
        self._background = background_palette(theme, custom_color)

    def set_background(self, theme: str, custom_color: str):
        palette = background_palette(theme, custom_color)
        if palette != self._background:
            self._background = palette
            self.update()

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(self.rect(), _background_pixmap(self._background))


def glass_styles(accent: str) -> str:
    accent = accent_color(accent)
    rgb = QColor(accent)
    h, s, lightness, _ = rgb.getHslF()
    edge = accent_ink(accent)
    color = QColor.fromHslF(max(0.0, h), min(s, 0.56), min(lightness, 0.53))

    def tone(base, amount):
        neutral = QColor(base)
        return QColor(*(round(a * (1 - amount) + b * amount)
                        for a, b in zip(neutral.getRgb()[:3], color.getRgb()[:3]))).name()

    return """
        * { color: #f0edf2; font-family: "Microsoft YaHei", "Segoe UI", "PingFang SC"; font-size: 13px; }
        QLabel, QScrollArea, QScrollArea QWidget#pageBody, QTabWidget::pane {
            background: transparent; border: none;
        }
        QLabel#eyebrow, QLabel#muted, QLabel#statLabel { color: #d0d6e1; }
        QLabel#title { font-size: 27px; font-weight: 750; }
        QLabel#brand { font-size: 22px; font-weight: 750; }
        QLabel#translation { font-size: 23px; font-weight: 750; padding: 3px 0; }
        QLabel#statValue { font-size: 18px; font-weight: 700; }
        QLabel#statLabel { font-size: 11px; }
        QLabel#sectionTitle { font-size: 15px; font-weight: 700; color: #f3f5fa; }
        QFrame#warning { background: rgba(234,235,250,18);
            border: 1px solid rgba(225,235,251,82); border-radius: 12px; }
        QFrame#warning QLabel { color: #e5e8f0; }
        QLabel#lastDiscard { color: #f2d49b; }
        QFrame#hero, QFrame#sidebar, QFrame#glassCard, QGroupBox#glassSection {
            background: qlineargradient(x1:0,y1:0,x2:0.7,y2:1,
                         stop:0 rgba(222,233,250,55), stop:0.45 rgba(173,187,215,37),
                         stop:1 rgba(14,21,37,80));
            border: 1px solid rgba(225,235,251,103); border-top-color: rgba(234,242,255,156);
            border-radius: 18px;
        }
        QFrame#sidebar {
            background: qlineargradient(x1:0,y1:0,x2:0.8,y2:1,
                         stop:0 rgba(230,241,255,45), stop:1 rgba(158,191,228,12));
            border-color: rgba(225,239,255,120);
        }
        QGroupBox#glassSection { margin: 0; padding: 0; }
        QPushButton, QComboBox, QSpinBox, QLineEdit { background: rgba(239,243,247,19);
            border: 1px solid rgba(228,234,241,88); border-radius: 11px; padding: 8px 11px; }
        QComboBox QAbstractItemView, QMenu { background: #25272d; color: #f0edf2; }
        QPushButton:hover { background: rgba(236,242,250,53); border-color: #c3cbd5; }
        QPushButton[gamePill="true"] { border-radius: 16px; }
        QPushButton[navigation="true"] { text-align: left; padding-left: 13px; }
        QPushButton:checked, QPushButton[gamePill="true"]:checked {
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                         stop:0 rgba(227,234,251,52), stop:1 rgba(196,210,237,19));
            border-color: EDGE;
        }
        QPushButton:pressed { background: rgba(239,243,247,11); }
        QPushButton#primary {
            border: 1px solid EDGE; border-radius: 17px; padding: 7px 17px; font-weight: 700;
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                         stop:0 TINT_TOP, stop:0.36 TINT_MID, stop:1 TINT_LOW);
            color: #fff9fe;
        }
        QPushButton#primary:hover {
            border-color: HOVER_EDGE;
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                         stop:0 HOVER_TOP, stop:0.40 TINT_MID, stop:1 TINT_LOW);
        }
        QPushButton#primary:pressed { background: TINT_LOW; }
        QPushButton:disabled, QPushButton#primary:disabled {
            color: #a0a4ad; background: #292d35; border-color: #4a505c; }
        QPushButton[ghost="true"] {
            border-radius: 17px; padding: 7px 17px;
            background: transparent; border-color: #a6afc1;
        }
        QPushButton[ghost="true"]:hover { border-color: #e6edf7; }
        QPushButton[ghost="true"]:disabled { color: #a0a4ad; border-color: #69707e; }
        QProgressBar { border: 1px solid #6f6374; border-radius: 7px; background: rgba(15,14,23,130);
            text-align: center; height: 15px; }
        QProgressBar::chunk { background: #a3aeba; border-radius: 6px; }
        QScrollBar:vertical { background: rgba(15,14,24,79); width: 9px; margin: 2px; }
        QScrollBar::handle:vertical { background: rgba(225,204,226,109); border-radius: 4px; min-height: 28px; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
        QStatusBar { background: #191b20; }
        QTableWidget { background: rgba(17,15,26,175); gridline-color: #5c5062; }
        QHeaderView::section { background: #332b38; padding: 7px; }
    """.replace("HOVER_EDGE", tone("#dfdce7", 0.23)).replace("EDGE", edge) \
        .replace("HOVER_TOP", tone("#617085", 0.24)).replace("TINT_TOP", tone("#566275", 0.29)) \
        .replace("TINT_MID", tone("#384257", 0.33)).replace("TINT_LOW", tone("#21293a", 0.25))
