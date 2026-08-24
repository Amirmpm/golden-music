@echo off
REM ============================================================
REM  Golden Music Windows Build Script
REM  Produces:
REM    dist\GoldenMusic\                        <- PyInstaller app folder
REM    dist\installer\GoldenMusicSetup-1.1.0.exe <- Inno Setup installer
REM
REM  REQUIREMENTS:
REM    - Python 3.11+ (3.14 recommended) with requirements.txt installed
REM    - Inno Setup 6 (ISCC.exe)
REM ============================================================

setlocal
cd /d %~dp0

set "ISCC_EXE=D:\Apps\Inno Setup 6\iscc.exe"
if not exist "%ISCC_EXE%" set "ISCC_EXE=C:\Program Files (x86)\Inno Setup 6\iscc.exe"

echo.
echo === [1/4] Installing Python dependencies ===
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pywin32
if errorlevel 1 (
    echo ERROR: pip install failed.
    pause
    exit /b 1
)

echo.
echo === [2/4] Running test suites ===
for %%T in (test_functional test_deep test_stability test_stability_v2 test_history_nav test_features test_crash) do (
    echo   --- %%T ---
    python scripts\%%T.py
    if errorlevel 1 (
        echo ERROR: tests failed in %%T — aborting build.
        pause
        exit /b 1
    )
)

echo.
echo === [3/4] Building optimized exe (PyInstaller + trim) ===
python build_release.py
if errorlevel 1 (
    echo ERROR: build_release.py failed.
    pause
    exit /b 1
)

echo.
echo === [4/4] Building installer with Inno Setup ===
if not exist "%ISCC_EXE%" (
    echo ERROR: Inno Setup 6 not found. Install it or set ISCC_EXE in this script.
    echo Download: https://jrsoftware.org/isdl.php
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
echo Installer:     dist\installer\GoldenMusicSetup-1.1.0.exe
echo.
pause
