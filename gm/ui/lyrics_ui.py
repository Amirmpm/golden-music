"""Golden Music 3.0 — lyrics UI layer (Qt widgets).

The Qt half of the lyrics overhaul. Everything here *renders and edits*
lyrics that the Qt-free engine modules already handle:

* ``gm.lyrics_quality.verdict``      -> :class:`LyricsQualityBar`
* ``gm.lyrics_io.to_lrc_text`` / ``apply_offset`` -> :class:`LyricsEditorDialog`
* ``gm.lyrics_translate.merge_translations``      -> bilingual display

Components
----------
* :class:`LyricsView`          — scrollable, bilingual, karaoke-highlight pane.
* :class:`LyricsQualityBar`    — "these lyrics look wrong / better lyrics
                                  found" warning banner.
* :class:`LyricsEditorDialog`  — LRC / plain-text editor with offset shift.
* :class:`FloatingLyricsWidget`— NetEase-style desktop floating lyrics.
* :class:`MiniLyricsBar`       — compact always-on-top one-line lyrics bar.
* :class:`GhostBtn` / :class:`GoldBtn` — button roles shared by the lyrics
  components (styled by the ``#GhostBtn`` / ``#GoldBtn`` rules of
  ``gm.theme_qss.build_qss_v3``).

Conventions
-----------
* Lines are ``[(time_ms_or_None, text)]``; bilingual rows are
  ``[(time_ms_or_None, original, translation_or_None)]``.
* Offsets are applied by the CALLER — these widgets always receive
  already-shifted lines (the editor is the one place a shift happens,
  immediately via "Shift timings").
* RTL: Persian/Arabic lines get a per-label ``RightToLeft`` layout
  direction (see :func:`_is_rtl`) so bilingual rows read correctly.
* Reduced motion is respected via the guarded ``gm.ui.animations``
  import (``REDUCE_ANIMATIONS`` / ``fade_in``) with a no-flicker fallback.
"""
from __future__ import annotations

import bisect
import logging
import os
import re

from PyQt6.QtCore import (
    Qt, QPoint, QRectF, pyqtSignal, QPropertyAnimation, QVariantAnimation,
    QEasingCurve,
)
from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QToolButton, QDialog, QVBoxLayout,
    QHBoxLayout, QScrollArea, QPlainTextEdit, QSpinBox, QComboBox,
    QMessageBox, QFrame, QGraphicsOpacityEffect,
)
from PyQt6.QtGui import (
    QColor, QBrush, QIcon, QPen, QPainter, QPainterPath, QMouseEvent,
    QFontMetrics,
)

from config import Theme
from icons import Icon, render_icon, get_dpr
from gm.theme_qss import build_qss_v3
from gm.lyrics_quality import verdict
from gm.lyrics_io import to_lrc_text, apply_offset
from gm.lyrics_translate import merge_translations

log = logging.getLogger("app.lyrics.ui")

__all__ = [
    "GhostBtn", "GoldBtn",
    "LyricsView", "LyricsQualityBar", "LyricsEditorDialog",
    "FloatingLyricsWidget", "MiniLyricsBar",
    "parse_lrc_local",
]

# ---------------------------------------------------------------------------
# Optional animation helpers (guarded — gm.ui.animations may be absent)
# ---------------------------------------------------------------------------
try:  # pragma: no cover - the animations module may be absent in dev checkouts
    from gm.ui import animations as _animations
    fade_in = _animations.fade_in
except Exception:  # noqa: BLE001 - degrade gracefully, never crash the UI
    _animations = None

    def fade_in(widget: QWidget, duration: int = 180) -> None:  # type: ignore[misc]
        """No-op fallback so call sites never need a guard."""


def _env_reduce_animations() -> bool:
    """Fallback switch: the GM_REDUCE_ANIMATIONS environment variable."""
    return os.environ.get(
        "GM_REDUCE_ANIMATIONS", "0").strip().lower() not in (
        "", "0", "false", "no")


def _reduce_animations() -> bool:
    """Live reduce-motion flag (gm.ui.animations wins; env var fallback)."""
    if _animations is not None:
        return bool(getattr(_animations, "REDUCE_ANIMATIONS", False))
    return _env_reduce_animations()


#: Import-time snapshot of the reduce-motion switch — call sites that need
#: the LIVE value (it can be flipped via ``set_reduce_animations()`` at
#: runtime) use :func:`_reduce_animations` instead.
REDUCE_ANIMATIONS: bool = _reduce_animations()


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

#: A single lyrics line: ``(time_ms_or_None, text)``; bilingual rows are
#: ``(time_ms_or_None, original, translation_or_None)`` — see the module
#: docstring for the exact contracts.

_LRC_TS_RE = re.compile(r"\[(\d{1,2}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
# A bracketed token that *looks* like a timestamp (digits then ':') but did
# not fully parse — e.g. "[05:XX]" or "[1:23.4567]".
_STAMPISH_RE = re.compile(r"\[\d{1,2}:[^\]]*\]")

# Strong-RTL Unicode blocks (Arabic script + presentation forms; Hebrew is
# included because it shares the bidi behaviour).
_RTL_RANGES: tuple[tuple[int, int], ...] = (
    (0x0590, 0x05FF),   # Hebrew
    (0x0600, 0x06FF),   # Arabic
    (0x0700, 0x074F),   # Syriac
    (0x0750, 0x077F),   # Arabic Supplement
    (0x08A0, 0x08FF),   # Arabic Extended-A
    (0xFB1D, 0xFDFF),   # Hebrew / Arabic presentation forms-A
    (0xFE70, 0xFEFF),   # Arabic presentation forms-B
)


def _is_rtl(text: str) -> bool:
    """True when *text* contains any strong RTL (Arabic/Hebrew block) char.

    Used to flip individual line labels to ``RightToLeft`` layout direction
    so Persian rows align and elide on the correct side.
    """
    for ch in str(text or ""):
        cp = ord(ch)
        for lo, hi in _RTL_RANGES:
            if lo <= cp <= hi:
                return True
    return False


def _stamp_ms(mm: str, ss: str, frac: str | None) -> int:
    """Convert regex groups (minutes, seconds, fractional) to milliseconds."""
    return int(mm) * 60_000 + int(ss) * 1_000 + int((frac or "0").ljust(3, "0")[:3])


def _format_stamp(ms: int) -> str:
    """Format milliseconds as an LRC ``[mm:ss.mmm]`` stamp."""
    total = max(0, int(ms))
    mm, rest = divmod(total, 60_000)
    ss, mmm = divmod(rest, 1_000)
    return f"[{mm:02d}:{ss:02d}.{mmm:03d}]"


def parse_lrc_local(text: str) -> list[tuple[int | None, str]]:
    """Minimal LRC parser used by the editor (multi-timestamp aware).

    ``[mm:ss.mmm]``, ``[mm:ss]`` and ``[mm:ss.mmm][mm:ss.mmm]text`` are all
    accepted; each stamp duplicates the line text. Untimed lines come back
    as ``(None, text)``.

    Raises ``ValueError`` on malformed timestamps — bracketed tokens that
    look like stamps but do not parse (``[05:XX]``) or carry seconds >= 60
    (``[00:99]``). Metadata tags such as ``[ti:...]`` are left alone.
    """
    out: list[tuple[int | None, str]] = []
    for raw in (text or "").splitlines():
        for m in _STAMPISH_RE.finditer(raw):
            if _LRC_TS_RE.fullmatch(m.group(0)) is None:
                raise ValueError(
                    f"invalid timestamp {m.group(0)!r} in line {raw.strip()!r}")
        stamps = list(_LRC_TS_RE.finditer(raw))
        content = _LRC_TS_RE.sub("", raw).strip()
        for m in stamps:
            if int(m.group(2)) > 59:
                raise ValueError(
                    f"seconds out of range in {m.group(0)!r} "
                    f"in line {raw.strip()!r}")
        if not stamps:
            if content:
                out.append((None, content))
            continue
        if not content:
            continue
        for m in stamps:
            out.append((_stamp_ms(m.group(1), m.group(2), m.group(3)), content))
    return out


def _normalize_lines(lines) -> list[tuple[int | None, str]]:
    """Coerce arbitrary input into ``[(ms|None, text)]`` without crashing."""
    out: list[tuple[int | None, str]] = []
    for item in lines or []:
        try:
            ms, text = item[0], str(item[1])
        except (TypeError, IndexError, KeyError, ValueError):
            continue
        if not text.strip():
            continue
        if ms is None:
            out.append((None, text.rstrip()))
            continue
        try:
            out.append((max(0, int(ms)), text.rstrip()))
        except (TypeError, ValueError):
            out.append((None, text.rstrip()))
    return out


def _normalize_translations(
        lines: list[tuple[int | None, str]],
        translations) -> list[tuple[int | None, str, str | None]]:
    """Accept either merged triplets (index-aligned with *lines*) or raw
    translation pairs ``[(ms, text)]`` (merged via ``merge_translations``)."""
    if not translations:
        return []
    try:
        first = tuple(translations[0])
    except (TypeError, IndexError, KeyError, ValueError):
        return []
    if len(first) >= 3:
        triples = []
        for item in translations:
            try:
                ms, _orig, tr = item[0], item[1], item[2]
            except (TypeError, IndexError, KeyError, ValueError):
                continue
            triples.append((ms, str(_orig), tr))
        if len(triples) == len(lines):
            return [(ms, txt, tr) for (ms, _o, tr), (_lms, txt)
                    in zip(triples, lines)]
        pool = {ms: tr for ms, _o, tr in triples if ms is not None}
        return [(ms, txt, pool.get(ms)) for ms, txt in lines]
    return merge_translations(lines, list(translations))


def _apply_layout_direction(label: QLabel, text: str,
                             centered: bool = False) -> None:
    """Flip a label to ``RightToLeft`` when its text is strong-RTL.

    Alignment follows the direction (left/right edge) unless *centered* is
    requested (floating / mini bars keep the text centered).
    """
    rtl = _is_rtl(text)
    label.setLayoutDirection(
        Qt.LayoutDirection.RightToLeft if rtl
        else Qt.LayoutDirection.LeftToRight)
    if centered:
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    elif rtl:
        label.setAlignment(Qt.AlignmentFlag.AlignRight
                           | Qt.AlignmentFlag.AlignVCenter)
    else:
        label.setAlignment(Qt.AlignmentFlag.AlignLeft
                           | Qt.AlignmentFlag.AlignVCenter)


def _default_theme(theme) -> dict:
    """Copy the theme dict (falling back to the brand default)."""
    if isinstance(theme, dict) and theme:
        return dict(theme)
    return dict(Theme.get(Theme.DEFAULT_NAME))


# ---------------------------------------------------------------------------
# Shared button roles
# ---------------------------------------------------------------------------

class GhostBtn(QPushButton):
    """Transparent outline button (the ``#GhostBtn`` QSS role).

    Secondary actions: "Find better", "Keep anyway", "Cancel" ...
    Colors come entirely from the ``build_qss_v3`` cascade applied by the
    owning component, so the button is theme-reactive for free.
    """

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setObjectName("GhostBtn")
        self._theme: dict | None = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_theme(self, theme: dict) -> None:
        """Store the active theme (styling is QSS-driven)."""
        self._theme = dict(theme) if theme else None


class GoldBtn(QPushButton):
    """Solid gold primary button (the ``#GoldBtn`` QSS role)."""

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setObjectName("GoldBtn")
        self._theme: dict | None = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_theme(self, theme: dict) -> None:
        """Store the active theme (styling is QSS-driven)."""
        self._theme = dict(theme) if theme else None


class _IconCircleButton(QPushButton):
    """22 px circular icon button with a hover circle (floating controls).

    A deliberately tiny local button so this module does not pull the
    heavier ``gm.ui.widgets`` import chain; behaviour matches IconButton.
    """

    def __init__(self, icon_name: str, tooltip: str = "",
                 size: int = 22, parent: QWidget | None = None):
        super().__init__(parent)
        self._icon_name = icon_name
        self._size = size
        self._theme: dict | None = None
        self._hover = False
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        self.setStyleSheet("background: transparent; border: none;")

    def set_theme(self, theme: dict) -> None:
        self._theme = dict(theme) if theme else None
        self._refresh_icon()

    def set_icon_name(self, name: str) -> None:
        """Swap the glyph (e.g. PIN <-> PIN_OFF) without rebuilding."""
        if name != self._icon_name:
            self._icon_name = name
            self._refresh_icon()

    def _refresh_icon(self) -> None:
        color = (self._theme or {}).get("muted", "#ab9776")
        if self._hover:
            color = (self._theme or {}).get("gold", "#e9b957")
        pm = render_icon(self._icon_name, max(10, self._size - 8),
                         color, get_dpr())
        self.setIcon(QIcon(pm))
        self.setIconSize(pm.size())

    def enterEvent(self, e) -> None:
        self._hover = True
        self._refresh_icon()
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        self._hover = False
        self._refresh_icon()
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._hover:
            rect = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
            p.setPen(QPen(QColor(255, 255, 255, 34), 1))
            p.setBrush(QBrush(QColor(255, 255, 255, 26)))
            p.drawEllipse(rect)
        pm = self.icon().pixmap(self.iconSize())
        if not pm.isNull():
            dpr = pm.devicePixelRatio() or 1.0
            lw, lh = int(pm.width() / dpr), int(pm.height() / dpr)
            p.drawPixmap((self.width() - lw) // 2, (self.height() - lh) // 2, pm)
        p.end()


# ---------------------------------------------------------------------------
# 1. LyricsView
# ---------------------------------------------------------------------------

class _LyricLineLabel(QLabel):
    """One lyrics line label; reports clicks back to its :class:`LyricsView`."""

    def __init__(self, text: str, index: int, ms: int | None,
                 synced: bool, parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setObjectName("LyricLine")
        self.setProperty("line_index", index)
        self.setProperty("line_ms", ms)
        self.setProperty("active", False)
        self.setWordWrap(True)
        self._view: "LyricsView | None" = None
        if synced and ms is not None:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip("Click to seek to this line")

    def mousePressEvent(self, e: QMouseEvent | None) -> None:
        """A click (or the first press of a double-click) seeks to the line."""
        if (e is not None and e.button() == Qt.MouseButton.LeftButton
                and self._view is not None):
            self._view._line_clicked(int(self.property("line_index") or 0))
        super().mousePressEvent(e)


class LyricsView(QWidget):
    """Scrollable, bilingual lyrics pane with karaoke-style highlighting.

    * Synced lyrics: the active line is bold + gold + larger; the next two
      lines are normal; everything else (past and far future) is muted.
      Smooth scrolling follows the active line (a ``QPropertyAnimation``
      on the scrollbar value; skipped entirely when animations are reduced).
    * Plain lyrics: no highlight — a calm reading view.
    * Bilingual: when ``translations`` are supplied each original line is
      followed by a muted, slightly smaller translation row; Persian/Arabic
      rows get a per-label ``RightToLeft`` layout direction.
    * Clicking a synced line emits :attr:`line_activated` with the line's
      millisecond position (double-click works too — the press that starts
      it already seeks).

    Offsets are applied by the caller — always pass already-shifted lines.
    """

    line_activated = pyqtSignal(int)
    translation_toggled = pyqtSignal(bool)

    FONT_SCALE_MIN = 0.8
    FONT_SCALE_MAX = 1.6

    def __init__(self, theme: dict | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("LyricsView")
        self.theme = _default_theme(theme)
        self._lines: list[tuple[int | None, str]] = []
        self._translations: list[tuple[int | None, str, str | None]] = []
        self._source = ""
        self._synced = False
        self._active_index = -1
        self._labels: list[_LyricLineLabel] = []
        self._translation_labels: list[QLabel] = []
        self._timed: list[tuple[int, int]] = []          # (ms, label index)
        self._show_translations = True
        self._font_scale = 1.0
        self._scroll_anim: QPropertyAnimation | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        # ---- header: source + translation toggle -------------------------
        self._header = QWidget()
        self._header.setObjectName("LyricsHeader")
        hrow = QHBoxLayout(self._header)
        hrow.setContentsMargins(10, 2, 10, 0)
        hrow.setSpacing(8)
        self._source_label = QLabel("")
        self._source_label.setObjectName("LyricsSource")
        hrow.addWidget(self._source_label)
        hrow.addStretch(1)
        self._toggle_btn = QToolButton()
        self._toggle_btn.setObjectName("LyricsTranslateToggle")
        self._toggle_btn.setCheckable(True)
        self._toggle_btn.setChecked(True)
        self._toggle_btn.setToolTip("Show / hide translations")
        self._toggle_btn.toggled.connect(self._on_toggle_translations)
        hrow.addWidget(self._toggle_btn)
        root.addWidget(self._header)

        # ---- scrolling lyrics body ---------------------------------------
        self._scroll = QScrollArea()
        self._scroll.setObjectName("LyricsScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._container: QWidget | None = None
        self._placeholder = QLabel("No lyrics")
        self._placeholder.setObjectName("LyricsEmpty")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._scroll, 1)

        self._rebuild()
        self.set_theme(self.theme)

    # ------------------------------------------------------------------ API
    def set_lines(self,
                  lines: list[tuple[int | None, str]],
                  translations: list[tuple[int | None, str, str | None]] | None = None,
                  source: str = "") -> None:
        """Replace the lyrics (``[(ms|None, text)]``).

        ``translations`` may be merged triplets aligned with *lines* or raw
        ``[(ms, translation_text)]`` pairs (merged here via
        ``gm.lyrics_translate.merge_translations``).
        """
        self._lines = _normalize_lines(lines)
        self._translations = _normalize_translations(self._lines, translations)
        self._source = str(source or "")
        self._synced = any(ms is not None for ms, _t in self._lines)
        self._active_index = -1
        self._timed = sorted(
            ((int(ms), i) for i, (ms, _t) in enumerate(self._lines)
             if ms is not None),
            key=lambda p: p[0])
        self._rebuild()
        self._update_header()
        if not _reduce_animations():
            try:
                fade_in(self._container)  # type: ignore[misc]
            except Exception:  # noqa: BLE001 - animation must never crash
                pass

    def sync_position(self, ms: int) -> None:
        """Highlight the line active at *ms* (no-op for plain lyrics)."""
        if not self._timed:
            return
        pos = max(0, int(ms))
        idx = bisect.bisect_right([t for t, _i in self._timed], pos) - 1
        label_index = self._timed[idx][1] if idx >= 0 else -1
        if label_index == self._active_index:
            return
        self._active_index = label_index
        self._apply_line_styles()
        self._scroll_to_active()

    def set_theme(self, theme: dict) -> None:
        """Apply a new theme dict (re-styles everything)."""
        self.theme = _default_theme(theme)
        self.setStyleSheet(build_qss_v3(self.theme) + self._local_qss())
        self._update_header()
        self._apply_line_styles()

    def clear(self) -> None:
        """Remove all lyrics and show the empty placeholder."""
        self._lines = []
        self._translations = []
        self._source = ""
        self._synced = False
        self._active_index = -1
        self._timed = []
        self._rebuild()
        self._update_header()

    @property
    def font_scale(self) -> float:
        """Line font scale factor (0.8–1.6)."""
        return self._font_scale

    @font_scale.setter
    def font_scale(self, value: float) -> None:
        self._font_scale = self._clamp_scale(value)
        self._apply_line_styles()

    def set_font_scale(self, value: float) -> None:
        """Set the line font scale factor (0.8–1.6)."""
        self.font_scale = value

    @staticmethod
    def _clamp_scale(value) -> float:
        try:
            v = float(value)
        except (TypeError, ValueError):
            v = 1.0
        return max(LyricsView.FONT_SCALE_MIN,
                   min(LyricsView.FONT_SCALE_MAX, v))

    # ------------------------------------------------------------ internals
    def _local_qss(self) -> str:
        t = self.theme
        return f"""
        QWidget#LyricsView {{ background: transparent; }}
        QScrollArea#LyricsScroll {{ background: transparent; border: none; }}
        QWidget#LyricsContainer {{ background: transparent; }}
        QLabel#LyricsSource {{
            color: {t['muted']}; font-size: 11px; font-weight: 700;
            background: transparent; border: none; letter-spacing: 1px;
        }}
        QLabel#LyricsEmpty {{
            color: {t['muted']}; font-size: 13px;
            background: transparent; border: none; padding: 24px;
        }}
        QToolButton#LyricsTranslateToggle {{
            background: transparent; border: none; padding: 3px;
        }}
        QToolButton#LyricsTranslateToggle:hover {{
            background: {t['active']}; border-radius: 7px;
        }}
        """

    def _rebuild(self) -> None:
        """Recreate the line labels inside the scroll container."""
        self._labels = []
        self._translation_labels = []
        # detach the placeholder before the old container (its parent)
        # is deleted, otherwise it would go down with it
        if self._placeholder.parent() is not None:
            self._placeholder.setParent(None)
        self._container = QWidget()
        self._container.setObjectName("LyricsContainer")
        vbox = QVBoxLayout(self._container)
        vbox.setContentsMargins(14, 20, 14, 24)
        vbox.setSpacing(4)

        if not self._lines:
            self._placeholder.setParent(self._container)
            vbox.addWidget(self._placeholder)
        else:
            for i, (ms, text) in enumerate(self._lines):
                lbl = _LyricLineLabel(text, i, ms, self._synced)
                lbl._view = self
                _apply_layout_direction(lbl, text)
                self._labels.append(lbl)
                vbox.addWidget(lbl)
                trans = self._translations[i][2] if i < len(self._translations) else None
                if trans:
                    tl = QLabel(str(trans))
                    tl.setObjectName("LyricTranslation")
                    tl.setProperty("line_index", i)
                    tl.setWordWrap(True)
                    _apply_layout_direction(tl, str(trans))
                    self._translation_labels.append(tl)
                    vbox.addWidget(tl)
            vbox.addStretch(1)

        self._scroll.setWidget(self._container)
        # NOTE: QScrollArea.setWidget() already deletes the previous
        # container (and with it the old line labels) — no manual
        # deleteLater, that would double-free.
        self._apply_line_styles()

    def _update_header(self) -> None:
        self._source_label.setText(self._source.upper() if self._source else "")
        has_trans = bool(self._translation_labels)
        self._toggle_btn.setVisible(has_trans)
        self._toggle_btn.setChecked(self._show_translations)
        icon_color = (self.theme["gold_light"] if self._show_translations
                      and has_trans else self.theme["muted"])
        self._toggle_btn.setIcon(QIcon(render_icon(
            Icon.LANGUAGES, 16, icon_color, get_dpr())))
        self._header.setVisible(bool(self._source) or has_trans)

    def _apply_line_styles(self) -> None:
        """Re-style every line label for the current active index."""
        t = self.theme
        base = round(15 * self._font_scale)
        big = round(19 * self._font_scale)
        trans_px = round(12.5 * self._font_scale)
        active_ss = (f"background: transparent; border: none; padding: 2px 6px;"
                     f" font-size: {big}px; font-weight: 700; color: {t['gold']};")
        near_ss = (f"background: transparent; border: none; padding: 2px 6px;"
                   f" font-size: {base}px; font-weight: 400; color: {t['text']};")
        muted_ss = (f"background: transparent; border: none; padding: 2px 6px;"
                    f" font-size: {base}px; font-weight: 400; color: {t['muted']};")
        trans_ss = (f"background: transparent; border: none; padding: 0 8px 4px 6px;"
                    f" font-size: {trans_px}px; color: {t['muted']};")

        a = self._active_index if self._synced else -1
        for i, lbl in enumerate(self._labels):
            role = "muted"
            if i == a:
                role = "active"
            elif a < 0 and i < 3:
                role = "near"          # nothing active yet: lead-in lines
            elif a < i <= a + 2:
                role = "near"
            lbl.setProperty("active", i == a)
            lbl.setProperty("role", role)
            lbl.setStyleSheet(
                active_ss if role == "active"
                else near_ss if role == "near" else muted_ss)
        for tl in self._translation_labels:
            tl.setStyleSheet(trans_ss)

    def _scroll_to_active(self) -> None:
        """Smoothly center the active line (or jump when motion is reduced)."""
        if not self.isVisible() or self._active_index < 0:
            return
        lbl = self._labels[self._active_index]
        viewport = self._scroll.viewport()
        target = (lbl.y() + lbl.height() // 2
                  - viewport.height() // 2)
        bar = self._scroll.verticalScrollBar()
        target = max(bar.minimum(), min(bar.maximum(), target))
        if _reduce_animations():
            bar.setValue(target)
            return
        if self._scroll_anim is None:
            self._scroll_anim = QPropertyAnimation(
                bar, b"value", self)
            self._scroll_anim.setEasingCurve(
                QEasingCurve.Type.OutCubic)
        else:
            self._scroll_anim.stop()
        self._scroll_anim.setDuration(320)
        self._scroll_anim.setStartValue(bar.value())
        self._scroll_anim.setEndValue(target)
        self._scroll_anim.start()

    def _line_clicked(self, index: int) -> None:
        """A line label was clicked — seek if the line is timed."""
        if not (0 <= index < len(self._lines)):
            return
        ms = self._lines[index][0]
        if self._synced and ms is not None:
            self.line_activated.emit(int(ms))

    def _on_toggle_translations(self, checked: bool) -> None:
        self._show_translations = bool(checked)
        for tl in self._translation_labels:
            tl.setVisible(self._show_translations)
        self._update_header()
        self.translation_toggled.emit(self._show_translations)


# ---------------------------------------------------------------------------
# 2. LyricsQualityBar
# ---------------------------------------------------------------------------

_REASON_TEXT: dict[str, str] = {
    "empty": "the lyrics are empty",
    "too_short": "too short",
    "spam_url": "they contain links",
    "spam_words": "they look promotional",
    "spam_contact": "they contain contact details",
    "spam_shouting": "they are shouted in ALL CAPS",
    "spam_repeated": "the same lines repeat",
    "instrumental": "the track is instrumental",
    "ok": "they look fine",
    "error": "they could not be checked",
}


class LyricsQualityBar(QWidget):
    """Slim warning banner for questionable lyrics.

    Two modes, driven by :meth:`show_warning`:

    * no candidate — "These lyrics look wrong (too short / spam)" plus the
      reason from ``gm.lyrics_quality.verdict`` and two ghost buttons
      "Find better" (:attr:`search_requested`) / "Keep anyway"
      (:attr:`keep_requested`);
    * with a candidate summary — "Better lyrics found: <summary>" and
      "Replace" (:attr:`replace_requested`) / "Keep current"
      (:attr:`keep_requested`).

    The bar hides itself after any choice.
    """

    search_requested = pyqtSignal()
    keep_requested = pyqtSignal()
    replace_requested = pyqtSignal()

    def __init__(self, theme: dict | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("LyricsQualityBar")
        self.theme = _default_theme(theme)
        self._candidate_mode = False

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(10)

        self._icon_label = QLabel()
        self._icon_label.setObjectName("QualityIcon")
        self._icon_label.setFixedSize(22, 22)
        row.addWidget(self._icon_label)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(1)
        self._title_label = QLabel("These lyrics look wrong")
        self._title_label.setObjectName("QualityTitle")
        text_col.addWidget(self._title_label)
        self._reason_label = QLabel("")
        self._reason_label.setObjectName("QualityReason")
        self._reason_label.setWordWrap(True)
        text_col.addWidget(self._reason_label)
        row.addLayout(text_col, 1)

        self._primary_btn = GhostBtn("Find better")
        self._primary_btn.setObjectName("QualityPrimaryBtn")
        self._primary_btn.clicked.connect(self._on_primary)
        row.addWidget(self._primary_btn)

        self._secondary_btn = GhostBtn("Keep anyway")
        self._secondary_btn.setObjectName("QualitySecondaryBtn")
        self._secondary_btn.clicked.connect(self._on_secondary)
        row.addWidget(self._secondary_btn)

        self.hide()
        self.set_theme(self.theme)

    # ------------------------------------------------------------------ API
    def show_warning(self, existing_text: str,
                     candidate_summary: str | None = None) -> None:
        """Show the banner for *existing_text* (classified via ``verdict``).

        When *candidate_summary* is given the banner switches to the
        "better lyrics found" replace/keep form.
        """
        v = verdict(existing_text)
        reason_code = str(v.get("reason", ""))
        friendly = _REASON_TEXT.get(reason_code, reason_code or "unknown")
        self._candidate_mode = bool(candidate_summary)
        if self._candidate_mode:
            self._title_label.setText("Better lyrics found")
            self._reason_label.setText(str(candidate_summary))
            self._primary_btn.setText("Replace")
            self._secondary_btn.setText("Keep current")
        else:
            self._title_label.setText("These lyrics look wrong (too short / spam)")
            self._reason_label.setText(f"{friendly} — {reason_code}")
            self._primary_btn.setText("Find better")
            self._secondary_btn.setText("Keep anyway")
        self.show()
        self.raise_()

    def set_theme(self, theme: dict) -> None:
        """Apply a new theme dict."""
        self.theme = _default_theme(theme)
        self.setStyleSheet(self._local_qss())
        gold = self.theme["gold"]
        self._icon_label.setPixmap(render_icon(Icon.ALERT, 18, gold, get_dpr()))
        self._primary_btn.set_theme(self.theme)
        self._secondary_btn.set_theme(self.theme)

    def reason_code(self) -> str:
        """The raw verdict reason code of the last shown warning."""
        if self._candidate_mode:
            return ""
        return str(self._reason_label.text()).rsplit("— ", 1)[-1].strip()

    # ------------------------------------------------------------ internals
    def _local_qss(self) -> str:
        t = self.theme
        return f"""
        QWidget#LyricsQualityBar {{
            background: {t['panel_bg']};
            border: 1px solid {t['gold']};
            border-radius: 9px;
        }}
        QLabel#QualityIcon {{ background: transparent; border: none; }}
        QLabel#QualityTitle {{
            color: {t['text']}; font-size: 12.5px; font-weight: 600;
            background: transparent; border: none;
        }}
        QLabel#QualityReason {{
            color: {t['muted']}; font-size: 11.5px;
            background: transparent; border: none;
        }}
        """

    def _on_primary(self) -> None:
        if self._candidate_mode:
            self.replace_requested.emit()
        else:
            self.search_requested.emit()
        self.hide()

    def _on_secondary(self) -> None:
        self.keep_requested.emit()
        self.hide()


# ---------------------------------------------------------------------------
# 3. LyricsEditorDialog
# ---------------------------------------------------------------------------

class LyricsEditorDialog(QDialog):
    """LRC / plain-text lyrics editor.

    * Toolbar — synced/plain chip, offset ``QSpinBox`` (-10000..10000, step
      100, " ms") with a live preview note, "Shift timings" (applies the
      offset to every synced line IMMEDIATELY via
      ``gm.lyrics_io.apply_offset``) and "Insert timestamp" (drops a
      ``[mm:ss.mmm]`` stamp at the cursor — synced mode only).
    * Center — the lyrics text (serialized with ``to_lrc_text`` when synced).
    * Bottom — save-mode combo ("embed" / "sidecar" / "none"), Cancel and
      a gold OK.

    On OK the text is parsed back with :func:`parse_lrc_local`; malformed
    timestamps produce a ``QMessageBox`` warning and the dialog stays open.

    ``offset_ms()`` returns the offset APPLIED via "Shift timings" (the
    spin value itself is only staged until then); ``result_lines()``
    returns exactly what the edited text parses to.
    """

    SAVE_MODES = ("embed", "sidecar", "none")
    SAVE_MODE_LABELS = ("Save to file tag", "Save .lrc sidecar",
                        "Just save in app")

    def __init__(self, lines, synced: bool, theme: dict | None = None,
                 title: str = "Edit lyrics", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("LyricsEditor")
        self.theme = _default_theme(theme)
        self._synced = bool(synced)
        self._lines = _normalize_lines(lines)
        self._offset_applied = 0
        self._result_lines: list[tuple[int | None, str]] | None = None

        self.setWindowTitle(str(title or "Edit lyrics"))
        self.resize(580, 500)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        # ---- toolbar ------------------------------------------------------
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self._chip = QFrame()
        self._chip.setObjectName("SyncChip")
        self._chip.setProperty("synced", self._synced)
        chip_row = QHBoxLayout(self._chip)
        chip_row.setContentsMargins(10, 3, 10, 3)
        chip_row.setSpacing(6)
        self._chip_icon = QLabel()
        self._chip_icon.setFixedSize(14, 14)
        chip_row.addWidget(self._chip_icon)
        self._chip_text = QLabel("Synced · LRC" if self._synced else "Plain text")
        chip_row.addWidget(self._chip_text)
        toolbar.addWidget(self._chip)

        toolbar.addSpacing(6)
        self._offset_spin = QSpinBox()
        self._offset_spin.setObjectName("OffsetSpin")
        self._offset_spin.setRange(-10_000, 10_000)
        self._offset_spin.setSingleStep(100)
        self._offset_spin.setSuffix(" ms")
        self._offset_spin.setToolTip("Offset to shift all synced timings by")
        self._offset_spin.valueChanged.connect(self._on_offset_changed)
        toolbar.addWidget(self._offset_spin)

        self._note_label = QLabel("Shift 0 ms — click Shift timings to apply")
        self._note_label.setObjectName("OffsetNote")
        toolbar.addWidget(self._note_label, 1)

        self._shift_btn = GhostBtn("Shift timings")
        self._shift_btn.setToolTip("Apply the offset to every synced line now")
        self._shift_btn.clicked.connect(self._shift_timings)
        toolbar.addWidget(self._shift_btn)

        self._insert_btn = GhostBtn("Insert timestamp")
        self._insert_btn.setToolTip("Insert a [mm:ss.mmm] stamp at the cursor")
        self._insert_btn.setEnabled(self._synced)
        self._insert_btn.clicked.connect(self._insert_timestamp)
        toolbar.addWidget(self._insert_btn)
        root.addLayout(toolbar)

        # ---- editor body --------------------------------------------------
        self._editor = QPlainTextEdit()
        self._editor.setObjectName("LyricsEditorText")
        self._editor.setPlainText(to_lrc_text(self._lines, self._synced))
        self._editor.setTabChangesFocus(True)
        root.addWidget(self._editor, 1)

        # ---- bottom row ---------------------------------------------------
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self._save_combo = QComboBox()
        self._save_combo.setObjectName("SaveModeCombo")
        for label in self.SAVE_MODE_LABELS:
            self._save_combo.addItem(label)
        self._save_combo.setToolTip("Where the edited lyrics will be saved")
        bottom.addWidget(self._save_combo)
        bottom.addStretch(1)

        cancel_btn = GhostBtn("Cancel")
        cancel_btn.clicked.connect(self.reject)
        bottom.addWidget(cancel_btn)

        self._ok_btn = GoldBtn("OK")
        self._ok_btn.clicked.connect(self._on_ok)
        bottom.addWidget(self._ok_btn)
        root.addLayout(bottom)

        self.set_theme(self.theme)

    # ------------------------------------------------------------------ API
    def result_lines(self) -> list[tuple[int | None, str]]:
        """The edited lyrics as ``[(ms|None, text)]``.

        Returns the lines stored at the last successful OK; if the dialog
        was never accepted it parses the current text (falling back to the
        original lines when the text is invalid).
        """
        if self._result_lines is not None:
            return list(self._result_lines)
        try:
            return parse_lrc_local(self._editor.toPlainText()) \
                if self._synced else self._plain_lines()
        except ValueError:
            return list(self._lines)

    def offset_ms(self) -> int:
        """Total offset applied via "Shift timings" (ms)."""
        return int(self._offset_applied)

    def save_mode(self) -> str:
        """The chosen persistence mode: ``"embed"``, ``"sidecar"`` or
        ``"none"`` (just keep them in app memory)."""
        idx = self._save_combo.currentIndex()
        return self.SAVE_MODES[idx] if 0 <= idx < len(self.SAVE_MODES) \
            else self.SAVE_MODES[0]

    def set_theme(self, theme: dict) -> None:
        """Apply a new theme dict (re-styles the dialog)."""
        self.theme = _default_theme(theme)
        self.setStyleSheet(build_qss_v3(self.theme) + self._local_qss())
        chip_color = self.theme["gold_light"] if self._synced \
            else self.theme["muted"]
        self._chip_icon.setPixmap(render_icon(
            Icon.TIMER if self._synced else Icon.LIST_LINES,
            14, chip_color, get_dpr()))
        self._chip_text.setStyleSheet(
            f"color: {chip_color}; font-weight: 700; font-size: 11px;"
            f" background: transparent; border: none;")
        self._note_label.setStyleSheet(
            f"color: {self.theme['muted']}; font-size: 11.5px;"
            f" background: transparent; border: none;")

    # ------------------------------------------------------------ internals
    def _local_qss(self) -> str:
        t = self.theme
        return f"""
        QFrame#SyncChip {{
            background: {t['active']};
            border: 1px solid {t['border']};
            border-radius: 9px;
        }}
        QPlainTextEdit#LyricsEditorText {{
            background: {t['panel_bg']};
            border: 1px solid {t['border']};
            border-radius: 10px;
            padding: 10px;
            color: {t['text']};
            selection-background-color: {t['active']};
            selection-color: {t['text']};
        }}
        QPlainTextEdit#LyricsEditorText:focus {{ border-color: {t['gold']}; }}
        QSpinBox#OffsetSpin {{
            background: {t['panel_bg_2']};
            border: 1px solid {t['border']};
            border-radius: 8px;
            padding: 4px 8px;
            color: {t['text']};
        }}
        QSpinBox#OffsetSpin:focus {{ border-color: {t['gold']}; }}
        QSpinBox#OffsetSpin::up-button, QSpinBox#OffsetSpin::down-button {{
            width: 16px; border: none; background: transparent;
        }}
        QSpinBox#OffsetSpin::up-arrow, QSpinBox#OffsetSpin::down-arrow {{
            width: 5px; height: 5px; background: {t['muted']};
        }}
        """

    def _plain_lines(self) -> list[tuple[int | None, str]]:
        return [(None, ln.strip()) for ln in self._editor.toPlainText().splitlines()
                if ln.strip()]

    def _on_offset_changed(self, value: int) -> None:
        self._note_label.setText(
            f"Shift {int(value):+d} ms — click Shift timings to apply")

    def _shift_timings(self) -> None:
        """Apply the staged offset to all synced lines immediately."""
        value = int(self._offset_spin.value())
        if value == 0:
            self._note_label.setText("Offset is 0 ms — nothing to shift")
            return
        self._lines = apply_offset(self._lines, value)
        self._offset_applied += value
        self._editor.setPlainText(to_lrc_text(self._lines, self._synced))
        total = self._offset_applied
        self._offset_spin.blockSignals(True)
        self._offset_spin.setValue(0)          # applied — restage from zero
        self._offset_spin.blockSignals(False)
        self._note_label.setText(
            f"Applied {value:+d} ms (total {total:+d} ms)")

    def _insert_timestamp(self) -> None:
        """Insert ``[mm:ss.mmm]`` at the cursor (synced mode only).

        The stamp continues from the last timestamp before the cursor
        (plus 5 s) so repeated inserts walk forward through the song.
        """
        if not self._synced:
            return
        cursor = self._editor.textCursor()
        before = self._editor.toPlainText()[:cursor.position()]
        last_ms = 0
        for m in _LRC_TS_RE.finditer(before):
            last_ms = _stamp_ms(m.group(1), m.group(2), m.group(3))
        stamp = _format_stamp(last_ms + 5_000)
        cursor.insertText(stamp)
        self._editor.setTextCursor(cursor)

    def _validate(self) -> bool:
        """Parse the edited text; warn (QMessageBox) and refuse on errors."""
        text = self._editor.toPlainText()
        if not self._synced:
            return True
        try:
            parse_lrc_local(text)
            return True
        except ValueError as e:
            QMessageBox.warning(
                self, "Invalid lyrics",
                f"The lyrics contain invalid timestamps:\n{e}\n\n"
                f"Fix them (or remove the brackets) and try again.")
            return False

    def _on_ok(self) -> None:
        if not self._validate():
            return
        self._result_lines = (parse_lrc_local(self._editor.toPlainText())
                              if self._synced else self._plain_lines())
        self.accept()


# ---------------------------------------------------------------------------
# 4. FloatingLyricsWidget
# ---------------------------------------------------------------------------

class FloatingLyricsWidget(QWidget):
    """NetEase-style desktop floating lyrics (two lines, always on top).

    * Current line — big, bold, gold. Next line (or the current line's
      translation, when one exists) — smaller, muted. Line changes
      cross-fade via a ``QVariantAnimation`` driving both labels'
      opacity effects (skipped when animations are reduced).
    * RTL rows flip per-label layout direction.
    * Dragging moves the window (when unlocked); :attr:`position_changed`
      fires on drag end so the app can persist the position — position
      persistence is the app's job.
    * A tiny control row (pin / click-through / close) appears on hover
      while unlocked; 22 px circular icon buttons.

    Locking semantics: ``lock(True)`` sets ``WA_TransparentForMouseEvents``
    plus 0.85 window opacity and shows a PIN_OFF corner chip. NOTE: once
    locked (or click-through) the window no longer receives mouse events,
    so the double-click :attr:`unlock_requested` gesture only works while
    the widget is interactive — in production the unlock action comes from
    the main app UI (menu / tray), which is expected behaviour.
    """

    seek_requested = pyqtSignal(int)
    unlock_requested = pyqtSignal()
    close_requested = pyqtSignal()
    position_changed = pyqtSignal(QPoint)

    FONT_SCALE_MIN = 0.8
    FONT_SCALE_MAX = 1.6

    def __init__(self, theme: dict | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("FloatingLyrics")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.resize(780, 86)

        self.theme = _default_theme(theme)
        self._lines: list[tuple[int | None, str]] = []
        self._translations: list[tuple[int | None, str, str | None]] = []
        self._timed: list[tuple[int, int]] = []
        self._current_index = -2        # -2 = "never synced" so the first
        self._current_ms: int | None = None   # sync before line 1 still paints
        self._locked = False
        self._click_through = False
        self._font_scale = 1.0
        self._drag_offset: QPoint | None = None
        self._drag_moved = False

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 8, 16, 8)
        root.setSpacing(2)
        root.addStretch(1)
        self._cur_label = QLabel("")
        self._cur_label.setObjectName("FloatCurrent")
        self._cur_label.setWordWrap(False)
        self._cur_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._cur_label)
        self._next_label = QLabel("")
        self._next_label.setObjectName("FloatNext")
        self._next_label.setWordWrap(False)
        self._next_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._next_label)
        root.addStretch(1)

        # hover control row (unlocked only)
        self._controls = QWidget()
        self._controls.setObjectName("FloatControls")
        crow = QHBoxLayout(self._controls)
        crow.setContentsMargins(0, 0, 0, 0)
        crow.setSpacing(4)
        self._pin_btn = _IconCircleButton(Icon.PIN, "Lock position")
        self._pin_btn.clicked.connect(lambda: self.lock(not self._locked))
        crow.addWidget(self._pin_btn)
        self._eye_btn = _IconCircleButton(Icon.EYE, "Toggle click-through")
        self._eye_btn.clicked.connect(
            lambda: self.set_click_through(not self._click_through))
        crow.addWidget(self._eye_btn)
        self._close_btn = _IconCircleButton(Icon.CLOSE, "Close floating lyrics")
        self._close_btn.clicked.connect(self.close_requested.emit)
        crow.addWidget(self._close_btn)
        root.addWidget(self._controls, 0, Qt.AlignmentFlag.AlignCenter)
        self._controls.setVisible(False)

        # locked corner chip (absolute position, moved in resizeEvent)
        self._lock_chip = QLabel()
        self._lock_chip.setObjectName("FloatLockChip")
        self._lock_chip.setFixedSize(22, 22)
        self._lock_chip.setToolTip("Locked — unlock from the main window")
        self._lock_chip.setVisible(False)
        self._lock_chip.setParent(self)

        # cross-fade machinery
        self._cur_effect = QGraphicsOpacityEffect(self._cur_label)
        self._cur_label.setGraphicsEffect(self._cur_effect)
        self._next_effect = QGraphicsOpacityEffect(self._next_label)
        self._next_label.setGraphicsEffect(self._next_effect)
        self._fade_anim = QVariantAnimation(self)
        self._fade_anim.setDuration(240)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade_anim.valueChanged.connect(self._on_fade_value)

        self.set_theme(self.theme)

    # ------------------------------------------------------------------ API
    def set_lines(self, lines: list[tuple[int | None, str]],
                  translations: list[tuple[int | None, str, str | None]] | None = None
                  ) -> None:
        """Replace the lyrics (merged triplets or raw translation pairs)."""
        self._lines = _normalize_lines(lines)
        self._translations = _normalize_translations(self._lines, translations)
        self._timed = sorted(
            ((int(ms), i) for i, (ms, _t) in enumerate(self._lines)
             if ms is not None),
            key=lambda p: p[0])
        self._current_index = -2
        self._current_ms = None
        if not self._timed and self._lines:
            # plain lyrics: static preview of the first two lines
            self._current_index = 0
            self._set_texts(self._lines[0][1],
                            self._lines[1][1] if len(self._lines) > 1 else "")
        else:
            self._set_texts("", "")

    def sync_position(self, ms: int) -> None:
        """Move the highlight to the line active at *ms*."""
        if not self._timed:
            return
        pos = max(0, int(ms))
        idx = bisect.bisect_right([t for t, _i in self._timed], pos) - 1
        index = self._timed[idx][1] if idx >= 0 else -1
        if index == self._current_index:
            return
        self._current_index = index
        self._current_ms = self._timed[idx][0] if idx >= 0 else None
        if index < 0:
            # before the first line: preview it in the "next" slot
            self._set_texts("", self._lines[self._timed[0][1]][1])
            return
        cur_text = self._lines[index][1]
        trans = (self._translations[index][2]
                 if index < len(self._translations) else None)
        next_text = ""
        if trans:
            next_text = str(trans)          # translation under the current line
        else:
            nxt = index + 1
            while nxt < len(self._lines) and not self._lines[nxt][1].strip():
                nxt += 1
            if nxt < len(self._lines):
                next_text = self._lines[nxt][1]
        self._set_texts(cur_text, next_text)

    def set_theme(self, theme: dict) -> None:
        """Apply a new theme dict."""
        self.theme = _default_theme(theme)
        self._apply_label_styles()
        for btn in (self._pin_btn, self._eye_btn, self._close_btn):
            btn.set_theme(self.theme)
        chip_color = self.theme["muted"]
        self._lock_chip.setPixmap(render_icon(
            Icon.PIN_OFF, 14, chip_color, get_dpr()))
        self.update()

    def lock(self, locked: bool) -> None:
        """Lock the widget in place (0.85 opacity, mouse-transparent,
        PIN_OFF corner chip, controls hidden)."""
        self._locked = bool(locked)
        self.setWindowOpacity(0.85 if self._locked else 1.0)
        self._update_mouse_transparency()
        self._lock_chip.setVisible(self._locked)
        if self._locked:
            self._controls.setVisible(False)
        self._pin_btn.set_icon_name(
            Icon.PIN_OFF if self._locked else Icon.PIN)
        self._pin_btn.setToolTip(
            "Locked — unlock from the main window" if self._locked
            else "Lock position")
        self.update()

    def set_click_through(self, enabled: bool) -> None:
        """Let mouse events fall through to whatever is below."""
        self._click_through = bool(enabled)
        self._update_mouse_transparency()
        self._eye_btn.set_icon_name(
            Icon.EYE_OFF if self._click_through else Icon.EYE)
        self._eye_btn.setToolTip(
            "Click-through is ON — disable from the main window"
            if self._click_through else "Toggle click-through")

    def set_font_scale(self, value: float) -> None:
        """Scale the two text labels (clamped to 0.8–1.6)."""
        try:
            v = float(value)
        except (TypeError, ValueError):
            v = 1.0
        self._font_scale = max(self.FONT_SCALE_MIN,
                               min(self.FONT_SCALE_MAX, v))
        self._apply_label_styles()

    @property
    def locked(self) -> bool:
        """Whether the floating widget is locked in place."""
        return self._locked

    # ------------------------------------------------------------ internals
    def _update_mouse_transparency(self) -> None:
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents,
                          self._locked or self._click_through)

    def _apply_label_styles(self) -> None:
        t = self.theme
        cur_px = round(22 * self._font_scale)
        next_px = round(14 * self._font_scale)
        self._cur_label.setStyleSheet(
            f"background: transparent; border: none; color: {t['gold']};"
            f" font-size: {cur_px}px; font-weight: 700;")
        self._next_label.setStyleSheet(
            f"background: transparent; border: none; color: {t['muted']};"
            f" font-size: {next_px}px; font-weight: 400;")

    def _set_texts(self, current: str, following: str) -> None:
        self._cur_label.setText(current)
        _apply_layout_direction(self._cur_label, current, centered=True)
        self._next_label.setText(following)
        _apply_layout_direction(self._next_label, following, centered=True)
        self._start_fade()

    def _start_fade(self) -> None:
        """Fade both labels in after a line change (or snap when reduced)."""
        if _reduce_animations() or not self.isVisible():
            self._cur_effect.setOpacity(1.0)
            self._next_effect.setOpacity(1.0)
            return
        self._fade_anim.stop()
        self._cur_effect.setOpacity(0.0)
        self._next_effect.setOpacity(0.0)
        self._fade_anim.start()

    def _on_fade_value(self, value) -> None:
        try:
            v = float(value)
        except (TypeError, ValueError):
            v = 1.0
        self._cur_effect.setOpacity(v)
        self._next_effect.setOpacity(v)

    # ---- window dragging / gestures -------------------------------------
    def mousePressEvent(self, e: QMouseEvent | None) -> None:
        if e is not None and e.button() == Qt.MouseButton.LeftButton \
                and not self._locked:
            self._drag_offset = (e.globalPosition().toPoint()
                                 - self.frameGeometry().topLeft())
            self._drag_moved = False
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QMouseEvent | None) -> None:
        if self._drag_offset is not None and e is not None:
            self.move(e.globalPosition().toPoint() - self._drag_offset)
            self._drag_moved = True
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e: QMouseEvent | None) -> None:
        if self._drag_offset is not None:
            if self._drag_moved:
                self.position_changed.emit(self.pos())
            elif self._current_ms is not None:
                # a plain click (no drag) on the lyric seeks to it
                self.seek_requested.emit(int(self._current_ms))
            self._drag_offset = None
            self._drag_moved = False
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e: QMouseEvent | None) -> None:
        """Double-click asks the app to unlock (works while interactive)."""
        self.unlock_requested.emit()
        super().mouseDoubleClickEvent(e)

    def enterEvent(self, e) -> None:
        if not self._locked:
            self._controls.setVisible(True)
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        self._controls.setVisible(False)
        super().leaveEvent(e)

    def resizeEvent(self, e) -> None:
        self._lock_chip.move(self.width() - self._lock_chip.width() - 6, 4)
        super().resizeEvent(e)

    def paintEvent(self, e) -> None:
        """Rounded translucent panel with a theme border (frameless)."""
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 16, 16)
        panel = QColor(self.theme.get("panel_bg", "#15110a"))
        panel.setAlpha(210 if self._locked else 235)
        p.fillPath(path, QBrush(panel))
        p.setPen(QPen(QColor(self.theme.get("border", "#372d1c")), 1))
        p.drawPath(path)
        p.end()


# ---------------------------------------------------------------------------
# 5. MiniLyricsBar
# ---------------------------------------------------------------------------

class MiniLyricsBar(QWidget):
    """Compact one-line lyrics bar for the taskbar area (360x40).

    Shows the current line (elided when long) over a rounded panel with a
    4 px album-color accent on the left. Double-click seeks to the current
    line; drag moves the window (:attr:`position_changed` fires on drag end
    so the app can persist it).
    """

    seek_requested = pyqtSignal(int)
    position_changed = pyqtSignal(QPoint)

    def __init__(self, theme: dict | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("MiniLyricsBar")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(360, 40)

        self.theme = _default_theme(theme)
        self._lines: list[tuple[int | None, str]] = []
        self._timed: list[tuple[int, int]] = []
        self._current_ms: int | None = None
        self._accent: str | None = None
        self._drag_offset: QPoint | None = None
        self._drag_moved = False

        root = QHBoxLayout(self)
        root.setContentsMargins(14, 4, 12, 4)
        self._label = QLabel("—")
        self._label.setObjectName("MiniLyricsLabel")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._label, 1)

        self.set_theme(self.theme)

    # ------------------------------------------------------------------ API
    def set_lines(self, lines: list[tuple[int | None, str]]) -> None:
        """Replace the lyrics (``[(ms|None, text)]``)."""
        self._lines = _normalize_lines(lines)
        self._timed = sorted(
            ((int(ms), i) for i, (ms, _t) in enumerate(self._lines)
             if ms is not None),
            key=lambda p: p[0])
        self._current_ms = None
        text = self._lines[0][1] if self._lines else "—"
        self._set_elided_text(text)

    def sync_position(self, ms: int) -> None:
        """Show the line active at *ms* (first line for plain lyrics)."""
        text = ""
        if self._timed:
            pos = max(0, int(ms))
            idx = bisect.bisect_right([t for t, _i in self._timed], pos) - 1
            if idx >= 0:
                self._current_ms = self._timed[idx][0]
                text = self._lines[self._timed[idx][1]][1]
            else:
                self._current_ms = None
                text = self._lines[self._timed[0][1]][1]
        elif self._lines:
            self._current_ms = None
            text = self._lines[0][1]
        self._set_elided_text(text or "—")

    def set_theme(self, theme: dict) -> None:
        """Apply a new theme dict."""
        self.theme = _default_theme(theme)
        self._label.setStyleSheet(
            f"background: transparent; border: none; color: {self.theme['text']};"
            f" font-size: 13px; font-weight: 600;")
        self.update()

    def set_accent_color(self, color: str | None) -> None:
        """Set the 4 px left accent color (album color); None = theme gold."""
        self._accent = str(color) if color else None
        self.update()

    # ------------------------------------------------------------ internals
    def _set_elided_text(self, text: str) -> None:
        """Center the (elided) line; RTL lines flip direction per label."""
        fm = QFontMetrics(self._label.font())
        width = max(60, self.width() - 28)
        elided = fm.elidedText(text, Qt.TextElideMode.ElideRight, width)
        self._label.setText(elided)
        _apply_layout_direction(self._label, text, centered=True)

    # ---- dragging / gestures --------------------------------------------
    def mousePressEvent(self, e: QMouseEvent | None) -> None:
        if e is not None and e.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = (e.globalPosition().toPoint()
                                 - self.frameGeometry().topLeft())
            self._drag_moved = False
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QMouseEvent | None) -> None:
        if self._drag_offset is not None and e is not None:
            self.move(e.globalPosition().toPoint() - self._drag_offset)
            self._drag_moved = True
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e: QMouseEvent | None) -> None:
        if self._drag_offset is not None:
            if self._drag_moved:
                self.position_changed.emit(self.pos())
            self._drag_offset = None
            self._drag_moved = False
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e: QMouseEvent | None) -> None:
        """Double-click seeks to the current line's position."""
        if self._current_ms is not None:
            self.seek_requested.emit(int(self._current_ms))
        super().mouseDoubleClickEvent(e)

    def paintEvent(self, e) -> None:
        """Rounded panel + 4 px accent strip on the left edge."""
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 10, 10)
        panel = QColor(self.theme.get("panel_bg", "#15110a"))
        panel.setAlpha(238)
        p.fillPath(path, QBrush(panel))
        accent = QColor(self._accent or self.theme.get("gold", "#e9b957"))
        strip = QPainterPath()
        strip.addRoundedRect(QRectF(0.5, 3.0, 4.0, self.height() - 6.0), 2, 2)
        p.fillPath(strip, QBrush(accent))
        p.setPen(QPen(QColor(self.theme.get("border", "#372d1c")), 1))
        p.drawPath(path)
        p.end()
