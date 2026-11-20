"""Static checks for CMD batch files; the real build still needs Windows."""
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MAIN_BUILD_BATCHES = (
    "make_domestic_installer_windows.bat",
    "make_public_installer_windows.bat",
    "build_windows.bat",
    "start_windows.bat",
)


def test_build_batch_encoding_and_cmd_line_continuations():
    for path in sorted(set(ROOT.glob("*.bat")) | set(ROOT.glob("*.cmd"))):
        name = path.name
        data = path.read_bytes()
        assert data.startswith(b"@echo off\r\n"), name
        assert data.endswith(b"\r\n"), name
        assert not re.search(rb"\r(?!\n)|(?<!\r)\n", data), name
        assert data.isascii(), name
        lines = data.split(b"\r\n")
        for index, line in enumerate(lines[:-1]):
            assert line.count(b'"') % 2 == 0, (name, index + 1)
            if line.rstrip().endswith(b"^"):
                assert line.endswith(b"^"), (name, index + 1)
                assert lines[index + 1].strip(), (name, index + 1)


def test_build_batch_labels_and_called_files_exist():
    for name in MAIN_BUILD_BATCHES:
        source = (ROOT / name).read_text(encoding="ascii")
        labels = set(re.findall(r"(?m)^:([a-z_]+)\s*$", source, re.I))
        targets = re.findall(r"\bgoto\s+([a-z_]+)", source, re.I)
        targets += re.findall(r"\bcall\s+:([a-z_]+)", source, re.I)
        assert set(targets) <= labels, (name, set(targets) - labels)
    for name in ("make_public_installer_windows.bat", "build_windows.bat"):
        assert (ROOT / name).is_file()
    for name in ("installer/ValorantTranslator.iss", "assets/app_icon.svg",
                 "config/glossary.json", "config/update_channel.json",
                 "config/update_channel_domestic.json", "scripts/windows_updater.ps1",
                 "THIRD_PARTY_NOTICES_CN.md", "main.py"):
        assert (ROOT / name).is_file(), name


def test_batch_retains_required_build_checks():
    domestic = (ROOT / MAIN_BUILD_BATCHES[0]).read_text(encoding="ascii")
    public = (ROOT / MAIN_BUILD_BATCHES[1]).read_text(encoding="ascii")
    portable = (ROOT / MAIN_BUILD_BATCHES[2]).read_text(encoding="ascii")
    assert "make_public_installer_windows.bat" in domestic
    assert "scripts.publish_cos --seal-build" in domestic
    assert "scripts.verify_release --require-update-channel" in public
    assert "scripts.create_icon" in portable
    assert "scripts.verify_release" in portable
    assert "ValorantTranslator.exe\" --self-test" in portable
    assert "scripts.audit_cuda_bundle" in portable
    assert "--add-data \"config\\update_channel.json;config\"" in portable


def test_pyinstaller_options_stay_in_one_cmd_command():
    lines = (ROOT / "build_windows.bat").read_text(encoding="ascii").splitlines()
    start = next(i for i, line in enumerate(lines) if ' -m PyInstaller ' in line)
    entry = next(i for i, line in enumerate(lines) if line.strip() == "main.py")
    assert start < entry
    assert all(line.endswith("^") for line in lines[start:entry])
    assert all(line.lstrip().startswith("--") for line in lines[start + 1:entry])


def test_installer_version_matches_application():
    from scripts.verify_release import main

    main()
