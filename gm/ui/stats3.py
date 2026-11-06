"""Golden Music 2.3 — Stats page v3 (custom-painted, theme-aware, guarded).

A self-contained listening-analytics page built exclusively from QPainter —
no third-party chart libraries. Every widget re-renders from the active
theme dict (see :mod:`config.Theme`) so ``set_theme()`` is instant, and the
whole page degrades gracefully when the backing store is missing: each
section is fed through guarded accessors and simply hides/empties itself
instead of raising.

Public API
----------
``LineChart``, ``BarChart``, ``HBarChart``, ``Heatmap``, ``TopTracksList``,
``WrappedDialog``, ``StatsPage3`` plus the small formatters
``fmt_hours`` / ``fmt_count`` / ``fmt_listen``.

Signals
-------
* ``HBarChart.row_clicked(int)``      — index into the rows given to
  ``set_data`` (row payloads are kept in ``HBarChart.rows``). The page does
  NOT wire this to playback on purpose; the app connects it later, e.g.
  ``page.artist_chart.row_clicked.connect(...)``
  / ``page.genre_chart.row_clicked.connect(...)``.
* ``TopTracksList.play_requested(str)`` / ``favorite_toggled(str, bool)``.
* ``StatsPage3`` re-emits both TopTracksList signals unchanged.

Data contract (duck-typed ``main_window``, all getattr-guarded)
---------------------------------------------------------------
* ``mw.theme``                     — theme dict (fallback: royal_gold).
* ``mw.store`` (with ``.db``)      — ``gm.db.LibraryDB``; optional. Used for
  ``plays_over_time`` / ``minutes_by_month`` / ``daily_heatmap`` /
  ``top_tracks`` / ``top_artists`` / ``top_genres`` / ``tracks_where`` /
  ``total_seconds`` / ``meta_get``.
* ``mw.stats``                     — legacy ``playstats.PlayStats`` OR new
  ``gm.store.StatsStore``: ``total_seconds()`` (attr or method),
  ``play_counts`` (dict attr or property), ``top_tracks(tag_cache, n)``,
  ``top_artists(n)``, ``sessions``, ``first_played``, ``artist_seconds``.
* ``mw.favorites``                 — set of paths (heart button state).
* ``mw.prefs``                     — dict with optional
  ``stats_charts_enabled`` / ``stats_heatmap_enabled`` /
  ``stats_wrapped_enabled`` (all default True).
* ``mw._tag_cache``                — legacy tag map for the stats fallback.
* ``mw._show_toast(text)``         — optional toast callback.
* ``mw._load_cover_async(path, cb)`` — optional cover loader; ``cb(path,
  QPixmap|None)``. A single-argument loader is tolerated (no covers then).

Animations are imported from :mod:`gm.ui.animations` when present
(``fade_in`` / ``stagger_fade`` / ``set_reduce_animations``); a local
QPropertyAnimation fallback covers checkouts without that module.
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable, Sequence

from PyQt6.QtCore import (
    QEasingCurve, QPoint, QPointF, QRectF, QSize, Qt, QPropertyAnimation,
    QTimer, pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QIcon, QLinearGradient, QMouseEvent, QPainter,
    QPainterPath, QPen, QPixmap,
)
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QFrame, QGraphicsOpacityEffect,
    QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout,
    QWidget,
)

from config import Theme
from icons import Icon, render_icon, get_dpr
from gm.theme_qss import build_qss_v3

log = logging.getLogger("app.stats3")

__all__ = [
    "BarChart", "ChartTip", "Heatmap", "HBarChart", "LineChart",
    "StatCard", "StatsPage3", "TopTracksList", "WrappedDialog",
    "fmt_count", "fmt_hours", "fmt_listen",
    "fade_in", "stagger_fade", "set_reduce_animations",
]

# --- optional animation helpers (module may not exist in all checkouts) -----
try:  # pragma: no cover - exercised only when gm.ui.animations ships
    from gm.ui.animations import fade_in, stagger_fade, set_reduce_animations
    HAVE_GM_ANIMATIONS = True
except Exception:  # ImportError or partial module init — local fallback
    HAVE_GM_ANIMATIONS = False
    _REDUCE_LOCAL = False

    def set_reduce_animations(reduce_: bool) -> None:  # noqa: F811
        """No-op fallback: remember the flag for the local helpers."""
        global _REDUCE_LOCAL
        _REDUCE_LOCAL = bool(reduce_)

    def fade_in(widget: QWidget, ms: int = 140,  # noqa: F811
                cb: Callable[[], None] | None = None) -> None:
        """Fallback fade-in (same signature as gm.ui.animations.fade_in)."""
        if widget is None or _REDUCE_LOCAL:
            if cb:
                cb()
            return
        try:
            eff = QGraphicsOpacityEffect(widget)
            eff.setOpacity(0.0)
            widget.setGraphicsEffect(eff)
            anim = QPropertyAnimation(eff, b"opacity", widget)
            anim.setDuration(max(0, int(ms)))
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.finished.connect(eff.deleteLater)
            anim.start()
        except Exception:
            log.debug("fade_in fallback failed", exc_info=True)
            if cb:
                cb()

    def stagger_fade(widgets: Sequence[QWidget],  # noqa: F811
                     per_item_ms: int = 26, start_ms: int = 60) -> None:
        """Fallback staggered fade-in (gm.ui.animations-compatible)."""
        if _REDUCE_LOCAL:
            return
        for i, w in enumerate(widgets or []):
            if i == 0:
                fade_in(w, ms=110)
            else:
                QTimer.singleShot(int(start_ms + i * per_item_ms),
                                  lambda ww=w: fade_in(ww, ms=110))


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _qcolor(theme: dict, key: str, fallback: str = "#888888") -> QColor:
    """Theme key -> QColor with a safe fallback."""
    try:
        return QColor(str(theme.get(key) or fallback))
    except Exception:
        return QColor(fallback)


def _merged_theme(theme: dict | None) -> dict:
    """Normalize a caller-supplied theme dict onto the app default.

    Partial themes (only a few keys) would otherwise feed ``None`` into
    icon colors / stylesheets; merging keeps every consumer guaranteed a
    complete key set while still honoring explicit overrides.
    """
    if not isinstance(theme, dict):
        return Theme.get(Theme.DEFAULT_NAME)
    try:
        base = Theme.get(Theme.DEFAULT_NAME)
    except Exception:
        base = {}
    out = dict(base)
    out.update({k: v for k, v in theme.items() if v is not None})
    return out


def _num(value, default: float = 0.0) -> float:
    """Best-effort float() with NaN/inf guard."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def fmt_hours(seconds: float) -> str:
    """Compact listening-time label: ``"142 h"``, ``"3.5 h"``, ``"12 min"``."""
    s = _num(seconds)
    if s >= 10.0 * 3600.0:
        return f"{int(round(s / 3600.0)):,} h"
    if s >= 3600.0:
        return f"{s / 3600.0:.1f} h"
    if s >= 60.0:
        return f"{int(s // 60)} min"
    return f"{int(s)} s"


def fmt_count(n) -> str:
    """Thousands-separated integer label."""
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return "0"


def fmt_listen(seconds: float) -> str:
    """Short duration for heatmap hovers: ``"12 min"``, ``"1.5 h"``."""
    s = _num(seconds)
    if s >= 3600.0:
        return f"{s / 3600.0:.1f} h"
    if s >= 60.0:
        return f"{int(round(s / 60.0))} min"
    return f"{int(s)} s"


def _call_or_attr(obj, name: str, default=None):
    """Read *name* off *obj*; call it when it is callable (guards both)."""
    try:
        value = getattr(obj, name, default)
    except Exception:
        return default
    if callable(value):
        try:
            value = value()
        except Exception:
            return default
    return value


def _call_with(obj, name: str, *args, default=None):
    """Call ``obj.name(*args)`` when possible, else ``default``."""
    fn = getattr(obj, name, None)
    if not callable(fn):
        return default
    try:
        return fn(*args)
    except Exception:
        return default


def _interp_color(c1: QColor, c2: QColor, t: float) -> QColor:
    """Linear RGB interpolation between two colors (t in [0, 1])."""
    t = max(0.0, min(1.0, float(t)))
    return QColor(
        int(round(c1.red() + (c2.red() - c1.red()) * t)),
        int(round(c1.green() + (c2.green() - c1.green()) * t)),
        int(round(c1.blue() + (c2.blue() - c1.blue()) * t)),
    )


def _year_bounds(year: int) -> tuple[float, float]:
    """(start_ts, end_ts) of the calendar year, guarded against junk input."""
    try:
        y = int(year)
    except (TypeError, ValueError):
        y = date.today().year
    start = datetime(y, 1, 1)
    end = datetime(y + 1, 1, 1)
    return start.timestamp(), end.timestamp()


def _month_label(month_str: str) -> str:
    """``"2025-03"`` -> ``"Mar '25"`` (raw string back on parse failure)."""
    try:
        y, m = str(month_str).split("-")
        return f"{_MONTHS[int(m) - 1]} '{y[2:]}"
    except Exception:
        return str(month_str)


class ChartTip(QLabel):
    """Shared floating tooltip for the custom-painted charts.

    A plain child label (styled from the theme) that the charts move next
    to the hovered element — keeps tooltips on-brand instead of the native
    QToolTip look.
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()

    def set_theme(self, theme: dict) -> None:
        t = theme if isinstance(theme, dict) else {}
        self.setStyleSheet(
            "QLabel {"
            f" background: {t.get('panel_bg', '#222')};"
            f" color: {t.get('text', '#eee')};"
            f" border: 1px solid {t.get('gold_deep', '#886')};"
            " border-radius: 7px; padding: 4px 9px; font-size: 12px; }")

    def popup(self, text: str, pos: QPoint) -> None:
        """Show *text* anchored above *pos* (parent coordinates), clamped."""
        if not text:
            self.vanish()
            return
        self.setText(text)
        self.adjustSize()
        x = pos.x() - self.width() // 2
        y = pos.y() - self.height() - 12
        parent = self.parentWidget()
        if parent is not None:
            x = max(2, min(x, max(2, parent.width() - self.width() - 2)))
            y = max(2, min(y, max(2, parent.height() - self.height() - 2)))
        self.move(x, y)
        self.show()
        self.raise_()

    def vanish(self) -> None:
        self.hide()


# ---------------------------------------------------------------------------
# 1. LineChart — smooth polyline + gradient area
# ---------------------------------------------------------------------------
class LineChart(QWidget):
    """Smoothed line chart with gradient area fill and hover dots.

    ``set_data(points, theme, value_fmt)`` where *points* is a list of
    ``(label, value)`` tuples. The polyline is drawn as a cubic-smoothed
    QPainterPath; the area under it is filled with a vertical gold gradient
    (alpha 60 -> 0). Hovering snaps a dot to the nearest point and shows a
    themed tooltip; x labels are thinned to ~6 and the y axis gets 4 dashed
    grid lines. Minimum height 180 px.
    """

    MIN_HEIGHT = 180

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._points: list[tuple[str, float]] = []
        self._theme: dict = Theme.get(Theme.DEFAULT_NAME)
        self._value_fmt: Callable[[float], str] = lambda v: f"{v:.0f}"
        self._hover_idx = -1
        self._tip = ChartTip(self)
        self._tip.set_theme(self._theme)
        self.setMouseTracking(True)
        self.setMinimumHeight(self.MIN_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Preferred)

    # -- API -----------------------------------------------------------------
    def set_data(self, points: Iterable[tuple[str, float]] | None,
                 theme: dict | None = None,
                 value_fmt: Callable[[float], str] | None = None) -> None:
        """Replace the data (and optionally theme / value formatter)."""
        cleaned: list[tuple[str, float]] = []
        for item in points or []:
            try:
                label, value = item
                cleaned.append((str(label), _num(value)))
            except Exception:
                continue
        self._points = cleaned
        if isinstance(theme, dict):
            self._theme = theme
            self._tip.set_theme(theme)
        if callable(value_fmt):
            self._value_fmt = value_fmt
        self._hover_idx = -1
        self._tip.vanish()
        self.update()

    def set_theme(self, theme: dict) -> None:
        """Re-render with a new theme dict (data is kept)."""
        if isinstance(theme, dict):
            self._theme = theme
            self._tip.set_theme(theme)
        self.update()

    def points(self) -> list[tuple[str, float]]:
        """Current ``(label, value)`` data (for tests / app wiring)."""
        return list(self._points)

    def sizeHint(self) -> QSize:
        return QSize(360, max(self.MIN_HEIGHT, 220))

    # -- geometry ------------------------------------------------------------
    def _margins(self) -> tuple[int, int, int, int]:
        return 46, 14, 12, 26          # left, top, right, bottom

    def _plot_rect(self) -> QRectF:
        l, t, r, b = self._margins()
        return QRectF(l, t, max(10.0, self.width() - l - r),
                      max(10.0, self.height() - t - b))

    def _point_positions(self) -> list[QPointF]:
        pts = self._points
        if not pts:
            return []
        rect = self._plot_rect()
        vmax = max((v for _, v in pts), default=0.0)
        span = vmax if vmax > 0 else 1.0
        n = len(pts)
        step_x = rect.width() / max(1, n - 1) if n > 1 else 0.0
        out = []
        for i, (_lbl, v) in enumerate(pts):
            x = rect.left() if n == 1 else rect.left() + step_x * i
            y = rect.bottom() - (v / span) * rect.height() * 0.92
            out.append(QPointF(x, y))
        return out

    # -- interaction ----------------------------------------------------------
    def _nearest_index(self, pos: QPointF) -> int:
        positions = self._point_positions()
        if not positions:
            return -1
        best, best_d = -1, None
        for i, p in enumerate(positions):
            d = (p.x() - pos.x()) ** 2 + (p.y() - pos.y()) ** 2
            if best_d is None or d < best_d:
                best, best_d = i, d
        # only snap when the pointer is horizontally near the plot
        rect = self._plot_rect()
        if pos.x() < rect.left() - 24 or pos.x() > rect.right() + 24:
            return -1
        return best

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        idx = self._nearest_index(event.position())
        if idx != self._hover_idx:
            self._hover_idx = idx
            self.update()
        if 0 <= idx < len(self._points):
            label, value = self._points[idx]
            pos = self._point_positions()[idx]
            self._tip.popup(f"{label}  ·  {self._value_fmt(value)}",
                            QPoint(int(pos.x()), int(pos.y())))
        else:
            self._tip.vanish()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover_idx = -1
        self._tip.vanish()
        self.update()
        super().leaveEvent(event)

    # -- painting -------------------------------------------------------------
    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        try:
            self._paint(p)
        finally:
            p.end()

    def _paint(self, p: QPainter) -> None:
        t = self._theme
        rect = self._plot_rect()
        if not self._points:
            p.setPen(QPen(_qcolor(t, "muted")))
            p.drawText(rect, int(Qt.AlignmentFlag.AlignCenter),
                       "No data yet — play something first")
            return

        muted_c = _qcolor(t, "muted")
        gold_c = _qcolor(t, "gold")
        gold_l = _qcolor(t, "gold_light")

        vmax = max((v for _, v in self._points), default=0.0)
        span = vmax if vmax > 0 else 1.0

        # --- y grid: 4 dashed lines with muted labels -----------------------
        grid_pen = QPen(muted_c)
        grid_pen.setStyle(Qt.PenStyle.DashLine)
        grid_pen.setWidthF(1.0)
        for i in range(1, 5):
            frac = i / 4.0
            y = rect.bottom() - rect.height() * frac
            p.setPen(grid_pen)
            p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            p.setPen(QPen(muted_c))
            p.drawText(QRectF(4, y - 8, rect.left() - 10, 16),
                       int(Qt.AlignmentFlag.AlignVCenter
                           | Qt.AlignmentFlag.AlignRight),
                       self._value_fmt(vmax * frac))

        # --- x labels (thinned to ~6) ----------------------------------------
        n = len(self._points)
        step = max(1, math.ceil(n / 6.0))
        p.setPen(QPen(muted_c))
        for i in range(0, n, step):
            label = self._points[i][0]
            p.drawText(QRectF(rect.left() + (0 if n == 1 else
                           (rect.width() / max(1, n - 1)) * i) - 30,
                           rect.bottom() + 4, 60, 16),
                       int(Qt.AlignmentFlag.AlignHCenter
                           | Qt.AlignmentFlag.AlignTop), label)

        # --- smooth polyline + gradient area ---------------------------------
        positions = self._point_positions()
        base = rect.bottom()
        line = QPainterPath(positions[0])
        for i in range(1, len(positions)):
            prev, cur = positions[i - 1], positions[i]
            dx = (cur.x() - prev.x()) * 0.45
            line.cubicTo(QPointF(prev.x() + dx, prev.y()),
                         QPointF(cur.x() - dx, cur.y()), cur)
        area = QPainterPath(line)
        area.lineTo(positions[-1].x(), base)
        area.lineTo(positions[0].x(), base)
        area.closeSubpath()

        grad = QLinearGradient(QPointF(0.0, float(rect.top())),
                               QPointF(0.0, float(base)))
        top = QColor(gold_c)
        top.setAlpha(60)
        bottom = QColor(gold_c)
        bottom.setAlpha(0)
        grad.setColorAt(0.0, top)
        grad.setColorAt(1.0, bottom)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(grad))
        p.drawPath(area)

        pen = QPen(gold_l)
        pen.setWidthF(2.0)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(pen)
        p.drawPath(line)

        # --- hover dot --------------------------------------------------------
        if 0 <= self._hover_idx < len(positions):
            hp = positions[self._hover_idx]
            guide = QPen(muted_c)
            guide.setStyle(Qt.PenStyle.DotLine)
            p.setPen(guide)
            p.drawLine(QPointF(hp.x(), rect.top()), QPointF(hp.x(), base))
            p.setPen(QPen(_qcolor(t, "panel_bg")))
            p.setBrush(QBrush(gold_l))
            p.drawEllipse(hp, 5.5, 5.5)
            p.setBrush(QBrush(gold_c))
            p.drawEllipse(hp, 2.5, 2.5)

        # subtle frame so grid labels don't bleed into neighbors
        p.setPen(QPen(_qcolor(t, "border")))
        p.drawLine(QPointF(rect.left(), base), QPointF(rect.right(), base))


# ---------------------------------------------------------------------------
# 2. BarChart — rounded-top columns
# ---------------------------------------------------------------------------
class BarChart(QWidget):
    """Rounded-top bar chart (6 px radius) with hover highlight.

    ``set_data(rows, theme, value_fmt)`` with ``[(label, value), ...]``.
    Hovering a bar brightens it and shows the exact value in a themed
    tooltip; x labels rotate -25° when they would collide; y axis gets 4
    dashed grid lines. Minimum height 180 px.
    """

    MIN_HEIGHT = 180

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._rows: list[tuple[str, float]] = []
        self._theme: dict = Theme.get(Theme.DEFAULT_NAME)
        self._value_fmt: Callable[[float], str] = lambda v: f"{v:.0f}"
        self._hover_idx = -1
        self._tip = ChartTip(self)
        self._tip.set_theme(self._theme)
        self.setMouseTracking(True)
        self.setMinimumHeight(self.MIN_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Preferred)

    def set_data(self, rows: Iterable[tuple[str, float]] | None,
                 theme: dict | None = None,
                 value_fmt: Callable[[float], str] | None = None) -> None:
        """Replace the data (and optionally theme / value formatter)."""
        cleaned: list[tuple[str, float]] = []
        for item in rows or []:
            try:
                label, value = item
                cleaned.append((str(label), _num(value)))
            except Exception:
                continue
        self._rows = cleaned
        if isinstance(theme, dict):
            self._theme = theme
            self._tip.set_theme(theme)
        if callable(value_fmt):
            self._value_fmt = value_fmt
        self._hover_idx = -1
        self._tip.vanish()
        self.update()

    def set_theme(self, theme: dict) -> None:
        if isinstance(theme, dict):
            self._theme = theme
            self._tip.set_theme(theme)
        self.update()

    def rows(self) -> list[tuple[str, float]]:
        """Current ``(label, value)`` rows."""
        return list(self._rows)

    def sizeHint(self) -> QSize:
        return QSize(360, max(self.MIN_HEIGHT, 220))

    def _margins(self) -> tuple[int, int, int, int]:
        return 46, 16, 12, 34          # room for rotated labels at bottom

    def _plot_rect(self) -> QRectF:
        l, t, r, b = self._margins()
        return QRectF(l, t, max(10.0, self.width() - l - r),
                      max(10.0, self.height() - t - b))

    def _bar_geometry(self) -> list[QRectF]:
        rect = self._plot_rect()
        n = len(self._rows)
        if n == 0:
            return []
        slot = rect.width() / n
        bw = max(4.0, min(slot * 0.62, 56.0))
        vmax = max((v for _, v in self._rows), default=0.0)
        span = vmax if vmax > 0 else 1.0
        out = []
        for i, (_lbl, v) in enumerate(self._rows):
            cx = rect.left() + slot * (i + 0.5)
            h = (v / span) * rect.height() * 0.92
            out.append(QRectF(cx - bw / 2, rect.bottom() - h, bw, h))
        return out

    def _index_at(self, pos: QPointF) -> int:
        bars = self._bar_geometry()
        for i, bar in enumerate(bars):
            if pos.x() >= bar.left() - 3 and pos.x() <= bar.right() + 3:
                return i
        return -1

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        idx = self._index_at(event.position())
        if idx != self._hover_idx:
            self._hover_idx = idx
            self.update()
        if 0 <= idx < len(self._rows):
            label, value = self._rows[idx]
            top = self._bar_geometry()[idx].top()
            self._tip.popup(f"{label}  ·  {self._value_fmt(value)}",
                            QPoint(int(event.position().x()), int(top)))
        else:
            self._tip.vanish()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover_idx = -1
        self._tip.vanish()
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        try:
            self._paint(p)
        finally:
            p.end()

    def _paint(self, p: QPainter) -> None:
        t = self._theme
        rect = self._plot_rect()
        if not self._rows:
            p.setPen(QPen(_qcolor(t, "muted")))
            p.drawText(rect, int(Qt.AlignmentFlag.AlignCenter),
                       "No data yet — play something first")
            return

        muted_c = _qcolor(t, "muted")
        gold_c = _qcolor(t, "gold")
        gold_l = _qcolor(t, "gold_light")
        gold_d = _qcolor(t, "gold_deep")

        vmax = max((v for _, v in self._rows), default=0.0)
        span = vmax if vmax > 0 else 1.0

        # --- y grid ----------------------------------------------------------
        grid_pen = QPen(muted_c)
        grid_pen.setStyle(Qt.PenStyle.DashLine)
        for i in range(1, 5):
            frac = i / 4.0
            y = rect.bottom() - rect.height() * frac
            p.setPen(grid_pen)
            p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            p.setPen(QPen(muted_c))
            p.drawText(QRectF(4, y - 8, rect.left() - 10, 16),
                       int(Qt.AlignmentFlag.AlignVCenter
                           | Qt.AlignmentFlag.AlignRight),
                       self._value_fmt(vmax * frac))

        # --- bars -------------------------------------------------------------
        bars = self._bar_geometry()
        for i, (bar, (_label, _value)) in enumerate(zip(bars, self._rows)):
            hovered = (i == self._hover_idx)
            grad = QLinearGradient(QPointF(float(bar.left()), float(bar.top())),
                                   QPointF(float(bar.left()), float(bar.bottom())))
            grad.setColorAt(0.0, gold_l if hovered else gold_c)
            grad.setColorAt(1.0, gold_d)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(grad))
            p.drawPath(self._round_top(bar, 6.0))
            if hovered:
                cap_pen = QPen(gold_l)
                cap_pen.setWidthF(1.4)
                p.setPen(cap_pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(self._round_top(bar, 6.0))

        # --- x labels (rotate -25° when crowded) ------------------------------
        fm = p.fontMetrics()
        full_w = sum(fm.horizontalAdvance(lbl) for lbl, _v in self._rows)
        rotate = full_w > rect.width() * 0.98
        p.setPen(QPen(muted_c))
        for i, (label, _v) in enumerate(self._rows):
            cx = bars[i].center().x()
            if rotate:
                p.save()
                p.translate(QPointF(cx, rect.bottom() + 6))
                p.rotate(-25)
                p.drawText(QRectF(-50, 0, 100, 16),
                           int(Qt.AlignmentFlag.AlignHCenter
                               | Qt.AlignmentFlag.AlignTop), label)
                p.restore()
            else:
                p.drawText(QRectF(cx - 45, rect.bottom() + 4, 90, 16),
                           int(Qt.AlignmentFlag.AlignHCenter
                               | Qt.AlignmentFlag.AlignTop), label)

        p.setPen(QPen(_qcolor(t, "border")))
        p.drawLine(QPointF(rect.left(), rect.bottom()),
                   QPointF(rect.right(), rect.bottom()))

    @staticmethod
    def _round_top(rect: QRectF, radius: float) -> QPainterPath:
        """Column path with only the top corners rounded."""
        r = max(0.0, min(radius, rect.width() / 2.0, rect.height()))
        path = QPainterPath()
        path.moveTo(rect.left(), rect.bottom())
        path.lineTo(rect.left(), rect.top() + r)
        path.quadTo(QPointF(rect.left(), rect.top()),
                    QPointF(rect.left() + r, rect.top()))
        path.lineTo(rect.right() - r, rect.top())
        path.quadTo(QPointF(rect.right(), rect.top()),
                    QPointF(rect.right(), rect.top() + r))
        path.lineTo(rect.right(), rect.bottom())
        path.closeSubpath()
        return path


# ---------------------------------------------------------------------------
# 3. HBarChart — horizontal ranked bars
# ---------------------------------------------------------------------------
class HBarChart(QWidget):
    """Horizontal ranked-bar chart for top artists / genres.

    ``set_data(rows, theme, value_fmt)`` with ``[(label, value), ...]``:
    a 120 px elided label column, a bar proportional to the row maximum and
    the formatted value at the bar end. Rows highlight on hover and are
    clickable — ``row_clicked(int)`` carries the row index; the payloads
    stay available in ``rows()`` for the app to wire playback.
    """

    row_clicked = pyqtSignal(int)

    LABEL_W = 120
    ROW_H = 26
    GAP = 6
    VALUE_W = 84
    MIN_HEIGHT = 180

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._rows: list[tuple[str, float]] = []
        self._theme: dict = Theme.get(Theme.DEFAULT_NAME)
        self._value_fmt: Callable[[float], str] = lambda v: f"{v:.0f}"
        self._hover_idx = -1
        self._press_idx = -1
        self._tip = ChartTip(self)
        self._tip.set_theme(self._theme)
        self.setMouseTracking(True)
        self.setMinimumHeight(self.MIN_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Preferred)

    def set_data(self, rows: Iterable[tuple[str, float]] | None,
                 theme: dict | None = None,
                 value_fmt: Callable[[float], str] | None = None) -> None:
        """Replace rows (and optionally theme / value formatter)."""
        cleaned: list[tuple[str, float]] = []
        for item in rows or []:
            try:
                label, value = item
                cleaned.append((str(label), _num(value)))
            except Exception:
                continue
        self._rows = cleaned
        if isinstance(theme, dict):
            self._theme = theme
            self._tip.set_theme(theme)
        if callable(value_fmt):
            self._value_fmt = value_fmt
        self._hover_idx = -1
        self._press_idx = -1
        self._tip.vanish()
        self._sync_height()
        self.update()

    def set_theme(self, theme: dict) -> None:
        if isinstance(theme, dict):
            self._theme = theme
            self._tip.set_theme(theme)
        self.update()

    def rows(self) -> list[tuple[str, float]]:
        """Row payloads (kept so ``row_clicked`` consumers can resolve them)."""
        return list(self._rows)

    def sizeHint(self) -> QSize:
        h = max(self.MIN_HEIGHT, len(self._rows) * (self.ROW_H + self.GAP) + 8)
        return QSize(300, h)

    def _sync_height(self) -> None:
        need = len(self._rows) * (self.ROW_H + self.GAP) + 8
        self.setMinimumHeight(max(self.MIN_HEIGHT, need))

    def _row_at(self, pos: QPointF) -> int:
        if not self._rows:
            return -1
        idx = int(pos.y() // (self.ROW_H + self.GAP))
        return idx if 0 <= idx < len(self._rows) else -1

    def _bar_rect(self, idx: int) -> QRectF:
        x0 = self.LABEL_W + 10
        track_w = max(20.0, self.width() - x0 - self.VALUE_W - 8)
        vmax = max((v for _, v in self._rows), default=0.0) or 1.0
        value = self._rows[idx][1]
        w = max(4.0, (value / vmax) * track_w) if value > 0 else 0.0
        y = idx * (self.ROW_H + self.GAP)
        return QRectF(float(x0), float(y + 5), float(w), float(self.ROW_H - 10))

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        idx = self._row_at(event.position())
        if idx != self._hover_idx:
            self._hover_idx = idx
            self.setCursor(Qt.CursorShape.PointingHandCursor
                           if idx >= 0 else Qt.CursorShape.ArrowCursor)
            self.update()
        if 0 <= idx < len(self._rows):
            label, value = self._rows[idx]
            self._tip.popup(f"{label}  ·  {self._value_fmt(value)}",
                            QPoint(int(event.position().x()),
                                   int(idx * (self.ROW_H + self.GAP))))
        else:
            self._tip.vanish()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover_idx = -1
        self._tip.vanish()
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._press_idx = self._row_at(event.position())
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        idx = self._row_at(event.position())
        if idx >= 0 and idx == self._press_idx:
            self.row_clicked.emit(idx)
        self._press_idx = -1
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        try:
            self._paint(p)
        finally:
            p.end()

    def _paint(self, p: QPainter) -> None:
        t = self._theme
        if not self._rows:
            p.setPen(QPen(_qcolor(t, "muted")))
            p.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter),
                       "Nothing ranked yet")
            return

        text_c = _qcolor(t, "text")
        muted_c = _qcolor(t, "muted")
        gold_c = _qcolor(t, "gold")
        gold_l = _qcolor(t, "gold_light")
        gold_d = _qcolor(t, "gold_deep")
        fm = p.fontMetrics()

        for i, (label, value) in enumerate(self._rows):
            y = i * (self.ROW_H + self.GAP)
            hovered = (i == self._hover_idx)
            if hovered:
                bg = _qcolor(t, "active")
                bg.setAlpha(150)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(bg))
                p.drawRoundedRect(QRectF(0, float(y), float(self.width()),
                                         float(self.ROW_H)), 7.0, 7.0)

            # label column (elided, leading-aligned -> RTL-safe)
            p.setPen(QPen(gold_l if hovered else text_c))
            elided = fm.elidedText(label, Qt.TextElideMode.ElideRight,
                                   self.LABEL_W)
            p.drawText(QRectF(0, float(y), float(self.LABEL_W),
                              float(self.ROW_H)),
                       int(Qt.AlignmentFlag.AlignLeading
                           | Qt.AlignmentFlag.AlignVCenter), elided)

            # bar track + fill
            x0 = self.LABEL_W + 10
            track_w = max(20.0, self.width() - x0 - self.VALUE_W - 8)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(_qcolor(t, "panel_bg_2")))
            p.drawRoundedRect(QRectF(float(x0), float(y + 5), float(track_w),
                                     float(self.ROW_H - 10)), 4.0, 4.0)
            bar = self._bar_rect(i)
            if bar.width() > 0:
                grad = QLinearGradient(QPointF(bar.left(), bar.top()),
                                       QPointF(bar.right(), bar.top()))
                grad.setColorAt(0.0, gold_d)
                grad.setColorAt(1.0, gold_l if hovered else gold_c)
                p.setBrush(QBrush(grad))
                p.drawRoundedRect(bar, 4.0, 4.0)

            # value at the end
            p.setPen(QPen(muted_c if not hovered else gold_l))
            p.drawText(QRectF(float(self.width() - self.VALUE_W), float(y),
                              float(self.VALUE_W), float(self.ROW_H)),
                       int(Qt.AlignmentFlag.AlignTrailing
                           | Qt.AlignmentFlag.AlignVCenter),
                       self._value_fmt(value))


# ---------------------------------------------------------------------------
# 4. Heatmap — GitHub-style contribution calendar
# ---------------------------------------------------------------------------
class Heatmap(QWidget):
    """GitHub-style listening calendar: last 52-53 weeks x 7 rows.

    ``set_data(rows, theme)`` with ``[(date_str, seconds), ...]`` (ISO
    dates; missing days count as zero). Cells are 11 px with a 3 px gap on
    a 5-step color ramp interpolated between ``panel_bg_2`` and ``gold``.
    Month labels sit on top, Mon/Wed/Fri labels on the left, the yearly
    total is drawn as a caption and hovering a cell shows
    ``"12 min on 2025-03-04"``. Weeks shrink adaptively when the widget is
    narrower than the full 53-column grid. Empty data renders an
    empty-state message instead.
    """

    CELL = 11
    GAP = 3
    STEP = CELL + GAP
    LEFT_W = 34
    TOP_H = 18
    BOTTOM_H = 24
    RAMP_STEPS = 5

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._days: dict[str, float] = {}
        self._theme: dict = Theme.get(Theme.DEFAULT_NAME)
        self._ramp: list[QColor] = []
        self._hover_cell: tuple[int, int] = (-1, -1)
        self._tip = ChartTip(self)
        self._tip.set_theme(self._theme)
        self.setMouseTracking(True)
        self.setMinimumHeight(self.TOP_H + 7 * self.STEP + self.BOTTOM_H + 8)
        self.setMinimumWidth(320)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)

    def set_data(self, rows: Iterable[tuple[str, float]] | None,
                 theme: dict | None = None) -> None:
        """Load ``[(date_str, seconds), ...]`` and re-render."""
        days: dict[str, float] = {}
        for item in rows or []:
            try:
                day, seconds = item
                days[str(day)] = days.get(str(day), 0.0) + _num(seconds)
            except Exception:
                continue
        self._days = days
        if isinstance(theme, dict):
            self._theme = theme
            self._tip.set_theme(theme)
        self._ramp = self._build_ramp(self._theme)
        self._hover_cell = (-1, -1)
        self._tip.vanish()
        self.update()

    def set_theme(self, theme: dict) -> None:
        if isinstance(theme, dict):
            self._theme = theme
            self._ramp = self._build_ramp(theme)
            self._tip.set_theme(theme)
        self.update()

    def days(self) -> dict[str, float]:
        """Loaded per-day seconds (tests / app wiring)."""
        return dict(self._days)

    def sizeHint(self) -> QSize:
        return QSize(self.LEFT_W + 53 * self.STEP + 8,
                     self.TOP_H + 7 * self.STEP + self.BOTTOM_H + 10)

    def _build_ramp(self, theme: dict) -> list[QColor]:
        base = _qcolor(theme, "panel_bg_2", "#1a1b1f")
        top = _qcolor(theme, "gold", "#e9b957")
        return [_interp_color(base, top, i / (self.RAMP_STEPS - 1))
                for i in range(self.RAMP_STEPS)]

    def _step_for(self, seconds: float) -> int:
        """Ramp index 0..4 for a day's seconds (0 stays at the base color)."""
        if seconds <= 0:
            return 0
        vmax = max(self._days.values(), default=0.0) or 1.0
        q = seconds / vmax
        if q <= 0.25:
            return 1
        if q <= 0.50:
            return 2
        if q <= 0.75:
            return 3
        return 4

    def _grid(self) -> tuple[date, int]:
        """(start Monday, week count) for the trailing year, width-aware."""
        end = date.today()
        start = end - timedelta(days=364)
        start -= timedelta(days=start.weekday())
        weeks = ((end - start).days + 6) // 7 + 1
        avail = self.width() - self.LEFT_W - 8
        if avail > 4 * self.STEP:
            weeks = min(weeks, max(4, avail // self.STEP))
        start = end - timedelta(days=weeks * 7 - 1)
        start -= timedelta(days=start.weekday())
        return start, weeks

    def _cell_origin(self, col: int, row: int) -> QPointF:
        return QPointF(float(self.LEFT_W + col * self.STEP),
                       float(self.TOP_H + row * self.STEP))

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        col = int((pos.x() - self.LEFT_W) // self.STEP)
        row = int((pos.y() - self.TOP_H) // self.STEP)
        start, weeks = self._grid()
        cell = (col, row)
        valid = (0 <= col < weeks and 0 <= row < 7)
        if valid:
            day = start + timedelta(days=col * 7 + row)
            valid = day <= date.today()
        if cell != self._hover_cell:
            self._hover_cell = cell if valid else (-1, -1)
            self.update()
        if valid:
            day = start + timedelta(days=col * 7 + row)
            seconds = self._days.get(day.isoformat(), 0.0)
            origin = self._cell_origin(col, row)
            self._tip.popup(f"{fmt_listen(seconds)} on {day.isoformat()}",
                            QPoint(int(origin.x() + self.CELL / 2),
                                   int(origin.y())))
        else:
            self._tip.vanish()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover_cell = (-1, -1)
        self._tip.vanish()
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        try:
            self._paint(p)
        finally:
            p.end()

    def _paint(self, p: QPainter) -> None:
        t = self._theme
        if not self._days:
            p.setPen(QPen(_qcolor(t, "muted")))
            p.drawText(self.rect().adjusted(0, -10, 0, -10),
                       int(Qt.AlignmentFlag.AlignCenter),
                       "No listening history yet")
            return
        if not self._ramp:
            self._ramp = self._build_ramp(self._theme)

        muted_c = _qcolor(t, "muted")
        start, weeks = self._grid()
        today = date.today()
        font = p.font()
        small = QFont(font)
        small.setPointSizeF(max(7.5, font.pointSizeF() - 1.5))
        p.setFont(small)

        # --- cells ------------------------------------------------------------
        for w in range(weeks):
            for dow in range(7):
                day = start + timedelta(days=w * 7 + dow)
                if day > today:
                    continue
                seconds = self._days.get(day.isoformat(), 0.0)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(self._ramp[self._step_for(seconds)]))
                origin = self._cell_origin(w, dow)
                p.drawRoundedRect(QRectF(origin.x(), origin.y(),
                                         float(self.CELL), float(self.CELL)),
                                  2.5, 2.5)
                if (w, dow) == self._hover_cell:
                    ring = QPen(_qcolor(t, "gold_light"))
                    ring.setWidthF(1.6)
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.setPen(ring)
                    p.drawRoundedRect(QRectF(origin.x() - 1.0, origin.y() - 1.0,
                                             float(self.CELL) + 2.0,
                                             float(self.CELL) + 2.0), 3.5, 3.5)

        # --- month labels -----------------------------------------------------
        p.setPen(QPen(muted_c))
        last_month = -1
        for w in range(weeks):
            day = start + timedelta(days=w * 7)
            if day.day > 7 or day.month == last_month or day > today:
                continue
            last_month = day.month
            origin = self._cell_origin(w, 0)
            p.drawText(QRectF(origin.x() - 6, 0, 40, self.TOP_H - 2),
                       int(Qt.AlignmentFlag.AlignLeft
                           | Qt.AlignmentFlag.AlignVCenter),
                       _MONTHS[(day.month - 1) % 12])

        # --- weekday labels ----------------------------------------------------
        for dow, name in ((0, "Mon"), (2, "Wed"), (4, "Fri")):
            origin = self._cell_origin(0, dow)
            p.drawText(QRectF(0, origin.y() - 2, self.LEFT_W - 6,
                              float(self.CELL) + 4),
                       int(Qt.AlignmentFlag.AlignRight
                           | Qt.AlignmentFlag.AlignVCenter), name)

        # --- total caption ------------------------------------------------------
        total = sum(self._days.values())
        active = sum(1 for v in self._days.values() if v > 0)
        p.setPen(QPen(_qcolor(t, "text")))
        p.setFont(font)
        p.drawText(QRectF(0, float(self.TOP_H + 7 * self.STEP + 2),
                          float(self.width()), float(self.BOTTOM_H)),
                   int(Qt.AlignmentFlag.AlignLeading
                       | Qt.AlignmentFlag.AlignVCenter),
                   f"{fmt_hours(total)} in the last year · "
                   f"{fmt_count(active)} active days")


# ---------------------------------------------------------------------------
# 5. TopTracksList — expandable ranked list
# ---------------------------------------------------------------------------
class _TrackRow(QWidget):
    """One ranked track row (custom hover background, painted children)."""

    HEIGHT = 58

    def __init__(self, rank: int, entry: dict, theme: dict, favored: bool,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.path: str = entry.get("path", "")
        self._title: str = entry.get("title", "")
        self._artist: str = entry.get("artist", "")
        self._count: int = int(entry.get("count", 0) or 0)
        self._theme: dict = theme
        self._hovered = False
        self.setFixedHeight(self.HEIGHT)
        # plain QHBox layouts mirror automatically under RTL layout direction
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(10)

        t = theme
        hot = rank <= 3
        self.rank_lbl = QLabel(str(rank))
        self.rank_lbl.setFixedWidth(30)
        self.rank_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.rank_lbl.setStyleSheet(
            f"color: {t.get('gold') if hot else t.get('muted')};"
            f" font-weight: {700 if hot else 600}; font-size: 13px;")
        lay.addWidget(self.rank_lbl)

        self.cover_lbl = QLabel()
        self.cover_lbl.setFixedSize(40, 40)
        self.cover_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_lbl.setStyleSheet(
            f"border: 1px solid {t.get('border')}; border-radius: 8px;"
            f" background: {t.get('panel_bg_2')};")
        self.set_cover(None)
        lay.addWidget(self.cover_lbl)

        col = QVBoxLayout()
        col.setSpacing(1)
        self.title_lbl = QLabel(self._title)
        self.title_lbl.setStyleSheet(
            f"color: {t.get('text')}; font-weight: 600; font-size: 13px;")
        artist_lbl = QLabel(self._artist or "—")
        artist_lbl.setStyleSheet(
            f"color: {t.get('muted')}; font-size: 11.5px;")
        col.addWidget(self.title_lbl)
        col.addWidget(artist_lbl)
        lay.addLayout(col, 1)

        count_lbl = QLabel(f"×{self._count}")
        count_lbl.setStyleSheet(
            f"color: {t.get('gold_light')}; font-weight: 700;"
            " font-size: 13px;")
        lay.addWidget(count_lbl)

        btn_qss = ("QPushButton { background: transparent; border: none;"
                   " border-radius: 17px; }"
                   f"QPushButton:hover {{ background: {t.get('panel_bg_2')}; }}")
        self.play_btn = QPushButton()
        self.play_btn.setFixedSize(34, 34)
        self.play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_btn.setToolTip(f"Play “{self._title}”")
        self.play_btn.setIcon(QIcon(render_icon(
            Icon.PLAY, 15, t.get("gold_light"), get_dpr())))
        self.play_btn.setIconSize(QSize(15, 15))
        self.play_btn.setStyleSheet(btn_qss)
        lay.addWidget(self.play_btn)

        self.heart_btn = QPushButton()
        self.heart_btn.setFixedSize(34, 34)
        self.heart_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.heart_btn.setCheckable(True)
        self.heart_btn.setChecked(favored)
        self.heart_btn.setToolTip("Favorite")
        self.heart_btn.setIconSize(QSize(16, 16))
        self.heart_btn.setStyleSheet(btn_qss)
        self._apply_heart_icon()
        lay.addWidget(self.heart_btn)

    # -- covers ---------------------------------------------------------------
    def set_cover(self, pixmap: QPixmap | None) -> None:
        """Show *pixmap* (already sized) or the note-icon fallback tile."""
        if pixmap is not None and not pixmap.isNull():
            self.cover_lbl.setPixmap(pixmap.scaled(
                40, 40, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation))
        else:
            self.cover_lbl.setPixmap(render_icon(
                Icon.MUSIC_NOTE, 20, self._theme.get("muted"), get_dpr()))

    def _apply_heart_icon(self) -> None:
        on = self.heart_btn.isChecked()
        color = (self._theme.get("gold")
                 if on else self._theme.get("muted"))
        name = Icon.HEART_FILLED if on else Icon.HEART
        self.heart_btn.setIcon(QIcon(render_icon(name, 16, color, get_dpr())))

    # -- hover painting ---------------------------------------------------------
    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        if not self._hovered:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        try:
            bg = _qcolor(self._theme, "active")
            bg.setAlpha(170)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(bg))
            p.drawRoundedRect(QRectF(0.5, 1.0, float(self.width() - 1.0),
                                     float(self.height() - 2.0)), 9.0, 9.0)
        finally:
            p.end()


class TopTracksList(QWidget):
    """Expandable most-played list.

    ``set_rows(rows, favorites_set)`` accepts either the DB shape
    (``dict`` with ``path``/``title``/``artist``/``play_count``) or the
    legacy tuples ``(title, artist, count, path)``. Ten rows are shown at
    first; a "Show more" ghost button appends ten more per click (capped
    at 100). Each row carries an async cover (40 px, note-icon fallback),
    a play button -> ``play_requested(path)`` and a heart toggle ->
    ``favorite_toggled(path, on)``.
    """

    play_requested = pyqtSignal(str)
    favorite_toggled = pyqtSignal(str, bool)

    PAGE = 10
    MAX = 100

    def __init__(self, theme: dict | None = None,
                 cover_loader: Callable[..., None] | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self._theme: dict = _merged_theme(theme)
        self._loader = cover_loader if callable(cover_loader) else None
        self._entries: list[dict] = []
        self._favorites: set[str] = set()
        self._shown = 0
        self._rows: list[_TrackRow] = []

        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(4)

        self._empty_title = QLabel("Nothing here yet")
        self._empty_title.setObjectName("EmptyTitle")
        self._empty_hint = QLabel("Play some music to build your stats.")
        self._empty_hint.setObjectName("EmptyHint")
        self._empty_lay = QVBoxLayout()
        self._empty_lay.setContentsMargins(8, 10, 8, 10)
        self._empty_lay.addWidget(self._empty_title)
        self._empty_lay.addWidget(self._empty_hint)
        self._empty_w = QWidget()
        self._empty_w.setLayout(self._empty_lay)
        self._lay.addWidget(self._empty_w)
        self._empty_w.hide()

        self._more_btn = QPushButton("Show more")
        self._more_btn.setObjectName("GhostBtn")
        self._more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._more_btn.clicked.connect(self._show_more)
        self._lay.addWidget(self._more_btn, 0,
                            Qt.AlignmentFlag.AlignHCenter)
        self._more_btn.hide()

    # -- API -------------------------------------------------------------------
    def set_rows(self, rows: Iterable | None,
                 favorites_set: Iterable[str] | None = None) -> None:
        """Normalize and display *rows*; *favorites_set* sets heart states."""
        self._entries = self._normalize(rows)
        try:
            self._favorites = {str(p) for p in (favorites_set or [])}
        except Exception:
            self._favorites = set()
        self._shown = min(self.PAGE, len(self._entries))
        self._rebuild()

    def set_favorites(self, favorites_set: Iterable[str] | None) -> None:
        """Refresh heart states only (after an external toggle)."""
        try:
            self._favorites = {str(p) for p in (favorites_set or [])}
        except Exception:
            self._favorites = set()
        for row in self._rows:
            row.heart_btn.blockSignals(True)
            row.heart_btn.setChecked(row.path in self._favorites)
            row.heart_btn.blockSignals(False)
            row._apply_heart_icon()

    def set_theme(self, theme: dict) -> None:
        """Rebuild rows with a new theme (pagination state is kept)."""
        if not isinstance(theme, dict):
            return
        self._theme = _merged_theme(theme)
        self._rebuild()

    def row_count(self) -> int:
        """Number of rows currently displayed."""
        return len(self._rows)

    def row_at(self, index: int) -> _TrackRow | None:
        """Displayed row widget by index (tests / app wiring)."""
        if 0 <= index < len(self._rows):
            return self._rows[index]
        return None

    def entries(self) -> list[dict]:
        """All normalized entries (not just the shown page)."""
        return list(self._entries)

    # -- internals ----------------------------------------------------------------
    @staticmethod
    def _normalize(rows: Iterable | None) -> list[dict]:
        out: list[dict] = []
        for row in rows or []:
            try:
                if isinstance(row, dict):
                    path = str(row.get("path") or "")
                    title = str(row.get("title") or "")
                    artist = str(row.get("artist") or "")
                    count = int(row.get("play_count") or 0)
                else:
                    title, artist, count, path = row
                    title, artist = str(title), str(artist)
                    count, path = int(count), str(path)
            except Exception:
                continue
            if not title and path:
                title = Path(path).stem
            out.append({"path": path, "title": title,
                        "artist": artist, "count": count})
        return out

    def _rebuild(self) -> None:
        for row in self._rows:
            row.setParent(None)
            row.deleteLater()
        self._rows = []
        take = min(self._shown, min(len(self._entries), self.MAX))
        for i in range(take):
            entry = self._entries[i]
            row = _TrackRow(i + 1, entry, self._theme,
                            entry.get("path", "") in self._favorites)
            row.play_btn.clicked.connect(
                lambda _c=False, p=entry.get("path", ""):
                    self.play_requested.emit(p))
            row.heart_btn.toggled.connect(
                lambda on, p=entry.get("path", ""): self._on_heart(p, on))
            self._request_cover(row)
            self._lay.insertWidget(self._lay.count() - 2, row)
            self._rows.append(row)

        self._empty_w.setVisible(not self._rows)
        more_possible = take < min(len(self._entries), self.MAX)
        self._more_btn.setVisible(more_possible)
        self._more_btn.setText(
            f"Show more ({min(len(self._entries), self.MAX) - take} left)")

    def _request_cover(self, row: _TrackRow) -> None:
        loader = self._loader
        path = row.path
        if loader is None or not path:
            return

        def _cb(p: str, pixmap: QPixmap | None) -> None:
            if p == row.path:
                row.set_cover(pixmap)

        try:
            loader(path, _cb)
        except TypeError:
            # legacy single-argument loader: no callback support
            try:
                loader(path)
            except Exception:
                pass
        except Exception:
            log.debug("cover request failed", exc_info=True)

    def _on_heart(self, path: str, on: bool) -> None:
        if on:
            self._favorites.add(path)
        else:
            self._favorites.discard(path)
        self.favorite_toggled.emit(path, on)

    def _show_more(self) -> None:
        self._shown = min(self._shown + self.PAGE,
                          min(len(self._entries), self.MAX))
        self._rebuild()


# ---------------------------------------------------------------------------
# 6. WrappedDialog — yearly report card + text export
# ---------------------------------------------------------------------------
class WrappedDialog(QDialog):
    """Yearly "Wrapped" report dialog.

    ``WrappedDialog(year, data, theme, parent)`` builds a scrollable card
    stack: big year title, headline totals (time / plays / sessions), top
    artist, top genre, top track (with cover), top album, "new this year"
    and a fun superlative row. Every card is optional: missing/None keys
    are simply skipped. ``export_summary()`` writes a plain-text report via
    a save dialog and confirms with an inline status label.

    *data* keys (all guarded):
      ``total_seconds`` float, ``total_plays`` int, ``sessions`` int,
      ``top_artist`` (name, plays, seconds) | None, ``top_genre``
      (name, count) | None, ``top_track`` (title, artist, count, path)
      | None, ``top_album`` (album, artist, plays) | None,
      ``new_count`` int | None, ``superlative`` str | None,
      ``first_played`` str.
    """

    def __init__(self, year: int, data: dict, theme: dict,
                 parent: QWidget | None = None,
                 toast_cb: Callable[[str], None] | None = None,
                 cover_loader: Callable[..., None] | None = None):
        super().__init__(parent)
        self._year = int(year)
        self._data = dict(data or {})
        self._theme: dict = _merged_theme(theme)
        self._toast_cb = toast_cb if callable(toast_cb) else None
        self._cover_loader = cover_loader if callable(cover_loader) else None

        self.setWindowTitle(f"{self._year} Wrapped — Golden Music")
        self.setMinimumSize(520, 560)
        self.resize(560, 680)
        try:
            self.setStyleSheet(build_qss_v3(self._theme))
        except Exception:
            log.debug("wrapped QSS failed", exc_info=True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 14)
        outer.setSpacing(10)

        head = QVBoxLayout()
        head.setSpacing(2)
        year_lbl = QLabel(str(self._year))
        year_lbl.setObjectName("PageTitle")
        year_lbl.setStyleSheet(
            f"font-size: 34px; font-weight: 800; color: "
            f"{self._theme.get('gold')};")
        sub_lbl = QLabel("Your year in music")
        sub_lbl.setObjectName("PageSubtitle")
        head.addWidget(year_lbl)
        head.addWidget(sub_lbl)
        outer.addLayout(head)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body.setObjectName("StatsBody")
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(0, 4, 0, 4)
        body_lay.setSpacing(10)
        self._build_cards(body_lay)
        body_lay.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        btns = QHBoxLayout()
        btns.setSpacing(10)
        self.export_btn = QPushButton("Share / Export")
        self.export_btn.setObjectName("GoldBtn")
        self.export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.export_btn.setIcon(QIcon(render_icon(
            Icon.SHARE, 15, "#141414", get_dpr())))
        self.export_btn.clicked.connect(self.export_summary)
        btns.addWidget(self.export_btn)
        close_btn = QPushButton("Close")
        close_btn.setObjectName("GhostBtn")
        close_btn.clicked.connect(self.close)
        btns.addWidget(close_btn)
        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet(
            f"color: {self._theme.get('gold')}; font-size: 12px;")
        self.status_lbl.hide()
        outer.addLayout(btns)
        outer.addWidget(self.status_lbl)

    # -- card construction -------------------------------------------------------
    def _card(self, title: str, icon_name: str) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("WrapCard")
        frame.setStyleSheet(
            "QFrame#WrapCard {"
            f" background: {self._theme.get('panel_bg')};"
            f" border: 1px solid {self._theme.get('border')};"
            " border-radius: 12px; }")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(4)
        head = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(render_icon(icon_name, 17,
                                   self._theme.get("gold"), get_dpr()))
        caption = QLabel(title)
        caption.setStyleSheet(
            f"color: {self._theme.get('muted')}; font-size: 11.5px;"
            " font-weight: 700; letter-spacing: 1px;")
        head.addWidget(icon)
        head.addWidget(caption)
        head.addStretch(1)
        lay.addLayout(head)
        return frame, lay

    def _value_lbl(self, text: str, big: bool = True) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("StatValue" if big else "SectionTitle")
        if big:
            lbl.setStyleSheet("font-size: 24px;")
        lbl.setWordWrap(True)
        return lbl

    def _build_cards(self, lay: QVBoxLayout) -> None:
        d = self._data
        # headline
        frame, clay = self._card("THE NUMBERS", Icon.ACTIVITY)
        row = QHBoxLayout()
        row.setSpacing(16)
        for value, caption in (
                (fmt_hours(d.get("total_seconds", 0.0)), "listened"),
                (fmt_count(d.get("total_plays", 0)), "plays"),
                (fmt_count(d.get("sessions", 0)), "sessions")):
            col = QVBoxLayout()
            col.setSpacing(0)
            col.addWidget(self._value_lbl(value))
            cap = QLabel(caption)
            cap.setObjectName("StatCaption")
            col.addWidget(cap)
            row.addLayout(col, 1)
        clay.addLayout(row)
        lay.addWidget(frame)

        top_artist = d.get("top_artist")
        if top_artist:
            try:
                name, plays, seconds = top_artist
                frame, clay = self._card("TOP ARTIST", Icon.TROPHY)
                clay.addWidget(self._value_lbl(str(name)))
                sub = QLabel(f"{fmt_count(plays)} plays · "
                             f"{fmt_hours(_num(seconds))}")
                sub.setObjectName("StatCaption")
                clay.addWidget(sub)
                lay.addWidget(frame)
            except Exception:
                log.debug("wrapped top_artist malformed", exc_info=True)

        top_genre = d.get("top_genre")
        if top_genre:
            try:
                name, count = top_genre
                frame, clay = self._card("TOP GENRE", Icon.STAR)
                clay.addWidget(self._value_lbl(str(name)))
                sub = QLabel(f"{fmt_count(count)} plays")
                sub.setObjectName("StatCaption")
                clay.addWidget(sub)
                lay.addWidget(frame)
            except Exception:
                log.debug("wrapped top_genre malformed", exc_info=True)

        top_track = d.get("top_track")
        if top_track:
            try:
                title, artist, count, path = (
                    str(top_track[0]), str(top_track[1]),
                    int(top_track[2]), str(top_track[3]))
                frame, clay = self._card("TOP TRACK", Icon.FLAME)
                row = QHBoxLayout()
                row.setSpacing(12)
                self._cover_lbl = QLabel()
                self._cover_lbl.setFixedSize(56, 56)
                self._cover_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self._cover_lbl.setStyleSheet(
                    f"border: 1px solid {self._theme.get('border')};"
                    f" border-radius: 10px;"
                    f" background: {self._theme.get('panel_bg_2')};")
                self._cover_lbl.setPixmap(render_icon(
                    Icon.MUSIC_NOTE, 26, self._theme.get("muted"), get_dpr()))
                self._request_cover(path)
                row.addWidget(self._cover_lbl)
                col = QVBoxLayout()
                col.setSpacing(0)
                track_lbl = self._value_lbl(title, big=False)
                track_lbl.setStyleSheet(
                    f"font-size: 16px; color: {self._theme.get('text')};")
                col.addWidget(track_lbl)
                sub = QLabel(f"{artist or '—'} · ×{fmt_count(count)}")
                sub.setObjectName("StatCaption")
                col.addWidget(sub)
                row.addLayout(col, 1)
                clay.addLayout(row)
                lay.addWidget(frame)
            except Exception:
                log.debug("wrapped top_track malformed", exc_info=True)

        top_album = d.get("top_album")
        if top_album:
            try:
                album, artist, plays = top_album
                frame, clay = self._card("TOP ALBUM", Icon.DISC)
                clay.addWidget(self._value_lbl(str(album), big=False))
                sub = QLabel(f"{artist or '—'} · {fmt_count(plays)} plays")
                sub.setObjectName("StatCaption")
                clay.addWidget(sub)
                lay.addWidget(frame)
            except Exception:
                log.debug("wrapped top_album malformed", exc_info=True)

        new_count = d.get("new_count")
        if new_count is not None:
            try:
                frame, clay = self._card("NEW THIS YEAR", Icon.ZAP)
                clay.addWidget(self._value_lbl(fmt_count(new_count), big=False))
                sub = QLabel("tracks joined your library")
                sub.setObjectName("StatCaption")
                clay.addWidget(sub)
                lay.addWidget(frame)
            except Exception:
                log.debug("wrapped new_count malformed", exc_info=True)

        superlative = d.get("superlative")
        if superlative:
            frame, clay = self._card("YOUR SUPERLATIVE", Icon.SPARKLES)
            lbl = QLabel(str(superlative))
            lbl.setObjectName("SectionTitle")
            lbl.setWordWrap(True)
            clay.addWidget(lbl)
            lay.addWidget(frame)

        first_played = str(d.get("first_played") or "")
        if first_played:
            frame, clay = self._card("SINCE", Icon.CLOCK)
            lbl = QLabel(f"Listening since {first_played}")
            lbl.setObjectName("StatCaption")
            clay.addWidget(lbl)
            lay.addWidget(frame)

    def _request_cover(self, path: str) -> None:
        loader = self._cover_loader
        if loader is None or not path:
            return

        def _cb(_p: str, pixmap: QPixmap | None) -> None:
            if pixmap is None or pixmap.isNull():
                return
            self._cover_lbl.setPixmap(pixmap.scaled(
                56, 56, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation))

        try:
            loader(path, _cb)
        except TypeError:
            try:
                loader(path)
            except Exception:
                pass
        except Exception:
            log.debug("wrapped cover request failed", exc_info=True)

    # -- export --------------------------------------------------------------------
    def summary_text(self) -> str:
        """Plain-text report (what ``export_summary`` writes)."""
        d = self._data
        lines = [f"Golden Music — {self._year} Wrapped", ""]
        lines.append(f"Listening time: {fmt_hours(d.get('total_seconds', 0.0))}")
        lines.append(f"Plays: {fmt_count(d.get('total_plays', 0))}")
        if d.get("sessions"):
            lines.append(f"Sessions: {fmt_count(d.get('sessions'))}")
        if d.get("top_artist"):
            try:
                name, plays, seconds = d["top_artist"]
                lines.append("")
                lines.append(f"Top artist: {name} — {fmt_count(plays)} plays, "
                             f"{fmt_hours(_num(seconds))}")
            except Exception:
                pass
        if d.get("top_genre"):
            try:
                name, count = d["top_genre"]
                lines.append(f"Top genre: {name} — {fmt_count(count)} plays")
            except Exception:
                pass
        if d.get("top_track"):
            try:
                title, artist, count, _path = d["top_track"]
                lines.append(f"Top track: \"{title}\" by {artist or 'Unknown'}"
                             f" — ×{fmt_count(count)}")
            except Exception:
                pass
        if d.get("top_album"):
            try:
                album, artist, plays = d["top_album"]
                lines.append(f"Top album: {album} — {artist or 'Unknown'} "
                             f"({fmt_count(plays)} plays)")
            except Exception:
                pass
        if d.get("new_count") is not None:
            lines.append(f"New this year: {fmt_count(d['new_count'])} tracks")
        if d.get("superlative"):
            lines.append("")
            lines.append(str(d["superlative"]))
        if d.get("first_played"):
            lines.append(f"Listening since {d['first_played']}")
        lines += ["", "Generated by Golden Music"]
        return "\n".join(lines)

    def export_summary(self) -> None:
        """Save the plain-text report to a user-chosen file."""
        default_name = f"goldenmusic-wrapped-{self._year}.txt"
        try:
            path, _filter = QFileDialog.getSaveFileName(
                self, "Export Wrapped summary", default_name,
                "Text files (*.txt);;All files (*)")
        except Exception:
            path = ""
        if not path:
            return
        try:
            Path(path).write_text(self.summary_text(), encoding="utf-8")
        except OSError as exc:
            log.warning("wrapped export failed: %s", exc)
            self.status_lbl.setStyleSheet(
                f"color: {self._theme.get('gold_deep')}; font-size: 12px;")
            self.status_lbl.setText(f"Could not save: {exc}")
            self.status_lbl.show()
            return
        self.status_lbl.setStyleSheet(
            f"color: {self._theme.get('gold')}; font-size: 12px;")
        self.status_lbl.setText(f"Saved to {path}")
        self.status_lbl.show()
        if self._toast_cb is not None:
            try:
                self._toast_cb(f"Wrapped summary saved to {path}")
            except Exception:
                log.debug("wrapped toast failed", exc_info=True)


# ---------------------------------------------------------------------------
# 7. StatsPage3 — the page itself
# ---------------------------------------------------------------------------
class StatCard(QFrame):
    """One KPI tile: icon + StatValue + StatCaption."""

    def __init__(self, icon_name: str, caption: str, theme: dict,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("StatCard")
        self._theme: dict = _merged_theme(theme)
        self._icon_name = icon_name
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(4)
        self.icon_lbl = QLabel()
        lay.addWidget(self.icon_lbl)
        self.value_lbl = QLabel("—")
        self.value_lbl.setObjectName("StatValue")
        lay.addWidget(self.value_lbl)
        self.caption_lbl = QLabel(caption)
        self.caption_lbl.setObjectName("StatCaption")
        self.caption_lbl.setWordWrap(True)
        lay.addWidget(self.caption_lbl)
        self.apply_theme(self._theme)

    def apply_theme(self, theme: dict) -> None:
        if not isinstance(theme, dict):
            return
        self._theme = _merged_theme(theme)
        t = self._theme
        self.setStyleSheet(
            "QFrame#StatCard {"
            f" background: {t.get('panel_bg')};"
            f" border: 1px solid {t.get('border')};"
            " border-radius: 12px; }")
        self.icon_lbl.setPixmap(render_icon(
            self._icon_name, 22, t.get("gold"), get_dpr()))

    def set_value(self, text: str) -> None:
        self.value_lbl.setText(str(text))


def _skeleton_lines(n: int, theme: dict) -> QWidget:
    """Loading placeholder made of ``n`` SkeletonLine frames."""
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(4, 6, 4, 6)
    lay.setSpacing(8)
    for _ in range(n):
        line = QFrame()
        line.setObjectName("SkeletonLine")
        line.setFixedHeight(14)
        lay.addWidget(line)
    _ = theme  # SkeletonLine colors come from the shared page QSS
    return w


class StatsPage3(QWidget):
    """Stats page v3: KPI cards, charts, heatmap, top tracks, Wrapped.

    ``StatsPage3(main_window)`` — the window is duck-typed and every access
    is guarded (see the module docstring for the exact contract). Call
    ``refresh()`` after showing (and whenever stats change), and
    ``set_theme(theme_dict)`` on theme switches.

    Signals ``play_requested(path)`` and ``favorite_toggled(path, on)``
    mirror the TopTracksList and are meant for the app to wire playback /
    favorites. The HBarCharts (``artist_chart`` / ``genre_chart``) emit
    ``row_clicked(int)`` which the page deliberately leaves unwired.
    """

    play_requested = pyqtSignal(str)
    favorite_toggled = pyqtSignal(str, bool)

    CARD_KEYS = ("total", "plays", "artists", "sessions")

    def __init__(self, main_window, parent: QWidget | None = None):
        super().__init__(parent)
        self._mw = main_window
        self._theme: dict = self._read_theme()
        self._first_refresh = True
        self._skeletons: list[QWidget] = []

        self.line_chart: LineChart | None = None
        self.bar_chart: BarChart | None = None
        self.artist_chart: HBarChart | None = None
        self.genre_chart: HBarChart | None = None
        self.heatmap: Heatmap | None = None
        self.tracks_list: TopTracksList | None = None

        self.setObjectName("StatsPage")
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_header())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body.setObjectName("StatsBody")
        self._body_lay = QVBoxLayout(body)
        self._body_lay.setContentsMargins(18, 4, 18, 18)
        self._body_lay.setSpacing(14)
        self._build_stat_cards()
        self._build_charts_panel()
        self._build_heatmap_panel()
        self._build_tracks_panel()
        self._body_lay.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        self._apply_qss()

    # -- construction ---------------------------------------------------------------
    def _read_theme(self) -> dict:
        t = getattr(self._mw, "theme", None)
        if isinstance(t, dict) and "gold" in t:
            return _merged_theme(t)
        try:
            return Theme.get(Theme.DEFAULT_NAME)
        except Exception:
            return {}

    def _apply_qss(self) -> None:
        try:
            qss = build_qss_v3(self._theme)
        except Exception:
            log.debug("stats3 QSS build failed", exc_info=True)
            qss = ""
        self.setStyleSheet(qss +
                           "\nQWidget#StatsPage, QWidget#StatsBody "
                           "{ background: transparent; }")

    def _build_header(self) -> QWidget:
        header = QWidget()
        lay = QHBoxLayout(header)
        lay.setContentsMargins(18, 16, 18, 6)
        lay.setSpacing(10)
        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        self.title_lbl = QLabel("Stats")
        self.title_lbl.setObjectName("PageTitle")
        subtitle = QLabel("Your listening, visualized")
        subtitle.setObjectName("PageSubtitle")
        title_col.addWidget(self.title_lbl)
        title_col.addWidget(subtitle)
        lay.addLayout(title_col, 1)

        self.year_combo = QComboBox()
        self.year_combo.setMinimumWidth(110)
        self.year_combo.setToolTip("Year shown in the Wrapped report")
        lay.addWidget(self.year_combo)

        self.wrapped_btn = QPushButton("Wrapped")
        self.wrapped_btn.setObjectName("GoldBtn")
        self.wrapped_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.wrapped_btn.setIcon(QIcon(render_icon(
            Icon.SPARKLES, 15, "#141414", get_dpr())))
        self.wrapped_btn.clicked.connect(self._open_wrapped)
        lay.addWidget(self.wrapped_btn)

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setObjectName("GhostBtn")
        self.refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_btn.setIcon(QIcon(render_icon(
            Icon.REFRESH, 15, self._theme.get("text"), get_dpr())))
        self.refresh_btn.clicked.connect(self.refresh)
        lay.addWidget(self.refresh_btn)
        return header

    def _panel(self, title: str, caption: str = "") -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("StatsPanel")
        frame.setStyleSheet(
            "QFrame#StatsPanel {"
            f" background: {self._theme.get('panel_bg')};"
            f" border: 1px solid {self._theme.get('border')};"
            " border-radius: 12px; }")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)
        title_lbl = QLabel(title)
        title_lbl.setObjectName("SectionTitle")
        lay.addWidget(title_lbl)
        if caption:
            cap = QLabel(caption)
            cap.setObjectName("PageSubtitle")
            lay.addWidget(cap)
        return frame, lay

    def _build_stat_cards(self) -> None:
        row = QHBoxLayout()
        row.setSpacing(12)
        self.cards: dict[str, StatCard] = {}
        specs = (("total", Icon.HEADPHONES, "total listening time"),
                 ("plays", Icon.PLAY, "tracks played"),
                 ("artists", Icon.USER, "artists heard"),
                 ("sessions", Icon.CALENDAR, "sessions opened"))
        for key, icon_name, caption in specs:
            card = StatCard(icon_name, caption, self._theme)
            self.cards[key] = card
            row.addWidget(card, 1)
        self._body_lay.addLayout(row)

    def _build_charts_panel(self) -> None:
        self.charts_panel, lay = self._panel(
            "Listening trends", "How your listening spread over time")
        self._line_caption = self._chart_sub(lay, "Plays over time — last 30 days")
        self._line_host = self._chart_host(lay, 4)
        self._bar_caption = self._chart_sub(lay, "Minutes per month")
        self._bar_host = self._chart_host(lay, 4)
        genre_row = QHBoxLayout()
        genre_row.setSpacing(12)
        left = QVBoxLayout()
        self._artist_caption = self._chart_sub(left, "Top artists")
        self._artist_host = self._chart_host(left, 5)
        genre_row.addLayout(left, 1)
        right = QVBoxLayout()
        self._genre_caption = self._chart_sub(right, "Top genres")
        self._genre_host = self._chart_host(right, 5)
        genre_row.addLayout(right, 1)
        lay.addLayout(genre_row)
        self._body_lay.addWidget(self.charts_panel)

    def _chart_sub(self, lay: QVBoxLayout, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("PageSubtitle")
        lay.addWidget(lbl)
        return lbl

    def _chart_host(self, lay: QVBoxLayout, skeleton_lines: int) -> QVBoxLayout:
        host = QVBoxLayout()
        host.setContentsMargins(0, 0, 0, 6)
        host.setSpacing(6)
        skel = _skeleton_lines(skeleton_lines, self._theme)
        self._skeletons.append(skel)
        host.addWidget(skel)
        lay.addLayout(host)
        return host

    def _drop_skeletons(self) -> None:
        for skel in self._skeletons:
            try:
                skel.setParent(None)
                skel.deleteLater()
            except Exception:
                pass
        self._skeletons = []

    def _build_heatmap_panel(self) -> None:
        self.heatmap_panel, lay = self._panel(
            "Daily listening — last year",
            "Each square is a day; brighter means more time")
        self._heat_host = self._chart_host(lay, 5)
        self._body_lay.addWidget(self.heatmap_panel)

    def _build_tracks_panel(self) -> None:
        self.tracks_panel, lay = self._panel(
            "Top tracks", "Your most played, all time")
        self.tracks_list = TopTracksList(
            self._theme, self._make_cover_loader())
        self.tracks_list.play_requested.connect(self.play_requested)
        self.tracks_list.favorite_toggled.connect(self.favorite_toggled)
        lay.addWidget(self.tracks_list)
        self._body_lay.addWidget(self.tracks_panel)

    def _make_cover_loader(self) -> Callable[..., None] | None:
        loader = getattr(self._mw, "_load_cover_async", None)
        return loader if callable(loader) else None

    def _toast(self, text: str) -> None:
        cb = getattr(self._mw, "_show_toast", None)
        if callable(cb):
            try:
                cb(text)
            except Exception:
                log.debug("toast failed", exc_info=True)

    def _pref(self, key: str, default: bool = True) -> bool:
        prefs = getattr(self._mw, "prefs", None)
        if isinstance(prefs, dict):
            value = prefs.get(key, default)
            if isinstance(value, bool):
                return value
        return default

    def _sync_reduce_animations(self) -> None:
        """Forward the optional 'reduce_animations' pref to the shared
        animation helpers (guarded — the helper may be the local fallback)."""
        prefs = getattr(self._mw, "prefs", None)
        reduce_ = bool(prefs.get("reduce_animations")) \
            if isinstance(prefs, dict) else False
        try:
            set_reduce_animations(reduce_)
        except Exception:
            log.debug("set_reduce_animations failed", exc_info=True)

    def _db_safe(self):
        store = getattr(self._mw, "store", None)
        if store is None:
            return None
        return getattr(store, "db", None)

    # -- refresh -----------------------------------------------------------------
    def refresh(self) -> None:
        """Reload every section (db first, legacy stats fallback, guarded)."""
        try:
            self._theme = self._read_theme()
            self._apply_qss()
            self._sync_reduce_animations()
            for card in self.cards.values():
                card.apply_theme(self._theme)
            db = self._db_safe()
            stats = getattr(self._mw, "stats", None)
            self._update_stat_cards(db, stats)
            self._update_year_combo(db, stats)
            self._update_charts(db, stats)
            self._update_heatmap(db)
            self._update_top_tracks(db, stats)
            if self._first_refresh:
                self._first_refresh = False
                widgets = ([self.cards[k] for k in self.CARD_KEYS]
                           + [self.charts_panel, self.heatmap_panel,
                              self.tracks_panel])
                try:
                    stagger_fade(widgets)   # positional args only: works
                                            # with gm.ui.animations AND the
                                            # local fallback above
                except Exception:
                    log.debug("stagger fade failed", exc_info=True)
        except Exception:
            log.exception("stats page refresh failed")

    def set_theme(self, theme: dict) -> None:
        """Theme switch: re-style everything, keep the loaded data."""
        if not isinstance(theme, dict):
            return
        self._theme = _merged_theme(theme)
        self._apply_qss()
        for card in self.cards.values():
            card.apply_theme(theme)
        for chart in (self.line_chart, self.bar_chart,
                      self.artist_chart, self.genre_chart, self.heatmap):
            if chart is not None:
                chart.set_theme(theme)
        if self.tracks_list is not None:
            self.tracks_list.set_theme(theme)
        self.refresh_btn.setIcon(QIcon(render_icon(
            Icon.REFRESH, 15, self._theme.get("text"), get_dpr())))
        for frame in (self.charts_panel, self.heatmap_panel,
                      self.tracks_panel):
            frame.setStyleSheet(
                "QFrame#StatsPanel {"
                f" background: {self._theme.get('panel_bg')};"
                f" border: 1px solid {self._theme.get('border')};"
                " border-radius: 12px; }")

    # -- sections ------------------------------------------------------------------
    def stat_card_text(self, key: str) -> str:
        """Current text of a stat card value (tests / app wiring)."""
        card = self.cards.get(key)
        return card.value_lbl.text() if card is not None else ""

    def _update_stat_cards(self, db, stats) -> None:
        total = None
        if db is not None:
            try:
                total = float(db.total_seconds())
            except Exception:
                total = None
        if total is None:
            total = _num(_call_or_attr(stats, "total_seconds"), 0.0)
        self.cards["total"].set_value(fmt_hours(total))

        plays = None
        if db is not None:
            try:
                rows = db.tracks_where("play_count>0", (), order="")
                plays = sum(int(r.get("play_count") or 0) for r in rows)
            except Exception:
                plays = None
        if plays is None:
            pc = _call_or_attr(stats, "play_counts")
            plays = sum(int(v) for v in pc.values()
                        if isinstance(v, (int, float))) \
                if isinstance(pc, dict) else 0
        self.cards["plays"].set_value(fmt_count(plays))

        artists = None
        if db is not None:
            try:
                artists = sum(1 for _a, c, _s in db.top_artists(5000)
                              if int(c or 0) > 0)
            except Exception:
                artists = None
        if artists is None:
            asc = _call_or_attr(stats, "artist_seconds")
            artists = len(asc) if isinstance(asc, dict) else 0
        self.cards["artists"].set_value(fmt_count(artists))

        sessions = _call_or_attr(stats, "sessions")
        if sessions is None and db is not None:
            try:
                sessions = int(db.meta_get("sessions", "0") or 0)
            except Exception:
                sessions = 0
        self.cards["sessions"].set_value(
            fmt_count(int(_num(sessions, 0))))

    def _update_year_combo(self, db, stats) -> None:
        years: set[str] = set()
        if db is not None:
            try:
                years |= {str(m)[:4] for m, _v in db.minutes_by_month(24)}
            except Exception:
                pass
            try:
                years |= {str(d)[:4] for d, _v in db.daily_heatmap(400)}
            except Exception:
                pass
        first = _call_or_attr(stats, "first_played")
        if isinstance(first, str) and len(first) >= 4 and first[:4].isdigit():
            years.add(first[:4])
        years.add(str(date.today().year))
        items = ["All time"] + sorted(
            (y for y in years if len(y) == 4 and y.isdigit()), reverse=True)
        current = self.year_combo.currentText()
        self.year_combo.blockSignals(True)
        self.year_combo.clear()
        self.year_combo.addItems(items)
        if current in items:
            self.year_combo.setCurrentText(current)
        else:
            self.year_combo.setCurrentIndex(0)
        self.year_combo.blockSignals(False)

    def _update_charts(self, db, stats) -> None:
        enabled = self._pref("stats_charts_enabled", True)
        self.charts_panel.setVisible(enabled)
        if not enabled:
            return

        pts: list[tuple[str, float]] = []
        if db is not None:
            try:
                pts = [(str(d)[5:], float(c))
                       for d, c in db.plays_over_time(30)]
            except Exception:
                pts = []
        if self.line_chart is None:
            self._drop_skeletons()
            self.line_chart = LineChart()
            self._line_host.addWidget(self.line_chart)
        self.line_chart.set_data(pts, self._theme, lambda v: f"{v:.0f}")
        active_days = len(pts)
        self._line_caption.setText(
            f"Plays over time — last 30 days"
            + (f" ({fmt_count(sum(v for _l, v in pts))} plays, "
               f"{fmt_count(active_days)} days)" if pts else ""))

        months: list[tuple[str, float]] = []
        if db is not None:
            try:
                months = [(_month_label(str(m)), float(v))
                          for m, v in db.minutes_by_month(12)]
            except Exception:
                months = []
        if self.bar_chart is None:
            self.bar_chart = BarChart()
            self._bar_host.addWidget(self.bar_chart)
        self.bar_chart.set_data(months, self._theme,
                                lambda v: f"{v:.0f} min")

        artist_rows: list[tuple[str, float]] = []
        if db is not None:
            try:
                artist_rows = [(str(a), _num(s))
                               for a, _c, s in db.top_artists(8)]
            except Exception:
                artist_rows = []
        if not artist_rows:
            asc = _call_or_attr(stats, "artist_seconds")
            if isinstance(asc, dict) and asc:
                ranked = sorted(asc.items(), key=lambda kv: -kv[1])[:8]
                artist_rows = [(str(a), _num(s)) for a, s in ranked]
        if self.artist_chart is None:
            self.artist_chart = HBarChart()
            self._artist_host.addWidget(self.artist_chart)
        self.artist_chart.set_data(artist_rows, self._theme, fmt_hours)

        genre_rows: list[tuple[str, float]] = []
        if db is not None:
            try:
                genre_rows = [(str(g), float(c))
                              for g, c in db.top_genres(8)]
            except Exception:
                genre_rows = []
        if self.genre_chart is None:
            self.genre_chart = HBarChart()
            self._genre_host.addWidget(self.genre_chart)
        self.genre_chart.set_data(
            genre_rows, self._theme, lambda v: f"{int(v)} plays")

    def _update_heatmap(self, db) -> None:
        enabled = self._pref("stats_heatmap_enabled", True)
        self.heatmap_panel.setVisible(enabled)
        if not enabled:
            return
        rows: list[tuple[str, float]] = []
        if db is not None:
            try:
                rows = [(str(d), float(s))
                        for d, s in db.daily_heatmap(365)]
            except Exception:
                rows = []
        if self.heatmap is None:
            self._drop_skeletons()
            self.heatmap = Heatmap()
            self._heat_host.addWidget(self.heatmap)
        self.heatmap.set_data(rows, self._theme)

    def _update_top_tracks(self, db, stats) -> None:
        rows = None
        if db is not None:
            try:
                rows = db.top_tracks(100)
            except Exception:
                rows = None
        if not rows:
            tag_cache = getattr(self._mw, "_tag_cache", None) or {}
            tops = _call_with(stats, "top_tracks", tag_cache, 100)
            rows = tops if isinstance(tops, list) else None
        favorites = getattr(self._mw, "favorites", None)
        try:
            fav_set = {str(p) for p in (favorites or [])}
        except Exception:
            fav_set = set()
        if self.tracks_list is not None:
            self.tracks_list.set_rows(rows or [], fav_set)

    # -- wrapped -------------------------------------------------------------------
    def _selected_year(self) -> int:
        text = self.year_combo.currentText()
        if len(text) == 4 and text.isdigit():
            return int(text)
        for i in range(1, self.year_combo.count()):
            item = self.year_combo.itemText(i)
            if len(item) == 4 and item.isdigit():
                return int(item)
        return date.today().year

    def _open_wrapped(self) -> None:
        if not self._pref("stats_wrapped_enabled", True):
            self._toast("Wrapped reports are disabled in settings")
            return
        year = self._selected_year()
        try:
            data = self.gather_wrapped(year)
        except Exception:
            log.exception("wrapped gather failed")
            data = {}
        dlg = WrappedDialog(year, data, self._theme, self,
                            toast_cb=self._toast,
                            cover_loader=self._make_cover_loader())
        dlg.show()
        dlg.raise_()

    def gather_wrapped(self, year: int) -> dict:
        """Collect the Wrapped payload for *year* (db first, legacy fallback).

        Every field is computed behind its own guard so a missing table,
        locked database or legacy-only window simply drops that card.
        """
        data: dict = {
            "total_seconds": 0.0, "total_plays": 0, "sessions": 0,
            "top_artist": None, "top_genre": None, "top_track": None,
            "top_album": None, "new_count": None, "superlative": None,
            "first_played": "",
        }
        db = self._db_safe()
        stats = getattr(self._mw, "stats", None)
        start, end = _year_bounds(year)

        if db is not None:
            try:
                conn = db._conn()
            except Exception:
                conn = None
            if conn is not None:
                try:
                    row = conn.execute(
                        "SELECT COUNT(*), COALESCE(SUM(seconds),0) "
                        "FROM play_events WHERE played_at>=? AND played_at<?",
                        (start, end)).fetchone()
                    data["total_plays"] = int(row[0])
                    data["total_seconds"] = _num(row[1])
                except Exception:
                    log.debug("wrapped totals failed", exc_info=True)
                try:
                    hist = [0.0] * 24
                    for h, s in conn.execute(
                            "SELECT CAST(strftime('%H', played_at, "
                            "'unixepoch', 'localtime') AS INTEGER), "
                            "SUM(seconds) FROM play_events "
                            "WHERE played_at>=? AND played_at<? GROUP BY 1",
                            (start, end)):
                        hour = int(h)
                        if 0 <= hour < 24:
                            hist[hour] += _num(s)
                    data["superlative"] = _superlative_from_hours(hist)
                except Exception:
                    log.debug("wrapped hour hist failed", exc_info=True)
                try:
                    row = conn.execute(
                        "SELECT t.artist, COUNT(*), COALESCE(SUM(pe.seconds),0)"
                        " FROM play_events pe JOIN tracks t ON t.path=pe.path"
                        " WHERE pe.played_at>=? AND pe.played_at<?"
                        " AND t.artist<>'' GROUP BY t.artist"
                        " ORDER BY 3 DESC LIMIT 1", (start, end)).fetchone()
                    if row is not None:
                        data["top_artist"] = (str(row[0]), int(row[1]),
                                              _num(row[2]))
                except Exception:
                    log.debug("wrapped top artist failed", exc_info=True)
                try:
                    row = conn.execute(
                        "SELECT t.genre, COUNT(*) FROM play_events pe "
                        "JOIN tracks t ON t.path=pe.path "
                        "WHERE pe.played_at>=? AND pe.played_at<?"
                        " AND t.genre<>'' GROUP BY t.genre"
                        " ORDER BY 2 DESC LIMIT 1", (start, end)).fetchone()
                    if row is not None:
                        data["top_genre"] = (str(row[0]), int(row[1]))
                except Exception:
                    log.debug("wrapped top genre failed", exc_info=True)
                try:
                    row = conn.execute(
                        "SELECT t.title, t.artist, COUNT(*), t.path "
                        "FROM play_events pe JOIN tracks t ON t.path=pe.path "
                        "WHERE pe.played_at>=? AND pe.played_at<? "
                        "GROUP BY t.path ORDER BY 3 DESC LIMIT 1",
                        (start, end)).fetchone()
                    if row is not None:
                        title = str(row[0]) or Path(str(row[3])).stem
                        data["top_track"] = (title, str(row[1]),
                                             int(row[2]), str(row[3]))
                except Exception:
                    log.debug("wrapped top track failed", exc_info=True)
                try:
                    row = conn.execute(
                        "SELECT t.album, t.artist, COUNT(*) FROM play_events pe"
                        " JOIN tracks t ON t.path=pe.path "
                        "WHERE pe.played_at>=? AND pe.played_at<?"
                        " AND t.album<>'' GROUP BY t.album, t.artist"
                        " ORDER BY 3 DESC LIMIT 1", (start, end)).fetchone()
                    if row is not None:
                        data["top_album"] = (str(row[0]), str(row[1]),
                                             int(row[2]))
                except Exception:
                    log.debug("wrapped top album failed", exc_info=True)
                try:
                    data["new_count"] = len(db.tracks_where(
                        "added_at>=? AND added_at<?", (start, end),
                        order=""))
                except Exception:
                    log.debug("wrapped new-count failed", exc_info=True)

        if data["total_plays"] <= 0:
            # legacy fallback (year-agnostic totals)
            data["total_seconds"] = _num(
                _call_or_attr(stats, "total_seconds"), 0.0)
            pc = _call_or_attr(stats, "play_counts")
            if isinstance(pc, dict):
                data["total_plays"] = sum(
                    int(v) for v in pc.values() if _num(v, -1) >= 0)
            asc = _call_or_attr(stats, "artist_seconds")
            if isinstance(asc, dict) and asc:
                name, seconds = max(asc.items(), key=lambda kv: kv[1])
                data["top_artist"] = (str(name), 0, _num(seconds))
            tag_cache = getattr(self._mw, "_tag_cache", None) or {}
            tops = _call_with(stats, "top_tracks", tag_cache, 1)
            if tops:
                try:
                    title, artist, count, path = tops[0]
                    data["top_track"] = (str(title), str(artist),
                                         int(count), str(path))
                except Exception:
                    pass

        sessions = _call_or_attr(stats, "sessions")
        if sessions is None and db is not None:
            try:
                sessions = int(db.meta_get("sessions", "0") or 0)
            except Exception:
                sessions = 0
        data["sessions"] = int(_num(sessions, 0))

        first = _call_or_attr(stats, "first_played")
        if isinstance(first, str) and first:
            data["first_played"] = first

        if not data["superlative"] and db is not None:
            try:
                rows = db.daily_heatmap(365)
                streak = _longest_streak(rows)
                if streak >= 7:
                    data["superlative"] = (
                        f"On a roll — {streak} days of listening in a row")
            except Exception:
                pass
        return data


def _superlative_from_hours(hist: Sequence[float]) -> str | None:
    """Fun label from a 24-bin hour histogram (None when nothing fits)."""
    total = sum(_num(v) for v in hist)
    if total <= 0:
        return None
    evening = sum(_num(v) for v in hist[18:]) + sum(_num(v) for v in hist[:5])
    morning = sum(_num(v) for v in hist[5:12])
    midday = sum(_num(v) for v in hist[12:18])
    if evening / total > 0.5:
        return "Night owl 🦉 — most of your listening happened after dark"
    if morning / total > 0.5:
        return "Early bird 🐦 — you soundtracked the sunrise"
    if midday / total > 0.5:
        return "Daydreamer ☀️ — afternoons were your studio"
    return None


def _longest_streak(rows: Iterable[tuple[str, float]]) -> int:
    """Longest run of consecutive active days in ``[(date_str, seconds)]``."""
    days: set[date] = set()
    for item in rows or []:
        try:
            day_str, seconds = item
            if _num(seconds) > 0:
                days.add(date.fromisoformat(str(day_str)[:10]))
        except Exception:
            continue
    if not days:
        return 0
    best = 0
    for day in days:
        if day - timedelta(days=1) in days:
            continue
        length = 1
        while day + timedelta(days=length) in days:
            length += 1
        best = max(best, length)
    return best
