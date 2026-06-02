@echo off
chcp 65001 >nul 2>nul

set PYTHONW=

if exist "%~dp0venv\Scripts\pythonw.exe" (
    set "PYTHONW=%~dp0venv\Scripts\pythonw.exe"
    goto :found
)

for /f "delims=" %%i in ('where python 2^>nul') do set PYTHON_DIR=%%~dpi
if exist "%PYTHON_DIR%pythonw.exe" (
    set "PYTHONW=%PYTHON_DIR%pythonw.exe"
    goto :found
)

if exist "C:\Users\Administrator\AppData\Local\Programs\Python\Python314\pythonw.exe" (
    set "PYTHONW=C:\Users\Administrator\AppData\Local\Programs\Python\Python314\pythonw.exe"
    goto :found
)

echo [error] pythonw.exe not found
pause
exit /b 1

:found
echo [info] Starting ShineHeKnowledge...
start "" /b "%PYTHONW%" "%~dp0main.py"

:: Give GUI process 2 seconds to start; if it exits immediately, something is wrong
timeout /t 2 /nobreak >nul 2>nul
