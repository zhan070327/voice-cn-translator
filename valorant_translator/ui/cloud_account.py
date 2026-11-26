"""Account and checkout dialogs for SameFreq Cloud."""
from __future__ import annotations

import base64
import binascii

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit

from valorant_translator.ui.dialogs import apply_dialog_theme
from valorant_translator.ui.glass import GlassButton
from valorant_translator.ui.texts import tr


def _plain_label(text=""):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class AccountDialog(QDialog):
    sign_in = Signal(str, str, str)
    sign_out = Signal()
    subscribe = Signal()

    def __init__(self, signed_in, balance, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(tr("engine.cloud_account"))
        self.setMinimumWidth(370)
        self.signed_in = signed_in
        form = QFormLayout(self)
        self.balance = _plain_label(balance)
        self.message = _plain_label()
        self.message.hide()
        buttons = QHBoxLayout()
        if signed_in:
            form.addRow(self.balance)
            sign_out = GlassButton(tr("engine.sign_out"))
            sign_out.clicked.connect(self._sign_out)
            subscribe = GlassButton(tr("engine.subscribe"))
            subscribe.clicked.connect(self.subscribe.emit)
            self.controls = (sign_out, subscribe)
            buttons.addWidget(subscribe)
            buttons.addWidget(sign_out)
        else:
            self.email = QLineEdit()
            self.email.setInputMethodHints(Qt.InputMethodHint.ImhEmailCharactersOnly)
            self.password = QLineEdit()
            self.password.setEchoMode(QLineEdit.EchoMode.Password)
            form.addRow(tr("engine.email"), self.email)
            form.addRow(tr("engine.password"), self.password)
            login = GlassButton(tr("engine.login"))
            register = GlassButton(tr("engine.register"))
            login.setDefault(True)
            login.clicked.connect(lambda: self._authenticate("login"))
            register.clicked.connect(lambda: self._authenticate("register"))
            self.controls = (self.email, self.password, login, register)
            buttons.addWidget(login)
            buttons.addWidget(register)
            self.email.setFocus()
        form.addRow(self.message)
        form.addRow(buttons)
        apply_dialog_theme(self)

    def set_balance(self, balance):
        self.balance.setText(balance)

    def _set_busy(self, busy):
        for control in self.controls:
            control.setEnabled(not busy)
        self.message.setText(tr("engine.checking") if busy else "")
        self.message.setVisible(busy)

    def _authenticate(self, action):
        email, password = self.email.text().strip(), self.password.text()
        if not email or not password:
            self.message.setText(tr("engine.enter_credentials"))
            self.message.show()
            (self.password if email else self.email).setFocus()
            return
        self._set_busy(True)
        self.sign_in.emit(action, email, password)

    def _sign_out(self):
        self._set_busy(True)
        self.sign_out.emit()

    def complete(self, error):
        if not error:
            self.accept()
            return
        self._set_busy(False)
        message = {
            "invalid_credentials": "login_failed",
            "email_in_use": "email_exists",
            "invalid_input": "invalid_account_fields",
            "account_blocked": "account_blocked",
            "rate_limited": "rate_limited",
            "unauthenticated": "login_failed",
        }.get(error, "account_unavailable")
        self.message.setText(tr(f"engine.{message}"))
        self.message.show()


class PlanDialog(QDialog):
    order = Signal(str, str)
    channels = ("wechat", "alipay")

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(tr("engine.subscribe"))
        self.setMinimumWidth(370)
        form = QFormLayout(self)
        self.plans = QComboBox()
        self.channels_combo = QComboBox()
        self.plans.currentIndexChanged.connect(self._set_channels)
        self.pay = GlassButton(tr("engine.create_order"))
        self.pay.clicked.connect(self._order)
        self.message = _plain_label(tr("engine.checking"))
        form.addRow(self.message)
        form.addRow(tr("engine.plan"), self.plans)
        form.addRow(tr("engine.payment_channel"), self.channels_combo)
        form.addRow(self.pay)
        self._set_busy(True)
        apply_dialog_theme(self)

    def _set_busy(self, value):
        self.plans.setEnabled(not value and self.plans.count() > 0)
        self.channels_combo.setEnabled(not value and self.channels_combo.count() > 0)
        self.pay.setEnabled(self.channels_combo.isEnabled())

    def set_plans(self, plans, error):
        if error:
            message = "account_unavailable"
        elif not plans:
            message = "plans_unavailable"
        else:
            message = ""
            for plan in plans:
                cents = plan["price_cents"]
                price = f"{cents // 100}.{cents % 100:02d}"
                self.plans.addItem(tr("engine.plan_price", name=plan["name"], price=price), plan)
        self.message.setText(tr(f"engine.{message}") if message else "")
        self.message.setVisible(bool(message))
        self._set_busy(False)

    def _set_channels(self, index):
        self.channels_combo.clear()
        for channel in (self.plans.currentData() or {}).get("channels", self.channels):
            if channel in self.channels:
                self.channels_combo.addItem(tr(f"engine.pay_{channel}"), channel)
        self._set_busy(False)

    def _order(self):
        self._set_busy(True)
        self.message.setText(tr("engine.checking"))
        self.message.show()
        self.order.emit(self.plans.currentData()["id"], self.channels_combo.currentData())

    def order_error(self, error):
        self._set_busy(False)
        self.message.setText(tr("engine.rate_limited" if error == "rate_limited" else "engine.order_unavailable"))
        self.message.show()


class PaymentDialog(QDialog):
    def __init__(self, order, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(tr("engine.payment"))
        form = QFormLayout(self)
        image = QPixmap()
        try:
            image.loadFromData(base64.b64decode(order.get("qr_png_base64", ""), validate=True), "PNG")
        except (ValueError, binascii.Error):
            pass
        if not image.isNull():
            preview = QLabel()
            preview.setAccessibleName(tr("engine.payment_qr"))
            preview.setPixmap(image)
            preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
            form.addRow(preview)
            form.addRow(_plain_label(tr("engine.payment_scan")))
        else:
            url = QUrl(order.get("pay_url", ""))
            launch = GlassButton(tr("engine.open_payment"))
            launch.setEnabled(url.isValid() and url.scheme() in ("https", "weixin", "alipays"))
            launch.clicked.connect(lambda: QDesktopServices.openUrl(url))
            form.addRow(launch)
        done = GlassButton(tr("engine.payment_done"))
        done.clicked.connect(self.accept)
        form.addRow(done)
        apply_dialog_theme(self)
