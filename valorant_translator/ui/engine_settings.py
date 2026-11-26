"""Edit the user's Azure credentials without sending a request."""
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
)

from valorant_translator.models.azure import valid_resource
from valorant_translator.ui.dialogs import apply_dialog_theme
from valorant_translator.ui.texts import tr


class AzureCredentialsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("engine.credentials"))
        self.setMinimumWidth(380)
        form = QFormLayout(self)
        self.cloud = QComboBox()
        for value in ("global", "china"):
            self.cloud.addItem(tr(f"engine.cloud_{value}"), value)
        self.cloud.setCurrentIndex(max(0, self.cloud.findData(settings.azure_cloud)))
        form.addRow(tr("engine.cloud"), self.cloud)
        self.edits = {}
        for field in ("speech_resource", "speech_key", "translator_region", "translator_key"):
            value = getattr(settings, f"azure_{field}")
            edit = QLineEdit(value if isinstance(value, str) else "")
            if field.endswith("_key"):
                edit.setEchoMode(QLineEdit.EchoMode.Password)
            self.edits[field] = edit
            form.addRow(tr(f"engine.{field}"), edit)
        self.edits["translator_region"].setPlaceholderText(tr("engine.region_placeholder"))
        self.hint = QLabel(tr("engine.credential_storage"))
        self.hint.setWordWrap(True)
        form.addRow(self.hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(tr("engine.save"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("engine.cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        apply_dialog_theme(self, settings)

    def values(self):
        return {"azure_cloud": self.cloud.currentData(), **{
            f"azure_{name}": edit.text().strip() for name, edit in self.edits.items()
        }}

    def accept(self):
        values = self.values()
        region = values["azure_translator_region"].lower()
        resource = values["azure_speech_resource"].lower()
        if ((resource and not valid_resource(resource)) or (region and not valid_resource(region))
                or (values["azure_cloud"] == "china" and region == "global")):
            self.hint.setText(tr("engine.invalid_resource"))
            return
        super().accept()
