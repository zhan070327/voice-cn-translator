@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "CI_MODE=0"
if /I "%~1"=="--ci" set "CI_MODE=1"
if not exist ".venv\Scripts\python.exe" (
    echo ERROR: Missing .venv Python. Run start_windows.bat first.
    if "%CI_MODE%"=="0" pause
    exit /b 1
)
".venv\Scripts\python.exe" -m pip install "pyinstaller>=6,<7"
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m scripts.create_icon
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m scripts.verify_release --write-exe-version
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onedir --windowed ^
  --name ValorantTranslator ^
  --version-file ".venv\windows-version.txt" ^
  --icon "assets\app_icon.ico" ^
  --add-data "config\glossary.json;config" ^
  --add-data "config\languages.json;config" ^
  --add-data "config\glossary_*.json;config" ^
  --add-data "config\update_channel.json;config" ^
  --add-data "scripts\windows_updater.ps1;scripts" ^
  --add-data "assets\app_icon.ico;assets" ^
  --add-data "assets\fonts;assets\fonts" ^
  --add-data "assets\i18n\zh_CN.json;assets\i18n" ^
  --add-data "THIRD_PARTY_NOTICES_CN.md;." ^
  --collect-all soundcard --collect-all faster_whisper --collect-all ctranslate2 ^
  --collect-all transformers --collect-all sentencepiece ^
  --collect-all huggingface_hub --collect-all tokenizers --collect-all opencc ^
  --exclude-module valorant_translator.translation.nllb ^
  main.py
if errorlevel 1 goto fail
if "%VALORANT_CPU_PACKAGE%"=="1" (
  ".venv\Scripts\python.exe" -m scripts.audit_cuda_bundle "dist\ValorantTranslator" --prune-cpu-only
  if errorlevel 1 goto fail
)
"dist\ValorantTranslator\ValorantTranslator.exe" --self-test
if errorlevel 1 (
    echo ERROR: Packaged EXE self-test failed. See build log.
    goto fail
)
".venv\Scripts\python.exe" -m scripts.audit_cuda_bundle "dist\ValorantTranslator"
if errorlevel 1 goto fail
if "%VALORANT_SKIP_PORTABLE%"=="1" exit /b 0
powershell -NoProfile -Command "Compress-Archive -Path 'dist\ValorantTranslator' -DestinationPath 'dist\ValorantTranslator_Portable.zip' -Force"
if errorlevel 1 goto fail
echo Portable package ready: dist\ValorantTranslator_Portable.zip
if "%CI_MODE%"=="0" pause
exit /b 0
:fail
echo ERROR: Portable package build failed. See build log.
if "%CI_MODE%"=="0" pause
exit /b 1
