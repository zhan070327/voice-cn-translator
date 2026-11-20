import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QPixmapCache
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app

    # Dispose dialogs and their child models while the Qt platform is still alive.
    app.closeAllWindows()
    for widget in app.topLevelWidgets():
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()

    # These Python caches own Qt graphics objects that otherwise outlive QApplication.
    from valorant_translator.ui.glass import _background_pixmap
    from valorant_translator.ui.icons import button_icon

    _background_pixmap.cache_clear()
    button_icon.cache_clear()
    QPixmapCache.clear()
