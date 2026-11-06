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

from gm.core import log


class TrackRowDelegate(QStyledItemDelegate):
    """Modern two-line track row:
       line 1:  01   Bohemian Rhapsody
       line 2:       Queen
    The item text is "title|artist" (artist may be empty → filename only).
    """

    TITLE_ROLE = Qt.ItemDataRole.UserRole + 1

    def __init__(self, theme: dict, parent=None):
        super().__init__(parent)
        self.theme = theme

    def set_theme(self, theme: dict):
        self.theme = theme

    def sizeHint(self, option, index):
        return QSize(0, 46)

    def paint(self, painter: QPainter, option, index):
        raw = index.data(Qt.ItemDataRole.DisplayRole) or ""
        if "|" in raw:
            title, artist = raw.split("|", 1)
        else:  # plain-text fallback (e.g. transient states)
            title, artist = raw, ""
        number = index.data(self.TITLE_ROLE) or ""
        playing = bool(index.data(Qt.ItemDataRole.UserRole + 2))

        t = self.theme or {}
        # Fall back to the brand theme when no palette has been assigned —
        # tests (and any pre-theme painting) may construct the delegate with
        # an empty dict; a paint must never raise for missing theme keys.
        if not t:
            from config import Theme as _Theme
            t = _Theme.get("royal_gold")
            self.theme = t
        rect = option.rect.adjusted(6, 3, -6, -3)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)

        p = painter
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Row background
        radius = 10
        if selected:
            bg = QColor(t["active"])
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(rect, radius, radius)
        elif hovered:
            hover = QColor(t["panel_bg_2"])
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(hover)
            p.drawRoundedRect(rect, radius, radius)

        # Playing rows get a slim accent bar on the left edge — a clearer
        # "now playing" cue than color alone (works in all 12 themes).
        if playing:
            bar_w = 3
            bar_rect = QRect(rect.left() + 1, rect.top() + 8,
                             bar_w, rect.height() - 16)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t["gold"]))
            p.drawRoundedRect(bar_rect, bar_w / 2, bar_w / 2)
            # shift content right so text never overlaps the bar
            rect = rect.adjusted(bar_w + 4, 0, 0, 0)

        # Colors — playing/selected rows glow gold; others stay neutral
        if playing or selected:
            num_col = QColor(t["gold"])
            title_col = QColor(t["gold_light"])
        else:
            num_col = QColor(t["muted"])
            title_col = QColor(t["text"])

        x_num = rect.left() + 8
        x_text = x_num + (34 if number else 0)

        # Base font — clone the option font and clamp every derived size to a
        # sane positive minimum. (An earlier version called
        # base.resolve(QFont().resolve()) here; PyQt6 6.11's resolve() needs an
        # explicit argument, so that line raised TypeError on every paint and
        # left every row's text undrawn — titles "invisible" in all themes.)
        base = QFont(option.font)
        base_size = base.pointSizeF()
        if base_size <= 0:
            base_size = 9.0
            base.setPointSizeF(base_size)

        # Index number (small, muted/gold)
        if number:
            f_num = QFont(base)
            f_num.setPointSizeF(max(8.5, base_size - 1.5))
            p.setFont(f_num)
            p.setPen(QPen(num_col))
            p.drawText(QRect(x_num, rect.top(), 30, rect.height()),
                       int(Qt.AlignmentFlag.AlignVCenter), str(number))

        # Title line
        f_title = QFont(base)
        f_title.setBold(True)
        f_title.setPointSizeF(max(8.5, base_size + 0.5))
        p.setFont(f_title)
        p.setPen(QPen(title_col))
        fm = QFontMetrics(f_title)
        title_rect = QRect(x_text, rect.top() + 4,
                           rect.right() - x_text, fm.height())
        p.drawText(title_rect, int(Qt.AlignmentFlag.AlignVCenter),
                   fm.elidedText(title, Qt.TextElideMode.ElideRight, title_rect.width()))

        # Artist line (muted, smaller) — only when present
        if artist:
            f_artist = QFont(base)
            f_artist.setPointSizeF(max(8.5, base_size - 2))
            p.setFont(f_artist)
            p.setPen(QPen(QColor(t["muted"])))
            fm_a = QFontMetrics(f_artist)
            art_rect = QRect(x_text, rect.bottom() - fm_a.height() - 4,
                             rect.right() - x_text, fm_a.height())
            p.drawText(art_rect, int(Qt.AlignmentFlag.AlignVCenter),
                       fm_a.elidedText(artist, Qt.TextElideMode.ElideRight, art_rect.width()))
        p.restore()

