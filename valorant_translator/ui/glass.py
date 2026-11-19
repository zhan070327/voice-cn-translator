from PySide6.QtGui import QColor, QPainter, QRadialGradient
from PySide6.QtWidgets import QWidget

DEFAULT_ACCENT = "#FF4655"


def accent_color(value: str) -> str:
    color = QColor(value)
    return color.name() if color.isValid() else DEFAULT_ACCENT


class GlassSurface(QWidget):
    def __init__(self, accent: str, parent=None):
        super().__init__(parent)
        self.accent = accent_color(accent)

    def paintEvent(self, event):
        paint = QPainter(self)
        paint.fillRect(self.rect(), QColor("#101016"))
        spread = max(self.width(), self.height())
        for x, y, value, opacity in ((0.96, 0.86, self.accent, 86),
                                      (0.08, 0.12, "#577d95", 70),
                                      (0.36, 0.94, "#455970", 54),
                                      (0.78, 0.08, self.accent, 30)):
            gradient = QRadialGradient(self.width() * x, self.height() * y, spread * 0.65)
            color = QColor(value)
            color.setAlpha(opacity)
            gradient.setColorAt(0, color)
            gradient.setColorAt(1, QColor(0, 0, 0, 0))
            paint.fillRect(self.rect(), gradient)


def glass_styles(accent: str) -> str:
    accent = accent_color(accent)
    return """
        * { color: #f0edf2; font-family: "Microsoft YaHei", "Segoe UI", "PingFang SC"; font-size: 13px; }
        QLabel, QScrollArea, QScrollArea QWidget#pageBody, QTabWidget::pane {
            background: transparent; border: none;
        }
        QLabel#eyebrow, QLabel#muted { color: #bdc4d0; }
        QLabel#title { font-size: 27px; font-weight: 750; }
        QLabel#brand { font-size: 22px; font-weight: 750; }
        QLabel#translation { font-size: 23px; font-weight: 750; padding: 7px; }
        QLabel#statValue { font-size: 17px; font-weight: 700; }
        QLabel#warning { color: #ffe0ab; background: rgba(111,68,35,93);
            border: 1px solid rgba(255,210,132,103); border-radius: 12px; padding: 9px; }
        QFrame#hero, QFrame#sidebar, QFrame#glassCard, QGroupBox {
            background: qlineargradient(x1:0,y1:0,x2:0.9,y2:1,stop:0 rgba(235,230,240,39),
                         stop:1 rgba(116,113,128,26));
            border: 1px solid rgba(241,224,236,99); border-radius: 18px;
        }
        QFrame#sidebar { background: rgba(202,210,228,29); }
        QGroupBox { margin-top: 16px; padding: 17px 13px 13px 13px; font-weight: 600; }
        QGroupBox::title { subcontrol-origin: margin; left: 15px; padding: 0 7px; color: #dcc8d7; }
        QPushButton, QComboBox, QSpinBox, QLineEdit { background: rgba(246,236,249,24);
            border: 1px solid rgba(235,225,244,113); border-radius: 11px; padding: 8px 11px; }
        QComboBox QAbstractItemView, QMenu { background: #27232d; color: #f0edf2; }
        QPushButton:hover, QFrame#glassCard:hover { background: rgba(240,231,249,44); border-color: #e7cbd9; }
        QPushButton:pressed, QPushButton:checked { background: rgba(189,84,104,105); border-color: ACCENT; }
        QPushButton:disabled { color: #a4a1ad; background: rgba(190,175,207,10); border-color: #554d5e; }
        QPushButton#primary { border-color: ACCENT; background: rgba(178,59,77,126); font-weight: 700; }
        QPushButton#primary:hover { background: rgba(202,74,93,170); }
        QPushButton#primary:disabled { background: #332733; border-color: #573b49; }
        QProgressBar { border: 1px solid #6f6374; border-radius: 7px; background: rgba(15,14,23,130);
            text-align: center; height: 15px; }
        QProgressBar::chunk { background: ACCENT; border-radius: 6px; }
        QScrollBar:vertical { background: rgba(15,14,24,79); width: 9px; margin: 2px; }
        QScrollBar::handle:vertical { background: rgba(225,204,226,109); border-radius: 4px; min-height: 28px; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
        QStatusBar { background: #17151f; }
        QTableWidget { background: rgba(17,15,26,175); gridline-color: #5c5062; }
        QHeaderView::section { background: #332b38; padding: 7px; }
    """.replace("ACCENT", accent)
