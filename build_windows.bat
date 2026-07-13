@echo off
REM ============================================================
REM  Golden Music Windows Build Script (v2.0)
REM  Produces:
REM    dist\GoldenMusic\                              <- PyInstaller app folder
REM    dist\installer\GoldenMusicSetup-2.0.0.exe      <- Installer
REM
REM  REQUIREMENTS:
REM    - Python 3.14 (or 3.11-3.14)
REM    - Inno Setup 6 at D:\Apps\Inno Setup 6
REM ============================================================

setlocal
cd /d %~dp0

set "ISCC_EXE=D:\Apps\Inno Setup 6\iscc.exe"

echo.
echo === [1/4] Installing Python dependencies ===
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: pip install failed.
    pause
    exit /b 1
)

echo.
echo === [2/4] Cleaning previous build ===
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo.
echo === [3/4] Building with PyInstaller ===
python -m PyInstaller goldenmusic.spec --noconfirm --log-level WARN
if errorlevel 1 (
    echo ERROR: PyInstaller build failed.
    pause
    exit /b 1
)

echo.
echo === [4/4] Building installer with Inno Setup ===
if not exist "%ISCC_EXE%" (
    echo ERROR: Inno Setup 6 not found at: %ISCC_EXE%
    echo Please install Inno Setup 6 from https://jrsoftware.org/isdl.php
    echo Or update the ISCC_EXE variable in this script.
    pause
    exit /b 1
)
"%ISCC_EXE%" goldenmusic.iss
if errorlevel 1 (
    echo ERROR: Inno Setup compilation failed.
    pause
    exit /b 1
)

echo.
echo === Build complete! ===
echo.
echo App folder:    dist\GoldenMusic\GoldenMusic.exe
echo Installer:     dist\installer\GoldenMusicSetup-2.0.0.exe
echo.
echo Use the installer for deployment.
echo.
pause
