@echo off
REM Desktop Agent Setup Script for Windows
REM This script sets up the development environment and installs dependencies.

echo === Desktop Agent Setup ===
echo.

REM Check Python version
python --version >nul 2>&1 || python3 --version >nul 2>&1 || (
    echo Python is required but not installed.
    pause
    exit /b 1
)

REM Determine python command
where python >nul 2>&1 && set PYTHON=python || set PYTHON=python3

REM Create virtual environment
echo [1/5] Creating virtual environment...
if exist venv (
    echo Virtual environment already exists. Skipping...
) else (
    %PYTHON% -m venv venv
)

REM Activate virtual environment
echo [2/5] Activating virtual environment...
call venv\Scripts\activate.bat

REM Upgrade pip
echo [3/5] Upgrading pip...
python -m pip install --upgrade pip

REM Install dependencies
echo [4/5] Installing dependencies...
pip install -r requirements.txt

REM Install package in editable mode
echo [5/5] Installing desktop-agent package...
pip install -e .

echo.
echo === Setup Complete! ===
echo.
echo To activate the virtual environment, run:
echo   venv\Scripts\activate
echo.
echo To run the Phase 1 demo:
echo   python main.py
echo.
echo To run tests:
echo   pytest tests\ -v
echo.
echo To run with coverage:
echo   pytest tests\ -v --cov=desktop_agent
echo.
pause