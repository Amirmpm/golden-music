"""
Golden Music v3.0 — split out of the former main.pyw monolith.
"""
import sys
import os

if sys.platform == 'win32':
    os.environ.setdefault('QT_MEDIA_BACKEND', 'windowsmediafoundation')

import json
import logging
import math
import random
import subprocess
import time
from pathlib import Path
from enum import Enum

from PyQt6.QtCore import (
    Qt, QSize, QRect, QRectF, QPointF, QTimer, QEvent, pyqtSignal, QUrl,
    QObject, QThread, QPropertyAnimation, QVariantAnimation, QPoint,
    QEasingCurve
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton,
    QVBoxLayout, QHBoxLayout, QGridLayout, QFrame, QSizePolicy,
    QSlider, QListWidget, QListWidgetItem, QStackedWidget,
    QSystemTrayIcon, QMenu, QFileDialog, QMessageBox,
    QStyle, QStyleFactory, QAbstractItemView,
    QToolButton, QButtonGroup, QProgressBar, QLineEdit, QComboBox,
    QSplitter, QTreeWidget, QTreeWidgetItem, QTextEdit, QPlainTextEdit,
    QGraphicsOpacityEffect
)
from PyQt6.QtGui import (
    QIcon, QPixmap, QPainter, QColor, QFont, QPalette, QAction,
    QLinearGradient, QBrush, QRadialGradient, QFontDatabase,
    QMouseEvent, QKeyEvent, QPaintEvent, QPen, QPainterPath,
    QShortcut, QKeySequence, QFontMetrics
)
from PyQt6.QtWidgets import QStyledItemDelegate

from config import (
    APP_NAME, APP_VERSION, ORG_NAME, AUDIO_EXTS, config_path,
    Theme, build_qss, fmt_time, scan_folder, extract_title_artist,
    HAVE_QT_AUDIO, load_json_file, load_tag_cache_file,
    restrict_file_permissions, path_within
)
from icons import Icon, render_icon, make_icon, get_dpr

log = logging.getLogger("app")


def _scratch_prefixes():
    """OS temp dirs whose paths must never be persisted to user config."""
    out = set()
    try:
        import tempfile
        out.add(os.path.normcase(os.path.abspath(tempfile.gettempdir())))
    except Exception:
        pass
    # OS scratch roots listed literally so we can REJECT them during config
    # persistence (never-wipe guard) — they are never used as storage here.
    for cand in (r"C:\Windows\Temp", "/tmp", "/var/tmp"):  # nosec B108 — exclusion list, never a write target
        try:
            out.add(os.path.normcase(os.path.abspath(cand)))
        except Exception:
            pass
    return {x for x in out if x}


_SCRATCH_PREFIXES = _scratch_prefixes()


def _is_scratch_path(p: str) -> bool:
    """True for Temp/sandbox paths (test-suite mkdtemp dirs, OS temp).

    Dev/test scripts build fake libraries under mkdtemp dirs; those paths
    must never be written into the real user config — see scripts/testenv.py
    (which additionally isolates every test run into a sandbox HOME).
    """
    try:
        ap = os.path.normcase(os.path.abspath(str(p)))
    except Exception:
        return True
    return any(ap == pre or ap.startswith(pre + os.sep)
               for pre in _SCRATCH_PREFIXES)


class RepeatMode(Enum):
    OFF = 0
    ALL = 1
    ONE = 2


class View(Enum):
    LIBRARY = "library"
    FAVORITES = "favorites"


class SortMode(Enum):
    TITLE = "title"
    ARTIST = "artist"
    DATE_ADDED = "date_added"
    FILENAME = "filename"


def _track_item_text(title: str, artist: str) -> str:
    """Pack a track's title/artist for TrackRowDelegate display."""
    return f"{title}|{artist}"


def _list_subdirs(dir_path: str) -> list:
    """Sorted visible subdirectories of dir_path ([] on any error)."""
    try:
        return sorted(d for d in os.listdir(dir_path)
                      if os.path.isdir(os.path.join(dir_path, d))
                      and not d.startswith("."))
    except OSError:
        return []


def _dir_has_subdirs(dir_path: str) -> bool:
    """True if dir_path contains at least one visible subdirectory."""
    try:
        for d in os.listdir(dir_path):
            if d.startswith("."):
                continue
            if os.path.isdir(os.path.join(dir_path, d)):
                return True
    except OSError:
        pass
    return False


def _rgba(hex_color: str) -> str:
    """'#rrggbb' -> 'r,g,b' (for QSS rgba())."""
    try:
        c = QColor(hex_color)
        return f"{c.red()},{c.green()},{c.blue()}"
    except Exception:
        return "36,29,23"

