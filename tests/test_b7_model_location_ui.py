import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from valorant_translator import config
from valorant_translator.models.location import InsufficientModelSpace
from valorant_translator.ui import app as ui
from valorant_translator.ui.texts import tr


@pytest.fixture
def model_window(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(ui, "MODEL_DIR", tmp_path / "default-models")
    monkeypatch.setattr(ui, "list_devices", lambda: [])
    monkeypatch.setattr(ui, "load_channel", lambda: None)
    monkeypatch.setattr(ui.MainWindow, "_start_hotkey", lambda self: None)
    config.Settings(routing_intro_seen=True).save()
    window = ui.MainWindow()
    try:
        yield window
    finally:
        window.close()


def wait_for_model_move(window):
    deadline = time.monotonic() + 4
    while window._model_busy and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    assert not window._model_busy


def test_model_location_picker_moves_models_and_refreshes_saved_path(model_window, monkeypatch, tmp_path):
    old = ui.MODEL_DIR
    destination = tmp_path / "selected models"
    destination.mkdir()
    for folder, files in (("whisper-small", ui.WHISPER_FILES), ("m2m100_418M", ui.M2M_FILES)):
        (old / folder).mkdir(parents=True)
        for file in files:
            (old / folder / file).write_bytes(b"existing model")
    partial = ".cos-small.zip.part"
    (old / partial).write_bytes(b"partial")
    monkeypatch.setattr(ui.QFileDialog, "getExistingDirectory", lambda *_: str(destination))

    assert model_window.model_location_path.isReadOnly()
    assert Path(model_window.model_location_path.text()) == old
    model_window.model_location_button.click()
    wait_for_model_move(model_window)

    assert Path(model_window.model_location_path.text()) == destination.resolve()
    assert config.Settings.load().model_dir == str(destination.resolve())
    assert model_window._models_ready()
    assert (destination / partial).read_bytes() == b"partial"
    assert not (old / partial).exists()


def test_model_location_picker_reports_space_and_keeps_previous_path(model_window, monkeypatch, tmp_path):
    destination = tmp_path / "target"
    destination.mkdir()
    monkeypatch.setattr(ui.QFileDialog, "getExistingDirectory", lambda *_: str(destination))
    warnings = []
    monkeypatch.setattr(ui.QMessageBox, "warning", lambda *args: warnings.append(args[1:]))

    def fail_move(_settings, folder, **_kwargs):
        raise InsufficientModelSpace(folder, 8192, 1024)

    monkeypatch.setattr(ui, "move_model_dir", fail_move)
    model_window.model_location_button.click()
    wait_for_model_move(model_window)

    assert Path(model_window.model_location_path.text()) == ui.MODEL_DIR
    assert config.Settings.load().model_dir == ""
    assert warnings == [(tr("app.0320"), tr("app.0322", p0=destination.anchor, p1=8192, p2=1024))]
    assert model_window.model_location_button.isEnabled()
