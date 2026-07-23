@echo off
chcp 65001 >nul 2>nul
cd /d "%~dp0"

set "PYTHONW="
set "PYTHON="

:: Prefer project virtualenvs (.venv first, then venv)
if exist "%~dp0.venv\Scripts\pythonw.exe" (
    set "PYTHONW=%~dp0.venv\Scripts\pythonw.exe"
    set "PYTHON=%~dp0.venv\Scripts\python.exe"
    goto :found
)
if exist "%~dp0venv\Scripts\pythonw.exe" (
    set "PYTHONW=%~dp0venv\Scripts\pythonw.exe"
    set "PYTHON=%~dp0venv\Scripts\python.exe"
    goto :found
)

:: Fallback: system pythonw from PATH
for /f "delims=" %%i in ('where python 2^>nul') do set "PYTHON_DIR=%%~dpi"
if defined PYTHON_DIR if exist "%PYTHON_DIR%pythonw.exe" (
    set "PYTHONW=%PYTHON_DIR%pythonw.exe"
    if exist "%PYTHON_DIR%python.exe" set "PYTHON=%PYTHON_DIR%python.exe"
    goto :found
)

echo [error] pythonw.exe not found
echo [info] Create a venv first: python -m venv .venv
echo [info] Then install GUI deps: .\.venv\Scripts\python.exe -m pip install -e ".[gui]"
pause
exit /b 1

:found
echo [info] Using: %PYTHONW%

:: Preflight: ensure GUI deps exist (pythonw hides ImportError)
if defined PYTHON (
    "%PYTHON%" -c "import PySide6" >nul 2>nul
    if errorlevel 1 (
        echo [error] PySide6 not installed in this Python environment.
        echo [info] Install GUI deps with:
        echo         "%PYTHON%" -m pip install -e ".[gui]"
        pause
        exit /b 1
    )
)

echo [info] Starting ShineHeKnowledge...
start "" /b "%PYTHONW%" "%~dp0main.py"

:: Give GUI process 2 seconds to start; if it exits immediately, something is wrong
timeout /t 2 /nobreak >nul 2>nul
