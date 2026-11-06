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

import coverart
from gm.core import extract_title_artist, log


class CoverLoaderThread(QThread):
    cover_ready = pyqtSignal(str, object)

    def __init__(self, filepath: str, parent=None):
        super().__init__(parent)
        self.filepath = filepath

    def run(self):
        try:
            data = coverart.get_cover_bytes(self.filepath)
        except Exception:
            data = None
        self.cover_ready.emit(self.filepath, data)


class TagLoaderThread(QThread):
    """Loads title/artist for a batch of file paths in background.
    Emits (path, title, artist) for each track as it's processed.
    """
    tag_loaded = pyqtSignal(str, str, str)

    def __init__(self, paths: list, parent=None):
        super().__init__(parent)
        self.paths = list(paths)
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        for path in self.paths:
            if self._cancel:
                break
            try:
                title, artist = extract_title_artist(path)
                self.tag_loaded.emit(path, title, artist)
            except Exception:
                # Fallback to filename
                fname = os.path.splitext(os.path.basename(path))[0]
                self.tag_loaded.emit(path, fname, "Unknown Artist")


class AlbumsWorkerThread(QThread):
    """Resolves the album tag for untagged tracks (mutagen, slow) and picks
    one cover per group. Emits one album group at a time so the grid fills
    progressively instead of freezing the UI."""
    album_group_ready = pyqtSignal(str, list, object)

    def __init__(self, untagged: list, parent=None):
        super().__init__(parent)
        self.untagged = list(untagged)
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        from mutagen import File as MutagenFile
        import coverart as _ca

        def _first_tag(val):
            if isinstance(val, (list, tuple)):
                return str(val[0]) if val else ""
            return str(val) if val else ""

        groups = {}
        order = []
        for p in self.untagged:
            if self._cancel:
                return
            album = ""
            try:
                m = MutagenFile(p, easy=True)
                if m is not None and m.tags:
                    album = _first_tag(m.tags.get("album"))
            except Exception:
                pass
            key = album if album else "Unknown Album"
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(p)
        # Emit one group per signal — cover picked here (bytes cross the
        # thread boundary, QPixmap never does).
        for name in order:
            if self._cancel:
                return
            paths = groups[name]
            cover_data = None
            for p in paths[:4]:
                try:
                    data = _ca.get_cover_bytes(p)
                except Exception:
                    data = None
                if data:
                    cover_data = data
                    break
            self.album_group_ready.emit(name, list(paths), cover_data)

