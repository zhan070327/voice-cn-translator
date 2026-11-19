@echo off
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
if not exist "build_logs" mkdir "build_logs"
if errorlevel 1 (
    echo ERROR: Cannot create build_logs directory.
    exit /b 1
)
set "BUILD_LOG=%CD%\build_logs\domestic_build_%RANDOM%%RANDOM%.log"
echo Building domestic installer. Full log: "%BUILD_LOG%"
call :run_build > "%BUILD_LOG%" 2>&1
set "BUILD_RC=%errorlevel%"
powershell -NoProfile -Command "Get-Content -LiteralPath $env:BUILD_LOG -Tail 25 -Encoding UTF8"
if not "%BUILD_RC%"=="0" echo BUILD FAILED. Full log: "%BUILD_LOG%"
if "%BUILD_RC%"=="0" echo BUILD SUCCEEDED. Full log: "%BUILD_LOG%"
exit /b %BUILD_RC%

:run_build
set "CHANNEL_BACKUP=%TEMP%\voicecn_channel_%RANDOM%%RANDOM%.json"
copy /Y "config\update_channel.json" "%CHANNEL_BACKUP%" >nul
if errorlevel 1 (
    echo ERROR: Could not back up the current update channel.
    exit /b 1
)
copy /Y "config\update_channel_domestic.json" "config\update_channel.json" >nul
if errorlevel 1 goto restore_failed_build
call "%~dp0make_public_installer_windows.bat" --ci
if errorlevel 1 goto restore_failed_build
".venv\Scripts\python.exe" -m scripts.publish_cos --seal-build
if errorlevel 1 goto restore_failed_build
".venv\Scripts\python.exe" -c "from pathlib import Path; from valorant_translator.version import APP_VERSION; p=Path('dist/VoiceCNTranslator_Beta_Setup.exe').resolve(strict=True); print('VERSION:', APP_VERSION); print('INSTALLER:', p); print('SIZE_BYTES:', p.stat().st_size)"
if errorlevel 1 goto restore_failed_build
copy /Y "%CHANNEL_BACKUP%" "config\update_channel.json" >nul
if errorlevel 1 (
    echo ERROR: Could not restore update channel. Backup: "%CHANNEL_BACKUP%"
    exit /b 1
)
del "%CHANNEL_BACKUP%"
exit /b 0

:restore_failed_build
echo ERROR: Domestic build step failed. Restoring the previous update channel.
copy /Y "%CHANNEL_BACKUP%" "config\update_channel.json" >nul
if errorlevel 1 (
    echo ERROR: Could not restore update channel. Backup: "%CHANNEL_BACKUP%"
    exit /b 1
)
del "%CHANNEL_BACKUP%"
exit /b 1
