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
start /b %PYTHONW% %~dp0main.py
