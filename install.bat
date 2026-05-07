@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

echo.
echo ========================================
echo   STAB AutoAdd System - Install
echo   Made by SGI
echo ========================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found.
    echo Please install Python 3.10+ from:
    echo   https://www.python.org/downloads/
    echo.
    echo IMPORTANT: Check "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo Python %PYVER% found.

if exist .venv\Scripts\python.exe (
    echo Virtual environment already exists.
) else (
    echo Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

echo.
echo Installing packages... (may take 5-15 min on first run)
echo.

.venv\Scripts\python.exe -m pip install --upgrade pip --quiet
if errorlevel 1 (
    echo [ERROR] pip upgrade failed.
    pause
    exit /b 1
)

.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Package installation failed.
    echo Check your internet connection and try again.
    pause
    exit /b 1
)

echo.
echo ----------------------------------------
echo Installation complete!
echo Run "run.bat" to start the program.
echo ----------------------------------------
echo.
pause
