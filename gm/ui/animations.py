"""Golden Music v3.0 — lightweight animation helpers.

A tiny toolbox of QPropertyAnimation/QVariantAnimation wrappers used across
the v3 UI (page fades, palette pop, list stagger, press feedback). Design
rules:

* ``REDUCE_ANIMATIONS`` — module-level accessibility switch. Every helper
  no-ops when it is set (callbacks still fire, synchronously, so callers
  never need to branch).
* Every animation object is parented to the animated widget, so it is
  garbage-collected with the widget even if the caller forgets it.
* Widgets that already carry a *foreign* QGraphicsOpacityEffect are skipped
  rather than corrupted (a QWidget can only hold one graphics effect).
* Opacity effects are removed as soon as the animation finishes — a resting
  QGraphicsOpacityEffect disables backing-store optimizations and costs
  repaint time, so we never leave one attached.

No per-pixel hacks, no busy loops: everything rides Qt's animation timers.
"""
from __future__ import annotations

import logging
from typing import Callable, Iterable, Sequence

from PyQt6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRect,
    QTimer,
    QVariantAnimation,
)
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QWidget

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Accessibility switch
# ---------------------------------------------------------------------------
REDUCE_ANIMATIONS: bool = False

#: widgets animated by helpers keep their effect / animation refs here so we
#: can interrupt a running animation cleanly instead of stacking effects.
_EFFECT_ATTR = "_gm_opacity_effect"
_FADE_ANIM_ATTR = "_gm_fade_anim"
_PULSE_ANIM_ATTR = "_gm_pulse_anim"

_EASINGS: dict[str, QEasingCurve.Type] = {
    "linear": QEasingCurve.Type.Linear,
    "in_quad": QEasingCurve.Type.InQuad,
    "out_quad": QEasingCurve.Type.OutQuad,
    "in_out_quad": QEasingCurve.Type.InOutQuad,
    "out_cubic": QEasingCurve.Type.OutCubic,
    "in_out_cubic": QEasingCurve.Type.InOutCubic,
    "out_back": QEasingCurve.Type.OutBack,
}

def set_reduce_animations(reduce_: bool) -> None:
    """Toggle the global reduce-motion switch (accessibility setting)."""
    global REDUCE_ANIMATIONS
    REDUCE_ANIMATIONS = bool(reduce_)


def easing_curve(name: str) -> QEasingCurve:
    """Map a friendly easing name onto a QEasingCurve (unknown -> out_cubic)."""
    return _EASINGS.get(str(name).lower(), _EASINGS["out_cubic"])


# ---------------------------------------------------------------------------
# internal helpers
# ---------------------------------------------------------------------------
def _managed_effect(widget: QWidget) -> QGraphicsOpacityEffect | None:
    """Return the opacity effect *we* installed on *widget*, if any."""
    effect = getattr(widget, _EFFECT_ATTR, None)
    if effect is not None and widget.graphicsEffect() is effect:
        return effect
    return None


def _stop_tracked(widget: QWidget, attr: str) -> None:
    """Stop (and drop) a tracked animation, letting its cleanup run."""
    anim = getattr(widget, attr, None)
    if anim is None:
        return
    try:
        setattr(widget, attr, None)
        anim.stop()
    except RuntimeError:  # wrapper already gone with the C++ object
        pass


def reset_opacity(widget: QWidget) -> None:
    """Remove any helper-managed opacity effect from *widget*.

    Public on purpose: it is the "make sure this widget is fully visible"
    escape hatch used by e.g. SkeletonRows.stop(). Foreign effects are left
    alone.
    """
    if not isinstance(widget, QWidget):
        return
    _stop_tracked(widget, _FADE_ANIM_ATTR)
    _stop_tracked(widget, _PULSE_ANIM_ATTR)
    effect = _managed_effect(widget)
    if effect is not None:
        try:
            if widget.graphicsEffect() is effect:
                widget.setGraphicsEffect(None)
        except RuntimeError:
            pass
        setattr(widget, _EFFECT_ATTR, None)


def _make_fade(widget: QWidget, start: float, end: float, ms: int,
               cb: Callable[[], None] | None,
               final: Callable[[], None] | None = None) -> None:
    """Shared engine for fade_in/fade_out (see those for semantics)."""
    if not isinstance(widget, QWidget):
        if cb:
            cb()
        return

    if REDUCE_ANIMATIONS:
        reset_opacity(widget)
        if final:
            final()
        if cb:
            cb()
        return
    existing = widget.graphicsEffect()
    if existing is not None and existing is not _managed_effect(widget):
        # foreign effect (another subsystem is fading this widget): skip
        if cb:
            cb()
        return

    effect = _managed_effect(widget)
    if effect is None:
        effect = QGraphicsOpacityEffect(widget)
        setattr(widget, _EFFECT_ATTR, effect)
        widget.setGraphicsEffect(effect)

    # interrupt any fade we already started on this widget
    _stop_tracked(widget, _FADE_ANIM_ATTR)

    anim = QPropertyAnimation(effect, b"opacity", widget)
    setattr(widget, _FADE_ANIM_ATTR, anim)
    anim.setStartValue(float(start))
    anim.setEndValue(float(end))
    anim.setDuration(max(0, int(ms)))
    anim.setEasingCurve(_EASINGS["out_cubic"])

    def _done() -> None:
        if getattr(widget, _FADE_ANIM_ATTR, None) is anim:
            setattr(widget, _FADE_ANIM_ATTR, None)
        try:
            if widget.graphicsEffect() is effect:
                widget.setGraphicsEffect(None)
            setattr(widget, _EFFECT_ATTR, None)
        except RuntimeError:
            pass
        anim.deleteLater()
        if final:
            final()
        if cb:
            cb()

    anim.finished.connect(_done)
    effect.setOpacity(float(start))
    anim.start()


# ---------------------------------------------------------------------------
# public helpers
# ---------------------------------------------------------------------------
def fade_in(widget: QWidget, ms: int = 140,
            cb: Callable[[], None] | None = None) -> None:
    """Fade *widget* from 0 to full opacity over *ms* milliseconds.

    The QGraphicsOpacityEffect is removed when the animation finishes (a
    resting effect keeps the widget on the slow repaint path). *cb* fires
    after the fade completes, or synchronously when REDUCE_ANIMATIONS is on
    or the widget already carries a foreign graphics effect.
    """
    _make_fade(widget, 0.0, 1.0, ms, cb)


def fade_out(widget: QWidget, ms: int = 120,
             cb: Callable[[], None] | None = None) -> None:
    """Fade *widget* from full opacity to 0, then hide() it.

    *cb* (if given) runs after the widget is hidden. In reduce mode, or when
    a foreign effect owns the widget, the widget is hidden immediately.
    """
    if not isinstance(widget, QWidget):
        if cb:
            cb()
        return

    def _final() -> None:
        widget.hide()

    existing = widget.graphicsEffect()
    if existing is not None and existing is not _managed_effect(widget):
        widget.hide()
        if cb:
            cb()
        return

    _make_fade(widget, 1.0, 0.0, ms, cb, final=_final)


_SLIDE_OFFSETS: dict[str, tuple[int, int]] = {
    # direction -> where the widget starts, relative to its final position
    "left": (-1, 0),    # enters from the left, moves right
    "right": (1, 0),    # enters from the right, moves left
    "up": (0, 1),       # enters from below, moves up
    "down": (0, -1),    # enters from above, moves down
}


def slide_in(widget: QWidget, ms: int = 160, direction: str = "left",
             cb: Callable[[], None] | None = None, distance: int = 48) -> None:
    """Slide *widget* into its current position from *direction*.

    Animates pos() from an offset to the widget's current geometry position,
    so it suits popups/floating panels (widgets inside a layout will simply
    be snapped back by the layout — use fade helpers for those). *cb* runs
    on completion (synchronously in reduce mode).
    """
    if not isinstance(widget, QWidget):
        if cb:
            cb()
        return
    if direction not in _SLIDE_OFFSETS:
        raise ValueError(f"slide_in: unknown direction {direction!r}")

    target = widget.pos()
    dx, dy = _SLIDE_OFFSETS[direction]
    offset = max(1, int(distance))
    start = QPoint(target.x() + dx * offset, target.y() + dy * offset)

    if REDUCE_ANIMATIONS:
        widget.move(target)
        if cb:
            cb()
        return

    anim = QPropertyAnimation(widget, b"pos", widget)
    anim.setStartValue(start)
    anim.setEndValue(target)
    anim.setDuration(max(0, int(ms)))
    anim.setEasingCurve(_EASINGS["out_cubic"])
    if cb:
        anim.finished.connect(cb)
    widget.move(start)
    anim.start()


def pop_in(widget: QWidget, ms: int = 130) -> None:
    """Give *widget* a scale-and-fade "pop" entrance.

    Robust by construction: the geometry delta is applied at window-frame
    level (only when *widget* is itself a top-level window/popup) so it can
    never fight a layout. Embedded widgets get a plain fade instead.
    """
    if not isinstance(widget, QWidget):
        return
    if REDUCE_ANIMATIONS:
        return
    if widget.isWindow():
        final = widget.geometry()
        w = max(1, int(final.width() * 0.94))
        h = max(1, int(final.height() * 0.90))
        center = final.center()
        start = QRect(center.x() - w // 2, center.y() - h // 2, w, h)
        anim = QPropertyAnimation(widget, b"geometry", widget)
        anim.setStartValue(start)
        anim.setEndValue(final)
        anim.setDuration(max(0, int(ms)))
        anim.setEasingCurve(_EASINGS["out_cubic"])
        widget.setGeometry(start)
        anim.start()
    fade_in(widget, ms=ms)


def stagger_fade(widgets: Iterable[QWidget], per_item_ms: int = 26,
                 start_ms: int = 60) -> None:
    """Reveal *widgets* one after another with a small fade.

    All widgets are parked at opacity 0 immediately (no flash of a full
    list), then fade_in() is scheduled with increasing delays via QTimer.
    Capped at 24 items — a 200-row list animating is noise, not polish.
    No-op when REDUCE_ANIMATIONS is on.
    """
    if REDUCE_ANIMATIONS:
        return
    items: Sequence[QWidget] = [w for w in widgets if isinstance(w, QWidget)][:24]
    for w in items:
        if w.graphicsEffect() is None:
            effect = QGraphicsOpacityEffect(w)
            effect.setOpacity(0.0)
            setattr(w, _EFFECT_ATTR, effect)
            w.setGraphicsEffect(effect)
        w.show()
    for i, w in enumerate(items):
        QTimer.singleShot(start_ms + i * per_item_ms,
                          lambda w=w: fade_in(w, ms=110))


# ---------------------------------------------------------------------------
# generic value animation
# ---------------------------------------------------------------------------
class ValueAnimator:
    """Thin wrapper around QVariantAnimation with named easings.

    ``ValueAnimator(start, end, ms, easing, on_update, on_done)`` — on_update
    receives each interpolated value, on_done fires once at the end. Keep a
    reference to the animator while it runs (the underlying QVariantAnimation
    is parentless so it is owned by this Python object).

    Unlike the decorative helpers this primitive does NOT consult
    REDUCE_ANIMATIONS — it drives functional things such as spinner rotation;
    the decorative wrapper :meth:`pulse` does respect the flag.
    """

    def __init__(self, start, end, ms: int, easing: str = "out_cubic",
                 on_update: Callable[[object], None] | None = None,
                 on_done: Callable[[], None] | None = None) -> None:
        self._anim = QVariantAnimation()
        self._anim.setStartValue(start)
        self._anim.setEndValue(end)
        self._anim.setDuration(max(0, int(ms)))
        self._anim.setEasingCurve(easing_curve(easing))
        if callable(on_update):
            self._anim.valueChanged.connect(
                lambda value: on_update(value))
        if callable(on_done):
            self._anim.finished.connect(on_done)

    # -- control ----------------------------------------------------------
    def start(self) -> None:
        """Start (or restart) the animation."""
        self._anim.start()

    def stop(self) -> None:
        """Stop the animation."""
        self._anim.stop()

    def is_running(self) -> bool:
        """True while the underlying animation is in the Running state."""
        return self._anim.state() == QAbstractAnimation.State.Running

    # -- decorative helper --------------------------------------------------
    @classmethod
    def pulse(cls, widget: QWidget, ms: int = 240) -> None:
        """Brief 1 -> 0.86 -> 1 opacity pulse (press feedback).

        No-op when REDUCE_ANIMATIONS is on or the widget already carries a
        graphics effect (e.g. mid-fade) — skipping is safer than corrupting.
        """
        if REDUCE_ANIMATIONS or not isinstance(widget, QWidget):
            return
        if widget.graphicsEffect() is not None:
            return

        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(1.0)
        setattr(widget, _EFFECT_ATTR, effect)
        widget.setGraphicsEffect(effect)

        anim = QVariantAnimation(widget)
        setattr(widget, _PULSE_ANIM_ATTR, anim)
        anim.setStartValue(1.0)
        anim.setEndValue(1.0)
        anim.setKeyValueAt(0.5, 0.86)
        anim.setDuration(max(0, int(ms)))
        anim.setEasingCurve(QEasingCurve.Type.Linear)
        anim.valueChanged.connect(
            lambda value: _safe_set_opacity(effect, value))

        def _done() -> None:
            if getattr(widget, _PULSE_ANIM_ATTR, None) is anim:
                setattr(widget, _PULSE_ANIM_ATTR, None)
            try:
                if widget.graphicsEffect() is effect:
                    widget.setGraphicsEffect(None)
                setattr(widget, _EFFECT_ATTR, None)
            except RuntimeError:
                pass
            anim.deleteLater()

        anim.finished.connect(_done)
        anim.start()


def _safe_set_opacity(effect: QGraphicsOpacityEffect, value) -> None:
    """Guarded opacity write (the effect may already be deleted)."""
    try:
        effect.setOpacity(float(value))
    except RuntimeError:
        pass
