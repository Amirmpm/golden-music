"""Golden Music v3.0 — empty states, skeletons and inline spinner.

Three small building blocks for "nothing here yet" moments:

* :class:`EmptyState` — big muted/gold icon, title, hint, optional gold
  action button. Fades in on show (honours REDUCE_ANIMATIONS).
* :class:`SkeletonRows` — N rounded placeholder bars (QFrame#SkeletonLine,
  styled by gm.theme_qss) with a subtle top-to-bottom shimmer built on
  gm.ui.animations pulses; disabled entirely when REDUCE_ANIMATIONS is on.
* :class:`InlineSpinner` — 16px arc spinner painted in the theme gold,
  driven by a looping QVariantAnimation capped at ~40 repaints/second and
  automatically stopped while hidden.

Colors come from the theme dict (config.Theme.get(...)); text colors are
handled by the application stylesheet via the EmptyTitle/EmptyHint/GoldBtn
objectNames.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from PyQt6.QtCore import QRectF, Qt, QTimer, QVariantAnimation
from PyQt6.QtGui import QColor, QHideEvent, QPainter, QPen, QShowEvent
from PyQt6.QtWidgets import (
    QFrame,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from gm.ui.animations import REDUCE_ANIMATIONS, ValueAnimator, fade_in, reset_opacity
from icons import get_dpr, render_icon

log = logging.getLogger(__name__)

ICON_PX = 44
#: relative widths of the skeleton bars (cycles for rows > len)
_WIDTH_PATTERN = (0.92, 0.66, 0.84, 0.58, 0.90, 0.74, 0.88, 0.62,
                  0.80, 0.70, 0.86, 0.64)
_SKELETON_BASE_W = 320
_SHIMMER_PERIOD_MS = 1700


class EmptyState(QWidget):
    """Big icon + title + hint (+ optional action) centered placeholder."""

    def __init__(self, icon: str, title: str, hint: str,
                 action_text: str | None = None,
                 on_action: Callable[[], None] | None = None,
                 theme: dict | None = None) -> None:
        super().__init__()
        self._theme: dict = dict(theme) if theme else {}
        self._icon = icon
        self._on_action = on_action

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(8)

        layout.addStretch(1)

        self.icon_label = QLabel(self)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._refresh_icon()
        layout.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignCenter)

        self.title_label = QLabel(title, self)
        self.title_label.setObjectName("EmptyTitle")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setWordWrap(True)
        layout.addWidget(self.title_label)

        self.hint_label = QLabel(hint, self)
        self.hint_label.setObjectName("EmptyHint")
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint_label.setWordWrap(True)
        layout.addWidget(self.hint_label)

        self.action_button: QPushButton | None = None
        if action_text:
            btn = QPushButton(action_text, self)
            btn.setObjectName("GoldBtn")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(self._run_action)
            self.action_button = btn
            layout.addSpacing(10)
            layout.addWidget(btn, 0, Qt.AlignmentFlag.AlignHCenter)

        layout.addStretch(1)

    # ------------------------------------------------------------------ api --
    def set_theme(self, theme: dict) -> None:
        """Swap the icon tint for *theme* (text colors come from app QSS)."""
        self._theme = dict(theme) if theme else {}
        self._refresh_icon()

    # --------------------------------------------------------------- internals --
    def _refresh_icon(self) -> None:
        color = self._theme.get("gold_deep", "#b88a33")
        self.icon_label.setPixmap(render_icon(self._icon, ICON_PX, color, get_dpr()))

    def _run_action(self) -> None:
        if not callable(self._on_action):
            return
        try:
            self._on_action()
        except Exception:  # action boundary
            log.exception("EmptyState: action callback raised")

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        fade_in(self, ms=160)


class SkeletonRows(QWidget):
    """N rounded placeholder bars with a subtle looping shimmer."""

    def __init__(self, rows: int = 6, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[QFrame] = []
        self._theme: dict = {}
        self._shimmering = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(10)

        for i in range(max(1, int(rows))):
            bar = QFrame(self)
            bar.setObjectName("SkeletonLine")
            bar.setFixedHeight(12)
            frac = _WIDTH_PATTERN[i % len(_WIDTH_PATTERN)]
            bar.setMaximumWidth(max(40, int(_SKELETON_BASE_W * frac)))
            bar.setSizePolicy(QSizePolicy.Policy.Preferred,
                              QSizePolicy.Policy.Fixed)
            layout.addWidget(bar, 0, Qt.AlignmentFlag.AlignLeft)
            self._rows.append(bar)

        self._timer = QTimer(self)
        self._timer.setInterval(_SHIMMER_PERIOD_MS)
        self._timer.timeout.connect(self._shimmer)

    # ------------------------------------------------------------------ api --
    def set_theme(self, theme: dict) -> None:
        """Store *theme* for API symmetry — bar fills are styled by the app
        stylesheet via the SkeletonLine objectName."""
        self._theme = dict(theme) if theme else {}

    def start(self) -> None:
        """Start the shimmer loop (no-op when REDUCE_ANIMATIONS is on)."""
        if REDUCE_ANIMATIONS:
            return
        self._shimmering = True
        self._timer.start()

    def stop(self) -> None:
        """Stop the shimmer loop immediately and clear in-flight effects."""
        self._shimmering = False
        self._timer.stop()
        for bar in self._rows:
            reset_opacity(bar)

    def rows(self) -> int:
        """Number of skeleton bars."""
        return len(self._rows)

    # --------------------------------------------------------------- internals --
    def _shimmer(self) -> None:
        """Schedule one shimmer pass: staggered pulses sweep down the rows."""
        for i, bar in enumerate(self._rows):
            QTimer.singleShot(i * 80, lambda b=bar: self._pulse_row(b))

    def _pulse_row(self, bar: QFrame) -> None:
        # singleShot callbacks may land after stop(); the guard makes
        # stop() deterministic instead of "stops within ~300ms".
        if self._shimmering:
            ValueAnimator.pulse(bar, ms=260)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.start()

    def hideEvent(self, event: QHideEvent) -> None:
        self.stop()
        super().hideEvent(event)


class InlineSpinner(QWidget):
    """16px arc spinner painted in the theme gold.

    The rotation is a looping QVariantAnimation (0..360 over 1s); repaints
    are throttled to >= 25ms apart (~40 FPS cap) so long lists of spinners
    stay cheap. The animation stops automatically when the widget hides.
    """

    def __init__(self, theme: dict | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = (theme or {}).get("gold", "#e9b957")
        self._angle = 0.0
        self._last_paint = 0.0
        self.setFixedSize(16, 16)

        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(360.0)
        self._anim.setDuration(1000)
        self._anim.setLoopCount(-1)
        self._anim.valueChanged.connect(self._on_value)

    # ------------------------------------------------------------------ api --
    def set_theme(self, theme: dict) -> None:
        """Adopt the gold accent from *theme*."""
        self.set_color((theme or {}).get("gold", self._color))

    def set_color(self, color: str) -> None:
        """Paint the arc in *color* (any QColor-parseable string)."""
        self._color = str(color)
        self.update()

    def is_spinning(self) -> bool:
        """True while the rotation animation is running."""
        return self._anim.state() == QVariantAnimation.State.Running

    def start(self) -> None:
        """Start spinning (idempotent)."""
        if not self.is_spinning():
            self._anim.start()

    def stop(self) -> None:
        """Stop spinning (idempotent)."""
        if self.is_spinning():
            self._anim.stop()

    # --------------------------------------------------------------- internals --
    def _on_value(self, value) -> None:
        self._angle = float(value) % 360.0
        now = time.monotonic()
        if now - self._last_paint >= 0.025:  # 40 FPS repaint cap
            self._last_paint = now
            self.update()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.start()

    def hideEvent(self, event: QHideEvent) -> None:
        self.stop()
        super().hideEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor(self._color))
        pen.setWidthF(1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        rect = QRectF(1.4, 1.4, self.width() - 2.8, self.height() - 2.8)
        start = int(self._angle * 16) % 5760
        painter.drawArc(rect, start, 300 * 16)
        painter.end()
