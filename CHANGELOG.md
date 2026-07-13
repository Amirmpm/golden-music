# Changelog

All notable changes to Golden Music will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.0] - 2026-07-14

### 🎉 First Public Release

The first stable, production-ready version of Golden Music!

### ✨ Added

#### Core Features
- **14 beautiful themes**: Aurora (default), Dark Gold, Light Gold, Midnight Blue, Forest Green, Rose Gold, Carbon Black, Ocean Cyan, Sunset Pink, Emerald, Lavender, Sand, Crimson, Teal
- **Theme picker popup** — Visual swatch grid for instant theme switching
- **Two-column layout** — Track list on the left, album art on the right
- **Folder tree** — Sidebar navigation with subfolders
- **Search bar** — Instant filtering by title or artist
- **Sort options** — Title, Artist, Date Added, Filename
- **Right-click context menu** — Play, Add/Remove Favorites, Remove from Library
- **Refresh button** — Rescan folders for new or removed tracks
- **Auto-rescan** on startup (configurable)
- **Tag caching** — Instant loading on subsequent launches

#### Playback
- **All popular formats**: MP3, WAV, FLAC, OGG, M4A, AAC, WMA
- **Windows Media Foundation** backend for superior audio quality
- **Click-to-seek** progress bar — Jump anywhere instantly
- **Smooth volume control** — Click or drag
- **Shuffle & Repeat** — Off / All / One modes
- **Album cover art** — Extracted automatically from file metadata

#### User Experience
- **Mini Player** — Compact floating window (`Ctrl+M`)
- **Sleep Timer** — Auto-stop after N minutes
- **Keyboard shortcuts** — Full control without the mouse
- **Settings dialog** — 4 tabs (Appearance, Playback, Library, About)
- **Remember last track** — Resume where you left off
- **System tray integration** — Left-click to toggle, right-click for quick actions

#### Interface
- **Custom logo** — Golden play button branding
- **Modern SVG icons** — Lucide/Feather-style line-art (no emoji)
- **Always-collapsed sidebar** with tooltips
- **Custom tray menu** with icon buttons
- **Progress bars** for all heavy operations

### 🔧 Technical

#### Architecture
- **PyQt6** (Python 3.14) for modern GUI
- **Async everything** — No UI freeze, even with 2000+ songs
- **Background tag loading** — Library appears instantly
- **Crash-proof** — Every operation wrapped in error handling
- **Memory efficient** — Cached data with automatic cleanup

#### Files
- `main.py` — Main application window
- `config.py` — Themes, QSS stylesheets, helpers
- `icons.py` — SVG icon library (30+ icons)
- `audio.py` — PyQt6 QMediaPlayer audio backend
- `scanner.py` — Async folder scanner (QThread)
- `coverart.py` — Album art extraction (thread-safe)
- `widgets.py` — ClickableSlider widget
- `tray_menu.py` — Custom tray popup menu
- `theme_picker.py` — Theme swatch picker popup
- `settings_dialog.py` — Settings dialog (4 tabs)
- `mini_player.py` — Compact floating player

### 🛠️ Fixed
- UI freeze when scanning large libraries
- Cover art not displaying (thread-safe QPixmap transfer)
- Play button not exactly centered
- Inno Setup `checked` flag error
- PyInstaller `QtSvgWidgets` hidden import warning
- Crash on slider click (removed custom QStyle internals)
- Audio playback feedback loop causing 0.5s repeat
- Music playback not working (simplified audio backend)
- Metadata not reading for some files (improved fallback)

### 📦 Build System
- **PyInstaller** spec for EXE packaging
- **Inno Setup** for Windows installer
- **One-click build** script (`build_windows.bat`)
- Bundles all Qt6 plugins (mediaservice, iconengines, imageformats)

---

## Version History

### Development Versions (pre-release)

<details>
<summary>Click to expand development history</summary>

### [0.5.0] - Internal testing
- Initial PyQt5 prototype
- Basic playback controls
- 2 themes (Dark Gold, Light Gold)

### [0.7.0] - Internal testing
- Migration to PyQt6
- 6 themes
- Modern SVG icons
- Seek bar at top of player bar

### [0.8.0] - Internal testing
- 13 themes
- Theme picker popup
- Folder tree
- Search and sort
- Right-click context menu
- Keyboard shortcuts
- Mini player
- Settings dialog

### [0.9.0] - Internal testing
- Aurora theme added (14 total)
- Async config loading
- Tag caching
- Smooth sidebar animation
- Crash fixes (removed QStyle internals)

### [0.9.5] - Internal testing
- Audio backend simplification
- Feedback loop fix
- Custom logo integration
- Tray menu improvements
- Refresh button
- Metadata cleanup

</details>

---

## 🔗 Links

- [Releases](../../releases)
- [Issues](../../issues)
- [Pull Requests](../../pulls)
- [Contributing Guide](CONTRIBUTING.md)

---

<div align="center">

**Golden Music v1.0.0** — First Public Release 🎉

</div>
