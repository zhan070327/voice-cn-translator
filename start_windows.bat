@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto validate
py -3.11 -m venv .venv >nul 2>&1
if exist ".venv\Scripts\python.exe" goto validate
py -3.12 -m venv .venv >nul 2>&1
if exist ".venv\Scripts\python.exe" goto validate
python -c "import sys; raise SystemExit(sys.version_info[:2] not in ((3,11),(3,12)) or sys.maxsize.bit_length()<63)" >nul 2>&1
if errorlevel 1 goto missing
python -m venv .venv
if not exist ".venv\Scripts\python.exe" goto missing
:validate
".venv\Scripts\python.exe" -c "import sys; raise SystemExit(sys.version_info[:2] not in ((3,11),(3,12)) or sys.maxsize.bit_length()<63)" >nul 2>&1
if errorlevel 1 goto badvenv
if exist ".venv\installed-v3.ok" goto run
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto pipfail
type nul > ".venv\installed-v3.ok"
:run
if /I "%~1"=="--prepare" exit /b 0
".venv\Scripts\python.exe" main.py
if errorlevel 1 pause
exit /b %errorlevel%
:missing
echo Python 3.11 or 3.12 64-bit was not found. Install Python first.
pause
exit /b 1
:badvenv
echo The .venv folder is incomplete or uses an unsupported Python. Rename it, then retry.
pause
exit /b 1
:pipfail
echo Dependency installation failed. Please capture the last error lines.
pause
exit /b 1
