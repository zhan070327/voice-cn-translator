from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from valorant_translator import config
from valorant_translator.models import catalog, cos, download, location, providers


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "MODEL_DIR", tmp_path / "models")
    return tmp_path


def _model(folder, names):
    folder.mkdir(parents=True)
    for name in names:
        (folder / name).write_bytes(b"model")


def test_default_and_saved_model_paths(storage):
    assert config.get_model_dir() == storage / "models"
    config.CONFIG_FILE.write_text('{"language":"en"}', encoding="utf-8")
    assert config.get_model_dir(config.Settings.load()) == storage / "models"
    config.Settings(model_dir=str(storage / "disk-g" / "models")).save()
    assert config.get_model_dir() == storage / "disk-g" / "models"


def test_move_preserves_models_and_resume_files(storage):
    settings = config.Settings()
    source = config.get_model_dir(settings)
    _model(source / "whisper-small", download.WHISPER_FILES)
    _model(source / "whisper-base", download.WHISPER_FILES)
    _model(source / "m2m100_418M", download.M2M_FILES)
    partial = ".cos-test.zip.part"
    (source / partial).write_bytes(b"resume")
    (source / "other.txt").write_bytes(b"keep")
    dest = storage / "new disk"
    dest.mkdir()

    assert location.move_model_dir(settings, dest) == dest
    assert config.get_model_dir() == dest
    assert download.models_ready("small", folder=dest)
    assert download.models_ready("base", folder=dest)
    assert (dest / partial).read_bytes() == b"resume"
    assert not (source / partial).exists()
    assert not (source / "whisper-small").exists()
    assert (source / "other.txt").read_bytes() == b"keep"


@pytest.mark.parametrize("failure", ["copy", "save", "space"])
def test_failed_migration_keeps_original_models_and_settings(storage, monkeypatch, failure):
    settings = config.Settings()
    settings.save()
    original = config.CONFIG_FILE.read_bytes()
    source = config.get_model_dir(settings)
    _model(source / "whisper-small", download.WHISPER_FILES)
    dest = storage / "other-disk"
    dest.mkdir()

    def fail(*args, **kwargs):
        raise OSError("simulated failure")

    if failure == "copy":
        monkeypatch.setattr(location.shutil, "copytree", fail)
    elif failure == "save":
        monkeypatch.setattr(settings, "save", fail)
    else:
        monkeypatch.setattr(location.shutil, "disk_usage", lambda path: SimpleNamespace(free=0))

    with pytest.raises(OSError):
        location.move_model_dir(settings, dest)
    assert config.CONFIG_FILE.read_bytes() == original
    assert settings.model_dir == ""
    assert download.model_ready(source / "whisper-small", download.WHISPER_FILES)
    assert not (dest / "whisper-small").exists()


def test_cos_space_check_blocks_before_transfer_and_counts_resume_bytes(storage, monkeypatch):
    folder = storage / "other-disk"
    fallback = cos.Bundle(("https://example.cn/model.zip",), 100, "a" * 64)
    folder.mkdir()
    part = folder / f".cos-{fallback.sha256}.zip.part"
    part.write_bytes(b"x" * 25)

    def no_request(*args, **kwargs):
        pytest.fail("Should check disk before opening a network connection")

    def disk_usage(path):
        assert path == folder
        return SimpleNamespace(free=50)

    monkeypatch.setattr(location.shutil, "disk_usage", disk_usage)
    monkeypatch.setattr(cos, "_download", no_request)
    with pytest.raises(location.InsufficientModelSpace) as err:
        cos.install_models(fallback, folder, lambda _: None, lambda *_: None, Event())
    assert (err.value.required, err.value.available) == (175, 50)
    assert part.stat().st_size == 25


def test_import_space_check_uses_actual_file_sizes(storage, monkeypatch):
    source = storage / "source"
    _model(source / "whisper-small", download.WHISPER_FILES)
    folder = storage / "target"
    monkeypatch.setattr(location.shutil, "disk_usage", lambda path: SimpleNamespace(free=9))
    with pytest.raises(location.InsufficientModelSpace) as err:
        download.import_models(source, "small", lambda _: None, folder=folder)
    assert err.value.required == 15
    assert not folder.exists()


def test_custom_location_reaches_both_model_provider_factories(storage, monkeypatch):
    from valorant_translator.recognition import whisper
    from valorant_translator.translation import m2m

    def provider(*args, model_dir):
        assert isinstance(model_dir, Path)
        return model_dir

    monkeypatch.setattr(whisper, "LocalWhisper", provider)
    monkeypatch.setattr(m2m, "LocalM2M", provider)
    folder = storage / "custom"
    settings = config.Settings(model_dir=str(folder))
    manager = providers.default_model_manager()
    assert manager.create_asr(settings) == folder
    assert manager.create_translation(settings) == folder


def test_cos_full_plan_is_preflighted_before_first_transfer(storage, monkeypatch):
    folder = storage / "target"
    tiny = cos.Bundle(("https://example.cn/whisper-tiny.zip",), 10, "a" * 64, ("whisper-tiny",))
    combined = cos.Bundle(("https://example.cn/models.zip",), 100, "b" * 64,
                          component_bytes=(("whisper-small", 20), ("m2m100_418M", 80)))
    plan = [(tiny, ("whisper-tiny",)), (combined, ("m2m100_418M",))]
    monkeypatch.setattr(catalog, "required_downloads", lambda *_: plan)
    monkeypatch.setattr(location.shutil, "disk_usage", lambda path: SimpleNamespace(free=150))

    def no_transfer(*args, **kwargs):
        pytest.fail("Space for the second model must be checked before the first transfer")

    monkeypatch.setattr(cos, "install_models", no_transfer)
    with pytest.raises(location.InsufficientModelSpace) as err:
        catalog.install_selected({}, {}, "tiny", folder, lambda _: None, lambda *_: None, Event())
    assert err.value.required == 190
    assert not folder.exists()
