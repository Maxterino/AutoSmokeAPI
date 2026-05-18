@echo off
REM Build AutoSmokeAPI.exe via PyInstaller.
REM Output: dist\AutoSmokeAPI\AutoSmokeAPI.exe (plus _internal\ folder of dependencies)

setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python is not installed or not on PATH.
    echo Install Python 3.10+ from https://www.python.org/downloads/
    pause
    exit /b 1
)

python -m pip install --upgrade --quiet customtkinter tkinterdnd2 pillow pyinstaller
if errorlevel 1 (
    echo Failed to install build dependencies.
    pause
    exit /b 1
)

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

python -m PyInstaller AutoSmokeAPI.spec --clean --noconfirm
if errorlevel 1 (
    echo Build failed.
    pause
    exit /b 1
)

echo.
echo ===================================================================
echo Build complete.
echo Output: dist\AutoSmokeAPI\AutoSmokeAPI.exe
echo Distribute the entire dist\AutoSmokeAPI folder (zip it, then share).
echo ===================================================================
pause
