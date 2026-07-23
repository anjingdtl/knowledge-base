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
pause
exit /b 1

:found
echo [info] Using: %PYTHONW%
echo [info] Starting MCP Server...
start "" /b "%PYTHONW%" "%~dp0run_mcp.py" -t streamable-http --host 127.0.0.1 -p 9000

timeout /t 3 /nobreak >nul 2>nul

netstat -ano | findstr ":9000.*LISTENING" >nul 2>nul
if errorlevel 1 (
    echo [warn] MCP Server may still be starting, waiting...
    timeout /t 5 /nobreak >nul 2>nul
    netstat -ano | findstr ":9000.*LISTENING" >nul 2>nul
    if errorlevel 1 (
        echo [error] MCP Server failed to start on port 9000
        echo [info] Try running manually:
        if defined PYTHON (
            echo         "%PYTHON%" run_mcp.py -t streamable-http --host 127.0.0.1 -p 9000
        ) else (
            echo         python run_mcp.py -t streamable-http --host 127.0.0.1 -p 9000
        )
        pause
        exit /b 1
    )
)

echo [ok] MCP Server running at http://127.0.0.1:9000/mcp
