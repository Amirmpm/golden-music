"""
Golden Music — Configuration, themes, and helpers.
Python 3.14 + PyQt6.
"""
import sys
import os
import json
import re
from pathlib import Path
from enum import Enum

from PyQt6.QtCore import Qt, QSize, QTimer, QUrl
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton,
    QVBoxLayout, QHBoxLayout, QGridLayout, QFrame, QSizePolicy,
    QSlider, QListWidget, QListWidgetItem, QStackedWidget,
    QSystemTrayIcon, QMenu, QFileDialog, QMessageBox,
    QStyle, QStyleFactory, QAbstractItemView,
    QToolButton, QButtonGroup
)
from PyQt6.QtGui import (
    QIcon, QPixmap, QPainter, QColor, QFont, QPalette,
    QLinearGradient, QBrush, QRadialGradient, QFontDatabase,
    QMouseEvent, QKeyEvent, QPaintEvent, QPen
)

# ---- Optional audio backend ----
try:
    from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
    HAVE_QT_AUDIO = True
except ImportError:
    HAVE_QT_AUDIO = False

APP_NAME = "Golden Music"
APP_VERSION = "1.1.0"
ORG_NAME = "GoldenMusic"

# Audio extensions
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".aac", ".wma"}


def config_path() -> Path:
    """Return path to JSON config file."""
    here = Path(sys.argv[0]).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
    portable = here / "goldenmusic_config.json"
    if portable.exists():
        return portable
    home = Path.home()
    cfg_dir = home / ".goldenmusic"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    return cfg_dir / "config.json"


def restrict_file_permissions(path) -> None:
    """Best-effort owner-only permissions on written data files.

    The config and caches contain local filesystem paths (privacy-sensitive).
    POSIX: chmod 0600. Other platforms (Windows ACLs already scope per-user):
    no-op.
    """
    try:
        if os.name == "posix":
            os.chmod(path, 0o600)
    except OSError:
        pass  # non-critical hardening — never fail a save over permissions


# ============================================================================
# Themes
# ============================================================================
class Theme:
    DARK = {
        "name": "dark",
        "window_bg": "#1a1410",
        "panel_bg": "#241d17",
        "panel_bg_2": "#2d251c",
        "border": "#3a2f24",
        "text": "#f0e6d2",
        "muted": "#9a8a6f",
        "gold": "#d4a85a",
        "gold_light": "#e8c47a",
        "gold_deep": "#a8843d",
        "active": "#3a2c1a",
        "is_dark": True,
    }
    LIGHT = {
        "name": "light",
        "window_bg": "#fbf7ee",
        "panel_bg": "#f1e8d2",
        "panel_bg_2": "#e8dbb8",
        "border": "#b8a06d",
        "text": "#2a1f10",
        "muted": "#5a4828",
        "gold": "#7a5a1e",
        "gold_light": "#9a7325",
        "gold_deep": "#5a4216",
        "active": "#d9b870",
        "is_dark": False,
    }
    MIDNIGHT = {
        "name": "midnight",
        "window_bg": "#0f1419",
        "panel_bg": "#1a2330",
        "panel_bg_2": "#243040",
        "border": "#2a3a4a",
        "text": "#e0e8f0",
        "muted": "#7a8a9a",
        "gold": "#4a9eff",
        "gold_light": "#6ab4ff",
        "gold_deep": "#2a7edf",
        "active": "#1e3a5a",
        "is_dark": True,
    }
    FOREST = {
        "name": "forest",
        "window_bg": "#0f1a12",
        "panel_bg": "#1a2820",
        "panel_bg_2": "#243528",
        "border": "#2a4030",
        "text": "#e0f0e0",
        "muted": "#7a9a80",
        "gold": "#5db075",
        "gold_light": "#7dc895",
        "gold_deep": "#3d8855",
        "active": "#1e3a28",
        "is_dark": True,
    }
    ROSE_GOLD = {
        "name": "rose_gold",
        "window_bg": "#1f1418",
        "panel_bg": "#2a1d24",
        "panel_bg_2": "#352830",
        "border": "#403040",
        "text": "#f0e0e4",
        "muted": "#a08890",
        "gold": "#e095a0",
        "gold_light": "#f0a8b4",
        "gold_deep": "#b06878",
        "active": "#3a2228",
        "is_dark": True,
    }
    CARBON = {
        "name": "carbon",
        "window_bg": "#0a0a0a",
        "panel_bg": "#141414",
        "panel_bg_2": "#1e1e1e",
        "border": "#2a2a2a",
        "text": "#e8e8e8",
        "muted": "#808080",
        "gold": "#c0c0c0",
        "gold_light": "#e0e0e0",
        "gold_deep": "#909090",
        "active": "#252525",
        "is_dark": True,
    }
    OCEAN = {
        "name": "ocean",
        "window_bg": "#0a1929",
        "panel_bg": "#102a43",
        "panel_bg_2": "#1a3a5c",
        "border": "#2a4a6c",
        "text": "#e0f0ff",
        "muted": "#7a9ab8",
        "gold": "#00bcd4",
        "gold_light": "#4dd0e1",
        "gold_deep": "#00838f",
        "active": "#1e4a6a",
        "is_dark": True,
    }
    SUNSET = {
        "name": "sunset",
        "window_bg": "#1a0f1a",
        "panel_bg": "#2a1a2a",
        "panel_bg_2": "#3a2535",
        "border": "#4a3545",
        "text": "#ffe8f0",
        "muted": "#a08aa0",
        "gold": "#ff6b9d",
        "gold_light": "#ff8fb1",
        "gold_deep": "#cc4a7a",
        "active": "#3a2235",
        "is_dark": True,
    }
    EMERALD = {
        "name": "emerald",
        "window_bg": "#0a1a14",
        "panel_bg": "#143025",
        "panel_bg_2": "#1e4035",
        "border": "#2e5045",
        "text": "#e0ffe8",
        "muted": "#7aa088",
        "gold": "#10b981",
        "gold_light": "#34d399",
        "gold_deep": "#059669",
        "active": "#1e4035",
        "is_dark": True,
    }
    LAVENDER = {
        "name": "lavender",
        "window_bg": "#1a1428",
        "panel_bg": "#241d38",
        "panel_bg_2": "#2e2848",
        "border": "#3e3858",
        "text": "#f0e8ff",
        "muted": "#9a8ab8",
        "gold": "#a78bfa",
        "gold_light": "#c4b5fd",
        "gold_deep": "#8b5cf6",
        "active": "#2e2448",
        "is_dark": True,
    }
    SAND = {
        "name": "sand",
        "window_bg": "#f5f0e8",
        "panel_bg": "#ede4d3",
        "panel_bg_2": "#e0d4be",
        "border": "#c9b890",
        "text": "#3a2f1a",
        "muted": "#6a5a3a",
        "gold": "#b8862c",
        "gold_light": "#d4a04a",
        "gold_deep": "#8a6620",
        "active": "#d9c490",
        "is_dark": False,
    }
    CRIMSON = {
        "name": "crimson",
        "window_bg": "#1a0a0a",
        "panel_bg": "#2a1414",
        "panel_bg_2": "#3a1e1e",
        "border": "#4a2e2e",
        "text": "#ffe8e8",
        "muted": "#a08888",
        "gold": "#dc2626",
        "gold_light": "#f87171",
        "gold_deep": "#991b1b",
        "active": "#3a1e1e",
        "is_dark": True,
    }
    TEAL = {
        "name": "teal",
        "window_bg": "#0a1a1a",
        "panel_bg": "#142828",
        "panel_bg_2": "#1e3838",
        "border": "#2e4848",
        "text": "#e0f8f8",
        "muted": "#7aa0a0",
        "gold": "#14b8a6",
        "gold_light": "#2dd4bf",
        "gold_deep": "#0d9488",
        "active": "#1e3838",
        "is_dark": True,
    }
    AURORA = {
        "name": "aurora",
        "window_bg": "#0f0c1e",
        "panel_bg": "#1a1530",
        "panel_bg_2": "#251e45",
        "border": "#352d5a",
        "text": "#f0eaff",
        "muted": "#9080b0",
        "gold": "#e91e63",
        "gold_light": "#ff4081",
        "gold_deep": "#c2185b",
        "active": "#2a1f4a",
        "is_dark": True,
    }

    ALL = [DARK, LIGHT, MIDNIGHT, FOREST, ROSE_GOLD, CARBON,
            OCEAN, SUNSET, EMERALD, LAVENDER, SAND, CRIMSON, TEAL, AURORA]

    @classmethod
    def get(cls, name):
        for t in cls.ALL:
            if t["name"] == name:
                return t
        return cls.DARK

    @classmethod
    def names(cls):
        return [t["name"] for t in cls.ALL]

    @classmethod
    def display_name(cls, name):
        return {
            "dark": "Dark Gold",
            "light": "Light Gold",
            "midnight": "Midnight Blue",
            "forest": "Forest Green",
            "rose_gold": "Rose Gold",
            "carbon": "Carbon Black",
            "ocean": "Ocean Cyan",
            "sunset": "Sunset Pink",
            "emerald": "Emerald",
            "lavender": "Lavender",
            "sand": "Sand Light",
            "crimson": "Crimson Red",
            "teal": "Teal",
            "aurora": "Aurora",
        }.get(name, name.title())


# ============================================================================
# QSS
# ============================================================================
def build_qss(t: dict) -> str:
    return f"""
    * {{
        font-family: 'Segoe UI', 'Tahoma', 'Arial', sans-serif;
        outline: none;
        padding: 0;
        margin: 0;
    }}
    QMainWindow, QWidget#Root {{
        background: {t['window_bg']};
    }}
    QWidget#SideRail {{
        background: {t['panel_bg']};
        border-right: 1px solid {t['border']};
    }}
    QToolButton#RailBtn {{
        border-radius: 12px;
        background: transparent;
        border: none;
        padding: 0;
        margin: 0;
    }}
    QToolButton#RailBtn:hover {{ background: {t['panel_bg_2']}; }}
    QToolButton#RailBtn:checked {{ background: {t['active']}; }}
    QWidget#MainArea {{ background: transparent; }}
    QLabel#PageTitle {{
        color: {t['gold_light']}; font-size: 18px; font-weight: 600;
        padding: 4px 0;
    }}
    QLabel#NowTitle {{
        color: {t['gold_light']}; font-size: 22px; font-weight: 700;
    }}
    QLabel#NowArtist {{
        color: {t['muted']}; font-size: 13px;
    }}
    QListWidget#TrackList {{
        background: {t['panel_bg']}; border: 1px solid {t['border']};
        border-radius: 10px;
        color: {t['text']};
        font-size: 13px;
        padding: 6px;
        outline: 0;
    }}
    QListWidget#TrackList::item {{
        padding: 9px 12px; border-radius: 6px;
    }}
    QListWidget#TrackList::item:hover {{ background: {t['panel_bg_2']}; }}
    QListWidget#TrackList::item:selected {{
        background: {t['active']}; color: {t['gold_light']};
    }}
    QListWidget#TrackList QScrollBar:vertical {{
        background: transparent; width: 10px; margin: 4px 2px 4px 0;
    }}
    QListWidget#TrackList QScrollBar::handle:vertical {{
        background: {t['border']}; border-radius: 4px; min-height: 24px;
    }}
    QListWidget#TrackList QScrollBar::handle:vertical:hover {{ background: {t['gold_deep']}; }}
    QListWidget#TrackList QScrollBar::add-line:vertical,
    QListWidget#TrackList QScrollBar::sub-line:vertical {{ height: 0; }}
    QListWidget#TrackList QScrollBar::add-page:vertical,
    QListWidget#TrackList QScrollBar::sub-page:vertical {{ background: transparent; }}
    QFrame#PlayerBar {{
        background: {t['panel_bg']};
        border-top: 1px solid {t['border']};
    }}
    QLabel#BarTitle {{
        color: {t['gold_light']}; font-size: 13px; font-weight: 600;
    }}
    QLabel#BarArtist {{ color: {t['muted']}; font-size: 12px; }}
    QLabel#BarCover {{
        background: transparent; border-radius: 8px;
        border: 1px solid {t['border']};
    }}
    QPushButton#PlayBtn {{
        background: {t['gold']}; border: 2px solid {t['gold_light']}; border-radius: 28px;
        padding: 0; margin: 0;
    }}
    QPushButton#PlayBtn:hover {{
        background: {t['gold_light']}; border-color: {t['gold']};
    }}
    QPushButton#PlayBtn:pressed {{ background: {t['gold_deep']}; }}
    QPushButton#GoldBtn {{
        background: {t['gold']}; color: {t['window_bg']};
        border: none; border-radius: 8px;
        padding: 8px 16px; font-weight: 600; font-size: 12px;
    }}
    QPushButton#GoldBtn:hover {{ background: {t['gold_light']}; }}
    QPushButton#GhostBtn {{
        background: transparent; color: {t['gold']};
        border: 1px solid {t['border']}; border-radius: 8px;
        padding: 8px 16px; font-size: 12px;
    }}
    QPushButton#GhostBtn:hover {{ background: {t['panel_bg_2']}; border-color: {t['gold']}; }}
    QSlider#SeekSlider::groove:horizontal {{
        height: 4px; background: {t['panel_bg_2']}; border-radius: 2px;
    }}
    QSlider#SeekSlider::sub-page:horizontal {{
        background: {t['gold']}; border-radius: 2px;
    }}
    QSlider#SeekSlider::add-page:horizontal {{
        background: {t['panel_bg_2']}; border-radius: 2px;
    }}
    QSlider#SeekSlider::handle:horizontal {{
        width: 14px; height: 14px; margin: -5px 0;
        background: {t['gold_light']}; border-radius: 7px;
    }}
    QSlider#SeekSlider::handle:horizontal:hover {{ background: {t['gold']}; }}
    QSlider#VolSlider::groove:horizontal {{
        height: 3px; background: {t['panel_bg_2']}; border-radius: 2px;
    }}
    QSlider#VolSlider::sub-page:horizontal {{ background: {t['gold']}; border-radius: 2px; }}
    QSlider#VolSlider::add-page:horizontal {{ background: {t['panel_bg_2']}; border-radius: 2px; }}
    QSlider#VolSlider::handle:horizontal {{
        width: 12px; height: 12px; margin: -4.5px 0;
        background: {t['gold_light']}; border-radius: 6px;
    }}
    QLabel#TimeLabel {{ color: {t['muted']}; font-size: 11px; min-width: 36px; }}
    QLabel#CountLabel {{ color: {t['muted']}; font-size: 12px; }}
    QProgressBar {{
        background: {t['panel_bg_2']}; border: none; border-radius: 2px;
        text-align: center;
    }}
    QProgressBar::chunk {{
        background: {t['gold']}; border-radius: 2px;
    }}
    QLineEdit {{
        background: {t['panel_bg_2']}; color: {t['text']};
        border: 1px solid {t['border']}; border-radius: 6px;
        padding: 6px 10px; font-size: 12px;
    }}
    QLineEdit:focus {{ border-color: {t['gold']}; }}
    QComboBox {{
        background: {t['panel_bg_2']}; color: {t['text']};
        border: 1px solid {t['border']}; border-radius: 6px;
        padding: 6px 10px; font-size: 12px; min-width: 80px;
    }}
    QComboBox:hover {{ border-color: {t['gold']}; }}
    QComboBox::drop-down {{ border: none; width: 24px; }}
    QComboBox::down-arrow {{
        image: none; width: 0; height: 0;
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-top: 5px solid {t['muted']};
        margin-right: 8px;
    }}
    QComboBox QAbstractItemView {{
        background: {t['panel_bg']}; color: {t['text']};
        selection-background-color: {t['active']};
        selection-color: {t['gold_light']};
        border: 1px solid {t['border']}; border-radius: 4px; outline: 0;
    }}
    QMenu {{
        background: {t['panel_bg']}; color: {t['text']};
        border: 1px solid {t['border']}; border-radius: 6px; padding: 6px;
    }}
    QMenu::item {{ padding: 6px 18px; border-radius: 4px; }}
    QMenu::item:selected {{ background: {t['active']}; color: {t['gold_light']}; }}
    QMenu::separator {{ height: 1px; background: {t['border']}; margin: 4px 8px; }}
    QToolTip {{
        background: {t['panel_bg_2']}; color: {t['text']};
        border: 1px solid {t['border']}; border-radius: 4px;
        padding: 4px 8px; font-size: 11px;
    }}
    """


# ============================================================================
# Helpers
# ============================================================================
def fmt_time(ms: int) -> str:
    if ms <= 0:
        return "0:00"
    s = ms // 1000
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def scan_folder(folder: str) -> list:
    folder_path = Path(folder)
    if not folder_path.is_dir():
        return []
    found = []
    for p in folder_path.rglob("*"):
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
            found.append(str(p))
    found.sort(key=lambda x: os.path.basename(x).lower())
    return found


# ============================================================================
# Safe JSON config loading
# ============================================================================
MAX_CONFIG_BYTES = 4 * 1024 * 1024  # 4 MiB — configs/caches are far smaller
MAX_CACHE_ENTRIES = 100_000


def load_json_file(path, what: str):
    """Load a JSON file with size cap and type validation.

    Returns (data, True) on success, (None, False) on any problem.
    `what` is used for the log line ("config" / "tag cache").
    """
    try:
        p = Path(path)
        if not p.is_file():
            return None, False
        if p.stat().st_size > MAX_CONFIG_BYTES:
            print(f"{what} file too large, ignoring: {p}")
            return None, False
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            print(f"Malformed {what} file (not an object), ignoring: {p}")
            return None, False
        return data, True
    except (json.JSONDecodeError, OSError, ValueError) as e:
        print(f"Failed to read {what} file ({e}), using defaults.")
        return None, False


def load_tag_cache_file(path) -> dict:
    """Load tag_cache.json strictly: dict[path] -> [title, artist] strings.

    Rejects oversized files/entries so a corrupted or tampered cache can't
    bloat memory at startup.
    """
    data, ok = load_json_file(path, "tag cache")
    if not ok:
        return {}
    cache = {}
    for k, v in data.items():
        if len(cache) >= MAX_CACHE_ENTRIES:
            print("Tag cache truncated (too many entries).")
            break
        if not isinstance(k, str) or not isinstance(v, list) or len(v) != 2:
            continue
        title, artist = v
        if isinstance(title, str) and isinstance(artist, str):
            cache[k] = (title, artist)
    return cache


def path_within(child: str, parent: str) -> bool:
    """True if `child` is inside directory `parent` (path-boundary safe).

    Uses realpaths so symlinked folders can't smuggle tracks in from outside,
    and compares path components — no 'C:\\MusicX' matching 'C:\\Music' prefix bug.
    """
    try:
        c = Path(child).resolve()
        p = Path(parent).resolve()
        return c == p or p in c.parents
    except (OSError, ValueError):
        return False


def extract_title_artist(filepath: str) -> tuple:
    """Extract (title, artist) from filepath.
    Tries mutagen tags first; falls back to parsing 'Artist - Title' from filename.
    Handles encoding issues and cleans up common metadata pollution.
    """
    filename = os.path.splitext(os.path.basename(filepath))[0]
    title = ""
    artist = ""
    try:
        from mutagen import File as MutagenFile
        m = MutagenFile(filepath)
        if m is not None:
            t = None
            a = None
            for key in ("title", "TITLE", "Title"):
                if key in m:
                    t = m[key]; break
            for key in ("artist", "ARTIST", "Artist"):
                if key in m:
                    a = m[key]; break
            if t is None and hasattr(m, 'tags') and m.tags is not None:
                for k in m.tags:
                    if k.startswith("TIT2"):
                        t = m.tags[k]; break
            if a is None and hasattr(m, 'tags') and m.tags is not None:
                for k in m.tags:
                    if k.startswith("TPE1"):
                        a = m.tags[k]; break
            if t:
                title = str(t[0]) if isinstance(t, list) else str(t)
                title = title.strip()
            if a:
                artist = str(a[0]) if isinstance(a, list) else str(a)
                artist = artist.strip()
    except Exception:
        pass

    # Clean up common metadata pollution (website URLs, brackets, etc.)
    def clean(s):
        if not s:
            return s
        s = re.sub(r'\[?wWw\..*?\]?', '', s)
        s = re.sub(r'\[?Download1Music\.IR\]?', '', s, flags=re.IGNORECASE)
        s = re.sub(r'\[?DibaMusics\.Com\]?', '', s, flags=re.IGNORECASE)
        s = re.sub(r'\[?SevilMusic\.Com\]?', '', s, flags=re.IGNORECASE)
        s = re.sub(r'\[?TakTaraneh\.Com\]?', '', s, flags=re.IGNORECASE)
        s = re.sub(r'~\[.*?\]~', '', s)
        s = re.sub(r'\(\s*.*?\.ir\s*\)', '', s, flags=re.IGNORECASE)
        s = re.sub(r'\(\s*.*?\.com\s*\)', '', s, flags=re.IGNORECASE)
        s = re.sub(r'^\[|\]$', '', s).strip()
        s = re.sub(r'\s+', ' ', s).strip()
        return s

    title = clean(title)
    artist = clean(artist)

    # If we still don't have title, fall back to filename
    if not title:
        stripped = re.sub(r'^\d+\s*[-\.]\s*', '', filename)
        if ' - ' in stripped:
            parts = stripped.split(' - ', 1)
            if not artist:
                artist = parts[0].strip()
            title = parts[1].strip()
        elif ' — ' in stripped:
            parts = stripped.split(' — ', 1)
            if not artist:
                artist = parts[0].strip()
            title = parts[1].strip()
        else:
            title = stripped

    if not artist:
        artist = "Unknown Artist"

    return title, artist
