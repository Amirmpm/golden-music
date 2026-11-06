"""
Golden Music — release build script.

Runs PyInstaller with the optimized spec, then trims dead weight that
PyInstaller can't know is unused (software-OpenGL rasterizer, QtPdf, etc.),
verifies the result launches, and reports the final size.

Usage:  python build_release.py
"""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist" / "GoldenMusic"

# Files PyInstaller bundles but the app provably never loads (verified by
# launching the trimmed bundle — see scripts/test_build_smoke.py).
# Aggressive-safe: every entry was checked against the import graph of
# main.pyw + late imports (hiddenimports in goldenmusic.spec) before adding.
TRIM_FILES = [
    # --- Qt binaries never loaded ---
    "_internal/PyQt6/Qt6/bin/opengl32sw.dll",   # ~5 MB software rasterizer
    "_internal/PyQt6/Qt6/bin/Qt6Pdf.dll",       # ~4.5 MB PDF engine (unused)
    "_internal/PyQt6/Qt6/bin/Qt6Qml.dll",       # QML stack (excluded in spec)
    "_internal/PyQt6/Qt6/bin/Qt6QmlModels.dll",
    "_internal/PyQt6/Qt6/bin/Qt6Quick.dll",
    "_internal/PyQt6/Qt6/bin/Qt6VirtualKeyboard.dll",
    "_internal/PyQt6/Qt6/bin/Qt6Charts.dll",
    "_internal/PyQt6/Qt6/bin/Qt6DataVisualization.dll",
    "_internal/PyQt6/Qt6/bin/Qt6Graphs.dll",
    "_internal/PyQt6/Qt6/bin/Qt6Location.dll",
    "_internal/PyQt6/Qt6/bin/Qt6Positioning.dll",
    "_internal/PyQt6/Qt6/bin/Qt6Sensors.dll",
    "_internal/PyQt6/Qt6/bin/Qt6SerialPort.dll",
    "_internal/PyQt6/Qt6/bin/Qt6Bluetooth.dll",
    "_internal/PyQt6/Qt6/bin/Qt6Nfc.dll",
    "_internal/PyQt6/Qt6/bin/Qt6TextToSpeech.dll",
    "_internal/PyQt6/Qt6/bin/Qt6RemoteObjects.dll",
    "_internal/PyQt6/Qt6/qml",                  # whole QML dir if present
    "_internal/PyQt6/Qt6/translations/qtwebengine_locales",  # webengine locales
    # --- Qt plugins never exercised ---
    "_internal/PyQt6/Qt6/plugins/imageformats/qpdf.dll",
    "_internal/PyQt6/Qt6/plugins/imageformats/qtiff.dll",    # TIFF covers: never
    "_internal/PyQt6/Qt6/plugins/imageformats/qwebp.dll",    # WebP covers: never
    "_internal/PyQt6/Qt6/plugins/generic",      # e.g. qtuiotouchplugin
    "_internal/PyQt6/Qt6/plugins/multimedia/ffmpegmediaplugin.dll",  # WMF used
    "_internal/PyQt6/Qt6/plugins/tls",          # only if unused (kept if QTLS needed)
    # --- Python stdlib dead weight ---
    "_internal/tcl",                             # tkinter excluded in spec
    "_internal/tk",
    "_internal/lib2to3",
]

# Trim a whole directory tree if present (best-effort, never fatal).
TRIM_DIRS = [
    "_internal/PyQt6/Qt6/qml",
]

# Size cap (MB) for the portable folder — the build FAILS loudly above this
# instead of silently shipping a bloated bundle.
SIZE_BUDGET_MB = 110


def run(cmd, **kw):
    print("+", " ".join(str(c) for c in cmd))
    return subprocess.run(cmd, cwd=ROOT, check=True, **kw)


def main():
    # 1. Clean previous output
    for d in ("build", "dist"):
        p = ROOT / d
        if p.exists():
            shutil.rmtree(p)

    # 2. PyInstaller
    run([sys.executable, "-m", "PyInstaller", "goldenmusic.spec",
         "--noconfirm"])

    # 3. Trim proven-unused binaries (best-effort: never fail the build)
    saved = 0
    for rel in TRIM_FILES:
        f = DIST / rel
        try:
            if f.is_file() or f.is_symlink():
                saved += f.stat().st_size
                f.unlink()
                print(f"trimmed {rel}")
        except OSError as e:
            print(f"trim skip {rel}: {e}")
    for rel in TRIM_DIRS:
        d = DIST / rel
        try:
            if d.is_dir():
                saved += sum(f.stat().st_size for f in d.rglob("*")
                             if f.is_file())
                shutil.rmtree(d, ignore_errors=True)
                print(f"trimmed dir {rel}")
        except OSError as e:
            print(f"trim skip dir {rel}: {e}")

    # 4. Smoke test — boot the built exe, fail loudly if it dies early.
    #    QT_QPA_PLATFORM=offscreen is NOT used here: we want the real
    #    window path (platforms/qwindows.dll) to load.
    exe = DIST / "GoldenMusic.exe"
    print(f"+ smoke: {exe} --smoke")
    t0 = time.time()
    proc = subprocess.run(
        [str(exe), "--smoke"], cwd=ROOT,
        capture_output=True, text=True, timeout=120)
    dt = time.time() - t0
    tail = (proc.stdout + proc.stderr)[-1500:]
    print(f"smoke exit={proc.returncode} in {dt:.1f}s\n{tail}")
    if proc.returncode != 0:
        raise SystemExit(f"SMOKE FAILED (exit {proc.returncode}) — see above")

    size_mb = sum(f.stat().st_size for f in DIST.rglob("*") if f.is_file()) / 1e6
    print(f"\nBuild OK: {exe}")
    print(f"Total folder size: {size_mb:.1f} MB (trim saved {saved/1e6:.1f} MB)")
    if size_mb > SIZE_BUDGET_MB:
        raise SystemExit(
            f"SIZE BUDGET EXCEEDED: {size_mb:.1f} MB > {SIZE_BUDGET_MB} MB")


if __name__ == "__main__":
    main()
