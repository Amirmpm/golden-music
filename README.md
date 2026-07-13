<div align="center">

<img src="assets/logo.png" width="120" height="120" alt="Golden Music Logo">

# 🎵 Golden Music

### A beautiful, modern music player for Windows

*Listen to your music in style — 14 themes, zero clutter, pure enjoyment.*

---

![Python](https://img.shields.io/badge/Python-3.14+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![PyQt6](https://img.shields.io/badge/PyQt6-6.11+-41CD52?style=for-the-badge&logo=qt&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Windows-0078D6?style=for-the-badge&logo=windows&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)
![Version](https://img.shields.io/badge/Version-1.0.0-brightgreen?style=for-the-badge)
![Stars](https://img.shields.io/github/stars/Amirmpm/golden-music?style=for-the-badge)

</div>

---

## 📸 Screenshots

<table>
<tr>
<td align="center"><b>Aurora Theme</b></td>
<td align="center"><b>Dark Gold Theme</b></td>
</tr>
<tr>
<td><img src="screenshots/aurora.png" width="400" alt="Aurora Theme"></td>
<td><img src="screenshots/dark_gold.png" width="400" alt="Dark Gold Theme"></td>
</tr>
<tr>
<td align="center"><b>Midnight Blue</b></td>
<td align="center"><b>Ocean Cyan</b></td>
</tr>
<tr>
<td><img src="screenshots/midnight.png" width="400" alt="Midnight Blue"></td>
<td><img src="screenshots/ocean.png" width="400" alt="Ocean Cyan"></td>
</tr>
</table>

---

## ✨ Features

### 🎨 Beautiful & Modern Interface
- **14 stunning themes** — Aurora, Dark Gold, Light Gold, Midnight Blue, Forest Green, Rose Gold, Carbon Black, Ocean Cyan, Sunset Pink, Emerald, Lavender, Sand, Crimson, Teal
- **Modern SVG icons** — Clean Lucide/Feather-style line-art throughout
- **Two-column layout** — Track list on the left, album art on the right
- **Custom logo** — Golden play button branding
- **System tray integration** — Minimize to tray, quick controls

### 🎵 Powerful Playback
- **All popular formats** — MP3, WAV, FLAC, OGG, M4A, AAC, WMA
- **Windows Media Foundation** backend for superior audio quality
- **Click-to-seek** progress bar — Jump anywhere instantly
- **Smooth volume control** — Click or drag
- **Shuffle & Repeat** — Off / All / One modes
- **Album cover art** — Extracted automatically from file metadata

### 📚 Smart Library Management
- **Add folders** with recursive subfolder scanning
- **Folder tree** navigation in the sidebar
- **Instant search** — Filter by title or artist
- **Sort by** Title, Artist, Date Added, or Filename
- **Right-click context menu** — Play, Add/Remove Favorites, Remove from Library
- **Refresh button** — Rescan folders for new or removed tracks
- **Auto-rescan** on startup (configurable)
- **Tag caching** — Instant loading on subsequent launches

### ⚡ Performance & Stability
- **Async everything** — No UI freeze, even with 2000+ songs
- **Background tag loading** — Library appears instantly, tags load progressively
- **Progress bars** for all heavy operations
- **Crash-proof** — Every operation wrapped in error handling
- **Memory efficient** — Cached data with automatic cleanup

### 🎯 User Experience
- **Mini Player** — Compact floating window (`Ctrl+M`)
- **Sleep Timer** — Auto-stop after N minutes
- **Keyboard shortcuts** — Full control without touching the mouse
- **Settings dialog** — 4 tabs (Appearance, Playback, Library, About)
- **Remember last track** — Resume where you left off
- **13 themes picker** — Visual swatch grid for instant switching

---

## ⌨️ Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Space` | Play / Pause |
| `Ctrl + ←` | Previous track |
| `Ctrl + →` | Next track |
| `Ctrl + ↑` | Volume Up |
| `Ctrl + ↓` | Volume Down |
| `Ctrl + F` | Focus search bar |
| `Ctrl + M` | Toggle Mini Player |

---

## 🚀 Quick Start

### Option 1: Download Installer (Recommended)

1. Go to [Releases](../../releases)
2. Download `GoldenMusicSetup-1.0.0.exe`
3. Run the installer
4. Enjoy your music! 🎉

### Option 2: Run from Source

```bash
# Clone the repository
git clone https://github.com/Amirmpm/golden-music.git
cd golden-music

# Install dependencies
pip install -r requirements.txt

# Run
python main.py
```

### Option 3: Build the Installer Yourself

**Prerequisites:**
- [Python 3.14+](https://www.python.org/downloads/)
- [Inno Setup 6](https://jrsoftware.org/isdl.php)

```bash
# Build the executable and installer
build_windows.bat
```

Output:
- `dist\GoldenMusic\GoldenMusic.exe` — The app
- `dist\installer\GoldenMusicSetup-1.0.0.exe` — The installer

---

## 📁 Project Structure

```
golden-music/
├── main.py                  # Main application window
├── config.py                # Themes, QSS stylesheets, helpers
├── icons.py                 # SVG icon library (30+ icons)
├── audio.py                 # PyQt6 QMediaPlayer audio backend
├── scanner.py               # Async folder scanner (QThread)
├── coverart.py              # Album art extraction (thread-safe)
├── widgets.py               # ClickableSlider widget
├── tray_menu.py             # Custom tray popup menu
├── theme_picker.py          # Theme swatch picker popup
├── settings_dialog.py       # Settings dialog (4 tabs)
├── mini_player.py           # Compact floating player
├── requirements.txt         # Python dependencies
├── goldenmusic.spec         # PyInstaller spec file
├── goldenmusic.iss          # Inno Setup installer script
├── build_windows.bat        # One-click build script
├── assets/                  # Icons, logos, images
│   ├── logo.png             # Custom app logo
│   ├── icon.png             # App icon (512x512)
│   ├── icon.ico             # Windows icon (multi-size)
│   └── icon_*.png           # Various sizes
├── screenshots/             # App screenshots for README
├── LICENSE                  # MIT License
├── README.md                # This file
├── CONTRIBUTING.md          # Contribution guidelines
├── CHANGELOG.md             # Version history
├── INSTALLATION.md          # Detailed installation guide
└── .gitignore               # Git ignore rules
```

---

## 🎨 Themes

Golden Music ships with **14 beautiful themes**:

| Theme | Style | Primary Color |
|-------|-------|---------------|
| Aurora | Dark | Pink/Magenta |
| Dark Gold | Dark | Gold |
| Light Gold | Light | Gold |
| Midnight Blue | Dark | Blue |
| Forest Green | Dark | Green |
| Rose Gold | Dark | Rose |
| Carbon Black | Dark | Silver |
| Ocean Cyan | Dark | Cyan |
| Sunset Pink | Dark | Pink |
| Emerald | Dark | Emerald |
| Lavender | Dark | Purple |
| Sand | Light | Sand |
| Crimson | Dark | Red |
| Teal | Dark | Teal |

Click the **theme icon** in the sidebar to open the visual theme picker and switch instantly.

---

## 🛠️ Tech Stack

| Technology | Purpose |
|------------|---------|
| ![Python](https://img.shields.io/badge/Python-3.14+-3776AB) | Core language |
| ![PyQt6](https://img.shields.io/badge/PyQt6-6.11+-41CD52) | GUI framework |
| ![mutagen](https://img.shields.io/badge/mutagen-1.47+-blue) | Audio metadata |
| ![PyInstaller](https://img.shields.io/badge/PyInstaller-6.10+-red) | EXE packaging |
| ![Inno Setup](https://img.shields.io/badge/Inno_Setup-6-orange) | Windows installer |

---

## 📦 Configuration

Golden Music stores its configuration at:

```
%USERPROFILE%\.goldenmusic\
├── config.json          # App settings, library, favorites
└── tag_cache.json       # Cached metadata for instant loading
```

**What's stored:**
- Added folders, library tracks, favorites
- Last played track and position
- Volume, theme, shuffle/repeat state
- Window geometry
- All settings preferences

---

## 🤝 Contributing

**We welcome contributions from everyone!** 🎉

Golden Music is an open-source project and we'd love your help to make it even better. Whether you're a developer, designer, translator, or just have a great idea — there's a place for you here.

### Ways to Contribute

- 🐛 **Report bugs** — Found a bug? [Open an issue](../../issues/new)
- 💡 **Suggest features** — Have an idea? [Let us know](../../issues/new)
- 🎨 **Design themes** — Create new color themes
- 🌍 **Translate** — Help translate the UI
- 📝 **Improve docs** — Fix typos, add guides
- 🔧 **Submit code** — Fix bugs, add features via Pull Request

### How to Contribute

1. **Fork** the repository
2. **Clone** your fork:
   ```bash
   git clone https://github.com/Amirmpm/golden-music.git
   ```
3. **Create a branch**:
   ```bash
   git checkout -b feature/my-awesome-feature
   ```
4. **Make your changes** and test them
5. **Commit** with a clear message:
   ```bash
   git commit -m "feat: add awesome new feature"
   ```
6. **Push** to your fork:
   ```bash
   git push origin feature/my-awesome-feature
   ```
7. **Open a Pull Request** 🚀

### 📋 Requesting a Version Upgrade

Want a new feature or improvement in the next version? Here's how:

1. **Check existing issues** — Someone may have already requested it
2. **Open a new issue** with the label `enhancement` or `feature-request`
3. **Describe clearly** what you'd like and why it's useful
4. **Upvote** existing requests with 👍

We review all requests and prioritize based on community interest and feasibility.

See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed guidelines.

---

## 📝 License

This project is licensed under the **MIT License** — see [LICENSE](LICENSE) for details.

```
MIT License

Copyright (c) 2026 Golden Music

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files...
```

**In short:** You're free to use, modify, distribute, and even sell this software. Just keep the copyright notice.

---

## 🙏 Acknowledgments

This project stands on the shoulders of giants:

- **[PyQt6](https://www.riverbankcomputing.com/software/pyqt/)** — by Riverbank Computing
- **[mutagen](https://mutagen.readthedocs.io/)** — Audio metadata library
- **[Lucide](https://lucide.dev/)** — Icon design inspiration
- **[Inno Setup](https://jrsoftware.org/isinfo.php)** — by Jordan Russell
- **[Python](https://www.python.org/)** — The language that makes it all possible

---

## 📊 Project Stats

![GitHub stars](https://img.shields.io/github/stars/Amirmpm/golden-music?style=social)
![GitHub forks](https://img.shields.io/github/forks/Amirmpm/golden-music?style=social)
![GitHub issues](https://img.shields.io/github/issues/Amirmpm/golden-music)
![GitHub downloads](https://img.shields.io/github/downloads/Amirmpm/golden-music/total)
![GitHub last commit](https://img.shields.io/github/last-commit/Amirmpm/golden-music)

---

## 🗺️ Roadmap

### Version 1.x
- [ ] Equalizer (10-band)
- [ ] Crossfade between tracks
- [ ] System media key integration
- [ ] Playlist management (custom playlists)
- [ ] Lyrics display

### Version 2.x
- [ ] Multi-language support
- [ ] Cloud sync for favorites
- [ ] Podcast support
- [ ] Theme editor
- [ ] Mobile companion app

*Have an idea for the roadmap? [Suggest it!](../../issues/new)*

---

## 💬 Community

- 🐛 [Report a Bug](../../issues/new?labels=bug)
- 💡 [Request a Feature](../../issues/new?labels=enhancement)
- 💬 [Start a Discussion](../../discussions)
- 📧 Contact: [Open an issue](../../issues/new)

---

<div align="center">

### ⭐ If you like Golden Music, give it a star! ⭐

**Made with ❤️ and lots of ☕**

*First public release — Version 1.0.0*

</div>
