@echo off
setlocal
cd /d "%~dp0"

where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw app.py
    exit /b
)

where python >nul 2>nul
if %errorlevel%==0 (
    start "" python app.py
    exit /b
)

echo Python is not installed or not on PATH.
echo Install Python 3.10+ from https://www.python.org/downloads/ and try again.
pause
