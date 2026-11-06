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

from gm.core import fmt_time, log
from gm.ui.widgets import IconButton


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

