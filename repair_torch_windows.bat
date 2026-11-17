@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto missing
".venv\Scripts\python.exe" -m pip install --upgrade "torch>=2.6,<3"
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -c "import torch; print('PyTorch:', torch.__version__); print('CUDA available:', torch.cuda.is_available())"
echo Restart Valorant Translator after closing this window.
pause
exit /b 0
:missing
echo No local Python environment found. Put this file beside main.py and start_windows.bat.
pause
exit /b 1
:failed
echo PyTorch update failed. Please capture the last error lines.
pause
exit /b 1
