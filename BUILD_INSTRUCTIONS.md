# Build Instructions — Golden Music v3.0.0 (Windows)

## Prerequisites
- Windows 10/11 x64, Python 3.11-3.14 (x64) on PATH
- Inno Setup 6 (default per-user path is auto-detected; override with `ISCC=`)

## One-command local build
```bat
build_windows.bat
```
Steps it performs: dependency install → 7 core test suites → PyInstaller (`build_release.py`) → Inno Setup → `dist\installer\GoldenMusicSetup-3.0.0.exe` + portable `dist\GoldenMusic` folder.

## Manual build
```bat
pip install -r requirements.txt
python scripts\test_functional.py        (plus any suites you want)
python build_release.py                  (PyInstaller + trimming + --smoke check)
"%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" goldenmusic.iss
```
`build_release.py` fails loudly if the smoke boot (`--smoke`) fails or the 115 MB size budget is exceeded.

## CI build (recommended)
`.github/workflows/ci.yml` (push to main) builds the portable folder + installer and attaches both as artifacts. `.github/workflows/release.yml` (tag `v3.0.0`) runs the full 13-suite test battery + Bandit + pip-audit + SBOM, builds installer + `.sha256` + portable zip, runs the installer smoke (install → upgrade → uninstall with registry assertions) and publishes the GitHub Release.

## Notes
- The FFmpeg media plugin is intentionally KEPT (all 9 formats decode; see AUDIT GM-03).
- Code signing: no certificate is available to this project — releases are published UNSIGNED with the limitation disclosed (RELEASE_NOTES.md). Never commit signing secrets.
