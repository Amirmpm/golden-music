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

from widgets import ClickableSlider
from gm.core import RepeatMode, log
from gm.ui.widgets import IconButton, MarqueeLabel


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

