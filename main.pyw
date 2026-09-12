"""
Golden Music — Main application window (v5.1, PyQt6, Python 3.14).

v5.1 changes:
  - Use Windows Media Foundation backend for better audio quality
  - Tray left-click toggles show/hide
  - Tray right-click: Add to Favorites instead of Show/Hide
  - Refresh button for rescanning folders
  - Sidebar always collapsed (no hover-expand), tooltips instead
  - Custom logo used everywhere
  - Improved metadata reading with better fallback
"""
import sys
import os

# CRITICAL: Set audio backend to Windows Media Foundation for better quality
# This must be set BEFORE any Qt imports
# On Windows, WMF provides better MP3 decoding quality than FFmpeg
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

from applog import setup_logging

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
    QSplitter, QTreeWidget, QTreeWidgetItem
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
from audio import AudioBackend
from scanner import FolderScanner
from settings_dialog import SettingsDialog
from mini_player import MiniPlayer
from widgets import ClickableSlider
from tray_menu import TrayMenuWidget
from theme_picker import ThemePickerPopup
import coverart
from mediakeys import MediaKeyFilter

try:
    from autottheme import windows_prefers_dark as _os_prefers_dark
except ImportError:
    def _os_prefers_dark():
        return True

log = logging.getLogger("app")


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


# ============================================================================
# MarqueeLabel — label that scrolls its text horizontally when it overflows
# ============================================================================
class MarqueeLabel(QLabel):
    """A QLabel that gently scrolls overflowing text (marquee) so even very
    long track titles become fully readable. Static when the text fits.

    The scroll pauses at the start, glides to the end, pauses, and glides
    back — far less jittery than a continuous loop.
    """

    _SCROLL_PX_PER_TICK = 1        # pixels per 40 ms tick ≈ 25 px/s
    _EDGE_PAUSE_TICKS = 40         # pause (≈1.6 s) at each end of the text
    _TICK_MS = 40

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._full_text = text
        self._offset = 0            # pixels scrolled into the text
        self._dir = 1               # +1 gliding left-ward, -1 coming back
        self._pause_left = 0        # countdown of pause ticks
        self._timer = QTimer(self)
        self._timer.setInterval(self._TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._needs_scroll = False
        self._fm = QFontMetrics(self.font())

    def setText(self, text: str):   # noqa: N802 (Qt naming)
        self._full_text = text
        self._offset = 0
        self._dir = 1
        self._pause_left = self._EDGE_PAUSE_TICKS
        super().setText(text)
        self._fm = QFontMetrics(self.font())
        self._update_scroll_state()

    def text(self) -> str:          # noqa: N802 (Qt naming)
        return self._full_text

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._update_scroll_state()

    def _update_scroll_state(self):
        text_w = self._fm.horizontalAdvance(self._full_text)
        avail = max(0, self.width() - 8)   # small inner margin
        self._needs_scroll = text_w > avail and avail > 0
        if not self._needs_scroll:
            self._timer.stop()
            self._offset = 0
            if self._full_text:
                super().setText(self._full_text)
        elif not self._timer.isActive():
            self._timer.start()

    def _tick(self):
        if not self.isVisible() or not self._needs_scroll:
            return
        text_w = self._fm.horizontalAdvance(self._full_text)
        avail = max(0, self.width() - 8)
        max_offset = max(0, text_w - avail)
        if max_offset == 0:
            self._timer.stop()
            return
        if self._pause_left > 0:
            self._pause_left -= 1
            return
        self._offset += self._SCROLL_PX_PER_TICK * self._dir
        if self._offset >= max_offset:
            self._offset = max_offset
            self._dir = -1
            self._pause_left = self._EDGE_PAUSE_TICKS
        elif self._offset <= 0:
            self._offset = 0
            self._dir = 1
            self._pause_left = self._EDGE_PAUSE_TICKS
        elided_src = self._full_text
        # Scroll by prefixing spaces equal to pixels consumed is imprecise;
        # instead clip via rich-text padding: draw from offset using QTextEdit
        # is heavy — the clean way is a custom paint, so do that:
        self.update()   # trigger paintEvent, which applies the offset

    def paintEvent(self, e):
        if not self._needs_scroll:
            super().paintEvent(e)
            return
        try:
            p = QPainter(self)
            flags = int(Qt.AlignmentFlag.AlignVCenter) | int(Qt.TextFlag.TextSingleLine)
            rect = self.rect().adjusted(2, 0, -2, 0)
            p.setClipRect(rect)
            p.drawText(rect.translated(-self._offset, 0), flags, self._full_text)
            # A soft fade-out on the trailing edge so the scrolling text
            # doesn't pop in at the boundary.
            grad = QLinearGradient(rect.right() - 16, 0, rect.right(), 0)
            grad.setColorAt(0.0, QColor(0, 0, 0, 0))
            grad.setColorAt(1.0, QColor(self.palette().color(self.backgroundRole())))
            p.fillRect(rect.right() - 16, 0, 16, self.height(), QBrush(grad))
            grad2 = QLinearGradient(rect.left(), 0, rect.left() + 16, 0)
            grad2.setColorAt(0.0, QColor(self.palette().color(self.backgroundRole())))
            grad2.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.fillRect(rect.left(), 0, 16, self.height(), QBrush(grad2))
            p.end()
        except Exception as ex:
            log.warning(f"MarqueeLabel paint error: {ex}")
            super().paintEvent(e)


# ============================================================================
# IconButton
# ============================================================================
class IconButton(QPushButton):
    """Round glass icon button — circular paint area with a smooth hover
    "breath" animation (icon gently grows on hover, shrinks back on leave)."""

    def __init__(self, svg_string: str, icon_size: int = 22, button_size: int = 36, parent=None):
        super().__init__(parent)
        self.svg_string = svg_string
        self.icon_size_base = icon_size
        self.icon_size = icon_size
        self.button_size = button_size
        self.setFixedSize(button_size, button_size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._theme = None
        self._hover = False
        # Smooth hover "breath": QVariantAnimation drives the icon size
        # without needing a custom Qt property (safer with PyQt6).
        self._breath_anim = QVariantAnimation(self)
        self._breath_anim.setDuration(120)
        self._breath_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._breath_anim.valueChanged.connect(self._on_breath_value)
        super().setIconSize(QSize(icon_size, icon_size))
        self._refresh_style()

    def _on_breath_value(self, v):
        px = max(6, round(float(v)))
        if px != self.icon_size:
            self.icon_size = px
            # Re-render the SVG at the animated size so the glyph itself
            # scales — QIcon.pixmap(upscale) blurs and, worse, the cached
            # pixmap's devicePixelRatio made the drawn glyph land off-center
            # (the "circle grows to one side" artifact).
            self.update_icon()
            self.update()

    def _animate_icon_to(self, target: int):
        self._breath_anim.stop()
        self._breath_anim.setStartValue(float(self.icon_size))
        self._breath_anim.setEndValue(float(max(6, int(target))))
        self._breath_anim.start()

    def _refresh_style(self):
        """Hover / checked visuals are painted in paintEvent (custom glass
        circle).  IMPORTANT: we must NOT set a widget-level stylesheet here —
        when the tooltip anchor carries its own QSS, Qt renders QTipLabel with
        a near-black background and the themed QToolTip rule is ignored
        (dark-on-dark unreadable tooltips in light themes).  Painting the
        glass circle ourselves keeps the exact same look with zero stylesheets.
        """
        self.update()

    def set_theme(self, theme: dict):
        self._theme = theme
        self.update_icon()

    def enterEvent(self, e):
        self._hover = True
        self._refresh_style()
        self.update_icon()
        # Gentle grow on hover — the button "breathes"
        self._animate_icon_to(min(self.button_size - 10, self.icon_size_base + 2))
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self._refresh_style()
        self.update_icon()
        self._animate_icon_to(self.icon_size_base)
        super().leaveEvent(e)

    def paintEvent(self, e: QPaintEvent):
        """Custom-painted circular glass background (hover / checked).
        Replaces the old widget-level stylesheet so the tooltip anchor stays
        stylesheet-free (fixes black-on-black QTipLabel rendering)."""
        try:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            if self.isChecked() or self._hover:
                rect = self.rect().adjusted(1, 1, -1, -1)
                if self.isChecked():
                    fill = QColor(212, 168, 90, 46)   # golden glass tint
                else:
                    fill = QColor(255, 255, 255, 26)  # subtle white glass
                p.setPen(QPen(QColor(255, 255, 255, 34), 1))
                p.setBrush(QBrush(fill))
                p.drawEllipse(rect)
            # Draw the icon EXACTLY centered in logical pixels: deviceRatio-
            # aware sizing keeps the glyph symmetric inside the growing
            # circle from every direction.
            pm = self.icon().pixmap(self.iconSize())
            if not pm.isNull():
                dpr = pm.devicePixelRatio() or 1.0
                lw = int(pm.width() / dpr)    # logical (CSS-px) size
                lh = int(pm.height() / dpr)
                ix = (self.width() - lw) // 2
                iy = (self.height() - lh) // 2
                p.drawPixmap(ix, iy, pm)
            p.end()
        except Exception as ex:
            print(f"IconButton paint error: {ex}")

    def update_icon(self):
        if self._theme is None:
            return
        if self.isChecked():
            color = self._theme["gold_light"]
        elif self._hover:
            color = self._theme["gold"]
        else:
            color = self._theme["muted"]
        dpr = get_dpr()
        # Render at the CURRENT animated size (not the base) so the glyph
        # grows with the circle, pixel-crisp, around a common center.
        pm = render_icon(self.svg_string, self.icon_size, color, dpr)
        self.setIcon(QIcon(pm))
        super().setIconSize(QSize(self.icon_size, self.icon_size))
        self._refresh_style()

    def set_svg(self, svg_string: str):
        self.svg_string = svg_string
        self.update_icon()


# ============================================================================
# CoverLabel
# ============================================================================
class CoverLabel(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap = None
        self._has_cover = False
        self._theme = None
        self.setMinimumSize(220, 220)
        self.setMaximumSize(280, 280)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background: transparent; border: none;")

    def set_theme(self, theme: dict):
        self._theme = theme
        self.update()

    def set_cover(self, pm: QPixmap = None):
        if pm is None or pm.isNull():
            self._has_cover = False
            self._pixmap = None
        else:
            self._has_cover = True
            self._pixmap = pm
        self.update()

    def paintEvent(self, e: QPaintEvent):
        try:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            w, h = self.width(), self.height()
            radius = 18
            if self._has_cover and self._pixmap:
                scaled = self._pixmap.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
                side = min(scaled.width(), scaled.height())
                x = (scaled.width() - side) // 2
                y = (scaled.height() - side) // 2
                cropped = scaled.copy(x, y, side, side)
                path = QPainterPath()
                path.addRoundedRect(0, 0, w, h, radius, radius)
                p.setClipPath(path)
                p.drawPixmap(0, 0, cropped)
                p.setClipPath(path, Qt.ClipOperation.NoClip)
                p.setPen(QPen(QColor(0, 0, 0, 60), 1))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(0, 0, w - 1, h - 1, radius, radius)
            else:
                # Empty state: theme-aware accent gradient (v2.0 psychology
                # suite) with brand-gold fallback before first apply.
                t = self._theme or {}
                is_dark = t.get("is_dark", True)
                c_core = QColor(t.get("gold", "#d4a85a"))
                c_light = QColor(t.get("gold_light", "#f0d896"))
                c_deep = QColor(t.get("gold_deep", "#8b6a2a"))
                grad = QLinearGradient(0, 0, w, h)
                if is_dark:
                    grad.setColorAt(0.0, c_deep)
                    grad.setColorAt(0.5, c_core)
                    grad.setColorAt(1.0, c_light)
                    note_color = "rgba(26,20,16,90)"
                else:
                    grad.setColorAt(0.0, c_core.lighter(170))
                    grad.setColorAt(0.5, c_core)
                    grad.setColorAt(1.0, c_deep)
                    note_color = "rgba(255,255,255,150)"
                p.setBrush(QBrush(grad))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(0, 0, w, h, radius, radius)
                dpr = get_dpr()
                note_pm = render_icon(Icon.MUSIC_NOTE, max(60, h // 3), note_color, dpr)
                p.drawPixmap((w - note_pm.width()) // 2, (h - note_pm.height()) // 2, note_pm)
            p.end()
        except Exception as ex:
            # Never crash from paint — just log
            log.warning(f"CoverLabel paint error: {ex}")


# ============================================================================
# PlayerBar — seek bar at TOP, controls below
# ============================================================================
class PlayerBar(QFrame):
    """Layout (2 rows):
       Row 1 (top): [time_current] [========seek========] [time_total]
       Row 2 (bottom): [cover|title|like] [stretch] [prev|play|next] [stretch] [shuffle|repeat|vol|more]
    """

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.setObjectName("PlayerBar")
        self.theme = theme
        self.setFixedHeight(120)

        # ---- Top row: seek bar ----
        self.time_current = QLabel("0:00")
        self.time_current.setObjectName("TimeLabel")
        self.time_current.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.seek_slider = ClickableSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setObjectName("SeekSlider")
        self.seek_slider.setRange(0, 1000)
        self.seek_slider.setValue(0)
        self.seek_slider.setTracking(False)
        self.seek_slider.setCursor(Qt.CursorShape.PointingHandCursor)

        self.time_total = QLabel("0:00")
        self.time_total.setObjectName("TimeLabel")
        self.time_total.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        seek_row = QHBoxLayout()
        seek_row.setContentsMargins(0, 0, 0, 0)
        seek_row.setSpacing(10)
        seek_row.addWidget(self.time_current)
        seek_row.addWidget(self.seek_slider, 1)
        seek_row.addWidget(self.time_total)
        seek_w = QWidget()
        seek_w.setLayout(seek_row)

        # ---- Bottom row: controls ----
        self.cover_thumb = QLabel()
        self.cover_thumb.setObjectName("BarCover")
        self.cover_thumb.setFixedSize(56, 56)
        self.cover_thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cover_thumb_pm = None
        # Clicking the cover opens the fullscreen player, clicking the
        # title/artist opens the track info dialog (wired via these callback
        # attributes — set by MainWindow).
        self.cover_click_handler = None   # → fullscreen player
        self.info_click_handler = None    # → track info dialog
        self.cover_thumb.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cover_thumb.setToolTip("Open fullscreen player")
        self.cover_thumb.mousePressEvent = self._on_cover_click

        self.title_label = MarqueeLabel("No track selected")
        self.title_label.setObjectName("BarTitle")
        self.artist_label = MarqueeLabel("—")
        self.artist_label.setObjectName("BarArtist")
        info_layout = QVBoxLayout()
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(2)
        info_layout.addWidget(self.title_label)
        info_layout.addWidget(self.artist_label)
        info_widget = QWidget()
        info_widget.setLayout(info_layout)
        info_widget.setMinimumWidth(140)
        info_widget.setMaximumWidth(200)
        for lbl in (self.title_label, self.artist_label):
            lbl.setCursor(Qt.CursorShape.PointingHandCursor)
            lbl.mousePressEvent = self._on_info_click
        info_widget.setToolTip("Track info")

        self.like_btn = IconButton(Icon.HEART, icon_size=18, button_size=32)

        left_layout = QHBoxLayout()
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)
        left_layout.addWidget(self.cover_thumb)
        left_layout.addWidget(info_widget)
        left_layout.addWidget(self.like_btn)
        left_w = QWidget()
        left_w.setLayout(left_layout)
        left_w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        # Center: prev + play + next
        self.prev_btn = IconButton(Icon.PREV, icon_size=22, button_size=36)
        self.play_btn = QPushButton()
        self.play_btn.setObjectName("PlayBtn")
        self.play_btn.setFixedSize(56, 56)
        self.play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_btn._svg = Icon.PLAY
        self._play_theme = None
        self.next_btn = IconButton(Icon.NEXT, icon_size=22, button_size=36)

        center_layout = QHBoxLayout()
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(12)
        center_layout.addWidget(self.prev_btn)
        center_layout.addWidget(self.play_btn)
        center_layout.addWidget(self.next_btn)
        center_w = QWidget()
        center_w.setLayout(center_layout)
        center_w.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        # Right: shuffle + lyrics + repeat + vol + ⋯ menu
        self.shuffle_btn = IconButton(Icon.SHUFFLE, icon_size=18, button_size=32)
        self.shuffle_btn.setCheckable(True)
        # Lyrics toggle
        self.lyrics_btn = IconButton(Icon.LYRICS, icon_size=18, button_size=32)
        self.lyrics_btn.setCheckable(True)
        self.lyrics_btn.setToolTip("Lyrics")
        self.repeat_btn = IconButton(Icon.REPEAT, icon_size=18, button_size=32)
        self.repeat_btn.setCheckable(True)
        self.vol_btn = IconButton(Icon.VOLUME_HIGH, icon_size=18, button_size=28)
        self.vol_slider = ClickableSlider(Qt.Orientation.Horizontal)
        self.vol_slider.setObjectName("VolSlider")
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(80)
        self.vol_slider.setFixedWidth(80)
        self.vol_slider.set_smooth(True)
        self.vol_slider.setTracking(True)
        self.vol_slider.setCursor(Qt.CursorShape.PointingHandCursor)
        # Less-used actions live behind the ⋯ overflow menu:
        # A-B repeat, playback speed and track options.
        self.more_btn = IconButton(Icon.ELLIPSIS, icon_size=18, button_size=32)
        self.more_btn.setToolTip("More: A-B Repeat, Speed, Track options")
        self.ab_btn = IconButton(Icon.REPEAT_ONE, icon_size=16, button_size=32)
        self.ab_btn.setToolTip("A-B Repeat")
        self.ab_btn.setVisible(False)   # reachable from the ⋯ menu only
        self.rate_btn = IconButton(Icon.SPEED, icon_size=16, button_size=32)
        self.rate_btn.setToolTip("Playback speed")
        self.rate_btn.setVisible(False)  # reachable from the ⋯ menu only
        self.track_menu_btn = IconButton(Icon.LIST_LINES, icon_size=18, button_size=32)
        self.track_menu_btn.setToolTip("Track options")

        right_layout = QHBoxLayout()
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)
        right_layout.addWidget(self.shuffle_btn)
        right_layout.addWidget(self.lyrics_btn)
        right_layout.addWidget(self.repeat_btn)
        right_layout.addWidget(self.vol_btn)
        right_layout.addWidget(self.vol_slider)
        right_layout.addSpacing(4)
        right_layout.addWidget(self.more_btn)
        right_w = QWidget()
        right_w.setLayout(right_layout)
        right_w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        # Bottom row layout with center alignment
        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 0, 0, 0)
        bottom_row.setSpacing(0)
        bottom_row.addWidget(left_w, 1)
        bottom_row.addStretch(1)
        bottom_row.addWidget(center_w, 0)
        bottom_row.addStretch(1)
        bottom_row.addWidget(right_w, 1)
        bottom_w = QWidget()
        bottom_w.setLayout(bottom_row)

        # Main layout (vertical: seek on top, controls below)
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 8, 20, 10)
        main_layout.setSpacing(6)
        main_layout.addWidget(seek_w)
        main_layout.addWidget(bottom_w)

        self.set_theme(theme)

    def set_theme(self, theme: dict):
        self.theme = theme
        for btn in [self.like_btn, self.prev_btn, self.next_btn,
                    self.shuffle_btn, self.repeat_btn, self.vol_btn,
                    self.lyrics_btn, self.track_menu_btn,
                    self.more_btn, self.ab_btn, self.rate_btn]:
            btn.set_theme(theme)
        # Play button icon (always window_bg color on gold background)
        dpr = get_dpr()
        pm = render_icon(self.play_btn._svg, 24, theme["window_bg"], dpr)
        self.play_btn.setIcon(QIcon(pm))
        self.play_btn.setIconSize(QSize(24, 24))
        self._play_theme = theme
        self._refresh_cover_thumb()

    def _refresh_cover_thumb(self):
        try:
            if self._cover_thumb_pm is not None and not self._cover_thumb_pm.isNull():
                scaled = self._cover_thumb_pm.scaled(56, 56, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
                side = min(scaled.width(), scaled.height())
                x = (scaled.width() - side) // 2
                y = (scaled.height() - side) // 2
                self.cover_thumb.setPixmap(scaled.copy(x, y, side, side))
            else:
                dpr = get_dpr()
                pm = QPixmap(int(56 * dpr), int(56 * dpr))
                pm.fill(Qt.GlobalColor.transparent)
                painter = QPainter(pm)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                grad = QLinearGradient(0, 0, 56 * dpr, 56 * dpr)
                grad.setColorAt(0.0, QColor(self.theme["gold_deep"]))
                grad.setColorAt(1.0, QColor(self.theme["gold_light"]))
                painter.setBrush(QBrush(grad))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(0, 0, int(56 * dpr), int(56 * dpr), 8 * dpr, 8 * dpr)
                note_pm = render_icon(Icon.MUSIC_NOTE, int(28 * dpr), "rgba(26,20,16,140)", 1.0)
                painter.drawPixmap(int(14 * dpr), int(14 * dpr), note_pm)
                painter.end()
                pm.setDevicePixelRatio(dpr)
                self.cover_thumb.setPixmap(pm)
        except Exception as ex:
            log.warning(f"Cover thumb refresh error: {ex}")

    def set_cover_thumb(self, pm: QPixmap = None):
        self._cover_thumb_pm = pm
        self._refresh_cover_thumb()

    def _on_cover_click(self, event):
        """Cover thumbnail clicked → open the fullscreen player."""
        from PyQt6.QtCore import QEvent
        if (event.type() == QEvent.Type.MouseButtonPress
                and event.button() == Qt.MouseButton.LeftButton
                and callable(self.cover_click_handler)):
            self.cover_click_handler()
            try:
                event.accept()
            except Exception:
                pass

    def _on_info_click(self, event):
        """Title / artist clicked → ask the main window to open the
        track info dialog (handler is assigned by MainWindow after setup)."""
        from PyQt6.QtCore import QEvent
        if (event.type() == QEvent.Type.MouseButtonPress
                and event.button() == Qt.MouseButton.LeftButton
                and callable(self.info_click_handler)):
            self.info_click_handler()

    def update_play_button(self, playing: bool):
        if playing:
            self.play_btn._svg = Icon.PAUSE
            self.play_btn.setToolTip("Pause")
        else:
            self.play_btn._svg = Icon.PLAY
            self.play_btn.setToolTip("Play")
        if self._play_theme:
            dpr = get_dpr()
            pm = render_icon(self.play_btn._svg, 24, self._play_theme["window_bg"], dpr)
            self.play_btn.setIcon(QIcon(pm))
            self.play_btn.setIconSize(QSize(24, 24))

    def update_like_button(self, liked: bool):
        if liked:
            self.like_btn.set_svg(Icon.HEART_FILLED)
            self.like_btn.setChecked(True)
            self.like_btn.setToolTip("Remove from Favorites")
        else:
            self.like_btn.set_svg(Icon.HEART)
            self.like_btn.setChecked(False)
            self.like_btn.setToolTip("Add to Favorites")

    def update_shuffle_button(self, on: bool):
        self.shuffle_btn.setChecked(on)
        self.shuffle_btn.setToolTip("Shuffle: On" if on else "Shuffle: Off")

    def update_repeat_button(self, mode):
        if mode == RepeatMode.OFF:
            self.repeat_btn.set_svg(Icon.REPEAT)
            self.repeat_btn.setChecked(False)
            self.repeat_btn.setToolTip("Repeat: Off")
        elif mode == RepeatMode.ALL:
            self.repeat_btn.set_svg(Icon.REPEAT)
            self.repeat_btn.setChecked(True)
            self.repeat_btn.setToolTip("Repeat: All")
        else:
            self.repeat_btn.set_svg(Icon.REPEAT_ONE)
            self.repeat_btn.setChecked(True)
            self.repeat_btn.setToolTip("Repeat: One")

    def update_volume_button(self, vol: int):
        if vol == 0:
            self.vol_btn.set_svg(Icon.VOLUME_MUTE)
            self.vol_btn.setToolTip("Unmute")
        elif vol < 50:
            self.vol_btn.set_svg(Icon.VOLUME_LOW)
            self.vol_btn.setToolTip("Mute")
        else:
            self.vol_btn.set_svg(Icon.VOLUME_HIGH)
            self.vol_btn.setToolTip("Mute")


# ============================================================================
# SideRail — hover-expand with smooth animation + text labels
# ============================================================================
class RailButton(QWidget):
    """A button with icon + text label. Text is shown/hidden based on width.
    Manages its own checked state (not a QAbstractButton).
    """
    clicked = pyqtSignal()

    def __init__(self, svg_string: str, label: str, icon_size: int = 20, parent=None):
        super().__init__(parent)
        self._theme = None
        self._hover = False
        self._checked = False
        self._checkable = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.icon_btn = QPushButton()
        self.icon_btn.setFixedSize(44, 44)
        self.icon_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.icon_btn.setStyleSheet("background: transparent; border: none; border-radius: 12px; padding: 0; margin: 0;")
        self.icon_btn.clicked.connect(self.clicked.emit)

        self.label = QLabel(label)
        self.label.setStyleSheet("background: transparent; border: none;")

        layout.addWidget(self.icon_btn)
        layout.addWidget(self.label, 1)
        layout.addStretch()

        self._svg = svg_string
        self._icon_size = icon_size
        self._label_text = label
        self._update_icon()

    def set_theme(self, theme):
        self._theme = theme
        self._update_icon()
        if self._checked:
            self.label.setStyleSheet(f"color: {theme['gold_light']}; background: transparent; border: none; font-weight: 600;")
        else:
            self.label.setStyleSheet(f"color: {theme['text']}; background: transparent; border: none;")

    def set_checkable(self, c):
        self._checkable = c

    def setChecked(self, c):
        self._checked = c
        if self._theme:
            self.set_theme(self._theme)
            self._update_icon()
            # Update background
            if c:
                self.icon_btn.setStyleSheet(f"background: {self._theme['active']}; border: none; border-radius: 12px;")
            else:
                self.icon_btn.setStyleSheet("background: transparent; border: none; border-radius: 12px;")
        self._update_icon()

    def isChecked(self):
        return self._checked

    def set_svg(self, svg):
        self._svg = svg
        self._update_icon()

    def enterEvent(self, e):
        self._hover = True
        self._update_icon()
        if not self._checked:
            self.icon_btn.setStyleSheet(f"background: {self._theme['panel_bg_2']}; border: none; border-radius: 12px;")
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self._update_icon()
        if not self._checked:
            self.icon_btn.setStyleSheet("background: transparent; border: none; border-radius: 12px;")
        super().leaveEvent(e)

    def _update_icon(self):
        if self._theme is None:
            return
        if self._checked:
            color = self._theme["gold_light"]
        elif self._hover:
            color = self._theme["gold"]
        else:
            color = self._theme["muted"]
        dpr = get_dpr()
        pm = render_icon(self._svg, self._icon_size, color, dpr)
        self.icon_btn.setIcon(QIcon(pm))
        self.icon_btn.setIconSize(QSize(self._icon_size, self._icon_size))

    def set_text_visible(self, visible: bool):
        self.label.setVisible(visible)


# ============================================================================
# TrackRowDelegate — two-line rows: bold title over muted artist + index
# ============================================================================
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


def _pixmap_to_png_bytes(pm) -> "bytes | None":
    """PNG bytes for embedding a pixmap into rich text (None on failure)."""
    try:
        from PyQt6.QtCore import QBuffer
        ba = QBuffer()
        ba.open(QBuffer.OpenModeFlag.WriteOnly)
        pm.save(ba, "PNG")
        return bytes(ba.data()) or None
    except Exception:
        return None


# ============================================================================
# StatTrackRow — one "Most Played" row on the stats page
# ============================================================================
# ============================================================================
# StatsChartBar — animated horizontal bar chart of the top tracks
# ============================================================================
class StatsChartBar(QWidget):
    """Gradient bar chart with staggered grow-in animation. Bars are drawn
    from the active theme accents; hovering a bar highlights it and shows
    the exact play count. Data set via set_data([(label, value), ...])."""

    BAR_H = 22
    GAP = 10

    def __init__(self, theme: dict, parent=None):
        super().__init__(parent)
        self._theme = theme
        self._rows = []            # [(label, value)]
        self._max_v = 1
        self._progress = 1.0       # 0..1 grow-in animation
        self._hover_idx = -1
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(650)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)
        self.setMinimumHeight(60)
        self.setMouseTracking(True)

    def set_theme(self, theme: dict):
        self._theme = theme
        self.update()

    def _on_anim(self, v):
        self._progress = float(v)
        self.update()

    def set_data(self, rows):
        self._rows = list(rows)[:8]
        self._max_v = max((v for _, v in self._rows), default=1) or 1
        self._hover_idx = -1
        self.setMinimumHeight(max(60, len(self._rows) * (self.BAR_H + self.GAP) + 6))
        self._progress = 0.0
        self._anim.stop()
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

    def leaveEvent(self, e):
        self._hover_idx = -1
        self.update()
        super().leaveEvent(e)

    def mouseMoveEvent(self, e):
        y = e.position().y()
        idx = -1
        for i in range(len(self._rows)):
            top = i * (self.BAR_H + self.GAP)
            if top <= y <= top + self.BAR_H:
                idx = i
                break
        if idx != self._hover_idx:
            self._hover_idx = idx
            self.update()
        super().mouseMoveEvent(e)

    def paintEvent(self, e):
        if not self._rows:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        t = self._theme
        fm = self.fontMetrics()
        w = self.width()
        label_w = min(190, max(90, int(w * 0.32)))
        count_w = 44
        track_x = label_w + 10
        track_w = max(40, w - track_x - count_w - 8)
        for i, (label, value) in enumerate(self._rows):
            top = i * (self.BAR_H + self.GAP)
            # staggered grow-in: bar i starts a bit later
            local = max(0.0, min(1.0, (self._progress - i * 0.06) / 0.7))
            frac = (value / self._max_v) * local
            hovered = (i == self._hover_idx)
            # label
            p.setPen(QPen(QColor(t["text"] if not hovered else t["gold_light"])))
            p.setFont(self.font())
            elided = fm.elidedText(label, Qt.TextElideMode.ElideRight,
                                   label_w)
            p.drawText(QRect(0, top, label_w, self.BAR_H),
                       int(Qt.AlignmentFlag.AlignVCenter), elided)
            # bar track
            bar = QRect(track_x, top + 5, track_w, self.BAR_H - 10)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t["panel_bg_2"]))
            p.drawRoundedRect(bar, (self.BAR_H - 10) // 2, (self.BAR_H - 10) // 2)
            # gradient fill — BOTH stops as explicit QPointF: the mixed
            # QPoint/QPointF overload native-crashes this PyQt6 build.
            grad = QLinearGradient(
                QPointF(float(bar.left()), float(bar.top())),
                QPointF(float(bar.right()), float(bar.top())))
            grad.setColorAt(0.0, QColor(t["gold_deep"]))
            grad.setColorAt(1.0, QColor(t["gold_light"] if hovered else t["gold"]))
            p.setBrush(QBrush(grad))
            fill_w = max(6, int(bar.width() * frac)) if frac > 0 else 0
            if fill_w:
                cap = QRect(bar.x(), bar.y(), fill_w, bar.height())
                p.drawRoundedRect(cap, bar.height() // 2, bar.height() // 2)
            # value
            p.setPen(QPen(QColor(t["gold_light"])))
            shown = int(round(value * local))
            p.drawText(QRect(w - count_w, top, count_w, self.BAR_H),
                       int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
                       f"{shown}×")
        p.end()


class StatTrackRow(QFrame):
    """Medal + cover + title/artist + play count + a ▶ button. Double-click
    on the row also plays the track. Clicking ▶ delegates to the MainWindow
    callback so the track joins a proper playlist context."""

    def __init__(self, rank: int, path: str, title: str, artist: str,
                 count: int, cover_pm, theme: dict, on_play, parent=None):
        super().__init__(parent)
        self.path = path
        self._on_play = on_play
        self.theme = theme
        self.setObjectName("TrackList")
        self.setFixedHeight(52)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        t = theme
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(10)

        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, str(rank))
        rank_lbl = QLabel(medal)
        rank_lbl.setFixedWidth(26)
        rank_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rank_lbl.setStyleSheet(f"color: {t['gold']}; font-weight: 700; font-size: 14px;")
        lay.addWidget(rank_lbl)

        cov = QLabel()
        cov.setFixedSize(38, 38)
        cov.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if cover_pm is not None and not cover_pm.isNull():
            cov.setPixmap(cover_pm.scaled(
                38, 38, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation))
        else:
            cov.setPixmap(render_icon(Icon.MUSIC_NOTE, 24, t["muted"], get_dpr()))
        cov.setStyleSheet(
            f"border-radius: 8px; border: 1px solid {t['border']};")
        lay.addWidget(cov)

        col = QVBoxLayout()
        col.setSpacing(1)
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"color: {t['text']}; font-weight: 600; font-size: 13px;")
        title_lbl.setWordWrap(False)
        artist_lbl = QLabel(artist or "—")
        artist_lbl.setStyleSheet(f"color: {t['muted']}; font-size: 11px;")
        artist_lbl.setWordWrap(False)
        col.addWidget(title_lbl)
        col.addWidget(artist_lbl)
        lay.addLayout(col, 1)

        cnt_lbl = QLabel(f"{count}×")
        cnt_lbl.setStyleSheet(f"color: {t['gold_light']}; font-weight: 600; font-size: 13px;")
        lay.addWidget(cnt_lbl)

        self.play_btn = QPushButton()
        self.play_btn.setFixedSize(34, 34)
        self.play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_btn.setToolTip(f"Play “{title}”")
        self.play_btn.setIcon(QIcon(render_icon(Icon.PLAY, 16, t["gold_light"], get_dpr())))
        self.play_btn.setIconSize(QSize(16, 16))
        self.play_btn.setStyleSheet(
            f"QPushButton {{ background: {t['panel_bg_2']}; border: none;"
            f" border-radius: 17px; }}"
            f"QPushButton:hover {{ background: {t['gold']}; }}")
        self.play_btn.clicked.connect(self._emit_play)
        lay.addWidget(self.play_btn)

    def _emit_play(self):
        try:
            self._on_play(self.path)
        except Exception as ex:
            log.warning(f"stats play failed: {ex}")

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._emit_play()
        super().mouseDoubleClickEvent(e)

    def set_theme(self, theme: dict):
        self.theme = theme
        t = theme
        self.play_btn.setIcon(QIcon(render_icon(Icon.PLAY, 16, t["gold_light"], get_dpr())))
        self.play_btn.setStyleSheet(
            f"QPushButton {{ background: {t['panel_bg_2']}; border: none;"
            f" border-radius: 17px; }}"
            f"QPushButton:hover {{ background: {t['gold']}; }}")


# ============================================================================
# MarqueeLabel — QLabel that scrolls overflow text horizontally (looping)
# ============================================================================
class MarqueeLabel(QLabel):
    """When the text is wider than the widget it scrolls continuously
    (left, gap, re-enter) so a long track title stays fully readable.
    Short text behaves exactly like a static centered QLabel."""

    GAP_PX = 48            # breathing room between the two copies
    SPEED_PX_PER_S = 28    # slow, classy crawl
    EDGE_PAD_PX = 10       # visual padding at both ends

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._offset = 0.0
        self._full_width = 0      # width of the full text (1 copy)
        self._timer = QTimer(self)
        self._timer.setInterval(33)   # ~30 fps — smooth, cheap
        self._timer.timeout.connect(self._tick)
        self._recalc()

    # -- text management -------------------------------------------------
    def setMarqueeText(self, text: str):
        """Set label text (restarts the scroll when text changes)."""
        if text == self.text():
            return
        self.setText(text)
        self._offset = 0
        self._recalc()

    def _recalc(self):
        fm = self.fontMetrics()
        self._full_width = max(0, fm.horizontalAdvance(self.text()))
        overflow = (self._full_width > max(1, self.width() - 2 * self.EDGE_PAD_PX))
        if overflow and not self.isVisibleTo(self.parentWidget()) is None:
            pass
        if overflow and not self._timer.isActive():
            self._timer.start()
        elif not overflow:
            self._timer.stop()
            self._offset = 0
        self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._recalc()

    def _tick(self):
        # Total loop = text width + gap; wraps back to the start seamlessly
        self._offset = (self._offset + self.SPEED_PX_PER_S * 0.033) % \
            (self._full_width + self.GAP_PX)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(self.palette().color(self.foregroundRole()))
        w = self.width()
        overflow = self._full_width > (w - 2 * self.EDGE_PAD_PX)
        if not overflow:
            # Static path — identical to a normal centered label
            p.drawText(self.rect(), int(self.alignment()), self.text())
            p.end()
            return
        # Clip the scrolling region so the copies never bleed outside
        p.setClipRect(self.rect())
        fm = self.fontMetrics()
        y = (self.height() + fm.ascent() - fm.descent()) // 2
        total = self._full_width + self.GAP_PX
        # Copy 1 scrolls left; copy 2 follows after the gap
        x1 = self.EDGE_PAD_PX - self._offset
        x2 = x1 + total
        p.drawText(QPointF(x1, y), self.text())
        p.drawText(QPointF(x2, y), self.text())
        # Soft fade masks at both edges (the theme window bg works as both
        # text backdrop and fade source in every theme)
        t = getattr(self.parent(), "theme", None) or {}
        bg = QColor(t.get("window_bg", "#0a0805"))
        edge = 16
        grad = QLinearGradient(QPointF(0, 0), QPointF(edge, 0))
        grad.setColorAt(0.0, QColor(bg.red(), bg.green(), bg.blue(), 255))
        grad.setColorAt(1.0, QColor(bg.red(), bg.green(), bg.blue(), 0))
        p.fillRect(0, 0, edge, self.height(), QBrush(grad))
        grad = QLinearGradient(QPointF(w - edge, 0), QPointF(w, 0))
        grad.setColorAt(0.0, QColor(bg.red(), bg.green(), bg.blue(), 0))
        grad.setColorAt(1.0, QColor(bg.red(), bg.green(), bg.blue(), 255))
        p.fillRect(w - edge, 0, edge, self.height(), QBrush(grad))
        p.end()


# ============================================================================
# LoadingOverlay — glassy "Loading…" veil with a themed three-arc spinner
# ============================================================================
class LoadingOverlay(QWidget):
    """Themed multi-arc loading indicator.

    NON-BLOCKING by design: the user asked to keep browsing other sections
    while something loads, so this is a compact glass card pinned to the
    bottom-right corner — it never covers or steals clicks from the rest of
    the window (WA_TransparentForMouseEvents on the veil)."""
    SPIN_MS = 1100          # one full revolution
    CARD_W, CARD_H = 260, 116

    def __init__(self, parent=None):
        super().__init__(parent)
        # Clicks pass THROUGH the veil; only the card is decoration. The
        # rest of the app stays fully interactive during any load.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setVisible(False)
        self._angle = 0          # spinner rotation (degrees)
        self._fade = 0.0         # 0..1 appearance fade for the card
        self._dots = 0
        self._timer = QTimer(self)
        self._timer.setInterval(350)
        self._timer.timeout.connect(self._tick)
        # High-frequency spinner clock — 60 fps-capable, cheap (one repaint
        # of the card region while visible, zero cost when hidden)
        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(16)
        self._spin_timer.timeout.connect(self._spin_tick)
        # Fade-in controller for a soft entrance
        self._fade_timer = QTimer(self)
        self._fade_timer.setInterval(16)
        self._fade_timer.timeout.connect(self._fade_tick)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 24, 56)   # anchor bottom-right
        lay.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        card = QFrame()
        card.setFixedSize(self.CARD_W, self.CARD_H)
        card.setObjectName("LoadingCard")
        card.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.label = QLabel("Loading")
        self.label.setObjectName("LoadingLabel")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(0, 14, 0, 0)
        card_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._spinner_host = _SpinnerCanvas(card)
        self._spinner_host.setFixedSize(64, 64)   # bigger, easier to see
        card_lay.addWidget(self._spinner_host, 0, Qt.AlignmentFlag.AlignCenter)
        card_lay.addSpacing(6)
        card_lay.addWidget(self.label)
        row.addWidget(card)
        lay.addLayout(row)
        self._card = card

    # -- timers ---------------------------------------------------------
    def _tick(self):
        self._dots = (self._dots + 1) % 4
        base = getattr(self, "_base_text", "Loading")
        self.label.setText(base + "." * self._dots)

    def _spin_tick(self):
        # 6°/frame @60fps ≈ 1.0 s/rev; the canvas paints itself from this
        self._angle = (self._angle + 6) % 360
        self._spinner_host.set_angle(self._angle, self._fade)

    def _fade_tick(self):
        self._fade = min(1.0, self._fade + 0.09)
        if self._fade >= 1.0:
            self._fade_timer.stop()

    # -- public API ------------------------------------------------------
    def show_loading(self, text: str = "Loading"):
        self._base_text = text
        self.label.setText(text)
        self._dots = 0
        self.setGeometry(self.parentWidget().rect())
        self.raise_()
        self.setVisible(True)
        # Sync spinner + card theme colors with the CURRENT theme
        t = getattr(self.parent(), "theme", None) or {}
        self._spinner_host.set_theme(t)
        self._spinner_host.set_stroke(5.2)   # thick, visible at a glance
        card = self._card
        card.setStyleSheet(
            f"#LoadingCard {{ background: rgba({_rgba(t.get('panel_bg', '#241d17'))}, 242);"
            f" border: 1px solid {t.get('border', '#372d1c')};"
            f" border-radius: 16px; }}")
        self._timer.start()
        self._spin_timer.start()
        self._fade = 0.0
        self._fade_timer.start()
        QApplication.processEvents()   # paint the indicator before the heavy work

    def hide_loading(self):
        self._timer.stop()
        self._spin_timer.stop()
        self._fade_timer.stop()
        self.setVisible(False)

    def paintEvent(self, e):
        # No dark veil anymore — the corner card is enough, and the window
        # stays fully clickable underneath.
        pass


def _rgba(hex_color: str) -> str:
    """'#rrggbb' -> 'r,g,b' (for QSS rgba())."""
    try:
        c = QColor(hex_color)
        return f"{c.red()},{c.green()},{c.blue()}"
    except Exception:
        return "36,29,23"


class _SpinnerCanvas(QWidget):
    """Three counter-rotating rounded arcs in theme accent tones — smooth,
    DPI-crisp, and colored by the active theme (no downloaded asset needed)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._angle = 0
        self._fade = 0.0
        self._stroke = 3.4
        self._core = QColor("#d4a85a")
        self._light = QColor("#ffd982")
        self._deep = QColor("#b88a33")

    def set_theme(self, t: dict):
        try:
            self._core = QColor(t.get("gold", "#d4a85a"))
            self._light = QColor(t.get("gold_light", "#ffd982"))
            self._deep = QColor(t.get("gold_deep", "#b88a33"))
        except Exception:
            pass

    def set_stroke(self, w: float):
        self._stroke = w
        self.update()

    def set_angle(self, angle: float, fade: float):
        self._angle = angle
        self._fade = fade
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        rect = QRectF(self.rect()).adjusted(4, 4, -4, -4)
        p.translate(w / 2, h / 2)
        # Outer arc — main accent, 270° sweep, rotates clockwise
        pen = QPen(self._core)
        pen.setWidthF(self._stroke)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.save()
        p.rotate(self._angle)
        p.drawArc(rect, 90 * 16, -270 * 16)
        p.restore()
        # Middle arc — lighter tone, counter-rotating, 140° sweep
        pen = QPen(self._light)
        pen.setWidthF(max(2.2, self._stroke * 0.72))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.save()
        p.rotate(-self._angle * 1.6)
        p.drawArc(rect.adjusted(6, 6, -6, -6), 0 * 16, 140 * 16)
        p.restore()
        # Inner dot — deep accent, orbiting
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._deep)
        inner = rect.adjusted(12, 12, -12, -12)
        a = math.radians(-self._angle * 2)
        cx = inner.center().x() + (inner.width() / 2) * math.cos(a)
        cy = inner.center().y() + (inner.height() / 2) * math.sin(a)
        p.drawEllipse(QPointF(cx, cy), 3.2, 3.2)
        p.end()


class SideRail(QFrame):
    """Always-collapsed sidebar with icon buttons + tooltips."""
    rail_clicked = pyqtSignal(str)
    COLLAPSED_WIDTH = 68

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.setObjectName("SideRail")
        self.theme = theme
        self.setFixedWidth(self.COLLAPSED_WIDTH)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 16, 12, 16)
        layout.setSpacing(6)

        # Empty space where the logo used to be
        self.logo_btn = QToolButton()
        self.logo_btn.setEnabled(False)
        self.logo_btn.setFixedSize(44, 44)
        self.logo_btn.setStyleSheet("background: transparent; border: none; padding: 0; margin: 0;")
        layout.addWidget(self.logo_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        # Use IconButton (simple, no text, always collapsed).
        # Grouped: navigation first, then a divider, then actions.
        self.btn_library = self._make_icon_btn(Icon.LIBRARY, "Library", "library", checked=True)
        self.btn_favorites = self._make_icon_btn(Icon.FAVORITES_NAV, "Favorites", "favorites")
        self.btn_albums = self._make_icon_btn(Icon.ALBUMS, "Albums", "albums")
        self.btn_stats = self._make_icon_btn(Icon.STATS, "Listening Stats", "stats")

        for btn in [self.btn_library, self.btn_favorites,
                    self.btn_albums, self.btn_stats]:
            layout.addWidget(btn)

        # Divider between navigation and actions
        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setFixedWidth(36)
        divider.setStyleSheet(f"background: {theme['border']}; margin: 4px auto;")
        layout.addWidget(divider)

        self.btn_add = self._make_icon_btn(Icon.FOLDER_ADD, "Add Folder", "addfolder", checkable=False)
        self.btn_refresh = self._make_icon_btn(Icon.REFRESH, "Refresh Library", "refresh", checkable=False)
        self.btn_playlists = self._make_icon_btn(Icon.PLAYLISTS, "Playlists", "playlists", checkable=False)

        for btn in [self.btn_add, self.btn_refresh, self.btn_playlists]:
            layout.addWidget(btn)

        layout.addStretch(1)

        self.btn_mini = self._make_icon_btn(Icon.MINIMIZE, "Mini Player", "mini", checkable=False)
        self.btn_theme = self._make_icon_btn(Icon.BRUSH, "Choose Theme", "theme", checkable=False)
        self.btn_settings = self._make_icon_btn(Icon.SETTINGS, "Settings", "settings", checkable=False)
        layout.addWidget(self.btn_mini)
        layout.addWidget(self.btn_theme)
        layout.addWidget(self.btn_settings)

        self._all_buttons = [self.btn_library, self.btn_favorites, self.btn_refresh,
                             self.btn_add, self.btn_albums, self.btn_stats,
                             self.btn_playlists, self.btn_mini,
                             self.btn_theme, self.btn_settings]

    def _make_icon_btn(self, svg_string, tooltip, key, checked=False, checkable=True):
        """Create a simple IconButton with tooltip."""
        b = IconButton(svg_string, icon_size=20, button_size=44)
        b.setCheckable(checkable)
        b.setChecked(checked)
        b.setToolTip(tooltip)
        b.set_theme(self.theme)
        b.clicked.connect(lambda: self.rail_clicked.emit(key))
        return b

    def set_theme(self, theme: dict):
        self.theme = theme
        for btn in self._all_buttons:
            btn.set_theme(theme)
        # Theme button always shows the brush (theme picker affordance)
        self.btn_theme.set_svg(Icon.BRUSH)
        self.btn_theme.setToolTip("Choose Theme")


# ============================================================================
# Cover loader thread
# ============================================================================
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


# ============================================================================
# Tag loader thread — loads title/artist in background for list items
# ============================================================================
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


# ============================================================================
# Albums worker thread — groups untagged tracks + decodes covers off-UI
# ============================================================================
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


# ============================================================================
# Main Window
# ============================================================================
class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1000, 700)
        self.setMinimumSize(860, 620)
        # Accept folder/file drops from the OS file manager
        self.setAcceptDrops(True)
        # Publish window title for second-launch focus (single-instance)
        self._publish_title_shm()

        # State
        self.theme_name = Theme.DEFAULT_NAME
        self.theme = Theme.get(self.theme_name)
        self.library = []
        self.favorites = set()
        self.added_folders = []
        self.playlists = {}   # {name: [track paths]} — persisted in config
        self.current_view = View.LIBRARY
        self.current_track = None
        self.current_index = -1
        self.current_playlist = []
        # Playback history for shuffle-correct Previous/Next navigation.
        # history holds tracks in the order they STARTED playing; redo holds
        # tracks "taken back" with Previous so Next can replay them forward.
        # Both store paths (not indexes) so library edits can't corrupt them.
        self.play_history = []    # [..., previous_track]
        self.redo_stack = []      # [next_track_after_redo, ...]
        self._nav_via_history = False  # True while stepping back through history
        # Smart shuffle: paths in the current playlist that have NOT played
        # yet this session. Shuffle picks from here first so every track
        # gets a turn before repeats; refilled when the playlist changes.
        self.shuffle_unplayed = set()
        self.shuffle = False
        self.repeat_mode = RepeatMode.OFF
        self.user_is_seeking = False
        self.sort_mode = SortMode.TITLE
        self.search_filter = ""
        # Folder filter from the folder tree — persisted while the app runs
        # so search/sort changes don't silently reset it (FUNC-6).
        self._folder_filter = None
        # Search typing debounce timer (created lazily in _on_search_changed)
        self._search_debounce = None

        # Settings
        self.auto_rescan = True
        self.remember_track = True
        # Taskbar click on the app icon minimizes by default; when this is
        # enabled the window hides to the tray instead of minimizing.
        self.taskbar_close_to_tray = False
        self.default_folder = ""
        self.sleep_timer_active = False
        self.sleep_timer_minutes = 30
        self._sleep_timer = None
        # Fullscreen visualizer (animated background) — persisted, toggled
        # in Settings → Interface and on the fullscreen top bar itself.
        self.visualizer_enabled = True

        # Audio
        self.audio = AudioBackend(self)
        self.audio.position_changed.connect(self._on_position_changed)
        self.audio.duration_changed.connect(self._on_duration_changed)
        self.audio.state_changed.connect(self._on_state_changed)
        self.audio.error_occurred.connect(self._on_audio_error)
        self.audio.track_finished.connect(self._on_track_finished)

        # Playback effects: speed / A-B repeat / fade
        from playback_fx import PlaybackFX
        self.fx = PlaybackFX(self.audio)

        # Play statistics (user data — survives updates)
        try:
            from playstats import PlayStats, set_search_roots
            self.stats = PlayStats(config_path().parent / "stats.json")
            # Moved-file resolution needs to know where music lives; the
            # full folder set arrives with the async config load, so seed
            # with what's known now and refresh after config + every scan.
            set_search_roots(list(getattr(self, "added_folders", []) or []))
            self._stats_timer = QTimer(self)
            self._stats_timer.setInterval(30_000)   # flush every 30 s
            self._stats_timer.timeout.connect(self._flush_stats)
            self._stats_timer.start()
            # Refresh the visible stats page once a minute while it's shown
            self._stats_view_timer = QTimer(self)
            self._stats_view_timer.setInterval(60_000)
            self._stats_view_timer.timeout.connect(
                lambda: (self.stack.currentIndex() == 3) and self._refresh_stats_view())
            self._stats_view_timer.start()
        except Exception as e:
            log.warning(f"stats init failed: {e}")
            self.stats = None

        # Track-change toast (window hidden only)
        self._toast_enabled = True
        # Per-track listening meter (70% credit rule for stats)
        self._listened_ms = 0
        self._last_toast_key = None

        # Threads
        self.scanner = None
        self._scan_results_pending = None
        self._cover_loader = None
        self._albums_worker = None

        # Mini player
        self.mini_player = None

        # Read the saved theme EARLY — BEFORE building anything visible.
        # (Tiny local JSON read.)  Order matters: the tray popup is built
        # in _build_tray() with self.theme, so it must come after this or
        # the tray menu would bake DEFAULT (royal_gold) colors on every
        # boot into a different theme.
        self._early_load_theme()

        # Tray
        self.tray = None
        self._build_tray()

        # UI
        self._build_ui()
        self._apply_theme()
        # Defer config loading to after window is shown (instant launch)
        self._force_quit = False
        self._last_volume = 80

        self.ui_timer = QTimer(self)
        self.ui_timer.setInterval(500)
        self.ui_timer.timeout.connect(self._ui_tick)
        self.ui_timer.start()
        # Faster tick for A-B repeat precision (100 ms)
        self._fx_timer = QTimer(self)
        self._fx_timer.setInterval(100)
        self._fx_timer.timeout.connect(lambda: getattr(self, "fx", None) and self.fx.tick())
        self._fx_timer.start()

        # Keyboard shortcuts
        self._setup_shortcuts()

        # Global media keys (keyboard/headset) — Windows only, best effort
        self._media_key_filter = None
        try:
            app_inst = QApplication.instance()
            if app_inst is not None and sys.platform == "win32":
                mk = MediaKeyFilter()
                if mk.install():
                    app_inst.installNativeEventFilter(mk)
                    mk.play_pause.connect(self._on_play_pause)
                    mk.stop.connect(lambda: (self.audio.stop(),
                                             self.player_bar.update_play_button(False)))
                    mk.next.connect(self._on_next)
                    mk.prev.connect(self._on_prev)
                    self._media_key_filter = mk
        except Exception as e:
            log.warning(f"Media key setup failed: {e}")

        # Load config AFTER window is shown (async, non-blocking)
        # User-data safety net: snapshot + auto-restore BEFORE touching config
        try:
            import userbackup
            data_dir = config_path().parent
            userbackup.restore_missing(data_dir)
            userbackup.create_startup_backup(data_dir)
        except Exception as e:
            log.warning(f"backup bootstrap failed: {e}")

        QTimer.singleShot(50, self._load_config_async)

        # Auto theme: follow the OS dark/light preference when enabled
        self.auto_theme_enabled = False
        try:
            from autottheme import AutoThemeWatcher
            self._auto_theme_watcher = AutoThemeWatcher(self)
            self._auto_theme_watcher.changed.connect(self._on_os_theme_changed)
        except Exception as e:
            log.warning(f"auto-theme watcher unavailable: {e}")
            self._auto_theme_watcher = None

    # Dark and light theme names used by "follow Windows" mode.
    AUTO_DARK_THEME = Theme.AUTO_DARK_DEFAULT
    AUTO_LIGHT_THEME = Theme.AUTO_LIGHT_DEFAULT

    def _apply_auto_theme_setting(self):
        if getattr(self, "_auto_theme_watcher", None) is None:
            return
        if self.auto_theme_enabled:
            self._auto_theme_watcher.start()
            self._on_os_theme_changed(_os_prefers_dark())
        else:
            self._auto_theme_watcher.stop()

    def _on_os_theme_changed(self, prefer_dark: bool):
        if not self.auto_theme_enabled:
            return
        target = self.AUTO_DARK_THEME if prefer_dark else self.AUTO_LIGHT_THEME
        if target != self.theme_name:
            self.theme_name = target
            self._apply_theme()
            self._save_config()

    def _publish_title_shm(self):
        """Share our window title via QSharedMemory so a second launch can
        find and raise this window."""
        try:
            from PyQt6.QtCore import QSharedMemory
            # Keep a reference for the process lifetime — a parent-less local
            # used to be garbage-collected the moment this function returned,
            # destroying the segment and silently disabling the feature.
            shm = QSharedMemory("GoldenMusic_WindowTitle")
            # Leftover segment from a crashed run — clean it up
            if shm.attach(QSharedMemory.AccessMode.ReadWrite):
                shm.detach()
            data = self.windowTitle().encode("utf-8") + b"\x00"
            if shm.create(len(data)):
                shm.data()[:len(data)] = data
                self._title_shm = shm
        except Exception as e:
            log.debug(f"title shared memory unavailable: {e}")

    def _build_ui(self):
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)

        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Scan progress bar (at very top)
        self.scan_progress = QProgressBar()
        self.scan_progress.setFixedHeight(4)
        self.scan_progress.setTextVisible(False)
        self.scan_progress.setRange(0, 100)
        self.scan_progress.setValue(0)
        self.scan_progress.setVisible(False)
        outer.addWidget(self.scan_progress)

        # Top area
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(0)

        self.rail = SideRail(self.theme)
        self.rail.rail_clicked.connect(self._on_rail_clicked)
        top.addWidget(self.rail)

        # Loading overlay (covers the whole window during slow operations)
        self.loading_overlay = LoadingOverlay(self)

        self.main_area = QWidget()
        self.main_area.setObjectName("MainArea")
        self.main_layout = QVBoxLayout(self.main_area)
        self.main_layout.setContentsMargins(28, 20, 28, 14)
        self.main_layout.setSpacing(10)

        # Top row: title + search + sort + buttons
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(8)

        self.page_title = QLabel("Library")
        self.page_title.setObjectName("PageTitle")
        top_row.addWidget(self.page_title)
        top_row.addSpacing(12)

        # Search bar
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search tracks...")
        self.search_edit.setFixedWidth(200)
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._on_search_changed)
        top_row.addWidget(self.search_edit)

        # Sort dropdown
        self.sort_combo = QComboBox()
        self.sort_combo.addItem("Sort: Title", SortMode.TITLE.value)
        self.sort_combo.addItem("Sort: Artist", SortMode.ARTIST.value)
        self.sort_combo.addItem("Sort: Date Added", SortMode.DATE_ADDED.value)
        self.sort_combo.addItem("Sort: Filename", SortMode.FILENAME.value)
        self.sort_combo.setFixedWidth(140)
        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        top_row.addWidget(self.sort_combo)

        top_row.addStretch(1)

        self.scan_status_label = QLabel("")
        self.scan_status_label.setObjectName("CountLabel")
        self.scan_status_label.setStyleSheet(f"color: {self.theme['gold']};")
        self.scan_status_label.setVisible(False)
        top_row.addWidget(self.scan_status_label)
        top_row.addSpacing(8)

        self.count_label = QLabel("")
        self.count_label.setObjectName("CountLabel")
        top_row.addWidget(self.count_label)

        top_row.addStretch(1)

        # Action group (kept together at the right edge)
        self.shuffle_all_btn = QPushButton("Shuffle All")
        self.shuffle_all_btn.setObjectName("GhostBtn")
        self.shuffle_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.shuffle_all_btn.clicked.connect(self._on_shuffle_all)
        top_row.addWidget(self.shuffle_all_btn)

        self.play_all_btn = QPushButton("Play All")
        self.play_all_btn.setObjectName("GoldBtn")
        self.play_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_all_btn.clicked.connect(self._on_play_all)
        top_row.addWidget(self.play_all_btn)

        top_row_w = QWidget()
        top_row_w.setLayout(top_row)
        self.main_layout.addWidget(top_row_w)

        # Stack
        self.stack = QStackedWidget()
        self.stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.stack.addWidget(self._build_library_page())
        self.stack.addWidget(self._build_favorites_page())
        self.stack.addWidget(self._build_albums_page())
        self.stack.addWidget(self._build_stats_page())
        self.main_layout.addWidget(self.stack, 1)

        top.addWidget(self.main_area, 1)
        outer.addLayout(top, 1)

        # Player bar
        self.player_bar = PlayerBar(self.theme)
        outer.addWidget(self.player_bar)
        # Cover click → fullscreen player; title/artist click →
        # track info dialog (separate handlers so the cover opens the
        # immersive view while the text still shows track details).
        self.player_bar.cover_click_handler = self._toggle_fullscreen_player
        self.player_bar.info_click_handler = self._on_bar_info_clicked

        # Signals
        self.player_bar.like_btn.clicked.connect(self._on_like_toggled)
        self.player_bar.prev_btn.clicked.connect(self._on_prev)
        self.player_bar.play_btn.clicked.connect(self._on_play_pause)
        self.player_bar.next_btn.clicked.connect(self._on_next)
        self.player_bar.shuffle_btn.clicked.connect(self._on_shuffle_toggled)
        self.player_bar.lyrics_btn.clicked.connect(self._toggle_lyrics_panel)
        self.player_bar.more_btn.clicked.connect(self._show_more_menu)
        self.player_bar.ab_btn.clicked.connect(self._on_ab_button)
        self.player_bar.rate_btn.clicked.connect(self._cycle_playback_rate)
        self.player_bar.repeat_btn.clicked.connect(self._on_repeat_toggled)
        self.player_bar.seek_slider.sliderPressed.connect(self._on_seek_pressed)
        self.player_bar.seek_slider.sliderReleased.connect(self._on_seek_released)
        self.player_bar.seek_slider.sliderMoved.connect(self._on_seek_moved)
        self.player_bar.seek_slider.valueChanged.connect(self._on_seek_value_changed)
        self.player_bar.vol_slider.valueChanged.connect(self._on_volume_changed)
        self.player_bar.vol_slider.sliderMoved.connect(self._on_volume_changed)
        self.player_bar.vol_btn.clicked.connect(self._on_mute_toggle)

        self.library_list.itemDoubleClicked.connect(self._on_track_double_clicked)
        self.favorites_list.itemDoubleClicked.connect(self._on_track_double_clicked)

        # Context menus
        self.library_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.library_list.customContextMenuRequested.connect(self._on_context_menu)
        self.favorites_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.favorites_list.customContextMenuRequested.connect(self._on_context_menu)

        icon_path = Path(__file__).parent / "assets" / "icon.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

    def _build_library_page(self) -> QWidget:
        """Two-column layout: track list LEFT, cover art RIGHT."""
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        # LEFT: folder tree + track list
        left_split = QSplitter(Qt.Orientation.Horizontal)
        left_split.setHandleWidth(8)
        left_split.setStyleSheet(f"QSplitter::handle {{ background: transparent; }}")

        # Folder tree
        self.folder_tree = QTreeWidget()
        self.folder_tree.setHeaderHidden(True)
        self.folder_tree.setObjectName("TrackList")
        self.folder_tree.setMinimumWidth(140)
        self.folder_tree.setMaximumWidth(220)
        self.folder_tree.setStyleSheet(
            f"QTreeWidget {{ background: {self.theme['panel_bg']}; border: 1px solid {self.theme['border']}; border-radius: 10px; padding: 6px; outline: 0; }}"
            f" QTreeWidget::item {{ padding: 8px 6px; border-radius: 8px; margin: 1px 2px; outline: 0; background: transparent; }}"
            f" QTreeWidget::item:hover {{ background: {self.theme['panel_bg_2']}; outline: 0; }}"
            f" QTreeWidget::item:selected {{ background: transparent; color: {self.theme['gold_light']}; outline: 0; border: none; }}"
            f" QTreeWidget::item:focus {{ background: transparent; outline: 0; border: none; }}"
        )
        # No selection rectangle at all behind folder names — clicks are
        # handled in code and the folder stays visually clean.
        self.folder_tree.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection)
        self.folder_tree.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.folder_tree.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.folder_tree.itemClicked.connect(self._on_folder_tree_click)
        self.folder_tree.itemExpanded.connect(self._on_folder_tree_expanded)
        self.folder_tree.customContextMenuRequested.connect(
            self._on_folder_tree_context_menu)
        left_split.addWidget(self.folder_tree)

        # Track list
        self.library_list = QListWidget()
        self.library_list.setObjectName("TrackList")
        self.library_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.library_list.setUniformItemSizes(True)
        self.library_list.setMinimumHeight(200)
        self._track_delegate = TrackRowDelegate(self.theme, self.library_list)
        self.library_list.setItemDelegate(self._track_delegate)
        left_split.addWidget(self.library_list)

        left_split.setSizes([160, 400])
        left_split.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(left_split, 3)

        # RIGHT: cover art + title + artist
        right_col = QVBoxLayout()
        right_col.setSpacing(10)
        right_col.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_col.setContentsMargins(10, 10, 10, 10)

        self.cover_label = CoverLabel()
        right_col.addWidget(self.cover_label, alignment=Qt.AlignmentFlag.AlignCenter)

        self.now_title = MarqueeLabel("No track selected")
        self.now_title.setObjectName("NowTitle")
        self.now_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_col.addWidget(self.now_title)

        self.now_artist = MarqueeLabel("Add a folder to get started")
        self.now_artist.setObjectName("NowArtist")
        self.now_artist.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_col.addWidget(self.now_artist)

        right_w = QWidget()
        right_w.setLayout(right_col)
        right_w.setFixedWidth(300)
        right_w.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        layout.addWidget(right_w, 0)

        return page

    def _build_favorites_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        center = QHBoxLayout()
        center.addStretch(1)
        cover_col = QVBoxLayout()
        cover_col.setSpacing(10)
        cover_col.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.fav_cover = CoverLabel()
        cover_col.addWidget(self.fav_cover, alignment=Qt.AlignmentFlag.AlignCenter)
        self.fav_title = QLabel("Favorites")
        self.fav_title.setObjectName("NowTitle")
        self.fav_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cover_col.addWidget(self.fav_title)
        self.fav_subtitle = QLabel("Double-click a track to play")
        self.fav_subtitle.setObjectName("NowArtist")
        self.fav_subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cover_col.addWidget(self.fav_subtitle)
        center.addLayout(cover_col)
        center.addStretch(1)
        center_w = QWidget()
        center_w.setLayout(center)
        center_w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(center_w, 3)
        self.favorites_list = QListWidget()
        self.favorites_list.setObjectName("TrackList")
        self.favorites_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.favorites_list.setUniformItemSizes(True)
        self.favorites_list.setMinimumHeight(140)
        self._fav_delegate = TrackRowDelegate(self.theme, self.favorites_list)
        self.favorites_list.setItemDelegate(self._fav_delegate)
        layout.addWidget(self.favorites_list, 2)
        return page

    # ------------------------------------------------------------------
    # Albums grid page
    # ------------------------------------------------------------------
    def _build_albums_page(self) -> QWidget:
        from PyQt6.QtWidgets import QListWidget, QListWidgetItem
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self.albums_grid = QListWidget()
        self.albums_grid.setObjectName("TrackList")
        self.albums_grid.setViewMode(QListWidget.ViewMode.IconMode)
        self.albums_grid.setIconSize(QSize(120, 120))
        self.albums_grid.setGridSize(QSize(150, 190))
        self.albums_grid.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)   # one album at a time
        self.albums_grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.albums_grid.setWordWrap(True)
        self.albums_grid.itemDoubleClicked.connect(self._on_album_clicked)
        layout.addWidget(self.albums_grid)
        return page

    def _rebuild_albums_grid(self):
        """Group library tracks by (album tag | artist | folder) and show
        each group's cover + name.

        LAG FIX: the old version ran mutagen over every untagged track and
        decoded covers synchronously on the UI thread (≈14 s freeze on a
        3k-track library). The grouping + cover decoding now runs in a
        QThread; results stream back and the grid fills incrementally.
        """
        import coverart as _ca

        # Fast pass — group by cached tags (no I/O); only truly untagged
        # tracks need the background worker.
        groups = {}
        untagged = []
        for p in self.library:
            title, artist = self._tag_cache.get(p, ("", ""))
            if not title and not artist:
                untagged.append(p)
                key = "Unknown Album"
            else:
                key = f"{artist} — Singles" if artist else "Unknown Album"
            groups.setdefault(key, []).append(p)

        dpr = get_dpr()
        self.albums_grid.clear()
        from PyQt6.QtGui import QIcon as _QI
        placeholder = render_icon(Icon.MUSIC_NOTE, 120, self.theme["gold"], dpr)
        for name in sorted(groups.keys(), key=str.lower):
            paths = groups[name]
            if name == "Unknown Album" and untagged:
                continue   # this bucket gets filled by the worker instead
            item = QListWidgetItem(f"{name}\n({len(paths)} tracks)")
            # LAG FIX: covers decode in the background worker for cache-misses;
            # only reuse an already-cached pixmap here (zero I/O on the UI
            # thread — the old code decoded up to 4 covers per album here).
            cover_pm = _ca.get_cached_pixmap(paths[0])
            if cover_pm is None:
                cover_pm = placeholder
            item.setIcon(_QI(cover_pm.scaled(
                130, 130, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)))
            item.setData(Qt.ItemDataRole.UserRole, paths[0])
            item.setToolTip("\n".join(os.path.basename(x) for x in paths[:8]))
            self.albums_grid.addItem(item)

        if not untagged:
            return

        # Background pass — resolve album tags + decode one cover per album
        # off the UI thread; chunks stream into the grid as they complete.
        # A startup preload may still be running — reuse it instead of
        # doubling the work; its results land in the same grid.
        worker = getattr(self, "_albums_worker", None)
        if worker is not None and worker.isRunning():
            return
        self._albums_worker = AlbumsWorkerThread(untagged, self)
        self._albums_worker.album_group_ready.connect(
            self._on_album_group_ready)
        self._albums_worker.finished.connect(
            lambda: self.loading_overlay.hide_loading())
        self.loading_overlay.show_loading("Loading albums")
        self._albums_worker.start()

    def _preload_albums_background(self):
        """Warm the album groups + cover cache at STARTUP (user request:
        «آلبوم‌ها در اول برنامه در پس‌زمینه لود بشن و بمونن»). Revisiting
        the Albums page afterwards is instant — everything comes from cache."""
        # A worker may already be filling the grid (user clicked Albums early)
        if getattr(self, "_albums_worker", None) is not None and \
                self._albums_worker.isRunning():
            return
        self._albums_worker = AlbumsWorkerThread(list(self.library), self)
        self._albums_worker.album_group_ready.connect(
            self._on_album_group_ready)   # grid updates live if visible
        self._albums_worker.start()

    def _on_album_group_ready(self, name: str, paths: list, cover_data):
        """Worker result: one album group — append it to the grid (or warm
        the caches during a background preload)."""
        if not self.albums_grid.isVisible():
            return
        dpr = get_dpr()
        item = QListWidgetItem(f"{name}\n({len(paths)} tracks)")
        if cover_data:
            pm = coverart.bytes_to_pixmap(cover_data)
            if pm.isNull():
                pm = None
        else:
            pm = None
        if pm is None:
            pm = render_icon(Icon.MUSIC_NOTE, 120, self.theme["gold"], dpr)
        from PyQt6.QtGui import QIcon as _QI
        item.setIcon(_QI(pm.scaled(
            130, 130, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)))
        item.setData(Qt.ItemDataRole.UserRole, paths[0])
        item.setToolTip("\n".join(os.path.basename(x) for x in paths[:8]))
        self.albums_grid.addItem(item)

    def _on_album_clicked(self, item):
        """Double-click an album: filter the library list to its tracks."""
        first = item.data(Qt.ItemDataRole.UserRole)
        if not first:
            return
        # Re-derive the album group of this representative track — the album
        # page groups by folder, so every track of the same folder belongs.
        target_dir = os.path.dirname(first)
        tracks = [p for p in self.library if path_within(p, target_dir)]
        if first not in tracks:
            tracks.append(first)
        self._play_from_list(tracks, tracks.index(first),
                             view=View.LIBRARY)

    # ------------------------------------------------------------------
    # Stats page
    # ------------------------------------------------------------------
    def _build_stats_page(self) -> QWidget:
        """Listening stats — redesigned: stat cards on top, a "Most Played"
        list with real album covers, and a compact top-artists strip."""
        from PyQt6.QtWidgets import QScrollArea
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(8, 4, 8, 8)
        outer.setSpacing(14)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent;")
        body = QWidget()
        body.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(16, 8, 16, 16)
        lay.setSpacing(16)

        # ---- Stat cards row (total / plays / artists / sessions) --------
        cards_row = QHBoxLayout()
        cards_row.setSpacing(12)
        self._stat_cards = {}
        for key, icon_name in (("total", Icon.TIMER), ("plays", Icon.PLAY),
                               ("artists", Icon.ALBUMS), ("sessions", Icon.STATS)):
            card = QFrame()
            card.setObjectName("TrackList")   # glass panel look
            card.setFixedHeight(92)
            cl = QHBoxLayout(card)
            cl.setContentsMargins(16, 12, 16, 12)
            cl.setSpacing(12)
            dpr = get_dpr()
            ic = QLabel()
            ic.setPixmap(render_icon(icon_name, 30, self.theme["gold"], dpr))
            ic.setFixedWidth(34)
            ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cl.addWidget(ic)
            col = QVBoxLayout()
            col.setSpacing(2)
            val = QLabel("—")
            val.setObjectName("NowTitle")
            val.setStyleSheet("font-size: 19px;")
            val.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            sub = QLabel("")
            sub.setStyleSheet(f"color: {self.theme['muted']}; font-size: 11px;")
            col.addWidget(val)
            col.addWidget(sub)
            cl.addLayout(col, 1)
            cards_row.addWidget(card, 1)
            self._stat_cards[key] = (val, sub, ic)
        lay.addLayout(cards_row)

        # ---- Most played: real rows with per-track play buttons ----------
        mp_card = QFrame()
        mp_card.setObjectName("TrackList")
        mp_lay = QVBoxLayout(mp_card)
        mp_lay.setContentsMargins(16, 14, 16, 14)
        mp_lay.setSpacing(8)
        mp_head = QLabel("Most Played Tracks")
        mp_head.setObjectName("PageTitle")
        mp_head.setStyleSheet("font-size: 15px;")
        mp_lay.addWidget(mp_head)
        # A vertical list of StatTrackRow widgets — each row: medal, cover,
        # title/artist, play count, and a ▶ button that plays that track
        # (double-click on the row plays it too).
        self._stat_tracks_area = QWidget()
        self._stat_tracks_area.setStyleSheet("background: transparent;")
        self._stat_tracks_lay = QVBoxLayout(self._stat_tracks_area)
        self._stat_tracks_lay.setContentsMargins(0, 0, 0, 0)
        self._stat_tracks_lay.setSpacing(4)
        mp_lay.addWidget(self._stat_tracks_area)
        self.stat_top_tracks = QLabel("")   # empty-state message lives here
        self.stat_top_tracks.setWordWrap(True)
        mp_lay.addWidget(self.stat_top_tracks)
        lay.addWidget(mp_card)

        # ---- Listening activity chart card --------------------------------
        ch_card = QFrame()
        ch_card.setObjectName("TrackList")
        ch_lay = QVBoxLayout(ch_card)
        ch_lay.setContentsMargins(16, 14, 16, 14)
        ch_lay.setSpacing(8)
        ch_head = QLabel("Top Tracks Chart")
        ch_head.setObjectName("PageTitle")
        ch_head.setStyleSheet("font-size: 15px;")
        ch_lay.addWidget(ch_head)
        self._stats_chart = StatsChartBar(t := self.theme)
        ch_lay.addWidget(self._stats_chart)
        lay.addWidget(ch_card)

        # ---- Top artists strip -------------------------------------------
        ar_card = QFrame()
        ar_card.setObjectName("TrackList")
        ar_lay = QVBoxLayout(ar_card)
        ar_lay.setContentsMargins(16, 14, 16, 14)
        ar_lay.setSpacing(8)
        ar_head = QLabel("Top Artists")
        ar_head.setObjectName("PageTitle")
        ar_head.setStyleSheet("font-size: 15px;")
        ar_lay.addWidget(ar_head)
        self.stat_artists_label = QLabel("")
        self.stat_artists_label.setWordWrap(True)
        ar_lay.addWidget(self.stat_artists_label)
        self.stat_misc = QLabel("")
        self.stat_misc.setStyleSheet(f"color: {self.theme['muted']}; font-size: 11px;")
        ar_lay.addWidget(self.stat_misc)
        lay.addWidget(ar_card)

        row = QHBoxLayout()
        row.addStretch(1)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.setObjectName("GhostBtn")
        refresh_btn.clicked.connect(self._refresh_stats_view)
        row.addWidget(refresh_btn)
        lay.addLayout(row)
        lay.addStretch(1)

        scroll.setWidget(body)
        outer.addWidget(scroll)
        self._stats_built = True
        return page

    def _refresh_stats_view(self):
        if not getattr(self, "stats", None) or not getattr(self, "_stats_built", False):
            return
        from playstats import PlayStats
        t = self.theme
        dpr = get_dpr()

        def _set_card(key, value, subtitle):
            val, sub, ic = self._stat_cards[key]
            val.setText(value)
            sub.setText(subtitle)
            icon_name = {"total": Icon.TIMER, "plays": Icon.PLAY,
                         "artists": Icon.ALBUMS, "sessions": Icon.STATS}[key]
            ic.setPixmap(render_icon(icon_name, 30, t["gold"], dpr))

        total = self.stats.total_seconds
        plays = sum(self.stats.play_counts.values())
        artists_n = len(self.stats.artist_seconds)
        _set_card("total", PlayStats.fmt_total(total), "total listening time")
        _set_card("plays", f"{plays:,}", "tracks played")
        _set_card("artists", f"{artists_n:,}", "artists heard")
        _set_card("sessions", f"{self.stats.sessions:,}", "sessions opened")

        # Most played — real interactive rows: cover + play button per track.
        # Paths ride along from top_tracks() so covers resolve DIRECTLY
        # (the old title-based lookup missed files with duplicate names).
        # Rebuilt only when the ranking changed (button mashing on Refresh
        # used to rebuild identical rows every click).
        tops = self.stats.top_tracks(self._tag_cache, 10)
        sig = tuple(tops)
        if sig == getattr(self, "_stats_rows_sig", None):
            return
        self._stats_rows_sig = sig
        while self._stat_tracks_lay.count():
            item = self._stat_tracks_lay.takeAt(0)
            wd = item.widget()
            if wd is not None:
                wd.deleteLater()
        if tops:
            self.stat_top_tracks.setVisible(False)
            for i, (title, artist, cnt, path) in enumerate(tops, 1):
                # Cover: warm cache first (instant); a cold cache reads the
                # file once — stats refresh is user-triggered and bounded
                # to 10 rows, so the small synchronous cost is acceptable
                # (and results land in the shared cover cache).
                cover_pm = coverart.get_cached_pixmap(path)
                if cover_pm is None:
                    data = coverart.get_cover_bytes(path)
                    if data:
                        cover_pm = coverart.bytes_to_pixmap(data)
                row = StatTrackRow(i, path, title, artist, cnt, cover_pm,
                                   self.theme, self._play_from_stats)
                self._stat_tracks_lay.addWidget(row)
            self._stat_tracks_area.setVisible(True)
            # Animated top-tracks bar chart
            self._stats_chart.set_theme(self.theme)
            self._stats_chart.set_data(
                [(title, cnt) for title, _a, cnt, _p in tops[:7]])
        else:
            self._stat_tracks_area.setVisible(False)
            self.stat_top_tracks.setVisible(True)
            self.stat_top_tracks.setText(
                f"<span style='color:{t['muted']};'>Nothing yet — play something!</span>")
            self._stats_chart.set_data([])

        artists = self.stats.top_artists(5)
        if artists:
            rows = []
            for rank, (a, dur) in enumerate(artists, 1):
                bar_pct = max(8, int(100 - rank * 16))
                rows.append(
                    f"<table width='100%' cellspacing='0'><tr>"
                    f"<td width='18' style='color:{t['muted']};'>{rank}</td>"
                    f"<td style='color:{t['text']};'>{self._esc_html(a)}</td>"
                    f"<td width='90'>"
                    f"<table width='{bar_pct}%'><tr><td style='background-color:"
                    f"{t['gold']};'>&nbsp;</td></tr></table></td>"
                    f"<td align='right' width='80' style='color:{t['muted']};'>"
                    f"{dur}</td></tr></table>")
            self.stat_artists_label.setText(
                "<div style='line-height:150%;'>" + "".join(rows) + "</div>")
        else:
            self.stat_artists_label.setText("")
        self.stat_misc.setText(
            ("Listening since " + self.stats.first_played)
            if self.stats.first_played else "")

    @staticmethod
    def _esc_html(s: str) -> str:
        return (s.replace("&", "&amp;").replace("<", "&lt;")
                 .replace(">", "&gt;"))

    def _play_from_stats(self, path: str):
        """▶ on a stats row: play this track with the library as the queue
        (proper next/prev context), like clicking it in the library list."""
        if not path:
            # Stale stats entry (file removed from library, cache not yet
            # rebuilt) — silently ignore instead of nagging the user.
            log.info("stats row has no resolvable track path; ignoring play")
            return
        if not os.path.exists(path):
            QMessageBox.information(self, APP_NAME,
                "This track is no longer in the library.")
            return
        if path not in self.library:
            self._play_path_from_playlist(path)
            return
        self._on_rail_clicked("library")
        self._play_from_list(list(self.library),
                             max(0, self.library.index(path)),
                             view=View.LIBRARY)

    def _build_tray(self):
        icon_path = Path(__file__).parent / "assets" / "icon.png"
        tray_icon = QIcon(str(icon_path)) if icon_path.exists() else QIcon()
        self.tray = QSystemTrayIcon(tray_icon, self)
        self.tray.setToolTip(APP_NAME)
        # Custom popup tray menu (two rows of icon buttons)
        self.tray_menu_widget = TrayMenuWidget(self.theme, self)
        self.tray_menu_widget.prev_clicked.connect(self._on_prev)
        self.tray_menu_widget.play_clicked.connect(self._on_play_pause)
        self.tray_menu_widget.next_clicked.connect(self._on_next)
        self.tray_menu_widget.show_clicked.connect(self._toggle_visible)
        self.tray_menu_widget.close_clicked.connect(self.tray_menu_widget.close)
        self.tray_menu_widget.quit_clicked.connect(self._quit)
        # Right-click opens the CUSTOM styled popup (not the gray native
        # QMenu — Windows would render that with the old system theme).
        # Left/middle clicks keep their behaviors in _on_tray_activated.
        self.tray.setContextMenu(None)
        self.tray.activated.connect(self._on_tray_activated)

    def _setup_shortcuts(self):
        # Space / Enter: play-pause
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, activated=self._on_play_pause)
        QShortcut(QKeySequence(Qt.Key.Key_Return), self, activated=self._on_play_pause)
        QShortcut(QKeySequence(Qt.Key.Key_Enter), self, activated=self._on_play_pause)
        # Left/Right arrows: previous / next track
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, activated=self._on_prev)
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, activated=self._on_next)
        # Up/Down arrows: volume — but let the focused list consume them for
        # row navigation first (context Qt.WindowShortcut fires after the
        # widget's own handling only when the list does not accept the key).
        up = QShortcut(QKeySequence(Qt.Key.Key_Up), self)
        up.setContext(Qt.ShortcutContext.WindowShortcut)
        up.activated.connect(self._on_arrow_up)
        down = QShortcut(QKeySequence(Qt.Key.Key_Down), self)
        down.setContext(Qt.ShortcutContext.WindowShortcut)
        down.activated.connect(self._on_arrow_down)
        # M: mute toggle
        QShortcut(QKeySequence(Qt.Key.Key_M), self, activated=self._on_mute_toggle)
        # F11 / F: fullscreen player
        QShortcut(QKeySequence(Qt.Key.Key_F11), self, activated=self._toggle_fullscreen_player)
        QShortcut(QKeySequence(Qt.Key.Key_F), self, activated=self._toggle_fullscreen_player)
        # Ctrl+F: focus search (plain F is fullscreen, so search keeps Ctrl)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=lambda: self.search_edit.setFocus())
        # Ctrl+M: mini player
        QShortcut(QKeySequence("Ctrl+M"), self, activated=self._toggle_mini_player)
        # Ctrl+J: jump to playing track
        QShortcut(QKeySequence("Ctrl+J"), self, activated=self._jump_to_playing)
        # Escape: leave the fullscreen player
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self._exit_fullscreen_player)

    # ------------------------------------------------------------------
    # Fullscreen player (F11 / F to toggle, Esc to exit)
    # ------------------------------------------------------------------
    def _toggle_fullscreen_player(self):
        if getattr(self, "_fullscreen_player", None) is not None and \
                self._fullscreen_player.isVisible():
            self._exit_fullscreen_player()
            return
        self._fullscreen_player = getattr(self, "_fullscreen_player", None)
        if self._fullscreen_player is None:
            from fullscreen_player import FullscreenPlayer
            self._fullscreen_player = FullscreenPlayer(self)
        self._fullscreen_player._apply_theme()
        self._fullscreen_player.refresh_state()
        self._fullscreen_player.show()
        self._fullscreen_player.raise_()
        self._fullscreen_player.activateWindow()

    def _exit_fullscreen_player(self):
        fs = getattr(self, "_fullscreen_player", None)
        if fs is not None and fs.isVisible():
            fs.close()

    def _on_arrow_up(self):
        """Up arrow: move the list selection when a list owns focus,
        otherwise raise the volume."""
        lst = self._focused_track_list()
        if lst is not None:
            row = max(0, lst.currentRow() - 1)
            lst.setCurrentRow(row)
            return
        self._change_volume(5)

    def _on_arrow_down(self):
        """Down arrow: move the list selection when a list owns focus,
        otherwise lower the volume."""
        lst = self._focused_track_list()
        if lst is not None:
            row = min(lst.count() - 1, lst.currentRow() + 1)
            lst.setCurrentRow(row)
            return
        self._change_volume(-5)

    def _focused_track_list(self):
        """The track list currently holding keyboard focus, if any."""
        fw = QApplication.focusWidget()
        if fw in (self.library_list, self.favorites_list):
            return fw
        return None

    def _change_volume(self, delta):
        v = self.player_bar.vol_slider.value() + delta
        v = max(0, min(100, v))
        self.player_bar.vol_slider.setValue(v)

    def _early_load_theme(self):
        """Synchronously read just the theme key from the config file.
        Tiny local-JSON read (~µs) that lets _build_ui() paint with the
        user's saved theme instead of the DEFAULT dark one — the root fix
        for light themes starting life with stale dark-theme inline colors."""
        try:
            cfg, ok = load_json_file(config_path(), "config")
            if ok and isinstance(cfg, dict) and cfg.get("theme"):
                self.theme_name = Theme.normalize(str(cfg["theme"]))
                self.theme = Theme.get(self.theme_name)
        except Exception as e:
            log.debug(f"early theme load skipped: {e}")

    _THEME_COLOR_KEYS = ("window_bg", "panel_bg", "panel_bg_2", "border",
                         "text", "muted", "gold", "gold_light", "gold_deep",
                         "active")

    def _sweep_inline_theme_styles(self):
        """Rewrite inline stylesheets that still carry the PREVIOUS theme's
        color tokens (stats labels, hints, custom panels...).  _apply_theme
        can only explicitly re-style a handful of widgets; everything styled
        with an f-string at construction time is caught by this sweep."""
        old = getattr(self, "_last_theme", None)
        if not old or old.get("name") == self.theme.get("name"):
            return
        new = self.theme
        mapping = [(old[k], new[k]) for k in self._THEME_COLOR_KEYS
                   if old.get(k) and old.get(k) != new.get(k)]
        if not mapping:
            return
        for w in self.findChildren(QWidget):
            ss = w.styleSheet()
            if not ss:
                continue
            updated = ss
            for old_c, new_c in mapping:
                if old_c in updated:
                    updated = updated.replace(old_c, new_c)
            if updated != ss:
                w.setStyleSheet(updated)

    def _sync_app_palette(self):
        """Mirror the active theme into the QApplication palette and force
        the matching Qt color scheme.  Without this, Fusion auto-follows the
        OS dark/light setting: on a Windows machine in dark mode, every
        unstyled area (tab strips, scroll viewports, tooltips, message boxes)
        renders in dark palette colors on top of a light theme."""
        app = QApplication.instance()
        if app is None:
            return
        t = self.theme
        is_dark = t.get("is_dark", True)
        pal = app.palette()
        roles = {
            QPalette.ColorRole.Window: t["window_bg"],
            QPalette.ColorRole.WindowText: t["text"],
            QPalette.ColorRole.Base: t["panel_bg"],
            QPalette.ColorRole.AlternateBase: t["panel_bg_2"],
            QPalette.ColorRole.Text: t["text"],
            QPalette.ColorRole.Button: t["panel_bg_2"],
            QPalette.ColorRole.ButtonText: t["text"],
            QPalette.ColorRole.ToolTipBase: t["panel_bg_2"],
            QPalette.ColorRole.ToolTipText: t["text"],
            QPalette.ColorRole.Highlight: t["gold"],
            QPalette.ColorRole.HighlightedText: ("#ffffff" if not is_dark
                                                 else t["window_bg"]),
            QPalette.ColorRole.Link: t["gold"],
            QPalette.ColorRole.PlaceholderText: t["muted"],
        }
        for role, color in roles.items():
            pal.setColor(role, QColor(color))
        # Disabled controls: muted text so they stay legible but dimmed.
        for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                     QPalette.ColorRole.ButtonText):
            pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(t["muted"]))
        app.setPalette(pal)
        # Stop Fusion from auto-following the OS color scheme (Qt 6.5+).
        try:
            app.styleHints().setColorScheme(
                Qt.ColorScheme.Dark if is_dark else Qt.ColorScheme.Light)
        except Exception:
            pass

    def _apply_theme(self):
        self.theme = Theme.get(self.theme_name)
        theme_changed = (getattr(self, "_last_theme", None) or {}).get("name") \
            != self.theme.get("name")
        qss = build_qss(self.theme)
        # Apply at BOTH levels: on the window for its children (as before)
        # and on the application so top-level popups that are not QObject
        # children of the window — QTipLabel tooltips above all — inherit
        # the themed QToolTip/QMenu rules instead of the native palette.
        app_inst = QApplication.instance()
        if app_inst is not None:
            app_inst.setStyleSheet(qss)
        self.setStyleSheet(qss)
        self._sync_app_palette()
        if hasattr(self, "rail"):
            self.rail.set_theme(self.theme)
        if hasattr(self, "_track_delegate"):
            self._track_delegate.set_theme(self.theme)
        if hasattr(self, "_fav_delegate"):
            self._fav_delegate.set_theme(self.theme)
        if hasattr(self, "player_bar"):
            self.player_bar.set_theme(self.theme)
        # Tray popup follows the theme too (dividers, accent buttons,
        # header, glass background) — previously it kept its boot colors.
        tmw = getattr(self, "tray_menu_widget", None)
        if tmw is not None:
            tmw.set_theme(self.theme)
        for cover in ("cover_label", "fav_cover"):
            if hasattr(self, cover):
                getattr(self, cover).set_theme(self.theme)
        if hasattr(self, "scan_status_label"):
            self.scan_status_label.setStyleSheet(f"color: {self.theme['gold']};")
        if hasattr(self, "folder_tree"):
            self.folder_tree.setStyleSheet(
                f"QTreeWidget {{ background: {self.theme['panel_bg']}; border: 1px solid {self.theme['border']}; border-radius: 10px; padding: 6px; outline: 0; }}"
                f" QTreeWidget::item {{ padding: 8px 6px; border-radius: 8px; margin: 1px 2px; outline: 0; background: transparent; }}"
                f" QTreeWidget::item:hover {{ background: {self.theme['panel_bg_2']}; outline: 0; }}"
                f" QTreeWidget::item:selected {{ background: transparent; color: {self.theme['gold_light']}; outline: 0; border: none; }}"
                f" QTreeWidget::item:focus {{ background: transparent; outline: 0; border: none; }}"
            )
            if theme_changed:
                self._rebuild_folder_tree()
        # Rebuild tray menu with new theme colors
        if hasattr(self, "tray") and self.tray is not None:
            self._rebuild_tray_menu()
        if hasattr(self, "library_list") and theme_changed:
            self._highlight_playing_in_lists()
        if theme_changed:
            # Catch every remaining widget whose inline stylesheet was baked
            # with the previous theme's colors. The sweep walks EVERY widget
            # (~500 on a loaded window) — skip it entirely for same-theme
            # re-applies (startup called it twice, wasting ~200 ms each).
            self._sweep_inline_theme_styles()
            # Stats rows carry per-instance styles the sweep can't guess —
            # re-theme them explicitly.
            rows_lay = getattr(self, "_stat_tracks_lay", None)
            if rows_lay is not None:
                for i in range(rows_lay.count()):
                    row = rows_lay.itemAt(i).widget()
                    if isinstance(row, StatTrackRow):
                        row.set_theme(self.theme)
            chart = getattr(self, "_stats_chart", None)
            if chart is not None:
                chart.set_theme(self.theme)
        # Snapshot for the next sweep.
        self._last_theme = dict(self.theme)

    def _rebuild_tray_menu(self):
        """The custom tray popup replaces the old native QMenu; just
        re-theme it when the palette changes."""
        if getattr(self, "tray_menu_widget", None) is not None:
            self.tray_menu_widget._apply_theme(self.theme)

    def _jump_to_playing(self):
        """Scroll to and select the currently playing track in its list."""
        if self.current_track is None:
            QMessageBox.information(self, APP_NAME, "Nothing is playing right now.")
            return
        # Switch to the view that contains the track
        target = self.favorites_list if self.current_track in self.favorites else self.library_list
        if self.current_view == View.FAVORITES and self.current_track not in self.favorites:
            # Playing from library while viewing favorites -> jump to library
            pass
        lst = None
        for candidate in (self.library_list, self.favorites_list):
            for i in range(candidate.count()):
                if candidate.item(i).data(Qt.ItemDataRole.UserRole) == self.current_track:
                    lst = candidate
                    break
            if lst:
                break
        if lst is None:
            QMessageBox.information(self, APP_NAME,
                "The playing track is not in the visible lists (it may have been removed).")
            return
        if lst is self.library_list and self.current_view != View.LIBRARY:
            self._on_rail_clicked("library")
        elif lst is self.favorites_list and self.current_view != View.FAVORITES:
            self._on_rail_clicked("favorites")
        for i in range(lst.count()):
            it = lst.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == self.current_track:
                lst.setCurrentRow(i)
                lst.scrollToItem(it, QListWidget.ScrollHint.PositionAtCenter)
                break

    def _on_rail_clicked(self, key: str):
        # Rail exclusivity: only the active nav button stays checked.
        # setChecked alone doesn't always repaint the QSS :checked state when
        # the mouse is still over the old button — force a style refresh.
        nav = {"library": self.rail.btn_library,
               "favorites": self.rail.btn_favorites,
               "albums": self.rail.btn_albums,
               "stats": self.rail.btn_stats}
        for k, btn in nav.items():
            if btn.isChecked() != (k == key):
                btn.setChecked(k == key)
                btn.update_icon()
                btn.style().unpolish(btn)
                btn.style().polish(btn)
        if key == "library":
            self.current_view = View.LIBRARY
            self.stack.setCurrentIndex(0)
            self.page_title.setText("Library")
            self.rail.btn_library.setChecked(True)
            self._update_count_label()
        elif key == "favorites":
            self.current_view = View.FAVORITES
            self.stack.setCurrentIndex(1)
            self.page_title.setText("Favorites")
            self.rail.btn_favorites.setChecked(True)
            self._update_count_label()
        elif key == "refresh":
            self._on_refresh()
        elif key == "addfolder":
            self._on_add_folder()
        elif key == "theme":
            self._show_theme_picker()
        elif key == "settings":
            self._show_settings()
        elif key == "albums":
            self.current_view = View.LIBRARY
            self.page_title.setText("Albums")
            self.rail.btn_albums.setChecked(True)
            self.stack.setCurrentIndex(2)
            # Grid builds from the tag cache instantly; untagged tracks are
            # grouped by the background worker (spinner until done).
            self._rebuild_albums_grid()
        elif key == "stats":
            self.stack.setCurrentIndex(3)
            self.page_title.setText("Listening Stats")
            self.rail.btn_stats.setChecked(True)
            self._refresh_stats_view()
        elif key == "playlists":
            self._show_playlists()
        elif key == "mini":
            self._toggle_mini_player()

    def _show_playlists(self):
        try:
            from playlist_dialog import PlaylistDialog
            dlg = PlaylistDialog(self, self)
            dlg.exec()
        except Exception as e:
            log.error(f"Playlist dialog failed: {e}")

    def sort_playlists_names(self):
        """Playlist names sorted case-insensitively (used by the dialog)."""
        return sorted(self.playlists.keys(), key=str.lower)

    def _show_theme_picker(self):
        """Show the theme picker popup, fully clamped inside the screen."""
        self.theme_popup = ThemePickerPopup(self.theme_name, self)
        self.theme_popup.theme_selected.connect(self._on_theme_selected)
        # Anchor near the theme button but clamp inside the available
        # screen area so the popup never spills off-monitor.
        btn = self.rail.btn_theme
        btn_pos = btn.mapToGlobal(QPoint(0, 0))
        screen_geo = QApplication.screenAt(
            QPoint(btn_pos.x() + btn.width() // 2,
                   btn_pos.y() + btn.height() // 2))
        if screen_geo is None:
            screen_geo = QApplication.primaryScreen().availableGeometry()
        else:
            screen_geo = screen_geo.availableGeometry()
        pw, ph = self.theme_popup.width(), self.theme_popup.height()
        # Prefer opening to the right of the sidebar, vertically centered
        x = max(screen_geo.left() + 8,
                min(btn_pos.x() + btn.width() + 12,
                    screen_geo.right() - pw - 8))
        y = max(screen_geo.top() + 8,
                min(btn_pos.y() - ph // 2,
                    screen_geo.bottom() - ph - 8))
        self.theme_popup.move(x, y)
        self.theme_popup.show()
        self.theme_popup.raise_()
        self.theme_popup.activateWindow()

    def _on_theme_selected(self, name: str):
        self.theme_name = name
        self._apply_theme()
        self._save_config()

    def _show_settings(self):
        dlg = SettingsDialog(self, self)
        dlg.exec()

    def _toggle_mini_player(self):
        if self.mini_player is None or not self.mini_player.isVisible():
            if self.mini_player is None:
                self.mini_player = MiniPlayer(self)
            self.mini_player._apply_theme()
            if self.current_track:
                title, artist = extract_title_artist(self.current_track)
                # Get current cover
                pm = self.player_bar._cover_thumb_pm
                self.mini_player.update_track(title, artist, pm)
            state = self.audio.state()
            self.mini_player.update_play_state(state == AudioBackend.STATE_PLAYING)
            self.mini_player.show()
            self.mini_player.move(self.geometry().center().x() - 160, self.geometry().bottom() - 120)
            self.hide()
        else:
            self.mini_player.hide()
            self.show()
            self.raise_()
            self.activateWindow()

    def _update_count_label(self):
        if self.current_view == View.LIBRARY:
            n = len(self._get_filtered_sorted_library())
            ff = getattr(self, "_folder_filter", None)
            total = len(self._folder_tracks(ff)) if ff else len(self.library)
            if n != total:
                suffix = " in folder" if ff else ""
                self.count_label.setText(f"{n} of {total} track{'' if total == 1 else 's'}{suffix}")
            else:
                suffix = " in folder" if ff else ""
                self.count_label.setText(f"{n} track{'' if n != 1 else 's'}{suffix}")
        else:
            n = len(self._get_filtered_sorted_favorites())
            total = len(self.favorites)
            if n != total:
                self.count_label.setText(f"{n} of {total} favorites")
            else:
                self.count_label.setText(f"{n} favorite{'s' if n != 1 else ''}")

    def _on_search_changed(self, text):
        # Debounced: rebuilding a 3k-track list on every keystroke stuttered
        # typing; 180 ms after the last keypress is instant-feeling but
        # coalesces bursts of keys.
        self.search_filter = text.lower().strip()
        if self._search_debounce is None:
            self._search_debounce = QTimer(self)
            self._search_debounce.setSingleShot(True)
            self._search_debounce.setInterval(180)
            self._search_debounce.timeout.connect(self._apply_search_filter)
        self._search_debounce.start()

    def _apply_search_filter(self):
        self._rebuild_library_list()
        self._rebuild_favorites_list()
        self._update_count_label()

    def _on_sort_changed(self, idx):
        mode_val = self.sort_combo.itemData(idx)
        self.sort_mode = SortMode(mode_val)
        self._rebuild_library_list()
        self._rebuild_favorites_list()

    def _get_filtered_sorted_library(self):
        tracks = list(self.library)
        # Folder filter (from the folder tree) narrows the pool first; it is
        # kept as state so a search/sort change no longer drops it.
        if getattr(self, "_folder_filter", None):
            ff = self._folder_filter
            tracks = [p for p in tracks if path_within(p, ff)]
        # Filter using cache (fast, no I/O)
        if self.search_filter:
            filtered = []
            for path in tracks:
                if path in self._tag_cache:
                    title, artist = self._tag_cache[path]
                else:
                    title = os.path.splitext(os.path.basename(path))[0]
                    artist = ""
                if self.search_filter in title.lower() or self.search_filter in artist.lower():
                    filtered.append(path)
            tracks = filtered
        # Sort using cache (fast)
        if self.sort_mode == SortMode.TITLE:
            tracks.sort(key=lambda x: (self._tag_cache.get(x, (os.path.splitext(os.path.basename(x))[0], ""))[0].lower()))
        elif self.sort_mode == SortMode.ARTIST:
            tracks.sort(key=lambda x: (self._tag_cache.get(x, ("", "Unknown"))[1].lower(),
                                        self._tag_cache.get(x, (os.path.splitext(os.path.basename(x))[0], ""))[0].lower()))
        elif self.sort_mode == SortMode.FILENAME:
            tracks.sort(key=lambda x: os.path.basename(x).lower())
        elif self.sort_mode == SortMode.DATE_ADDED:
            # getmtime is a syscall per file — cache it per library snapshot
            # instead of hitting the disk on every search keystroke.
            tracks.sort(key=lambda x: self._mtime_cache(x), reverse=True)
        return tracks

    def _mtime_cache(self, path: str) -> float:
        cache = getattr(self, "_mtime_map", None)
        if cache is None or path not in cache:
            if cache is None:
                cache = self._mtime_map = {}
                self._mtime_lib_len = 0
            # Rebuild wholesale when the library changes (cheap: one walk)
            if getattr(self, "_mtime_lib_len", 0) != len(self.library):
                cache.clear()
                for p in self.library:
                    try:
                        cache[p] = os.path.getmtime(p)
                    except OSError:
                        cache[p] = 0.0
                self._mtime_lib_len = len(self.library)
            else:
                try:
                    cache[path] = os.path.getmtime(path)
                except OSError:
                    cache[path] = 0.0
        return cache.get(path, 0.0)

    def _get_filtered_sorted_favorites(self):
        tracks = list(self.favorites)
        if self.search_filter:
            filtered = []
            for path in tracks:
                if path in self._tag_cache:
                    title, artist = self._tag_cache[path]
                else:
                    title = os.path.splitext(os.path.basename(path))[0]
                    artist = ""
                if self.search_filter in title.lower() or self.search_filter in artist.lower():
                    filtered.append(path)
            tracks = filtered
        if self.sort_mode == SortMode.TITLE:
            tracks.sort(key=lambda x: (self._tag_cache.get(x, (os.path.splitext(os.path.basename(x))[0], ""))[0].lower()))
        elif self.sort_mode == SortMode.ARTIST:
            tracks.sort(key=lambda x: (self._tag_cache.get(x, ("", "Unknown"))[1].lower(),
                                        self._tag_cache.get(x, (os.path.splitext(os.path.basename(x))[0], ""))[0].lower()))
        elif self.sort_mode == SortMode.FILENAME:
            tracks.sort(key=lambda x: os.path.basename(x).lower())
        elif self.sort_mode == SortMode.DATE_ADDED:
            tracks.sort(key=lambda x: self._mtime_cache(x), reverse=True)
        return tracks

    @staticmethod
    def _default_music_dir() -> str:
        """The OS's user music folder (Windows: <Profile>\\Music)."""
        try:
            from PyQt6.QtCore import QStandardPaths
            locs = QStandardPaths.standardLocations(
                QStandardPaths.StandardLocation.MusicLocation)
            if locs and os.path.isdir(locs[0]):
                return locs[0]
        except Exception:
            pass
        return str(Path.home() / "Music")

    def _on_add_folder(self):
        # Default to the OS music folder unless the user set one explicitly
        if self.default_folder and os.path.isdir(self.default_folder):
            start_dir = self.default_folder
        elif not self.added_folders:
            start_dir = self._default_music_dir()
        else:
            start_dir = ""
        folder = QFileDialog.getExistingDirectory(self, "Select Music Folder", start_dir)
        if not folder:
            return
        if folder in self.added_folders:
            QMessageBox.information(self, APP_NAME, "This folder is already in your library.")
            return
        self.added_folders.append(folder)
        self._start_scan([folder], label="Scanning new folder...")

    def _on_refresh(self):
        """Rescan all added folders: add new tracks, remove deleted ones."""
        if not self.added_folders:
            QMessageBox.information(self, APP_NAME, "No folders to refresh. Add a folder first.")
            return
        existing = [f for f in self.added_folders if os.path.isdir(f)]
        if not existing:
            QMessageBox.information(self, APP_NAME, "None of your added folders exist anymore.")
            return
        self._start_scan(existing, label="Refreshing library...")

    def _startup_rescan(self):
        if not self.added_folders:
            return
        existing = [f for f in self.added_folders if os.path.isdir(f)]
        if len(existing) != len(self.added_folders):
            self.added_folders = existing
        if not existing:
            return
        self._start_scan(existing, label="Checking for new tracks...")

    def _start_scan(self, folders: list, label: str = "Scanning..."):
        if self.scanner is not None and self.scanner.isRunning():
            self._scan_results_pending = folders
            return
        self.scan_status_label.setText(label)
        self.scan_status_label.setVisible(True)
        self.scan_progress.setValue(0)
        self.scan_progress.setVisible(True)
        self.rail.btn_add.setEnabled(False)
        self.rail.btn_refresh.setEnabled(False)  # no double-queue scans
        self.scanner = FolderScanner(folders, self)
        self.scanner.progress.connect(self._on_scan_progress)
        self.scanner.finished_scan.connect(self._on_scan_finished)
        self.scanner.start()

    def _on_scan_progress(self, folder: str, found: int, total: int):
        short = os.path.basename(folder) or folder
        if len(short) > 25:
            short = short[:22] + "..."
        self.scan_status_label.setText(f"Scanning: {short} — {found} found")
        if total > 0:
            pct = int(found * 100 / total)
            self.scan_progress.setValue(pct)

    def _on_scan_finished(self, found: list):
        existing = set(self.library)
        added = 0
        for p in found:
            if p not in existing:
                self.library.append(p)
                existing.add(p)
                added += 1
        before = len(self.library)
        self.library = [p for p in self.library if os.path.exists(p)]
        removed = before - len(self.library)
        self.library.sort(key=lambda x: os.path.basename(x).lower())
        self.favorites = {p for p in self.favorites if os.path.exists(p)}
        self.scan_progress.setVisible(False)
        self.scan_status_label.setVisible(False)
        self.rail.btn_add.setEnabled(True)
        self.rail.btn_refresh.setEnabled(True)
        self._rebuild_library_list(rebuild_tree=True)
        self._rebuild_favorites_list()
        self._update_count_label()
        try:
            from playstats import set_search_roots
            if getattr(self, "stats", None) is not None:
                _roots = list(self.added_folders or [])
                _roots += list({os.path.dirname(os.path.abspath(p))
                                for p in self.library if p})
                set_search_roots(_roots)
                self.stats.prune_missing(set(self.library))
        except Exception:
            pass
        self._save_config()
        if added > 0 or removed > 0:
            msg = []
            if added:
                msg.append(f"Added {added} new track(s)")
            if removed:
                msg.append(f"Removed {removed} missing track(s)")
            if self.tray and self.tray.isVisible() and not self.isActiveWindow():
                self.tray.showMessage(APP_NAME, " · ".join(msg) + f" (total: {len(self.library)})",
                    QSystemTrayIcon.MessageIcon.Information, 2000)
        if self._scan_results_pending is not None:
            nxt = self._scan_results_pending
            self._scan_results_pending = None
            self._start_scan(nxt)

    def _rebuild_library_list(self, rebuild_tree: bool = False):
        """Rebuild the library list FAST — just filenames, no tag reading.
        Tags are loaded in background by _start_tag_loader.

        `rebuild_tree`: the folder tree mirrors added_folders, not the
        library list — rebuilding it here (a recursive walk of the whole
        music collection) on every search keystroke was a major lag source.
        Callers that actually change folders pass True.
        """
        self.library_list.clear()
        tracks = self._get_filtered_sorted_library()
        for i, path in enumerate(tracks):
            # Use cached tags when available; fall back to filename (no I/O)
            if path in self._tag_cache:
                title, artist = self._tag_cache[path]
            else:
                title = os.path.splitext(os.path.basename(path))[0]
                artist = ""
            item = QListWidgetItem(_track_item_text(title, artist))
            item.setData(TrackRowDelegate.TITLE_ROLE, i + 1)
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(f"{title}\n{artist}\n{path}" if artist else path)
            self.library_list.addItem(item)
        self._highlight_playing_in_lists()
        # Reflect the active folder filter in the count label (search count
        # stays as-is; a folder filter deserves the same clarity).
        self._update_count_label()
        if rebuild_tree:
            self._rebuild_folder_tree()
        # Start background tag loading
        self._start_tag_loader(tracks)

    _tag_loader = None
    _tag_cache = {}
    _tag_loader_generation = 0   # bumped per loader; stale results are dropped

    def _start_tag_loader(self, tracks: list):
        """Load title/artist tags in background thread."""
        # Cancel previous loader
        if self._tag_loader is not None:
            self._tag_loader.cancel()
            self._tag_loader.wait(500)

        # Filter out cached tracks
        uncached = [p for p in tracks if p not in self._tag_cache]
        if not uncached:
            # All cached — update items immediately
            self._update_list_items_with_tags()
            return

        self._tag_loader_generation += 1
        self._tag_loader = TagLoaderThread(uncached, self)
        self._tag_loader.generation = self._tag_loader_generation
        self._tag_loader.tag_loaded.connect(self._on_tag_loaded)
        self._tag_loader.start()

    def _on_tag_loaded(self, path: str, title: str, artist: str):
        """Called when a tag is loaded in background."""
        # Drop results from a cancelled loader: a slow mutagen read on an old
        # thread must never overwrite a fresher value written after an edit.
        loader = self._tag_loader
        if loader is not None and getattr(loader, "generation", None) != self._tag_loader_generation:
            return
        self._tag_cache[path] = (title, artist)
        # Update the corresponding item in the list
        for lst in (self.library_list, self.favorites_list):
            for i in range(lst.count()):
                item = lst.item(i)
                if item.data(Qt.ItemDataRole.UserRole) == path:
                    item.setText(_track_item_text(title, artist))
                    item.setToolTip(f"{title}\n{artist}\n{path}")
                    lst.viewport().update()
                    break

    def _update_list_items_with_tags(self):
        """Update all list items with cached tags (fast, no I/O)."""
        for i in range(self.library_list.count()):
            item = self.library_list.item(i)
            path = item.data(Qt.ItemDataRole.UserRole)
            if path in self._tag_cache:
                title, artist = self._tag_cache[path]
                item.setText(_track_item_text(title, artist))
                item.setToolTip(f"{title}\n{artist}\n{path}")
        self.library_list.viewport().update()

    def _save_tag_cache(self):
        """Save tag cache to disk for instant loading on next startup."""
        try:
            p = config_path().parent / "tag_cache.json"
            cache_data = {path: list(tags) for path, tags in self._tag_cache.items()}
            p.write_text(json.dumps(cache_data, ensure_ascii=False), encoding="utf-8")
            restrict_file_permissions(p)
        except Exception as e:
            log.error(f"Tag cache save error: {e}")

    def _load_tag_cache(self):
        """Load tag cache from disk (instant — no I/O for tag reading).

        Validated strictly — a corrupted/tampered cache must never crash the
        app or blow up memory at startup.
        """
        self._tag_cache = load_tag_cache_file(config_path().parent / "tag_cache.json")
        return bool(self._tag_cache)

    def _rebuild_folder_tree(self):
        """Build a tree of added folders and their subfolders.

        LAG FIX: subfolders load LAZILY — each branch gets a single
        placeholder child and real children are built on first expand
        (itemExpanded). Walking 1400+ directories and creating 1400+
        QTreeWidgetItems at once froze every search keystroke and scan.
        """
        if not hasattr(self, 'folder_tree'):
            return
        self.folder_tree.clear()
        dpr = get_dpr()
        folder_icon = QIcon(render_icon(Icon.FOLDER, 18, self.theme["gold"], dpr))
        add_icon = QIcon(render_icon(Icon.FOLDER_ADD, 18, self.theme["gold_light"], dpr))
        all_icon = QIcon(render_icon(Icon.LIBRARY, 18, self.theme["gold"], dpr))

        # "All Tracks" root item
        all_item = QTreeWidgetItem(["All Tracks"])
        all_item.setIcon(0, all_icon)
        all_item.setData(0, Qt.ItemDataRole.UserRole, "__all__")
        all_item.setFont(0, self._bold_font())
        self.folder_tree.addTopLevelItem(all_item)

        # "Add Folder..." action item
        add_item = QTreeWidgetItem(["Add Folder..."])
        add_item.setIcon(0, add_icon)
        add_item.setData(0, Qt.ItemDataRole.UserRole, "__add__")
        add_item.setForeground(0, QColor(self.theme["gold_light"]))
        self.folder_tree.addTopLevelItem(add_item)

        # User-added folders — children attach lazily on expand.
        for folder_path in self.added_folders:
            if not os.path.isdir(folder_path):
                continue
            folder_name = os.path.basename(folder_path) or folder_path
            root_item = QTreeWidgetItem([folder_name])
            root_item.setIcon(0, folder_icon)
            root_item.setData(0, Qt.ItemDataRole.UserRole, folder_path)
            root_item.setToolTip(0, folder_path)
            if _dir_has_subdirs(folder_path):
                root_item.addChild(QTreeWidgetItem(["…"]))   # lazy marker
            self.folder_tree.addTopLevelItem(root_item)
        # first-level folders stay collapsed (expandAll would force the
        # full lazy build — exactly what we avoid)

    def _on_folder_tree_expanded(self, item):
        """Replace the lazy marker with the branch's real subfolders."""
        if item.childCount() != 1 or item.child(0).text(0) != "…":
            return   # already materialized (or childless)
        item.takeChildren()
        dpr = get_dpr()
        folder_icon = QIcon(render_icon(Icon.FOLDER, 18, self.theme["gold"], dpr))
        dir_path = item.data(0, Qt.ItemDataRole.UserRole)
        for sub in _list_subdirs(dir_path):
            sub_path = os.path.join(dir_path, sub)
            sub_item = QTreeWidgetItem([sub])
            sub_item.setIcon(0, folder_icon)
            sub_item.setData(0, Qt.ItemDataRole.UserRole, sub_path)
            sub_item.setToolTip(0, sub_path)
            if _dir_has_subdirs(sub_path):
                sub_item.addChild(QTreeWidgetItem(["…"]))   # next lazy level
            item.addChild(sub_item)

    def _bold_font(self):
        f = self.font()
        f.setBold(True)
        return f

    def _on_folder_tree_click(self, item, column):
        """Handle folder tree click: filter tracks or add folder."""
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data == "__add__":
            self._on_add_folder()
            return
        if data == "__all__":
            self._folder_filter = None
            self._mark_selected_folder(None)
            self.search_filter = ""
            self.search_edit.setText("")
            self._rebuild_library_list()
            return
        # Filter tracks to those in this folder (path-boundary safe —
        # plain startswith would match sibling folders sharing a name prefix).
        # The filter is remembered so a later search/sort change keeps it
        # (it used to silently vanish on the first keystroke in the search box).
        folder = data
        self._folder_filter = folder
        self._mark_selected_folder(item)
        self.search_filter = ""
        self.search_edit.setText("")
        self._rebuild_library_list()

    def _mark_selected_folder(self, selected_item):
        """Visual selection without any rectangle: the picked folder swaps
        its closed-folder icon for an OPEN folder drawn in the theme accent
        (gold) and its label turns gold — Explorer-style, symmetric, and
        obvious at a glance."""
        dpr = get_dpr()
        t = self.theme
        closed = QIcon(render_icon(Icon.FOLDER, 18, t["gold"], dpr))
        opened = QIcon(render_icon(Icon.FOLDER_OPEN, 18, t["gold_light"], dpr))
        tree = self.folder_tree
        for i in range(tree.topLevelItemCount()):
            self._walk_folder_rows(tree.topLevelItem(i), selected_item,
                                   closed, opened)

    def _walk_folder_rows(self, item, selected_item, closed, opened):
        if item.data(0, Qt.ItemDataRole.UserRole) not in ("__all__", "__add__"):
            is_sel = (item is selected_item)
            item.setIcon(0, opened if is_sel else closed)
            item.setForeground(0, QColor(self.theme["gold_light"] if is_sel
                                         else self.theme["text"]))
        for i in range(item.childCount()):
            self._walk_folder_rows(item.child(i), selected_item,
                                   closed, opened)

    def _on_folder_tree_context_menu(self, pos):
        """Right-click menu on a folder tree entry: folder-wide actions."""
        item = self.folder_tree.itemAt(pos)
        if item is None:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data in ("__add__", "__all__", None):
            return
        folder = data
        menu = QMenu(self)
        dpr = get_dpr()
        t = self.theme
        act_play = QAction("Play Folder", self)
        act_play.setIcon(QIcon(render_icon(Icon.PLAY, 16, t["gold"], dpr)))
        menu.addAction(act_play)
        act_shuffle = QAction("Shuffle Folder", self)
        act_shuffle.setIcon(QIcon(render_icon(Icon.SHUFFLE, 16, t["gold"], dpr)))
        menu.addAction(act_shuffle)
        menu.addSeparator()
        act_queue = QAction("Add Folder to Queue", self)
        act_queue.setIcon(QIcon(render_icon(Icon.LIST_LINES, 16, t["gold"], dpr)))
        menu.addAction(act_queue)
        act_pl = QAction("Add Folder to Playlist...", self)
        act_pl.setIcon(QIcon(render_icon(Icon.PLAYLISTS, 16, t["gold"], dpr)))
        menu.addAction(act_pl)
        menu.addSeparator()
        act_explore = QAction("Open in Explorer", self)
        act_explore.setIcon(QIcon(render_icon(Icon.FOLDER, 16, t["gold"], dpr)))
        menu.addAction(act_explore)
        act_rescan = QAction("Rescan Folder", self)
        act_rescan.setIcon(QIcon(render_icon(Icon.REFRESH, 16, t["gold"], dpr)))
        menu.addAction(act_rescan)
        menu.addSeparator()
        act_remove = QAction("Remove Folder from Library", self)
        act_remove.setIcon(QIcon(render_icon(Icon.TRASH, 16, "#e05050", dpr)))
        menu.addAction(act_remove)
        act_delete = QAction("Delete Folder from Disk...", self)
        act_delete.setIcon(QIcon(render_icon(Icon.TRASH, 16, "#e05050", dpr)))
        menu.addAction(act_delete)
        chosen = menu.exec(self.folder_tree.mapToGlobal(pos))
        if chosen is None:
            return
        if chosen == act_play:
            self._play_folder(folder, shuffle=False)
        elif chosen == act_shuffle:
            self._play_folder(folder, shuffle=True)
        elif chosen == act_queue:
            self._queue_folder(folder)
        elif chosen == act_pl:
            self._folder_to_playlist(folder)
        elif chosen == act_explore:
            self._open_file_location(folder if os.path.isdir(folder)
                                     else os.path.dirname(folder))
        elif chosen == act_rescan:
            if os.path.isdir(folder):
                self._start_scan([folder], label="Rescanning folder...")
        elif chosen == act_remove:
            self._remove_folder_from_library(folder)
        elif chosen == act_delete:
            self._delete_folder_from_disk(folder)

    # ------------------------------------------------------------------
    # Folder-wide actions (folder tree right-click menu)
    # ------------------------------------------------------------------
    def _folder_tracks(self, folder: str) -> list:
        """All library tracks under `folder` (path-boundary safe)."""
        return [p for p in self.library if path_within(p, folder)]

    def _play_folder(self, folder: str, shuffle: bool):
        """Play / shuffle-play only the tracks inside `folder`."""
        if folder == "__all__":
            tracks = list(self.library)
        else:
            tracks = self._folder_tracks(folder)
        if not tracks:
            QMessageBox.information(self, APP_NAME,
                "This folder has no audio tracks in the library.")
            return
        if shuffle:
            random.shuffle(tracks)
        self._on_rail_clicked("library")
        self._play_from_list(tracks, 0, view=View.LIBRARY, shuffled=shuffle)
        name = os.path.basename(folder) or folder
        self.page_title.setText(f"{'Shuffle: ' if shuffle else ''}{name}")

    def _queue_folder(self, folder: str):
        """Append every folder track to the end of the current playlist."""
        tracks = self._folder_tracks(folder)
        if not tracks:
            QMessageBox.information(self, APP_NAME,
                "This folder has no audio tracks in the library.")
            return
        if not self.current_playlist:
            self._play_from_list(tracks, 0, view=self.current_view)
            return
        existing = set(self.current_playlist)
        added = [p for p in tracks if p not in existing]
        self.current_playlist.extend(added)
        self.shuffle_unplayed.update(added)
        if self.tray and self.tray.isVisible():
            self.tray.showMessage(APP_NAME,
                f"Queued {len(added)} track(s) from {os.path.basename(folder)}",
                QSystemTrayIcon.MessageIcon.Information, 2000)

    def _folder_to_playlist(self, folder: str):
        """Create/extend a personal playlist with every track of the folder."""
        from playlist_dialog import PlaylistStore
        tracks = self._folder_tracks(folder)
        if not tracks:
            QMessageBox.information(self, APP_NAME,
                "This folder has no audio tracks in the library.")
            return
        default = os.path.basename(folder) or "New Playlist"
        name, ok = QInputDialog.getText(self, APP_NAME,
            "Playlist name:", text=PlaylistStore.sanitize_name(default))
        if not ok:
            return
        clean = PlaylistStore.valid_new_name(name, self.playlists)
        if not clean:
            existing = PlaylistStore.sanitize_name(name)
            if existing and existing.casefold() in \
                    {k.casefold() for k in self.playlists}:
                # Extend the existing playlist with the folder tracks
                pl = self.playlists[existing]
                added = [p for p in tracks if p not in pl]
                pl.extend(added)
                self._save_config()
                QMessageBox.information(self, APP_NAME,
                    f"Added {len(added)} track(s) to '{existing}'.")
            return
        self.playlists[clean] = list(tracks)
        self._save_config()
        QMessageBox.information(self, APP_NAME,
            f"Created '{clean}' with {len(tracks)} track(s).")

    def _remove_folder_from_library(self, folder: str):
        """Remove a folder and its tracks from the library (files untouched)."""
        name = os.path.basename(folder) or folder
        tracks = self._folder_tracks(folder)
        reply = QMessageBox.question(
            self, APP_NAME,
            f"Remove '{name}' from the library?\n\n"
            f"{len(tracks)} track(s) will leave the library. "
            f"The files on disk are NOT deleted.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.added_folders = [f for f in self.added_folders
                              if os.path.normcase(os.path.abspath(f)) !=
                              os.path.normcase(os.path.abspath(folder))]
        for p in tracks:
            self.library.remove(p) if p in self.library else None
            self.favorites.discard(p)
            try:
                self.play_history.remove(p)
            except ValueError:
                pass
            try:
                self.redo_stack.remove(p)
            except ValueError:
                pass
            for name_pl in list(self.playlists):
                if p in self.playlists[name_pl]:
                    self.playlists[name_pl].remove(p)
        if self.current_track in tracks:
            self.audio.stop()
            self.current_track = None
            self.player_bar.update_play_button(False)
            self.player_bar.title_label.setMarqueeText("No track selected")
            self.player_bar.artist_label.setMarqueeText("—")
            self.now_title.setMarqueeText("No track selected")
            self.now_artist.setMarqueeText("Select a track to play")
        self.current_playlist = [p for p in self.current_playlist
                                 if p not in set(tracks)]
        self.current_index = 0 if self.current_playlist else -1
        if self._folder_filter and (path_within(self._folder_filter, folder)
                                    or self._folder_filter == folder):
            self._folder_filter = None
        self._rebuild_library_list()
        self._rebuild_favorites_list()
        self._rebuild_folder_tree()
        self._update_count_label()
        self._save_config()
        if self.tray and self.tray.isVisible():
            self.tray.showMessage(APP_NAME, f"Removed '{name}' from library",
                QSystemTrayIcon.MessageIcon.Information, 2000)

    def _delete_folder_from_disk(self, folder: str):
        """Permanently delete a folder's audio files from disk (2-step confirm).
        The folder is removed from the library first, then the files."""
        name = os.path.basename(folder) or folder
        tracks = self._folder_tracks(folder)
        reply = QMessageBox.warning(
            self, APP_NAME,
            f"PERMANENTLY DELETE '{name}' from your computer?\n\n"
            f"{len(tracks)} audio file(s) will be deleted.\n"
            f"This does NOT go to the Recycle Bin and cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        reply2 = QMessageBox.warning(
            self, APP_NAME,
            f"Are you REALLY sure about deleting {len(tracks)} file(s)\n"
            f"inside:\n{folder}\n\nLast chance — this cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply2 != QMessageBox.StandardButton.Yes:
            return
        # Stop playback first if a doomed track is playing
        if self.current_track in tracks:
            self.audio.stop()
            self.current_track = None
            self.player_bar.update_play_button(False)
            self.player_bar.title_label.setMarqueeText("No track selected")
            self.player_bar.artist_label.setMarqueeText("—")
        self._remove_folder_from_library_quiet(folder)
        deleted = 0
        errors = []
        for p in tracks:
            try:
                if os.path.exists(p):
                    os.remove(p)
                    deleted += 1
            except OSError as ex:
                errors.append(f"{os.path.basename(p)}: {ex}")
        # Try to prune now-empty subdirectories up to the folder root itself
        self._prune_empty_dirs(folder)
        log.info(f"Folder delete: {deleted} file(s) removed from {folder}")
        if errors:
            QMessageBox.warning(self, APP_NAME,
                f"Deleted {deleted} file(s); some could not be deleted:\n\n" +
                "\n".join(errors[:8]))
        elif self.tray and self.tray.isVisible():
            self.tray.showMessage(APP_NAME,
                f"Deleted '{name}' ({deleted} files) from disk",
                QSystemTrayIcon.MessageIcon.Information, 2500)

    def _remove_folder_from_library_quiet(self, folder: str):
        """Library bookkeeping for _delete_folder_from_disk (no prompts)."""
        tracks = set(self._folder_tracks(folder))
        self.added_folders = [f for f in self.added_folders
                              if os.path.normcase(os.path.abspath(f)) !=
                              os.path.normcase(os.path.abspath(folder))]
        self.library = [p for p in self.library if p not in tracks]
        self.favorites -= tracks
        self.play_history = [p for p in self.play_history if p not in tracks]
        self.redo_stack = [p for p in self.redo_stack if p not in tracks]
        self.current_playlist = [p for p in self.current_playlist
                                 if p not in tracks]
        self.current_index = 0 if self.current_playlist else -1
        for name_pl in list(self.playlists):
            self.playlists[name_pl] = [p for p in self.playlists[name_pl]
                                       if p not in tracks]
        if self._folder_filter and self._folder_filter in tracks:
            self._folder_filter = None
        self._rebuild_library_list()
        self._rebuild_favorites_list()
        self._rebuild_folder_tree()
        self._update_count_label()
        self._save_config()

    @staticmethod
    def _prune_empty_dirs(folder: str):
        """Delete the folder and its now-empty ancestors' empty children."""
        try:
            for root, dirs, files in os.walk(folder, topdown=False):
                if not dirs and not files:
                    os.rmdir(root)
            if os.path.isdir(folder) and not os.listdir(folder):
                os.rmdir(folder)
        except OSError:
            pass  # non-empty or locked — leave it alone

    def _rebuild_favorites_list(self):
        """Rebuild favorites list — two-line rows via TrackRowDelegate."""
        self.favorites_list.clear()
        tracks = self._get_filtered_sorted_favorites()
        for i, path in enumerate(tracks):
            if path in self._tag_cache:
                title, artist = self._tag_cache[path]
                item = QListWidgetItem(_track_item_text(title, artist))
                item.setToolTip(f"{title}\n{artist}\n{path}")
            else:
                fname = os.path.splitext(os.path.basename(path))[0]
                item = QListWidgetItem(_track_item_text(fname, ""))
                item.setToolTip(path)
            item.setData(TrackRowDelegate.TITLE_ROLE, i + 1)
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.favorites_list.addItem(item)
        self._highlight_playing_in_lists()
        self._update_count_label()

    def _highlight_playing_in_lists(self):
        # TrackRowDelegate renders the "playing" glow from this role —
        # font/background tweaks on the item itself are ignored by the delegate.
        for lst in (self.library_list, self.favorites_list):
            for i in range(lst.count()):
                item = lst.item(i)
                is_playing = (self.current_track is not None and
                              item.data(Qt.ItemDataRole.UserRole) == self.current_track)
                item.setData(Qt.ItemDataRole.UserRole + 2, bool(is_playing))
            lst.viewport().update()

    def _on_context_menu(self, pos):
        lst = self.library_list if self.current_view == View.LIBRARY else self.favorites_list
        item = lst.itemAt(pos)
        if not item:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        menu = QMenu(self)
        dpr = get_dpr()
        t = self.theme
        act_play = QAction("Play", self)
        act_play.setIcon(QIcon(render_icon(Icon.PLAY, 16, t["gold"], dpr)))
        act_play.triggered.connect(lambda: self._play_track_from_list(path))
        menu.addAction(act_play)
        menu.addSeparator()
        if path in self.favorites:
            act_unfav = QAction("Remove from Favorites", self)
            act_unfav.setIcon(QIcon(render_icon(Icon.HEART_FILLED, 16, t["gold"], dpr)))
            act_unfav.triggered.connect(lambda: self._toggle_favorite(path))
            menu.addAction(act_unfav)
        else:
            act_fav = QAction("Add to Favorites", self)
            act_fav.setIcon(QIcon(render_icon(Icon.HEART, 16, t["gold"], dpr)))
            act_fav.triggered.connect(lambda: self._toggle_favorite(path))
            menu.addAction(act_fav)
        menu.addSeparator()
        act_remove = QAction("Remove from Library", self)
        act_remove.setIcon(QIcon(render_icon(Icon.TRASH, 16, "#e05050", dpr)))
        act_remove.triggered.connect(lambda: self._remove_from_library(path))
        menu.addAction(act_remove)
        menu.addSeparator()
        act_add_pl = QAction("Add to Playlist...", self)
        act_add_pl.setIcon(QIcon(render_icon(Icon.LIBRARY, 16, t["gold"], dpr)))
        act_add_pl.triggered.connect(
            lambda: self._add_to_playlist(path))
        menu.addAction(act_add_pl)
        menu.addSeparator()
        act_open_loc = QAction("Open File Location", self)
        act_open_loc.setIcon(QIcon(render_icon(Icon.FOLDER, 16, t["gold"], dpr)))
        act_open_loc.triggered.connect(lambda: self._open_file_location(path))
        menu.addAction(act_open_loc)
        act_props = QAction("Properties", self)
        act_props.setIcon(QIcon(render_icon(Icon.SETTINGS, 16, t["muted"], dpr)))
        act_props.triggered.connect(lambda: self._show_track_properties(path))
        menu.addAction(act_props)
        # Use cached tags for display if available
        if path in self._tag_cache:
            title, artist = self._tag_cache[path]
            menu.setWindowTitle(f"{title} — {artist}")
        menu.exec(lst.mapToGlobal(pos))

    def _add_to_playlist(self, path):
        """Right-click helper — delegate to the playlist picker dialog."""
        try:
            from playlist_dialog import add_track_to_playlist_dialog
            add_track_to_playlist_dialog(self, path)
        except Exception as e:
            log.error(f"Add-to-playlist failed: {e}")

    def _open_file_location(self, path):
        """Reveal the track's file in the OS file manager."""
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", path])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(path)])
        except Exception as e:
            log.warning(f"Open file location failed: {e}")

    def _show_track_properties(self, path):
        """Show the technical properties dialog for a track."""
        try:
            from trackinfo import get_track_info
            from track_info_dialog import TrackInfoDialog
            info = get_track_info(path)
            dlg = TrackInfoDialog(info, self.theme, self)
            dlg.exec()
        except Exception as e:
            log.error(f"Track properties failed: {e}")
            QMessageBox.warning(self, APP_NAME, f"Could not read file info:\n{e}")

    def _on_bar_info_clicked(self):
        """Clicking the player-bar cover / title / artist opens the playing
        track's info dialog."""
        if self.current_track:
            self._show_track_properties(self.current_track)

    # ------------------------------------------------------------------
    # Track menu (three-line button) for the CURRENTLY PLAYING track
    # ------------------------------------------------------------------
    def _show_more_menu(self):
        """⋯ overflow menu on the player bar: Audio Output, A-B Repeat,
        Playback Speed, Show in Folder and Track Options."""
        menu = QMenu(self)
        dpr = get_dpr()
        t = self.theme
        # ---- Audio output submenu (System Default / HDMI / S/PDIF / ...) --
        out_menu = QMenu("Audio Output", menu)
        out_menu.setIcon(QIcon(render_icon(Icon.VOLUME_HIGH, 16, t["gold"], dpr)))
        current_id = str(getattr(self, "audio_output_id", "") or "")
        grp_devices = []
        act_def = QAction("System Default", out_menu)
        act_def.setCheckable(True)
        act_def.setChecked(current_id == "")
        act_def.triggered.connect(lambda: self._switch_audio_output(""))
        out_menu.addAction(act_def)
        out_menu.addSeparator()
        try:
            from audio_output import list_output_devices
            for name, dev_id in list_output_devices():
                act = QAction(name, out_menu)
                act.setCheckable(True)
                act.setChecked(dev_id == current_id)
                act.triggered.connect(
                    lambda _=False, did=dev_id: self._switch_audio_output(did))
                out_menu.addAction(act)
                grp_devices.append(act)
        except Exception as e:
            log.warning(f"output enumeration failed: {e}")
        if not grp_devices:
            act_none = QAction("No other devices found", out_menu)
            act_none.setEnabled(False)
            out_menu.addAction(act_none)
        menu.addMenu(out_menu)
        menu.addSeparator()
        act_ab = QAction("A-B Repeat", self)
        act_ab.setIcon(QIcon(render_icon(Icon.REPEAT_ONE, 16, t["gold"], dpr)))
        act_ab.triggered.connect(self._on_ab_button)
        menu.addAction(act_ab)
        act_rate = QAction("Playback Speed...", self)
        act_rate.setIcon(QIcon(render_icon(Icon.SPEED, 16, t["gold"], dpr)))
        act_rate.triggered.connect(
            lambda: self._cycle_playback_rate(self.player_bar.more_btn))
        menu.addAction(act_rate)
        act_loc = QAction("Show in Folder", self)
        act_loc.setIcon(QIcon(render_icon(Icon.FOLDER, 16, t["gold"], dpr)))
        act_loc.triggered.connect(self._share_current_track)
        menu.addAction(act_loc)
        act_opts = QAction("Track Options", self)
        act_opts.setIcon(QIcon(render_icon(Icon.LIST_LINES, 16, t["gold"], dpr)))
        act_opts.triggered.connect(self._show_track_menu)
        menu.addAction(act_opts)
        btn = self.player_bar.more_btn
        menu.exec(btn.mapToGlobal(QPoint(btn.width() // 2 - 60, -menu.sizeHint().height() - 8)))

    def _switch_audio_output(self, device_id: str):
        """Route playback to a named output device (menu pick) and persist."""
        try:
            from audio_output import apply_output_device
            ok = apply_output_device(self.audio, device_id)
            self.audio_output_id = device_id if ok else ""
            if ok and self.tray and self.tray.isVisible():
                label = device_id or "System Default"
                if device_id:
                    try:
                        from audio_output import list_output_devices
                        for name, did in list_output_devices():
                            if did == device_id:
                                label = name
                                break
                    except Exception:
                        pass
                self.tray.showMessage(APP_NAME, f"Audio output: {label}",
                    QSystemTrayIcon.MessageIcon.Information, 1500)
        except Exception as e:
            log.warning(f"audio output switch failed: {e}")
        self._save_config_debounced()

    def _share_current_track(self):
        """Reveal the playing track in Explorer (file pre-selected)."""
        if not self.current_track:
            QMessageBox.information(self, APP_NAME, "No track is playing.")
            return
        self._open_file_location(self.current_track)

    def _show_track_menu(self):
        """Popup menu for the current track: Delete / Properties / Open in
        Folder (+ Edit Tags). Mirrors the track context menu."""
        if not self.current_track:
            QMessageBox.information(self, APP_NAME, "No track is playing.")
            return
        path = self.current_track
        menu = QMenu(self)
        dpr = get_dpr()
        t = self.theme
        act_delete = QAction("Delete", self)
        act_delete.setIcon(QIcon(render_icon(Icon.TRASH, 16, "#e05050", dpr)))
        act_delete.triggered.connect(lambda: self._delete_track_from_disk(path))
        menu.addAction(act_delete)
        menu.addSeparator()
        act_props = QAction("Properties", self)
        act_props.setIcon(QIcon(render_icon(Icon.SETTINGS, 16, t["muted"], dpr)))
        act_props.triggered.connect(lambda: self._show_track_properties(path))
        menu.addAction(act_props)
        act_folder = QAction("Open in Folder", self)
        act_folder.setIcon(QIcon(render_icon(Icon.FOLDER, 16, t["gold"], dpr)))
        act_folder.triggered.connect(lambda: self._open_file_location(path))
        menu.addAction(act_folder)
        act_edit = QAction("Edit Tags...", self)
        act_edit.setIcon(QIcon(render_icon(Icon.LYRICS, 16, t["gold_light"], dpr)))
        act_edit.triggered.connect(lambda: self._edit_tags(path))
        menu.addAction(act_edit)
        if hasattr(self, "player_bar") and hasattr(self.player_bar, "track_menu_btn"):
            btn = self.player_bar.track_menu_btn
            menu.exec(btn.mapToGlobal(QPoint(btn.width() // 2 - 60, btn.height() + 6)))
        else:
            from PyQt6.QtGui import QCursor
            menu.exec(QCursor.pos())

    def _delete_track_from_disk(self, path):
        """Permanently delete the audio file from disk (with confirmation).
        The track is also removed from library/history/playlists."""
        title = os.path.basename(path)
        reply = QMessageBox.warning(
            self, APP_NAME,
            f"Permanently delete this file from your computer?\n\n"
            f"{title}\n\nThis cannot be undone (does NOT go to Recycle Bin).",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            was_playing = path == self.current_track
            if was_playing:
                self.audio.stop()
                self.current_track = None
                self.player_bar.update_play_button(False)
                self.player_bar.title_label.setMarqueeText("No track selected")
                self.player_bar.artist_label.setMarqueeText("—")
                if getattr(self, "tray_menu_widget", None) is not None:
                    self.tray_menu_widget.update_now_playing()
            # Library bookkeeping (also purges history entries)
            self._remove_from_library(path)
            # Playlists cleanup
            changed = False
            for name in list(self.playlists):
                if path in self.playlists[name]:
                    self.playlists[name].remove(path)
                    changed = True
            if changed:
                self._save_config()
            # Finally remove the physical file
            os.remove(path)
            log.info(f"Deleted file from disk: {path}")
            if self.tray and self.tray.isVisible():
                self.tray.showMessage(APP_NAME, f"Deleted {title}",
                    QSystemTrayIcon.MessageIcon.Information, 2000)
        except FileNotFoundError:
            QMessageBox.warning(self, APP_NAME, "File was already gone.")
        except OSError as e:
            log.error(f"Disk delete failed: {e}")
            QMessageBox.critical(self, APP_NAME,
                f"Could not delete file:\n{e}\n\n(Is it locked by another program?)")

    def _edit_tags(self, path):
        """Open the real tag editor for a file."""
        try:
            from tag_editor import TagEditorDialog
            dlg = TagEditorDialog(path, self.theme, self)
            if dlg.exec():
                # File changed on disk → refresh caches so the UI reflects it
                self._tag_cache.pop(path, None)
                coverart.clear_cache()
                self._start_tag_loader([path])
                if path == self.current_track:
                    title, artist = extract_title_artist(path)
                    self.now_title.setMarqueeText(title)
                    self.now_artist.setMarqueeText(artist)
                    self.player_bar.title_label.setMarqueeText(title)
                    self.player_bar.artist_label.setMarqueeText(artist)
                self._load_cover_async(path)
                self._rebuild_library_list()
        except Exception as e:
            log.error(f"Tag editor failed: {e}")
            QMessageBox.warning(self, APP_NAME, f"Tag editor failed:\n{e}")

    # ------------------------------------------------------------------
    # Lyrics panel (local .lrc / embedded / online via lrclib.net)
    # ------------------------------------------------------------------
    def _toggle_lyrics_panel(self):
        try:
            from lyrics_panel import LyricsPanel
            if getattr(self, "_lyrics_panel", None) is None:
                self._lyrics_panel = LyricsPanel(self)
            self._lyrics_panel._apply_style()
            if self._lyrics_panel.isVisible():
                self._lyrics_panel.hide()
                return
            self._lyrics_panel.load_for_track(
                self.current_track,
                allow_online=bool(getattr(self, "lyrics_online_enabled", True)))
            gp = self.mapToGlobal(self.rect().bottomLeft())
            self._lyrics_panel.move(gp.x() + 80, gp.y() - 420)
            self._lyrics_panel.show()
            self._lyrics_panel.raise_()
        except Exception as e:
            log.error(f"Lyrics panel failed: {e}")
            QMessageBox.warning(self, APP_NAME, f"Lyrics failed:\n{e}")

    def _on_lyrics_position_tick(self, pos_ms: int):
        """Called from _ui_tick to sync the active lyric line."""
        panel = getattr(self, "_lyrics_panel", None)
        if panel is not None and panel.isVisible() and self.current_track:
            panel.sync_position(pos_ms)
        fs = getattr(self, "_fullscreen_player", None)
        if fs is not None and fs.isVisible() and self.current_track:
            try:
                fs.sync_lyrics(pos_ms)
            except Exception:
                pass

    def _play_track_from_list(self, path):
        lst = self.library_list if self.current_view == View.LIBRARY else self.favorites_list
        tracks = []
        target_idx = 0
        for i in range(lst.count()):
            it = lst.item(i)
            p = it.data(Qt.ItemDataRole.UserRole)
            tracks.append(p)
            if p == path:
                target_idx = i
        self._play_from_list(tracks, target_idx, view=self.current_view)

    def _toggle_favorite(self, path):
        if path in self.favorites:
            self.favorites.discard(path)
        else:
            self.favorites.add(path)
        if path == self.current_track:
            self.player_bar.update_like_button(path in self.favorites)
        self._rebuild_favorites_list()
        self._save_config()

    def _remove_from_library(self, path):
        if path in self.library:
            self.library.remove(path)
        self.favorites.discard(path)
        # Keep history consistent — a removed track must not come back via
        # Previous/Next history navigation
        try:
            self.play_history.remove(path)
        except ValueError:
            pass
        try:
            self.redo_stack.remove(path)
        except ValueError:
            pass
        if path != self.current_track:
            # Removing a non-playing track: drop it from the playlist and keep
            # current_index pointing at the SAME playing track.
            try:
                pl_pos = self.current_playlist.index(path)
                self.current_playlist.pop(pl_pos)
                if pl_pos < self.current_index:
                    self.current_index -= 1
                if not self.current_playlist:
                    self.current_index = -1
            except ValueError:
                pass  # not in the active playlist — nothing to fix
        if path == self.current_track:
            self.audio.stop()
            self.current_track = None
            # Remove the dead entry from the playlist too, keeping the next
            # track at the same index (or clamped to the new last position)
            if path in self.current_playlist:
                pl_pos = self.current_playlist.index(path)
                self.current_playlist.pop(pl_pos)
                self.current_index = min(max(0, pl_pos), max(0, len(self.current_playlist) - 1))
                if not self.current_playlist:
                    self.current_index = -1
            else:
                self.current_index = -1
            self.player_bar.title_label.setMarqueeText("No track selected")
            self.player_bar.artist_label.setMarqueeText("—")
            self.now_title.setMarqueeText("No track selected")
            self.now_artist.setMarqueeText("Select a track to play")
            if getattr(self, "tray_menu_widget", None) is not None:
                self.tray_menu_widget.update_now_playing()
        self._rebuild_library_list()
        self._rebuild_favorites_list()
        self._save_config()

    def _on_play_all(self):
        tracks = self._get_filtered_sorted_library()
        if not tracks:
            QMessageBox.information(self, APP_NAME, "Library is empty. Add a folder first.")
            return
        self._on_rail_clicked("library")
        self._play_from_list(tracks, 0, view=View.LIBRARY)

    def _on_shuffle_all(self):
        tracks = self._get_filtered_sorted_library() if self.current_view == View.LIBRARY else self._get_filtered_sorted_favorites()
        if not tracks:
            QMessageBox.information(self, APP_NAME, "No tracks to play.")
            return
        random.shuffle(tracks)
        self._play_from_list(tracks, 0, view=self.current_view, shuffled=True)
        self.shuffle = True
        self.player_bar.update_shuffle_button(True)

    def _on_track_double_clicked(self, item: QListWidgetItem):
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path or not os.path.exists(path):
            return
        lst = self.library_list if self.current_view == View.LIBRARY else self.favorites_list
        tracks = []
        target_idx = 0
        for i in range(lst.count()):
            it = lst.item(i)
            p = it.data(Qt.ItemDataRole.UserRole)
            tracks.append(p)
            if p == path:
                target_idx = i
        self._play_from_list(tracks, target_idx, view=self.current_view)

    def _play_from_list(self, tracks, index, view=View.LIBRARY, shuffled=False):
        if not tracks:
            return
        self.current_playlist = list(tracks)
        self.current_index = index
        self.shuffle = shuffled
        self.player_bar.update_shuffle_button(shuffled)
        # New listening session — old history belongs to the previous queue
        # unless we're already inside it (double-click during history nav).
        if not self._nav_via_history:
            self.play_history.clear()
            self.redo_stack.clear()
            # Smart shuffle: fresh queue = everything is unplayed again
            # except whatever is about to start.
            self.shuffle_unplayed = set(tracks)
        self._load_and_play_current()

    _HISTORY_MAX = 200  # bound memory; deep enough for any listening session

    def _push_history(self, path):
        """Record a track start in history, clearing the redo branch —
        same semantics as browser back/forward.

        Skipped entirely while navigating via history (_nav_via_history):
        _on_prev/_on_next already maintain history/redo themselves."""
        if not path or self._nav_via_history:
            return
        # Avoid consecutive duplicates (repeat-one restarts, seeks)
        if self.play_history and self.play_history[-1] == path:
            return
        self.play_history.append(path)
        del self.play_history[:-self._HISTORY_MAX]
        self.redo_stack.clear()

    def _load_and_play_current(self):
        if not self.current_playlist or self.current_index < 0:
            return
        path = self.current_playlist[self.current_index]
        self._push_history(path)
        self.shuffle_unplayed.discard(path)   # smart shuffle: this one had its turn
        if getattr(self, "fx", None):
            self.fx.clear_ab()                # A-B loop is per-track
        # Listen metering restarts per track (70% credit rule — see stats)
        self._finalize_listen_credit()
        self._listened_ms = 0
        self.current_track = path
        # Use cached tags if available, otherwise extract (just for current track)
        if path in self._tag_cache:
            title, artist = self._tag_cache[path]
        else:
            try:
                title, artist = extract_title_artist(path)
                self._tag_cache[path] = (title, artist)
            except Exception:
                title = os.path.splitext(os.path.basename(path))[0]
                artist = "Unknown Artist"
                self._tag_cache[path] = (title, artist)
        self.now_title.setMarqueeText(title)
        self.now_artist.setMarqueeText(artist)
        self.player_bar.title_label.setMarqueeText(title)
        self.player_bar.artist_label.setMarqueeText(artist)
        self.player_bar.time_total.setText("0:00")
        self.player_bar.time_current.setText("0:00")
        self.player_bar.seek_slider.setValue(0)
        self.player_bar.update_like_button(path in self.favorites)
        self._load_cover_async(path)
        self._highlight_playing_in_lists()
        # Load and play immediately (simplified audio backend)
        self.audio.set_volume(self.player_bar.vol_slider.value())
        self.audio.load_and_play(path)
        if getattr(self, "fx", None):
            self.fx.fade_in_from_silence()
            rate = self.fx.current_rate()
            if abs(rate - 1.0) > 1e-6:
                self.fx.set_rate(rate)   # re-apply speed to the new media item
        # Update mini player
        if self.mini_player and self.mini_player.isVisible():
            pm = self.player_bar._cover_thumb_pm
            self.mini_player.update_track(title, artist, pm)
        # Keep the tray popup header in sync (it stays alive while hidden)
        if getattr(self, "tray_menu_widget", None) is not None:
            self.tray_menu_widget.update_now_playing(
                title, artist, self.player_bar._cover_thumb_pm)

    def _on_track_finished(self):
        """Called when a track finishes naturally."""
        self._finalize_listen_credit()
        self._on_next()

    def _finalize_listen_credit(self):
        """Apply the 70% rule for the track that just ended (natural finish
        or manual skip): credit ONE play when >70% was actually heard."""
        if not getattr(self, "stats", None) or not self.current_track:
            return
        dur_ms = self.audio.duration()
        listened = float(getattr(self, "_listened_ms", 0)) / 1000.0
        title, artist = self._tag_cache.get(self.current_track, ("", ""))
        self.stats.credit_track(
            self.current_track,
            listened,
            artist or "",
            duration_seconds=dur_ms / 1000.0 if dur_ms else 0.0,
            listened_seconds=listened)
        self._flush_stats()

    def _flush_stats(self):
        if getattr(self, "stats", None) and self.stats._dirty:
            self.stats.save()

    # ------------------------------------------------------------------
    # Track-change toast (only while the main window is hidden)
    # ------------------------------------------------------------------
    _TOAST_COOLDOWN_S = 4

    def _maybe_toast_track_change(self):
        if not self.current_track or self.isVisible():
            self._last_toast_key = None
            return
        key = (self.current_track,
               self.audio.state() == AudioBackend.STATE_PLAYING)
        if key == self._last_toast_key:
            return
        now = time.time()
        if now - getattr(self, "_last_toast_ts", 0) < self._TOAST_COOLDOWN_S:
            return
        self._last_toast_key = key
        self._last_toast_ts = now
        if self.tray and self.tray.isVisible():
            title, artist = self._tag_cache.get(
                self.current_track, ("", ""))
            if not title:
                # Never run mutagen on the UI thread for a toast — fall back
                # to the filename stem instead.
                title = os.path.splitext(os.path.basename(self.current_track))[0]
            body = f"{title} — {artist}" if artist else title
            self.tray.showMessage(APP_NAME, f"♪ {body}",
                QSystemTrayIcon.MessageIcon.Information, 2500)

    def _on_media_loaded(self):
        """Deprecated — kept for compatibility."""
        pass

    def _load_cover_async(self, path):
        # Remember which request is current so a stale loader finishing late
        # can't overwrite the new track's cover.
        self._cover_request = path
        if self._cover_loader is not None:
            try:
                self._cover_loader.cover_ready.disconnect(self._on_cover_ready)
            except TypeError:
                pass  # already disconnected
            if self._cover_loader.isRunning():
                # Don't deleteLater() a still-running QThread — that can hard-
                # crash ("QThread: Destroyed while thread is still running").
                # Park it under the QApplication so Qt owns its remaining
                # lifetime: the disconnect above blocks stale results and
                # deleteLater fires from its own finished signal.
                self._cover_loader.finished.connect(
                    self._cover_loader.deleteLater)
                self._cover_loader.setParent(QApplication.instance())
            else:
                self._cover_loader.deleteLater()
        self._cover_loader = CoverLoaderThread(path)
        self._cover_loader.cover_ready.connect(self._on_cover_ready)
        self._cover_loader.start()

    def _on_cover_ready(self, path, data):
        # Ignore results from an outdated load (fast track switching).
        if path != getattr(self, "_cover_request", None) or path != self.current_track:
            return
        if data is None:
            self.cover_label.set_cover(None)
            self.fav_cover.set_cover(None)
            self.player_bar.set_cover_thumb(None)
            pm = None
        else:
            pm = coverart.bytes_to_pixmap(data)
            if pm.isNull():
                self.cover_label.set_cover(None)
                self.fav_cover.set_cover(None)
                self.player_bar.set_cover_thumb(None)
                pm = None
            else:
                self.cover_label.set_cover(pm)
                self.fav_cover.set_cover(pm)
                self.player_bar.set_cover_thumb(pm)
        # Keep the tray popup's cover in sync with the loaded art too.
        if getattr(self, "tray_menu_widget", None) is not None:
            title, artist = self._current_track_tags()
            self.tray_menu_widget.update_now_playing(title, artist, pm)
        if self.mini_player and self.mini_player.isVisible() and self.current_track:
            self.mini_player.update_track(*self._current_track_tags(), pm)

    def _on_play_pause(self):
        if self.current_track is None:
            if self.current_playlist:
                self._load_and_play_current()
            else:
                self._on_play_all()
            self._tray_sync_now_playing()
            return
        state = self.audio.state()
        if state == AudioBackend.STATE_PLAYING:
            if getattr(self, "fx", None):
                self.fx.fade_out_then(self.audio.pause)
            else:
                self.audio.pause()
        elif state == AudioBackend.STATE_PAUSED:
            self.audio.play()
            if getattr(self, "fx", None):
                self.fx.fade_in_from_silence()
        else:
            # Stopped — reload and play
            self.audio.set_volume(self.player_bar.vol_slider.value())
            self.audio.load_and_play(self.current_track)

    def _cycle_playback_rate(self, anchor_btn=None):
        """Open a small popup with a speed slider (0.5x – 2.0x).

        Anchors to the button that ASKED for it (rate button from the bar,
        menu item from the ⋯ menu) and is clamped fully inside the monitor —
        it used to always anchor to more_btn and could open off-screen.
        Non-modal (Popup): the player keeps playing and the old modal dialog
        no longer grabs the playlist context menu's focus away."""
        if not getattr(self, "fx", None):
            return
        # Close a previous popup instead of stacking dialogs on fast clicks.
        old = getattr(self, "_rate_popup", None)
        if old is not None:
            try:
                old.close()
            except Exception:
                pass
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QSlider, QHBoxLayout
        dlg = QDialog(self, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        dlg.setWindowTitle("Playback Speed")
        dlg.setWindowIcon(QIcon(render_icon(Icon.SPEED, 32, self.theme["gold"],
                                            get_dpr())))
        dlg.setModal(False)
        dlg.setFixedWidth(320)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._rate_popup = dlg
        lay = QVBoxLayout(dlg)
        row = QHBoxLayout()
        icon_lbl = QLabel()
        icon_lbl.setPixmap(render_icon(Icon.SPEED, 22, self.theme["gold"],
                                       get_dpr()))
        row.addStretch(1)
        row.addWidget(icon_lbl)
        lbl = QLabel(f"{self.fx.current_rate():.2f}x")
        lbl.setObjectName("NowTitle")
        row.addWidget(lbl)
        row.addStretch(1)
        lay.addLayout(row)
        slider = QSlider(Qt.Orientation.Horizontal)
        # 50..200 mapped /100 -> 0.5x..2x
        slider.setRange(50, 200)
        slider.setValue(int(round(self.fx.current_rate() * 100)))
        slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        slider.setTickInterval(25)
        def _on_move(v):
            lbl.setText(f"{v/100:.2f}x")
            self.fx.set_rate(v / 100.0)
        slider.valueChanged.connect(_on_move)
        lay.addWidget(slider)
        # Preset buttons with Lucide icons — consistent with the app's icon style
        presets = QHBoxLayout()
        current_rate = self.fx.current_rate()
        # Map each preset to an appropriate speed icon
        # 0.5x = turtle-slow, 0.75x = slower, 1.0x = normal (play), 1.25x = slightly faster,
        # 1.5x = fast (chevron-right), 2.0x = fastest (double chevron concept via gauge)
        preset_icons = {
            0.5: "timer",           # very slow
            0.75: "chevron-left",   # slow
            1.0: "play",            # normal speed
            1.25: "music-note",     # slightly faster
            1.5: "chevron-right",   # fast
            2.0: "gauge",           # fastest (speed gauge)
        }
        dpr = get_dpr()
        for p in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0):
            b = QPushButton()
            b.setObjectName("GhostBtn")
            # Create icon for this preset
            icon_name = preset_icons[p]
            is_current = abs(p - current_rate) < 0.01
            icon_color = self.theme["gold"] if is_current else self.theme["text"]
            b.setIcon(make_icon(icon_name, 16, icon_color, dpr))
            b.setIconSize(QSize(16, 16))
            b.setText(f"{p:g}x")
            b.setToolTip(f"Set speed to {p:g}x")
            b.clicked.connect(lambda _, pv=p: (slider.setValue(int(pv*100))))
            presets.addWidget(b)
        lay.addLayout(presets)
        # ---- Anchor to the requesting button, clamp inside its monitor ----
        if anchor_btn is None:
            anchor_btn = self.player_bar.rate_btn \
                if self.player_bar.rate_btn.isVisible() \
                else self.player_bar.more_btn
        btn_pos = anchor_btn.mapToGlobal(QPoint(0, anchor_btn.height()))
        # Which monitor is the anchor on? (fallback: primary)
        screen = QApplication.screenAt(
            QPoint(btn_pos.x() + anchor_btn.width() // 2, btn_pos.y()))
        geo = (screen if screen is not None
               else QApplication.primaryScreen()).availableGeometry()
        dlg.adjustSize()
        dw, dh = dlg.width(), dlg.height()
        x = max(geo.left() + 4,
                min(btn_pos.x() + anchor_btn.width() // 2 - dw // 2,
                    geo.right() - dw - 4))
        y = btn_pos.y() + 6
        if y + dh > geo.bottom() - 4:          # below would clip → open above
            y = max(geo.top() + 4,
                    anchor_btn.mapToGlobal(QPoint(0, 0)).y() - dh - 6)
        dlg.move(x, y)
        dlg.finished.connect(lambda _r: self.player_bar.rate_btn.setToolTip(
            f"Playback speed: {self.fx.current_rate():g}x"))
        dlg.show()

    def _on_ab_button(self):
        """First click sets A, second sets B (loop), third clears."""
        if not getattr(self, "fx", None):
            return
        if not self.fx.ab_active():
            if self.fx.a_ms is None:
                a = self.fx.mark_a()
                self.player_bar.ab_btn.setChecked(True)
                self.player_bar.ab_btn.setToolTip(
                    f"A-B Repeat: A set at {fmt_time(a or 0)} — click again to set B")
            else:
                b = self.fx.mark_b()
                self.player_bar.ab_btn.setToolTip(
                    f"A-B loop active {fmt_time(self.fx.a_ms)} → {fmt_time(b or 0)} — click to clear")
        else:
            self.fx.clear_ab()
            self.player_bar.ab_btn.setChecked(False)
            self.player_bar.ab_btn.setToolTip("A-B Repeat")

    def _play_path_from_playlist(self, path):
        """Play `path` via the playlist when present, or directly (history
        entry may have been removed from the current list)."""
        try:
            idx = self.current_playlist.index(path)
        except ValueError:
            # Track no longer in the active playlist — play it standalone so
            # history navigation never dies after library edits.
            # Mirror _load_and_play_current's per-track resets so the
            # standalone path doesn't leak stale A-B loops, stale 70%
            # meters, or a stale current_index into the next track.
            self._err_skip_count = 0
            if getattr(self, "fx", None):
                self.fx.clear_ab()
            self._finalize_listen_credit()
            self._listened_ms = 0
            self.current_track = path
            self.current_index = -1   # no valid position in current_playlist
            if path in self._tag_cache:
                title, artist = self._tag_cache[path]
            else:
                try:
                    title, artist = extract_title_artist(path)
                    self._tag_cache[path] = (title, artist)
                except Exception:
                    title = os.path.splitext(os.path.basename(path))[0]
                    artist = "Unknown Artist"
                    self._tag_cache[path] = (title, artist)
            self.now_title.setMarqueeText(title)
            self.now_artist.setMarqueeText(artist)
            self.player_bar.title_label.setMarqueeText(title)
            self.player_bar.artist_label.setMarqueeText(artist)
            self.player_bar.time_total.setText("0:00")
            self.player_bar.time_current.setText("0:00")
            self.player_bar.seek_slider.setValue(0)
            self.player_bar.update_like_button(path in self.favorites)
            self._load_cover_async(path)
            self._highlight_playing_in_lists()
            self._push_history(path)
            if path in getattr(self, "shuffle_unplayed", set()):
                self.shuffle_unplayed.discard(path)
            self.audio.set_volume(self.player_bar.vol_slider.value())
            self.audio.load_and_play(path)
            if getattr(self, "fx", None):
                self.fx.fade_in_from_silence()
                rate = self.fx.current_rate()
                if abs(rate - 1.0) > 1e-6:
                    self.fx.set_rate(rate)
            if self.mini_player and self.mini_player.isVisible():
                self.mini_player.update_track(
                    title, artist, self.player_bar._cover_thumb_pm)
            if getattr(self, "tray_menu_widget", None) is not None:
                self.tray_menu_widget.update_now_playing(
                    title, artist, self.player_bar._cover_thumb_pm)
            return
        self.current_index = idx
        self._load_and_play_current()

    def _on_next(self):
        if not self.current_playlist:
            return
        # A successful manual/auto advance means the previous error is over —
        # reset the consecutive-error counter (used by _on_audio_error).
        self._err_skip_count = 0
        if self.repeat_mode == RepeatMode.ONE and self.current_track:
            # Repeat-one restart is a fresh listen of the same track: credit
            # the finished pass under the 70% rule, then reset the meter.
            self._finalize_listen_credit()
            self._listened_ms = 0
            self.audio.set_position(0)
            self.audio.play()
            return
        # Forward through history first (redo branch) — applies to shuffle too
        if self.redo_stack:
            nxt = self.redo_stack.pop()
            self.play_history.append(nxt)
            del self.play_history[:-self._HISTORY_MAX]
            self._nav_via_history = True
            try:
                self._play_path_from_playlist(nxt)
            finally:
                self._nav_via_history = False
            return
        if self.shuffle:
            # Fresh random jump — only when there is nothing to redo.
            # Smart shuffle: prefer tracks that haven't played yet this
            # session so everything gets a turn before anything repeats.
            candidates = [p for p in self.shuffle_unplayed
                          if p in set(self.current_playlist)]
            if len(self.current_playlist) > 1:
                if candidates:
                    chosen = random.choice(candidates)
                    self.shuffle_unplayed.discard(chosen)
                else:
                    # Everything has played — plain shuffle over the list
                    idx = self.current_index
                    while idx == self.current_index:
                        idx = random.randrange(len(self.current_playlist))
                    chosen = self.current_playlist[idx]
                self.current_index = max(0, self.current_playlist.index(chosen)) \
                    if chosen in self.current_playlist else 0
            else:
                self.current_index = 0
        else:
            self.current_index += 1
            if self.current_index >= len(self.current_playlist):
                if self.repeat_mode == RepeatMode.ALL:
                    self.current_index = 0
                else:
                    self.current_index = len(self.current_playlist) - 1
                    self.audio.stop()
                    self.player_bar.update_play_button(False)
                    return
        self._load_and_play_current()

    def _on_prev(self):
        if not self.current_playlist:
            return
        pos = self.audio.position()
        if pos > 3000:
            self.audio.set_position(0)
            return
        # History-aware Previous: in shuffle mode this must go back to the
        # track that ACTUALLY played before, not pick a new random one.
        if len(self.play_history) >= 2:
            cur = self.play_history[-1]
            prev_path = self.play_history[-2]
            if prev_path == cur:
                # Defensive: deduped history should not contain adjacent dupes
                return
            self.play_history.pop()
            self.redo_stack.append(cur)
            self._nav_via_history = True
            try:
                self._play_path_from_playlist(prev_path)
            finally:
                self._nav_via_history = False
            return
        # No usable history — fall back to sequential behavior
        if self.shuffle:
            if len(self.current_playlist) > 1:
                idx = self.current_index
                while idx == self.current_index:
                    idx = random.randrange(len(self.current_playlist))
                self.current_index = idx
            else:
                self.current_index = 0
        else:
            self.current_index -= 1
            if self.current_index < 0:
                if self.repeat_mode == RepeatMode.ALL:
                    self.current_index = len(self.current_playlist) - 1
                else:
                    self.current_index = 0
        self._load_and_play_current()

    def _on_like_toggled(self):
        if self.current_track is None:
            return
        self._toggle_favorite(self.current_track)

    def _on_shuffle_toggled(self):
        self.shuffle = self.player_bar.shuffle_btn.isChecked()
        self.player_bar.update_shuffle_button(self.shuffle)
        # Smart shuffle: when turning shuffle ON mid-queue, seed the unplayed
        # set with everything except the current track so unplayed tracks
        # get priority from this point on.
        if self.shuffle and self.current_playlist:
            self.shuffle_unplayed = set(self.current_playlist)
            self.shuffle_unplayed.discard(self.current_track)

    def _on_repeat_toggled(self):
        if self.repeat_mode == RepeatMode.OFF:
            self.repeat_mode = RepeatMode.ALL
        elif self.repeat_mode == RepeatMode.ALL:
            self.repeat_mode = RepeatMode.ONE
        else:
            self.repeat_mode = RepeatMode.OFF
        self.player_bar.update_repeat_button(self.repeat_mode)
        self._save_config()

    def _on_seek_pressed(self):
        self.user_is_seeking = True

    def _on_seek_moved(self, val):
        """Called while dragging or clicking on seek bar."""
        dur = self.audio.duration()
        if dur > 0:
            new_pos = int(dur * val / 1000.0)
            self.player_bar.time_current.setText(fmt_time(new_pos))

    def _on_seek_released(self):
        # Seek to the final drag position FIRST, then clear the flag so the
        # UI tick resumes following the (new) playback position.
        val = self.player_bar.seek_slider.value()
        dur = self.audio.duration()
        if dur > 0:
            new_pos = int(dur * val / 1000.0)
            self.audio.set_position(new_pos)
            self.player_bar.time_current.setText(fmt_time(new_pos))
        self.user_is_seeking = False

    def _on_seek_value_changed(self, val):
        """Only called when the USER changes the slider (click or drag).
        Programmatic updates use blockSignals(True) so this doesn't fire.
        """
        if not self.user_is_seeking:
            # This is a click-to-seek (user clicked on the groove)
            dur = self.audio.duration()
            if dur > 0:
                new_pos = int(dur * val / 1000.0)
                self.audio.set_position(new_pos)
                self.player_bar.time_current.setText(fmt_time(new_pos))
        else:
            # During drag, just update the time label
            dur = self.audio.duration()
            if dur > 0:
                self.player_bar.time_current.setText(fmt_time(int(dur * val / 1000.0)))

    def _on_volume_changed(self, val):
        self.audio.set_volume(val)
        self.player_bar.update_volume_button(val)
        if val > 0:
            self._last_volume = val
        self._save_config_debounced()

    def _on_mute_toggle(self):
        if self.player_bar.vol_slider.value() == 0:
            self.player_bar.vol_slider.setValue(self._last_volume or 80)
        else:
            self._last_volume = self.player_bar.vol_slider.value()
            self.player_bar.vol_slider.setValue(0)

    def _on_position_changed(self, pos):
        if not self.user_is_seeking:
            dur = self.audio.duration()
            if dur > 0:
                # Block signals to prevent feedback loop:
                # setValue → valueChanged → _on_seek_value_changed → set_position → position reset
                self.player_bar.seek_slider.blockSignals(True)
                self.player_bar.seek_slider.setValue(int(pos * 1000 / dur))
                self.player_bar.seek_slider.blockSignals(False)
            self.player_bar.time_current.setText(fmt_time(pos))

    def _on_duration_changed(self, dur):
        self.player_bar.time_total.setText(fmt_time(dur))
        if not self.user_is_seeking:
            pos = self.audio.position()
            if dur > 0:
                # Block signals here too
                self.player_bar.seek_slider.blockSignals(True)
                self.player_bar.seek_slider.setValue(int(pos * 1000 / dur))
                self.player_bar.seek_slider.blockSignals(False)

    def _on_state_changed(self, state):
        if state == AudioBackend.STATE_PLAYING:
            self.player_bar.update_play_button(True)
            if self.mini_player and self.mini_player.isVisible():
                self.mini_player.update_play_state(True)
        elif state == AudioBackend.STATE_PAUSED:
            self.player_bar.update_play_button(False)
            if self.mini_player and self.mini_player.isVisible():
                self.mini_player.update_play_state(False)
        elif state == AudioBackend.STATE_STOPPED:
            self.player_bar.update_play_button(False)
            if self.mini_player and self.mini_player.isVisible():
                self.mini_player.update_play_state(False)
        # Keep the tray popup's play/pause icon in sync too (it stays alive
        # while hidden, so it must follow every state flip).
        if getattr(self, "tray_menu_widget", None) is not None:
            self.tray_menu_widget.update_play_state(
                state == AudioBackend.STATE_PLAYING)

    def _on_audio_error(self, msg):
        # A corrupt / unsupported file must never stall the player on a dead
        # track: finalize its (uncounted) listen credit, then auto-skip to
        # the next track (guarding against a playlist full of bad files by
        # capping consecutive skips).
        if self.tray and self.tray.isVisible():
            self.tray.showMessage(APP_NAME, f"Audio: {msg}", QSystemTrayIcon.MessageIcon.Warning, 3000)
        try:
            bad = self.current_track
            if bad:
                self._finalize_listen_credit()
                self.current_track = None
                n = getattr(self, "_err_skip_count", 0) + 1
                self._err_skip_count = n
                if bad in (self.current_playlist or []):
                    if n <= max(3, len(self.current_playlist)):
                        log.warning(f"Auto-skipping unreadable file ({n}): {bad}")
                        QTimer.singleShot(400, self._on_next)
                        return
                self._err_skip_count = 0
        except Exception as ex:
            log.warning(f"error auto-skip failed: {ex}")

    def _ui_tick(self):
        if self.audio.state() == AudioBackend.STATE_PLAYING and not self.user_is_seeking:
            pos = self.audio.position()
            dur = self.audio.duration()
            if dur > 0:
                # Block signals to prevent feedback loop
                self.player_bar.seek_slider.blockSignals(True)
                self.player_bar.seek_slider.setValue(int(pos * 1000 / dur))
                self.player_bar.seek_slider.blockSignals(False)
            self.player_bar.time_current.setText(fmt_time(pos))
            self._on_lyrics_position_tick(pos)
        # Stats: accumulate listening time while playing (0.5 s per 500 ms
        # tick) + per-track listen metering for the 70% credit rule.
        if self.audio.state() == AudioBackend.STATE_PLAYING:
            if getattr(self, "stats", None):
                self.stats.add_seconds(0.5)
            if self.current_track is not None:
                self._listened_ms = getattr(self, "_listened_ms", 0) + 500
        # Toast notification when the window is hidden
        if getattr(self, "_toast_enabled", True):
            self._maybe_toast_track_change()

    # Sleep timer
    def start_sleep_timer(self, minutes):
        self.sleep_timer_active = True
        self.sleep_timer_minutes = minutes
        if self._sleep_timer is None:
            self._sleep_timer = QTimer(self)
            self._sleep_timer.setSingleShot(True)
            self._sleep_timer.timeout.connect(self._on_sleep_timer_done)
        self._sleep_timer.start(minutes * 60 * 1000)
        if self.tray and self.tray.isVisible():
            self.tray.showMessage(APP_NAME, f"Sleep timer: will stop in {minutes} min",
                QSystemTrayIcon.MessageIcon.Information, 2000)

    def stop_sleep_timer(self):
        self.sleep_timer_active = False
        if self._sleep_timer is not None:
            self._sleep_timer.stop()

    def _on_sleep_timer_done(self):
        self.sleep_timer_active = False
        self.audio.stop()
        self.player_bar.update_play_button(False)
        if self.tray and self.tray.isVisible():
            self.tray.showMessage(APP_NAME, "Sleep timer: playback stopped", QSystemTrayIcon.MessageIcon.Information, 3000)

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            # Left-click: toggle show/hide
            self._toggle_visible()
        elif reason == QSystemTrayIcon.ActivationReason.MiddleClick:
            self._on_play_pause()
        elif reason == QSystemTrayIcon.ActivationReason.Context:
            # Right-click: our own styled popup instead of the gray native menu
            self._show_tray_popup()

    def _current_track_tags(self) -> tuple:
        """(title, artist) for the playing track from cache only — never runs
        mutagen on the UI thread; falls back to the filename stem."""
        if not self.current_track:
            return "", ""
        title, artist = self._tag_cache.get(self.current_track, ("", ""))
        if not title:
            title = os.path.splitext(os.path.basename(self.current_track))[0]
        return title, artist

    def _tray_sync_now_playing(self):
        """Push the current track's info (title / artist / cover) into the
        tray popup header from live app state."""
        if getattr(self, "tray_menu_widget", None) is None:
            return
        if self.current_track:
            title, artist = self._current_track_tags()
            self.tray_menu_widget.update_now_playing(
                title, artist, self.player_bar._cover_thumb_pm)
        else:
            self.tray_menu_widget.update_now_playing()

    def _show_tray_popup(self):
        """Show the custom tray popup above the taskbar, near the cursor."""
        from PyQt6.QtGui import QCursor
        pos = QCursor.pos()
        # Update play state, theme and now-playing header
        self.tray_menu_widget._apply_theme(self.theme)
        playing = self.audio.state() == AudioBackend.STATE_PLAYING
        self.tray_menu_widget.update_play_state(playing)
        self._tray_sync_now_playing()
        menu_size = self.tray_menu_widget.size()
        # X: centered on the cursor, clamped to the taskbar's screen
        for scr in QApplication.screens():
            if scr.geometry().contains(pos):
                screen_geo = scr.availableGeometry()
                break
        else:
            screen_geo = QApplication.primaryScreen().availableGeometry()
        x = pos.x() - menu_size.width() // 2
        x = max(screen_geo.left() + 4,
                min(x, screen_geo.right() - menu_size.width() - 4))
        # Y: bottom edge sits exactly on top of the taskbar line
        y = screen_geo.bottom() - menu_size.height() + 1
        self.tray_menu_widget.move(x, y)
        self.tray_menu_widget.show()
        self.tray_menu_widget.raise_()
        self.tray_menu_widget.activateWindow()

    def _toggle_visible(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()

    # ------------------------------------------------------------------
    # Drag & drop from the OS file manager
    # ------------------------------------------------------------------
    _DROP_MAX_FILES = 5000  # sanity cap per drop event

    def dragEnterEvent(self, e):
        """Accept drops that contain at least one local dir/audio file."""
        md = e.mimeData()
        if not md.hasUrls():
            return
        for url in md.urls()[:self._DROP_MAX_FILES]:
            if not url.isLocalFile():
                continue
            p = Path(url.toLocalFile())
            if p.is_dir() or p.suffix.lower() in AUDIO_EXTS:
                e.acceptProposedAction()
                return

    def dropEvent(self, e):
        md = e.mimeData()
        folders, files = [], []
        for url in md.urls()[:self._DROP_MAX_FILES]:
            if not url.isLocalFile():
                continue
            p = Path(url.toLocalFile())
            if p.is_dir():
                folders.append(str(p))
            elif p.suffix.lower() in AUDIO_EXTS and p.exists():
                files.append(str(p))
        if not folders and not files:
            return
        # Add folders to scan list (dedup against existing)
        new_folders = [f for f in folders if f not in self.added_folders]
        self.added_folders.extend(new_folders)
        # Direct file drops join the library immediately
        existing = set(self.library)
        added_files = [f for f in files if f not in existing]
        self.library.extend(added_files)
        if new_folders:
            self._start_scan(new_folders, label="Scanning dropped folder...")
        else:
            # No folder scan pending — refresh lists directly. Dropped files
            # may live outside every known folder → tree must refresh once.
            self.library.sort(key=lambda x: os.path.basename(x).lower())
            self._rebuild_library_list(rebuild_tree=True)
            self._update_count_label()
            self._save_config()
        if added_files or new_folders:
            n = len(added_files) + len(new_folders)
            log.info(f"Drop: +{len(added_files)} files, +{len(new_folders)} folders")

    def changeEvent(self, e):
        """When enabled in Settings, clicking the taskbar icon (minimize)
        sends the window to the tray instead."""
        from PyQt6.QtCore import QEvent
        if (e.type() == QEvent.Type.WindowStateChange
                and self.windowState() & Qt.WindowState.WindowMinimized
                and getattr(self, "taskbar_close_to_tray", False)):
            # Defer so the window state settles before hiding
            QTimer.singleShot(0, self._minimize_to_tray)
        super().changeEvent(e)

    def _minimize_to_tray(self):
        if self.windowState() & Qt.WindowState.WindowMinimized:
            self.hide()
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
            if self.tray and getattr(self, "_tray_minimize_notice_shown", True):
                self._tray_minimize_notice_shown = False   # tell once per session
                self.tray.showMessage(APP_NAME,
                    f"{APP_NAME} keeps playing in the tray.",
                    QSystemTrayIcon.MessageIcon.Information, 2000)

    def closeEvent(self, e):
        if self._force_quit:
            self._save_config()
            if self.scanner is not None and self.scanner.isRunning():
                self.scanner.cancel()
                self.scanner.wait(2000)
            # Cancel remaining worker threads so nothing is mid-emit when the
            # interpreter tears down (avoids QThread destroyed warnings/crashes)
            for attr in ("_tag_loader", "_cover_loader", "_albums_worker"):
                t = getattr(self, attr, None)
                if t is not None:
                    try:
                        t.cancel()
                    except AttributeError:
                        pass
                    if t.isRunning():
                        t.wait(1000)
            lp = getattr(self, "_lyrics_panel", None)
            if lp is not None and getattr(lp, "_loader", None) is not None:
                lp._loader.cancel()
                if lp._loader.isRunning():
                    lp._loader.wait(1000)
            if self.mini_player is not None:
                self.mini_player.close()
            e.accept()
        else:
            e.ignore()
            self.hide()
            if self.tray:
                self.tray.showMessage(APP_NAME, f"{APP_NAME} is running in the tray.",
                    QSystemTrayIcon.MessageIcon.Information, 2500)

    def _quit(self):
        self._force_quit = True
        if self.tray:
            self.tray.hide()
        # Tear down worker threads BEFORE quitting the event loop — the old
        # code called QApplication.quit() directly, leaving CoverLoader /
        # TagLoader / AlbumsWorker / LyricsLoader mid-emit while the
        # interpreter tore down ("QThread: Destroyed while thread is still
        # running" hard-crash on exit under load).
        # close() runs MainWindow.closeEvent, which waits on every worker;
        # tray.hide() above means closeEvent takes the _force_quit branch.
        self.close()
        QApplication.quit()

    _save_timer = None

    def _save_config_debounced(self):
        if self._save_timer is None:
            self._save_timer = QTimer(self)
            self._save_timer.setSingleShot(True)
            self._save_timer.setInterval(800)
            self._save_timer.timeout.connect(self._save_config)
        self._save_timer.start()

    def _save_config(self):
        cfg = {
            "version": APP_VERSION,
            "theme": self.theme_name,
            "library": self.library,
            "favorites": sorted(self.favorites),
            "added_folders": self.added_folders,
            "playlists": {name: paths for name, paths in self.playlists.items()
                          if isinstance(name, str) and isinstance(paths, list)},
            "volume": self.player_bar.vol_slider.value() if hasattr(self, "player_bar") else 80,
            "last_track": self.current_track if self.remember_track else None,
            "last_position": self.audio.position() if self.remember_track and self.audio.available else 0,
            "shuffle": self.shuffle,
            "repeat_mode": self.repeat_mode.value,
            # Persist geometry even while hidden-to-tray: Qt remembers the
            # pre-hide normal geometry, so quitting from the tray no longer
            # resets window position/size on next launch.
            "geometry": self.saveGeometry().data().hex(),
            "auto_rescan": self.auto_rescan,
            "remember_track": self.remember_track,
            "taskbar_close_to_tray": self.taskbar_close_to_tray,
            "default_folder": self.default_folder,
            "sleep_timer_active": self.sleep_timer_active,
            "sleep_timer_minutes": self.sleep_timer_minutes,
            "sort_mode": self.sort_mode.value,
            # Settings-dialog fields that previously reset on every restart
            "lyrics_online": bool(getattr(self, "lyrics_online_enabled", True)),
            "audio_output_id": str(getattr(self, "audio_output_id", "") or ""),
            "auto_theme": bool(getattr(self, "auto_theme_enabled", False)),
            "toast_enabled": bool(getattr(self, "_toast_enabled", True)),
            "visualizer_enabled": bool(getattr(self, "visualizer_enabled", True)),
            "default_volume": self._validated_int(getattr(self, "_last_volume", 80), 80, 0, 100),
            "fade_enabled": bool(getattr(self.fx, "enabled_fade", True)) if getattr(self, "fx", None) else True,
            "fade_ms": self._validated_int(getattr(self.fx, "fade_ms", 300), 300, 100, 2000) if getattr(self, "fx", None) else 300,
            "show_ab_button": (self.player_bar.ab_btn.isVisible() if hasattr(self, "player_bar") else False),
            "show_rate_button": (self.player_bar.rate_btn.isVisible() if hasattr(self, "player_bar") else False),
            "show_lyrics_button": (self.player_bar.lyrics_btn.isVisible() if hasattr(self, "player_bar") else True),
        }
        try:
            p = config_path()
            p.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
            restrict_file_permissions(p)
        except Exception as e:
            log.error(f"Config save error: {e}")
        # Also save tag cache
        self._save_tag_cache()

    def _load_config(self):
        cfg, ok = load_json_file(config_path(), "config")
        if not ok:
            return

        self.theme_name = cfg.get("theme", "royal_gold")
        self.library = cfg.get("library", [])
        self.favorites = set(cfg.get("favorites", []))
        self.added_folders = cfg.get("added_folders", [])
        raw_pl = cfg.get("playlists", {})
        if isinstance(raw_pl, dict):
            self.playlists = {str(k): [p for p in v if isinstance(p, str)]
                              for k, v in raw_pl.items()
                              if isinstance(k, str) and isinstance(v, list)}
        else:
            self.playlists = {}
        vol = cfg.get("volume", 80)
        self._last_volume = vol
        if hasattr(self, "player_bar"):
            self.player_bar.vol_slider.setValue(vol)
        self.shuffle = cfg.get("shuffle", False)
        self.player_bar.update_shuffle_button(self.shuffle)
        rm = cfg.get("repeat_mode", 0)
        self.repeat_mode = RepeatMode(rm)
        self.player_bar.update_repeat_button(self.repeat_mode)
        self.auto_rescan = cfg.get("auto_rescan", True)
        self.remember_track = cfg.get("remember_track", True)
        self.taskbar_close_to_tray = bool(cfg.get("taskbar_close_to_tray", False))
        self.default_folder = cfg.get("default_folder", "")
        self.sleep_timer_active = cfg.get("sleep_timer_active", False)
        self.sleep_timer_minutes = cfg.get("sleep_timer_minutes", 30)
        sm = cfg.get("sort_mode", "title")
        self.sort_mode = SortMode(sm)
        if hasattr(self, "sort_combo"):
            idx = self.sort_combo.findData(sm)
            if idx >= 0:
                self.sort_combo.setCurrentIndex(idx)
        geom_hex = cfg.get("geometry")
        if geom_hex:
            try:
                self.restoreGeometry(bytes.fromhex(geom_hex))
            except Exception:
                pass
        self._apply_theme()
        self.library = [p for p in self.library if os.path.exists(p)]
        self.favorites = {p for p in self.favorites if os.path.exists(p)}
        self._rebuild_library_list()
        self._rebuild_favorites_list()
        self._update_count_label()
        if self.sleep_timer_active:
            self.start_sleep_timer(self.sleep_timer_minutes)
        last = cfg.get("last_track")
        last_pos = cfg.get("last_position", 0)
        if last and last in self.library and self.remember_track:
            idx = self.library.index(last)
            self.current_playlist = list(self.library)
            self.current_index = idx
            self.current_track = last
            title, artist = extract_title_artist(last)
            self.now_title.setMarqueeText(title)
            self.now_artist.setMarqueeText(artist)
            self.player_bar.title_label.setMarqueeText(title)
            self.player_bar.artist_label.setMarqueeText(artist)
            self.player_bar.update_like_button(last in self.favorites)
            self._highlight_playing_in_lists()
            self._load_cover_async(last)
            self.audio.load(last)
            QTimer.singleShot(500, lambda: self._restore_position(last, last_pos))

    def _load_config_async(self):
        """Load config in background — non-blocking.
        Shows a loading indicator while reading config file.
        """
        # Show loading state
        self.scan_status_label.setText("Loading library...")
        self.scan_status_label.setVisible(True)
        self.scan_progress.setVisible(True)
        self.scan_progress.setRange(0, 0)  # indeterminate

        # Read config file (fast — just JSON, validated)
        cfg, ok = load_json_file(config_path(), "config")
        if not ok:
            self._finish_config_load(None)
            return

        # Apply config on main thread (fast operations only)
        self._finish_config_load(cfg)

    @staticmethod
    def _validated_int(value, default: int, lo: int, hi: int) -> int:
        """Coerce a config value to an int in [lo, hi]; default on any junk."""
        try:
            v = int(value)
        except (TypeError, ValueError):
            return default
        return max(lo, min(hi, v))

    @staticmethod
    def _validated_enum(enum_cls, value, default):
        """Coerce a config value to an enum member; default on any junk."""
        try:
            return enum_cls(value)
        except ValueError:
            return default

    def _finish_config_load(self, cfg):
        """Apply loaded config — fast operations only, no tag reading."""
        if cfg is None:
            cfg = {}

        self.scan_status_label.setVisible(False)
        self.scan_progress.setVisible(False)

        # Default to Royal Gold for new installs (v2.0.0 psychology
        # suite); migrate pre-2.0 theme ids so users keep their vibe.
        self.theme_name = Theme.normalize(cfg.get("theme", Theme.DEFAULT_NAME))
        if self.theme_name not in Theme.names():
            self.theme_name = Theme.DEFAULT_NAME
        self.theme = Theme.get(self.theme_name)
        self.library = cfg.get("library", [])
        self.favorites = set(cfg.get("favorites", []))
        self.added_folders = cfg.get("added_folders", [])
        raw_pl = cfg.get("playlists", {})
        if isinstance(raw_pl, dict):
            self.playlists = {str(k): [p for p in v if isinstance(p, str)]
                              for k, v in raw_pl.items()
                              if isinstance(k, str) and isinstance(v, list)}
        else:
            self.playlists = {}
        # Corrupt/tampered values must never abort the whole load — fall
        # back to defaults per field (ValueError/TypeError safe).
        vol = self._validated_int(cfg.get("volume", 80), 80, 0, 100)
        self._last_volume = vol
        self.player_bar.vol_slider.setValue(vol)
        self.shuffle = bool(cfg.get("shuffle", False))
        self.player_bar.update_shuffle_button(self.shuffle)
        self.repeat_mode = self._validated_enum(
            RepeatMode, cfg.get("repeat_mode", 0), RepeatMode.OFF)
        self.player_bar.update_repeat_button(self.repeat_mode)
        self.auto_rescan = cfg.get("auto_rescan", True)
        self.remember_track = cfg.get("remember_track", True)
        self.taskbar_close_to_tray = bool(cfg.get("taskbar_close_to_tray", False))
        self.default_folder = cfg.get("default_folder", "")
        sm = cfg.get("sort_mode", "title")
        try:
            self.sort_mode = SortMode(sm)
        except ValueError:
            self.sort_mode = SortMode.TITLE
        # Settings-dialog fields — restore what _save_config persisted
        self.lyrics_online_enabled = bool(cfg.get("lyrics_online", True))
        self.audio_output_id = str(cfg.get("audio_output_id", "") or "")
        self.auto_theme_enabled = bool(cfg.get("auto_theme", False))
        self._toast_enabled = bool(cfg.get("toast_enabled", True))
        self.visualizer_enabled = bool(cfg.get("visualizer_enabled", True))
        self._last_volume = self._validated_int(cfg.get("default_volume", vol), vol, 0, 100)
        if getattr(self, "fx", None):
            self.fx.enabled_fade = bool(cfg.get("fade_enabled", True))
            self.fx.fade_ms = self._validated_int(cfg.get("fade_ms", 300), 300, 100, 2000)
        # Sleep timer is persisted so it survives restarts too
        self.sleep_timer_minutes = self._validated_int(
            cfg.get("sleep_timer_minutes", 30), 30, 1, 480)
        if bool(cfg.get("sleep_timer_active", False)):
            self.start_sleep_timer(self.sleep_timer_minutes)
        if hasattr(self, "sort_combo"):
            idx = self.sort_combo.findData(sm)
            if idx >= 0:
                self.sort_combo.setCurrentIndex(idx)
        geom_hex = cfg.get("geometry")
        if geom_hex:
            try:
                self.restoreGeometry(bytes.fromhex(geom_hex))
            except Exception:
                pass
        self._apply_theme()

        # Filter missing files (fast — os.path.exists)
        self.library = [p for p in self.library if os.path.exists(p)]
        self.favorites = {p for p in self.favorites if os.path.exists(p)}
        # The stats seed at __init__ ran before added_folders was known —
        # refresh moved-file search roots now that folders are populated.
        try:
            from playstats import set_search_roots
            _roots = list(self.added_folders or [])
            _roots += list({os.path.dirname(os.path.abspath(p))
                            for p in self.library if p})
            set_search_roots(_roots)
        except Exception:
            pass

        # Rebuild list FAST (filenames only, no tag reading)
        # Load tag cache first (instant — no I/O)
        self._load_tag_cache()
        self._rebuild_library_list()
        self._rebuild_favorites_list()
        self._update_count_label()

        # Restore last track (if any)
        last = cfg.get("last_track")
        last_pos = cfg.get("last_position", 0)
        if last and last in self.library and self.remember_track:
            idx = self.library.index(last)
            self.current_playlist = list(self.library)
            self.current_index = idx
            self.current_track = last
            # Use cached tags if available, otherwise filename
            if last in self._tag_cache:
                title, artist = self._tag_cache[last]
            else:
                title = os.path.splitext(os.path.basename(last))[0]
                artist = "Unknown Artist"
            self.now_title.setMarqueeText(title)
            self.now_artist.setMarqueeText(artist)
            self.player_bar.title_label.setMarqueeText(title)
            self.player_bar.artist_label.setMarqueeText(artist)
            self.player_bar.update_like_button(last in self.favorites)
            self._highlight_playing_in_lists()
            self._load_cover_async(last)
            # Load media without playing (user can press play)
            self.audio.load(last)
            QTimer.singleShot(500, lambda: self._restore_position(last, last_pos))

        # Start rescan in background
        if self.auto_rescan:
            QTimer.singleShot(100, self._startup_rescan)

        # Apply restored UI toggles + audio output + auto-theme (all were
        # persisted by _save_config; see FUNC-4 fix — these used to reset
        # to defaults on every restart).
        if hasattr(self, "player_bar"):
            self.player_bar.ab_btn.setVisible(bool(cfg.get("show_ab_button", False)))
            self.player_bar.rate_btn.setVisible(bool(cfg.get("show_rate_button", False)))
            self.player_bar.lyrics_btn.setVisible(bool(cfg.get("show_lyrics_button", True)))
        if self.audio_output_id:
            try:
                from audio_output import apply_output_device
                apply_output_device(self.audio, self.audio_output_id)
            except Exception as e:
                log.warning(f"restored audio output unavailable: {e}")
        if self.auto_theme_enabled:
            self._apply_auto_theme_setting()

        # Warm album groups + covers in the background so the Albums page is
        # instant on first visit (user request: preload, keep, revisit fast).
        QTimer.singleShot(1500, self._preload_albums_background)

    def _restore_position(self, path, pos):
        if self.current_track == path:
            self.audio.set_position(pos)
            self.player_bar.time_current.setText(fmt_time(pos))
            dur = self.audio.duration()
            if dur > 0:
                # Block signals to prevent feedback loop
                self.player_bar.seek_slider.blockSignals(True)
                self.player_bar.seek_slider.setValue(int(pos * 1000 / dur))
                self.player_bar.seek_slider.blockSignals(False)


# ============================================================================
# Single-instance support (Windows)
# ============================================================================
_mutex_handle = None

def _acquire_single_instance_lock() -> bool:
    """Hold a named mutex for the process lifetime.

    Returns True if we are the first instance. The handle is kept in a module
    global so it survives for the whole process — releasing on exit is optional
    since Windows cleans up abandoned mutexes automatically.
    """
    global _mutex_handle
    try:
        import win32event
        import win32api
        import winerror
        _mutex_handle = win32event.CreateMutex(None, False, "GoldenMusic_SingleInstance_Mutex")
        if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
            return False
        return True
    except ImportError:
        # pywin32 missing — fall back to a lock-file heuristic. A crashed run
        # leaves the file behind, so verify the recorded pid is still alive
        # before refusing to start.
        try:
            lock = config_path().parent / "instance.lock"
            if lock.exists():
                try:
                    pid = int(lock.read_text(encoding="utf-8").strip() or 0)
                except (OSError, ValueError):
                    pid = 0
                if pid and pid != os.getpid():
                    try:
                        os.kill(pid, 0)     # raises OSError if pid is gone
                        return False        # a live instance really is running
                    except PermissionError:
                        return False        # exists but not ours — treat as running
                    except OSError:
                        pass                # stale lock from a crashed run
                lock.write_text(str(os.getpid()), encoding="utf-8")
                return True
            lock.write_text(str(os.getpid()), encoding="utf-8")
            return True
        except OSError:
            return True  # never block the app over a lock failure
    except Exception as e:
        log.warning(f"Single-instance lock unavailable: {e}")
        return True


def _focus_existing_instance():
    """Bring the already-running window to the foreground (no error shown)."""
    log.info("Second launch detected — focusing existing instance")
    try:
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QSharedMemory
        # Preferred path: running instance publishes its window title via
        # shared memory so we can find and raise it.
        shm = QSharedMemory("GoldenMusic_WindowTitle")
        if shm.attach(QSharedMemory.AccessMode.ReadOnly) and shm.data() is not None:
            raw = bytes(shm.data()[:shm.size()]).split(b"\x00", 1)[0]
            title = raw.decode("utf-8", errors="replace")
            shm.detach()
            if title and _raise_window_by_title(title):
                return
        # Fallback: match by exact window title prefix
        if _raise_window_by_title(APP_NAME):
            return
        log.warning("Existing instance found but its window could not be raised")
    except Exception as e:
        log.warning(f"Could not focus existing instance: {e}")


def _raise_window_by_title(title_substring: str) -> bool:
    """Win32: find a visible top-level window whose title contains the
    substring, restore it if minimized, and bring it to the foreground."""
    try:
        import win32gui
        import win32con
        found = []

        def enum_handler(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return
            text = win32gui.GetWindowText(hwnd)
            if title_substring in text:
                found.append(hwnd)

        win32gui.EnumWindows(enum_handler, None)
        if not found:
            return False
        hwnd = found[0]
        if win32gui.IsIconic(hwnd):  # minimized -> restore first
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        return True
    except Exception as e:
        log.warning(f"raise_window_by_title failed: {e}")
        return False


def _install_crash_guard(app):
    """Log unhandled exceptions AND exceptions raised inside Qt slots.

    sys.excepthook alone never fires for exceptions inside Qt slots in
    PyQt6 — they propagate through QApplication.notify(). A subclassed
    QApplication overrides notify() (the official interception point):
    a slot that throws is logged with full traceback and swallowed so one
    bad handler can't kill the player. threading.excepthook +
    unraisablehook cover worker-thread deaths (scanner, tag/cover loaders).
    Truly fatal conditions (MemoryError) are re-raised.
    """
    import traceback as _tb
    import threading as _threading

    def excepthook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, (KeyboardInterrupt, SystemExit, MemoryError)):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        log.error("Unhandled exception:\n" +
                  "".join(_tb.format_exception(exc_type, exc_value, exc_tb)))

    sys.excepthook = excepthook

    def thread_excepthook(args):
        try:
            if issubclass(args.exc_type,
                           (KeyboardInterrupt, SystemExit, MemoryError)):
                sys.__excepthook__(args.exc_type, args.exc_value,
                                   args.exc_traceback)
                return
            log.error("Unhandled thread exception "
                      f"({getattr(args, 'thread', None) and args.thread.name}):\n" +
                      "".join(_tb.format_exception(args.exc_type, args.exc_value,
                                                   args.exc_traceback)))
        except Exception:
            pass

    _threading.excepthook = thread_excepthook

    try:
        def _unraisable(args):
            try:
                log.error("Unraisable exception "
                          f"({getattr(args, 'where', '')}): "
                          f"{args.exc_value!r}")
            except Exception:
                pass
        sys.unraisablehook = _unraisable
    except Exception:
        pass

    # Qt 6.5+: exceptions in slots propagate through the event loop — route
    # notify() failures to the excepthook above.
    cls = type(app)
    if not getattr(cls, "_gm_notify_guarded", False):
        cls._gm_notify_guarded = True
        _orig_notify = cls.notify

        def _guarded_notify(self, receiver, event):
            try:
                return _orig_notify(self, receiver, event)
            except (KeyboardInterrupt, SystemExit, MemoryError):
                raise
            except Exception:
                excepthook(*sys.exc_info())
                return False

        cls.notify = _guarded_notify


def _enable_windows_glass(widget):
    """Best-effort Mica/Acrylic backdrop (Win 11 22H2+) + dark titlebar.
    Falls back silently on older systems."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = int(widget.winId())
        dwm = ctypes.windll.dwmapi
        try:
            DWMSBT_MAINWINDOW = 2
            dwm.DwmSetWindowAttribute(hwnd, 38,
                ctypes.byref(ctypes.c_int(DWMSBT_MAINWINDOW)), 4)
        except Exception:
            pass
        try:
            dwm.DwmSetWindowAttribute(hwnd, 20,
                ctypes.byref(ctypes.c_int(1)), 4)   # dark chrome
        except Exception:
            pass
        try:
            margins = ctypes.c_int(-1)
            dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
        except Exception:
            pass
    except Exception as e:
        log.debug(f"glass backdrop unavailable: {e}")


def main():
    log_path = setup_logging()
    if sys.platform == 'win32':
        # Single-instance: focus the running window instead of launching a copy
        if not _acquire_single_instance_lock():
            _focus_existing_instance()
            sys.exit(0)
    QApplication.setStyle(QStyleFactory.create("Fusion"))
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(ORG_NAME)
    app.setQuitOnLastWindowClosed(False)
    # Use custom logo as app icon
    icon_path = Path(__file__).parent / "assets" / "icon.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    w = MainWindow()
    _install_crash_guard(app)
    w.show()
    _enable_windows_glass(w)
    if QSystemTrayIcon.isSystemTrayAvailable():
        w.tray.show()
    code = app.exec()
    sys.exit(code)


if __name__ == "__main__":
    main()
