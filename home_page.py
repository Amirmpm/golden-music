"""Golden Music v3 — Home page: the user's LOCAL music library in the Nuvx
Desktop home layout.

Product rules (PART 4):
  * the visual structure follows Nuvx Home (title 34, strapline shelves,
    horizontal card rows, square tiles >= 144 px, 20 px gaps, hover play)
  * the CONTENT is Golden Music's existing local library — never online
    recommendations
  * sections: Recently Played, Recently Added, Albums, Artists, Playlists,
    Favorites, Recently Downloaded
  * every item comes from real library data — no placeholder music, no
    fabricated metadata (empty sections are hidden; an empty library shows
    an "Add your music folders" call-to-action)

The page is data-driven and emits intents back to the MainWindow (play,
play next, add to queue, favorite, add to playlist, open album, open
artist, track info) — it never touches playback/config itself.
"""
from __future__ import annotations

from collections import OrderedDict

from PyQt6.QtCore import (Qt, pyqtSignal, QSize, QPoint, QRect,
                          QThread, QMutex, QWaitCondition,
                          QPropertyAnimation, QEasingCurve)
from PyQt6.QtGui import (QPixmap, QImage, QPainter, QColor, QPainterPath,
                          QPen, QIcon)
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                              QPushButton, QFrame, QScrollArea, QSizePolicy,
                              QMenu, QToolButton)

import coverart
from icons import Icon, render_icon

TILE = 168          # default square tile px (>= Nuvx minimum 144)
GAP = 20
CARD_RADIUS = 8


def _qcolor(value, fallback: str = "#000000") -> QColor:
    """QColor from a theme value. ``QColor()`` cannot parse float-alpha
    ``rgba()`` strings, which the Liquid Glass theme uses everywhere."""
    s = str(value or "").strip()
    if s.startswith("rgba(") or s.startswith("rgb("):
        try:
            parts = [p.strip() for p in s[s.find("(") + 1:s.rfind(")")]
                     .split(",")]
            r, g, b = (int(float(parts[0])), int(float(parts[1])),
                       int(float(parts[2])))
            a = float(parts[3]) if len(parts) > 3 else 1.0
            return QColor(r, g, b, max(0, min(255, int(a * 255))))
        except Exception:
            return QColor(fallback)
    c = QColor(s)
    return c if c.isValid() else QColor(fallback)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class HomeItem:
    """One card / row item backed by REAL library data."""

    __slots__ = ("kind", "key", "title", "subtitle", "paths", "cover_path")

    def __init__(self, kind, key, title, subtitle, paths, cover_path=None):
        self.kind = kind          # track | album | artist | playlist | folder
        self.key = key
        self.title = title
        self.subtitle = subtitle
        self.paths = list(paths)
        self.cover_path = cover_path or (paths[0] if paths else None)


def _track_meta(path: str, tags: dict):
    t = tags.get(path)
    if isinstance(t, dict):
        return t.get("title") or path, t.get("artist") or "Unknown artist"
    if isinstance(t, (tuple, list)) and len(t) >= 2:
        return t[0] or path, t[1] or "Unknown artist"
    return path, "Unknown artist"


def _album_meta(path: str, tags: dict):
    t = tags.get(path)
    if isinstance(t, dict):
        return t.get("album") or "", t.get("artist") or ""
    if isinstance(t, (tuple, list)) and len(t) >= 2:
        return "", t[1] or ""
    return "", ""


# ---------------------------------------------------------------------------
# Card widget
# ---------------------------------------------------------------------------

class RoundCover(QLabel):
    """Cover label that clips its pixmap to the card radius.

    A plain QLabel's QSS ``border-radius`` does NOT clip pixmap content, so
    without this the artwork renders with square corners over a rounded
    border."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._radius = CARD_RADIUS
        self._border = "rgba(255,255,255,0.14)"

    def set_round(self, radius: int, border):
        radius = int(radius)
        if radius != self._radius or border != self._border:
            self._radius = radius
            self._border = border
            self.update()

    def paintEvent(self, e):
        pm = self.pixmap()
        if pm is None or pm.isNull():
            super().paintEvent(e)          # placeholder glyph / background
            return
        try:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            r = self._radius
            path = QPainterPath()
            path.addRoundedRect(0.5, 0.5, self.width() - 1,
                                self.height() - 1, r, r)
            p.setClipPath(path)
            p.drawPixmap(0, 0, pm)
            p.setClipping(False)
            p.setPen(QPen(_qcolor(self._border, "#ffffff"), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(0.5, 0.5, self.width() - 1,
                              self.height() - 1, r, r)
            p.end()
        except Exception:
            super().paintEvent(e)


class Card(QFrame):
    """One artwork card in the Nuvx feed-tile shape: ``w x (w + 58)``,
    14.5/13 typography, hover scrim + bottom-right play affordance.

    Click = play collection (behaviour preserved); right-click opens the
    existing context menu."""

    activated = pyqtSignal(object)            # HomeItem
    context_requested = pyqtSignal(object, QPoint)

    MIN_TILE = 144
    MAX_TILE = 240
    TEXT_H = 58                       # Nuvx: height = width + 58

    def __init__(self, item: HomeItem, theme: dict, parent=None, page=None):
        super().__init__(parent)
        self.item = item
        self.theme = dict(theme or {})
        self.page = page              # HomePage back-ref for async covers
        self._tile = TILE
        self._cover_src: QPixmap | None = None
        self._play_base = QPoint(TILE - 58, TILE - 58)
        self._slide = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self.cover = RoundCover(self)
        self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scrim = QLabel(self)            # dark wash over the art (hover)
        self.title = QLabel(item.title, self)
        self.title.setWordWrap(True)
        self.subtitle = QLabel(item.subtitle, self)

        self.play_overlay = QPushButton(self)
        self.play_overlay.setFixedSize(44, 44)
        self.play_overlay.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_overlay.setToolTip("Play")
        self.play_overlay.setVisible(False)
        self.play_overlay.clicked.connect(
            lambda: self.activated.emit(self.item))

        self.set_theme(self.theme)
        self._load_cover()
        self.setFixedSize(TILE, TILE + self.TEXT_H)

    # -- geometry -------------------------------------------------------
    def set_tile_width(self, w: int):
        """Responsive tile width (Nuvx feed_tile: 144..240)."""
        w = max(self.MIN_TILE, min(self.MAX_TILE, int(w)))
        if w == self._tile:
            return
        self._tile = w
        self.setFixedSize(w, w + self.TEXT_H)   # → resizeEvent → _place()
        self._render_cover()

    def resizeEvent(self, e):
        self._place()
        super().resizeEvent(e)

    def _place(self):
        w = self._tile
        self.cover.setGeometry(0, 0, w, w)
        self.scrim.setGeometry(0, 0, w, w)
        self.title.setGeometry(0, w + 4, w, 38)
        self.subtitle.setGeometry(0, w + 42, w, 16)
        self._play_base = QPoint(w - 58, w - 58)
        if self.play_overlay.isVisible():
            self.play_overlay.move(self._play_base)

    # -- artwork --------------------------------------------------------
    def _load_cover(self):
        """Immediate placeholder, then async fill: NO disk I/O on the GUI
        thread (v3.0 did a synchronous mutagen read + decode per card)."""
        cp = self.item.cover_path
        if cp and self.page is not None:
            cached = self.page.cached_cover(cp)
            if cached is not None:
                self.set_cover(cached)
                return
            self.set_cover(None)          # placeholder right now
            self.page.request_cover(self, cp)
            return
        self.set_cover(None)

    def set_cover(self, pm: QPixmap | None):
        self._cover_src = pm
        self._render_cover()

    def _render_cover(self):
        pm = self._cover_src
        if pm is None or pm.isNull():
            self.cover.setPixmap(QPixmap())
            self.cover.setText("♪")
            self.cover.setStyleSheet("font-size: 42px;")
            return
        self.cover.setText("")
        side = max(1, self._tile)
        scaled = pm.scaled(
            side, side,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation)
        # centre-crop so wide/tall art never shifts the composition
        cw, ch = scaled.width(), scaled.height()
        x, y = (cw - side) // 2, (ch - side) // 2
        self.cover.setPixmap(scaled.copy(max(0, x), max(0, y), side, side))

    # -- theme ----------------------------------------------------------
    def set_theme(self, theme: dict):
        self.theme = dict(theme or {})
        t = self.theme
        text = t.get("text", "#f2f0f5")
        muted = t.get("muted", "#b2acba")
        border = t.get("glass_border",
                       t.get("border", "rgba(255,255,255,0.14)"))
        fill = t.get("glass_fill", "rgba(255,255,255,0.06)")
        radius = int((t.get("lg") or {}).get("radius_card", CARD_RADIUS))
        self.title.setStyleSheet(f"color: {text}; font-size: 14.5px; "
                                 f"font-weight: 600; background: transparent;")
        self.subtitle.setStyleSheet(f"color: {muted}; font-size: 13px; "
                                    f"background: transparent;")
        self.cover.setStyleSheet(
            f"QLabel {{ background: {fill}; color: {muted}; "
            f"font-size: 42px; }}")
        self.cover.set_round(radius, border)
        self.scrim.setStyleSheet(
            "background: rgba(0,0,0,0.28); border: none; "
            f"border-radius: {radius}px;")
        self.scrim.setVisible(self.play_overlay.isVisible())
        # Nuvx: translucent dark circle + white glyph (never a gold fill)
        self.play_overlay.setStyleSheet(
            "QPushButton { border-radius: 22px; background: rgba(0,0,0,0.62);"
            " border: none; }"
            "QPushButton:hover { background: rgba(0,0,0,0.78); }")
        glyph = render_icon(Icon.PLAY, 16, "rgba(255,255,255,0.95)")
        self.play_overlay.setIcon(QIcon(glyph))
        self.play_overlay.setIconSize(QSize(16, 16))
        self.setStyleSheet("QFrame { background: transparent; border: none; }")

    # -- hover affordance ----------------------------------------------
    def enterEvent(self, e):
        self._show_play()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hide_play()
        super().leaveEvent(e)

    def _show_play(self):
        self.scrim.setVisible(True)
        self.play_overlay.setVisible(True)
        dur = int((self.theme.get("lg") or {}).get("anim_ms_fast", 150) or 0)
        target = self._play_base
        if dur <= 0:
            if self._slide is not None:
                self._slide.stop()
            self.play_overlay.move(target)
            return
        if self._slide is not None:
            self._slide.stop()
        start = QPoint(target.x(), target.y() + 8)
        self.play_overlay.move(start)
        anim = QPropertyAnimation(self.play_overlay, b"pos", self)
        anim.setDuration(dur)
        anim.setStartValue(start)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start()
        self._slide = anim

    def _hide_play(self):
        if self._slide is not None:
            self._slide.stop()
        self.scrim.setVisible(False)
        self.play_overlay.setVisible(False)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self.item)
        elif e.button() == Qt.MouseButton.RightButton:
            self.context_requested.emit(self.item, self.mapToGlobal(e.pos()))
        self._hide_play()
        super().leaveEvent(e)

    def _show_play(self):
        self.scrim.setVisible(True)
        self.play_overlay.setVisible(True)
        dur = int((self.theme.get("lg") or {}).get("anim_ms_fast", 150) or 0)
        target = self._play_base
        if dur <= 0:
            if self._slide is not None:
                self._slide.stop()
            self.play_overlay.move(target)
            return
        if self._slide is not None:
            self._slide.stop()
        start = QPoint(target.x(), target.y() + 8)
        self.play_overlay.move(start)
        anim = QPropertyAnimation(self.play_overlay, b"pos", self)
        anim.setDuration(dur)
        anim.setStartValue(start)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start()
        self._slide = anim

    def _hide_play(self):
        if self._slide is not None:
            self._slide.stop()
        self.scrim.setVisible(False)
        self.play_overlay.setVisible(False)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self.item)
        elif e.button() == Qt.MouseButton.RightButton:
            self.context_requested.emit(self.item, self.mapToGlobal(e.pos()))


# ---------------------------------------------------------------------------
# Shelf
# ---------------------------------------------------------------------------

class Shelf(QWidget):
    """Strapline + bold header + horizontal card row with Nuvx-style
    prev/next affordances (the scrollbar itself stays hidden)."""

    see_all_requested = pyqtSignal(str)

    def __init__(self, strapline: str, header: str, theme: dict,
                 see_all_key: str = "", parent=None):
        super().__init__(parent)
        self.theme = dict(theme or {})
        self.see_all_key = see_all_key
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self.strapline = QLabel(strapline.upper())
        lay.addWidget(self.strapline)
        head = QHBoxLayout()
        head.setSpacing(6)
        self.header = QLabel(header)
        head.addWidget(self.header)
        head.addStretch(1)

        # Nuvx shelf arrows — shown only while the row overflows
        self.prev_btn = QToolButton()
        self.next_btn = QToolButton()
        for b in (self.prev_btn, self.next_btn):
            b.setFixedSize(28, 28)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setVisible(False)
        self.prev_btn.clicked.connect(lambda: self._scroll_by(-1))
        self.next_btn.clicked.connect(lambda: self._scroll_by(1))
        head.addWidget(self.prev_btn)
        head.addWidget(self.next_btn)

        if see_all_key:
            self.see_all = QPushButton("See all")
            self.see_all.setCursor(Qt.CursorShape.PointingHandCursor)
            self.see_all.clicked.connect(
                lambda: self.see_all_requested.emit(self.see_all_key))
            head.addWidget(self.see_all)
        else:
            self.see_all = None
        lay.addLayout(head)

        self.row_host = QWidget()
        self.row_lay = QHBoxLayout(self.row_host)
        self.row_lay.setContentsMargins(0, 0, 0, 0)
        self.row_lay.setSpacing(GAP)
        self.row_lay.addStretch(1)

        self.scroll = QScrollArea()
        self.scroll.setWidget(self.row_host)
        self.scroll.setWidgetResizable(True)
        self.scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lay.addWidget(self.scroll)
        self.set_theme(self.theme)

    def add_card(self, card: Card):
        self.row_lay.insertWidget(self.row_lay.count() - 1, card)

    def count(self) -> int:
        return self.row_lay.count() - 1

    # -- responsive layout ---------------------------------------------
    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._relayout()

    def _cards(self) -> list:
        return [w for w in (self.row_lay.itemAt(i).widget()
                            for i in range(self.row_lay.count()))
                if isinstance(w, Card)]

    def _relayout(self):
        """Nuvx feed columns: 4 (<1260) / 5 (<1580) / 6, tile 144..240."""
        cards = self._cards()
        avail = max(160, self.scroll.viewport().width())
        cols = 4 if avail < 1260 else (5 if avail < 1580 else 6)
        w = (avail - (cols - 1) * GAP) // cols
        w = max(Card.MIN_TILE, min(Card.MAX_TILE, w))
        while w > Card.MIN_TILE and w * cols + (cols - 1) * GAP > avail:
            w -= 4
        for c in cards:
            c.set_tile_width(w)
        need = len(cards) * w + max(0, len(cards) - 1) * GAP
        more = need > avail and len(cards) > 1
        self.prev_btn.setVisible(more)
        self.next_btn.setVisible(more)
        height = w + Card.TEXT_H + 16           # scrollbar breathing room
        if self.scroll.height() != height:
            self.scroll.setFixedHeight(height)

    def _scroll_by(self, direction: int):
        sb = self.scroll.horizontalScrollBar()
        step = max(160, int(self.scroll.viewport().width() * 0.6))
        sb.setValue(sb.value() + direction * step)

    # -- theme ----------------------------------------------------------
    def set_theme(self, theme: dict):
        self.theme = dict(theme or {})
        t = self.theme
        muted = t.get("muted", "#b2acba")
        text = t.get("text", "#f2f0f5")
        border = t.get("glass_border",
                       t.get("border", "rgba(255,255,255,0.14)"))
        fill = t.get("glass_fill", "rgba(255,255,255,0.06)")
        hover = t.get("glass_hover", "rgba(255,255,255,0.14)")
        self.strapline.setStyleSheet(
            f"color: {muted}; font-size: 11.5px; font-weight: 600; "
            f"letter-spacing: 1.2px; background: transparent;")
        self.header.setStyleSheet(
            f"color: {text}; font-size: 21px; font-weight: 700; "
            f"background: transparent;")
        if self.see_all is not None:
            # neutral ghost pill — gold stays reserved for real state
            self.see_all.setStyleSheet(f"""
                QPushButton {{
                    color: {text}; background: transparent;
                    border: 1px solid {border}; border-radius: 14px;
                    padding: 4px 14px; font-size: 12px; font-weight: 600;
                }}
                QPushButton:hover {{ background: {fill}; }}
            """)
        arrow_css = f"""
            QToolButton {{ background: {fill}; border: none;
                           border-radius: 14px; }}
            QToolButton:hover {{ background: {hover}; }}
        """
        self.prev_btn.setStyleSheet(arrow_css)
        self.next_btn.setStyleSheet(arrow_css)
        self.prev_btn.setIcon(QIcon(render_icon(Icon.CHEVRON_LEFT, 16, text)))
        self.next_btn.setIcon(QIcon(render_icon(Icon.CHEVRON_RIGHT, 16, text)))
        self.prev_btn.setIconSize(QSize(16, 16))
        self.next_btn.setIconSize(QSize(16, 16))
        self.scroll.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QScrollBar:horizontal { height: 0; background: transparent; }
            QScrollBar::handle:horizontal { background: transparent; }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                width: 0; height: 0;
            }
        """)


# ---------------------------------------------------------------------------
# Home page
# ---------------------------------------------------------------------------

class HomePage(QWidget):
    """Nuvx-layout home for the LOCAL library."""

    # intents to MainWindow
    play_requested = pyqtSignal(object)            # HomeItem (paths list)
    play_next_requested = pyqtSignal(object)
    add_queue_requested = pyqtSignal(object)
    favorite_requested = pyqtSignal(object)        # HomeItem(track)
    add_to_playlist_requested = pyqtSignal(object)
    open_album_requested = pyqtSignal(object)
    open_artist_requested = pyqtSignal(object)
    track_info_requested = pyqtSignal(object)      # HomeItem(track)
    add_folder_requested = pyqtSignal()
    see_all_requested = pyqtSignal(str)            # 'albums'|'artists'|...

    MAX_CARDS = 10
    _COVER_CACHE_MAX = 120

    def __init__(self, theme: dict, parent=None):
        super().__init__(parent)
        self.theme = dict(theme or {})
        self._model = {
            "library": [], "tags": {}, "recently_played": [],
            "recently_added": [], "favorites": set(),
            "playlists": {}, "recently_downloaded": [],
        }
        # async covers: bounded loader + per-path request map + pixmap cache
        self._cover_loader = HomeCoverLoader(self)
        self._cover_loader.covers_ready.connect(self._on_covers_ready)
        self._cover_requests: dict = {}    # path -> [Card]
        self._pm_cache: "OrderedDict[str, QPixmap]" = OrderedDict()
        self._cover_loader.start()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.setSpacing(18)
        # One heading only: MainWindow hides the global toolbar + page title
        # on Home (Nuvx shows a single Bold 34 page title).
        self.title = QLabel("Home")
        lay.addWidget(self.title)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.body = QWidget()
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(0, 0, 0, 96)   # Nuvx page bottom
        self.body_lay.setSpacing(26)                    # shelf-to-shelf rhythm
        self.body_lay.addStretch(1)
        self.scroll.setWidget(self.body)
        lay.addWidget(self.scroll, 1)

        self.empty_state = QWidget()
        el = QVBoxLayout(self.empty_state)
        el.setContentsMargins(0, 40, 0, 0)
        el.setSpacing(10)
        self.empty_title = QLabel("Your library is empty")
        self.empty_hint = QLabel("Add your music folders to see them here.")
        self.empty_btn = QPushButton("Add music folder")
        self.empty_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.empty_btn.clicked.connect(self.add_folder_requested.emit)
        el.addWidget(self.empty_title, alignment=Qt.AlignmentFlag.AlignCenter)
        el.addWidget(self.empty_hint, alignment=Qt.AlignmentFlag.AlignCenter)
        el.addWidget(self.empty_btn, alignment=Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.empty_state, 1)
        self.set_theme(self.theme)

    # ------------------------------------------------------------------
    def set_theme(self, theme: dict):
        self.theme = dict(theme or {})
        t = self.theme
        self.title.setStyleSheet(
            f"color: {t.get('text', '#f2f0f5')}; font-size: 34px; "
            f"font-weight: 700; background: transparent;")
        accent = t.get("gold", "#e9b957")
        self.empty_btn.setStyleSheet(f"""
            QPushButton {{
                background: {accent}; color: {'#111' if t.get('is_dark', True) else '#fff'};
                border: none; border-radius: 19px; padding: 10px 22px;
                font-size: 14px; font-weight: 600;
            }}
            QPushButton:hover {{ opacity: 0.9; }}
        """)
        self.empty_title.setStyleSheet(
            f"color: {t.get('text', '#f2f0f5')}; font-size: 22px; "
            f"font-weight: 700; background: transparent;")
        self.empty_hint.setStyleSheet(
            f"color: {t.get('muted', '#b2acba')}; font-size: 13px; "
            f"background: transparent;")
        self.scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }")
        for shelf in self._shelves():
            shelf.set_theme(t)
            for card in self._shelf_cards(shelf):
                card.set_theme(t)

    def _shelves(self) -> list:
        return [w for w in (self.body_lay.itemAt(i).widget()
                             for i in range(self.body_lay.count()))
                if isinstance(w, Shelf)]

    def _shelf_cards(self, shelf: Shelf) -> list:
        return [w for w in (shelf.row_lay.itemAt(i).widget()
                            for i in range(shelf.row_lay.count()))
                if isinstance(w, Card)]

    # ------------------------------------------------------------------
    def set_model(self, model: dict):
        """Feed REAL library data + rebuild all shelves."""
        self._model.update(model)
        self._rebuild()

    def _rebuild(self):
        # clear body
        self._cover_loader.cancel_all()      # old cards are gone
        self._cover_requests.clear()
        while self.body_lay.count() > 1:
            item = self.body_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        lib = [p for p in (self._model.get("library") or []) if p]
        tags = self._model.get("tags") or {}
        empty = not lib
        self.empty_state.setVisible(empty)
        self.scroll.setVisible(not empty)
        if empty:
            return

        def add_shelf(strap, header, items, see_all=""):
            if not items:
                return None
            sh = Shelf(strap, header, self.theme, see_all)
            sh.see_all_requested.connect(self.see_all_requested.emit)
            for it in items[: self.MAX_CARDS]:
                card = Card(it, self.theme, page=self)
                card.activated.connect(self._on_card)
                card.context_requested.connect(self._on_card_menu)
                sh.add_card(card)
            self.body_lay.insertWidget(self.body_lay.count() - 1, sh)
            return sh

        rp = [p for p in self._model.get("recently_played", []) if p in set(lib)]
        add_shelf("Jump back in", "Recently played",
                  [self._track_item(p, tags) for p in rp])

        ra = [p for p in self._model.get("recently_added", []) if p in set(lib)]
        add_shelf("Fresh arrivals", "Recently added",
                  [self._track_item(p, tags) for p in ra])

        albums = self._album_items(lib, tags)
        add_shelf("Your collection", "Albums", albums, see_all="albums")

        artists = self._artist_items(lib, tags)
        add_shelf("People", "Artists", artists, see_all="artists")

        playlists = self._playlist_items(self._model.get("playlists") or {})
        add_shelf("Curation", "Playlists", playlists, see_all="playlists")

        fav = [p for p in sorted(self._model.get("favorites", set()))
               if p in set(lib)]
        add_shelf("Loved", "Favorites",
                  [self._track_item(p, tags) for p in fav], see_all="favorites")

        rd = [p for p in self._model.get("recently_downloaded", []) if p]
        add_shelf("Saved from online", "Recently downloaded",
                  [self._track_item(p, tags) for p in rd])

        self.set_theme(self.theme)

    # ------------------------------------------------------------------
    @staticmethod
    def _track_item(path, tags) -> HomeItem:
        title, artist = _track_meta(path, tags)
        return HomeItem("track", path, title, artist, [path], path)

    @staticmethod
    def _album_items(lib, tags) -> list:
        groups: "OrderedDict[str, list]" = OrderedDict()
        for p in lib:
            album, artist = _album_meta(p, tags)
            if not album:
                album = "Unknown album"
            groups.setdefault(album, []).append(p)
        out = []
        for album, paths in groups.items():
            title, _ = _track_meta(paths[0], tags)
            out.append(HomeItem("album", album, album,
                                f"{len(paths)} song" + ("s" if len(paths) != 1 else ""),
                                paths, paths[0]))
        return out

    @staticmethod
    def _artist_items(lib, tags) -> list:
        groups: "OrderedDict[str, list]" = OrderedDict()
        for p in lib:
            _, artist = _track_meta(p, tags)
            groups.setdefault(artist or "Unknown artist", []).append(p)
        return [HomeItem("artist", a, a, f"{len(ps)} song"
                         + ("s" if len(ps) != 1 else ""), ps, ps[0])
                for a, ps in groups.items()]

    def _playlist_items(self, playlists: dict) -> list:
        out = []
        for name in sorted(playlists, key=str.lower):
            paths = [p for p in (playlists.get(name) or []) if p]
            out.append(HomeItem("playlist", name, name,
                                f"{len(paths)} song"
                                + ("s" if len(paths) != 1 else ""),
                                paths, paths[0] if paths else None))
        return out

    # ------------------------------------------------------------------
    # Async cover plumbing (GUI-thread side)
    # ------------------------------------------------------------------
    def cached_cover(self, path: str):
        """Already-decoded TILE pixmap or None — zero I/O."""
        pm = self._pm_cache.get(path)
        if pm is not None:
            self._pm_cache.move_to_end(path)   # LRU touch
        return pm

    def request_cover(self, card, path: str):
        """Register a card for async fill (deduplicated per path)."""
        self._cover_requests.setdefault(path, []).append(card)
        self._cover_loader.request(path, TILE)

    def _on_covers_ready(self, batch):
        """GUI thread: wrap finished QImages as pixmaps and deliver."""
        for path, img in batch:
            pm = QPixmap.fromImage(img)
            self._pm_cache[path] = pm
            while len(self._pm_cache) > self._COVER_CACHE_MAX:
                self._pm_cache.popitem(last=False)
            for card in self._cover_requests.pop(path, []):
                try:
                    card.set_cover(pm)
                except RuntimeError:
                    pass          # card's C++ object already deleted

    def shutdown(self):
        """Coordinated shutdown (called from MainWindow.closeEvent)."""
        try:
            self._cover_loader.stop_and_join()
        except Exception:
            pass

    # ------------------------------------------------------------------
    def _on_card(self, item: HomeItem):
        self.play_requested.emit(item)

    def _on_card_menu(self, item: HomeItem, global_pos: QPoint):
        m = QMenu(self)
        m.addAction("Play", lambda: self.play_requested.emit(item))
        m.addAction("Play next", lambda: self.play_next_requested.emit(item))
        m.addAction("Add to queue", lambda: self.add_queue_requested.emit(item))
        if item.kind == "track":
            m.addSeparator()
            m.addAction("Add to favorites",
                        lambda: self.favorite_requested.emit(item))
            m.addAction("Add to playlist…",
                        lambda: self.add_to_playlist_requested.emit(item))
        if item.kind in ("track", "album"):
            m.addAction("Open album", lambda: self.open_album_requested.emit(item))
        if item.kind in ("track", "artist"):
            m.addAction("Open artist",
                        lambda: self.open_artist_requested.emit(item))
        if item.kind == "track":
            m.addAction("View track information",
                        lambda: self.track_info_requested.emit(item))
        m.exec(global_pos)
        if item.kind in ("track", "album"):
            m.addAction("Open album", lambda: self.open_album_requested.emit(item))
        if item.kind in ("track", "artist"):
            m.addAction("Open artist",
                        lambda: self.open_artist_requested.emit(item))
        if item.kind == "track":
            m.addAction("View track information",
                        lambda: self.track_info_requested.emit(item))
        m.exec(global_pos)
class HomeCoverLoader(QThread):
    """Bounded cover-art worker for Home cards.

    Disk access (mutagen reads) AND image decoding run here — QImage is
    safe to build off the GUI thread (QPixmap is NOT); the GUI-side slot
    only wraps the finished QImage. v3.0 ran up to ~70 sequential reads +
    decodes synchronously inside one rebuild, freezing the UI on a cold
    cache. Deduplicates requests; a rebuild cancels pending ones.
    """

    covers_ready = pyqtSignal(list)              # [(path, QImage)]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue: list = []
        self._queued = set()
        self._mutex = QMutex()
        self._wake = QWaitCondition()
        self._running = True

    def request(self, path: str, size: int = TILE):
        if not path:
            return
        self._mutex.lock()
        try:
            if path not in self._queued:
                self._queued.add(path)
                self._queue.append((path, size))
                self._wake.wakeAll()
        finally:
            self._mutex.unlock()

    def cancel_all(self):
        """Drop pending requests (page rebuild — old cards are gone)."""
        self._mutex.lock()
        try:
            self._queue.clear()
            self._queued.clear()
        finally:
            self._mutex.unlock()

    def stop_and_join(self):
        self._mutex.lock()
        try:
            self._running = False
            self._wake.wakeAll()
        finally:
            self._mutex.unlock()
        self.wait(3000)

    def run(self):
        while True:
            item = None
            self._mutex.lock()
            try:
                while self._running and not self._queue:
                    self._wake.wait(self._mutex, 500)
                if not self._running:
                    return
                if self._queue:
                    item = self._queue.pop(0)
            finally:
                self._mutex.unlock()
            if item is None:
                continue
            path, size = item
            try:
                data = coverart.get_cover_bytes(path)
            except Exception:
                data = None
            if not data:
                continue
            img = QImage()
            if not img.loadFromData(data):
                continue
            img = img.scaled(
                size, size,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation)
            self.covers_ready.emit([(path, img)])
    def shutdown(self):
        """Coordinated shutdown (called from MainWindow.closeEvent)."""
        try:
            self._cover_loader.stop_and_join()
        except Exception:
            pass
    def _on_covers_ready(self, batch):
        """GUI thread: wrap finished QImages as pixmaps and deliver."""
        for path, img in batch:
            pm = QPixmap.fromImage(img)
            self._pm_cache[path] = pm
            while len(self._pm_cache) > self._COVER_CACHE_MAX:
                self._pm_cache.popitem(last=False)
            for card in self._cover_requests.pop(path, []):
                try:
                    card.set_cover(pm)
                except RuntimeError:
                    pass          # card's C++ object already deleted
    def request_cover(self, card, path: str):
        """Register a card for async fill (deduplicated per path)."""
        self._cover_requests.setdefault(path, []).append(card)
        self._cover_loader.request(path, TILE)
    def cached_cover(self, path: str):
        """Already-decoded TILE pixmap or None — zero I/O."""
        pm = self._pm_cache.get(path)
        if pm is not None:
            self._pm_cache.move_to_end(path)   # LRU touch
        return pm
