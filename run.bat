@echo off
setlocal
cd /d "%~dp0"
py -3.11 -c "import sys" >nul 2>nul
if errorlevel 1 (
    set "STACKLAB_PYTHON=python"
) else (
    set "STACKLAB_PYTHON=py -3.11"
)
if not exist ".venv\Scripts\python.exe" (
    %STACKLAB_PYTHON% -m venv .venv
    if errorlevel 1 goto fail
)
".venv\Scripts\python.exe" -c "import PySide6, scipy, reportlab, openpyxl" >nul 2>nul
if errorlevel 1 (
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto fail
)
rem Do not let an active CAD/Conda environment inject incompatible Qt DLLs.
set "QT_PLUGIN_PATH="
set "QT_QPA_PLATFORM_PLUGIN_PATH="
set "PYTHONHOME="
set "PYTHONPATH="
set "PATH=%~dp0.venv\Scripts;%SystemRoot%\System32;%SystemRoot%"
".venv\Scripts\python.exe" main.py %*
if errorlevel 1 goto fail
exit /b 0
:fail
echo.
echo Startup failed. Install Python 3.11 or newer and use this project's .venv.
echo Then run: .venv\Scripts\python.exe -m pip install -r requirements.txt
pause
exit /b 1
