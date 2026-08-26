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
import random
import subprocess
import time
from pathlib import Path
from enum import Enum

from applog import setup_logging

from PyQt6.QtCore import (
    Qt, QSize, QRect, QTimer, QEvent, pyqtSignal, QUrl,
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
            super().setIconSize(QSize(px, px))

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
            # Draw the icon centered (we bypass QPushButton's style chrome —
            # without a stylesheet Fusion would paint a bevel frame here).
            pm = self.icon().pixmap(self.iconSize())
            if not pm.isNull():
                ix = (self.width() - pm.width()) // 2
                iy = (self.height() - pm.height()) // 2
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
        pm = render_icon(self.svg_string, self.icon_size_base, color, dpr)
        self.setIcon(QIcon(pm))
        # NOTE: icon *size* is animated via the icon_px property; here we only
        # refresh the pixmap so the color/shape matches the current state.
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
        # Clicking the cover or the title/artist asks the main window to
        # open the track info dialog (wired via this callback attribute).
        self.cover_click_handler = None   # set by MainWindow
        self.cover_thumb.setCursor(Qt.CursorShape.PointingHandCursor)

        self.title_label = QLabel("No track selected")
        self.title_label.setObjectName("BarTitle")
        self.artist_label = QLabel("—")
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

    def _on_info_click(self, event):
        """Cover / title / artist clicked → ask the main window to open the
        track info dialog (handler is assigned by MainWindow after setup)."""
        from PyQt6.QtCore import QEvent
        if (event.type() == QEvent.Type.MouseButtonPress
                and event.button() == Qt.MouseButton.LeftButton
                and callable(self.cover_click_handler)):
            self.cover_click_handler()

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

        t = self.theme
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

        # Colors — playing/selected rows glow gold; others stay neutral
        if playing or selected:
            num_col = QColor(t["gold"])
            title_col = QColor(t["gold_light"])
        else:
            num_col = QColor(t["muted"])
            title_col = QColor(t["text"])

        x_num = rect.left() + 8
        x_text = x_num + (34 if number else 0)

        # Index number (small, muted/gold)
        if number:
            f_num = QFont(option.font)
            f_num.setPointSizeF(max(8.5, option.font.pointSizeF() - 1.5))
            p.setFont(f_num)
            p.setPen(QPen(num_col))
            p.drawText(QRect(x_num, rect.top(), 30, rect.height()),
                       int(Qt.AlignmentFlag.AlignVCenter), str(number))

        # Title line
        f_title = QFont(option.font)
        f_title.setBold(True)
        f_title.setPointSizeF(option.font.pointSizeF() + 0.5)
        p.setFont(f_title)
        p.setPen(QPen(title_col))
        fm = QFontMetrics(f_title)
        title_rect = QRect(x_text, rect.top() + 4,
                           rect.right() - x_text, fm.height())
        p.drawText(title_rect, int(Qt.AlignmentFlag.AlignVCenter),
                   fm.elidedText(title, Qt.TextElideMode.ElideRight, title_rect.width()))

        # Artist line (muted, smaller) — only when present
        if artist:
            f_artist = QFont(option.font)
            f_artist.setPointSizeF(max(8.5, option.font.pointSizeF() - 2))
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


# ============================================================================
# LoadingOverlay — glassy "Loading…" veil shown during slow operations
# ============================================================================
class LoadingOverlay(QWidget):
    """Semi-transparent overlay with a spinner-ish label. Parent it to the
    main window, call `show_loading(text)` / `hide_loading()`."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setVisible(False)
        self._dots = 0
        self._timer = QTimer(self)
        self._timer.setInterval(350)
        self._timer.timeout.connect(self._tick)

        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card = QFrame()
        card.setFixedSize(220, 90)
        card.setObjectName("LoadingCard")
        card_lay = QVBoxLayout(card)
        card_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label = QLabel("Loading")
        self.label.setObjectName("LoadingLabel")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_lay.addWidget(self.label)
        lay.addWidget(card, alignment=Qt.AlignmentFlag.AlignCenter)

    def _tick(self):
        self._dots = (self._dots + 1) % 4
        base = getattr(self, "_base_text", "Loading")
        self.label.setText(base + "." * self._dots)

    def show_loading(self, text: str = "Loading"):
        self._base_text = text
        self.label.setText(text)
        self._dots = 0
        self.setGeometry(self.parentWidget().rect())
        self.raise_()
        self.setVisible(True)
        self._timer.start()
        QApplication.processEvents()   # paint the overlay before the heavy work

    def hide_loading(self):
        self._timer.stop()
        self.setVisible(False)

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 120))
        # Card background follows the theme
        t = getattr(self.parent(), "theme", None) or {}
        card_color = QColor(t.get("panel_bg", "#241d17"))
        card_color.setAlpha(240)
        p.setPen(QPen(QColor(t.get("gold", "#d4a85a")), 1))
        p.setBrush(QBrush(card_color))
        p.drawRoundedRect(
            (self.width() - 220) // 2, (self.height() - 90) // 2, 220, 90,
            14, 14)


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
            from playstats import PlayStats
            self.stats = PlayStats(config_path().parent / "stats.json")
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
        self._last_toast_key = None

        # Threads
        self.scanner = None
        self._scan_results_pending = None
        self._cover_loader = None

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
            shm = QSharedMemory("GoldenMusic_WindowTitle")
            # Leftover segment from a crashed run — clean it up
            if shm.attach(QSharedMemory.AccessMode.ReadWrite):
                shm.detach()
            data = self.windowTitle().encode("utf-8") + b"\x00"
            if shm.create(len(data)):
                shm.data()[:len(data)] = data
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
        # Cover / title / artist click → track info dialog
        self.player_bar.cover_click_handler = self._on_bar_info_clicked

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
        self.folder_tree.setStyleSheet(f"QTreeWidget {{ background: {self.theme['panel_bg']}; border: 1px solid {self.theme['border']}; border-radius: 10px; padding: 6px; outline: 0; }} QTreeWidget::item {{ padding: 6px 4px; border-radius: 4px; outline: 0; }} QTreeWidget::item:hover {{ background: {self.theme['panel_bg_2']}; outline: 0; }} QTreeWidget::item:selected {{ background: {self.theme['active']}; color: {self.theme['gold_light']}; outline: 0; }}")
        self.folder_tree.itemClicked.connect(self._on_folder_tree_click)
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

        self.now_title = QLabel("No track selected")
        self.now_title.setObjectName("NowTitle")
        self.now_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_col.addWidget(self.now_title)

        self.now_artist = QLabel("Add a folder to get started")
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
        each group's cover + name. Fast path: reuse the tag cache; only fall
        back to mutagen for tracks the background loader hasn't tagged yet."""

        def _first_tag(val):
            if isinstance(val, (list, tuple)):
                return str(val[0]) if val else ""
            return str(val) if val else ""

        import coverart as _ca
        groups = {}   # album_key -> [paths]
        untagged = []
        # Pass 1 — group by cached tags (no I/O at all)
        for p in self.library:
            title, artist = self._tag_cache.get(p, ("", ""))
            if not title and not artist:
                untagged.append(p)
                key = "Unknown Album"
            else:
                key = f"{artist} — Singles" if artist else "Unknown Album"
            groups.setdefault(key, []).append(p)

        # Pass 2 — only for still-untagged tracks read the file once
        if untagged:
            try:
                from mutagen import File as MutagenFile
            except Exception:
                MutagenFile = None
            still_unknown = []
            for p in untagged:
                album = ""
                if MutagenFile is not None:
                    try:
                        m = MutagenFile(p, easy=True)
                        if m is not None and m.tags:
                            album = _first_tag(m.tags.get("album"))
                    except Exception:
                        pass
                if album:
                    groups.setdefault(album, []).append(p)
                else:
                    still_unknown.append(p)
            for p in still_unknown:
                groups.setdefault("Unknown Album", []).append(p)

        dpr = get_dpr()
        self.albums_grid.clear()
        from PyQt6.QtGui import QIcon as _QI
        for name in sorted(groups.keys(), key=str.lower):
            paths = groups[name]
            item = QListWidgetItem(f"{name}\n({len(paths)} tracks)")
            cover_pm = None
            for p in paths[:4]:
                data = _ca.get_cover_bytes(p)
                if data:
                    from PyQt6.QtCore import QByteArray
                    pm = QPixmap()
                    if pm.loadFromData(QByteArray(data)):
                        cover_pm = pm
                        break
            if cover_pm is None:
                cover_pm = render_icon(Icon.MUSIC_NOTE, 120,
                                       self.theme["gold"], dpr)
            item.setIcon(_QI(cover_pm.scaled(
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
        # Re-derive the album group of this representative track
        target_dir = os.path.dirname(first)
        tracks = [p for p in self.library
                  if path_within(p, target_dir) or True]  # keep order stable
        self._play_from_list(tracks, max(0, tracks.index(first))
                             if first in tracks else 0, view=View.LIBRARY)

    # ------------------------------------------------------------------
    # Stats page
    # ------------------------------------------------------------------
    def _build_stats_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(8, 0, 8, 0)
        card = QFrame()
        card.setObjectName("TrackList")   # reuse the glass panel styling
        lay = QVBoxLayout(card)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(14)

        title = QLabel("Your Listening Stats")
        title.setObjectName("NowTitle")
        lay.addWidget(title)

        self.stat_total_label = QLabel("—")
        self.stat_total_label.setObjectName("NowTitle")
        lay.addWidget(self.stat_total_label)
        sub = QLabel("total listening time")
        sub.setStyleSheet(f"color: {self.theme['muted']}; font-size: 12px;")
        lay.addWidget(sub)

        self.stat_top_tracks = QLabel("")
        self.stat_top_tracks.setWordWrap(True)
        lay.addWidget(self.stat_top_tracks)
        self.stat_artists_label = QLabel("")
        self.stat_artists_label.setWordWrap(True)
        lay.addWidget(self.stat_artists_label)
        self.stat_misc = QLabel("")
        self.stat_misc.setStyleSheet(f"color: {self.theme['muted']}; font-size: 12px;")
        lay.addWidget(self.stat_misc)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setObjectName("GhostBtn")
        refresh_btn.clicked.connect(self._refresh_stats_view)
        lay.addWidget(refresh_btn)
        lay.addStretch(1)

        outer.addWidget(card)
        return page

    def _refresh_stats_view(self):
        if not getattr(self, "stats", None):
            return
        from playstats import PlayStats
        self.stat_total_label.setText(PlayStats.fmt_total(self.stats.total_seconds))
        tops = self.stats.top_tracks(self._tag_cache, 10)
        lines = []
        for i, (t, a, cnt) in enumerate(tops, 1):
            lines.append(f"  {i}.  {t}" + (f" — {a}" if a else "") +
                         f"   ·  {cnt}×")
        self.stat_top_tracks.setText(
            "<b>Most played tracks</b><br>" + "<br>".join(lines) if lines
            else "<b>Most played tracks</b><br>Nothing yet — play something!")
        artists = self.stats.top_artists(5)
        if artists:
            self.stat_artists_label.setText(
                "<b>Top artists</b><br>" +
                "<br>".join(f"  {a}  ·  {dur}" for a, dur in artists))
        else:
            self.stat_artists_label.setText("")
        self.stat_misc.setText(
            f"Sessions opened: {self.stats.sessions}"
            + (f"   ·   Listening since {self.stats.first_played}"
               if self.stats.first_played else ""))

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
        # Space: play/pause
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, activated=self._on_play_pause)
        # Left/Right arrows: prev/next
        QShortcut(QKeySequence("Ctrl+Left"), self, activated=self._on_prev)
        QShortcut(QKeySequence("Ctrl+Right"), self, activated=self._on_next)
        # Up/Down: volume
        QShortcut(QKeySequence("Ctrl+Up"), self, activated=lambda: self._change_volume(5))
        QShortcut(QKeySequence("Ctrl+Down"), self, activated=lambda: self._change_volume(-5))
        # Ctrl+F: focus search
        QShortcut(QKeySequence("Ctrl+F"), self, activated=lambda: self.search_edit.setFocus())
        # Ctrl+M: mini player
        QShortcut(QKeySequence("Ctrl+M"), self, activated=self._toggle_mini_player)
        # Ctrl+J: jump to playing track
        QShortcut(QKeySequence("Ctrl+J"), self, activated=self._jump_to_playing)

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
                f" QTreeWidget::item {{ padding: 6px 4px; border-radius: 4px; outline: 0; }}"
                f" QTreeWidget::item:hover {{ background: {self.theme['panel_bg_2']}; outline: 0; }}"
                f" QTreeWidget::item:selected {{ background: {self.theme['active']}; color: {self.theme['gold_light']}; outline: 0; }}"
            )
            self._rebuild_folder_tree()
        # Rebuild tray menu with new theme colors
        if hasattr(self, "tray") and self.tray is not None:
            self._rebuild_tray_menu()
        if hasattr(self, "library_list"):
            self._highlight_playing_in_lists()
        # Catch every remaining widget whose inline stylesheet was baked with
        # the previous theme's colors (stats page, hints, custom panels...).
        self._sweep_inline_theme_styles()
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
            self.loading_overlay.show_loading("Loading albums")
            try:
                QApplication.processEvents()
                self._rebuild_albums_grid()
            finally:
                self.loading_overlay.hide_loading()
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
            total = len(self.library)
            if n != total:
                self.count_label.setText(f"{n} of {total} tracks")
            else:
                self.count_label.setText(f"{n} track{'s' if n != 1 else ''}")
        else:
            n = len(self._get_filtered_sorted_favorites())
            total = len(self.favorites)
            if n != total:
                self.count_label.setText(f"{n} of {total} favorites")
            else:
                self.count_label.setText(f"{n} favorite{'s' if n != 1 else ''}")

    def _on_search_changed(self, text):
        self.search_filter = text.lower().strip()
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
            tracks.sort(key=lambda x: os.path.getmtime(x) if os.path.exists(x) else 0, reverse=True)
        return tracks

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
            tracks.sort(key=lambda x: os.path.getmtime(x) if os.path.exists(x) else 0, reverse=True)
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
        self._rebuild_library_list()
        self._rebuild_favorites_list()
        self._update_count_label()
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

    def _rebuild_library_list(self):
        """Rebuild the library list FAST — just filenames, no tag reading.
        Tags are loaded in background by _start_tag_loader.
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
        self._update_count_label()
        self._rebuild_folder_tree()
        # Start background tag loading
        self._start_tag_loader(tracks)

    _tag_loader = None
    _tag_cache = {}

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

        self._tag_loader = TagLoaderThread(uncached, self)
        self._tag_loader.tag_loaded.connect(self._on_tag_loaded)
        self._tag_loader.start()

    def _on_tag_loaded(self, path: str, title: str, artist: str):
        """Called when a tag is loaded in background."""
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
        """Build a tree of added folders and their subfolders."""
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

        # User-added folders with ALL nested subfolders (recursive, no cap —
        # the scanner already walks recursively so the tree must match it).
        for folder_path in self.added_folders:
            if not os.path.isdir(folder_path):
                continue
            folder_name = os.path.basename(folder_path) or folder_path
            root_item = QTreeWidgetItem([folder_name])
            root_item.setIcon(0, folder_icon)
            root_item.setData(0, Qt.ItemDataRole.UserRole, folder_path)
            root_item.setToolTip(0, folder_path)
            self.folder_tree.addTopLevelItem(root_item)
            self._add_subtree(root_item, folder_path, folder_icon)

        # Expand all
        self.folder_tree.expandAll()

    def _add_subtree(self, parent_item, dir_path, folder_icon, depth=0):
        """Recursively attach every subdirectory of dir_path (max depth 12 as
        a cycle/loop guard for pathological filesystems)."""
        if depth > 12:
            return
        try:
            subdirs = sorted([d for d in os.listdir(dir_path)
                              if os.path.isdir(os.path.join(dir_path, d))
                              and not d.startswith(".")])
        except OSError:
            return
        for sub in subdirs:
            sub_path = os.path.join(dir_path, sub)
            sub_item = QTreeWidgetItem([sub])
            sub_item.setIcon(0, folder_icon)
            sub_item.setData(0, Qt.ItemDataRole.UserRole, sub_path)
            sub_item.setToolTip(0, sub_path)
            parent_item.addChild(sub_item)
            self._add_subtree(sub_item, sub_path, folder_icon, depth + 1)

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
            self.search_filter = ""
            self.search_edit.setText("")
            self._rebuild_library_list()
            return
        # Filter tracks to those in this folder (path-boundary safe —
        # plain startswith would match sibling folders sharing a name prefix)
        folder = data
        self.search_filter = ""
        self.search_edit.setText("")
        self.loading_overlay.show_loading("Loading folder")
        try:
            QApplication.processEvents()
            filtered = [p for p in self.library if path_within(p, folder)]
            self.library_list.clear()
            for i, path in enumerate(filtered):
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
                self.library_list.addItem(item)
        finally:
            self.loading_overlay.hide_loading()
        self._highlight_playing_in_lists()
        self.count_label.setText(f"{len(filtered)} track{'s' if len(filtered) != 1 else ''} in folder")

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
        """⋯ overflow menu on the player bar: A-B Repeat, Playback Speed,
        Show in Folder and Track Options."""
        menu = QMenu(self)
        dpr = get_dpr()
        t = self.theme
        act_ab = QAction("A-B Repeat", self)
        act_ab.setIcon(QIcon(render_icon(Icon.REPEAT_ONE, 16, t["gold"], dpr)))
        act_ab.triggered.connect(self._on_ab_button)
        menu.addAction(act_ab)
        act_rate = QAction("Playback Speed...", self)
        act_rate.setIcon(QIcon(render_icon(Icon.SPEED, 16, t["gold"], dpr)))
        act_rate.triggered.connect(self._cycle_playback_rate)
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
                self.player_bar.title_label.setText("No track selected")
                self.player_bar.artist_label.setText("—")
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
                    self.now_title.setText(title)
                    self.now_artist.setText(artist)
                    self.player_bar.title_label.setText(title)
                    self.player_bar.artist_label.setText(artist)
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
            self.player_bar.title_label.setText("No track selected")
            self.player_bar.artist_label.setText("—")
            self.now_title.setText("No track selected")
            self.now_artist.setText("Select a track to play")
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
        self.now_title.setText(title)
        self.now_artist.setText(artist)
        self.player_bar.title_label.setText(title)
        self.player_bar.artist_label.setText(artist)
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
        # Credit the finished listen to play statistics
        if getattr(self, "stats", None) and self.current_track:
            title, artist = self._tag_cache.get(self.current_track, ("", ""))
            dur = self.audio.duration() / 1000.0
            self.stats.credit_track(self.current_track, dur, artist or "")
            self._flush_stats()
        self._on_next()

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
                self.current_track,
                extract_title_artist(self.current_track))
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
                self._cover_loader.wait(200)
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
            title, artist = self._tag_cache.get(
                self.current_track,
                extract_title_artist(self.current_track))
            self.tray_menu_widget.update_now_playing(title, artist, pm)
        if self.mini_player and self.mini_player.isVisible() and self.current_track:
            title, artist = extract_title_artist(self.current_track)
            self.mini_player.update_track(title, artist, pm)

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

    def _cycle_playback_rate(self):
        """Open a small popup with a speed slider (0.5x – 2.0x)."""
        if not getattr(self, "fx", None):
            return
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QSlider, QHBoxLayout
        btn_pos = self.player_bar.more_btn.mapToGlobal(
            QPoint(0, self.player_bar.more_btn.height()))
        dlg = QDialog(self)
        dlg.setWindowTitle("Playback Speed")
        dlg.setModal(True)
        dlg.setFixedWidth(280)
        lay = QVBoxLayout(dlg)
        row = QHBoxLayout()
        lbl = QLabel(f"{self.fx.current_rate():.2f}x")
        lbl.setObjectName("NowTitle")
        row.addStretch(1); row.addWidget(lbl); row.addStretch(1)
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
        presets = QHBoxLayout()
        for p in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0):
            b = QPushButton(f"{p:g}x")
            b.setObjectName("GhostBtn")
            b.clicked.connect(lambda _, pv=p: (slider.setValue(int(pv*100))))
            presets.addWidget(b)
        lay.addLayout(presets)
        btn_pos = self.player_bar.more_btn.mapToGlobal(
            QPoint(0, self.player_bar.more_btn.height()))
        dlg.move(btn_pos.x() - 100, btn_pos.y() + 6)
        dlg.exec()
        self.player_bar.rate_btn.setToolTip(f"Playback speed: {self.fx.current_rate():g}x")

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
            # history navigation never dies after library edits
            self.current_track = path
            title, artist = extract_title_artist(path)
            if path in self._tag_cache:
                title, artist = self._tag_cache[path]
            self.now_title.setText(title)
            self.now_artist.setText(artist)
            self.player_bar.title_label.setText(title)
            self.player_bar.artist_label.setText(artist)
            self._load_cover_async(path)
            self._push_history(path)
            self.audio.set_volume(self.player_bar.vol_slider.value())
            self.audio.load_and_play(path)
            if getattr(self, "tray_menu_widget", None) is not None:
                self.tray_menu_widget.update_now_playing(
                    title, artist, self.player_bar._cover_thumb_pm)
            return
        self.current_index = idx
        self._load_and_play_current()

    def _on_next(self):
        if not self.current_playlist:
            return
        if self.repeat_mode == RepeatMode.ONE and self.current_track:
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
        if self.tray and self.tray.isVisible():
            self.tray.showMessage(APP_NAME, f"Audio: {msg}", QSystemTrayIcon.MessageIcon.Warning, 3000)

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
        # Stats: accumulate listening time while playing
        if getattr(self, "stats", None) and self.audio.state() == AudioBackend.STATE_PLAYING:
            self.stats.add_seconds(0.5)
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

    def _tray_sync_now_playing(self):
        """Push the current track's info (title / artist / cover) into the
        tray popup header from live app state."""
        if getattr(self, "tray_menu_widget", None) is None:
            return
        if self.current_track:
            title, artist = self._tag_cache.get(
                self.current_track,
                extract_title_artist(self.current_track))
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
            # No folder scan pending — refresh lists directly
            self.library.sort(key=lambda x: os.path.basename(x).lower())
            self._rebuild_library_list()
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
        self._save_config()
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
            "geometry": self.saveGeometry().data().hex() if self.isVisible() else None,
            "auto_rescan": self.auto_rescan,
            "remember_track": self.remember_track,
            "taskbar_close_to_tray": self.taskbar_close_to_tray,
            "default_folder": self.default_folder,
            "sleep_timer_active": self.sleep_timer_active,
            "sleep_timer_minutes": self.sleep_timer_minutes,
            "sort_mode": self.sort_mode.value,
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
            self.now_title.setText(title)
            self.now_artist.setText(artist)
            self.player_bar.title_label.setText(title)
            self.player_bar.artist_label.setText(artist)
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
        vol = cfg.get("volume", 80)
        self._last_volume = vol
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

        # Filter missing files (fast — os.path.exists)
        self.library = [p for p in self.library if os.path.exists(p)]
        self.favorites = {p for p in self.favorites if os.path.exists(p)}

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
            self.now_title.setText(title)
            self.now_artist.setText(artist)
            self.player_bar.title_label.setText(title)
            self.player_bar.artist_label.setText(artist)
            self.player_bar.update_like_button(last in self.favorites)
            self._highlight_playing_in_lists()
            self._load_cover_async(last)
            # Load media without playing (user can press play)
            self.audio.load(last)
            QTimer.singleShot(500, lambda: self._restore_position(last, last_pos))

        # Start rescan in background
        if self.auto_rescan:
            QTimer.singleShot(100, self._startup_rescan)

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
        # pywin32 missing — fall back to a lock-file heuristic
        try:
            lock = config_path().parent / "instance.lock"
            if lock.exists():
                return False
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
    """Log + swallow unhandled Python exceptions raised inside Qt slots.

    A desktop player must not die because one signal handler threw. The
    exception is logged with full traceback; truly fatal conditions (MemoryError)
    are re-raised so the OS can see them.
    """
    import traceback as _tb

    def excepthook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, (KeyboardInterrupt, SystemExit, MemoryError)):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        log.error("Unhandled exception:\n" +
                  "".join(_tb.format_exception(exc_type, exc_value, exc_tb)))

    sys.excepthook = excepthook

    # Qt 6.5+: exceptions in slots propagate through the event loop; notify()
    # is the official interception point.
    from PyQt6.QtCore import qInstallMessageHandler  # noqa: F401 (already installed)
    try:
        app.installEventFilter(None)  # no-op placeholder keeps imports tidy
    except Exception:
        pass


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
