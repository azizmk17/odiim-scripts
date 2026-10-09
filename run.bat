@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
    set "ODIIM_PYTHON=python"
) else (
    set "ODIIM_PYTHON=py -3"
)
if not exist ".venv\Scripts\python.exe" (
    %ODIIM_PYTHON% -m venv .venv
    if errorlevel 1 goto fail
)
".venv\Scripts\python.exe" -c "import scipy" >nul 2>nul
if errorlevel 1 (
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto fail
)
".venv\Scripts\python.exe" main.py %*
if errorlevel 1 goto fail
exit /b 0
:fail
echo.
echo Startup failed. Install Python 3.10 or newer with Tcl/Tk support.
echo Then run: python -m pip install -r requirements.txt
pause
exit /b 1
