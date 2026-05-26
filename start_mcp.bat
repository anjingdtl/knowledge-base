@echo off
chcp 65001 >/dev/null 2>/dev/null

set PYTHONW=

if exist %~dp0venv\Scripts\pythonw.exe (
    set PYTHONW=%~dp0venv\Scripts\pythonw.exe
    goto :found
)

for /f "delims=" %%i in ('where python 2^>nul') do set PYTHON_DIR=%%~dpi
if exist %PYTHON_DIR%pythonw.exe (
    set PYTHONW=%PYTHON_DIR%pythonw.exe
    goto :found
)

if exist C:\Users\Administrator\AppData\Local\Programs\Python\Python314\pythonw.exe (
    set PYTHONW=C:\Users\Administrator\AppData\Local\Programs\Python\Python314\pythonw.exe
    goto :found
)

echo [error] pythonw.exe not found
pause
exit /b 1

:found
start /b %PYTHONW% %~dp0run_mcp.py -t streamable-http --host 127.0.0.1 -p 9000

timeout /t 2 /nobreak >/dev/null 2>/dev/null

netstat -ano 2>/dev/null | findstr ":9000.*LISTENING" >/dev/null 2>/dev/null
if errorlevel 1 (
    echo MCP Server starting...
) else (
    echo MCP Server running at http://127.0.0.1:9000/mcp
)
