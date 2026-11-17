from __future__ import annotations

from valorant_translator.recognition import whisper


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
