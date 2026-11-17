"""Create small source/update ZIPs without local models, venv, or user data."""
from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
FOLDERS = (".github", "assets", "config", "installer", "scripts", "test_audio",
           "tests", "valorant_translator")
FILES = ("main.py", "requirements.txt", "start_windows.bat", "build_windows.bat",
         "make_public_installer_windows.bat",
         "create_shortcut_windows.vbs", "repair_torch_windows.bat", "README_CN.md",
         "PUBLIC_BETA_GUIDE_CN.md", "BETA_NOTICE_CN.txt", "UPGRADE_CN.md",
         "DIAGNOSE_CN.md", "GAME_TERMS_REVIEW_CN.md", "GAME_TERMS_REVIEW.csv",
         "PERFORMANCE_REPORT_CN.md", "TEST_PLAN_CN.md", "THIRD_PARTY_NOTICES_CN.md",
         "UPDATE_RELEASE_GUIDE_CN.md",
         "safe_public_beta/RIOT_QUERY_DRAFT_CN.md")
EXCLUDE_PARTS = {"__pycache__", ".pytest_cache", ".venv", ".git", ".DS_Store"}


def package(path: Path) -> tuple[int, int]:
    included = [ROOT / name for name in FILES]
    for folder in FOLDERS:
        included.extend((ROOT / folder).rglob("*"))
    included = sorted(file for file in included if file.is_file()
                      and not any(part in EXCLUDE_PARTS for part in file.relative_to(ROOT).parts)
                      and file.suffix not in {".pyc", ".zip"})
    with ZipFile(path, "w", ZIP_DEFLATED, compresslevel=9) as archive:
        for file in included:
            archive.write(file, file.relative_to(ROOT).as_posix())
    with ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise RuntimeError(f"ZIP 校验失败：{path.name}")
        if any(name.endswith("translation/nllb.py") or name.endswith("test_nllb_version.py")
               for name in archive.namelist()):
            raise RuntimeError("公开源码包仍包含旧 NLLB 模块")
    return len(included), path.stat().st_size


if __name__ == "__main__":
    for name in ("ValorantTranslator_Public_Beta_Update.zip",
                 "ValorantTranslator_Public_Beta_Source.zip"):
        count, size = package(ROOT / name)
        print(f"{name}: {count} files, {size / 1024:.1f} KiB; ZIP verified")
