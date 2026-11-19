from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QRectF
from PySide6.QtGui import QColor, QPainter, QRadialGradient
from PySide6.QtWidgets import QPushButton, QStyle, QStyleOptionButton, QStylePainter, QWidget

DEFAULT_ACCENT = "#FF4655"


def accent_color(value: str) -> str:
    color = QColor(value)
    return color.name() if color.isValid() else DEFAULT_ACCENT


class GlassButton(QPushButton):
    """Animate the painting only, keeping the layout and hit target stable."""

    def __init__(self, *args):
        super().__init__(*args)
        self._scale = 1.0
        self._press_animation = QPropertyAnimation(self, b"visualScale", self)
        self._press_animation.setDuration(80)
        self._press_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.pressed.connect(lambda: self._animate_to(0.97))
        self.released.connect(lambda: self._animate_to(1.0))

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
        painter.drawControl(QStyle.ControlElement.CE_PushButton, option)
        if scale < 1:
            painter.setPen(QColor("transparent"))
            painter.setBrush(QColor(0, 0, 0, round((1 - scale) * 1600)))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 10, 10)


class GlassSurface(QWidget):
    def paintEvent(self, event):
        paint = QPainter(self)
        paint.fillRect(self.rect(), QColor("#111216"))
        spread = max(self.width(), self.height())
        for x, y, value, opacity in ((0.08, 0.06, "#c5cbd2", 36),
                                      (0.92, 0.62, "#909ba6", 25),
                                      (0.23, 1.00, "#98a2aa", 30)):
            gradient = QRadialGradient(self.width() * x, self.height() * y, spread * 0.65)
            color = QColor(value)
            color.setAlpha(opacity)
            gradient.setColorAt(0, color)
            gradient.setColorAt(1, QColor(0, 0, 0, 0))
            paint.fillRect(self.rect(), gradient)


def glass_styles(accent: str) -> str:
    accent = accent_color(accent)
    rgb = QColor(accent)
    brightness = (rgb.red() * 299 + rgb.green() * 587 + rgb.blue() * 114) / 1000
    foreground = "#16171b" if brightness >= 115 else "#ffffff"
    return """
        * { color: #f0edf2; font-family: "Microsoft YaHei", "Segoe UI", "PingFang SC"; font-size: 13px; }
        QLabel, QScrollArea, QScrollArea QWidget#pageBody, QTabWidget::pane {
            background: transparent; border: none;
        }
        QLabel#eyebrow, QLabel#muted { color: #bdc2c9; }
        QLabel#title { font-size: 27px; font-weight: 750; }
        QLabel#brand { font-size: 22px; font-weight: 750; }
        QLabel#translation { font-size: 23px; font-weight: 750; padding: 7px; }
        QLabel#statValue { font-size: 17px; font-weight: 700; }
        QLabel#warning { color: #ffe0ab; background: rgba(111,68,35,93);
            border: 1px solid rgba(255,210,132,103); border-radius: 12px; padding: 9px; }
        QLabel#lastDiscard { color: #f2d49b; }
        QFrame#hero, QFrame#sidebar, QFrame#glassCard, QGroupBox {
            background: qlineargradient(x1:0,y1:0,x2:0.9,y2:1,stop:0 rgba(228,231,237,34),
                         stop:1 rgba(157,164,174,19));
            border: 1px solid rgba(227,233,239,82); border-radius: 18px;
        }
        QFrame#sidebar {
            background: qlineargradient(x1:0,y1:0,x2:0.8,y2:1,stop:0 rgba(239,243,247,41),
                         stop:1 rgba(171,178,186,18));
        }
        QGroupBox { margin-top: 16px; padding: 17px 13px 13px 13px; font-weight: 600; }
        QGroupBox::title { subcontrol-origin: margin; left: 15px; padding: 0 7px; color: #e0e3e8; }
        QPushButton, QComboBox, QSpinBox, QLineEdit { background: rgba(239,243,247,19);
            border: 1px solid rgba(228,234,241,88); border-radius: 11px; padding: 8px 11px; }
        QComboBox QAbstractItemView, QMenu { background: #25272d; color: #f0edf2; }
        QPushButton:hover, QFrame#glassCard:hover { background: rgba(236,242,250,53); border-color: #c3cbd5; }
        QPushButton:checked { background: rgba(228,234,241,36); border-color: ACCENT; }
        QPushButton:pressed { background: rgba(239,243,247,11); }
        QPushButton#primary { border-color: ACCENT; background: ACCENT; color: FOREGROUND; font-weight: 700; }
        QPushButton#primary:hover { border-color: #f9e5e7; }
        QPushButton:disabled, QPushButton#primary:disabled {
            color: #90949b; background: #282a2e; border-color: #44474d; }
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
    """.replace("ACCENT", accent).replace("FOREGROUND", foreground)
