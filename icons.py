"""
Golden Music — Icon library (v4.0).
Real SVG icon files (Lucide set) loaded from assets/icons/, rendered via QSvgRenderer.
Falls back to the built-in v3.0 strings when a file is missing (e.g. dev checkouts
without the assets folder), so the app never breaks.
"""
from pathlib import Path

from PyQt6.QtCore import Qt, QByteArray, QSize
from PyQt6.QtGui import QPixmap, QPainter, QIcon
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QApplication


# ---------------------------------------------------------------------------
# File-based icons (Lucide) -------------------------------------------------
# ---------------------------------------------------------------------------

_ASSETS_DIR = Path(__file__).resolve().parent / "assets" / "icons" / "lucide"
_cache: dict[tuple[str, int, str, int], QPixmap] = {}


def _load_svg(name: str) -> str | None:
    """Load an SVG file's text from the Lucide asset folder."""
    p = _ASSETS_DIR / f"{name}.svg"
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Built-in fallbacks (v3.0 inline strings, used only if a file is missing)
# ---------------------------------------------------------------------------

_FALLBACK = {
    "play": '''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M7 4.8v14.4a.9.9 0 0 0 1.35.78l11.6-7.2a.9.9 0 0 0 0-1.56L8.35 4.02A.9.9 0 0 0 7 4.8Z"/></svg>''',
    "pause": '''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="currentColor" stroke="none"><rect x="6" y="4" width="4.2" height="16" rx="1.6"/><rect x="13.8" y="4" width="4.2" height="16" rx="1.6"/></svg>''',
    "stop": '''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="5" width="14" height="14" rx="2"/></svg>''',
    "next": '''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="currentColor" stroke="none"><path d="M5.5 5.1v13.8a.9.9 0 0 0 1.37.77l9.63-6.53a.92.92 0 0 0 0-1.54L6.87 4.33a.9.9 0 0 0-1.37.77Z"/><rect x="17" y="4.5" width="2.6" height="15" rx="1.3"/></svg>''',
    "prev": '''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="currentColor" stroke="none"><path d="M18.5 18.9V5.1a.9.9 0 0 0-1.37-.77l-9.63 6.53a.92.92 0 0 0 0 1.54l9.63 6.87a.9.9 0 0 0 1.37-.77Z"/><rect x="4.4" y="4.5" width="2.6" height="15" rx="1.3"/></svg>''',
    "shuffle": '''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 3h5v5"/><path d="M21 3 13.5 10.5"/><path d="M16 21h5v-5"/><path d="m21 21-7.5-7.5"/><path d="M3 3l7.5 7.5"/><path d="M3 21l7.5-7.5"/></svg>''',
    "repeat": '''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m17 2 4 4-4 4"/><path d="M3 11v-1a4 4 0 0 1 4-4h14"/><path d="m7 22-4-4 4-4"/><path d="M21 13v1a4 4 0 0 1-4 4H3"/></svg>''',
    "repeat-one": '''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m17 2 4 4-4 4"/><path d="M3 11v-1a4 4 0 0 1 4-4h14"/><path d="m7 22-4-4 4-4"/><path d="M21 13v1a4 4 0 0 1-4 4H3"/><path d="M11 10h1v4"/></svg>''',
    "heart": '''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19.2 5.4A5.4 5.4 0 0 0 12 5.28a5.4 5.4 0 0 0-7.65 7.62L12 20.5l7.65-7.6a5.4 5.4 0 0 0-.45-7.5Z"/></svg>''',
    "heart-filled": '''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="currentColor" stroke="none"><path d="M19.2 5.4A5.4 5.4 0 0 0 12 5.28a5.4 5.4 0 0 0-7.65 7.62L12 20.5l7.65-7.6a5.4 5.4 0 0 0-.45-7.5Z"/></svg>''',
    "volume-high": '''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" fill="currentColor"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/></svg>''',
    "volume-low": '''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" fill="currentColor"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/></svg>''',
    "volume-mute": '''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" fill="currentColor"/><line x1="22" y1="9" x2="16" y2="15"/><line x1="16" y1="9" x2="22" y2="15"/></svg>''',
    "library": '''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>''',
    "favorites-nav": '''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19.2 5.4A5.4 5.4 0 0 0 12 5.28a5.4 5.4 0 0 0-7.65 7.62L12 20.5l7.65-7.6a5.4 5.4 0 0 0-.45-7.5Z"/></svg>''',
    "folder-add": '''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 10v6"/><path d="M9 13h6"/><path d="M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z"/></svg>''',
    "folder": '''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/></svg>''',
    "sun": '''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4" fill="currentColor"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/></svg>''',
    "moon": '''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/></svg>''',
    "settings": '''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/></svg>''',
    "music-note": '''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>''',
    "search": '''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>''',
    "sort-asc": '''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m3 8 4-4 4 4"/><path d="M7 4v16"/><path d="M11 12h4"/><path d="M11 16h7"/><path d="M11 20h10"/></svg>''',
    "sort-desc": '''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m3 16 4 4 4-4"/><path d="M7 20V4"/><path d="M11 4h10"/><path d="M11 8h7"/><path d="M11 12h4"/></svg>''',
    "queue": '''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h13"/><path d="M3 12h13"/><path d="M3 18h9"/><path d="M17 6v6l4-3-4-3Z" fill="currentColor"/></svg>''',
    "equalizer": '''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="6" y1="20" x2="6" y2="14"/><line x1="6" y1="10" x2="6" y2="4"/><line x1="12" y1="20" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="4"/><line x1="18" y1="20" x2="18" y2="16"/><line x1="18" y1="12" x2="18" y2="4"/><line x1="3" y1="14" x2="9" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="15" y1="16" x2="21" y2="16"/></svg>''',
    "timer": '''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="13" r="8"/><path d="M12 9v4l2 2"/><path d="M5 3 2 6"/><path d="m22 6-3-3"/></svg>''',
    "minimize": '''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="4" width="20" height="16" rx="3"/><path d="M8 21h8"/></svg>''',
    "chevron-left": '''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>''',
    "chevron-right": '''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>''',
    "menu": '''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="4" y1="6" x2="20" y2="6"/><line x1="4" y1="12" x2="20" y2="12"/><line x1="4" y1="18" x2="20" y2="18"/></svg>''',
    "list-lines": '''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M3 12h18"/><path d="M3 18h12"/></svg>''',
    "lyrics": '''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/><path d="M8 9h8"/><path d="M8 13h5"/></svg>''',
    "more": '''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="5" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="12" cy="19" r="1.5"/></svg>''',
    "trash": '''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>''',
    "close": '''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>''',
    "refresh": '''<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/></svg>''',
}


def get_svg_source(name: str) -> str:
    """Return the SVG markup for *name*: asset file first, built-in fallback second."""
    svg = _load_svg(name)
    if svg:
        return svg
    return _FALLBACK.get(name, _FALLBACK["music-note"])


class Icon:
    """Icon name constants — each resolves to an SVG in assets/icons/lucide/."""

    # ---- Playback ----
    PLAY = "play"
    PAUSE = "pause"
    STOP = "stop"
    NEXT = "next"
    PREV = "prev"

    SHUFFLE = "shuffle"
    REPEAT = "repeat"
    REPEAT_ONE = "repeat-one"

    # ---- Heart ----
    HEART = "heart"
    HEART_FILLED = "heart-filled"

    # ---- Volume ----
    VOLUME_HIGH = "volume-high"
    VOLUME_LOW = "volume-low"
    VOLUME_MUTE = "volume-mute"

    # ---- Navigation ----
    LIBRARY = "library"
    FAVORITES_NAV = "favorites-nav"
    FOLDER_ADD = "folder-add"
    FOLDER = "folder"

    # ---- Theme ----
    SUN = "sun"
    MOON = "moon"
    BRUSH = "brush"

    # ---- Settings ----
    SETTINGS = "settings"

    # ---- Music note ----
    MUSIC_NOTE = "music-note"

    # ---- Search ----
    SEARCH = "search"

    # ---- Sort ----
    SORT_ASC = "sort-asc"
    SORT_DESC = "sort-desc"

    # ---- Queue / Playlist ----
    QUEUE = "queue"
    PLAYLISTS = "list-music"

    # ---- Albums ----
    ALBUMS = "disc-3"

    # ---- Stats ----
    STATS = "chart-column"

    # ---- More (⋯ menu) ----
    ELLIPSIS = "ellipsis"

    # ---- Share ----
    SHARE = "share"

    # ---- Playback speed ----
    SPEED = "gauge"

    # ---- Equalizer ----
    EQUALIZER = "equalizer"

    # ---- Sleep timer ----
    TIMER = "timer"

    # ---- Mini player ----
    MINIMIZE = "minimize"

    # ---- Collapse / Expand ----
    CHEVRON_LEFT = "chevron-left"
    CHEVRON_RIGHT = "chevron-right"
    CHEVRON_DOWN = "chevron-down"

    MENU = "menu"

    LIST_LINES = "list-lines"
    LYRICS = "lyrics"
    MORE = "more"

    TRASH = "trash"
    CLOSE = "close"
    REFRESH = "refresh"


def render_icon(icon, size: int = 24, color: str = "#d4a85a",
                device_pixel_ratio: float = 1.0) -> QPixmap:
    """Render *icon* (an ``Icon.*`` constant or raw SVG text) to a crisp QPixmap.

    Results are cached per (source, size, color, dpr).
    """
    if icon.startswith("<"):
        source_key, svg = icon, icon
    else:
        svg = get_svg_source(icon)
        source_key = f"{icon}|{color}"

    key = (source_key, int(size), color, round(device_pixel_ratio, 2))
    cached = _cache.get(key)
    if cached is not None:
        pm = QPixmap(cached)
        pm.setDevicePixelRatio(device_pixel_ratio)
        return pm

    colored = svg.replace("currentColor", color)
    renderer = QSvgRenderer(QByteArray(colored.encode("utf-8")))
    actual_size = max(1, int(size * device_pixel_ratio))
    pixmap = QPixmap(actual_size, actual_size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    renderer.render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(device_pixel_ratio)

    _cache[key] = pixmap
    out = QPixmap(pixmap)
    out.setDevicePixelRatio(device_pixel_ratio)
    return out


def make_icon(icon, size: int = 24, color: str = "#d4a85a",
              device_pixel_ratio: float = 1.0) -> QIcon:
    """Build a QIcon from an ``Icon.*`` constant or raw SVG text."""
    return QIcon(render_icon(icon, size, color, device_pixel_ratio))


def get_dpr() -> float:
    app = QApplication.instance()
    return app.devicePixelRatio() if app else 1.0
