"""Golden Music 2.3 — library browsing pages (albums / artists / genres /
playlists).

New v3.0 pages built on the *provider contract*: every page takes the
duck-typed main window (``mw``) and only touches a small, fully
getattr-guarded surface of it:

    mw.theme            dict (Theme.* shape)          -> fallback royal_gold
    mw.library          list[str] of paths            -> fallback []
    mw._tag_cache       dict path -> (title, artist)  -> fallback {}
    mw.prefs            dict (keys may be missing)    -> fallback {}
    mw.store            object with .db or None       -> in-memory grouping
    mw._play_from_list(tracks, index, view=None)      -> guarded call
    mw._show_toast(msg)                              -> guarded call
    mw._load_cover_async(path, cb(path, QPixmap|None))-> guarded call
    mw.playlists        dict name -> [paths]          -> guarded (Playlists)
    mw.smart_playlist_rows() / mw.radio_rows()        -> guarded calls
    mw.request_* hooks (new playlist / m3u import-export / radio) -> guarded

Every database call is wrapped: if ``mw.store.db`` is missing or raises,
pages fall back to in-memory grouping built from ``mw.library`` +
``mw._tag_cache``.

Rendering is lazy by design (100k-track target): the table model is fully
virtual (dict lookups per cell, no per-row precompute at reset time) and all
large lists (album grid, artist cards, detail track lists) are built in
chunks of 60 items through a zero-timeout timer chain.  Album covers are
requested only for items in the viewport range, capped at the first
``_COVER_REQUEST_CAP`` (200) items when the view is not on screen yet —
beyond that, covers stream in on scroll.

RTL-safe: no hardcoded left/right alignments — labels use AlignLeading /
AlignTrailing / AlignHCenter, and row layouts mirror automatically.

This module never imports ``gm.window`` / ``main`` / ``settings_dialog`` so
it can be unit-tested with a bare fake ``mw``.
"""
from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.parse import urlparse

from PyQt6.QtCore import (
    QAbstractTableModel, QEvent, QModelIndex, QPoint, QRect, QSize, Qt,
    QTimer, pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QIcon, QLinearGradient, QMouseEvent, QPainter,
    QPainterPath, QPen, QPixmap,
)
from PyQt6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QComboBox, QFrame, QGridLayout,
    QHBoxLayout, QHeaderView, QLabel, QLayout, QLineEdit, QListWidget,
    QListView, QListWidgetItem, QPushButton, QScrollArea, QStackedWidget,
    QTableView, QToolButton, QVBoxLayout, QWidget, QWidgetItem,
)

from config import Theme
from icons import Icon, render_icon, get_dpr

_LOG = logging.getLogger("gm.pages3")

# ---------------------------------------------------------------------------
# Optional sibling modules — guarded so this file stands alone in tests.
# ---------------------------------------------------------------------------
try:  # pragma: no cover - animations module is optional at runtime
    from gm.ui.animations import (  # type: ignore[attr-defined]
        REDUCE_ANIMATIONS, fade_in, set_reduce_animations, stagger_fade,
    )
except ImportError:  # no-op fallbacks keep pages usable without the module
    REDUCE_ANIMATIONS = False

    def fade_in(widget: QWidget, duration: int = 180) -> None:  # noqa: D401
        """No-op fallback for gm.ui.animations.fade_in."""

    def stagger_fade(widgets: Sequence[QWidget],
                     step_ms: int = 24) -> None:  # noqa: D401
        """No-op fallback for gm.ui.animations.stagger_fade."""

    def set_reduce_animations(reduce_: bool) -> None:  # noqa: D401
        """No-op fallback for gm.ui.animations.set_reduce_animations."""

try:  # pragma: no cover - empty_states module is optional at runtime
    from gm.ui.empty_states import EmptyState  # type: ignore[attr-defined]
except ImportError:
    EmptyState = None  # type: ignore[assignment]

try:  # pragma: no cover - smart module is optional (counts for smart rows)
    from gm import smart as _smart_mod
except ImportError:
    _smart_mod = None


def _anim_fade(widget: QWidget) -> None:
    """fade_in() guarded — an animation backend must never break a page."""
    if REDUCE_ANIMATIONS:
        return
    try:
        fade_in(widget)
    except Exception:
        _LOG.debug("fade_in failed", exc_info=True)


def _anim_stagger(widgets: Sequence[QWidget]) -> None:
    """stagger_fade() guarded."""
    if REDUCE_ANIMATIONS or not widgets:
        return
    try:
        stagger_fade(list(widgets))
    except Exception:
        _LOG.debug("stagger_fade failed", exc_info=True)


# ---------------------------------------------------------------------------
# Small defensive helpers around the duck-typed main window.
# ---------------------------------------------------------------------------
def _g(obj: Any, name: str, default: Any = None) -> Any:
    """getattr with a default — the ONLY way pages touch ``mw``."""
    return getattr(obj, name, default) if obj is not None else default


def _call(obj: Any, name: str, *args: Any, default: Any = None) -> Any:
    """Call ``obj.name(*args)`` if it exists; swallow any error.

    Never raises, never uses ``pass`` (bandit B110 clean).
    """
    fn = _g(obj, name)
    if not callable(fn):
        return default
    try:
        return fn(*args)
    except Exception:
        _LOG.debug("mw.%s(%r…) failed", name, args[:1], exc_info=True)
        return default


def _theme_from(mw: Any) -> dict:
    """Return the active theme dict, defaulting to Royal Gold."""
    theme = _g(mw, "theme")
    return theme if isinstance(theme, dict) else Theme.get("royal_gold")


def _prefs_from(mw: Any) -> dict:
    """Return mw.prefs as a dict (missing keys are expected)."""
    prefs = _g(mw, "prefs")
    return prefs if isinstance(prefs, dict) else {}


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------
def format_duration_ms(ms: int | None) -> str:
    """Format milliseconds as ``m:ss`` (``h:mm:ss`` past an hour).

    ``None`` (mutagen-free: no metadata yet) formats as ``""`` so cells stay
    empty instead of lying with a fake 0:00.
    """
    if ms is None:
        return ""
    try:
        ms = int(ms)
    except (TypeError, ValueError):
        return ""
    if ms <= 0:
        return "0:00"
    total = ms // 1000
    m, s = divmod(total, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def star_text(rating: int | None) -> str:
    """Render a 0..5 rating as five star glyphs, e.g. ``"★★★★☆"``."""
    if rating is None:
        return ""
    try:
        r = max(0, min(5, int(rating)))
    except (TypeError, ValueError):
        return ""
    return "★" * r + "☆" * (5 - r)


def _album_key(album: str, artist: str) -> str:
    """Stable key for an (album, artist) group."""
    return f"{album}\x1f{artist}"


def _split_album_key(key: str) -> tuple[str, str]:
    """Inverse of _album_key; unknown shapes return (key, '')."""
    album, sep, artist = key.partition("\x1f")
    if not sep:
        return key, ""
    return album, artist


def _host_of(url: str) -> str:
    """Hostname of a radio URL ('' when unparseable)."""
    try:
        netloc = urlparse(str(url)).netloc
        return netloc.split("@")[-1].split(":")[0]
    except (ValueError, TypeError):
        return ""


def _base_title(path: str) -> str:
    """Filename without extension — title fallback."""
    return Path(path).stem


def _folder_of(path: str) -> str:
    """Parent-folder basename — poor-man's album for the no-db fallback."""
    parent = Path(path).parent
    return parent.name or "Unknown Album"


# ---------------------------------------------------------------------------
# Data source: wraps the provider contract with db-first / memory-fallback.
# ---------------------------------------------------------------------------
class _LibrarySource:
    """Uniform, exception-proof data access for all browsing pages.

    Every method tries ``mw.store.db`` first and falls back to grouping
    ``mw.library`` + ``mw._tag_cache`` in memory, so a bare fake ``mw``
    works in tests and a broken db never blanks a page.
    """

    def __init__(self, mw: Any) -> None:
        self._mw = mw

    # -- providers ---------------------------------------------------------
    @property
    def db(self) -> Any:
        """The LibraryDB or None."""
        store = _g(self._mw, "store")
        return _g(store, "db") if store is not None else None

    def tag_cache(self) -> dict[str, tuple[str, str]]:
        """path -> (title, artist) mapping."""
        tc = _g(self._mw, "_tag_cache")
        return tc if isinstance(tc, dict) else {}

    def library_paths(self) -> list[str]:
        """All known library paths."""
        lib = _g(self._mw, "library")
        if isinstance(lib, (list, tuple)):
            return [str(p) for p in lib]
        return []

    def meta_for(self) -> Callable[[str], dict]:
        """Return a path -> metadata-dict provider (never raises)."""
        db = self.db
        if db is not None and callable(_g(db, "get_track")):

            def provider(path: str) -> dict:
                row = _call(db, "get_track", path, default=None)
                return row if isinstance(row, dict) else {}
            return provider
        return lambda path: {}

    # -- fallback grouping --------------------------------------------------
    def _memory_track(self, path: str) -> dict:
        tc = self.tag_cache()
        title, artist = tc.get(path, ("", ""))
        return {
            "path": path, "title": title or _base_title(path),
            "artist": artist or "", "album": _folder_of(path),
            "genre": "", "duration_ms": None, "rating": None,
            "play_count": None, "bitrate_kbps": None,
        }

    # -- albums ---------------------------------------------------------------
    def albums(self) -> list[dict]:
        """Album groups: dicts with album/artist/n/rep/dur (+key added)."""
        db = self.db
        rows: list[dict] = []
        if db is not None and callable(_g(db, "album_groups")):
            got = _call(db, "album_groups", default=None)
            if isinstance(got, list):
                rows = [dict(r) for r in got if isinstance(r, dict)]
        if not rows:
            groups: dict[tuple[str, str], dict] = {}
            for path in self.library_paths():
                tr = self._memory_track(path)
                k = (tr["album"], tr["artist"])
                g = groups.setdefault(
                    k, {"album": k[0], "artist": k[1], "n": 0,
                        "rep": path, "dur": 0})
                g["n"] += 1
                if path < g["rep"]:
                    g["rep"] = path
            rows = list(groups.values())
        for row in rows:
            row.setdefault("rep", "")
            row.setdefault("dur", 0)
            try:
                row["dur"] = int(row.get("dur") or 0)
                row["n"] = int(row.get("n") or 0)
            except (TypeError, ValueError):
                row["dur"], row["n"] = 0, 0
            row["key"] = _album_key(str(row.get("album", "")),
                                    str(row.get("artist", "")))
        return rows

    def album_tracks(self, album: str, artist: str) -> list[dict]:
        """Tracks of one (album, artist) group, title-ordered."""
        db = self.db
        if db is not None and callable(_g(db, "tracks_where")):
            rows = _call(db, "tracks_where", "album=? AND artist=?",
                         (album, artist), "title COLLATE NOCASE, track_no",
                         default=None)
            if isinstance(rows, list):
                return [dict(r) for r in rows if isinstance(r, dict)]
        tracks = [self._memory_track(p) for p in self.library_paths()]
        return sorted(
            (t for t in tracks
             if t["album"] == album and t["artist"] == artist),
            key=lambda t: str(t["title"]).casefold())

    # -- artists ---------------------------------------------------------------
    def artists(self) -> list[tuple[str, int]]:
        """(artist, track count) sorted by count then name."""
        db = self.db
        if db is not None and callable(_g(db, "artist_counts")):
            got = _call(db, "artist_counts", default=None)
            if isinstance(got, list):
                return [(str(a), int(n)) for a, n in got]
        counts: dict[str, int] = {}
        for path in self.library_paths():
            _, artist = self.tag_cache().get(path, ("", ""))
            if artist:
                counts[artist] = counts.get(artist, 0) + 1
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))

    def artist_plays(self) -> dict[str, int]:
        """artist -> summed play_count when the db can answer (else {})."""
        db = self.db
        if db is not None and callable(_g(db, "top_artists")):
            got = _call(db, "top_artists", 100_000, default=None)
            if isinstance(got, list):
                out: dict[str, int] = {}
                for row in got:
                    try:
                        out[str(row[0])] = int(row[1] or 0)
                    except (TypeError, ValueError, IndexError):
                        continue
                return out
        return {}

    def artist_tracks(self, artist: str) -> list[dict]:
        """All tracks of one artist, title-ordered."""
        db = self.db
        if db is not None and callable(_g(db, "tracks_where")):
            rows = _call(db, "tracks_where", "artist=?", (artist,),
                         "title COLLATE NOCASE, album", default=None)
            if isinstance(rows, list):
                return [dict(r) for r in rows if isinstance(r, dict)]
        tracks = [self._memory_track(p) for p in self.library_paths()]
        return sorted((t for t in tracks if t["artist"] == artist),
                      key=lambda t: str(t["title"]).casefold())

    def artist_reps(self) -> dict[str, str]:
        """artist -> representative path (for avatar covers), cheap."""
        reps: dict[str, str] = {}
        for row in self.albums():
            artist = str(row.get("artist", ""))
            rep = str(row.get("rep", ""))
            if artist and rep and (artist not in reps or rep < reps[artist]):
                reps[artist] = rep
        return reps

    # -- genres ------------------------------------------------------------
    def genres(self) -> list[tuple[str, int]]:
        """(genre, track count) sorted by count then name."""
        db = self.db
        if db is not None and callable(_g(db, "genre_counts")):
            got = _call(db, "genre_counts", default=None)
            if isinstance(got, list):
                return [(str(g), int(n)) for g, n in got]
        paths = self.library_paths()
        return [("Unknown", len(paths))] if paths else []

    def genre_tracks(self, genre: str) -> list[dict]:
        """Tracks of one genre, title-ordered."""
        db = self.db
        if db is not None and callable(_g(db, "tracks_where")):
            rows = _call(db, "tracks_where", "genre=?", (genre,),
                         "title COLLATE NOCASE, artist", default=None)
            if isinstance(rows, list):
                return [dict(r) for r in rows if isinstance(r, dict)]
        tracks = [self._memory_track(p) for p in self.library_paths()]
        if genre == "Unknown":
            return sorted(tracks, key=lambda t: str(t["title"]).casefold())
        return []

    # -- search ------------------------------------------------------------
    def search(self, query: str, limit: int = 500) -> list[str]:
        """FTS search through the db (empty on failure)."""
        db = self.db
        if db is not None and callable(_g(db, "search_tracks")):
            got = _call(db, "search_tracks", query, limit, default=None)
            if isinstance(got, list):
                return [str(p) for p in got]
        return []

    # -- smart playlist counts ----------------------------------------------
    def smart_count(self, key: str) -> int | None:
        """Track count for a builtin smart playlist (None when unknown)."""
        if _smart_mod is None:
            return None
        db = self.db
        if db is None or not callable(_g(db, "tracks_where")):
            return None
        spec = _smart_mod.BUILTINS.get(key)
        if not spec:
            return None
        try:
            where, args = _smart_mod.sql_where(
                _smart_mod.validate_spec(spec["spec"]))
            rows = db.tracks_where(where, args, "", 5000)
            return len(rows) if isinstance(rows, list) else None
        except Exception:
            _LOG.debug("smart_count(%r) failed", key, exc_info=True)
            return None


# ---------------------------------------------------------------------------
# Progressive builder (chunks of 60 on a zero-timeout timer chain).
# ---------------------------------------------------------------------------
class _ChunkBuilder:
    """Append UI items in event-loop chunks so refreshes never block.

    Usage: pages bump ``self._builder.start(get_total, add_range, done)``;
    calling ``start()`` again invalidates any in-flight chain (generation
    counter), and ``cancel()`` stops everything.
    """

    CHUNK = 60

    def __init__(self) -> None:
        self._gen = 0

    def cancel(self) -> None:
        """Drop any scheduled work."""
        self._gen += 1

    def start(self, total_getter: Callable[[], int],
              add_range: Callable[[int, int], None],
              done: Callable[[], None] | None = None) -> None:
        """Run ``add_range(pos, next)`` in CHUNK-sized slices."""
        gen = self._gen + 1
        self._gen = gen
        state = {"pos": 0}

        def _tick() -> None:
            if gen != self._gen:
                return  # superseded by a newer build
            total = max(0, int(total_getter()))
            pos = state["pos"]
            nxt = min(pos + self.CHUNK, total)
            if nxt > pos:
                try:
                    add_range(pos, nxt)
                except Exception:
                    _LOG.warning("chunked build failed", exc_info=True)
                    return  # abort this chain; page stays consistent
                state["pos"] = nxt
            if state["pos"] < total:
                QTimer.singleShot(0, _tick)
            elif done is not None:
                try:
                    done()
                except Exception:
                    _LOG.warning("chunk done callback failed", exc_info=True)

        QTimer.singleShot(0, _tick)


# ---------------------------------------------------------------------------
# Custom-painted visuals shared by the pages.
# ---------------------------------------------------------------------------
class _CoverTile(QWidget):
    """Rounded cover tile with a themed gradient + note-icon placeholder.

    Paints nothing until a real pixmap arrives (covers load async through
    ``mw._load_cover_async``), and never crashes on paint.
    """

    def __init__(self, size: int = 150, radius: int = 14,
                 glyph: str = Icon.MUSIC_NOTE, parent: QWidget | None = None):
        super().__init__(parent)
        self._size = int(size)
        self._radius = int(radius)
        self._glyph = glyph
        self._pixmap: QPixmap | None = None
        self._theme: dict = {}
        self.setFixedSize(self._size, self._size)

    def set_theme(self, theme: dict) -> None:
        """Store theme for placeholder painting."""
        self._theme = theme if isinstance(theme, dict) else {}
        self.update()

    def set_pixmap(self, pm: QPixmap | None) -> None:
        """Set (or clear) the cover pixmap."""
        self._pixmap = pm if (pm is not None and not pm.isNull()) else None
        self.update()

    def paintEvent(self, event: QEvent) -> None:  # noqa: N802 (Qt API)
        try:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            w, h = self.width(), self.height()
            path = QPainterPath()
            path.addRoundedRect(QRect(0, 0, w, h), self._radius, self._radius)
            if self._pixmap is not None:
                scaled = self._pixmap.scaled(
                    w, h,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation)
                side = min(scaled.width(), scaled.height())
                sx = (scaled.width() - side) // 2
                sy = (scaled.height() - side) // 2
                painter.setClipPath(path)
                painter.drawPixmap(0, 0, scaled.copy(sx, sy, side, side))
            else:
                t = self._theme
                is_dark = bool(t.get("is_dark", True))
                core = QColor(t.get("gold", "#d4a85a"))
                light = QColor(t.get("gold_light", "#f0d896"))
                deep = QColor(t.get("gold_deep", "#8b6a2a"))
                grad = QLinearGradient(0, 0, w, h)
                if is_dark:
                    grad.setColorAt(0.0, deep)
                    grad.setColorAt(0.5, core)
                    grad.setColorAt(1.0, light)
                    glyph_color = "rgba(26,20,16,90)"
                else:
                    grad.setColorAt(0.0, core.lighter(170))
                    grad.setColorAt(0.5, core)
                    grad.setColorAt(1.0, deep)
                    glyph_color = "rgba(255,255,255,150)"
                painter.setBrush(QBrush(grad))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawPath(path)
                note = render_icon(self._glyph, max(24, h // 3),
                                   glyph_color, get_dpr())
                painter.drawPixmap((w - note.width()) // 2,
                                   (h - note.height()) // 2, note)
            painter.end()
        except Exception:
            _LOG.warning("CoverTile paint failed", exc_info=True)


class _AvatarBubble(QWidget):
    """96px circular artist avatar: initial letter on a themed gradient,
    replaced by a clipped cover pixmap when one arrives."""

    def __init__(self, diameter: int = 96, parent: QWidget | None = None):
        super().__init__(parent)
        self._d = int(diameter)
        self._pixmap: QPixmap | None = None
        self._theme: dict = {}
        self._letter = "?"
        self.setFixedSize(self._d, self._d)

    def set_name(self, name: str) -> None:
        """Set the artist name (initial letter derives from it)."""
        text = (name or "").strip()
        self._letter = text[:1].upper() if text else "?"
        self.update()

    def set_theme(self, theme: dict) -> None:
        """Store theme for gradient painting."""
        self._theme = theme if isinstance(theme, dict) else {}
        self.update()

    def set_pixmap(self, pm: QPixmap | None) -> None:
        """Set (or clear) the artist cover."""
        self._pixmap = pm if (pm is not None and not pm.isNull()) else None
        self.update()

    def paintEvent(self, event: QEvent) -> None:  # noqa: N802 (Qt API)
        try:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            w, h = self.width(), self.height()
            rect = QRect(0, 0, w - 1, h - 1)
            if self._pixmap is not None:
                scaled = self._pixmap.scaled(
                    w, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation)
                side = min(scaled.width(), scaled.height())
                sx = (scaled.width() - side) // 2
                sy = (scaled.height() - side) // 2
                path = QPainterPath()
                path.addEllipse(QRect(0, 0, w, h))
                painter.setClipPath(path)
                painter.drawPixmap(0, 0, scaled.copy(sx, sy, side, side))
                painter.setClipping(False)
                pen = QPen(QColor(255, 255, 255, 40), 1)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(rect)
                painter.end()
                return
            t = self._theme
            is_dark = bool(t.get("is_dark", True))
            grad = QLinearGradient(0, 0, w, h)
            if is_dark:
                grad.setColorAt(0.0, QColor(t.get("gold_deep", "#8b6a2a")))
                grad.setColorAt(1.0, QColor(t.get("gold_light", "#f0d896")))
                text_color = QColor("#141414")
            else:
                grad.setColorAt(0.0, QColor(t.get("gold_light", "#f0d896")))
                grad.setColorAt(1.0, QColor(t.get("gold_deep", "#8b6a2a")))
                text_color = QColor("#ffffff")
            painter.setBrush(QBrush(grad))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(rect)
            font = QFont(self.font())
            font.setBold(True)
            font.setPixelSize(max(14, int(h * 0.42)))
            painter.setFont(font)
            painter.setPen(QPen(text_color))
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter),
                             self._letter)
            painter.end()
        except Exception:
            _LOG.warning("AvatarBubble paint failed", exc_info=True)


class _TrackRowWidget(QWidget):
    """Two-line track row (item widget for detail lists).

    Leading track number, bold title + muted subtitle (artist · album ·
    duration, plus ★ glyphs when rated), trailing duration.  All labels are
    mouse-transparent so double-clicks reach the QListWidget.
    """

    def __init__(self, index: int, track: dict, theme: dict,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self._theme = theme if isinstance(theme, dict) else {}
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(10)

        self._num = QLabel(str(index))
        self._num.setObjectName("TrackNum")
        self._num.setProperty("class", "muted")
        self._num.setFixedWidth(30)
        self._num.setAlignment(Qt.AlignmentFlag.AlignTrailing |
                              Qt.AlignmentFlag.AlignVCenter)

        title = str(track.get("title") or _base_title(str(track.get("path", ""))))
        artist = str(track.get("artist") or "")
        album = str(track.get("album") or "")
        dur = format_duration_ms(track.get("duration_ms"))
        rating = track.get("rating")

        self._title = QLabel(title)
        self._title.setWordWrap(False)
        mid = QVBoxLayout()
        mid.setSpacing(0)
        mid.addWidget(self._title, 0)
        sub_parts = [p for p in (artist, album, dur) if p]
        try:
            rating_value = int(rating or 0)
        except (TypeError, ValueError):
            rating_value = 0
        if rating_value > 0:
            sub_parts.append(star_text(rating_value))
        self._sub = QLabel("  ·  ".join(sub_parts))
        self._sub.setWordWrap(False)
        mid.addWidget(self._sub, 0)

        self._dur = QLabel(dur)
        self._dur.setAlignment(Qt.AlignmentFlag.AlignTrailing |
                               Qt.AlignmentFlag.AlignVCenter)

        row.addWidget(self._num)
        row.addLayout(mid, 1)
        row.addWidget(self._dur)

        for label in (self._num, self._title, self._sub, self._dur):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents,
                               True)
        self.apply_theme(theme)

    def apply_theme(self, theme: dict) -> None:
        """Restyle labels from a theme dict (cheap QSS)."""
        self._theme = theme if isinstance(theme, dict) else {}
        text = self._theme.get("text", "#f4f4f6")
        muted = self._theme.get("muted", "#979ca6")
        self._title.setStyleSheet(
            f"background: transparent; color: {text}; font-weight: 600;")
        self._sub.setStyleSheet(
            f"background: transparent; color: {muted};")
        self._num.setStyleSheet(
            f"background: transparent; color: {muted};")
        self._dur.setStyleSheet(
            f"background: transparent; color: {muted};")


class _DetailTrackList(QListWidget):
    """QListWidget of two-line track rows, built progressively.

    Emits :pyattr:`row_activated(int)` on double-click / activation; the
    owning page plays ``self.paths()[row]`` through ``mw._play_from_list``.
    """

    row_activated = pyqtSignal(int)

    def __init__(self, theme: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("TrackList")
        self._theme = theme if isinstance(theme, dict) else {}
        self._tracks: list[dict] = []
        self._builder = _ChunkBuilder()
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setUniformItemSizes(False)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.itemActivated.connect(self._on_activated)
        self.itemDoubleClicked.connect(self._on_activated)

    # -- API ------------------------------------------------------------------
    def set_theme(self, theme: dict) -> None:
        """Restyle existing row widgets."""
        self._theme = theme if isinstance(theme, dict) else {}
        for i in range(self.count()):
            widget = self.itemWidget(self.item(i))
            if isinstance(widget, _TrackRowWidget):
                widget.apply_theme(self._theme)

    def tracks(self) -> list[dict]:
        """Currently displayed tracks (in row order)."""
        return list(self._tracks)

    def paths(self) -> list[str]:
        """Row-ordered paths (what gets passed to _play_from_list)."""
        return [str(t.get("path", "")) for t in self._tracks]

    def set_tracks(self, tracks: Sequence[dict]) -> None:
        """Rebuild the list progressively (60 rows per event-loop tick)."""
        self._builder.cancel()
        self._tracks = [dict(t) for t in tracks]
        self.clear()

        def add_range(pos: int, nxt: int) -> None:
            for i in range(pos, nxt):
                self._append_row(i)

        self._builder.start(lambda: len(self._tracks), add_range)

    def build_now(self) -> None:
        """Synchronously finish any pending chunked build (tests/tooling)."""
        for i in range(self.count(), len(self._tracks)):
            self._append_row(i)

    # -- internals ------------------------------------------------------------
    def _append_row(self, i: int) -> None:
        track = self._tracks[i]
        widget = _TrackRowWidget(i + 1, track, self._theme)
        item = QListWidgetItem()
        item.setSizeHint(QSize(0, 56))
        self.addItem(item)
        self.setItemWidget(item, widget)

    def _on_activated(self, item: QListWidgetItem) -> None:
        self.row_activated.emit(self.row(item))


class _FallbackEmptyState(QWidget):
    """Minimal empty state (icon + EmptyTitle + EmptyHint) used when
    gm.ui.empty_states cannot be imported."""

    def __init__(self, title: str, hint: str = "",
                 glyph: str = Icon.ALBUMS, parent: QWidget | None = None):
        super().__init__(parent)
        self._glyph = glyph
        self._theme: dict = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 40, 24, 40)
        lay.setSpacing(8)
        self._icon = QLabel()
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title = QLabel(title)
        self._title.setObjectName("EmptyTitle")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint = QLabel(hint)
        self._hint.setObjectName("EmptyHint")
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint.setWordWrap(True)
        lay.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._title, 0, Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._hint, 0, Qt.AlignmentFlag.AlignCenter)

    def set_theme(self, theme: dict) -> None:
        """Re-render the glyph in the theme's muted color."""
        self._theme = theme if isinstance(theme, dict) else {}
        pm = render_icon(self._glyph, 44,
                         self._theme.get("muted", "#979ca6"), get_dpr())
        self._icon.setPixmap(pm)


def make_empty_state(title: str, hint: str = "",
                     glyph: str = Icon.ALBUMS) -> QWidget:
    """Build an EmptyState from gm.ui.empty_states when available.

    The real widget's constructor is duck-typed-detected; on any mismatch we
    fall back to :class:`_FallbackEmptyState` so pages always render.
    """
    if EmptyState is not None:
        for ctor_args, ctor_kwargs in (
            ((), {"icon": glyph, "title": title, "hint": hint}),
            ((), {"title": title, "hint": hint}),
            ((title, hint), {}),
        ):
            try:
                return EmptyState(*ctor_args, **ctor_kwargs)  # type: ignore[misc]
            except TypeError:
                continue
            except Exception:
                _LOG.debug("EmptyState construction failed", exc_info=True)
                break
    return _FallbackEmptyState(title, hint, glyph)


def _theme_empty_state(widget: QWidget, theme: dict) -> None:
    """set_theme() an empty-state widget (real or fallback) if it can."""
    fn = getattr(widget, "set_theme", None)
    if callable(fn):
        try:
            fn(theme)
        except Exception:
            _LOG.debug("empty-state set_theme failed", exc_info=True)


# ---------------------------------------------------------------------------
# FlowLayout — minimal wrapping layout for genre chips.
# ---------------------------------------------------------------------------
class FlowLayout(QLayout):
    """Left-to-right (RTL: right-to-left) wrapping flow of widgets."""

    def __init__(self, parent: QWidget | None = None,
                 margin: int = 0, spacing: int = 8):
        super().__init__(parent)
        self.setContentsMargins(margin, margin, margin, margin)
        self._spacing = int(spacing)
        self._items: list[QWidgetItem] = []

    # -- QLayout API -----------------------------------------------------------
    def addItem(self, item: QLayout) -> None:  # noqa: N802 (Qt API)
        """Append a layout item."""
        self._items.append(item)

    def insertWidget(self, index: int, widget: QWidget) -> None:
        """Insert *widget* at *index*."""
        self.addChildWidget(widget)
        self._items.insert(max(0, min(index, len(self._items))),
                           QWidgetItem(widget))
        self.update()

    def count(self) -> int:
        """Number of items."""
        return len(self._items)

    def itemAt(self, index: int) -> QLayout | None:  # noqa: N802 (Qt API)
        """Item at *index* or None."""
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayout | None:  # noqa: N802 (Qt API)
        """Remove and return the item at *index*."""
        return self._items.pop(index) if 0 <= index < len(self._items) \
            else None

    def expandingDirections(self) -> Qt.Orientations:  # noqa: N802
        """Flows never expand."""
        return Qt.Orientations(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt API)
        """Rows wrap — height depends on width."""
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 (Qt API)
        """Compute wrapped height for *width*."""
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802 (Qt API)
        """Lay out children inside *rect*."""
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt API)
        """Preferred size (one row if unconstrained)."""
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802 (Qt API)
        """Minimum size: the widest item plus margins."""
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(),
                      margins.top() + margins.bottom())
        return size

    def clear(self) -> None:
        """Remove all items (widgets are deleted by their parents)."""
        while self._items:
            item = self._items.pop()
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)

    # -- internals ------------------------------------------------------------
    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        margins = self.contentsMargins()
        effective = rect.adjusted(margins.left(), margins.top(),
                                  -margins.right(), -margins.bottom())
        x = effective.x()
        y = effective.y()
        line_height = 0
        spacing = self._spacing
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + spacing
            if next_x - spacing > effective.right() + 1 and line_height > 0:
                x = effective.x()
                y = y + line_height + spacing
                line_height = 0
                next_x = x + hint.width() + spacing
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y),
                                        QSize(hint.width(), hint.height())))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + margins.bottom()


# ---------------------------------------------------------------------------
# 1. TrackTableModel + LibraryTable
# ---------------------------------------------------------------------------
ALL_COLUMNS: list[tuple[str, str]] = [
    ("index", "#"),
    ("title", "Title"),
    ("artist", "Artist"),
    ("album", "Album"),
    ("genre", "Genre"),
    ("duration", "Duration"),
    ("rating", "Rating"),
    ("bitrate", "Bitrate"),
    ("play_count", "Plays"),
    ("path", "File"),
]
_COLUMN_LABELS = dict(ALL_COLUMNS)
_DEFAULT_COLUMNS = ["index", "title", "artist", "album", "genre", "duration"]

_META_COLUMNS = frozenset(
    {"album", "genre", "duration", "rating", "bitrate", "play_count"})
# Lazy per-path metadata memo cap — beyond this the dict resets (virtual
# model stays memory-bounded for 100k-track libraries).
_META_MEMO_CAP = 20_000


class TrackTableModel(QAbstractTableModel):
    """Virtual table model over a list of paths.

    Cell text is computed on demand from ``tag_cache`` (title/artist) and the
    optional ``meta_for(path) -> dict`` provider (album/genre/duration_ms/
    rating/bitrate/play_count) — no per-row precompute happens in
    :meth:`set_paths`, so 100k rows reset instantly.

    Sorting (view ``sortByColumn``) re-orders the stored path list and emits
    ``layoutChanged``.
    """

    _LEADING = (Qt.AlignmentFlag.AlignLeading | Qt.AlignmentFlag.AlignVCenter)
    _TRAILING = (Qt.AlignmentFlag.AlignTrailing | Qt.AlignmentFlag.AlignVCenter)

    def __init__(self, paths: Sequence[str] | None = None,
                 tag_cache: dict[str, tuple[str, str]] | None = None,
                 meta_for: Callable[[str], dict] | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self._paths: list[str] = [str(p) for p in (paths or [])]
        self._tag_cache: dict[str, tuple[str, str]] = tag_cache or {}
        self._meta_for = meta_for
        self._columns: list[str] = list(_DEFAULT_COLUMNS)
        self._meta_memo: dict[str, dict] = {}

    # -- setters ------------------------------------------------------------
    def set_paths(self, paths: Sequence[str]) -> None:
        """Reset the model with a new path list."""
        self.beginResetModel()
        self._paths = [str(p) for p in paths]
        self._meta_memo = {}
        self.endResetModel()

    def set_tag_cache(self, tag_cache: dict[str, tuple[str, str]]) -> None:
        """Swap the (title, artist) cache."""
        self._tag_cache = tag_cache if isinstance(tag_cache, dict) else {}
        self._meta_memo = {}
        self.layoutAboutToBeChanged.emit()
        self.layoutChanged.emit()

    def set_meta_provider(self, meta_for: Callable[[str], dict] | None) -> None:
        """Swap the path -> metadata-dict provider."""
        self._meta_for = meta_for
        self._meta_memo = {}
        self.layoutAboutToBeChanged.emit()
        self.layoutChanged.emit()

    def set_columns(self, col_ids: Sequence[str]) -> None:
        """Select which of ALL_COLUMNS are shown (unknown ids ignored)."""
        cols = [c for c in col_ids if c in _COLUMN_LABELS]
        self._columns = cols or list(_DEFAULT_COLUMNS)
        self.layoutAboutToBeChanged.emit()
        self.layoutChanged.emit()
        self.headerDataChanged.emit(
            Qt.Orientation.Horizontal, 0, len(self._columns) - 1)

    # -- introspection ---------------------------------------------------------
    def paths(self) -> list[str]:
        """Current (possibly sorted) path list."""
        return list(self._paths)

    def columns(self) -> list[str]:
        """Active column ids."""
        return list(self._columns)

    def column_id(self, column: int) -> str:
        """Column id at *column* ('' when out of range)."""
        return self._columns[column] if 0 <= column < len(self._columns) else ""

    # -- QAbstractTableModel ---------------------------------------------------
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802,D102
        return 0 if parent.isValid() else len(self._paths)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802,D102
        return 0 if parent.isValid() else len(self._columns)

    def _meta(self, path: str) -> dict:
        """Metadata for *path* with a bounded lazy memo."""
        meta = self._meta_memo.get(path)
        if meta is not None:
            return meta
        provider = self._meta_for
        if provider is None:
            meta = {}
        else:
            try:
                got = provider(path)
                meta = got if isinstance(got, dict) else {}
            except Exception:
                meta = {}
        if len(self._meta_memo) >= _META_MEMO_CAP:
            self._meta_memo.clear()
        self._meta_memo[path] = meta
        return meta

    def data(self, index: QModelIndex,
             role: int = Qt.ItemDataRole.DisplayRole) -> Any:  # noqa: D102
        if not index.isValid():
            return None
        row, col = index.row(), index.column()
        if not (0 <= row < len(self._paths)) or \
                not (0 <= col < len(self._columns)):
            return None
        path = self._paths[row]
        cid = self._columns[col]

        if role == Qt.ItemDataRole.TextAlignmentRole:
            if cid in ("index", "duration", "rating", "bitrate", "play_count"):
                return int(self._TRAILING)
            return int(self._LEADING)

        if role == Qt.ItemDataRole.ToolTipRole and cid == "path":
            return path

        if role != Qt.ItemDataRole.DisplayRole:
            return None

        if cid == "index":
            return str(row + 1)
        if cid == "title":
            title, _artist = self._tag_cache.get(path, ("", ""))
            return title or _base_title(path)
        if cid == "artist":
            _title, artist = self._tag_cache.get(path, ("", ""))
            return artist
        if cid == "path":
            return path

        meta = self._meta(path)
        if cid == "album":
            return str(meta.get("album") or "")
        if cid == "genre":
            return str(meta.get("genre") or "")
        if cid == "duration":
            return format_duration_ms(meta.get("duration_ms"))
        if cid == "rating":
            return star_text(meta.get("rating"))
        if cid == "bitrate":
            br = meta.get("bitrate_kbps")
            if br is None:
                br = meta.get("bitrate")
            try:
                return f"{int(br)} kbps" if br is not None else ""
            except (TypeError, ValueError):
                return ""
        if cid == "play_count":
            pc = meta.get("play_count")
            try:
                return str(int(pc)) if pc is not None else ""
            except (TypeError, ValueError):
                return ""
        return None

    def headerData(self, section: int, orientation: Qt.Orientation,  # noqa: N802,D102
                   role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return _COLUMN_LABELS.get(self.column_id(section), "")
        return str(section + 1)

    # -- sorting ------------------------------------------------------------
    def sort(self, column: int, order: Qt.SortOrder =  # noqa: N802,D102
             Qt.SortOrder.AscendingOrder) -> None:
        """Re-order the path list by *column* and emit layoutChanged.

        All sort keys are ``(missing_flag, value)`` tuples so rows without
        metadata sort LAST in both ascending and descending order (a final
        stable pass on the flag alone re-buries them after a reverse).
        """
        cid = self.column_id(column)
        if not cid:
            return
        key_fn = self._sort_key(cid)
        self.layoutAboutToBeChanged.emit()
        self._paths.sort(key=key_fn)
        if order == Qt.SortOrder.DescendingOrder:
            self._paths.reverse()
            # stable re-sort by missing-flag only keeps no-meta rows last
            self._paths.sort(key=lambda p: key_fn(p)[0])
        self._meta_memo = {}
        self.layoutChanged.emit()

    def _sort_key(self, cid: str) -> Callable[[str], tuple[int, Any]]:
        """Decorate a path into a sortable ``(missing, value)`` tuple."""
        if cid == "index":
            return lambda p: (0, 0)
        if cid == "title":
            def title_key(p: str) -> tuple[int, str]:
                title, _ = self._tag_cache.get(p, ("", ""))
                return (0, (title or _base_title(p)).casefold())
            return title_key
        if cid == "artist":
            def artist_key(p: str) -> tuple[int, str]:
                _, artist = self._tag_cache.get(p, ("", ""))
                return (0, (artist or "").casefold())
            return artist_key
        if cid == "path":
            return lambda p: (0, p.casefold())

        field = {"album": "album", "genre": "genre",
                 "duration": "duration_ms", "rating": "rating",
                 "bitrate": "bitrate_kbps",
                 "play_count": "play_count"}[cid]

        def meta_key(p: str) -> tuple[int, Any]:
            meta = self._meta(p)
            value = meta.get(field)
            if value is None:
                value = meta.get("bitrate") if field == "bitrate_kbps" else None
            if value is None:
                return (1, 0)          # missing sorts last in both orders
            if isinstance(value, str):
                return (0, value.casefold())
            try:
                return (0, float(value))
            except (TypeError, ValueError):
                return (1, 0)
        return meta_key


def _table_qss(theme: dict) -> str:
    """LibraryTable-only QSS (alternating rows, header, selection)."""
    t = theme if isinstance(theme, dict) else {}
    panel2 = t.get("panel_bg_2", "#1a1b1f")
    panel = t.get("panel_bg", "#101013")
    border = t.get("border", "#2c2d33")
    active = t.get("active", "#26272d")
    muted = t.get("muted", "#979ca6")
    return f"""
    QTableView#LibraryTable {{
        background: transparent;
        border: none;
        gridline-color: transparent;
        alternate-background-color: {panel2};
        selection-background-color: {active};
        selection-color: {t.get('text', '#f4f4f6')};
    }}
    QTableView#LibraryTable::item {{ padding: 0 6px; border: none; }}
    QHeaderView::section {{
        background: {panel}; color: {muted};
        border: none; border-bottom: 1px solid {border};
        padding: 8px 10px; font-weight: 700;
    }}
    QTableCornerButton::section {{ background: {panel}; border: none; }}
    """


class LibraryTable(QTableView):
    """Themed virtual track table on :class:`TrackTableModel`.

    Row height follows the ``density`` pref (compact 28 / default 34 /
    comfortable 40); double-click plays the row via
    ``mw._play_from_list(current_paths, row_index, view=self)``.
    """

    _DENSITY_ROWS = {"compact": 28, "default": 34, "comfortable": 40}
    _FIXED_WIDTHS = {"index": 60, "duration": 90, "rating": 110,
                     "bitrate": 90, "play_count": 80}
    _DEFAULT_WIDTHS = {"album": 200, "genre": 120, "path": 260}
    _STRETCH_COLUMNS = {"title", "artist"}

    def __init__(self, mw: Any, parent: QWidget | None = None,
                 columns: Sequence[str] | None = None):
        super().__init__(parent)
        self._mw = mw
        self._theme = _theme_from(mw)
        self.setObjectName("LibraryTable")

        prefs = _prefs_from(mw)
        self._row_height = self._DENSITY_ROWS.get(
            str(prefs.get("density", "default")), 34)

        self._src = _LibrarySource(mw)
        self._model = TrackTableModel(
            tag_cache=self._src.tag_cache(),
            meta_for=self._src.meta_for(), parent=self)
        if columns:
            self._model.set_columns(columns)
        elif isinstance(prefs.get("table_columns"), (list, tuple)):
            cols = [str(c) for c in prefs["table_columns"]]
            self._model.set_columns(cols)
        self.setModel(self._model)

        vh = self.verticalHeader()
        vh.setVisible(False)
        vh.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        vh.setDefaultSectionSize(self._row_height)
        vh.setMinimumSectionSize(self._row_height)

        hh = self.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hh.setStretchLastSection(False)
        hh.setSectionsClickable(True)
        hh.setSortIndicatorShown(True)

        self.setShowGrid(False)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setSortingEnabled(True)
        self.setWordWrap(False)

        self.doubleClicked.connect(self._on_double_clicked)
        self._apply_column_widths()
        self.set_theme(self._theme)

    # -- API -------------------------------------------------------------------
    def set_paths(self, paths: Sequence[str]) -> None:
        """Point the table at a new path list."""
        self._model.set_tag_cache(self._src.tag_cache())
        self._model.set_meta_provider(self._src.meta_for())
        self._model.set_paths(paths)

    def set_columns(self, col_ids: Sequence[str]) -> None:
        """Change visible columns and re-apply width policies."""
        self._model.set_columns(col_ids)
        self._apply_column_widths()

    def set_theme(self, theme: dict) -> None:
        """Re-apply the table QSS for *theme*."""
        self._theme = theme if isinstance(theme, dict) else {}
        self.setStyleSheet(_table_qss(self._theme))

    def current_paths(self) -> list[str]:
        """The model's path list in display order."""
        return self._model.paths()

    def track_model(self) -> TrackTableModel:
        """The TrackTableModel (typed convenience)."""
        return self._model

    # -- internals --------------------------------------------------------------
    def _on_double_clicked(self, index: QModelIndex) -> None:
        self._play_row(index.row())

    def _play_row(self, row: int) -> None:
        """Play row *row* of the current path list."""
        if 0 <= row < len(self._model.paths()):
            _call(self._mw, "_play_from_list", self.current_paths(), row,
                  self, default=None)

    def _apply_column_widths(self) -> None:
        hh = self.horizontalHeader()
        if hh is None:
            return
        for col in range(self._model.columnCount()):
            cid = self._model.column_id(col)
            if cid in self._STRETCH_COLUMNS:
                hh.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
            elif cid in self._FIXED_WIDTHS:
                hh.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
                hh.resizeSection(col, self._FIXED_WIDTHS[cid])
            elif cid in self._DEFAULT_WIDTHS:
                hh.resizeSection(col, self._DEFAULT_WIDTHS[cid])


# ---------------------------------------------------------------------------
# Shared page furniture
# ---------------------------------------------------------------------------
_COVER_REQUEST_CAP = 200   # covers requested for off-screen lists (documented)


class _PageHeader(QWidget):
    """Title + search box + view-toggle + optional combo, shared by pages.

    Emits :pyattr:`search_changed(str)`, :pyattr:`mode_changed(str)` and
    :pyattr:`sort_changed(str)`.
    """

    search_changed = pyqtSignal(str)
    mode_changed = pyqtSignal(str)
    sort_changed = pyqtSignal(str)

    DEBOUNCE_MS = 150

    def __init__(self, title: str, theme: dict,
                 modes: Sequence[tuple[str, str, str]] | None = None,
                 sorts: Sequence[str] | None = None,
                 search_hint: str = "Search…",
                 parent: QWidget | None = None):
        super().__init__(parent)
        self._theme = theme if isinstance(theme, dict) else {}
        self._mode = modes[0][0] if modes else ""

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        self.title = QLabel(title)
        self.title.setObjectName("PageTitle")
        lay.addWidget(self.title)
        lay.addStretch(1)

        self.search = QLineEdit()
        self.search.setPlaceholderText(search_hint)
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(220)
        self._search_action = self.search.addAction(
            QIcon(render_icon(Icon.SEARCH, 16,
                              self._theme.get("muted", "#979ca6"), get_dpr())),
            QLineEdit.ActionPosition.LeadingPosition)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(self.DEBOUNCE_MS)
        self._debounce.timeout.connect(
            lambda: self.search_changed.emit(self.search.text()))
        self.search.textChanged.connect(lambda _t: self._debounce.start())
        lay.addWidget(self.search)

        if modes:
            group = QButtonGroup(self)
            group.setExclusive(True)
            for mode_id, glyph, tooltip in modes:
                btn = QToolButton()
                btn.setObjectName("ViewToggle")
                btn.setCheckable(True)
                btn.setToolTip(tooltip)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setProperty("mode", mode_id)
                btn.setFixedSize(34, 30)
                group.addButton(btn)
                if mode_id == self._mode:
                    btn.setChecked(True)
                btn.clicked.connect(
                    lambda _c, m=mode_id: self._select_mode(m))
                lay.addWidget(btn)
                setattr(self, f"_btn_{mode_id}", btn)
            self._toggle_group = group

        if sorts:
            self.sort_combo = QComboBox()
            for label in sorts:
                self.sort_combo.addItem(label)
            self.sort_combo.setCursor(Qt.CursorShape.PointingHandCursor)
            self.sort_combo.currentIndexChanged.connect(
                lambda _i: self.sort_changed.emit(self.sort_combo.currentText()))
            lay.addWidget(self.sort_combo)

    # -- API ----------------------------------------------------------------
    def mode(self) -> str:
        """Active view mode id."""
        return self._mode

    def set_mode(self, mode_id: str) -> None:
        """Programmatically switch the toggle."""
        self._select_mode(mode_id)

    def set_theme(self, theme: dict) -> None:
        """Restyle header controls."""
        self._theme = theme if isinstance(theme, dict) else {}
        self._search_action.setIcon(
            QIcon(render_icon(Icon.SEARCH, 16,
                              self._theme.get("muted", "#979ca6"), get_dpr())))
        group = getattr(self, "_toggle_group", None)
        if group is None:
            return
        gold = self._theme.get("gold", "#d4a85a")
        active = self._theme.get("active", "#26272d")
        border = self._theme.get("border", "#2c2d33")
        glyphs = {"grid": Icon.GRID, "list": Icon.ROWS,
                  "cards": Icon.GRID, "rows": Icon.ROWS}
        for btn in group.buttons():
            glyph = glyphs.get(str(btn.property("mode")), Icon.GRID)
            color = gold if btn.isChecked() \
                else self._theme.get("muted", "#979ca6")
            btn.setIcon(QIcon(render_icon(glyph, 18, color, get_dpr())))
            btn.setStyleSheet(f"""
                QToolButton#ViewToggle {{
                    background: transparent;
                    border: 1px solid {border};
                    border-radius: 8px;
                }}
                QToolButton#ViewToggle:checked {{ background: {active}; }}
                QToolButton#ViewToggle:hover {{
                    background: {self._theme.get('panel_bg_2', '#1a1b1f')};
                }}
            """)

    # -- internals ------------------------------------------------------------
    def _select_mode(self, mode_id: str) -> None:
        if mode_id == self._mode:
            return
        self._mode = mode_id
        btn = getattr(self, f"_btn_{mode_id}", None)
        if btn is not None:
            btn.setChecked(True)
        self.set_theme(self._theme)
        self.mode_changed.emit(mode_id)


class _DetailHeader(QWidget):
    """Back button + big title row used at the top of detail views."""

    back_requested = pyqtSignal()

    def __init__(self, title: str, theme: dict,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self._theme = theme if isinstance(theme, dict) else {}
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        self.back_btn = QToolButton()
        self.back_btn.setObjectName("ViewToggle")
        self.back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_btn.setToolTip("Back")
        self.back_btn.setFixedSize(34, 30)
        self.back_btn.clicked.connect(self.back_requested.emit)
        lay.addWidget(self.back_btn)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("PageSubtitle")
        self.title_label.setWordWrap(True)
        lay.addWidget(self.title_label, 1)
        self.set_theme(self._theme)

    def set_title(self, title: str) -> None:
        """Update the detail title."""
        self.title_label.setText(title)

    def set_theme(self, theme: dict) -> None:
        """Re-render the back chevron."""
        self._theme = theme if isinstance(theme, dict) else {}
        self.back_btn.setIcon(QIcon(render_icon(
            Icon.CHEVRON_LEFT, 18,
            self._theme.get("muted", "#979ca6"), get_dpr())))
        self.back_btn.setStyleSheet(f"""
            QToolButton#ViewToggle {{
                background: transparent;
                border: 1px solid {self._theme.get('border', '#2c2d33')};
                border-radius: 8px;
            }}
            QToolButton#ViewToggle:hover {{
                background: {self._theme.get('panel_bg_2', '#1a1b1f')};
            }}
        """)


def _gold_button(text: str, glyph: str, theme: dict) -> QPushButton:
    """Small GoldBtn with icon + label."""
    btn = QPushButton(text)
    btn.setObjectName("GoldBtn")
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setIcon(QIcon(render_icon(glyph, 16, "#141414", get_dpr())))
    return btn


def _ghost_button(text: str, glyph: str, theme: dict) -> QPushButton:
    """GhostBtn with icon + label."""
    btn = QPushButton(text)
    btn.setObjectName("GhostBtn")
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setIcon(QIcon(render_icon(
        glyph, 16, theme.get("text", "#f4f4f6"), get_dpr())))
    return btn


def _request_cover(mw: Any, path: str, cb: Callable[[str, QPixmap | None], None]) -> None:
    """Ask mw for a cover asynchronously (guarded)."""
    if not path:
        cb(path, None)
        return
    loader = _g(mw, "_load_cover_async")
    if callable(loader):
        try:
            loader(path, cb)
            return
        except Exception:
            _LOG.debug("_load_cover_async failed", exc_info=True)
    cb(path, None)


# ---------------------------------------------------------------------------
# 3. AlbumsPage
# ---------------------------------------------------------------------------
class AlbumsPage(QWidget):
    """Album browser: grid / compact list / album detail.

    Public API:
        refresh()              reload albums (progressive build)
        set_theme(theme)       restyle
        mode()                 'grid' | 'list'
        albums()               album group dicts (with ``key``)
    Testability hooks (public-ish):
        _set_mode(mode) _apply_search(text) _open_album(key)
        _play_detail_row(row) _play_album() _shuffle_album()
    """

    def __init__(self, mw: Any, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Root")
        self._mw = mw
        self._theme = _theme_from(mw)
        self._src = _LibrarySource(mw)
        prefs = _prefs_from(mw)

        self._albums: list[dict] = []
        self._filtered: list[dict] = []
        self._covers: dict[str, QPixmap | None] = {}
        self._requested: set[str] = set()
        self._detail_tracks: list[dict] = []
        self._detail_key = ""
        self._sort = "Title"
        self._show_covers_in_list = bool(prefs.get("show_covers_in_list", True))

        self._builder = _ChunkBuilder()

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        self._header = _PageHeader(
            "Albums", self._theme,
            modes=(("grid", Icon.GRID, "Album grid"),
                   ("list", Icon.ROWS, "Album list")),
            sorts=("Title", "Artist", "Tracks"),
            search_hint="Search albums…")
        self._header.mode_changed.connect(self._set_mode)
        self._header.sort_changed.connect(self._on_sort_changed)
        self._header.search_changed.connect(self._apply_search)
        root.addWidget(self._header)

        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)

        # -- grid mode -----------------------------------------------------
        self._grid = QListWidget()
        self._grid.setObjectName("AlbumGrid")
        self._grid.setViewMode(QListView.ViewMode.IconMode)
        self._grid.setGridSize(QSize(176, 216))
        self._grid.setIconSize(QSize(150, 150))
        self._grid.setUniformItemSizes(True)
        self._grid.setResizeMode(QListView.ResizeMode.Adjust)
        self._grid.setMovement(QListView.Movement.Static)
        self._grid.setWrapping(True)
        self._grid.setWordWrap(True)
        self._grid.setSpacing(10)
        self._grid.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._grid.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._grid.setMouseTracking(True)
        self._grid.itemActivated.connect(self._on_grid_activated)
        self._grid.itemDoubleClicked.connect(self._on_grid_activated)
        self._grid.verticalScrollBar().valueChanged.connect(
            lambda _v: self._request_visible_covers())
        self._stack.addWidget(self._grid)

        # -- list mode -------------------------------------------------------
        self._album_list = QListWidget()
        self._album_list.setObjectName("AlbumList")
        self._album_list.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._album_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self._album_list.setUniformItemSizes(False)
        self._album_list.setSpacing(2)
        self._album_list.itemActivated.connect(self._on_list_activated)
        self._album_list.itemDoubleClicked.connect(self._on_list_activated)
        self._album_list.verticalScrollBar().valueChanged.connect(
            lambda _v: self._request_visible_covers())
        self._stack.addWidget(self._album_list)

        # -- detail -----------------------------------------------------------
        self._detail = QWidget()
        d_root = QVBoxLayout(self._detail)
        d_root.setContentsMargins(0, 6, 0, 0)
        d_root.setSpacing(12)

        self._detail_header = _DetailHeader("", self._theme)
        self._detail_header.back_requested.connect(self._back_from_detail)
        d_root.addWidget(self._detail_header)

        hero = QHBoxLayout()
        hero.setSpacing(16)
        self._detail_cover = _CoverTile(220, 18, Icon.ALBUMS)
        hero.addWidget(self._detail_cover, 0,
                       Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeading)

        info = QVBoxLayout()
        info.setSpacing(10)
        self._detail_title = QLabel("")
        self._detail_title.setObjectName("PageTitle")
        self._detail_title.setWordWrap(True)
        self._detail_meta = QLabel("")
        self._detail_meta.setObjectName("PageSubtitle")
        self._detail_meta.setWordWrap(True)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self._play_all_btn = _gold_button("Play All", Icon.PLAY, self._theme)
        self._shuffle_btn = _ghost_button("Shuffle", Icon.SHUFFLE, self._theme)
        self._play_all_btn.clicked.connect(self._play_album)
        self._shuffle_btn.clicked.connect(self._shuffle_album)
        buttons.addWidget(self._play_all_btn)
        buttons.addWidget(self._shuffle_btn)
        buttons.addStretch(1)
        info.addWidget(self._detail_title)
        info.addWidget(self._detail_meta)
        info.addLayout(buttons)
        info.addStretch(1)
        hero.addLayout(info, 1)
        d_root.addLayout(hero)

        self._detail_list = _DetailTrackList(self._theme)
        self._detail_list.row_activated.connect(self._play_detail_row)
        d_root.addWidget(self._detail_list, 1)
        self._stack.addWidget(self._detail)

        # -- empty state --------------------------------------------------------
        self._empty = make_empty_state(
            "No albums yet", "Add a music folder to build your library.",
            Icon.ALBUMS)
        self._stack.addWidget(self._empty)

        self._set_mode(str(prefs.get("list_view", "grid")) or "grid")

    # -- public API -----------------------------------------------------------
    def refresh(self) -> None:
        """Reload album groups and rebuild the active view progressively."""
        self._albums = self._src.albums()
        self._covers.clear()
        self._requested.clear()
        self._apply_search(self._header.search.text())

    def set_theme(self, theme: dict) -> None:
        """Restyle the whole page for *theme*."""
        self._theme = theme if isinstance(theme, dict) else {}
        self._header.set_theme(self._theme)
        self._detail_header.set_theme(self._theme)
        self._detail_list.set_theme(self._theme)
        self._detail_cover.set_theme(self._theme)
        for widget in (self._play_all_btn, self._shuffle_btn):
            glyph = Icon.PLAY if widget is self._play_all_btn else Icon.SHUFFLE
            color = "#141414" if widget is self._play_all_btn \
                else self._theme.get("text", "#f4f4f6")
            widget.setIcon(QIcon(render_icon(glyph, 16, color, get_dpr())))
        if isinstance(self._empty, _FallbackEmptyState):
            self._empty.set_theme(self._theme)
        else:
            _theme_empty_state(self._empty, self._theme)
        # repaint covers that already arrived keep their pixmaps; only the
        # placeholder gradients change, handled inside the tiles.

    def mode(self) -> str:
        """Current view mode: 'grid' or 'list'."""
        return self._header.mode()

    def albums(self) -> list[dict]:
        """All album groups from the last refresh."""
        return list(self._albums)

    # -- public-ish internals (test hooks) --------------------------------------
    def _set_mode(self, mode_id: str) -> None:
        mode_id = mode_id if mode_id in ("grid", "list") else "grid"
        if self._header.mode() != mode_id:
            self._header.set_mode(mode_id)
            return  # mode_changed re-enters _set_mode with the new mode
        self._rebuild()

    def _apply_search(self, text: str) -> None:
        """Filter albums by *text* (case-insensitive over album + artist)
        and rebuild the visible list."""
        query = (text or "").strip().casefold()
        albums = self._albums
        if query:
            self._filtered = [
                a for a in albums
                if query in str(a.get("album", "")).casefold()
                or query in str(a.get("artist", "")).casefold()]
        else:
            self._filtered = list(albums)
        self._sort_albums()
        self._rebuild()

    def _open_album(self, key: str) -> None:
        """Open the album detail view for group *key* (album\x1fartist)."""
        album, artist = _split_album_key(key)
        tracks = self._src.album_tracks(album, artist)
        if not tracks:
            _call(self._mw, "_show_toast", "Album has no tracks",
                  default=None)
            return
        self._detail_key = key
        self._detail_tracks = tracks
        self._detail_header.set_title(album or "Unknown Album")
        self._detail_title.setText(album or "Unknown Album")
        n = len(tracks)
        dur = sum(int(t.get("duration_ms") or 0) for t in tracks)
        artist_txt = artist or "Unknown Artist"
        self._detail_meta.setText(
            f"{artist_txt}  ·  {n} track{'s' if n != 1 else ''}"
            + (f"  ·  {format_duration_ms(dur)}" if dur else ""))
        self._detail_list.set_tracks(tracks)
        self._detail_cover.set_pixmap(self._covers.get(key))
        if key not in self._requested:
            self._requested.add(key)
            rep = next((a.get("rep") for a in self._albums if a["key"] == key),
                       tracks[0].get("path", ""))
            _request_cover(self._mw, str(rep), self._on_detail_cover)
        self._stack.setCurrentWidget(self._detail)
        _anim_fade(self._detail)

    def _play_detail_row(self, row: int) -> None:
        """Play detail row *row* (double-click hook)."""
        paths = [str(t.get("path", "")) for t in self._detail_tracks]
        if 0 <= row < len(paths):
            _call(self._mw, "_play_from_list", paths, row, self, default=None)

    def _play_album(self) -> None:
        """Play All in the open detail view."""
        paths = [str(t.get("path", "")) for t in self._detail_tracks]
        if paths:
            _call(self._mw, "_play_from_list", paths, 0, self, default=None)

    def _shuffle_album(self) -> None:
        """Shuffle-play the open detail view."""
        paths = [str(t.get("path", "")) for t in self._detail_tracks]
        if paths:
            random.shuffle(paths)
            _call(self._mw, "_play_from_list", paths, 0, self, default=None)

    def _back_from_detail(self) -> None:
        """Leave the detail view back to grid/list."""
        self._stack.setCurrentWidget(
            self._grid if self.mode() == "grid" else self._album_list)

    def _grid_item_count(self) -> int:
        """Items in the ACTIVE list (test hook)."""
        return (self._grid.count() if self.mode() == "grid"
                else self._album_list.count())

    # -- internals --------------------------------------------------------------
    def _on_sort_changed(self, label: str) -> None:
        self._sort = label or "Title"
        self._sort_albums()
        self._rebuild()

    def _sort_albums(self) -> None:
        if self._sort == "Artist":
            key = lambda a: (str(a.get("artist", "")).casefold(),   # noqa: E731
                             str(a.get("album", "")).casefold())
        elif self._sort == "Tracks":
            key = lambda a: (-int(a.get("n", 0)),                   # noqa: E731
                             str(a.get("album", "")).casefold())
        else:
            key = lambda a: str(a.get("album", "")).casefold()     # noqa: E731
        self._filtered.sort(key=key)

    def _rebuild(self) -> None:
        self._builder.cancel()
        self._grid.clear()
        self._album_list.clear()
        if not self._filtered:
            self._stack.setCurrentWidget(self._empty)
            _theme_empty_state(self._empty, self._theme)
            return
        target = self._grid if self.mode() == "grid" else self._album_list
        self._stack.setCurrentWidget(target)

        def add_range(pos: int, nxt: int) -> None:
            if self.mode() == "grid":
                for i in range(pos, nxt):
                    self._append_grid_item(self._filtered[i])
            else:
                for i in range(pos, nxt):
                    self._append_list_row(self._filtered[i])
            self._request_visible_covers()

        self._builder.start(lambda: len(self._filtered), add_range)

    def _append_grid_item(self, album: dict) -> None:
        key = str(album.get("key", ""))
        name = str(album.get("album", "")) or "Unknown Album"
        artist = str(album.get("artist", "")) or "Unknown Artist"
        n = int(album.get("n", 0))
        item = QListWidgetItem()
        item.setText(f"{name}\n{artist} · {n} track"
                     f"{'s' if n != 1 else ''}")
        item.setData(Qt.ItemDataRole.UserRole, key)
        item.setTextAlignment(int(Qt.AlignmentFlag.AlignHCenter))
        pm = self._covers.get(key)
        if pm is not None:
            item.setIcon(QIcon(pm))
        else:
            item.setIcon(QIcon(render_icon(
                Icon.ALBUMS, 150, self._theme.get("muted", "#979ca6"),
                get_dpr())))
        self._grid.addItem(item)

    def _append_list_row(self, album: dict) -> None:
        key = str(album.get("key", ""))
        name = str(album.get("album", "")) or "Unknown Album"
        artist = str(album.get("artist", "")) or "Unknown Artist"
        n = int(album.get("n", 0))
        dur = format_duration_ms(int(album.get("dur", 0) or 0))
        parts = [f"{name} — {artist}", f"{n} track{'s' if n != 1 else ''}"]
        if dur:
            parts.append(dur)
        item = QListWidgetItem("  ·  ".join(parts))
        item.setData(Qt.ItemDataRole.UserRole, key)
        item.setSizeHint(QSize(0, 52))
        pm = self._covers.get(key)
        if pm is not None:
            thumb = pm.scaled(44, 44, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                              Qt.TransformationMode.SmoothTransformation)
            item.setIcon(QIcon(thumb))
        elif self._show_covers_in_list:
            item.setIcon(QIcon(render_icon(
                Icon.ALBUMS, 44, self._theme.get("muted", "#979ca6"),
                get_dpr())))
        self._album_list.addItem(item)

    def _on_grid_activated(self, item: QListWidgetItem) -> None:
        key = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if key:
            self._open_album(key)

    def _on_list_activated(self, item: QListWidgetItem) -> None:
        key = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if key:
            self._open_album(key)

    # -- covers --------------------------------------------------------------
    def _active_list(self) -> QListWidget:
        return self._grid if self.mode() == "grid" else self._album_list

    def _visible_rows(self, lst: QListWidget) -> range:
        """Viewport row range, or the first _COVER_REQUEST_CAP rows when the
        list is not on screen (offscreen tests, pre-show refreshes)."""
        if lst.count() <= 0:
            return range(0)
        try:
            if lst.isVisible():
                viewport = lst.viewport()
                first = lst.indexAt(QPoint(0, 0)).row()
                last = lst.indexAt(QPoint(0, max(1, viewport.height() - 1))).row()
                if first >= 0:
                    if last < 0:
                        last = lst.count() - 1
                    return range(first, min(last + 1, lst.count()))
        except Exception:
            _LOG.debug("visible-row probe failed", exc_info=True)
        return range(0, min(lst.count(), _COVER_REQUEST_CAP))

    def _request_visible_covers(self) -> None:
        lst = self._active_list()
        for row in self._visible_rows(lst):
            item = lst.item(row)
            if item is None:
                continue
            key = str(item.data(Qt.ItemDataRole.UserRole) or "")
            if not key or key in self._requested:
                continue
            album = next((a for a in self._filtered if a.get("key") == key), None)
            if album is None:
                continue
            rep = str(album.get("rep", ""))
            if not rep:
                continue
            self._requested.add(key)
            _request_cover(self._mw, rep, self._on_cover_arrived)

    def _on_cover_arrived(self, path: str, pm: QPixmap | None) -> None:
        """Cover callback: cache + patch the matching item's icon directly
        (no model reset — grid stays responsive)."""
        for album in self._filtered:
            if str(album.get("rep", "")) == path:
                key = str(album.get("key", ""))
                if key in self._covers:
                    return  # first callback wins
                self._covers[key] = pm
                self._patch_item_icon(key, pm)
                if self._detail_key == key:
                    self._detail_cover.set_pixmap(pm)
                return

    def _patch_item_icon(self, key: str, pm: QPixmap | None) -> None:
        lst = self._active_list()
        for row in range(lst.count()):
            item = lst.item(row)
            if item is None:
                continue
            if str(item.data(Qt.ItemDataRole.UserRole) or "") == key:
                if pm is None:
                    continue
                if lst is self._grid:
                    item.setIcon(QIcon(pm))
                else:
                    thumb = pm.scaled(
                        44, 44, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation)
                    item.setIcon(QIcon(thumb))
                return

    def _on_detail_cover(self, path: str, pm: QPixmap | None) -> None:
        rep = next((str(a.get("rep", "")) for a in self._albums
                    if a.get("key") == self._detail_key), "")
        if not rep and self._detail_tracks:
            rep = str(self._detail_tracks[0].get("path", ""))
        if rep == path:
            self._covers[self._detail_key] = pm
            self._detail_cover.set_pixmap(pm)


# ---------------------------------------------------------------------------
# 4. ArtistsPage
# ---------------------------------------------------------------------------
class _ArtistCard(QFrame):
    """One artist card: avatar + name + caption; emits ``clicked(str)``."""

    clicked = pyqtSignal(str)

    CARD_WIDTH = 168

    def __init__(self, artist: str, count: int, plays: int | None,
                 theme: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self._theme = theme if isinstance(theme, dict) else {}
        self._artist = artist
        self.setFixedWidth(self.CARD_WIDTH)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 12, 10, 12)
        lay.setSpacing(6)
        self.avatar = _AvatarBubble(96)
        self.avatar.set_name(artist)
        self.name = QLabel(artist)
        self.name.setWordWrap(True)
        self.name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        caption = f"{count} track{'s' if count != 1 else ''}"
        if plays:
            caption += f" · {plays} play{'s' if plays != 1 else ''}"
        self.caption = QLabel(caption)
        self.caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.caption.setWordWrap(True)
        lay.addWidget(self.avatar, 0, Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.name, 0, Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.caption, 0, Qt.AlignmentFlag.AlignCenter)
        self.apply_theme(theme)

    def apply_theme(self, theme: dict) -> None:
        """Restyle the card frame + labels."""
        self._theme = theme if isinstance(theme, dict) else {}
        self.avatar.set_theme(self._theme)
        self.setStyleSheet(f"""
            QFrame#ArtistCard {{
                background: {self._theme.get('panel_bg', '#15110a')};
                border: 1px solid {self._theme.get('border', '#372d1c')};
                border-radius: 12px;
            }}
            QFrame#ArtistCard:hover {{
                border-color: {self._theme.get('gold', '#d4a85a')};
            }}
        """)
        self.name.setStyleSheet(
            f"background: transparent; color: {self._theme.get('text', '#f4f4f6')};"
            f" font-weight: 700;")
        self.caption.setStyleSheet(
            f"background: transparent; color: {self._theme.get('muted', '#979ca6')};")

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt API)
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._artist)
        super().mousePressEvent(event)


class ArtistsPage(QWidget):
    """Artist browser: grid cards / list rows / artist detail.

    Public API: ``refresh()``, ``set_theme(theme)``, ``mode()``,
    ``artists()``. Test hooks: ``_open_artist(name)``, ``_play_artist_row(row)``,
    ``_set_mode(mode)``.
    """

    _CARD_COLUMNS = 4

    def __init__(self, mw: Any, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Root")
        self._mw = mw
        self._theme = _theme_from(mw)
        self._src = _LibrarySource(mw)

        self._artists: list[tuple[str, int]] = []
        self._filtered: list[tuple[str, int]] = []
        self._plays: dict[str, int] = {}
        self._reps: dict[str, str] = {}
        self._covers: dict[str, QPixmap | None] = {}
        self._requested: set[str] = set()
        self._detail_artist = ""
        self._detail_tracks: list[dict] = []
        self._builder = _ChunkBuilder()
        self._cards: list[_ArtistCard] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        self._header = _PageHeader(
            "Artists", self._theme,
            modes=(("grid", Icon.GRID, "Artist cards"),
                   ("list", Icon.ROWS, "Artist list")),
            search_hint="Search artists…")
        self._header.mode_changed.connect(self._set_mode)
        self._header.search_changed.connect(self._on_search)
        root.addWidget(self._header)

        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)

        # -- cards grid (scrollable) ----------------------------------------
        self._cards_host = QWidget()
        self._cards_grid = QGridLayout(self._cards_host)
        self._cards_grid.setContentsMargins(0, 0, 0, 0)
        self._cards_grid.setSpacing(14)
        cards_scroll = QScrollArea()
        cards_scroll.setWidgetResizable(True)
        cards_scroll.setWidget(self._cards_host)
        cards_scroll.setObjectName("CardsScroll")
        self._stack.addWidget(cards_scroll)

        # -- list mode ----------------------------------------------------------
        self._artist_list = QListWidget()
        self._artist_list.setObjectName("ArtistList")
        self._artist_list.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._artist_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self._artist_list.setSpacing(2)
        self._artist_list.itemActivated.connect(self._on_list_activated)
        self._artist_list.itemDoubleClicked.connect(self._on_list_activated)
        self._stack.addWidget(self._artist_list)

        # -- detail ------------------------------------------------------------
        self._detail = QWidget()
        d_root = QVBoxLayout(self._detail)
        d_root.setContentsMargins(0, 6, 0, 0)
        d_root.setSpacing(12)

        self._detail_header = _DetailHeader("", self._theme)
        self._detail_header.back_requested.connect(self._back_from_detail)
        d_root.addWidget(self._detail_header)

        hero = QHBoxLayout()
        hero.setSpacing(16)
        self._avatar = _AvatarBubble(96)
        hero.addWidget(self._avatar, 0,
                       Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeading)
        info = QVBoxLayout()
        info.setSpacing(10)
        self._detail_title = QLabel("")
        self._detail_title.setObjectName("PageTitle")
        self._detail_meta = QLabel("")
        self._detail_meta.setObjectName("PageSubtitle")
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self._play_all_btn = _gold_button("Play All", Icon.PLAY, self._theme)
        self._shuffle_btn = _ghost_button("Shuffle", Icon.SHUFFLE, self._theme)
        self._play_all_btn.clicked.connect(self._play_artist)
        self._shuffle_btn.clicked.connect(self._shuffle_artist)
        buttons.addWidget(self._play_all_btn)
        buttons.addWidget(self._shuffle_btn)
        buttons.addStretch(1)
        info.addWidget(self._detail_title)
        info.addWidget(self._detail_meta)
        info.addLayout(buttons)
        info.addStretch(1)
        hero.addLayout(info, 1)
        d_root.addLayout(hero)

        self._detail_list = _DetailTrackList(self._theme)
        self._detail_list.row_activated.connect(self._play_artist_row)
        d_root.addWidget(self._detail_list, 1)
        self._stack.addWidget(self._detail)

        # -- empty ------------------------------------------------------------
        self._empty = make_empty_state(
            "No artists yet", "Imported tracks will list their artists here.",
            Icon.USERS)
        self._stack.addWidget(self._empty)

        self._set_mode("grid")

    # -- public API ------------------------------------------------------------
    def refresh(self) -> None:
        """Reload artists + plays and rebuild the active view."""
        self._artists = self._src.artists()
        self._plays = self._src.artist_plays()
        self._reps = self._src.artist_reps()
        self._covers.clear()
        self._requested.clear()
        self._on_search(self._header.search.text())

    def set_theme(self, theme: dict) -> None:
        """Restyle for *theme*."""
        self._theme = theme if isinstance(theme, dict) else {}
        self._header.set_theme(self._theme)
        self._detail_header.set_theme(self._theme)
        self._detail_list.set_theme(self._theme)
        self._avatar.set_theme(self._theme)
        for card in self._cards:
            card.apply_theme(self._theme)
        for widget, glyph in ((self._play_all_btn, Icon.PLAY),
                              (self._shuffle_btn, Icon.SHUFFLE)):
            color = "#141414" if widget is self._play_all_btn \
                else self._theme.get("text", "#f4f4f6")
            widget.setIcon(QIcon(render_icon(glyph, 16, color, get_dpr())))
        if isinstance(self._empty, _FallbackEmptyState):
            self._empty.set_theme(self._theme)
        else:
            _theme_empty_state(self._empty, self._theme)

    def mode(self) -> str:
        """Current view mode: 'grid' (cards) or 'list' (rows)."""
        return self._header.mode()

    def artists(self) -> list[tuple[str, int]]:
        """(artist, track count) list from the last refresh."""
        return list(self._artists)

    # -- test hooks --------------------------------------------------------------
    def _set_mode(self, mode_id: str) -> None:
        mode_id = mode_id if mode_id in ("grid", "list") else "grid"
        if self._header.mode() != mode_id:
            self._header.set_mode(mode_id)
            return
        self._rebuild()

    def _open_artist(self, artist: str) -> None:
        """Open the artist detail view."""
        tracks = self._src.artist_tracks(artist)
        if not tracks:
            _call(self._mw, "_show_toast", "Artist has no tracks", default=None)
            return
        self._detail_artist = artist
        self._detail_tracks = tracks
        self._detail_header.set_title(artist)
        self._detail_title.setText(artist)
        plays = self._plays.get(artist)
        meta = f"{len(tracks)} track{'s' if len(tracks) != 1 else ''}"
        if plays:
            meta += f" · {plays} play{'s' if plays != 1 else ''}"
        self._detail_meta.setText(meta)
        self._avatar.set_name(artist)
        self._avatar.set_pixmap(self._covers.get(artist))
        self._detail_list.set_tracks(tracks)
        if artist not in self._requested:
            self._requested.add(artist)
            _request_cover(self._mw, self._reps.get(artist, ""),
                           self._on_detail_cover)
        self._stack.setCurrentWidget(self._detail)
        _anim_fade(self._detail)

    def _play_artist_row(self, row: int) -> None:
        """Play detail row *row*."""
        paths = [str(t.get("path", "")) for t in self._detail_tracks]
        if 0 <= row < len(paths):
            _call(self._mw, "_play_from_list", paths, row, self, default=None)

    def _play_artist(self) -> None:
        """Play All for the open artist."""
        paths = [str(t.get("path", "")) for t in self._detail_tracks]
        if paths:
            _call(self._mw, "_play_from_list", paths, 0, self, default=None)

    def _shuffle_artist(self) -> None:
        """Shuffle-play the open artist."""
        paths = [str(t.get("path", "")) for t in self._detail_tracks]
        if paths:
            random.shuffle(paths)
            _call(self._mw, "_play_from_list", paths, 0, self, default=None)

    def _back_from_detail(self) -> None:
        """Return from detail to the browse view."""
        if self.mode() == "grid":
            self._stack.setCurrentIndex(0)
        else:
            self._stack.setCurrentWidget(self._artist_list)

    def _card_count(self) -> int:
        """Number of artist cards currently built (test hook)."""
        return len(self._cards)

    # -- internals ------------------------------------------------------------------
    def _on_search(self, text: str) -> None:
        query = (text or "").strip().casefold()
        if query:
            self._filtered = [(a, n) for a, n in self._artists
                              if query in a.casefold()]
        else:
            self._filtered = list(self._artists)
        self._rebuild()

    def _rebuild(self) -> None:
        self._builder.cancel()
        self._cards = []
        while self._cards_grid.count():
            item = self._cards_grid.takeAt(0)
            widget = item.widget() if item else None
            if widget is not None:
                widget.deleteLater()
        self._artist_list.clear()
        if not self._filtered:
            self._stack.setCurrentWidget(self._empty)
            _theme_empty_state(self._empty, self._theme)
            return
        self._stack.setCurrentIndex(0 if self.mode() == "grid" else 1)

        def add_range(pos: int, nxt: int) -> None:
            if self.mode() == "grid":
                for i in range(pos, nxt):
                    self._append_card(self._filtered[i])
                self._request_card_covers(pos, nxt)
            else:
                for i in range(pos, nxt):
                    self._append_list_row(self._filtered[i])

        self._builder.start(lambda: len(self._filtered), add_range)

    def _append_card(self, pair: tuple[str, int]) -> None:
        artist, count = pair
        card = _ArtistCard(artist, count, self._plays.get(artist),
                           self._theme)
        card.clicked.connect(self._open_artist)
        idx = len(self._cards)
        self._cards.append(card)
        self._cards_grid.addWidget(
            card, idx // self._CARD_COLUMNS, idx % self._CARD_COLUMNS)

    def _append_list_row(self, pair: tuple[str, int]) -> None:
        artist, count = pair
        plays = self._plays.get(artist)
        text = f"{artist} — {count} track{'s' if count != 1 else ''}"
        if plays:
            text += f" · {plays} play{'s' if plays != 1 else ''}"
        item = QListWidgetItem(text)
        item.setData(Qt.ItemDataRole.UserRole, artist)
        item.setSizeHint(QSize(0, 44))
        pm = self._covers.get(artist)
        if pm is not None:
            thumb = pm.scaled(36, 36,
                              Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                              Qt.TransformationMode.SmoothTransformation)
            item.setIcon(QIcon(thumb))
        else:
            item.setIcon(QIcon(render_icon(
                Icon.USER, 28, self._theme.get("muted", "#979ca6"),
                get_dpr())))
        self._artist_list.addItem(item)

    def _on_list_activated(self, item: QListWidgetItem) -> None:
        artist = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if artist:
            self._open_artist(artist)

    def _request_card_covers(self, pos: int, nxt: int) -> None:
        for i in range(pos, nxt):
            if i >= _COVER_REQUEST_CAP:
                break  # documented cap: beyond 200 cards, covers load on use
            artist = self._filtered[i][0]
            if artist in self._requested:
                continue
            rep = self._reps.get(artist, "")
            if not rep:
                continue
            self._requested.add(artist)
            _request_cover(self._mw, rep, self._on_cover_arrived)

    def _on_cover_arrived(self, path: str, pm: QPixmap | None) -> None:
        for artist, rep in self._reps.items():
            if rep == path:
                if artist in self._covers:
                    return
                self._covers[artist] = pm
                for card in self._cards:
                    if card._artist == artist:  # noqa: SLF001 (same module)
                        card.avatar.set_pixmap(pm)
                if self._detail_artist == artist:
                    self._avatar.set_pixmap(pm)
                return

    def _on_detail_cover(self, path: str, pm: QPixmap | None) -> None:
        rep = self._reps.get(self._detail_artist, "")
        if rep == path:
            self._covers[self._detail_artist] = pm
            self._avatar.set_pixmap(pm)


# ---------------------------------------------------------------------------
# 5. GenresPage
# ---------------------------------------------------------------------------
class GenresPage(QWidget):
    """Genre browser: wrapping chip flow + genre detail view.

    Public API: ``refresh()``, ``set_theme(theme)``, ``genres()``.
    Test hooks: ``_open_genre(name)``, ``_play_genre_row(row)``,
    ``_genre_labels()``.
    """

    def __init__(self, mw: Any, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Root")
        self._mw = mw
        self._theme = _theme_from(mw)
        self._src = _LibrarySource(mw)

        self._genres: list[tuple[str, int]] = []
        self._sort = "Count"
        self._search = ""
        self._detail_genre = ""
        self._detail_tracks: list[dict] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        self._header = _PageHeader(
            "Genres", self._theme,
            sorts=("Name", "Count"),
            search_hint="Search genres…")
        self._header.sort_changed.connect(self._on_sort_changed)
        self._header.search_changed.connect(self._on_search)
        root.addWidget(self._header)

        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)

        # -- chip flow -------------------------------------------------------
        self._chips_host = QWidget()
        self._flow = FlowLayout(self._chips_host, margin=0, spacing=8)
        chips_scroll = QScrollArea()
        chips_scroll.setWidgetResizable(True)
        chips_scroll.setWidget(self._chips_host)
        self._stack.addWidget(chips_scroll)

        # -- detail ------------------------------------------------------------
        self._detail = QWidget()
        d_root = QVBoxLayout(self._detail)
        d_root.setContentsMargins(0, 6, 0, 0)
        d_root.setSpacing(12)
        self._detail_header = _DetailHeader("", self._theme)
        self._detail_header.back_requested.connect(self._back_from_detail)
        d_root.addWidget(self._detail_header)

        top = QHBoxLayout()
        top.setSpacing(12)
        title_box = QVBoxLayout()
        title_box.setSpacing(4)
        self._detail_title = QLabel("")
        self._detail_title.setObjectName("PageTitle")
        self._detail_meta = QLabel("")
        self._detail_meta.setObjectName("PageSubtitle")
        title_box.addWidget(self._detail_title)
        title_box.addWidget(self._detail_meta)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self._play_all_btn = _gold_button("Play All", Icon.PLAY, self._theme)
        self._shuffle_btn = _ghost_button("Shuffle", Icon.SHUFFLE, self._theme)
        self._play_all_btn.clicked.connect(self._play_genre)
        self._shuffle_btn.clicked.connect(self._shuffle_genre)
        buttons.addWidget(self._play_all_btn)
        buttons.addWidget(self._shuffle_btn)
        buttons.addStretch(1)
        top.addLayout(title_box, 1)
        top.addLayout(buttons)
        d_root.addLayout(top)

        self._detail_list = _DetailTrackList(self._theme)
        self._detail_list.row_activated.connect(self._play_genre_row)
        d_root.addWidget(self._detail_list, 1)
        self._stack.addWidget(self._detail)

        # -- empty -----------------------------------------------------------
        self._empty = make_empty_state(
            "No genres yet", "Genres appear once your tracks carry tags.",
            Icon.TAG)
        self._stack.addWidget(self._empty)

    # -- public API ------------------------------------------------------------
    def refresh(self) -> None:
        """Reload genre counts and rebuild the chip flow."""
        self._genres = self._src.genres()
        self._rebuild_chips()

    def set_theme(self, theme: dict) -> None:
        """Restyle for *theme*."""
        self._theme = theme if isinstance(theme, dict) else {}
        self._header.set_theme(self._theme)
        self._detail_header.set_theme(self._theme)
        self._detail_list.set_theme(self._theme)
        for widget, glyph in ((self._play_all_btn, Icon.PLAY),
                              (self._shuffle_btn, Icon.SHUFFLE)):
            color = "#141414" if widget is self._play_all_btn \
                else self._theme.get("text", "#f4f4f6")
            widget.setIcon(QIcon(render_icon(glyph, 16, color, get_dpr())))
        if isinstance(self._empty, _FallbackEmptyState):
            self._empty.set_theme(self._theme)
        else:
            _theme_empty_state(self._empty, self._theme)
        self._restyle_chips()

    def genres(self) -> list[tuple[str, int]]:
        """(genre, track count) list from the last refresh."""
        return list(self._genres)

    # -- test hooks -----------------------------------------------------------
    def _genre_labels(self) -> list[str]:
        """Chip texts in display order."""
        return [self._chip_text(g, n) for g, n in self._sorted_filtered()]

    def _open_genre(self, genre: str) -> None:
        """Open the genre detail view."""
        tracks = self._src.genre_tracks(genre)
        if not tracks:
            _call(self._mw, "_show_toast", "Genre has no tracks", default=None)
            return
        self._detail_genre = genre
        self._detail_tracks = tracks
        self._detail_header.set_title(genre)
        self._detail_title.setText(genre)
        n = len(tracks)
        dur = sum(int(t.get("duration_ms") or 0) for t in tracks)
        meta = f"{n} track{'s' if n != 1 else ''}"
        if dur:
            meta += f" · {format_duration_ms(dur)}"
        self._detail_meta.setText(meta)
        self._detail_list.set_tracks(tracks)
        self._stack.setCurrentWidget(self._detail)
        _anim_fade(self._detail)

    def _play_genre_row(self, row: int) -> None:
        """Play detail row *row*."""
        paths = [str(t.get("path", "")) for t in self._detail_tracks]
        if 0 <= row < len(paths):
            _call(self._mw, "_play_from_list", paths, row, self, default=None)

    def _play_genre(self) -> None:
        """Play All in the open genre."""
        paths = [str(t.get("path", "")) for t in self._detail_tracks]
        if paths:
            _call(self._mw, "_play_from_list", paths, 0, self, default=None)

    def _shuffle_genre(self) -> None:
        """Shuffle-play the open genre."""
        paths = [str(t.get("path", "")) for t in self._detail_tracks]
        if paths:
            random.shuffle(paths)
            _call(self._mw, "_play_from_list", paths, 0, self, default=None)

    def _back_from_detail(self) -> None:
        """Return to the chip flow."""
        self._stack.setCurrentIndex(0)

    # -- internals ------------------------------------------------------------------
    def _on_sort_changed(self, label: str) -> None:
        self._sort = label or "Count"
        self._rebuild_chips()

    def _on_search(self, text: str) -> None:
        self._search = (text or "").strip().casefold()
        self._rebuild_chips()

    def _sorted_filtered(self) -> list[tuple[str, int]]:
        query = getattr(self, "_search", "")
        rows = self._genres
        if query:
            rows = [(g, n) for g, n in rows if query in g.casefold()]
        if self._sort == "Name":
            return sorted(rows, key=lambda kv: kv[0].casefold())
        return sorted(rows, key=lambda kv: (-kv[1], kv[0].casefold()))

    @staticmethod
    def _chip_text(genre: str, count: int) -> str:
        return f"{genre} · {count}"

    def _rebuild_chips(self) -> None:
        self._flow.clear()
        rows = self._sorted_filtered()
        if not rows:
            self._stack.setCurrentWidget(self._empty)
            _theme_empty_state(self._empty, self._theme)
            return
        self._stack.setCurrentIndex(0)
        for genre, count in rows:
            chip = QPushButton(self._chip_text(genre, count))
            chip.setObjectName("GhostBtn")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.clicked.connect(lambda _c=False, g=genre: self._open_genre(g))
            self._flow.addWidget(chip)
        self._restyle_chips()
        _anim_stagger([self._chips_host])

    def _restyle_chips(self) -> None:
        icon = QIcon(render_icon(Icon.TAG, 15,
                                 self._theme.get("gold", "#d4a85a"), get_dpr()))
        for i in range(self._flow.count()):
            item = self._flow.itemAt(i)
            widget = item.widget() if item else None
            if isinstance(widget, QPushButton):
                widget.setIcon(icon)


# ---------------------------------------------------------------------------
# 6. PlaylistsPage3
# ---------------------------------------------------------------------------
_SMART_BUILTINS: list[tuple[str, str]] = [
    ("most_played", "Most Played"),
    ("recently_added", "Recently Added"),
    ("never_played", "Never Played"),
    ("not_heard_recently", "Not Heard Recently"),
    ("top_rated", "Top Rated"),
]


class _SmartRow(QFrame):
    """Smart-playlist row frame; emits ``activated(str key)`` on click."""

    activated = pyqtSignal(str)

    def __init__(self, key: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._key = key
        self.setObjectName("ListRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt API)
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self._key)
        super().mousePressEvent(event)


class PlaylistsPage3(QWidget):
    """Playlists hub: user playlists, smart playlists, m3u import/export, radio.

    Public API: ``refresh()``, ``set_theme(theme)``.
    Test hooks: ``_user_rows``, ``_smart_rows``, ``_radio_rows`` lists and
    ``_play_user_row(i)`` / ``_play_smart_row(i)`` / ``_play_radio_row(i)``.
    """

    def __init__(self, mw: Any, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Root")
        self._mw = mw
        self._theme = _theme_from(mw)
        self._src = _LibrarySource(mw)

        self._user_rows: list[dict] = []
        self._smart_rows: list[dict] = []
        self._radio_rows: list[dict] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(10)
        title = QLabel("Playlists")
        title.setObjectName("PageTitle")
        header.addWidget(title)
        header.addStretch(1)
        self._new_btn = _gold_button("New Playlist", Icon.PLUS, self._theme)
        self._new_btn.clicked.connect(
            lambda: _call(self._mw, "request_new_playlist", default=None))
        header.addWidget(self._new_btn)
        root.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("PlaylistsScroll")
        body = QWidget()
        self._body_lay = QVBoxLayout(body)
        self._body_lay.setContentsMargins(0, 0, 0, 0)
        self._body_lay.setSpacing(14)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        # -- sections: static titles + dynamic rows hosts -------------------
        self._user_section = self._make_section("Your Playlists")
        self._smart_section = self._make_section("Smart Playlists")
        self._tools_section = self._make_section("Import / Export")
        self._radio_section = self._make_section("Radio")
        for section in (self._user_section, self._smart_section,
                        self._tools_section, self._radio_section):
            self._body_lay.addWidget(section)
        self._body_lay.addStretch(1)

        self._user_rows_lay = self._rows_lay(self._user_section)
        self._smart_rows_lay = self._rows_lay(self._smart_section)
        self._radio_rows_lay = self._rows_lay(self._radio_section)

        self._build_static_sections()

    # -- public API -----------------------------------------------------------
    def refresh(self) -> None:
        """Reload user/smart/radio rows."""
        self._load_user_rows()
        self._load_smart_rows()
        self._load_radio_rows()
        self._rebuild_user()
        self._rebuild_smart()
        self._rebuild_radio()

    def set_theme(self, theme: dict) -> None:
        """Restyle for *theme* (sections rebuild cheaply)."""
        self._theme = theme if isinstance(theme, dict) else {}
        for widget, glyph in (
            (self._new_btn, Icon.PLUS),
            (self._import_btn, Icon.DOWNLOAD),
            (self._export_btn, Icon.UPLOAD),
            (self._add_radio_btn, Icon.RADIO),
        ):
            color = "#141414" if widget is self._new_btn \
                else self._theme.get("text", "#f4f4f6")
            widget.setIcon(QIcon(render_icon(glyph, 15, color, get_dpr())))
        self._rebuild_user()
        self._rebuild_smart()
        self._rebuild_radio()

    # -- data loading ------------------------------------------------------------
    def _load_user_rows(self) -> None:
        rows: list[dict] = []
        db = self._src.db
        db_rows: list[dict] = []
        if db is not None and callable(_g(db, "playlists")):
            got = _call(db, "playlists", default=None)
            if isinstance(got, list):
                db_rows = [dict(r) for r in got if isinstance(r, dict)]
        if db_rows:
            for row in db_rows:
                if str(row.get("kind", "user")) != "user":
                    continue
                pid = row.get("id")
                tracks: list[str] = []
                if db is not None and callable(_g(db, "playlist_tracks")):
                    got = _call(db, "playlist_tracks", pid, default=None)
                    if isinstance(got, list):
                        tracks = [str(p) for p in got]
                rows.append({"name": str(row.get("name", "")), "paths": tracks})
        if not rows:
            playlists = _g(self._mw, "playlists")
            if isinstance(playlists, dict):
                for name, paths in playlists.items():
                    track_list = [str(p) for p in paths] if isinstance(
                        paths, (list, tuple)) else []
                    rows.append({"name": str(name), "paths": track_list})
        rows.sort(key=lambda r: r["name"].casefold())
        self._user_rows = rows

    def _load_smart_rows(self) -> None:
        rows: list[dict] = []
        raw = _call(self._mw, "smart_playlist_rows", default=None)
        if isinstance(raw, (list, tuple)) and raw:
            for entry in raw:
                if isinstance(entry, dict):
                    rows.append({
                        "key": str(entry.get("key") or entry.get("id") or ""),
                        "label": str(entry.get("label") or entry.get("name")
                                  or ""),
                        "count": entry.get("count"),
                    })
                elif isinstance(entry, (list, tuple)) and entry:
                    rows.append({"key": str(entry[0]),
                                 "label": str(entry[1] if len(entry) > 1
                                               else entry[0]),
                                 "count": None})
            rows = [r for r in rows if r["key"]]
        else:
            prefs = _prefs_from(self._mw)
            for key, label in _SMART_BUILTINS:
                enabled = bool(prefs.get(f"smart_{key}",
                                         prefs.get(key, False)))
                if not enabled:
                    continue
                rows.append({"key": key, "label": label,
                             "count": self._src.smart_count(key)})
        self._smart_rows = rows

    def _load_radio_rows(self) -> None:
        rows: list[dict] = []
        raw = _call(self._mw, "radio_rows", default=None)
        if isinstance(raw, (list, tuple)):
            for entry in raw:
                if isinstance(entry, dict):
                    rows.append({
                        "id": entry.get("id", entry.get("station_id")),
                        "name": str(entry.get("name") or entry.get("title")
                                   or ""),
                        "url": str(entry.get("url") or ""),
                    })
        self._radio_rows = [r for r in rows if r["name"]]

    def _make_section(self, title: str) -> QWidget:
        """Create a section widget: SectionTitle + (caller-managed) body."""
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        label = QLabel(title)
        label.setObjectName("SectionTitle")
        lay.addWidget(label)
        return host

    @staticmethod
    def _rows_lay(section: QWidget) -> QVBoxLayout:
        """Append a rows-host layout to *section* and return it."""
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        section.layout().addWidget(host)
        return lay

    # -- static sections -----------------------------------------------------------
    def _build_static_sections(self) -> None:
        """Import/export buttons + radio header Add-Station button."""
        tools_lay = self._rows_lay(self._tools_section)
        row = QHBoxLayout()
        row.setSpacing(8)
        self._import_btn = _ghost_button("Import M3U", Icon.DOWNLOAD,
                                         self._theme)
        self._export_btn = _ghost_button("Export M3U", Icon.UPLOAD,
                                         self._theme)
        self._import_btn.clicked.connect(
            lambda: _call(self._mw, "request_m3u_import", default=None))
        self._export_btn.clicked.connect(
            lambda: _call(self._mw, "request_m3u_export", default=None))
        row.addWidget(self._import_btn)
        row.addWidget(self._export_btn)
        row.addStretch(1)
        tools_lay.addLayout(row)

        self._add_radio_btn = _ghost_button("Add Station", Icon.RADIO,
                                            self._theme)
        self._add_radio_btn.clicked.connect(
            lambda: _call(self._mw, "request_add_radio", default=None))
        self._radio_rows_lay.addWidget(self._add_radio_btn)

    def _clear_layout(self, lay: QLayout) -> None:
        while lay.count():
            item = lay.takeAt(0)
            widget = item.widget() if item else None
            sub = item.layout() if item else None
            if widget is not None:
                widget.deleteLater()
            elif sub is not None:
                self._clear_layout(sub)
                sub.deleteLater()

    def _clear_layout_after(self, lay: QLayout, keep: QWidget) -> None:
        """Remove every item of *lay* after the *keep* widget."""
        keep_idx = lay.indexOf(keep)
        if keep_idx < 0:
            self._clear_layout(lay)
            return
        while lay.count() > keep_idx + 1:
            item = lay.takeAt(keep_idx + 1)
            widget = item.widget() if item else None
            sub = item.layout() if item else None
            if widget is not None:
                widget.deleteLater()
            elif sub is not None:
                self._clear_layout(sub)
                sub.deleteLater()

    # -- rebuilds --------------------------------------------------------------
    def _rebuild_user(self) -> None:
        self._clear_layout(self._user_rows_lay)
        if not self._user_rows:
            hint = QLabel("No playlists yet — create one to get started.")
            hint.setObjectName("EmptyHint")
            self._user_rows_lay.addWidget(hint)
            return
        for i, row in enumerate(self._user_rows):
            self._user_rows_lay.addWidget(self._make_user_row(i, row))

    def _make_user_row(self, index: int, row: dict) -> QWidget:
        host = QFrame()
        host.setObjectName("ListRow")
        h = QHBoxLayout(host)
        h.setContentsMargins(10, 8, 10, 8)
        h.setSpacing(10)
        name = QLabel(row["name"])
        name.setStyleSheet(
            f"background: transparent; color: {self._theme.get('text', '#f4f4f6')};"
            f" font-weight: 600;")
        count = QLabel(f"{len(row['paths'])} track"
                       f"{'s' if len(row['paths']) != 1 else ''}")
        count.setObjectName("EmptyHint")
        play = _gold_button("Play", Icon.PLAY, self._theme)
        play.clicked.connect(lambda _c=False, i=index: self._play_user_row(i))
        h.addWidget(name, 1)
        h.addWidget(count)
        h.addWidget(play)
        host.setStyleSheet(f"""
            QFrame#ListRow {{
                background: {self._theme.get('panel_bg', '#15110a')};
                border: 1px solid {self._theme.get('border', '#372d1c')};
                border-radius: 10px;
            }}
        """)
        return host

    def _rebuild_smart(self) -> None:
        self._clear_layout(self._smart_rows_lay)
        if not self._smart_rows:
            hint = QLabel("Enable smart playlists in Settings → Library.")
            hint.setObjectName("EmptyHint")
            self._smart_rows_lay.addWidget(hint)
            return
        for row in self._smart_rows:
            self._smart_rows_lay.addWidget(self._make_smart_row(row))

    def _make_smart_row(self, row: dict) -> QWidget:
        host = _SmartRow(str(row.get("key", "")))
        h = QHBoxLayout(host)
        h.setContentsMargins(10, 8, 10, 8)
        h.setSpacing(10)
        glyph = QLabel()
        glyph.setPixmap(render_icon(Icon.SPARKLES, 18,
                                    self._theme.get("gold", "#d4a85a"),
                                    get_dpr()))
        glyph.setFixedSize(22, 22)
        name = QLabel(str(row.get("label", "")))
        name.setStyleSheet(
            f"background: transparent; color: "
            f"{self._theme.get('text', '#f4f4f6')}; font-weight: 600;")
        count_txt = ""
        if isinstance(row.get("count"), int):
            n = int(row["count"])
            count_txt = f"{n} track{'s' if n != 1 else ''}"
        count = QLabel(count_txt)
        count.setObjectName("EmptyHint")
        h.addWidget(glyph)
        h.addWidget(name, 1)
        h.addWidget(count)
        host.activated.connect(self._activate_smart)
        host.setStyleSheet(f"""
            QFrame#ListRow {{
                background: {self._theme.get('panel_bg', '#15110a')};
                border: 1px solid {self._theme.get('border', '#372d1c')};
                border-radius: 10px;
            }}
            QFrame#ListRow:hover {{
                border-color: {self._theme.get('gold', '#d4a85a')};
            }}
        """)
        return host

    def _rebuild_radio(self) -> None:
        # keep the static Add Station button; rows go below it
        self._clear_layout_after(self._radio_rows_lay, self._add_radio_btn)
        for i, row in enumerate(self._radio_rows):
            self._radio_rows_lay.addWidget(self._make_radio_row(i, row))

    def _make_radio_row(self, index: int, row: dict) -> QWidget:
        host = QFrame()
        host.setObjectName("ListRow")
        h = QHBoxLayout(host)
        h.setContentsMargins(10, 8, 10, 8)
        h.setSpacing(10)
        name = QLabel(row["name"])
        name.setStyleSheet(
            f"background: transparent; color: {self._theme.get('text', '#f4f4f6')};"
            f" font-weight: 600;")
        host_label = _host_of(row.get("url", ""))
        sub = QLabel(host_label or row.get("url", ""))
        sub.setObjectName("EmptyHint")
        play = _gold_button("Play", Icon.PLAY, self._theme)
        play.clicked.connect(lambda _c=False, i=index: self._play_radio_row(i))
        trash = QToolButton()
        trash.setObjectName("ViewToggle")
        trash.setToolTip("Remove station")
        trash.setCursor(Qt.CursorShape.PointingHandCursor)
        trash.setFixedSize(30, 28)
        trash.setIcon(QIcon(render_icon(
            Icon.TRASH, 16, self._theme.get("muted", "#979ca6"), get_dpr())))
        trash.setStyleSheet(f"""
            QToolButton#ViewToggle {{
                background: transparent;
                border: 1px solid {self._theme.get('border', '#372d1c')};
                border-radius: 8px;
            }}
            QToolButton#ViewToggle:hover {{
                background: {self._theme.get('panel_bg_2', '#1a1b1f')};
            }}
        """)
        trash.clicked.connect(lambda _c=False, i=index:
                               self._remove_radio_row(i))
        h.addWidget(name)
        h.addWidget(sub, 1)
        h.addWidget(play)
        h.addWidget(trash)
        host.setStyleSheet(f"""
            QFrame#ListRow {{
                background: {self._theme.get('panel_bg', '#15110a')};
                border: 1px solid {self._theme.get('border', '#372d1c')};
                border-radius: 10px;
            }}
        """)
        return host

    # -- actions (guarded mw hooks) ----------------------------------------------
    def _play_user_row(self, index: int) -> None:
        if 0 <= index < len(self._user_rows):
            paths = list(self._user_rows[index]["paths"])
            _call(self._mw, "_play_from_list", paths, 0, self, default=None)

    def _activate_smart(self, key: str) -> None:
        _call(self._mw, "request_play_smart", key, default=None)

    def _play_radio_row(self, index: int) -> None:
        if 0 <= index < len(self._radio_rows):
            station_id = self._radio_rows[index].get("id")
            _call(self._mw, "request_play_radio", station_id, default=None)

    def _remove_radio_row(self, index: int) -> None:
        if 0 <= index < len(self._radio_rows):
            station_id = self._radio_rows[index].get("id")
            _call(self._mw, "request_remove_radio", station_id, default=None)


__all__ = [
    "ALL_COLUMNS",
    "FlowLayout",
    "TrackTableModel",
    "LibraryTable",
    "AlbumsPage",
    "ArtistsPage",
    "GenresPage",
    "PlaylistsPage3",
    "format_duration_ms",
    "star_text",
]
