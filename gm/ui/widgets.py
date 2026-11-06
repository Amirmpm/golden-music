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

from gm.core import _rgba, log
from widgets import ClickableSlider


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

