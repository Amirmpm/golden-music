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
Help translate Golden Music to your language — the i18n infrastructure now
ships in `gm/i18n.py` (Persian + full RTL layout already included). Open an
issue and we'll point you at the string tables!

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
- Python 3.11 or higher
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
   (On Windows add `pip install pywin32`; on Linux install the Qt system
   libraries first — see [INSTALLATION.md](INSTALLATION.md))

5. **Run the app**:
   ```bash
   python main.pyw
   ```

6. **Create a branch** for your changes:
   ```bash
   git checkout -b feature/my-feature
   ```

---

## 📝 Development Workflow

1. **Make your changes** following the code style below
2. **Test your changes** thoroughly (cross-platform, headless-friendly):
   ```bash
   QT_QPA_PLATFORM=offscreen python scripts/test_functional.py
   ```
   (Windows PowerShell: `$env:QT_QPA_PLATFORM="offscreen"` first — see the
   Testing section for the full suite list)
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
| `style` | Code style | `style: format main.pyw with black` |
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
- The v3 QSS design system (`gm/theme_qss.py`) picks themes up from there —
  verify your palette in both the row and table views

---

## 🧩 New-module Conventions

- **Guarded imports for optional dependencies** — wrap optional deps
  (python-mpv, miniaudio, qrcode, dbus-python) in `try/except ImportError`
  and degrade gracefully. A missing optional must never crash or block
  startup — features simply mark themselves unavailable
- **Never crash at IO boundaries** — file, tag, database and lyrics-provider
  failures are logged and surfaced to the user, never raised into the UI
  thread
- **English UI strings + `tr()`** — new UI chrome uses English literals
  wrapped in the translation helper from `gm/i18n.py`, so translations can
  pick it up (Persian + RTL already ship)
- **Plain-script tests** — every new module ships a
  `scripts/test_<name>.py` suite following the `[OK]` / `[FAIL]` per-check,
  `SUMMARY` line, non-zero exit code convention (below)

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
- OS: Windows 11 / Ubuntu 24.04 (include your distro on Linux)
- Python version: 3.12.0
- Golden Music version: 3.0.0
- How did you install: [installer / from source]
- Optional extras installed: [python-mpv / miniaudio / qrcode / dbus-python / none]
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

The project has **23 offline test suites** — plain Python scripts, no pytest
required — and CI runs them on **Windows and Linux** (Python 3.12/3.13,
offscreen Qt, plus a Bandit security gate and the build job).

Run any suite locally, headless-friendly and cross-platform:

```bash
# Linux / macOS
QT_QPA_PLATFORM=offscreen python scripts/test_functional.py
```

```powershell
# Windows (PowerShell)
$env:QT_QPA_PLATFORM = "offscreen"
python scripts\test_functional.py
```

The suites: the 12 v2 suites — `test_functional`, `test_deep`,
`test_stability`, `test_stability_v2`, `test_history_nav`, `test_features`,
`test_crash`, `test_tray_toggle`, `test_open_with`, `test_shortcuts`,
`test_updater`, `test_version_sync` — plus the 11 v3 suites — `test_sqlite`,
`test_store_boot`, `test_audio_engines`, `test_lyrics3`, `test_remote`,
`test_m3u`, `test_ui_components`, `test_settings3`, `test_pages3`,
`test_stats3`, `test_lyrics_ui`.

**All tests must pass before a PR can be merged** — on BOTH platforms when
your change touches platform-specific code.

### ⚠️ The testenv sandbox rule (absolute)

Any script that boots `MainWindow` **must** call `testenv.install()` before
any other project import:

```python
sys.path.insert(0, <repo root>)
import testenv; testenv.install()
```

`scripts/testenv.py` sandboxes `HOME`/`USERPROFILE` and the portable dev
config so a test can never overwrite real user data. This rule is
non-negotiable: the 2.1.1 data-loss incident — a test suite booted the real
`MainWindow` with fake tracks and `_save_config()` wiped a real library —
is exactly what it prevents. Escape hatch (live-data screenshot scripts
only): `GOLDENMUSIC_ALLOW_REAL_CONFIG=1`.

### Writing new tests

- Plain scripts: print `[OK]` / `[FAIL]` per check, end with a
  `SUMMARY: N passed, M failed` line, and exit non-zero on any failure —
  that's what CI gates on
- Keep them offline: no network, no real user data

### Security gate (Bandit)

```bash
bandit -r . -q --exclude "./.git,./build,./dist,./screenshots,./scripts,./assets" --severity-level medium
```

Zero findings at medium severity or above — same command CI runs.

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
├── main.pyw / main.py   # Thin entry points (double-click / importable)
├── gm/                  # The application package (v3)
│   ├── core.py / window.py / app.py     # MainWindow, app bootstrap
│   ├── platforms.py                     # Cross-platform OS layer
│   ├── db.py / store.py / migration.py  # SQLite library (WAL + FTS5)
│   ├── player_adapter.py / audio_engines.py   # Engine layer (mpv → Qt)
│   ├── lyrics_*.py                      # Search, quality, editor, i18n
│   ├── webremote.py / telegrambot.py / mpris.py
│   ├── workers.py                       # Background threads
│   ├── prefs.py / i18n.py / theme_qss.py / dynamic_theme.py
│   └── ui/                              # All v3 widgets & pages
├── config.py, icons.py, scanner.py, ... # Shared v2-era modules still in
│                                        #   use (themes, icons, scanning)
├── scripts/             # 23 offline test suites + tooling
├── docs/DECISIONS.md    # Architecture decision log — read this first
└── requirements.txt     # Hard deps + documented optional extras
```

**Architecture questions?** Read [docs/DECISIONS.md](docs/DECISIONS.md) —
it records every non-obvious 3.0 decision (SQLite + FTS5, the audio engine
layer with mpv→Qt fallback, opt-in smart playlists, the content-driven FFT
spectrum analyzer, the stdlib-only web remote, …) and the reasoning behind
each one.

---

## 🎯 Areas Needing Help

### High Priority
- 🐧 **Linux packaging** — deb/rpm, AppImage or Flatpak (PyInstaller
  one-dir builds already work; see [INSTALLATION.md](INSTALLATION.md))
- 🌍 **More translations** — the i18n infrastructure and Persian (RTL)
  already ship in `gm/i18n.py`; more languages are welcome

### Medium Priority
- 📱 **Android port** — the PySide6 + Qt for Android route is documented in
  [INSTALLATION.md](INSTALLATION.md); big but well-scoped
- 🎤 **More lyrics providers** — the provider interface is injectable
  (lrclib.net ships built-in)
- 🎨 **Theme editor** — a custom theme creator on top of the v3 QSS design
  system

### Nice to Have
- ☁️ **Cloud sync** — sync favorites and ratings across devices
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
