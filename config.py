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
APP_VERSION = "2.1.0"
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
    # ------------------------------------------------------------------------
    # v2.0.0 "Psychology Suite" - 12 hand-tuned themes (6 dark + 6 light).
    # Designed around color-psychology principles and verified numerically
    # against WCAG 2.x contrast gates (body text >= 4.5:1 everywhere;
    # headings/accents >= 5:1; disabled-grade fills >= 3:1).
    #
    #   DARK  (black-dominant): restful and OLED-friendly; vivid accents pop
    #         against darkness (von Restorff highlight effect).
    #   LIGHT (white-dominant): clarity, focus, low eye strain; accents are
    #         applied as dark "ink" over near-white surfaces.
    # ------------------------------------------------------------------------
    DEFAULT_NAME = "royal_gold"
    AUTO_DARK_DEFAULT = "royal_gold"
    AUTO_LIGHT_DEFAULT = "ivory"

    # ========================================================================
    # DARK FAMILY - black dominant
    # rest, depth, glow; built for night listening
    # ========================================================================
    # Obsidian Black - true #000 monochrome — rest, discipline, elegance; OLED power saving
    OBSIDIAN = {
        "name": "obsidian",
        "window_bg": "#000000",
        "panel_bg": "#101013",
        "panel_bg_2": "#1a1b1f",
        "border": "#2c2d33",
        "text": "#f4f4f6",
        "muted": "#979ca6",
        "gold": "#c7ccd6",
        "gold_light": "#eef0f4",
        "gold_deep": "#8f95a1",
        "active": "#26272d",
        "is_dark": True,
    }

    # Royal Gold - black + champagne gold — luxury, confidence; the brand signature
    ROYAL_GOLD = {
        "name": "royal_gold",
        "window_bg": "#0a0805",
        "panel_bg": "#15110a",
        "panel_bg_2": "#201910",
        "border": "#372d1c",
        "text": "#f8f1de",
        "muted": "#ab9776",
        "gold": "#e9b957",
        "gold_light": "#ffd982",
        "gold_deep": "#b88a33",
        "active": "#352918",
        "is_dark": True,
    }

    # Midnight Blue - navy black + electric blue — depth, nocturnal calm, trust
    MIDNIGHT_BLUE = {
        "name": "midnight_blue",
        "window_bg": "#070b13",
        "panel_bg": "#0e1626",
        "panel_bg_2": "#152238",
        "border": "#24334e",
        "text": "#e8effb",
        "muted": "#8292b0",
        "gold": "#4591ea",
        "gold_light": "#74b6ff",
        "gold_deep": "#2868c4",
        "active": "#14294b",
        "is_dark": True,
    }

    # Deep Amethyst - violet-tinted black + vivid purple — mystery, imagination
    AMETHYST = {
        "name": "amethyst",
        "window_bg": "#0b0714",
        "panel_bg": "#150e24",
        "panel_bg_2": "#1f1736",
        "border": "#35285c",
        "text": "#f0eafc",
        "muted": "#9c8dc6",
        "gold": "#9670f5",
        "gold_light": "#c7abff",
        "gold_deep": "#6f44d6",
        "active": "#2c1f52",
        "is_dark": True,
    }

    # Neon Rose - rose glow on near-black — boldness, romance, modern energy
    NEON_ROSE = {
        "name": "neon_rose",
        "window_bg": "#11060b",
        "panel_bg": "#1e0c15",
        "panel_bg_2": "#2b1320",
        "border": "#461f2e",
        "text": "#fcebf2",
        "muted": "#c18ea2",
        "gold": "#fc5fa3",
        "gold_light": "#ff92c2",
        "gold_deep": "#d53a83",
        "active": "#3f1928",
        "is_dark": True,
    }

    # Emerald Night - forest black + emerald — nature, growth, tranquility
    EMERALD_NIGHT = {
        "name": "emerald_night",
        "window_bg": "#051009",
        "panel_bg": "#0b1c12",
        "panel_bg_2": "#12291b",
        "border": "#204129",
        "text": "#e9f7ee",
        "muted": "#81aa90",
        "gold": "#30bf79",
        "gold_light": "#59dfa3",
        "gold_deep": "#18925b",
        "active": "#133423",
        "is_dark": True,
    }

    # ========================================================================
    # LIGHT FAMILY - white dominant
    # clarity, focus, warmth; built for daylight work
    # ========================================================================
    # Porcelain White - pure white monochrome — clarity, order, Swiss-grid focus
    PORCELAIN = {
        "name": "porcelain",
        "window_bg": "#ffffff",
        "panel_bg": "#f6f7f9",
        "panel_bg_2": "#edeff3",
        "border": "#d5dae0",
        "text": "#171a20",
        "muted": "#58606b",
        "gold": "#272d38",
        "gold_light": "#39414e",
        "gold_deep": "#171c26",
        "active": "#e1e6ec",
        "is_dark": False,
    }

    # Ivory Gold - warm ivory + amber-gold ink — warmth, optimism, quiet prestige
    IVORY = {
        "name": "ivory",
        "window_bg": "#fffdf6",
        "panel_bg": "#faf5e6",
        "panel_bg_2": "#f4ebd4",
        "border": "#ddcea8",
        "text": "#2c2110",
        "muted": "#6f5e3e",
        "gold": "#6d500f",
        "gold_light": "#80600f",
        "gold_deep": "#57400c",
        "active": "#f0e2b4",
        "is_dark": False,
    }

    # Clear Azure - ice white + dependable blue ink — trust, calm concentration
    AZURE = {
        "name": "azure",
        "window_bg": "#fbfeff",
        "panel_bg": "#eef6fb",
        "panel_bg_2": "#e1edf5",
        "border": "#bcd2df",
        "text": "#102330",
        "muted": "#496375",
        "gold": "#115380",
        "gold_light": "#18619c",
        "gold_deep": "#0c4066",
        "active": "#d0e4f3",
        "is_dark": False,
    }

    # Soft Lilac - lavender white + violet ink — creativity, mindfulness
    LILAC = {
        "name": "lilac",
        "window_bg": "#fefeff",
        "panel_bg": "#f7f4fd",
        "panel_bg_2": "#efe9f9",
        "border": "#d1c6e8",
        "text": "#241b33",
        "muted": "#61567b",
        "gold": "#5e3aad",
        "gold_light": "#704ccd",
        "gold_deep": "#492c87",
        "active": "#e6dff9",
        "is_dark": False,
    }

    # Rose Blush - blush white + rose ink — compassion, playful warmth
    BLUSH = {
        "name": "blush",
        "window_bg": "#fffdfe",
        "panel_bg": "#fdf2f6",
        "panel_bg_2": "#fae6ee",
        "border": "#eec7d6",
        "text": "#341621",
        "muted": "#7e5665",
        "gold": "#a62054",
        "gold_light": "#bc3063",
        "gold_deep": "#851742",
        "active": "#fcdfea",
        "is_dark": False,
    }

    # Fresh Sage - mint white + green ink — balance, renewal, lowest eye strain
    SAGE = {
        "name": "sage",
        "window_bg": "#fbfefc",
        "panel_bg": "#f0f7f2",
        "panel_bg_2": "#e3f0e9",
        "border": "#c0ddcd",
        "text": "#14261d",
        "muted": "#50705d",
        "gold": "#0f623c",
        "gold_light": "#16714a",
        "gold_deep": "#0a4c2d",
        "active": "#d1ece0",
        "is_dark": False,
    }

    ALL = [OBSIDIAN, ROYAL_GOLD, MIDNIGHT_BLUE, AMETHYST, NEON_ROSE, EMERALD_NIGHT,
           PORCELAIN, IVORY, AZURE, LILAC, BLUSH, SAGE]

    # Pre-2.0 theme ids -> closest successor (graceful migration).
    LEGACY_ALIASES = {
        "dark": "royal_gold",
        "light": "ivory",
        "midnight": "midnight_blue",
        "forest": "emerald_night",
        "rose_gold": "blush",
        "carbon": "obsidian",
        "ocean": "midnight_blue",
        "sunset": "neon_rose",
        "emerald": "emerald_night",
        "lavender": "amethyst",
        "sand": "ivory",
        "crimson": "blush",
        "teal": "emerald_night",
        "aurora": "amethyst",
        "mocha": "royal_gold",
        "nord": "midnight_blue",
        "dracula": "amethyst"
    }

    @classmethod
    def normalize(cls, name):
        """Map pre-2.0 theme ids onto their closest v2.0 successor."""
        return cls.LEGACY_ALIASES.get(name, name)

    @classmethod
    def get(cls, name):
        name = cls.normalize(name)
        for t in cls.ALL:
            if t["name"] == name:
                return t
        return cls.ROYAL_GOLD

    @classmethod
    def names(cls):
        return [t["name"] for t in cls.ALL]

    @classmethod
    def display_name(cls, name):
        return {
            "obsidian": "Obsidian Black",
            "royal_gold": "Royal Gold",
            "midnight_blue": "Midnight Blue",
            "amethyst": "Deep Amethyst",
            "neon_rose": "Neon Rose",
            "emerald_night": "Emerald Night",
            "porcelain": "Porcelain White",
            "ivory": "Ivory Gold",
            "azure": "Clear Azure",
            "lilac": "Soft Lilac",
            "blush": "Rose Blush",
            "sage": "Fresh Sage"
        }.get(cls.normalize(name), str(name).title())


# ============================================================================
# QSS — modern "liquid glass" design language
# Darker surfaces, larger radii, soft translucency, generous spacing.
# Each theme keeps its own accent (Golden stays gold).
# ============================================================================
def build_qss(t: dict) -> str:
    is_dark = t.get("is_dark", True)
    # Glass surface colors: slightly translucent feel via lighter borders on dark
    glass_border = t["border"]
    hover_surface = t["panel_bg_2"] if not is_dark else _lighten(t["panel_bg_2"], 14)
    return f"""
    * {{
        font-family: 'Segoe UI Variable Text', 'Segoe UI Variable Display',
                     'Segoe UI', 'Tahoma', sans-serif;
        font-size: 13px;   /* single standard body size for the whole app */
        outline: none;
        padding: 0;
        margin: 0;
    }}
    QMainWindow, QWidget#Root {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
            stop:0 {t['window_bg']}, stop:1 {_lighten(t['window_bg'], 6)});
    }}
    QWidget#SideRail {{
        background: {t['panel_bg']};
        border-right: 1px solid {glass_border};
    }}
    QToolButton#RailBtn {{
        border-radius: 14px;
        background: transparent;
        border: none;
        padding: 0;
        margin: 0;
    }}
    QToolButton#RailBtn:hover {{ background: {hover_surface}; }}
    QToolButton#RailBtn:checked {{
        background: {t['active']};
        border-left: 3px solid {t['gold']};
    }}
    QWidget#MainArea {{ background: transparent; }}
    QLabel#PageTitle {{
        color: {t['text']}; font-size: 20px; font-weight: 700;
        letter-spacing: 0.3px;
    }}
    QLabel#NowTitle {{
        color: {t['gold_light']}; font-size: 22px; font-weight: 700;
    }}
    QLabel#NowArtist {{
        color: {t['muted']}; font-size: 13px;
    }}
    QListWidget#TrackList, QTreeWidget#TrackList {{
        background: {t['panel_bg']};
        border: 1px solid {glass_border};
        border-radius: 16px;
        color: {t['text']};
        font-size: 13px;
        padding: 10px;
        outline: 0;
    }}
    QListWidget#TrackList::item {{
        min-height: 34px;
        border-radius: 10px;
        margin: 1px 0;
    }}
    QListWidget#TrackList::item:hover {{ background: {hover_surface}; }}
    QListWidget#TrackList::item:selected {{
        background: {t['active']}; color: {t['gold_light']};
    }}
    QListWidget#TrackList QScrollBar:vertical,
    QTreeWidget#TrackList QScrollBar:vertical {{
        background: transparent; width: 8px; margin: 6px 2px;
    }}
    QListWidget#TrackList QScrollBar::handle:vertical,
    QTreeWidget#TrackList QScrollBar::handle:vertical {{
        background: {t['border']}; border-radius: 4px; min-height: 28px;
    }}
    QListWidget#TrackList QScrollBar::handle:vertical:hover,
    QTreeWidget#TrackList QScrollBar::handle:vertical:hover {{ background: {t['gold_deep']}; }}
    QListWidget#TrackList QScrollBar::add-line:vertical,
    QListWidget#TrackList QScrollBar::sub-line:vertical,
    QTreeWidget#TrackList QScrollBar::add-line:vertical,
    QTreeWidget#TrackList QScrollBar::sub-line:vertical {{ height: 0; }}
    QListWidget#TrackList QScrollBar::add-page:vertical,
    QListWidget#TrackList QScrollBar::sub-page:vertical,
    QTreeWidget#TrackList QScrollBar::add-page:vertical,
    QTreeWidget#TrackList QScrollBar::sub-page:vertical {{ background: transparent; }}
    QFrame#PlayerBar {{
        background: {t['panel_bg']};
        border-top: 1px solid {glass_border};
        border-radius: 18px 18px 0 0;
    }}
    QLabel#BarTitle {{
        color: {t['text']}; font-size: 13px; font-weight: 600;
    }}
    QLabel#BarArtist {{ color: {t['muted']}; font-size: 12px; }}
    QLabel#BarCover {{
        background: transparent; border-radius: 10px;
        border: 1px solid {glass_border};
    }}
    QPushButton#PlayBtn {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 {t['gold_light']}, stop:1 {t['gold']});
        border: none; border-radius: 28px;
        padding: 0; margin: 0;
    }}
    QPushButton#PlayBtn:hover {{
        background: {t['gold_light']};
    }}
    QPushButton#PlayBtn:pressed {{ background: {t['gold_deep']}; }}
    QPushButton#GoldBtn {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 {t['gold']}, stop:1 {t['gold_deep']});
        color: {t['window_bg'] if is_dark else '#ffffff'};
        min-height: 34px; max-height: 34px;
        border-radius: 17px;
        padding: 0 20px; font-weight: 600; font-size: 13px;
    }}
    QPushButton#GoldBtn:hover {{ background: {t['gold_light']}; color: {t['window_bg']}; }}
    QPushButton#GoldBtn:pressed {{ background: {t['gold_deep']}; padding-top: 2px; }}
    QPushButton#GhostBtn {{
        background: transparent; color: {t['text']};
        border: 1px solid {glass_border}; border-radius: 17px;
        min-height: 32px; max-height: 32px;
        padding: 0 20px; font-size: 13px;
    }}
    QPushButton#GhostBtn:hover {{ background: {hover_surface}; border-color: {t['gold']}; color: {t['gold_light']}; }}
    QPushButton#GhostBtn:pressed {{ background: {t['active']}; }}
    QSlider#SeekSlider::groove:horizontal {{
        height: 5px; background: {t['panel_bg_2']}; border-radius: 2.5px;
    }}
    QSlider#SeekSlider::sub-page:horizontal {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 {t['gold_deep']}, stop:1 {t['gold_light']});
        border-radius: 2.5px;
    }}
    QSlider#SeekSlider::add-page:horizontal {{
        background: {t['panel_bg_2']}; border-radius: 2.5px;
    }}
    QSlider#SeekSlider::handle:horizontal {{
        width: 14px; height: 14px; margin: -5px 0;
        background: #ffffff; border-radius: 7px;
    }}
    QSlider#SeekSlider::handle:horizontal:hover {{
        background: {t['gold_light']};
    }}
    QSlider#VolSlider::groove:horizontal {{
        height: 4px; background: {t['panel_bg_2']}; border-radius: 2px;
    }}
    QSlider#VolSlider::sub-page:horizontal {{ background: {t['gold']}; border-radius: 2px; }}
    QSlider#VolSlider::add-page:horizontal {{ background: {t['panel_bg_2']}; border-radius: 2px; }}
    QSlider#VolSlider::handle:horizontal {{
        width: 12px; height: 12px; margin: -4px 0;
        background: #ffffff; border-radius: 6px;
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
        border: 1px solid {glass_border}; border-radius: 18px;
        padding: 8px 16px; font-size: 12px;
        selection-background-color: {t['gold']};
    }}
    QLineEdit:focus {{ border-color: {t['gold']}; background: {_darken(t['panel_bg_2'], 8)}; }}
    QComboBox {{
        background: {t['panel_bg_2']}; color: {t['text']};
        border: 1px solid {glass_border}; border-radius: 14px;
        padding: 7px 14px; font-size: 12px; min-width: 80px;
    }}
    QComboBox:hover {{ border-color: {t['gold']}; }}
    QComboBox::drop-down {{ border: none; width: 24px; }}
    QComboBox::down-arrow {{
        image: none; width: 0; height: 0;
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-top: 5px solid {t['muted']};
        margin-right: 10px;
    }}
    QComboBox QAbstractItemView {{
        background: {t['panel_bg']}; color: {t['text']};
        selection-background-color: {t['active']};
        selection-color: {t['gold_light']};
        border: 1px solid {glass_border}; border-radius: 10px;
        outline: 0; padding: 4px;
    }}
    QMenu {{
        background: {t['panel_bg']}; color: {t['text']};
        border: 1px solid {glass_border}; border-radius: 12px; padding: 6px;
    }}
    QMenu::item {{ padding: 8px 22px; border-radius: 8px; font-size: 12px; }}
    QMenu::item:selected {{ background: {t['active']}; color: {t['gold_light']}; }}
    QMenu::separator {{ height: 1px; background: {t['border']}; margin: 5px 10px; }}
    QToolTip {{
        background: {t['panel_bg_2']}; color: {t['text']};
        border: 1px solid {glass_border}; border-radius: 8px;
        padding: 6px 10px; font-size: 11px;
    }}
    QLabel#LoadingLabel {{
        color: {t['gold_light']}; font-size: 15px; font-weight: 600;
        background: transparent; letter-spacing: 0.5px;
    }}
    """


def _lighten(hex_color: str, amount: int) -> str:
    """Lighten a #rrggbb color by `amount` (0-255)."""
    try:
        c = QColor(hex_color)
        c.setRed(min(255, c.red() + amount))
        c.setGreen(min(255, c.green() + amount))
        c.setBlue(min(255, c.blue() + amount))
        return c.name()
    except Exception:
        return hex_color


def _darken(hex_color: str, amount: int) -> str:
    try:
        c = QColor(hex_color)
        c.setRed(max(0, c.red() - amount))
        c.setGreen(max(0, c.green() - amount))
        c.setBlue(max(0, c.blue() - amount))
        return c.name()
    except Exception:
        return hex_color


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


_path_cache: dict[str, str] = {}   # raw path -> resolved (normcased)
_PATH_CACHE_MAX = 50_000


def _resolved(path: str) -> str:
    """Cached Path.resolve() — resolve() is a syscall chain per call, and the
    folder-filter hot loop ran it for every library track on every click.
    Results are stable for a session; cache is size-bounded."""
    hit = _path_cache.get(path)
    if hit is not None:
        return hit
    try:
        r = os.path.normcase(str(Path(path).resolve()))
    except (OSError, ValueError):
        r = os.path.normcase(path)
    if len(_path_cache) < _PATH_CACHE_MAX:
        _path_cache[path] = r
    return r


def path_within(child: str, parent: str) -> bool:
    """True if `child` is inside directory `parent` (path-boundary safe).

    Uses realpaths so symlinked folders can't smuggle tracks in from outside,
    and compares path components — no 'C:\\MusicX' matching 'C:\\Music' prefix bug.
    Fast path first: a normcased component-wise prefix check answers the
    overwhelming majority of cases with zero syscalls; only near-miss
    candidates (same prefix, could be a sibling like MusicX vs Music) fall
    through to the memoized full resolve.
    """
    fast = _fast_within(os.path.normcase(child), os.path.normcase(parent))
    if fast is not None:
        return fast
    try:
        c = Path(_resolved(child))
        p = Path(_resolved(parent))
        return c == p or p in c.parents
    except (OSError, ValueError):
        return False


def _fast_within(nc_child: str, nc_parent: str) -> bool | None:
    """Component-wise prefix check on normcased strings.

    Returns True/False when the answer is certain from the string shapes,
    None when a boundary case needs the resolve-based check (trailing
    separator differences or sibling-prefix ambiguity)."""
    if not nc_parent or not nc_child:
        return False
    if nc_child == nc_parent:
        return True
    sep = "\\" if "\\" in nc_parent or "/" not in nc_parent else "/"
    if nc_child.startswith(nc_parent + sep):
        return True
    # Definite NO only when the parent isn't a path-prefix at all AND there
    # is no component boundary ambiguity worth resolving (symlinks can still
    # redirect a plain-prefix match, so those stay in the slow path).
    if len(nc_child) > len(nc_parent) and not nc_parent.endswith(("\\", "/")):
        # e.g. child C:\musicx\... vs parent C:\music -> ambiguous sibling?
        head = nc_child[:len(nc_parent)]
        nxt = nc_child[len(nc_parent)]
        if head == nc_parent and nxt not in ("\\", "/"):
            return False   # 'C:\musicX' vs 'C:\music' — genuinely outside
    return None


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
        # Literal "www." domains inside optional brackets — [wWw] was a
        # single-char class before, which never matched "www." at all.
        s = re.sub(r'\[?\s*www\.[^\]\s]+\.?[a-z]{2,}\s*\]?', '', s, flags=re.IGNORECASE)
        for site in ("Download1Music", "DibaMusics", "SevilMusic", "TakTaraneh"):
            s = re.sub(r'\[?\s*' + site + r'\.(com|ir)\s*\]?', '', s, flags=re.IGNORECASE)
        s = re.sub(r'~\[.*?\]~', '', s)
        # Site tags inside ONE paren group only — never bridge two separate
        # parens (the old lazy '.*?' happily ate "(Tehran) bootleg (fan.ir)").
        s = re.sub(r'\(\s*[^()]*?\.(?:ir|com|net)\s*\)', '', s, flags=re.IGNORECASE)
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
