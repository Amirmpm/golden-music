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
import time
from pathlib import Path
from enum import Enum

from applog import setup_logging

from PyQt6.QtCore import (
    Qt, QSize, QTimer, QEvent, pyqtSignal, QUrl, QObject, QThread,
    QPropertyAnimation, QPoint, QEasingCurve
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
    QShortcut, QKeySequence
)

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
    def __init__(self, svg_string: str, icon_size: int = 22, button_size: int = 36, parent=None):
        super().__init__(parent)
        self.svg_string = svg_string
        self.icon_size = icon_size
        self.button_size = button_size
        self.setFixedSize(button_size, button_size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._theme = None
        self._hover = False
        self.setStyleSheet("padding: 0; margin: 0; border: none; background: transparent;")

    def set_theme(self, theme: dict):
        self._theme = theme
        self.update_icon()

    def enterEvent(self, e):
        self._hover = True
        self.update_icon()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update_icon()
        super().leaveEvent(e)

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
        pm = render_icon(self.svg_string, self.icon_size, color, dpr)
        self.setIcon(QIcon(pm))
        self.setIconSize(QSize(self.icon_size, self.icon_size))

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
        self.setMinimumSize(220, 220)
        self.setMaximumSize(280, 280)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background: transparent; border: none;")

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
                grad = QLinearGradient(0,0, w, h)
                grad.setColorAt(0.0, QColor(0x8b, 0x6a, 0x2a))
                grad.setColorAt(0.5, QColor(0xd4, 0xa8, 0x5a))
                grad.setColorAt(1.0, QColor(0xf0, 0xd8, 0x96))
                p.setBrush(QBrush(grad))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(0, 0, w, h, radius, radius)
                dpr = get_dpr()
                note_pm = render_icon(Icon.MUSIC_NOTE, max(60, h // 3), "rgba(26,20,16,90)", dpr)
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

        # Right: shuffle + repeat + vol
        self.shuffle_btn = IconButton(Icon.SHUFFLE, icon_size=18, button_size=32)
        self.shuffle_btn.setCheckable(True)
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

        right_layout = QHBoxLayout()
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)
        right_layout.addWidget(self.shuffle_btn)
        right_layout.addWidget(self.repeat_btn)
        right_layout.addWidget(self.vol_btn)
        right_layout.addWidget(self.vol_slider)
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
                    self.shuffle_btn, self.repeat_btn, self.vol_btn]:
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

        # Logo — use the custom logo image
        self.logo_btn = QToolButton()
        self.logo_btn.setEnabled(False)
        self.logo_btn.setFixedSize(44, 44)
        self.logo_btn.setStyleSheet("background: transparent; border: none; padding: 0; margin: 0;")
        logo_path = Path(__file__).parent / "assets" / "logo.png"
        if logo_path.exists():
            logo_pm = QPixmap(str(logo_path))
            if not logo_pm.isNull():
                # Scale to fit the button
                scaled = logo_pm.scaled(40, 40, Qt.AspectRatioMode.KeepAspectRatio,
                                        Qt.TransformationMode.SmoothTransformation)
                self.logo_btn.setIcon(QIcon(scaled))
                self.logo_btn.setIconSize(QSize(40, 40))
            else:
                dpr = get_dpr()
                self.logo_btn.setIcon(QIcon(render_icon(Icon.MUSIC_NOTE, 28, theme["gold"], dpr)))
                self.logo_btn.setIconSize(QSize(28, 28))
        else:
            dpr = get_dpr()
            self.logo_btn.setIcon(QIcon(render_icon(Icon.MUSIC_NOTE, 28, theme["gold"], dpr)))
            self.logo_btn.setIconSize(QSize(28, 28))
        layout.addWidget(self.logo_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        # Use IconButton (simple, no text, always collapsed)
        self.btn_library = self._make_icon_btn(Icon.LIBRARY, "Library", "library", checked=True)
        self.btn_favorites = self._make_icon_btn(Icon.FAVORITES_NAV, "Favorites", "favorites")
        self.btn_refresh = self._make_icon_btn(Icon.REFRESH, "Refresh Library", "refresh", checkable=False)
        self.btn_add = self._make_icon_btn(Icon.FOLDER_ADD, "Add Folder", "addfolder", checkable=False)
        self.btn_mini = self._make_icon_btn(Icon.MINIMIZE, "Mini Player", "mini", checkable=False)

        for btn in [self.btn_library, self.btn_favorites, self.btn_refresh,
                    self.btn_add, self.btn_mini]:
            layout.addWidget(btn)

        layout.addStretch(1)

        self.btn_theme = self._make_icon_btn(Icon.MOON, "Choose Theme", "theme", checkable=False)
        self.btn_settings = self._make_icon_btn(Icon.SETTINGS, "Settings", "settings", checkable=False)
        layout.addWidget(self.btn_theme)
        layout.addWidget(self.btn_settings)

        self._all_buttons = [self.btn_library, self.btn_favorites, self.btn_refresh,
                             self.btn_add, self.btn_mini, self.btn_theme, self.btn_settings]

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
        self.btn_theme.set_svg(Icon.SUN if theme["name"] == "dark" else Icon.MOON)
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
        # Publish window title for second-launch focus (single-instance)
        self._publish_title_shm()

        # State
        self.theme_name = "aurora"
        self.theme = Theme.get(self.theme_name)
        self.library = []
        self.favorites = set()
        self.added_folders = []
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
        self.shuffle = False
        self.repeat_mode = RepeatMode.OFF
        self.user_is_seeking = False
        self.sort_mode = SortMode.TITLE
        self.search_filter = ""

        # Settings
        self.hover_expand = True
        self.auto_rescan = True
        self.remember_track = True
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

        # Threads
        self.scanner = None
        self._scan_results_pending = None
        self._cover_loader = None

        # Mini player
        self.mini_player = None

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

        # Keyboard shortcuts
        self._setup_shortcuts()

        # Load config AFTER window is shown (async, non-blocking)
        QTimer.singleShot(50, self._load_config_async)

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
        self.rail.hover_expand = self.hover_expand
        self.rail.rail_clicked.connect(self._on_rail_clicked)
        top.addWidget(self.rail)

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
        top_row.addSpacing(12)

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
        self.main_layout.addWidget(self.stack, 1)

        top.addWidget(self.main_area, 1)
        outer.addLayout(top, 1)

        # Player bar
        self.player_bar = PlayerBar(self.theme)
        outer.addWidget(self.player_bar)

        # Signals
        self.player_bar.like_btn.clicked.connect(self._on_like_toggled)
        self.player_bar.prev_btn.clicked.connect(self._on_prev)
        self.player_bar.play_btn.clicked.connect(self._on_play_pause)
        self.player_bar.next_btn.clicked.connect(self._on_next)
        self.player_bar.shuffle_btn.clicked.connect(self._on_shuffle_toggled)
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
        self.folder_tree.setStyleSheet(f"QTreeWidget {{ background: {self.theme['panel_bg']}; border: 1px solid {self.theme['border']}; border-radius: 10px; padding: 6px; }} QTreeWidget::item {{ padding: 6px 4px; border-radius: 4px; }} QTreeWidget::item:hover {{ background: {self.theme['panel_bg_2']}; }} QTreeWidget::item:selected {{ background: {self.theme['active']}; color: {self.theme['gold_light']}; }}")
        self.folder_tree.itemClicked.connect(self._on_folder_tree_click)
        left_split.addWidget(self.folder_tree)

        # Track list
        self.library_list = QListWidget()
        self.library_list.setObjectName("TrackList")
        self.library_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.library_list.setUniformItemSizes(True)
        self.library_list.setMinimumHeight(200)
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
        layout.addWidget(self.favorites_list, 2)
        return page

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
        self.tray_menu_widget.library_clicked.connect(lambda: self._on_rail_clicked("library"))
        self.tray_menu_widget.quit_clicked.connect(self._quit)
        # Also keep a standard menu as fallback (right-click on some systems)
        menu = QMenu()
        dpr = get_dpr()
        t = self.theme
        act_prev = QAction("Previous", self)
        act_prev.setIcon(QIcon(render_icon(Icon.PREV, 16, t["gold"], dpr)))
        act_prev.triggered.connect(self._on_prev)
        menu.addAction(act_prev)
        act_play = QAction("Play / Pause", self)
        act_play.setIcon(QIcon(render_icon(Icon.PLAY, 16, t["gold"], dpr)))
        act_play.triggered.connect(self._on_play_pause)
        menu.addAction(act_play)
        act_next = QAction("Next", self)
        act_next.setIcon(QIcon(render_icon(Icon.NEXT, 16, t["gold"], dpr)))
        act_next.triggered.connect(self._on_next)
        menu.addAction(act_next)
        menu.addSeparator()
        act_show = QAction("Show / Hide", self)
        act_show.triggered.connect(self._toggle_visible)
        menu.addAction(act_show)
        menu.addSeparator()
        act_quit = QAction("Quit", self)
        act_quit.triggered.connect(self._quit)
        menu.addAction(act_quit)
        self.tray.setContextMenu(menu)
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

    def _change_volume(self, delta):
        v = self.player_bar.vol_slider.value() + delta
        v = max(0, min(100, v))
        self.player_bar.vol_slider.setValue(v)

    def _apply_theme(self):
        self.theme = Theme.get(self.theme_name)
        qss = build_qss(self.theme)
        self.setStyleSheet(qss)
        if hasattr(self, "rail"):
            self.rail.set_theme(self.theme)
        if hasattr(self, "player_bar"):
            self.player_bar.set_theme(self.theme)
        if hasattr(self, "scan_status_label"):
            self.scan_status_label.setStyleSheet(f"color: {self.theme['gold']};")
        if hasattr(self, "folder_tree"):
            self.folder_tree.setStyleSheet(
                f"QTreeWidget {{ background: {self.theme['panel_bg']}; border: 1px solid {self.theme['border']}; border-radius: 10px; padding: 6px; }}"
                f" QTreeWidget::item {{ padding: 6px 4px; border-radius: 4px; }}"
                f" QTreeWidget::item:hover {{ background: {self.theme['panel_bg_2']}; }}"
                f" QTreeWidget::item:selected {{ background: {self.theme['active']}; color: {self.theme['gold_light']}; }}"
            )
            self._rebuild_folder_tree()
        # Rebuild tray menu with new theme colors
        if hasattr(self, "tray") and self.tray is not None:
            self._rebuild_tray_menu()
        if hasattr(self, "library_list"):
            self._highlight_playing_in_lists()

    def _rebuild_tray_menu(self):
        if self.tray is None:
            return
        menu = QMenu()
        dpr = get_dpr()
        t = self.theme
        act_prev = QAction("Previous", self)
        act_prev.setIcon(QIcon(render_icon(Icon.PREV, 16, t["gold"], dpr)))
        act_prev.triggered.connect(self._on_prev)
        menu.addAction(act_prev)
        act_play = QAction("Play / Pause", self)
        act_play.setIcon(QIcon(render_icon(Icon.PLAY, 16, t["gold"], dpr)))
        act_play.triggered.connect(self._on_play_pause)
        menu.addAction(act_play)
        act_next = QAction("Next", self)
        act_next.setIcon(QIcon(render_icon(Icon.NEXT, 16, t["gold"], dpr)))
        act_next.triggered.connect(self._on_next)
        menu.addAction(act_next)
        menu.addSeparator()
        # Add to Favorites instead of Show/Hide
        if self.current_track:
            if self.current_track in self.favorites:
                act_fav = QAction("Remove from Favorites", self)
                act_fav.setIcon(QIcon(render_icon(Icon.HEART_FILLED, 16, t["gold"], dpr)))
            else:
                act_fav = QAction("Add to Favorites", self)
                act_fav.setIcon(QIcon(render_icon(Icon.HEART, 16, t["gold"], dpr)))
            act_fav.triggered.connect(self._on_like_toggled)
            menu.addAction(act_fav)
            menu.addSeparator()
        act_quit = QAction("Quit", self)
        act_quit.triggered.connect(self._quit)
        menu.addAction(act_quit)
        self.tray.setContextMenu(menu)

    def _on_rail_clicked(self, key: str):
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
        elif key == "mini":
            self._toggle_mini_player()

    def _show_theme_picker(self):
        """Show the theme picker popup near the theme button."""
        self.theme_popup = ThemePickerPopup(self.theme_name, self)
        self.theme_popup.theme_selected.connect(self._on_theme_selected)
        # Position near the theme button
        btn = self.rail.btn_theme
        btn_pos = btn.mapToGlobal(QPoint(0, 0))
        x = btn_pos.x() + btn.width() + 8
        y = btn_pos.y() - 8
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

    def _on_add_folder(self):
        start_dir = self.default_folder if self.default_folder else ""
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
            # Use filename only (fast, no I/O)
            fname = os.path.splitext(os.path.basename(path))[0]
            item = QListWidgetItem(f"{i+1:>3}.  {fname}")
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
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
        for i in range(self.library_list.count()):
            item = self.library_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == path:
                idx = i + 1
                item.setText(f"{idx:>3}.  {title}  —  {artist}")
                item.setToolTip(f"{title}\n{artist}\n{path}")
                break
        # Also update favorites list
        for i in range(self.favorites_list.count()):
            item = self.favorites_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == path:
                idx = i + 1
                item.setText(f"{idx:>3}.  {title}  —  {artist}")
                item.setToolTip(f"{title}\n{artist}\n{path}")
                break

    def _update_list_items_with_tags(self):
        """Update all list items with cached tags (fast, no I/O)."""
        for i in range(self.library_list.count()):
            item = self.library_list.item(i)
            path = item.data(Qt.ItemDataRole.UserRole)
            if path in self._tag_cache:
                title, artist = self._tag_cache[path]
                item.setText(f"{i+1:>3}.  {title}  —  {artist}")
                item.setToolTip(f"{title}\n{artist}\n{path}")

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

        # User-added folders with subfolders
        for folder_path in self.added_folders:
            if not os.path.isdir(folder_path):
                continue
            folder_name = os.path.basename(folder_path) or folder_path
            root_item = QTreeWidgetItem([folder_name])
            root_item.setIcon(0, folder_icon)
            root_item.setData(0, Qt.ItemDataRole.UserRole, folder_path)
            root_item.setToolTip(0, folder_path)
            self.folder_tree.addTopLevelItem(root_item)

            # Add immediate subfolders
            try:
                subdirs = sorted([d for d in os.listdir(folder_path)
                                  if os.path.isdir(os.path.join(folder_path, d))])
                for sub in subdirs[:20]:  # limit to 20 subfolders
                    sub_path = os.path.join(folder_path, sub)
                    sub_item = QTreeWidgetItem([sub])
                    sub_item.setIcon(0, folder_icon)
                    sub_item.setData(0, Qt.ItemDataRole.UserRole, sub_path)
                    sub_item.setToolTip(0, sub_path)
                    root_item.addChild(sub_item)
            except Exception:
                pass

        # Expand all
        self.folder_tree.expandAll()

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
        filtered = [p for p in self.library if path_within(p, folder)]
        self.library_list.clear()
        for i, path in enumerate(filtered):
            if path in self._tag_cache:
                title, artist = self._tag_cache[path]
                item = QListWidgetItem(f"{i+1:>3}.  {title}  —  {artist}")
                item.setToolTip(f"{title}\n{artist}\n{path}")
            else:
                fname = os.path.splitext(os.path.basename(path))[0]
                item = QListWidgetItem(f"{i+1:>3}.  {fname}")
                item.setToolTip(path)
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.library_list.addItem(item)
        self._highlight_playing_in_lists()
        self.count_label.setText(f"{len(filtered)} track{'s' if len(filtered) != 1 else ''} in folder")

    def _rebuild_favorites_list(self):
        """Rebuild favorites list FAST — just filenames, no tag reading."""
        self.favorites_list.clear()
        tracks = self._get_filtered_sorted_favorites()
        for i, path in enumerate(tracks):
            if path in self._tag_cache:
                title, artist = self._tag_cache[path]
                item = QListWidgetItem(f"{i+1:>3}.  {title}  —  {artist}")
                item.setToolTip(f"{title}\n{artist}\n{path}")
            else:
                fname = os.path.splitext(os.path.basename(path))[0]
                item = QListWidgetItem(f"{i+1:>3}.  {fname}")
                item.setToolTip(path)
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.favorites_list.addItem(item)
        self._highlight_playing_in_lists()
        self._update_count_label()

    def _highlight_playing_in_lists(self):
        if self.current_track is None:
            return
        for lst in (self.library_list, self.favorites_list):
            for i in range(lst.count()):
                item = lst.item(i)
                if item.data(Qt.ItemDataRole.UserRole) == self.current_track:
                    f = item.font()
                    f.setBold(True)
                    item.setFont(f)
                    item.setBackground(QColor(self.theme["active"]))
                    item.setForeground(QColor(self.theme["gold_light"]))
                else:
                    f = item.font()
                    f.setBold(False)
                    item.setFont(f)
                    item.setBackground(QColor("transparent"))
                    item.setForeground(QColor(self.theme["text"]))

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
        # Use cached tags for display if available
        if path in self._tag_cache:
            title, artist = self._tag_cache[path]
            menu.setWindowTitle(f"{title} — {artist}")
        menu.exec(lst.mapToGlobal(pos))

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
        # Update mini player
        if self.mini_player and self.mini_player.isVisible():
            pm = self.player_bar._cover_thumb_pm
            self.mini_player.update_track(title, artist, pm)

    def _on_track_finished(self):
        """Called when a track finishes naturally."""
        self._on_next()

    def _on_media_loaded(self):
        """Deprecated — kept for compatibility."""
        pass

    def _load_cover_async(self, path):
        if self._cover_loader is not None:
            if self._cover_loader.isRunning():
                self._cover_loader.quit()
                self._cover_loader.wait(500)
            self._cover_loader.deleteLater()
        self._cover_loader = CoverLoaderThread(path)
        self._cover_loader.cover_ready.connect(self._on_cover_ready)
        self._cover_loader.start()

    def _on_cover_ready(self, path, data):
        if path != self.current_track:
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
        if self.mini_player and self.mini_player.isVisible() and self.current_track:
            title, artist = extract_title_artist(self.current_track)
            self.mini_player.update_track(title, artist, pm)

    def _on_play_pause(self):
        if self.current_track is None:
            if self.current_playlist:
                self._load_and_play_current()
            else:
                self._on_play_all()
            return
        state = self.audio.state()
        if state == AudioBackend.STATE_PLAYING:
            self.audio.pause()
        elif state == AudioBackend.STATE_PAUSED:
            self.audio.play()
        else:
            # Stopped — reload and play
            self.audio.set_volume(self.player_bar.vol_slider.value())
            self.audio.load_and_play(self.current_track)

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
            # Fresh random jump — only when there is nothing to redo
            if len(self.current_playlist) > 1:
                idx = self.current_index
                while idx == self.current_index:
                    idx = random.randrange(len(self.current_playlist))
                self.current_index = idx
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
        self.user_is_seeking = False
        val = self.player_bar.seek_slider.value()
        dur = self.audio.duration()
        if dur > 0:
            new_pos = int(dur * val / 1000.0)
            self.audio.set_position(new_pos)
            self.player_bar.time_current.setText(fmt_time(new_pos))

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
            # Right-click: context menu (set by setContextMenu)
            pass  # Qt handles this automatically

    def _show_tray_popup(self):
        """Show the custom two-row tray popup near the cursor."""
        from PyQt6.QtGui import QCursor
        pos = QCursor.pos()
        # Update play state and theme
        self.tray_menu_widget._apply_theme(self.theme)
        playing = self.audio.state() == AudioBackend.STATE_PLAYING
        self.tray_menu_widget.update_play_state(playing)
        # Position the popup above the cursor (so it doesn't go off-screen)
        menu_size = self.tray_menu_widget.size()
        x = pos.x() - menu_size.width() // 2
        y = pos.y() - menu_size.height() - 8
        if y < 0:
            y = pos.y() + 8
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
            "volume": self.player_bar.vol_slider.value() if hasattr(self, "player_bar") else 80,
            "last_track": self.current_track if self.remember_track else None,
            "last_position": self.audio.position() if self.remember_track and self.audio.available else 0,
            "shuffle": self.shuffle,
            "repeat_mode": self.repeat_mode.value,
            "geometry": self.saveGeometry().data().hex() if self.isVisible() else None,
            "hover_expand": self.hover_expand,
            "auto_rescan": self.auto_rescan,
            "remember_track": self.remember_track,
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

        self.theme_name = cfg.get("theme", "dark")
        self.library = cfg.get("library", [])
        self.favorites = set(cfg.get("favorites", []))
        self.added_folders = cfg.get("added_folders", [])
        vol = cfg.get("volume", 80)
        self._last_volume = vol
        if hasattr(self, "player_bar"):
            self.player_bar.vol_slider.setValue(vol)
        self.shuffle = cfg.get("shuffle", False)
        self.player_bar.update_shuffle_button(self.shuffle)
        rm = cfg.get("repeat_mode", 0)
        self.repeat_mode = RepeatMode(rm)
        self.player_bar.update_repeat_button(self.repeat_mode)
        self.hover_expand = cfg.get("hover_expand", True)
        self.auto_rescan = cfg.get("auto_rescan", True)
        self.remember_track = cfg.get("remember_track", True)
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
        if hasattr(self, "rail"):
            self.rail.hover_expand = self.hover_expand
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

        # Default to aurora theme for new installs
        self.theme_name = cfg.get("theme", "aurora")
        if self.theme_name not in Theme.names():
            self.theme_name = "aurora"
        self.theme = Theme.get(self.theme_name)
        self.library = cfg.get("library", [])
        self.favorites = set(cfg.get("favorites", []))
        self.added_folders = cfg.get("added_folders", [])
        vol = cfg.get("volume", 80)
        self._last_volume = vol
        self.player_bar.vol_slider.setValue(vol)
        self.shuffle = cfg.get("shuffle", False)
        self.player_bar.update_shuffle_button(self.shuffle)
        rm = cfg.get("repeat_mode", 0)
        self.repeat_mode = RepeatMode(rm)
        self.player_bar.update_repeat_button(self.repeat_mode)
        self.hover_expand = cfg.get("hover_expand", True)
        self.auto_rescan = cfg.get("auto_rescan", True)
        self.remember_track = cfg.get("remember_track", True)
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
        if hasattr(self, "rail"):
            self.rail.hover_expand = self.hover_expand

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
    if QSystemTrayIcon.isSystemTrayAvailable():
        w.tray.show()
    code = app.exec()
    sys.exit(code)


if __name__ == "__main__":
    main()
