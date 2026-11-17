from __future__ import annotations

import pytest

from valorant_translator.models import download as models


def _write_required(folder, names):
    folder.mkdir(parents=True)
    for name in names:
        (folder / name).write_bytes(b"test")


def test_import_model_folder_and_reject_incomplete(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "MODEL_DIR", tmp_path / "installed")
    source = tmp_path / "source"
    _write_required(source / "whisper-small", models.WHISPER_FILES)
    _write_required(source / "m2m100_418M", models.M2M_FILES)
    statuses = []
    models.import_models(source, "small", statuses.append)
    assert models.models_ready("small")
    assert statuses[-1] == "模型导入完成。"
    (tmp_path / "installed" / "m2m100_418M" / "pytorch_model.bin").unlink()
    _write_required(tmp_path / "installed" / "nllb-200-distilled-600M",
                    ("pytorch_model.bin", "config.json", "sentencepiece.bpe.model"))
    assert not models.models_ready("small")
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError, match="找不到完整模型"):
        models.import_models(tmp_path / "empty", "small", statuses.append)


def test_download_uses_explicit_model_files(tmp_path, monkeypatch):
    import huggingface_hub

    monkeypatch.setattr(models, "MODEL_DIR", tmp_path / "models")

    def fake_snapshot_download(repo_id, local_dir, allow_patterns):
        assert repo_id in {models.M2M_REPO, models.WHISPER_REPOS["small"]}
        required = models.WHISPER_FILES if "whisper" in repo_id else models.M2M_FILES
        assert all(name in allow_patterns for name in required)
        _write_required(local_dir, required)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot_download)
    statuses = []
    models.download_models("small", statuses.append)
    assert models.models_ready("small")
    assert "断网" in statuses[-1]
