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

from gm.ui.widgets import IconButton


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

