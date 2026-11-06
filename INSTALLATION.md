# 📦 Golden Music — Installation Guide

This guide covers all the ways to install and run Golden Music on **Windows
and Linux** (plus the honest status on Android).

---

## 🚀 Quick Install (Windows, Recommended)

### Download the Installer

1. Go to [Releases](../../releases)
2. Download `GoldenMusicSetup-2.3.0.exe`
3. Run the installer — it detects an existing installation and updates in place; your library, favorites, ratings and settings are preserved (the first 3.0 start migrates your data to the new SQLite library — backed up and verified)
4. Follow the setup wizard
5. Launch Golden Music from the Start Menu or Desktop shortcut

**That's it!** No Python or dependencies needed — everything is bundled.

---

## 🛠️ Build from Source

### Prerequisites

#### 1. Python 3.11 or later

Golden Music requires **Python 3.11+** (tested with 3.11–3.14).

- Download: [python.org/downloads](https://www.python.org/downloads/)
- During installation, **check "Add Python to PATH"**

Verify installation:
```bash
python --version
# Should print: Python 3.11.x or later
```

#### 2. Inno Setup 6 (for building the installer)

- Download: [jrsoftware.org/isdl.php](https://jrsoftware.org/isdl.php)
- Install to default location or `D:\Apps\Inno Setup 6\`
- If installed elsewhere, edit `ISCC_EXE` in `build_windows.bat`

---

### Build Steps

1. **Download or clone the source code:**
   ```bash
   git clone https://github.com/Amirmpm/golden-music.git
   cd golden-music
   ```

2. **Open Command Prompt** in the project folder
   (Shift + Right-click → "Open command window here")

3. **Run the build script:**
   ```bash
   build_windows.bat
   ```

4. **Wait for the build to complete.** You'll see:
   - Dependencies installed
   - PyInstaller building the EXE
   - Inno Setup creating the installer

5. **Find the output:**
   ```
   dist\GoldenMusic\GoldenMusic.exe         # The app
   dist\installer\GoldenMusicSetup-2.3.0.exe # The installer
   ```

6. **Run the installer** (`GoldenMusicSetup-2.3.0.exe`) to install properly.

---

## 👨‍💻 Run from Source (Development, Windows)

If you want to run Golden Music without building an installer:

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   pip install pywin32   # media keys + single-instance on Windows
   ```

2. **Run:**
   ```bash
   python main.pyw
   ```

---

## 🐧 Linux

Golden Music 2.3 runs on Linux from source, and the full test matrix runs
on Ubuntu in CI. There is no Linux installer yet (see *Linux packaging*
below).

### 1. System packages (Debian/Ubuntu)

Qt needs a few system libraries, plus a running audio server:

```bash
sudo apt install libegl1 libgl1 libxkbcommon0 libdbus-1-3 libfontconfig1
# audio — most modern desktops already have one of these:
sudo apt install pipewire pipewire-pulse   # or: pulseaudio
```

(On Fedora/Arch the same libraries exist under similar names —
`libglvnd`/`mesa-libGL`, `libxkbcommon`, `dbus-libs`, `fontconfig`.)

### 2. Install Python dependencies

```bash
git clone https://github.com/Amirmpm/golden-music.git
cd golden-music
pip install -r requirements.txt
```

`pywin32` is Windows-only and is skipped automatically on Linux.

### 3. Optional extras

| Extra | Install | Unlocks |
|---|---|---|
| `miniaudio` | `pip install miniaudio` | Waveform seekbar + spectrum analyzer |
| libmpv engine | `pip install python-mpv` **and** the system `libmpv` (e.g. `apt install libmpv2` — package name varies by distro/release) | 10-band equalizer, silence skipping, pitch-preserving speed — the mpv engine is auto-detected at startup and the Qt engine is used otherwise |
| `qrcode` | `pip install qrcode` | QR pairing page for the Web Remote (falls back to a plain text token) |
| `dbus-python` | `pip install dbus-python` | MPRIS media controls (desktop media keys / lock screen controls — optional, auto-detected) |

All extras degrade gracefully — the app never *requires* any of them.

### 4. Run

```bash
python main.pyw
```

### Linux packaging

PyInstaller one-dir builds work on Linux (`pyinstaller goldenmusic.spec` —
the same spec used on Windows). Official distro packages (deb/rpm), an
AppImage or a Flatpak are **not shipped yet** — they are a great
community-contribution opportunity; please open an issue if you'd like to
work on one.

---

## 📱 Android (status: not shipped)

Golden Music does not run on Android today — and **PyQt6 has no Android
support**, so there is no shortcut. The realistic route, documented for
contributors who want to attempt it:

- **PySide6** (Qt for Python) ships official Android support and works with
  **python-for-android** or Qt Creator's *Deploy to Android* device kits
- Porting would mean swapping the PyQt6 import surface for PySide6 (small
  but wide), plus touch-first variants of the dense desktop controls
- The codebase is already prepared in two ways: every OS touchpoint lives
  behind the `gm/platforms.py` abstraction layer, and there are no hard
  `win32` dependencies outside guarded, Windows-only modules

Recommendation: track upstream Qt for Python Android support and open a
discussion issue before starting — it is a large but well-scoped project.

---

## ✍️ Code Signing (optional, Windows)

Windows builds can be Authenticode-signed. Signing is **fully optional** —
unsigned builds remain valid — and this project never fakes or bypasses
signing.

- **CI (GitHub secrets):** `WINDOWS_CERTIFICATE` — the `.pfx` certificate,
  base64-encoded — and `WINDOWS_CERTIFICATE_PASSWORD`
- **Local builds:** set `GOLDENMUSIC_CERT_FILE` (path to the `.pfx`) and
  `GOLDENMUSIC_CERT_PASSWORD`
- `signtool` must be on `PATH` (it ships with the Windows SDK)
- Signatures are timestamped via DigiCert's public RFC 3161 server, so they
  remain valid after the certificate itself expires

`build_release.py` signs `GoldenMusic.exe` automatically whenever a
certificate is configured; the installer then bundles the signed exe.

**Honest note:** unsigned builds trigger Windows SmartScreen's "unknown
publisher" warning until enough installs build reputation over time — that
is expected behavior, not a bug, and installing anyway is safe when the
download comes from this project's Releases.

---

## 🧪 Verify the Build

After a successful build, check that these directories exist:

```
golden-music\
├── build\                          # PyInstaller intermediate files (can delete)
├── dist\
│   ├── GoldenMusic\                # The app folder
│   │   ├── GoldenMusic.exe
│   │   ├── assets\
│   │   │   ├── icon.ico
│   │   │   └── logo.png
│   │   ├── PyQt6\
│   │   │   └── Qt6\
│   │   │       └── bin\
│   │   └── _internal\
│   │       └── PyQt6\
│   │           └── Qt6\
│   │               └── plugins\
│   │                   ├── mediaservice\    # Audio backend (FFmpeg stack)
│   │                   ├── iconengines\     # SVG rendering
│   │                   ├── imageformats\    # Cover art
│   │                   ├── platforms\
│   │                   └── styles\
│   └── installer\
│       └── GoldenMusicSetup-2.3.0.exe      # The installer
└── ...
```

**If `dist\GoldenMusic\_internal\PyQt6\Qt6\plugins\mediaservice\` is missing or empty, audio playback won't work.** Re-run the build.

---

## 🔧 Troubleshooting

### "No module named PyQt6"

Run:
```bash
pip install -r requirements.txt
```

### Build fails with "Qt6 plugins not found"

Make sure PyQt6 is installed:
```bash
pip install PyQt6 PyQt6-Qt6
```

### Installer not created

Check that Inno Setup 6 is installed at the path in `build_windows.bat`
(default: `D:\Apps\Inno Setup 6\iscc.exe`).

If installed elsewhere, edit `build_windows.bat` and update:
```batch
set "ISCC_EXE=YOUR_PATH\iscc.exe"
```

### No audio

- Ensure your default audio device is enabled
- Try playing an MP3 first (most universally supported)
- For FLAC/OGG, install "Web Media Extensions" from Microsoft Store
- Check system tray for error notifications
- **On Linux:** make sure PipeWire or PulseAudio is running
  (`pipewire` or `pulseaudio` processes)

### Equalizer / silence-skip / pitch-preserving speed unavailable

Those features need the optional **mpv engine**. Install the system `libmpv`
and `pip install python-mpv`, then restart the app — the engine is
detected automatically and the Qt engine is used otherwise (gapless and
crossfade work on both engines).

### Waveform seekbar or spectrum analyzer missing

Install the optional `miniaudio` extra (`pip install miniaudio`) and
restart — the waveform seekbar falls back to a plain seekbar (toggle in
Settings → Playback) and the spectrum hides itself when no decoder is
available.

### App disappears when clicking X

That's intended — Golden Music minimizes to the system tray.
- **Left-click** the tray icon to show/hide the window
- **Right-click** the tray icon for quick actions (Play, Favorites, Quit)

### Album cover not showing

- The audio file may not have embedded cover art
- Golden Music will show a golden gradient placeholder instead

### Program runs but is unstable

1. Make sure you have the latest version from [Releases](../../releases)
2. Back up your music library data if you need it (`~/.goldenmusic/library.db`)
   and delete the config folder, then restart:
   ```bash
   rmdir /s %USERPROFILE%\.goldenmusic
   ```
3. If issues persist, [report a bug](../../issues/new?labels=bug)

---

## 📁 Configuration Location

Golden Music stores its data at `%USERPROFILE%\.goldenmusic\` on Windows
(`~/.goldenmusic/` on Linux):

```
~/.goldenmusic/
├── library.db           # SQLite library: tracks, tags, FTS index, favorites,
│                        #   playlists, ratings, play history, bookmarks,
│                        #   radio stations, waveform cache
├── config.json          # Settings & preferences
├── lyrics_cache/        # Cached lyrics
├── backups/             # Automatic backups (incl. pre_sqlite_<timestamp>/)
└── goldenmusic.log      # Rotating diagnostics log
```

**To reset to defaults:** delete this folder and restart the app — note that
this also removes `library.db` (your library), so copy it somewhere safe
first if you might want it back.

*Portable mode:* if a `goldenmusic_config.json` sits next to the executable
(or `main.pyw`), the app stores everything in that folder instead.

**Upgrading from 2.x:** the first 3.0 start backs up your legacy JSON files
into `backups/pre_sqlite_<timestamp>/` and migrates them into `library.db`
in one atomic, row-count-verified, rollback-safe transaction — the original
files are never deleted, and a 2.x downgrade keeps working.

---

## ❓ Need Help?

- 📖 [README](README.md) — Overview and features
- 🤝 [Contributing](CONTRIBUTING.md) — How to contribute
- 🐛 [Report a Bug](../../issues/new?labels=bug)
- 💡 [Request a Feature](../../issues/new?labels=enhancement)
- 💬 [Start a Discussion](../../discussions)

---

<div align="center">

**Enjoy your music!** 🎵

</div>
