# 🤝 Contributing to Golden Music

First off, **thank you** for considering contributing to Golden Music! 🎉

This project is open-source and we welcome contributions from everyone — whether you're a seasoned developer or just getting started. Every contribution, no matter how small, makes a difference.

---

## 🌟 Ways to Contribute

You don't have to write code to contribute! Here are many ways you can help:

### 🐛 Report Bugs
Found a bug? Help us fix it!
1. Check if the bug is already reported in [Issues](../../issues)
2. If not, [open a new issue](../../issues/new?labels=bug)
3. Use the bug report template below

### 💡 Suggest Features
Have an idea to make Golden Music better?
1. [Open a feature request](../../issues/new?labels=enhancement)
2. Describe your idea clearly
3. Explain why it would be useful

### 🎨 Create Themes
Love design? Create new color themes!
1. Fork the repository
2. Add your theme to `config.py` in the `Theme` class
3. Submit a Pull Request

### 🌍 Translate
Help translate Golden Music to your language.
*(Internationalization support coming soon — express interest in an issue!)*

### 📝 Improve Documentation
- Fix typos
- Add guides
- Improve code comments
- Translate documentation

### 🔧 Write Code
Fix bugs, add features, optimize performance.

---

## 🚀 Getting Started for Developers

### Prerequisites
- Python 3.14 or higher
- Git
- A code editor (VS Code, PyCharm, etc.)

### Setup Development Environment

1. **Fork** the repository on GitHub

2. **Clone** your fork:
   ```bash
   git clone https://github.com/yourusername/golden-music.git
   cd golden-music
   ```

3. **Create a virtual environment**:
   ```bash
   python -m venv .venv
   .venv\Scripts\activate  # Windows
   # source .venv/bin/activate  # Linux/Mac
   ```

4. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

5. **Run the app**:
   ```bash
   python main.py
   ```

6. **Create a branch** for your changes:
   ```bash
   git checkout -b feature/my-feature
   ```

---

## 📝 Development Workflow

1. **Make your changes** following the code style below
2. **Test your changes** thoroughly:
   ```bash
   python scripts/test_v3.py        # Functional tests
   python scripts/test_crash.py     # Crash tests
   ```
3. **Commit** with a clear message (see convention below)
4. **Push** to your fork
5. **Open a Pull Request** with a clear description

---

## 📋 Commit Message Convention

We use [Conventional Commits](https://www.conventionalcommits.org/):

| Type | Description | Example |
|------|-------------|---------|
| `feat` | New feature | `feat: add equalizer with 10-band control` |
| `fix` | Bug fix | `fix: resolve crash when clicking seek bar` |
| `docs` | Documentation | `docs: update README with new screenshots` |
| `style` | Code style | `style: format main.py with black` |
| `refactor` | Refactoring | `refactor: simplify audio backend` |
| `test` | Tests | `test: add crash test for slider` |
| `chore` | Build/tooling | `chore: update dependencies` |

**Format:**
```
type: brief description

[optional body with details]

[optional footer]
```

---

## 🎨 Code Style

### Python
- Follow [PEP 8](https://pep8.org/)
- Use 4-space indentation
- Maximum line length: 100 characters
- Use descriptive variable names
- Add docstrings for all public functions/classes

### PyQt6
- Use fully-qualified enums: `Qt.AlignmentFlag.AlignCenter` (not `Qt.AlignCenter`)
- Use `Qt.Orientation.Horizontal` (not `Qt.Horizontal`)
- Use `Qt.ItemDataRole.UserRole` (not `Qt.UserRole`)
- Always set object names for styled widgets: `widget.setObjectName("MyWidget")`

### Icons
- All icons must be SVG (no emoji in UI)
- Use the `Icon` class in `icons.py`
- 24x24 viewBox, 2px stroke, Lucide-style

### Themes
- Add new themes to the `Theme` class in `config.py`
- Include all required keys: `name`, `window_bg`, `panel_bg`, `panel_bg_2`, `border`, `text`, `muted`, `gold`, `gold_light`, `gold_deep`, `active`, `is_dark`
- Add a display name in `Theme.display_name()`
- Add the theme to `Theme.ALL` list

---

## 🐛 Bug Report Template

```markdown
**Describe the bug**
A clear description of the bug.

**To Reproduce**
Steps to reproduce:
1. Go to '...'
2. Click on '...'
3. See error

**Expected behavior**
What you expected to happen.

**Screenshots**
If applicable, add screenshots.

**Environment:**
- OS: Windows 11
- Python version: 3.14.0
- Golden Music version: 1.0.0
- How did you install: [installer / from source]
```

---

## ✨ Feature Request Template

```markdown
**Is your feature request related to a problem?**
A description of what the problem is.

**Proposed solution**
A clear description of what you want to happen.

**Alternatives considered**
Any alternative solutions you've considered.

**Additional context**
Any other context or screenshots about the feature request.
```

---

## 🧪 Testing

Before submitting a Pull Request, please test:

```bash
# Run functional tests
python scripts/test_v3.py

# Run crash tests
python scripts/test_crash.py

# Run stability tests
python scripts/test_stability.py
```

**All tests must pass** before a PR can be merged.

---

## 📦 Building

See [INSTALLATION.md](INSTALLATION.md) for build instructions.

To build the installer:
```bash
build_windows.bat
```

---

## 🗺️ Project Structure

```
golden-music/
├── main.py              # Main window (start here)
├── config.py            # Themes, QSS, helpers
├── icons.py             # SVG icons
├── audio.py             # Audio backend
├── scanner.py           # Folder scanner
├── coverart.py          # Cover art extraction
├── widgets.py           # Custom widgets
├── tray_menu.py         # Tray menu
├── theme_picker.py      # Theme picker
├── settings_dialog.py   # Settings dialog
├── mini_player.py       # Mini player
├── assets/              # Icons and images
└── scripts/             # Test scripts
```

---

## 🎯 Areas Needing Help

### High Priority
- 🎚️ **Equalizer** — 10-band graphic EQ
- 🔊 **Crossfade** — Smooth transitions between tracks
- 📱 **System media keys** — Integrate with Windows media keys
- 🌍 **Internationalization** — Multi-language support

### Medium Priority
- 📋 **Playlist management** — Custom playlists
- 🎤 **Lyrics display** — Synced lyrics
- 📊 **Statistics** — Play count, recently played
- 🔄 **Gapless playback** — For albums/concerts

### Nice to Have
- 📱 **Mobile companion app**
- ☁️ **Cloud sync** — Sync favorites across devices
- 🎨 **Theme editor** — Custom theme creator
- 📡 **Podcast support**

---

## ❓ Questions?

Feel free to [open an issue](../../issues/new) with the `question` label. No question is too small!

---

## 📜 Code of Conduct

Be respectful and kind. We're all here because we love music and open-source. Harassment or discrimination of any kind will not be tolerated.

---

## 🙏 Thank You

Every contribution matters. Whether you fixed a typo, reported a bug, or added a major feature — **you're part of the Golden Music community**. 

Let's make the best music player together! 🎵

---

<div align="center">

**Happy coding!** 💻

</div>
