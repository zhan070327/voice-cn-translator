@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo Preparing Python environment and Windows download package...
call "%~dp0start_windows.bat" --prepare
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m scripts.verify_release --require-update-channel
if errorlevel 1 goto fail

".venv\Scripts\python.exe" -c "import torch,sys; sys.exit(0 if torch.__version__.startswith('2.8.0+cu128') else 1)"
if errorlevel 1 (
    echo Installing the CUDA 12.8 PyTorch package for the Windows build...
    ".venv\Scripts\python.exe" -m pip install --upgrade torch==2.8.0 --index-url https://download.pytorch.org/whl/cu128
    if errorlevel 1 goto fail
)

call "%~dp0build_windows.bat" --ci
if errorlevel 1 goto fail

set "ISCC_EXE="
call :find_iscc
if defined ISCC_EXE goto compile

where winget >nul 2>&1
if errorlevel 1 goto no_inno
echo Installing Inno Setup to create one installer EXE...
winget install --id JRSoftware.InnoSetup -e -s winget --scope user --silent --accept-source-agreements --accept-package-agreements
if errorlevel 1 goto no_inno
call :find_iscc
if not defined ISCC_EXE goto no_inno

:compile
"%ISCC_EXE%" "installer\ValorantTranslator.iss"
if errorlevel 1 goto fail
if not exist "dist\VoiceCNTranslator_Beta_Setup.exe" goto fail
echo.
echo READY: dist\VoiceCNTranslator_Beta_Setup.exe
echo Send THIS EXE to testers after checking it on another Windows PC.
echo Testers double-click it, install, then click Download Models in the app.
pause
exit /b 0

:find_iscc
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC_EXE=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC_EXE=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC_EXE=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
for /f "delims=" %%P in ('where ISCC.exe 2^>nul') do if not defined ISCC_EXE set "ISCC_EXE=%%P"
exit /b 0

:no_inno
echo.
echo Inno Setup was not found. Portable package is ready:
echo dist\ValorantTranslator_Portable.zip
echo To create a single installer EXE, install Inno Setup 6, then double-click this script again.
echo https://jrsoftware.org/inno-setup/downloads/
pause
exit /b 2

:fail
echo.
echo Build failed. Please send a photo of the last error lines.
pause
exit /b 1
