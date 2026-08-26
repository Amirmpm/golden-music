# 📦 Golden Music — Installation Guide

This guide covers all the ways to install and run Golden Music on Windows.

---

## 🚀 Quick Install (Recommended)

### Download the Installer

1. Go to [Releases](../../releases)
2. Download `GoldenMusicSetup-1.0.0.exe`
3. Run the installer
4. Follow the setup wizard
5. Launch Golden Music from the Start Menu or Desktop shortcut

**That's it!** No Python or dependencies needed — everything is bundled.

---

## 🛠️ Build from Source

### Prerequisites

#### 1. Python 3.14 or later

Golden Music requires **Python 3.14+** (also works with 3.11–3.13).

- Download: [python.org/downloads](https://www.python.org/downloads/)
- During installation, **check "Add Python to PATH"**

Verify installation:
```bash
python --version
# Should print: Python 3.14.x
```

#### 2. Inno Setup 6 (for building the installer)

- Download: [jrsoftware.org/isdl.php](https://jrsoftware.org/isdl.php)
- Install to default location or `D:\Apps\Inno Setup 6\`
- If installed elsewhere, edit `ISCC_EXE` in `build_windows.bat`

---

### Build Steps

1. **Download or clone the source code:**
   ```bash
   git clone https://github.com/yourusername/golden-music.git
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
   dist\installer\GoldenMusicSetup-1.0.0.exe # The installer
   ```

6. **Run the installer** (`GoldenMusicSetup-1.0.0.exe`) to install properly.

---

## 👨‍💻 Run from Source (Development)

If you want to run Golden Music without building an installer:

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Run:**
   ```bash
   python main.py
   ```

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
│   │   │       └── plugins\
│   │   │           ├── mediaservice\    # Audio backend
│   │   │           ├── iconengines\     # SVG rendering
│   │   │           ├── imageformats\    # Cover art
│   │   │           ├── platforms\
│   │   │           ├── styles\
│   │   │           └── audio\
│   │   └── _internal\
│   └── installer\
│       └── GoldenMusicSetup-1.0.0.exe   # The installer
└── ...
```

**If `dist\GoldenMusic\PyQt6\Qt5\plugins\mediaservice\` is missing or empty, audio playback won't work.** Re-run the build.

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

### App disappears when clicking X

That's intended — Golden Music minimizes to the system tray.
- **Left-click** the tray icon to show/hide the window
- **Right-click** the tray icon for quick actions (Play, Favorites, Quit)

### Album cover not showing

- The audio file may not have embedded cover art
- Golden Music will show a golden gradient placeholder instead

### Program runs but is unstable

1. Make sure you have the latest version from [Releases](../../releases)
2. Delete the config folder and restart:
   ```bash
   rmdir /s %USERPROFILE%\.goldenmusic
   ```
3. If issues persist, [report a bug](../../issues/new?labels=bug)

---

## 📁 Configuration Location

Golden Music stores its data at:

```
%USERPROFILE%\.goldenmusic\
├── config.json          # Settings, library, favorites
└── tag_cache.json       # Cached metadata
```

**To reset to defaults:** Delete this folder and restart the app.

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
