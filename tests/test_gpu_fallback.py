from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

import numpy as np

from valorant_translator.recognition import whisper
from valorant_translator.translation.m2m import LocalM2M


def test_whisper_cuda_load_failure_falls_back_to_cpu(monkeypatch, tmp_path):
    model_dir = tmp_path / "whisper-small"
    model_dir.mkdir()
    calls = []

    def fake_model(path, device, compute_type, **kwargs):
        calls.append((device, compute_type))
        if device == "cuda":
            raise RuntimeError("CUDA library unavailable")
        return object()

    import faster_whisper
    monkeypatch.setattr(faster_whisper, "WhisperModel", fake_model)
    monkeypatch.setattr(whisper, "MODEL_DIR", tmp_path)
    monkeypatch.setattr(whisper, "cuda_available", lambda: True)
    loaded = whisper.LocalWhisper("small", "auto")
    assert loaded.device == "cpu"
    assert calls == [("cuda", "float16"), ("cpu", "int8")]


def test_whisper_cuda_inference_failure_reloads_on_cpu(monkeypatch, tmp_path):
    import faster_whisper

    calls = []

    def fake_model(_path, device, compute_type, **_kwargs):
        calls.append((device, compute_type))
        return object()

    monkeypatch.setattr(faster_whisper, "WhisperModel", fake_model)
    recognizer = whisper.LocalWhisper.__new__(whisper.LocalWhisper)
    recognizer.device = "cuda"
    recognizer.path = tmp_path
    recognizer.model = object()

    def fake_transcribe(_audio, _language):
        if recognizer.device == "cuda":
            raise RuntimeError("CUDA error: no kernel image is available for execution on the device")
        return "recognized"

    monkeypatch.setattr(recognizer, "_transcribe", fake_transcribe)
    assert recognizer.transcribe(np.zeros(16000), "en") == "recognized"
    assert recognizer.device == "cpu"
    assert calls == [("cpu", "int8")]


def test_m2m_cuda_kernel_failure_reloads_on_cpu(monkeypatch):
    class Batch(dict):
        def to(self, device):
            self.device = device
            return self

    class Tokenizer:
        def __call__(self, *_args, **_kwargs):
            return Batch()

        def get_lang_id(self, _language):
            return 1

        def batch_decode(self, *_args, **_kwargs):
            return ["快攻 A"]

    class Model:
        def __init__(self, fail=False):
            self.fail = fail

        def generate(self, **_kwargs):
            if self.fail:
                raise RuntimeError("CUDA error: no kernel image is available for execution on the device")
            return [1]

    translator = LocalM2M.__new__(LocalM2M)
    translator.device = "cuda"
    translator.fast_mode = True
    translator.model = Model(fail=True)
    translator.model_type = Model
    translator.tokenizer = Tokenizer()
    translator.converter = SimpleNamespace(convert=lambda value: value)
    translator.torch = SimpleNamespace(inference_mode=nullcontext,
                                       cuda=SimpleNamespace(empty_cache=lambda: None))
    loads = []

    def load_model(model_type):
        loads.append((model_type, translator.device))
        return Model()

    monkeypatch.setattr(translator, "_load_model", load_model)
    assert translator.translate("rush A", "en") == "快攻 A"
    assert translator.device == "cpu"
    assert loads == [(Model, "cpu")]
