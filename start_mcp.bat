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
        echo [info] Try running manually: python run_mcp.py -t streamable-http --host 127.0.0.1 -p 9000
        pause
        exit /b 1
    )
)

echo [ok] MCP Server running at http://127.0.0.1:9000/mcp
