from dataclasses import dataclass
from functools import lru_cache

from PySide6.QtCore import Property, QEasingCurve, QEvent, QPropertyAnimation, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPixmap, QRadialGradient
from PySide6.QtWidgets import (QGroupBox, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QStyle, QStyleOptionButton, QStyleOptionGroupBox,
                               QStylePainter, QVBoxLayout, QWidget)

from valorant_translator.ui import tokens as t
from valorant_translator.ui.fonts import display_style, track_caption
from valorant_translator.ui.icons import NEUTRAL, SIZE, LineIcon, button_icon

DEFAULT_ACCENT = t.DEFAULT_ACCENT
DEFAULT_BACKGROUND_COLOR = t.DEFAULT_BACKGROUND_COLOR
GAP = t.GAP
GUTTER = t.INSET
INLINE = t.INLINE


@dataclass(frozen=True)
class BackgroundPalette:
    top: str
    bottom: str
    left_glow: str
    right_glow: str
    upper_glow: str
    origin: tuple[float, float] | None = None
    horizontal: bool = False


BACKGROUND_THEMES = {
    key: (label, BackgroundPalette(*colors)) for key, (label, colors) in t.BACKGROUND_PRESETS.items()
}


def background_palette(theme: str, custom_color: str, game: str | None = None) -> BackgroundPalette:
    if theme == "default" and game is not None:
        skin = t.skin_for(game)
        return BackgroundPalette(skin.background, skin.panel, skin.accent,
                                 skin.accent, t.EDGE, skin.light_origin, game == "general")
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
        self._reduced = False
        self._dock_opacity = 0.0
        self._line_icon = icon
        self._icon_accent = accent_ink(DEFAULT_ACCENT)
        self._primary_ink_is_light = False
        self._hover = 0.0
        self._scale = 1.0
        self._press_animation = QPropertyAnimation(self, b"visualScale", self)
        self._press_animation.setDuration(t.PRESS_MS)
        self._press_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.pressed.connect(lambda: self._animate_to(0.97))
        self.released.connect(lambda: self._animate_to(1.0))
        self._hover_animation = QPropertyAnimation(self, b"visualHover", self)
        self._hover_animation.setDuration(t.INTERACTION_MS)
        self._hover_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.setIconSize(QSize(SIZE, SIZE))
        self.toggled.connect(self._refresh_icon)
        self.objectNameChanged.connect(self._refresh_icon)
        self._refresh_icon()

    def set_line_icon(self, name):
        if name != self._line_icon:
            self._line_icon = name
            self._refresh_icon()

    def set_accent(self, color, ink_is_light=False):
        self._primary_ink_is_light = ink_is_light
        if self._line_icon:
            self._icon_accent = color
            self._refresh_icon()

    def _refresh_icon(self):
        if getattr(self, "_line_icon", None):
            self.setIcon(button_icon(self._line_icon, self._line_color()))

    def _line_color(self):
        if not self.isEnabled():
            return t.MUTED
        if self.objectName() == "primary":
            return t.TEXT if self._primary_ink_is_light else t.skin_for("valorant").background
        return self._icon_accent if self.isChecked() else NEUTRAL

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

    def set_reduced_motion(self, value):
        self._reduced = value
        if value:
            self._hover_animation.stop()
            self._press_animation.stop()
            self.visualHover = 0.0
            self.visualScale = 1.0

    def _animate_hover(self, target):
        if not self._reduced and self._line_icon and self.isEnabled() and self.isVisible() and not self.window().isMinimized():
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
        if self._reduced:
            return
        animation = self._press_animation
        animation.stop()
        animation.setStartValue(self._scale)
        animation.setEndValue(scale)
        animation.start()

    @Property(float)
    def dockOpacity(self):
        return self._dock_opacity

    @dockOpacity.setter
    def dockOpacity(self, opacity):
        self._dock_opacity = opacity
        self.update()

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
        if self._line_icon and self.isEnabled() and (self._hover or self._dock_opacity):
            option.icon = button_icon(self._line_icon, self._line_color(), round(self._hover * 4))
            option.iconSize = self.iconSize() * (1 + 0.28 * self._dock_opacity)
        painter.drawControl(QStyle.ControlElement.CE_PushButton, option)
        if scale < 1:
            painter.setPen(QColor("transparent"))
            painter.setBrush(QColor(0, 0, 0, round((1 - scale) * 1600)))
            pill = self.objectName() == "primary" or self.property("gamePill") or self.property("ghost")
            radius = 17 if pill else 10
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), radius, radius)


def tint_icons(parent, value):
    accent = accent_ink(value)
    for item in parent.findChildren(GlassButton):
        item.set_accent(accent, primary_ink(value) == t.TEXT)
    for item in parent.findChildren(LineIcon):
        item.set_accent(accent)
    for label in parent.findChildren(QLabel):
        if label.objectName() in ("muted", "statLabel", "modelTransfer") or label.property("metric"):
            track_caption(label)


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
        self.icon = LineIcon(icon, accented=True)
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
    pixmap.fill(QColor(palette.top))
    with QPainter(pixmap) as painter:
        rect = pixmap.rect()
        base = QLinearGradient(0, 0, width if palette.horizontal else width * 0.75,
                               0 if palette.horizontal else height)
        base.setColorAt(0, QColor(palette.top))
        base.setColorAt(1, QColor(palette.bottom))
        if palette.horizontal:
            color = QColor(palette.right_glow)
            color.setAlpha(t.SKIN_GLOW_ALPHA // 2)
            base.setColorAt(0.30, color)
        painter.fillRect(rect, base)
        if palette.horizontal:
            return pixmap
        lights = ((palette.origin[0], palette.origin[1], 0.92, palette.right_glow, t.SKIN_GLOW_ALPHA),
                  (1 - palette.origin[0], 0.08, 0.63, palette.upper_glow, 78)) if palette.origin else (
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

    def set_background(self, theme: str, custom_color: str, game=None):
        palette = background_palette(theme, custom_color, game)
        if palette != self._background:
            self._background = palette
            self.update()

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(self.rect(), _background_pixmap(self._background))


def primary_ink(value):
    channels = QColor(accent_color(value)).getRgbF()[:3]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    luminance = sum(c * weight for c, weight in zip(linear, (0.2126, 0.7152, 0.0722)))
    return t.TEXT if luminance < 0.17 else t.skin_for("valorant").background


def rgba(value, alpha):
    color = QColor(value)
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha})"


def glass_fill(panel, opacity=t.GLASS_FILL_ALPHA):
    highlight = rgba(t.MUTED, round(opacity * 0.24))
    fill = rgba(panel, opacity)
    return f"qlineargradient(x1:0,y1:0,x2:0.24,y2:1, stop:0 {highlight}, stop:0.16 {fill}, stop:1 {fill})"


def glass_styles(accent: str, game="valorant") -> str:
    accent = accent_color(accent)
    edge = accent_ink(accent)
    skin = t.skin_for(game)
    surface, panel = skin.background, skin.panel
    ink = primary_ink(accent)
    display = display_style()
    rim = rgba(t.MUTED, t.GLASS_RIM_ALPHA)
    top = rgba(t.MUTED, t.GLASS_HIGHLIGHT_ALPHA)
    return f"""
        * {{ color: {t.TEXT}; font-family: "{t.BODY_FONT}"; font-size: {t.BODY}px; }}
        QLabel, QScrollArea, QScrollArea QWidget#pageBody, QTabWidget::pane {{
            background: transparent; border: none;
        }}
        QWidget#gameNotice, QLabel#muted, QLabel#statLabel, QLabel#lastDiscard {{
            color: {t.MUTED}; font-size: {t.CAPTION}px;
        }}
        QLabel#lastDiscard {{ color: {t.WARNING}; }}
        QLabel#title, QLabel#brand, QLabel#sectionTitle, QLabel#translation, QLabel#previousCaption {{
            {display}
        }}
        QLabel#title, QLabel#brand {{ font-size: {t.TITLE}px; }}
        QLabel#sectionTitle {{ font-size: {t.SECTION}px; }}
        QLabel#translation {{ font-size: {t.SUBTITLE}px; }}
        QLabel#translation[quiet="true"] {{ font-size: {t.TITLE}px; }}
        QLabel#previousCaption {{ font-size: {t.SECTION}px; color: {t.MUTED}; }}
        QLabel#statValue {{ font-size: {t.BODY}px; font-weight: 500; }}
        QLabel[metric="true"], QLabel#modelTransfer {{
            font-family: {t.NUMBER_STACK}, "{t.BODY_FONT}"; font-size: {t.CAPTION}px;
        }}
        QFrame#warning, QFrame#updateStrip, QWidget#historyPage, QLabel#toast {{
            background: {panel}; border: 1px solid {t.EDGE}; border-radius: {t.CONTROL_RADIUS}px;
        }}
        QFrame#warning QLabel {{ color: {t.WARNING}; font-size: {t.CAPTION}px; }}
        QLabel#toast {{ padding: {t.INLINE}px {t.INSET}px; color: {t.INFO}; }}
        QLabel#toast[success="true"] {{ color: {t.SUCCESS}; }}
        QFrame#hero, QFrame#sidebar, QFrame#glassCard, QGroupBox#glassSection, QFrame#dock {{
            background: {glass_fill(panel)}; border: 1px solid {rim}; border-top-color: {top};
            border-radius: {t.RADIUS}px;
        }}
        QFrame#hero {{
            background: {glass_fill(surface)}; border: 1px solid {rim}; border-top-color: {top};
        }}
        QFrame#captionWell {{ background: {surface}; border: 0; border-radius: {t.CONTROL_RADIUS}px; }}
        QFrame#hero QPushButton {{ padding: {t.HERO_BUTTON_PAD_Y}px {t.INSET}px; }}
        QPushButton[gamePill="true"]:checked {{
            background: {rgba(accent, 58)}; border: 1px solid {edge};
        }}
        QPushButton[gamePill="true"]:checked:focus {{
            background: {rgba(accent, 78)}; border: 1px solid {edge};
        }}
        QGroupBox#glassSection {{ margin: 0; padding: 0; }}
        QPushButton, QComboBox, QSpinBox, QLineEdit {{
            background: {surface}; border: 1px solid {t.EDGE}; border-radius: {t.CONTROL_RADIUS}px;
            padding: {t.INSET}px;
        }}
        QPushButton[gamePill="true"], QPushButton[navigation="true"] {{
            padding: {t.INSET}px; border-radius: {t.RADIUS}px;
        }}
        QPushButton[navigation="true"] {{ text-align: left; }}
        QComboBox, QSpinBox, QLineEdit {{ padding: {t.INLINE}px {t.INSET}px; }}
        QComboBox QAbstractItemView, QMenu, QToolTip {{
            background: {panel}; color: {t.TEXT}; border: 1px solid {t.EDGE};
        }}
        QPushButton:hover, QCheckBox:hover, QComboBox:hover, QLineEdit:hover, QSpinBox:hover {{
            border-color: {t.MUTED};
        }}
        QPushButton:focus, QPushButton#primary:focus, QPushButton[ghost="true"]:focus, QCheckBox:focus,
        QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QAbstractItemView:focus {{
            border: 2px solid {t.TEXT};
        }}
        QCheckBox {{ border: 2px solid transparent; }}
        QPushButton:checked {{ border-color: {edge}; font-weight: 600; }}
        QPushButton:checked:focus {{ border-color: {t.TEXT}; }}
        QPushButton:pressed {{ background: {panel}; }}
        QPushButton#primary {{
            border: 1px solid {edge}; border-radius: {t.RADIUS}px; font-weight: 600;
            background: {accent}; color: {ink};
        }}
        QPushButton#primary:hover {{ border-color: {t.TEXT}; }}
        QPushButton[ghost="true"] {{ border-radius: {t.RADIUS}px; border-color: {t.MUTED}; }}
        QPushButton#guideAction:enabled {{ border: 1px solid {rim}; }}
        QPushButton#guideAction:enabled:focus, QPushButton#guideAction:enabled:hover {{
            border: 1px solid {edge}; background: {rgba(accent, 36)};
        }}
        QPushButton:disabled, QPushButton#primary:disabled, QPushButton[ghost="true"]:disabled {{
            color: {t.MUTED}; background: {panel}; border-color: {t.EDGE};
        }}
        QFrame#dock QPushButton {{ padding: 0; border-color: transparent; }}
        QFrame#dock QPushButton:focus, QFrame#dock QPushButton:hover {{ border-color: {t.MUTED}; }}
        QFrame#skeleton {{ background: {t.EDGE}; border-radius: {t.TIGHT}px; }}
        QProgressBar {{
            border: 1px solid {t.EDGE}; border-radius: {t.TIGHT}px; background: {surface};
            text-align: center; height: {t.INSET}px; font-family: {t.NUMBER_STACK};
        }}
        QProgressBar::chunk {{ background: {t.EDGE}; border-radius: {t.TIGHT}px; }}
        QScrollBar:vertical {{ background: {surface}; width: {t.INLINE}px; }}
        QScrollBar::handle:vertical {{ background: {t.EDGE}; min-height: {t.SPACE}px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
        QStatusBar, QHeaderView::section {{ background: {panel}; }}
        QStatusBar QLabel, QHeaderView::section {{ font-size: {t.CAPTION}px; }}
        QHeaderView::section {{ padding: {t.INLINE}px; }}
        QTableWidget {{ background: {surface}; gridline-color: {t.EDGE}; }}
    """
