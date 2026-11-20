import pytest

from scripts import publish_paired_release as paired
from valorant_translator.updates.github import INSTALLERS, UpdateError


@pytest.mark.parametrize("case", ["ok", "gpu_hash", "cpu_size", "manifest_changed"])
def test_cos_only_pins_both_installers_and_writes_manifest_last(monkeypatch, tmp_path, case):
    gpu = tmp_path / "gpu"
    cpu = tmp_path / "cpu"
    preview = tmp_path / "preview.json"
    payload = b'{"version":"0.3.0b5"}'
    manifest = {
        "size": 1_623_751_846,
        "sha256": "a9a788002ab354611c01932041c2afcadfbab1999e8325724b70d25b7f273a8d",
        "editions": {"cpu": {
            "size": 216_024_614,
            "sha256": "d40232ea5edc272d6a984a11d891d1abb66badaf7916189acd7c557d8a1e5122",
        }},
    }
    if case == "gpu_hash":
        manifest["sha256"] = "0" * 64
    if case == "cpu_size":
        manifest["editions"]["cpu"]["size"] -= 1
    events = []
    remote = None

    def prepare(gpu_dir, cpu_dir, output, **pins):
        assert (gpu_dir, cpu_dir, output) == (gpu, cpu, preview)
        assert pins == {
            "expected_commit": "aac7062ce6cff3bb65aac7c5ad2ab1f83ee042d8",
            "expected_run": "44689887810", "expected_attempt": "1",
        }
        output.write_bytes(payload)
        return manifest, "", pins["expected_commit"]

    class Client:
        def put_object(self, **kwargs):
            nonlocal remote
            assert kwargs["Key"] == "latest.json" and kwargs["Body"] == payload
            events.append("latest.json")
            remote = payload

    def stage(client, path, entry):
        nonlocal remote
        events.append(path.name)
        if case == "manifest_changed":
            remote = b'{"version":"0.3.0b6"}'

    def forbid_github(*_args):
        pytest.fail("COS-only publishing contacted GitHub Releases")

    monkeypatch.setattr(paired, "prepare", prepare)
    monkeypatch.setattr(paired.cos, "_make_client", Client)
    monkeypatch.setattr(paired.cos, "_read_remote_manifest", lambda client: remote)
    monkeypatch.setattr(paired.cos, "_probe_public_model", lambda channel: None)
    monkeypatch.setattr(paired.upload, "require_unversioned", lambda client: None)
    monkeypatch.setattr(paired, "_stage_installer", stage)
    monkeypatch.setattr(paired, "_gh", forbid_github)

    if case == "ok":
        paired.publish_b5_cos_only(gpu, cpu, preview)
        assert events == [INSTALLERS["gpu"], INSTALLERS["cpu"], "latest.json"]
        assert remote == payload
    else:
        message = "changed during staging" if case == "manifest_changed" else "SHA-256 or size"
        with pytest.raises(UpdateError, match=message):
            paired.publish_b5_cos_only(gpu, cpu, preview)
        assert "latest.json" not in events
        if case != "manifest_changed":
            assert events == []
