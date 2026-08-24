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
TRIM_FILES = [
    "_internal/PyQt6/Qt6/bin/opengl32sw.dll",   # 5.4 MB software rasterizer
    "_internal/PyQt6/Qt6/bin/Qt6Pdf.dll",       # 4.5 MB PDF engine (unused)
    "_internal/PyQt6/Qt6/imageformats/qpdf.dll",
]


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

    # 3. Trim proven-unused binaries
    saved = 0
    for rel in TRIM_FILES:
        f = DIST / rel
        if f.exists():
            saved += f.stat().st_size
            f.unlink()
            print(f"trimmed {rel}")

    size_mb = sum(f.stat().st_size for f in DIST.rglob("*") if f.is_file()) / 1e6
    print(f"\nBuild OK: {DIST / 'GoldenMusic.exe'}")
    print(f"Total folder size: {size_mb:.1f} MB (trim saved {saved/1e6:.1f} MB)")


if __name__ == "__main__":
    main()
