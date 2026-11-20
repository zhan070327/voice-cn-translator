from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pytest

from valorant_translator.config import Settings
from valorant_translator.models import catalog, cos
from valorant_translator.models.download import WHISPER_FILES, model_ready
from valorant_translator.translation import glossary
from valorant_translator.ui.talk import PushToTalk
from valorant_translator.updates import channel, domestic, github

ROOT = Path(__file__).resolve().parents[1]


def test_b5_attachment_glossary_and_game_profiles():
    dictionary = glossary.Glossary(ROOT / "config/glossary.json")
    assert tuple(len(dictionary.data[key]) for key in (
        "exact_phrases", "location_aliases", "target_replacements", "recognition_hotwords",
        "entity_aliases", "translated_name_aliases", "contextual_rewrites")) == (561, 173, 268, 79, 41, 38, 14)
    assert dictionary.exact("finding") == "致盲。"
    assert dictionary.exact("veto ult") == "Veto 开大了。"
    assert dictionary.callout("hookah") == "B 水烟。"
    assert dictionary.callout("heaven") == "天台。"
    assert dictionary.exact("waylay flash") is None
    assert not glossary.game_glossary("cs2").callout("hookah")
    assert not glossary.game_glossary("apex").hotwords


def test_b4_builtin_copy_keeps_new_entries_and_user_changes(monkeypatch, tmp_path):
    custom = tmp_path / "glossary_custom.json"
    data = json.loads((ROOT / "config/glossary_legacy_b4.json").read_text())
    data["exact_phrases"]["go rush a"] = "走 A！"
    custom.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(glossary, "CUSTOM_GLOSSARY_FILE", custom)
    dictionary = glossary.Glossary()
    assert dictionary.exact("go rush a") == "走 A！"
    assert dictionary.callout("heaven") == "天台。"


def test_split_model_install_keeps_small_available_and_validates_inventory(monkeypatch, tmp_path):
    channel_config = domestic.load_channel(ROOT / "config/update_channel_domestic.json")
    temporary = tmp_path / "tiny.zip"
    with ZipFile(temporary, "w") as archive:
        for file in cos.required_files("whisper-tiny"):
            archive.writestr(f"whisper-tiny/{file}", b"abc")
    digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
    bundle = cos.Bundle.from_entry({"sha256": digest, "size": temporary.stat().st_size,
                                    "urls": [channel_config["model_manifest_url"].replace("models.json", "whisper-tiny.zip")]},
                                   channel_config["download_hosts"], ("whisper-tiny",))
    model_root = tmp_path / "models"
    model_root.mkdir()
    temporary.rename(model_root / f".cos-{digest}.zip.part")
    cos.install_models(bundle, model_root, lambda _: None, lambda *_: None, threading.Event())
    assert model_ready(model_root / "whisper-tiny", WHISPER_FILES)
    assert not (model_root / "whisper-small").exists()
    (model_root / "whisper-tiny/model.bin").write_bytes(b"x")
    assert not model_ready(model_root / "whisper-tiny", WHISPER_FILES)
    plan = catalog.required_downloads(channel_config, {"tiny": bundle}, "tiny", model_root)
    assert [folders for _, folders in plan] == [("whisper-tiny",), ("m2m100_418M",)]
    fallback = catalog.required_downloads(channel_config, {}, "small", model_root)
    assert fallback[0][1] == ("whisper-small", "m2m100_418M")


def test_push_to_talk_captures_only_selected_microphone_and_hands_text_to_ui():
    called = threading.Event()
    seen = []

    class Capture:
        def __init__(self, device, on_audio, *_):
            assert device == "mic-only"
            self.on_audio = on_audio

        def start(self):
            self.on_audio(np.ones(8000, dtype=np.float32), 0.0)

        def stop(self):
            pass

    def result(pair):
        seen.append(pair)
        called.set()

    class Recognition:
        text = "我们去 B 点"

    worker = PushToTalk(lambda _: None, result, Capture)
    assert worker.begin(Settings(microphone_id="mic-only"), lambda audio: (Recognition(), "Let's go to B."))
    worker.end()
    assert called.wait(3)
    assert seen == [("我们去 B 点", "Let's go to B.")]


def test_cpu_and_gpu_updates_are_disjoint_and_fallback_keeps_edition(monkeypatch):
    configs = domestic.load_channel(ROOT / "config/update_channel_domestic.json")
    configs.update(channel="dual", edition="cpu", github_repository="a/b")

    def entry(edition):
        return {"version": "0.3.0b6", "release_notes": "new", "sha256": "a" * 64, "size": 123,
                "download_url": f"https://{configs['download_hosts'][0]}/releases/0.3.0b6/{github.INSTALLERS[edition]}"}

    payload = {**entry("gpu"), "editions": {"cpu": entry("cpu")}}
    assert domestic.parse_manifest(json.dumps(entry("gpu")).encode(), configs["download_hosts"], edition="cpu") is None
    assert domestic.parse_manifest(json.dumps(payload).encode(), configs["download_hosts"]).installer_name == github.INSTALLER_NAME
    cpu = domestic.parse_manifest(json.dumps(payload).encode(), configs["download_hosts"], edition="cpu")
    assert cpu.installer_name == github.CPU_INSTALLER_NAME
    def cos_offline(*_):
        raise github.UpdateError("offline")
    monkeypatch.setattr(domestic, "find_update", cos_offline)
    def github_ok(repository, *, edition):
        assert repository == "a/b" and edition == "cpu"
        return cpu
    monkeypatch.setattr(github, "find_update", github_ok)
    assert channel.check_for_update(configs).source == "github"


def test_release_mirror_rejects_different_checksum():
    from scripts.publish_paired_release import verify_github_assets
    manifest = {"sha256": "1" * 64, "size": 3, "editions": {"cpu": {"sha256": "2" * 64, "size": 1}}}
    assets = [{"name": github.INSTALLER_NAME, "state": "uploaded", "digest": "sha256:" + "1" * 64, "size": 3},
              {"name": github.CPU_INSTALLER_NAME, "state": "uploaded", "digest": "sha256:" + "2" * 64, "size": 1}]
    verify_github_assets(assets, manifest)
    assets[1]["digest"] = "sha256:" + "0" * 64
    with pytest.raises(github.UpdateError):
        verify_github_assets(assets, manifest)


def test_cpu_packaging_prunes_vendor_cuda_but_refuses_cuda_torch(tmp_path):
    from scripts.audit_cuda_bundle import prune_cpu_runtime

    cuda = tmp_path / "cudnn64_9.dll"
    cpu = tmp_path / "torch_cpu.dll"
    cuda.write_bytes(b"CUDA")
    cpu.write_bytes(b"CPU")
    assert prune_cpu_runtime(tmp_path) == [cuda]
    assert not cuda.exists() and cpu.exists()
    torch_cuda = tmp_path / "torch_cuda.dll"
    torch_cuda.write_bytes(b"CUDA")
    with pytest.raises(ValueError, match="CUDA PyTorch"):
        prune_cpu_runtime(tmp_path)
    assert torch_cuda.exists()
