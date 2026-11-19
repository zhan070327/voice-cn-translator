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
if /I "%~1"=="--prepare-gpu" call :prepare_gpu
if errorlevel 1 exit /b %errorlevel%
if exist ".venv\installed-v3.ok" goto run
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto pipfail
type nul > ".venv\installed-v3.ok"
:run
if /I "%~1"=="--prepare" exit /b 0
if /I "%~1"=="--prepare-gpu" exit /b 0
".venv\Scripts\python.exe" main.py
if errorlevel 1 pause
exit /b %errorlevel%
:prepare_gpu
if "%VALORANT_PACKAGE_WITHOUT_GPU%"=="1" (
    ".venv\Scripts\python.exe" -m scripts.check_torch_cuda --package-only
    if errorlevel 1 exit /b 1
    exit /b 0
)
".venv\Scripts\python.exe" -m scripts.check_torch_cuda
set "TORCH_CHECK=%errorlevel%"
if "%TORCH_CHECK%"=="0" exit /b 0
if "%TORCH_CHECK%"=="4" (
    echo Existing CUDA PyTorch cannot run GPU kernels on this card. Build stopped without downloading another wheel.
    echo If needed, manually install a compatible NVIDIA CUDA wheel from https://pytorch.org/get-started/previous-versions/
    exit /b 1
)
if "%TORCH_CHECK%"=="3" (
    echo GPU or CUDA driver is unavailable. Fix the driver/runtime before packaging.
    exit /b 1
)
if not "%TORCH_CHECK%"=="2" exit /b 1
echo Installing official CUDA PyTorch once. Pip will use its local cache when available.
".venv\Scripts\python.exe" -m pip cache dir
".venv\Scripts\python.exe" -m pip install --upgrade torch==2.8.0 --index-url https://download.pytorch.org/whl/cu128
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m scripts.check_torch_cuda
if errorlevel 1 exit /b 1
exit /b 0
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
