@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "CI_MODE=0"
if /I "%~1"=="--ci" set "CI_MODE=1"
if not exist ".venv\Scripts\python.exe" (
    echo 请先运行 start_windows.bat 安装依赖，再运行本脚本。
    if "%CI_MODE%"=="0" pause
    exit /b 1
)
".venv\Scripts\python.exe" -m pip install "pyinstaller>=6,<7"
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m scripts.create_icon
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m scripts.verify_release
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onedir --windowed ^
  --name ValorantTranslator ^
  --icon "assets\app_icon.ico" ^
  --add-data "config\glossary.json;config" ^
  --add-data "config\update_channel.json;config" ^
  --add-data "assets\app_icon.ico;assets" ^
  --add-data "THIRD_PARTY_NOTICES_CN.md;." ^
  --collect-all soundcard --collect-all faster_whisper --collect-all ctranslate2 ^
  --collect-all transformers --collect-all torch --collect-all sentencepiece ^
  --collect-all huggingface_hub --collect-all tokenizers --collect-all opencc ^
  --exclude-module valorant_translator.translation.nllb ^
  main.py
if errorlevel 1 goto fail
"dist\ValorantTranslator\ValorantTranslator.exe" --self-test
if errorlevel 1 (
    echo 打包后的启动检查失败，请查看运行日志。
    goto fail
)
powershell -NoProfile -Command "Compress-Archive -Path 'dist\ValorantTranslator' -DestinationPath 'dist\ValorantTranslator_Portable.zip' -Force"
if errorlevel 1 goto fail
echo 打包完成：dist\ValorantTranslator_Portable.zip
if "%CI_MODE%"=="0" pause
exit /b 0
:fail
echo 打包失败，请保留上面的错误信息。
if "%CI_MODE%"=="0" pause
exit /b 1
