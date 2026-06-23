@echo off
setlocal enabledelayedexpansion
title STOP - ShineHe KB Services

echo ============================================
echo   ShineHe KB - Stop All Services
echo ============================================
echo.

:: 1. Stop MCP Windows Service
echo [1/3] Stopping MCP service (ShineHeMCP)...
net stop ShineHeMCP >nul 2>&1
if !errorlevel! equ 0 (
    echo       [OK] MCP service stopped
) else (
    echo       [--] MCP service not running
)

:: 2. Stop FastAPI backend on port 8000
echo [2/3] Stopping FastAPI backend (port 8000)...
for /f "tokens=5" %%a in ('C:\Windows\System32\netstat.exe -aon 2^>nul ^| findstr ":8000 " ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
    if !errorlevel! equ 0 (
        echo       [OK] Killed backend PID %%a
    )
)

:: 3. Cleanup residual run_api.py processes
echo [3/3] Cleaning residual processes...
for /f "tokens=2 delims=," %%a in ('wmic process where "CommandLine like '%%run_api.py%%'" get ProcessId /format:csv 2^>nul ^| findstr /r "[0-9]"') do (
    taskkill /F /PID %%a >nul 2>&1
    if !errorlevel! equ 0 (
        echo       [OK] Cleaned residual PID %%a
    )
)

echo.
echo ============================================
echo   All services stopped
echo ============================================
echo.
pause
