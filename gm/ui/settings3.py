"""Golden Music 2.3 — Settings dialog (v3).

A modern two-pane settings dialog:

* left  — fixed-width category navigation with a live search field;
* right — scrollable category pages built from :class:`SettingRow` rows
  (title + muted description + control), a 10-band EQ editor driven by
  :class:`gm.equalizer.EQController`, and a rebindable-shortcuts table
  with conflict detection.

The search field filters rows across ALL categories into a flat
"Search results" page — matching rows are *re-parented* (never cloned)
so every control keeps its live state, and are restored in their
original position when the search is cleared.

Main-window contract (duck-typed; everything except ``prefs`` /
``apply_prefs`` / ``theme`` is optional and getattr-guarded so tests
can pass a minimal fake object):

========================  ==================================================
``mw.prefs``              dict — copied at open, written back on OK.
``mw.apply_prefs(dict)``  called exactly once when OK is pressed.
``mw.theme``              current theme dict (falls back to royal_gold).
``mw.request_*()``        optional live-action hooks (see ``_MW_HOOKS``).
========================  ==================================================

This module never imports ``gm.window`` / ``main`` — it is safe to
instantiate with a bare QApplication and a fake main window.
"""
from __future__ import annotations

import copy
import secrets
from pathlib import Path
from typing import Any, Callable, Sequence

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor, QKeySequence, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog,
    QFileDialog, QFrame, QGroupBox, QHBoxLayout, QHeaderView,
    QKeySequenceEdit, QLabel, QLineEdit, QMessageBox, QPushButton,
    QScrollArea, QSlider, QSpinBox, QStackedWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from config import APP_NAME, Theme
from icons import Icon, make_icon
from gm.equalizer import (
    BAND_COUNT, BAND_FREQS, EQController, MAX_GAIN_DB, MIN_GAIN_DB, PRESETS,
)
from gm.theme_qss import build_qss_v3

__all__ = ["SettingsDialog3", "SettingRow", "DEFAULT_SHORTCUT_DEFS"]


#: Fallback shortcut table (id, title, default) used when the main window
#: does not expose ``shortcut_defs()``.  Ratings use one combined row
#: ("Ctrl+1..5") because 1–5 map to the same action family.
DEFAULT_SHORTCUT_DEFS: list[tuple[str, str, str]] = [
    ("play_pause", "Play / Pause", "Space"),
    ("prev", "Previous track", "Ctrl+Left"),
    ("next", "Next track", "Ctrl+Right"),
    ("volume_up", "Volume up", "Ctrl+Up"),
    ("volume_down", "Volume down", "Ctrl+Down"),
    ("search_focus", "Focus search", "Ctrl+F"),
    ("mini_player", "Mini player", "Ctrl+M"),
    ("jump_playing", "Jump to playing track", "Ctrl+J"),
    ("fullscreen", "Fullscreen player", "F11"),
    ("command_palette", "Command palette", "Ctrl+K"),
    ("lyrics_panel", "Lyrics panel", "Ctrl+L"),
    ("queue_panel", "Queue panel", "Ctrl+Q"),
    ("toggle_favorite", "Toggle favorite", "Ctrl+B"),
    ("rate_current", "Rate current track (1-5 stars)", "Ctrl+1..5"),
    ("sleep_timer", "Sleep timer", "Ctrl+T"),
    ("theme_picker", "Theme picker", "Ctrl+P"),
]

#: Preferences key written by the "Reset all settings" (dangerous) action.
#: The app should respond by wiping stored settings and re-creating them
#: from defaults; every other key is absent from that payload.
RESET_FLAG_KEY = "___reset___"

#: Optional main-window hooks used by this dialog (all getattr-guarded;
#: listed for the window implementor — a missing hook is silently OK).
_MW_HOOKS: list[str] = [
    "request_theme_picker", "request_theme_refresh", "request_ui_scale",
    "request_output_picker", "request_m3u_import", "request_m3u_export",
    "request_pairing_qr", "request_telegram_test", "request_update_check",
    "request_open_log", "request_open_repo", "request_clear_lyrics_cache",
    "request_clear_waveform_cache", "shortcut_defs",
]

_ASSETS_DIR = Path(__file__).resolve().parent.parent.parent / "assets"

# EQ sliders work in 0.5 dB steps to keep integer slider ranges.
_EQ_STEP_DB = 0.5


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _fmt_hz(freq: float) -> str:
    """Format a band centre frequency like ``"31 Hz"`` / ``"16 kHz"``."""
    f = float(freq)
    if f >= 1000.0:
        return f"{f / 1000.0:g} kHz"
    return f"{f:g} Hz"


def _fmt_slider_value(value: float, step: float) -> str:
    """Compact slider label: ints without decimals, floats trimmed."""
    if float(step).is_integer():
        return f"{int(round(value)):d}"
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def _canon_seq(seq: str) -> str:
    """Canonical comparison form of a shortcut string (whitespace + case)."""
    return " ".join(str(seq or "").split()).casefold()


def _call(mw: Any, name: str, *args: Any) -> bool:
    """Call ``mw.<name>(*args)`` if it exists; never raise.

    Returns True when the hook existed, was callable and succeeded.
    """
    fn = getattr(mw, name, None)
    if not callable(fn):
        return False
    try:
        fn(*args)
        return True
    except Exception:  # noqa: BLE001 - hooks must never break the dialog
        return False


def _safe_theme(theme: Any) -> dict:
    """Return a usable theme dict (fallback: royal_gold)."""
    required = ("window_bg", "panel_bg", "panel_bg_2", "border", "text",
                "muted", "gold", "gold_light", "gold_deep", "active",
                "is_dark")
    if isinstance(theme, dict) and all(k in theme for k in required):
        return dict(theme)
    return dict(Theme.get("royal_gold"))


# ---------------------------------------------------------------------------
# rebuildable section container
# ---------------------------------------------------------------------------

class _Section:
    """Ordered, rebuildable list of widgets inside one layout.

    A *section* is the body of a settings page or of a QGroupBox.  Rows
    can temporarily be re-parented into the search-results page; calling
    :meth:`rebuild` puts every child back, in the original order, which
    is what allows search to borrow live widgets without cloning them.
    """

    def __init__(self, layout: QVBoxLayout) -> None:
        self._layout = layout
        self.widgets: list[QWidget] = []
        self._stretch = False

    def add(self, w: QWidget) -> None:
        """Append a widget (row or group box) to the section."""
        self.widgets.append(w)
        self._layout.addWidget(w)

    def add_stretch(self) -> None:
        """Append a trailing stretch (re-created on every rebuild)."""
        self._stretch = True
        self._layout.addStretch(1)

    def clear(self) -> None:
        """Remove everything from the layout and forget the children."""
        while self._layout.count():
            self._layout.takeAt(0)
        self.widgets.clear()
        self._stretch = False

    def rebuild(self) -> None:
        """Re-add all children in order (re-parenting borrowed rows)."""
        while self._layout.count():
            self._layout.takeAt(0)
        for w in self.widgets:
            self._layout.addWidget(w)
        if self._stretch:
            self._layout.addStretch(1)


# ---------------------------------------------------------------------------
# the setting row framework
# ---------------------------------------------------------------------------

class SettingRow(QFrame):
    """One setting: bold title, muted wrapping description, control.

    Attributes
    ----------
    title:
        Row title (bold).  Feeds the search filter.
    keywords:
        Extra search keywords (title + keywords + category are matched,
        case-insensitively, as substrings).
    category:
        Display name of the page the row belongs to.
    key:
        Preferences key this row edits (None for info rows).
    control:
        The live control widget (may be None for info rows).
    read:
        ``() -> value`` getter collecting the control's current value.
    sync:
        ``() -> None`` re-loads the control from the working prefs copy
        (used after "Reset Defaults" / factory reset).
    """

    def __init__(self, title: str, description: str = "",
                 control: QWidget | None = None, *,
                 keywords: Sequence[str] = (),
                 category: str = "",
                 control_below: bool = False,
                 key: str | None = None,
                 read: Callable[[], Any] | None = None,
                 sync: Callable[[], None] | None = None) -> None:
        super().__init__()
        self.setObjectName("SettingRow")
        self.title = str(title)
        self.keywords: list[str] = [str(k) for k in keywords]
        self.category = str(category)
        self.key = key
        self.control = control
        self.read = read
        self.sync = sync

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)
        title_lbl = QLabel(self.title)
        title_lbl.setObjectName("SettingTitle")
        title_lbl.setWordWrap(True)
        text_col.addWidget(title_lbl)
        if description:
            desc_lbl = QLabel(description)
            desc_lbl.setObjectName("SettingDesc")
            desc_lbl.setWordWrap(True)
            text_col.addWidget(desc_lbl)

        if control_below:
            root = QVBoxLayout(self)
            root.setContentsMargins(0, 9, 0, 9)
            root.setSpacing(6)
            root.addLayout(text_col)
            if control is not None:
                root.addWidget(control)
        else:
            root = QHBoxLayout(self)
            root.setContentsMargins(0, 9, 0, 9)
            root.setSpacing(16)
            root.addLayout(text_col, 1)
            if control is not None:
                root.addWidget(control, 0,
                               Qt.AlignmentFlag.AlignRight
                               | Qt.AlignmentFlag.AlignVCenter)

    def matches(self, query: str) -> bool:
        """True when *query* is a case-insensitive substring of the
        title, any keyword or the category name."""
        q = str(query).strip().casefold()
        if not q:
            return False
        haystack = [self.title, self.category, *self.keywords]
        return any(q in str(h).casefold() for h in haystack if h)


# ---------------------------------------------------------------------------
# the dialog
# ---------------------------------------------------------------------------

class SettingsDialog3(QDialog):
    """Two-pane settings dialog with search, EQ editor and shortcuts.

    Parameters
    ----------
    main_window:
        Duck-typed main window.  Only ``prefs``, ``apply_prefs`` and
        (optionally) ``theme`` are assumed; everything else is guarded
        with :func:`getattr` so minimal fakes work (see the tests).
    parent:
        Optional Qt parent widget.
    """

    #: (category id, display label) in nav order.
    CATEGORIES: list[tuple[str, str]] = [
        ("appearance", "Appearance"),
        ("playback", "Playback"),
        ("audio", "Audio & EQ"),
        ("library", "Library"),
        ("lyrics", "Lyrics"),
        ("stats", "Stats"),
        ("integrations", "Integrations"),
        ("shortcuts", "Shortcuts"),
        ("advanced", "Advanced"),
        ("about", "About"),
    ]

    #: Nav icon per category id.
    CATEGORY_ICONS: dict[str, str] = {
        "appearance": Icon.PALETTE,
        "playback": Icon.PLAY,
        "audio": Icon.EQUALIZER,
        "library": Icon.LIBRARY,
        "lyrics": Icon.LYRICS,
        "stats": Icon.STATS,
        "integrations": Icon.CAST,
        "shortcuts": Icon.KEYBOARD,
        "advanced": Icon.SLIDERS,
        "about": Icon.INFO,
    }

    SIDE_WIDTH = 190

    # ------------------------------------------------------------------ init
    def __init__(self, main_window: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mw = main_window
        self._theme: dict = _safe_theme(getattr(main_window, "theme", None))
        src_prefs = getattr(main_window, "prefs", None)
        self._prefs: dict[str, Any] = copy.deepcopy(dict(src_prefs or {}))

        self._rows: list[SettingRow] = []
        self._sections: list[_Section] = []
        self._pages: dict[str, QScrollArea] = {}
        self._page_sections: dict[str, _Section] = {}
        self._cat_btns: dict[str, QPushButton] = {}
        self._icon_targets: list[tuple[QPushButton, str]] = []

        self._eq = self._load_eq()
        self._eq_sliders: list[QSlider] = []
        self._eq_combo: QComboBox | None = None
        self._eq_widget: QWidget | None = None
        self._eq_ui_lock = False

        self._shortcut_defs = self._load_shortcut_defs()
        self._shortcuts: dict[str, str] = {}
        self._sc_table: QTableWidget | None = None

        self._current_category = "appearance"
        self._search_section: _Section | None = None
        self._search_scroll: QScrollArea | None = None
        self._search_count_lbl: QLabel | None = None
        self._no_results_row: SettingRow | None = None

        self.setWindowTitle("Settings")
        self.setModal(True)
        self.setMinimumSize(860, 600)
        self.resize(980, 680)
        self.setSizeGripEnabled(True)

        self._build_ui()
        self._load_shortcut_values()
        self.set_theme(self._theme)

    # ------------------------------------------------------------ mw access
    def _load_eq(self) -> EQController:
        """Build the working EQ copy from the prefs copy (tolerant)."""
        data = {
            "enabled": bool(self._prefs.get("eq_enabled", False)),
            "preset": str(self._prefs.get("eq_preset", "Flat")),
            "gains": self._prefs.get("eq_gains"),
        }
        try:
            return EQController.from_dict(data)
        except (TypeError, ValueError):
            return EQController(enabled=bool(data["enabled"]))

    def _load_shortcut_defs(self) -> list[tuple[str, str, str]]:
        """Shortcut definitions from ``mw.shortcut_defs()`` or fallback."""
        fn = getattr(self._mw, "shortcut_defs", None)
        raw = None
        if callable(fn):
            try:
                raw = fn()
            except Exception:  # noqa: BLE001 - guarded by contract
                raw = None
        defs: list[tuple[str, str, str]] = []
        if raw:
            for entry in raw:
                try:
                    defs.append((str(entry[0]), str(entry[1]), str(entry[2])))
                except (TypeError, IndexError, KeyError):
                    continue
        return defs or list(DEFAULT_SHORTCUT_DEFS)

    def _load_shortcut_values(self) -> None:
        """Current sequence per action: custom override or default."""
        custom = self._prefs.get("custom_shortcuts")
        custom = custom if isinstance(custom, dict) else {}
        self._shortcuts = {
            aid: str(custom.get(aid, default))
            for aid, _title, default in self._shortcut_defs
        }
        self._refresh_shortcut_table()

    # ------------------------------------------------------------- UI build
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_side())
        body.addWidget(self._build_stack(), 1)
        root.addLayout(body, 1)
        root.addWidget(self._build_bottom_bar())

    def _build_side(self) -> QFrame:
        """Left navigation: search field + category buttons."""
        side = QFrame()
        side.setObjectName("SettingsSide")
        side.setFixedWidth(self.SIDE_WIDTH)
        self._side = side
        lay = QVBoxLayout(side)
        lay.setContentsMargins(10, 14, 10, 12)
        lay.setSpacing(4)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Search settings…")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.textChanged.connect(self._on_search_changed)
        lay.addWidget(self._search_edit)
        lay.addSpacing(8)

        self._cat_group = QButtonGroup(self)
        self._cat_group.setExclusive(True)
        for cat, label in self.CATEGORIES:
            btn = QPushButton(label.replace("&", "&&"))  # literal ampersand
            btn.setObjectName("SettingsCatBtn")
            btn.setCheckable(True)
            btn.setChecked(cat == self._current_category)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setIconSize(QSize(16, 16))
            btn.clicked.connect(lambda _=False, c=cat: self._show_category(c))
            self._cat_group.addButton(btn)
            lay.addWidget(btn)
            self._cat_btns[cat] = btn
            self._icon_targets.append((btn, self.CATEGORY_ICONS[cat]))
        lay.addStretch(1)
        return side

    def _build_stack(self) -> QStackedWidget:
        """Right pane: one scroll page per category + search results."""
        self._stack = QStackedWidget()
        builders: dict[str, Callable[[_Section], None]] = {
            "appearance": self._build_appearance,
            "playback": self._build_playback,
            "audio": self._build_audio,
            "library": self._build_library,
            "lyrics": self._build_lyrics,
            "stats": self._build_stats,
            "integrations": self._build_integrations,
            "shortcuts": self._build_shortcuts,
            "advanced": self._build_advanced,
            "about": self._build_about,
        }
        for cat, label in self.CATEGORIES:
            scroll, section = self._make_page(label)
            self._pages[cat] = scroll
            self._page_sections[cat] = section
            builders[cat](section)
            section.add_stretch()   # trailing stretch AFTER all rows
        self._make_search_page()
        return self._stack

    def _make_page(self, header: str) -> tuple[QScrollArea, _Section]:
        """Create one scrollable category page; returns (scroll, section)."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content.setObjectName("SettingsContent")
        lay = QVBoxLayout(content)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(2)
        head = QLabel(header)
        head.setObjectName("PageTitle")
        section = _Section(lay)
        section.add(head)
        scroll.setWidget(content)
        self._stack.addWidget(scroll)
        self._sections.append(section)
        return scroll, section

    def _make_group(self, page: _Section, title: str) -> _Section:
        """Create a QGroupBox sub-section inside *page*."""
        box = QGroupBox(title)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(10, 6, 10, 8)
        lay.setSpacing(2)
        section = _Section(lay)
        page.add(box)
        self._sections.append(section)
        return section

    def _make_search_page(self) -> None:
        """The flat 'Search results' page (rows borrowed from categories)."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content.setObjectName("SettingsContent")
        lay = QVBoxLayout(content)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(2)
        head = QLabel("Search results")
        head.setObjectName("PageTitle")
        self._search_count_lbl = QLabel("")
        self._search_count_lbl.setObjectName("PageSubtitle")
        rows_lay = QVBoxLayout()
        self._search_section = _Section(rows_lay)
        lay.addWidget(head)
        lay.addWidget(self._search_count_lbl)
        lay.addLayout(rows_lay)
        lay.addStretch(1)
        scroll.setWidget(content)
        self._stack.addWidget(scroll)
        self._search_scroll = scroll
        self._no_results_row = SettingRow(
            "No settings found",
            "No setting matches your search. Try another term.",
            category="Search",
        )

    def _build_bottom_bar(self) -> QWidget:
        """Reset Defaults / Cancel / OK."""
        bar = QWidget()
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(8)

        reset_btn = QPushButton("Reset Defaults")
        reset_btn.setObjectName("GhostBtn")
        reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reset_btn.clicked.connect(self._reset_defaults)
        lay.addWidget(reset_btn)
        self._icon_targets.append((reset_btn, Icon.REFRESH))

        lay.addStretch(1)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("GhostBtn")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)
        lay.addWidget(cancel_btn)
        self._icon_targets.append((cancel_btn, Icon.CLOSE))

        ok_btn = QPushButton("OK")
        ok_btn.setObjectName("GoldBtn")
        ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._collect_and_apply)
        lay.addWidget(ok_btn)
        self._icon_targets.append((ok_btn, Icon.CHECK))
        return bar

    # ------------------------------------------------------- row framework
    def _category_label(self) -> str:
        """Display label of the category currently being built."""
        for cat, label in self.CATEGORIES:
            if cat == getattr(self, "_build_cat", self._current_category):
                return label
        return self.CATEGORIES[0][1]

    def _register_row(self, section: _Section, row: SettingRow) -> SettingRow:
        """Append a row to a section and to the searchable row list."""
        section.add(row)
        self._rows.append(row)
        return row

    def add_toggle(self, section: _Section, title: str, description: str,
                   key: str, keywords: Sequence[str] = ()) -> SettingRow:
        """Boolean row backed by a checkbox; writes ``prefs[key]`` live."""
        cb = QCheckBox()
        cb.setChecked(bool(self._prefs.get(key, False)))
        cb.setCursor(Qt.CursorShape.PointingHandCursor)
        row = SettingRow(title, description, cb, keywords=keywords,
                         category=self._category_label(), key=key)
        cb.toggled.connect(lambda on, k=key: self._prefs.__setitem__(k, bool(on)))
        row.read = lambda cb=cb, k=key: cb.isChecked()
        row.sync = lambda cb=cb, k=key: cb.setChecked(
            bool(self._prefs.get(k, False)))
        return self._register_row(section, row)

    def add_combo(self, section: _Section, title: str, description: str,
                  key: str, options: Sequence[tuple[Any, str]],
                  keywords: Sequence[str] = ()) -> SettingRow:
        """Combo row; ``options`` is a list of ``(value, label)`` pairs."""
        combo = QComboBox()
        for value, label in options:
            combo.addItem(str(label), value)
        combo.setCursor(Qt.CursorShape.PointingHandCursor)
        combo.setMinimumWidth(170)

        def find_index(value: Any) -> int:
            for i in range(combo.count()):
                if combo.itemData(i) == value:
                    return i
            return 0

        combo.setCurrentIndex(find_index(self._prefs.get(key)))
        row = SettingRow(title, description, combo, keywords=keywords,
                         category=self._category_label(), key=key)
        combo.currentIndexChanged.connect(
            lambda i, k=key, c=combo: self._prefs.__setitem__(k, c.itemData(i)))
        row.read = lambda c=combo: c.currentData()
        row.sync = lambda c=combo, k=key, f=find_index: c.setCurrentIndex(
            f(self._prefs.get(k)))
        return self._register_row(section, row)

    def add_spin(self, section: _Section, title: str, description: str,
                 key: str, lo: int, hi: int, step: int = 1,
                 suffix: str = "", keywords: Sequence[str] = ()) -> SettingRow:
        """Integer spin-box row."""
        spin = QSpinBox()
        spin.setRange(int(lo), int(hi))
        spin.setSingleStep(int(step))
        if suffix:
            spin.setSuffix(suffix)
        try:
            spin.setValue(int(self._prefs.get(key, lo)))
        except (TypeError, ValueError):
            spin.setValue(int(lo))
        row = SettingRow(title, description, spin, keywords=keywords,
                         category=self._category_label(), key=key)
        spin.valueChanged.connect(
            lambda v, k=key: self._prefs.__setitem__(k, int(v)))
        row.read = lambda s=spin: s.value()

        def sync_spin(s: QSpinBox = spin, k: str = key,
                      lo: int = int(lo), hi: int = int(hi)) -> None:
            raw = self._prefs.get(k, lo)
            try:
                value = int(raw)
            except (TypeError, ValueError):
                value = lo
            s.setValue(max(lo, min(hi, value)))

        row.sync = sync_spin
        return self._register_row(section, row)

    def add_slider(self, section: _Section, title: str, description: str,
                   key: str, lo: float, hi: float, step: float = 1.0,
                   suffix: str = "", keywords: Sequence[str] = (),
                   live_cb: Callable[[float], None] | None = None,
                   default: float | None = None
                   ) -> SettingRow:
        """Full-width slider row (control rendered below the text).

        Float ranges are supported via an internal step index.  *default*
        is the value used when the key is missing from prefs (falls back
        to ``lo``).  When *live_cb* is given it is invoked on every
        change (guarded — an exception in the callback never breaks the
        dialog).
        """
        fallback = float(default) if default is not None else float(lo)
        steps = max(1, int(round((float(hi) - float(lo)) / float(step))))
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(0, steps)
        slider.setCursor(Qt.CursorShape.PointingHandCursor)
        value_lbl = QLabel("")
        value_lbl.setObjectName("SettingValue")

        def to_value(index: int) -> float:
            return round(float(lo) + index * float(step), 6)

        def fmt(value: float) -> str:
            return f"{_fmt_slider_value(value, step)}{suffix}"

        try:
            initial = float(self._prefs.get(key, fallback))
        except (TypeError, ValueError):
            initial = float(lo)
        initial = max(float(lo), min(float(hi), initial))
        slider.setValue(int(round((initial - float(lo)) / float(step))))
        value_lbl.setText(fmt(to_value(slider.value())))

        def on_change(index: int, k: str = key) -> None:
            value = to_value(index)
            self._prefs[k] = value
            value_lbl.setText(fmt(value))
            if live_cb is not None:
                try:
                    live_cb(value)
                except Exception:  # noqa: BLE001 - live hooks are best-effort
                    pass

        slider.valueChanged.connect(on_change)
        holder = QWidget()
        hlay = QHBoxLayout(holder)
        hlay.setContentsMargins(0, 0, 0, 0)
        hlay.setSpacing(10)
        hlay.addWidget(slider, 1)
        hlay.addWidget(value_lbl, 0, Qt.AlignmentFlag.AlignRight)
        row = SettingRow(title, description, holder, keywords=keywords,
                         category=self._category_label(), key=key,
                         control_below=True)
        row.read = lambda s=slider: to_value(s.value())

        def sync_slider(s: QSlider = slider, k: str = key,
                        lo: float = float(lo), hi: float = float(hi),
                        st: float = float(step),
                        fb: float = fallback) -> None:
            raw = self._prefs.get(k, fb)
            try:
                value = float(raw)
            except (TypeError, ValueError):
                value = fb
            value = max(lo, min(hi, value))
            s.setValue(int(round((value - lo) / st)))

        row.sync = sync_slider
        return self._register_row(section, row)

    def add_text(self, section: _Section, title: str, description: str,
                 key: str, keywords: Sequence[str] = (),
                 placeholder: str = "", password: bool = False
                 ) -> SettingRow:
        """Text row; ``password=True`` uses password echo mode."""
        edit = QLineEdit()
        edit.setMinimumWidth(260)
        if placeholder:
            edit.setPlaceholderText(placeholder)
        if password:
            edit.setEchoMode(QLineEdit.EchoMode.Password)
        edit.setText(str(self._prefs.get(key, "") or ""))
        row = SettingRow(title, description, edit, keywords=keywords,
                         category=self._category_label(), key=key)
        edit.textChanged.connect(
            lambda text, k=key: self._prefs.__setitem__(k, str(text)))
        row.read = lambda e=edit: e.text()
        row.sync = lambda e=edit, k=key: e.setText(
            str(self._prefs.get(k, "") or ""))
        return self._register_row(section, row)

    def add_button_row(self, section: _Section, title: str, description: str,
                       buttons: Sequence[tuple[str, str | None,
                                               Callable[[], None]]],
                       keywords: Sequence[str] = ()) -> SettingRow:
        """Row of action buttons: ``[(label, icon, callback), ...]``."""
        holder = QWidget()
        hlay = QHBoxLayout(holder)
        hlay.setContentsMargins(0, 0, 0, 0)
        hlay.setSpacing(8)
        for label, icon, callback in buttons:
            btn = QPushButton(str(label))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            if icon:
                btn.setIconSize(QSize(16, 16))
                self._icon_targets.append((btn, icon))
            btn.clicked.connect(lambda _=False, cb=callback: cb())
            hlay.addWidget(btn)
        row = SettingRow(title, description, holder, keywords=keywords,
                         category=self._category_label())
        return self._register_row(section, row)

    def add_custom(self, section: _Section, widget: QWidget, title: str,
                   description: str = "", keywords: Sequence[str] = (),
                   key: str | None = None,
                   read_fn: Callable[[], Any] | None = None,
                   sync_fn: Callable[[], None] | None = None,
                   control_below: bool = False) -> SettingRow:
        """Row wrapping an arbitrary *widget* as the control."""
        row = SettingRow(title, description, widget, keywords=keywords,
                         category=self._category_label(), key=key,
                         control_below=control_below)
        row.read = read_fn
        row.sync = sync_fn
        return self._register_row(section, row)

    def add_info(self, section: _Section, title: str, description: str,
                 keywords: Sequence[str] = ()) -> SettingRow:
        """Info-only row (no control)."""
        row = SettingRow(title, description, keywords=keywords,
                         category=self._category_label())
        return self._register_row(section, row)

    # ------------------------------------------------------------ pages
    def _build_appearance(self, section: _Section) -> None:
        self._build_cat = "appearance"
        theme_name = str(self._prefs.get("theme_name",
                                         self._theme.get("name", "royal_gold")))
        self.add_button_row(
            section, "Theme",
            f"Current: {Theme.display_name(theme_name)}",
            [("Change Theme…", Icon.BRUSH,
              lambda: _call(self._mw, "request_theme_picker"))],
            keywords=["theme", "skin", "color", "palette", "accent",
                      "appearance", "look"])

        self.add_slider(
            section, "Interface scale",
            "Scales fonts and layout density across the whole app.",
            "ui_scale", 0.75, 1.5, 0.05, "×",
            keywords=["scale", "zoom", "ui", "size", "font", "dpi"],
            live_cb=lambda v: self._live_ui_scale(v), default=1.0)
        self.add_toggle(
            section, "Reduce animations",
            "Calmer, flat hover states — better for motion sensitivity.",
            "reduce_animations", ["animation", "motion", "accessibility",
                                  "reduce", "transition"])
        self.add_combo(
            section, "Density", "Row padding in lists and tables.",
            "density",
            [("normal", "Normal"), ("compact", "Compact"),
             ("relaxed", "Relaxed")],
            keywords=["density", "compact", "relaxed", "spacing", "padding"])
        self.add_toggle(
            section, "Show covers in list",
            "Display album art thumbnails next to tracks.",
            "show_covers_in_list", ["covers", "artwork", "thumbnails", "list"])
        self.add_combo(
            section, "Library view", "How the track list is presented.",
            "list_view", [("rows", "Rows"), ("table", "Table")],
            keywords=["list", "view", "table", "rows", "columns"])
        self.add_toggle(
            section, "Dynamic album theme",
            "Extract accent colors from album art for the player view.",
            "dynamic_album_theme", ["album", "dynamic", "accent", "color",
                                    "art", "theme"])
        self.add_combo(
            section, "Language", "Application language (restart to apply).",
            "language", [("en", "English"), ("fa", "Persian (فارسی)")],
            keywords=["language", "english", "persian", "farsi", "locale"])

    def _live_ui_scale(self, value: float) -> None:
        """Live-apply the interface scale (hook + this dialog's own QSS)."""
        _call(self._mw, "request_ui_scale", value)
        self.set_theme(self._theme)

    def _build_playback(self, section: _Section) -> None:
        self._build_cat = "playback"
        self.add_slider(
            section, "Crossfade",
            "Smoothly blend track transitions. 0 = gapless.",
            "crossfade_ms", 0, 12000, 250, " ms",
            keywords=["crossfade", "fade", "transition", "gap", "blend"])
        self.add_toggle(
            section, "Gapless playback",
            "Start the next track the instant the current one ends.",
            "gapless", ["gapless", "gap", "seamless", "transition"])
        self.add_toggle(
            section, "Preserve pitch on speed change",
            "Keep the original pitch when playback speed is changed.",
            "pitch_preserve_speed", ["pitch", "speed", "tempo", "rate"])
        self.add_toggle(
            section, "Skip silence",
            "Automatically jump over long silent passages.",
            "silence_skip", ["silence", "skip", "quiet", "gap"])
        self.add_toggle(
            section, "Bookmarks",
            "Remember the position for long files (audiobooks, mixes).",
            "bookmarks_enabled", ["bookmarks", "position", "resume",
                                  "audiobook", "long"])
        self.add_toggle(
            section, "Fade in / out",
            "Legacy fade of volume on play / pause / stop.",
            "fade_enabled", ["fade", "fade in", "fade out", "volume"])
        self.add_slider(
            section, "Fade duration", "Legacy fade length.",
            "fade_ms", 100, 2000, 50, " ms",
            keywords=["fade", "duration", "ms", "legacy"])
        self.add_info(
            section, "Sleep timer",
            "Start and stop the sleep timer from the player bar timer "
            "button; the remaining minutes are shown there.",
            keywords=["sleep", "timer", "minutes", "shutdown"])

    def _build_audio(self, section: _Section) -> None:
        self._build_cat = "audio"
        self.add_combo(
            section, "Audio engine",
            "mpv unlocks EQ / crossfade extras; falls back automatically.",
            "audio_engine",
            [("auto", "Auto (best available)"), ("mpv", "mpv"),
             ("qt", "Qt Multimedia")],
            keywords=["engine", "mpv", "qt", "backend", "audio", "output"])
        self.add_combo(
            section, "Volume normalization",
            "Level loudness differences between tracks.",
            "normalization",
            [("off", "Off"), ("tags", "By tags (album / track gain)")],
            keywords=["normalization", "volume", "loudness", "replaygain",
                      "gain"])

        eq_group = self._make_group(section, "Equalizer")
        eq_toggle = self.add_toggle(
            eq_group, "Enable equalizer",
            "Apply the 10-band ISO curve to playback (mpv engine).",
            "eq_enabled", ["eq", "equalizer", "enable", "bands"])
        eq_checkbox = eq_toggle.control
        self._eq_combo = QComboBox()
        self._eq_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        for name in PRESETS:
            self._eq_combo.addItem(name, name)
        self._eq_combo.currentIndexChanged.connect(
            lambda i, c=self._eq_combo: self._on_eq_preset_chosen(
                str(c.itemData(i))))
        preset_row = self.add_custom(
            eq_group, self._eq_combo, "Preset",
            "Named curves; moving a slider switches to Custom.",
            keywords=["preset", "eq", "equalizer", "flat", "bass", "rock",
                      "night", "vocal", "treble"], key="eq_preset",
            read_fn=lambda: str(self._eq.preset))
        preset_row.sync = lambda: self._sync_eq_combo()
        self._sync_eq_combo()

        self._eq_widget = self._make_eq_widget(eq_checkbox)
        self.add_custom(
            eq_group, self._eq_widget, "Equalizer curve",
            "10-band ISO equalizer, ±12 dB per band.",
            keywords=["eq", "equalizer", "bands", "slider", "bass", "treble",
                      "gain", "curve", "hz", "khz"],
            control_below=True)

        eq_btns = QWidget()
        blay = QHBoxLayout(eq_btns)
        blay.setContentsMargins(0, 0, 0, 0)
        blay.setSpacing(8)
        reset_btn = QPushButton("Reset (flat)")
        reset_btn.clicked.connect(self._eq_reset)
        save_btn = QPushButton("Save as Custom preset")
        save_btn.clicked.connect(self._eq_save_custom)
        blay.addWidget(reset_btn)
        blay.addWidget(save_btn)
        blay.addStretch(1)
        self.add_custom(
            eq_group, eq_btns, "Curve actions",
            "Reset flattens all bands; Save marks the current curve as "
            "the Custom preset.",
            keywords=["eq", "reset", "flat", "custom", "save", "preset"],
            control_below=True)

        self.add_button_row(
            section, "Output device",
            "Choose where audio is played.",
            [("Choose Output…", Icon.SPEAKER,
              lambda: _call(self._mw, "request_output_picker"))],
            keywords=["output", "device", "speaker", "audio", "sink",
                      "headphones"])

    def _make_eq_widget(self, enable_checkbox: QCheckBox | None) -> QWidget:
        """The 10 vertical band sliders with Hz labels."""
        widget = QWidget()
        lay = QHBoxLayout(widget)
        lay.setContentsMargins(4, 6, 4, 2)
        lay.setSpacing(4)
        for i in range(BAND_COUNT):
            col = QVBoxLayout()
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(4)
            slider = QSlider(Qt.Orientation.Vertical)
            slider.setObjectName("EQBandSlider")
            slider.setRange(int(MIN_GAIN_DB / _EQ_STEP_DB),
                            int(MAX_GAIN_DB / _EQ_STEP_DB))
            slider.setValue(int(round(self._eq.gains[i] / _EQ_STEP_DB)))
            slider.setMinimumHeight(110)
            slider.setCursor(Qt.CursorShape.PointingHandCursor)
            slider.setToolTip(
                f"{_fmt_hz(BAND_FREQS[i])}: {self._eq.gains[i]:+.1f} dB")
            slider.valueChanged.connect(
                lambda raw, i=i: self._on_eq_band(i, raw))
            freq_lbl = QLabel(_fmt_hz(BAND_FREQS[i]))
            freq_lbl.setObjectName("EqBandLabel")
            freq_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            col.addWidget(slider, 1)
            col.addWidget(freq_lbl)
            lay.addLayout(col, 1)
            self._eq_sliders.append(slider)
        if self._eq_combo is not None:
            self._eq_combo.setEnabled(bool(self._eq.enabled))
        widget.setEnabled(bool(self._eq.enabled))
        if enable_checkbox is not None:
            enable_checkbox.toggled.connect(
                lambda on, w=widget: self._on_eq_enabled(on, w))
        return widget

    def _on_eq_enabled(self, on: bool, widget: QWidget) -> None:
        """Enable / disable the whole EQ editor when the toggle flips."""
        self._eq.enabled = bool(on)
        widget.setEnabled(bool(on))
        if self._eq_combo is not None:
            self._eq_combo.setEnabled(bool(on))

    def _on_eq_band(self, index: int, raw: int) -> None:
        """A band slider moved — update the EQ copy (switches to Custom)."""
        db = raw * _EQ_STEP_DB
        self._eq.set_band(index, db)
        slider = self._eq_sliders[index]
        slider.setToolTip(
            f"{_fmt_hz(BAND_FREQS[index])}: {db:+.1f} dB")
        self._sync_eq_combo()

    def _on_eq_preset_chosen(self, name: str) -> None:
        """User picked a preset from the combo (Custom keeps the curve)."""
        if self._eq_ui_lock:
            return
        if name == "Custom":
            self._eq.preset = "Custom"
        else:
            self._eq.set_preset(name)
            self._sync_eq_sliders()

    def _sync_eq_sliders(self) -> None:
        """Re-position the band sliders from the EQ copy (no signals)."""
        for i, slider in enumerate(self._eq_sliders):
            slider.blockSignals(True)
            slider.setValue(int(round(self._eq.gains[i] / _EQ_STEP_DB)))
            slider.setToolTip(
                f"{_fmt_hz(BAND_FREQS[i])}: {self._eq.gains[i]:+.1f} dB")
            slider.blockSignals(False)

    def _sync_eq_combo(self) -> None:
        """Point the preset combo at the EQ copy's preset (no signals)."""
        if self._eq_combo is None:
            return
        self._eq_ui_lock = True
        idx = self._eq_combo.findData(self._eq.preset)
        self._eq_combo.setCurrentIndex(max(0, idx))
        self._eq_ui_lock = False

    def _eq_reset(self) -> None:
        """Reset button — flatten all bands."""
        self._eq.flat()
        self._eq.preset = "Flat"
        self._sync_eq_sliders()
        self._sync_eq_combo()

    def _eq_save_custom(self) -> None:
        """Save-as-Custom button — mark the current curve as Custom."""
        self._eq.preset = "Custom"
        self._sync_eq_combo()

    def _build_library(self, section: _Section) -> None:
        self._build_cat = "library"
        self.add_toggle(
            section, "Auto rescan",
            "Re-scan the library folders when the app starts.",
            "auto_rescan", ["rescan", "scan", "startup", "refresh"])
        self.add_toggle(
            section, "Watch folders",
            "Live folder watching with debouncing.",
            "folder_watch", ["watch", "folder", "live", "monitor", "debounce"])
        self.add_spin(
            section, "Watch debounce",
            "How long folder changes settle before a rescan.",
            "folder_watch_debounce_ms", 250, 10000, 250, " ms",
            keywords=["debounce", "watch", "folder", "ms", "delay"])

        smart = self._make_group(section, "Smart playlists")
        self.add_info(
            smart, "Opt-in playlists",
            "Each smart playlist appears in the Playlists page only when "
            "enabled here.",
            keywords=["smart", "playlist", "opt-in", "automatic"])
        self.add_toggle(
            smart, "Most played",
            "Tracks with the highest play counts.",
            "smart_most_played", ["smart", "most", "played", "playlist",
                                  "top"])
        self.add_toggle(
            smart, "Recently added",
            "The newest additions to your library.",
            "smart_recently_added", ["smart", "recent", "added", "new",
                                     "playlist"])
        self.add_toggle(
            smart, "Never played",
            "Tracks you have never listened to.",
            "smart_never_played", ["smart", "never", "unplayed", "playlist"])
        self.add_toggle(
            smart, "Not heard recently",
            "Favorites you have not played in a while.",
            "smart_not_heard_recently", ["smart", "not", "heard", "recently",
                                         "forgotten", "playlist"])
        self.add_toggle(
            smart, "Top rated",
            "Your highest rated tracks.",
            "smart_top_rated", ["smart", "top", "rated", "rating", "stars",
                                "playlist"])

        self.add_button_row(
            section, "Playlists import / export",
            "Move playlists in and out of the app via M3U files.",
            [("Import M3U…", Icon.DOWNLOAD,
              lambda: _call(self._mw, "request_m3u_import")),
             ("Export all playlists", Icon.UPLOAD,
              lambda: _call(self._mw, "request_m3u_export"))],
            keywords=["m3u", "import", "export", "playlist", "playlists"])
        self.add_toggle(
            section, "Portable M3U paths",
            "Write relative paths so playlists stay portable.",
            "m3u_relative_paths", ["m3u", "relative", "portable", "paths",
                                   "playlist"])

    def _build_lyrics(self, section: _Section) -> None:
        self._build_cat = "lyrics"
        self.add_toggle(
            section, "Search online",
            "Look lyrics up online when nothing is stored locally.",
            "lyrics_online", ["online", "search", "lyrics", "internet",
                              "fetch"])
        self.add_toggle(
            section, "Show translation",
            "Bilingual by-line: show a translation under each lyric line.",
            "lyrics_show_translation", ["translation", "bilingual", "lyrics",
                                        "dual", "language"])
        self.add_combo(
            section, "Save mode",
            "Where fetched lyrics are stored.",
            "lyrics_save_mode",
            [("ask", "Ask every time"), ("embed", "Embed in file tags"),
             ("sidecar", "Sidecar .lrc file")],
            keywords=["save", "embed", "sidecar", "lrc", "lyrics", "mode"])
        self.add_toggle(
            section, "Quality check",
            "Detect short / spam lyrics and offer better ones.",
            "lyrics_quality_check", ["quality", "spam", "short", "lyrics",
                                     "check", "better"])
        self.add_toggle(
            section, "Floating lyrics window",
            "Show synchronized lyrics in a desktop-wide overlay.",
            "floating_lyrics", ["floating", "desktop", "overlay", "lyrics",
                                "window"])
        self.add_slider(
            section, "Lyrics font scale",
            "Text size of the lyrics panel and overlay.",
            "lyrics_font_scale", 0.8, 1.6, 0.05, "×",
            keywords=["lyrics", "font", "scale", "size", "text"],
            default=1.0)

    def _build_stats(self, section: _Section) -> None:
        self._build_cat = "stats"
        self.add_toggle(
            section, "Listening charts",
            "Bar / line charts of your listening activity.",
            "stats_charts_enabled", ["charts", "graph", "stats", "activity"])
        self.add_toggle(
            section, "Play heatmap",
            "Calendar heatmap of plays per day.",
            "stats_heatmap_enabled", ["heatmap", "calendar", "stats",
                                      "plays"])
        self.add_toggle(
            section, "Wrapped recap",
            "Your yearly listening recap, generated locally.",
            "stats_wrapped_enabled", ["wrapped", "recap", "yearly", "stats",
                                      "annual"])
        self.add_info(
            section, "Privacy",
            "All statistics are anonymous and local-only: play counts, "
            "the heatmap and your recap never leave this computer.",
            keywords=["stats", "privacy", "anonymous", "local"])

    def _build_integrations(self, section: _Section) -> None:
        self._build_cat = "integrations"

        web = self._make_group(section, "Web remote")
        self.add_toggle(
            web, "Enable web remote",
            "Control the player from a browser (LAN-only, token auth).",
            "webremote_enabled",
            keywords=["web", "remote", "browser", "lan", "server", "http"])
        self.add_combo(
            web, "Bind address",
            "Who may reach the web remote.",
            "webremote_host",
            [("127.0.0.1", "This PC only"), ("0.0.0.0", "LAN")],  # nosec B104
            keywords=["host", "bind", "address", "lan", "localhost",
                      "remote", "web"])

        self.add_spin(
            web, "Port", "TCP port for the web remote.",
            "webremote_port", 1024, 65535, 1, "",
            keywords=["port", "web", "remote", "tcp"])
        self._build_token_row(web)

        tg = self._make_group(section, "Telegram")
        self.add_toggle(
            tg, "Enable Telegram bot",
            "Control playback from a Telegram chat (off by default).",
            "telegram_enabled", ["telegram", "bot", "chat", "remote"])
        self.add_text(
            tg, "Bot token", "Token from @BotFather.",
            "telegram_bot_token", ["telegram", "bot", "token", "secret"],
            placeholder="123456:ABC-DEF…", password=True)
        self.add_text(
            tg, "Allowed chat IDs",
            "Comma-separated numeric IDs allowed to talk to the bot.",
            "telegram_allowed_ids", ["telegram", "allowed", "ids", "chats",
                                     "users"],
            placeholder="e.g. 12345678, 87654321")
        self.add_button_row(
            tg, "Test connection",
            "Send a test message through the configured bot.",
            [("Send test message", Icon.SEND,
              lambda: _call(self._mw, "request_telegram_test"))],
            keywords=["telegram", "test", "message", "send", "check"])

        upd = self._make_group(section, "Updates")
        self.add_toggle(
            upd, "Check for updates",
            "Look for new releases in the background.",
            "updates_enabled", ["updates", "upgrade", "version", "release"])
        self.add_button_row(
            upd, "Update check", "Check for a new version right now.",
            [("Check now", Icon.REFRESH,
              lambda: _call(self._mw, "request_update_check"))],
            keywords=["update", "check", "version", "release", "now"])

    def _build_token_row(self, section: _Section) -> None:
        """Web-remote token: masked text + Regenerate + Show QR buttons."""
        holder = QWidget()
        hlay = QHBoxLayout(holder)
        hlay.setContentsMargins(0, 0, 0, 0)
        hlay.setSpacing(8)
        edit = QLineEdit()
        edit.setEchoMode(QLineEdit.EchoMode.Password)
        edit.setMinimumWidth(210)
        edit.setText(str(self._prefs.get("webremote_token", "") or ""))
        regen_btn = QPushButton("Regenerate")
        regen_btn.clicked.connect(
            lambda _=False, e=edit: e.setText(secrets.token_hex(16)))
        qr_btn = QPushButton("Show QR")
        qr_btn.clicked.connect(
            lambda _=False: _call(self._mw, "request_pairing_qr"))
        hlay.addWidget(edit, 1)
        hlay.addWidget(regen_btn)
        hlay.addWidget(qr_btn)

        def write(text: str) -> None:
            self._prefs["webremote_token"] = str(text)

        edit.textChanged.connect(write)
        row = self.add_custom(
            section, holder, "Pairing token",
            "Token authenticating web-remote requests; scan the QR on "
            "another device to pair.",
            keywords=["token", "web", "remote", "auth", "pairing", "qr",
                      "secret"], key="webremote_token",
            read_fn=lambda e=edit: e.text(),
            sync_fn=lambda e=edit: e.setText(
                str(self._prefs.get("webremote_token", "") or "")))

    def _build_shortcuts(self, section: _Section) -> None:
        self._build_cat = "shortcuts"
        self._sc_table = QTableWidget(len(self._shortcut_defs), 3)
        self._sc_table.setObjectName("ShortcutTable")
        self._sc_table.setHorizontalHeaderLabels(["Action", "Shortcut", ""])
        self._sc_table.verticalHeader().setVisible(False)
        self._sc_table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection)
        self._sc_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._sc_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        header = self._sc_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

        for r, (aid, title, _default) in enumerate(self._shortcut_defs):
            action_item = QTableWidgetItem(title)
            self._sc_table.setItem(r, 0, action_item)
            seq_item = QTableWidgetItem("")
            self._sc_table.setItem(r, 1, seq_item)
            btns = QWidget()
            blay = QHBoxLayout(btns)
            blay.setContentsMargins(4, 2, 4, 2)
            blay.setSpacing(6)
            edit_btn = QPushButton("Edit")
            edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            edit_btn.clicked.connect(
                lambda _=False, a=aid: self._edit_shortcut(a))
            reset_btn = QPushButton("Reset")
            reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            reset_btn.clicked.connect(
                lambda _=False, a=aid: self._reset_shortcut(a))
            blay.addWidget(edit_btn)
            blay.addWidget(reset_btn)
            blay.addStretch(1)
            self._sc_table.setCellWidget(r, 2, btns)
        self._sc_table.setMinimumHeight(len(self._shortcut_defs) * 38 + 34)
        section.add(self._sc_table)

        self.add_button_row(
            section, "Reset all shortcuts",
            "Restore every action to its default key combination.",
            [("Reset All Shortcuts", Icon.REFRESH,
              self._reset_all_shortcuts)],
            keywords=["reset", "all", "shortcuts", "keys", "restore",
                      "default"])
        self.add_info(
            section, "Conflicts",
            "Duplicate combinations are highlighted in red with a tooltip "
            "naming the conflicting action.",
            keywords=["conflict", "duplicate", "shortcuts", "keys", "red"])

    def _build_advanced(self, section: _Section) -> None:
        self._build_cat = "advanced"
        self._build_folder_row(section)
        self.add_toggle(
            section, "Toast notifications",
            "Small in-app popups for track changes and actions.",
            "toast_enabled", ["toast", "notification", "popup", "snackbar"])
        self.add_toggle(
            section, "Close to tray",
            "Keep the app running in the system tray when closing.",
            "taskbar_close_to_tray", ["tray", "close", "minimize", "taskbar",
                                      "background"])
        self._build_log_row(section)
        self._build_cache_row(section)

        danger = self._make_group(section, "Dangerous")
        reset_all = QPushButton("Reset all settings")
        reset_all.setObjectName("DangerBtn")
        reset_all.setCursor(Qt.CursorShape.PointingHandCursor)
        reset_all.clicked.connect(self._factory_reset)
        dlay = QHBoxLayout()
        dlay.setContentsMargins(0, 0, 0, 0)
        dlay.addWidget(reset_all)
        dlay.addStretch(1)
        holder = QWidget()
        holder.setLayout(dlay)
        self.add_custom(
            danger, holder, "Factory reset",
            "Wipe every stored setting and return the app to a fresh "
            "install state (asks for confirmation).",
            keywords=["reset", "factory", "wipe", "dangerous", "clear",
                      "all", "settings"],
            control_below=True)

    def _build_folder_row(self, section: _Section) -> None:
        """Default music folder: text field + browse button."""
        holder = QWidget()
        hlay = QHBoxLayout(holder)
        hlay.setContentsMargins(0, 0, 0, 0)
        hlay.setSpacing(8)
        edit = QLineEdit()
        edit.setMinimumWidth(320)
        edit.setText(str(self._prefs.get("default_folder", "") or ""))
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(
            lambda _=False, e=edit: self._browse_default_folder(e))
        hlay.addWidget(edit, 1)
        hlay.addWidget(browse_btn)
        edit.textChanged.connect(
            lambda text, k="default_folder": self._prefs.__setitem__(k,
                                                                     str(text)))
        self.add_custom(
            section, holder, "Default music folder",
            "Where the library browser starts; scanned on first run.",
            keywords=["default", "folder", "music", "library", "path",
                      "directory", "browse"],
            key="default_folder",
            read_fn=lambda e=edit: e.text(),
            sync_fn=lambda e=edit: e.setText(
                str(self._prefs.get("default_folder", "") or "")))

    def _browse_default_folder(self, edit: QLineEdit) -> None:
        """QFileDialog browse for the default folder."""
        current = edit.text().strip()
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose default music folder", current)
        if chosen:
            edit.setText(chosen)

    def _build_log_row(self, section: _Section) -> None:
        """Log file path + Open Log button (hook-guarded)."""
        path = str(getattr(self._mw, "log_path", "") or "")
        desc = f"Log file: {path}" if path else "The application log file."
        self.add_button_row(
            section, "Log file", desc,
            [("Open Log", Icon.FILE, lambda: _call(self._mw,
                                                   "request_open_log"))],
            keywords=["log", "file", "debug", "open", "diagnostics"])

    def _build_cache_row(self, section: _Section) -> None:
        """Clear lyrics / waveform caches (hook-guarded)."""
        self.add_button_row(
            section, "Caches",
            "Cached lyrics and waveform data live on disk; clearing them "
            "frees space and forces a re-download / re-scan.",
            [("Clear lyrics cache", Icon.TRASH,
              lambda: _call(self._mw, "request_clear_lyrics_cache")),
             ("Clear waveform cache", Icon.TRASH,
              lambda: _call(self._mw, "request_clear_waveform_cache"))],
            keywords=["cache", "clear", "lyrics", "waveform", "disk",
                      "space", "purge"])

    def _build_about(self, section: _Section) -> None:
        self._build_cat = "about"
        version = str(getattr(self._mw, "app_version", "2.3.0"))
        logo_lbl = QLabel()
        logo_path = _ASSETS_DIR / "logo.png"
        pm = QPixmap(str(logo_path)) if logo_path.is_file() else QPixmap()
        if not pm.isNull():
            logo_lbl.setPixmap(pm.scaled(
                112, 112, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
            logo_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            section.add(logo_lbl)
        name_lbl = QLabel(f"{APP_NAME} {version}")
        name_lbl.setObjectName("PageTitle")
        name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        section.add(name_lbl)

        self.add_button_row(
            section, "Source code",
            "Browse the repository, report issues and read the docs.",
            [("GitHub repository", Icon.EXTERNAL,
              lambda: _call(self._mw, "request_open_repo"))],
            keywords=["github", "repo", "repository", "source", "code",
                      "link", "issues"])
        self.add_info(
            section, "License",
            "Released under the MIT License — free to use, modify and "
            "redistribute, with the copyright notice preserved "
            "(see the LICENSE file).",
            keywords=["license", "mit", "legal", "copyright"])
        self.add_info(
            section, "Credits",
            "Icons by Lucide · audio via mpv / Qt Multimedia · 10-band "
            "biquad EQ · made for people who love listening.",
            keywords=["credits", "icons", "lucide", "authors", "thanks"])
        made_lbl = QLabel("Made with PyQt6")
        made_lbl.setObjectName("PageSubtitle")
        made_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        section.add(made_lbl)

    # ------------------------------------------------------------- search
    def _on_search_changed(self, text: str) -> None:
        """Filter rows across all categories into the results page."""
        if self._search_section is None or self._search_scroll is None:
            return
        query = str(text).strip()
        self._restore_all_sections()
        self._search_section.clear()
        if not query:
            if self._search_count_lbl is not None:
                self._search_count_lbl.setText("")
            self._stack.setCurrentWidget(
                self._pages.get(self._current_category, self._stack.widget(0)))
            return
        matches = [row for row in self._rows if row.matches(query)]
        for row in matches:
            self._search_section.add(row)
        if matches:
            if self._no_results_row is not None:
                self._no_results_row.hide()
            if self._search_count_lbl is not None:
                self._search_count_lbl.setText(
                    f"{len(matches)} setting"
                    f"{'s' if len(matches) != 1 else ''} matching "
                    f"“{query}”")
        else:
            if self._no_results_row is not None:
                self._search_section.add(self._no_results_row)
                self._no_results_row.show()
            if self._search_count_lbl is not None:
                self._search_count_lbl.setText(
                    f"Nothing matches “{query}”")
        self._stack.setCurrentWidget(self._search_scroll)

    def _restore_all_sections(self) -> None:
        """Send every borrowed row back to its home section, in order."""
        for section in self._sections:
            section.rebuild()

    def _show_category(self, cat: str) -> None:
        """Switch to a category page (clearing any active search)."""
        self._current_category = cat
        if self._search_edit.text().strip():
            self._search_edit.clear()
            return
        page = self._pages.get(cat)
        if page is not None:
            self._stack.setCurrentWidget(page)

    # ---------------------------------------------------------- shortcuts
    def _edit_shortcut(self, action_id: str) -> None:
        """Open the small QKeySequenceEdit dialog for one action."""
        title = self._shortcut_title(action_id)
        current = self._shortcuts.get(action_id, "")
        dlg = QDialog(self)
        dlg.setWindowTitle("Edit shortcut")
        dlg.setModal(True)
        vlay = QVBoxLayout(dlg)
        vlay.setContentsMargins(16, 16, 16, 16)
        vlay.setSpacing(10)
        prompt = QLabel(f"Press a new key combination for:\n{title}")
        prompt.setWordWrap(True)
        vlay.addWidget(prompt)
        kse = QKeySequenceEdit(QKeySequence(current))
        vlay.addWidget(kse)
        btns = QWidget()
        blay = QHBoxLayout(btns)
        blay.setContentsMargins(0, 0, 0, 0)
        blay.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(dlg.reject)
        ok = QPushButton("OK")
        ok.setObjectName("GoldBtn")
        ok.setDefault(True)
        ok.clicked.connect(dlg.accept)
        blay.addWidget(cancel)
        blay.addWidget(ok)
        vlay.addWidget(btns)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            seq = kse.keySequence().toString()
            self._set_shortcut(action_id, seq)

    def _shortcut_title(self, action_id: str) -> str:
        """Display title of a shortcut action id."""
        for aid, title, _default in self._shortcut_defs:
            if aid == action_id:
                return title
        return action_id

    def _shortcut_default(self, action_id: str) -> str:
        """Default sequence of a shortcut action id."""
        for aid, _title, default in self._shortcut_defs:
            if aid == action_id:
                return default
        return ""

    def _set_shortcut(self, action_id: str, sequence: str) -> bool:
        """Set the current sequence for *action_id* (test entry point)."""
        if action_id not in self._shortcuts:
            return False
        self._shortcuts[action_id] = str(sequence)
        self._refresh_shortcut_table()
        return True

    def _reset_shortcut(self, action_id: str) -> None:
        """Reset one action to its default sequence."""
        self._set_shortcut(action_id, self._shortcut_default(action_id))

    def _reset_all_shortcuts(self) -> None:
        """Reset every action to its default sequence."""
        for aid, _title, _default in self._shortcut_defs:
            self._shortcuts[aid] = _default
        self._refresh_shortcut_table()

    def _shortcut_conflicts(self) -> dict[str, list[str]]:
        """Map of action id -> other action ids using the same sequence.

        Empty sequences (unbound actions) never conflict.
        """
        by_seq: dict[str, list[str]] = {}
        for aid, _title, _default in self._shortcut_defs:
            canon = _canon_seq(self._shortcuts.get(aid, ""))
            if canon:
                by_seq.setdefault(canon, []).append(aid)
        conflicts: dict[str, list[str]] = {}
        for group in by_seq.values():
            if len(group) > 1:
                for aid in group:
                    conflicts[aid] = sorted(x for x in group if x != aid)
        return conflicts

    def _refresh_shortcut_table(self) -> None:
        """Repaint the shortcut column + conflict highlights."""
        if self._sc_table is None:
            return
        conflicts = self._shortcut_conflicts()
        for r, (aid, _title, _default) in enumerate(self._shortcut_defs):
            item = self._sc_table.item(r, 1)
            if item is None:
                continue
            seq = self._shortcuts.get(aid, "")
            item.setText(seq if seq else "—")
            others = conflicts.get(aid)
            if others:
                item.setForeground(QColor("#e05050"))
                names = ", ".join(self._shortcut_title(o) for o in others)
                item.setToolTip(f"Conflicts with {names}")
            else:
                item.setForeground(QColor(str(self._theme.get("text",
                                                             "#ffffff"))))
                item.setToolTip("")

    # ------------------------------------------------------- reset / apply
    def _reset_defaults(self) -> None:
        """Reset Defaults: clear the prefs copy back to app defaults."""
        self._prefs = {}
        self._eq = EQController()
        for aid, _title, default in self._shortcut_defs:
            self._shortcuts[aid] = default
        self._search_edit.clear()
        self._sync_ui_from_prefs()
        self._refresh_shortcut_table()

    def _factory_reset(self) -> None:
        """Dangerous reset: wipe everything (flagged) after confirmation."""
        answer = QMessageBox.question(
            self, "Reset all settings",
            "This wipes every stored setting and returns the app to a "
            "fresh-install state.\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._reset_defaults()
        self._prefs = {RESET_FLAG_KEY: True}

    def _sync_ui_from_prefs(self) -> None:
        """Re-load every row control from the working prefs copy."""
        for row in self._rows:
            sync = row.sync
            if sync is None:
                continue
            try:
                sync()
            except Exception:  # noqa: BLE001 - one bad row must not kill sync
                continue
        self._sync_eq_sliders()
        self._sync_eq_combo()

    def _collect(self) -> None:
        """Flush all controls into the working prefs copy."""
        for row in self._rows:
            if row.key is not None and row.read is not None:
                try:
                    self._prefs[row.key] = row.read()
                except Exception:  # noqa: BLE001 - defensive collection
                    continue
        self._prefs["eq_enabled"] = bool(self._eq.enabled)
        self._prefs["eq_preset"] = str(self._eq.preset)
        self._prefs["eq_gains"] = [float(g) for g in self._eq.gains]
        custom: dict[str, str] = {}
        for aid, _title, default in self._shortcut_defs:
            current = self._shortcuts.get(aid, default)
            if _canon_seq(current) != _canon_seq(default):
                custom[aid] = str(current)
        self._prefs["custom_shortcuts"] = custom

    def _collect_and_apply(self) -> None:
        """OK: collect everything, hand the prefs to the window, close."""
        self._collect()
        payload = copy.deepcopy(self._prefs)
        apply_fn = getattr(self._mw, "apply_prefs", None)
        if callable(apply_fn):
            try:
                apply_fn(payload)
            except Exception:  # noqa: BLE001 - window owns error handling
                pass
        _call(self._mw, "request_theme_refresh")
        self.accept()

    # ------------------------------------------------------------- theme
    def set_theme(self, theme: dict) -> None:
        """Re-apply the QSS (build_qss_v3 + dialog extras) and icon tint.

        Uses the CURRENT ``ui_scale`` / ``reduce_animations`` values from
        the working prefs copy, so live scale changes re-skin the dialog
        immediately.
        """
        self._theme = _safe_theme(theme)
        try:
            scale = float(self._prefs.get("ui_scale", 1.0) or 1.0)
        except (TypeError, ValueError):
            scale = 1.0
        scale = max(0.75, min(1.5, scale))
        reduce_animations = bool(self._prefs.get("reduce_animations", False))
        try:
            qss = build_qss_v3(self._theme, scale, reduce_animations)
        except Exception:  # noqa: BLE001 - theme engine must never crash UI
            qss = ""
        self.setStyleSheet(qss + self._extra_qss(scale))
        self._retheme_icons()
        self._refresh_shortcut_table()

    def _extra_qss(self, scale: float) -> str:
        """Dialog-local QSS supplement (rows, EQ sliders, spin boxes...)."""
        t = self._theme
        fs = lambda px: f"{round(px * scale)}px"  # noqa: E731
        return f"""
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QWidget#SettingsContent {{ background: transparent; }}
QFrame#SettingRow {{
    background: transparent; border: none;
    border-bottom: 1px solid {t['border']};
}}
QLabel#SettingTitle {{ font-weight: 700; color: {t['text']}; }}
QLabel#SettingDesc {{ color: {t['muted']}; font-size: {fs(12.5)}; }}
QLabel#SettingValue {{
    color: {t['gold']}; font-weight: 700;
    min-width: 64px; font-size: {fs(12.5)};
}}
QLabel#EqBandLabel {{ color: {t['muted']}; font-size: {fs(11)}; }}
QSlider#EQBandSlider {{ background: transparent; width: 24px; }}
QSlider#EQBandSlider::groove:vertical {{
    width: 5px; border-radius: 2px; background: {t['panel_bg_2']};
}}
QSlider#EQBandSlider::sub-page:vertical {{
    width: 5px; border-radius: 2px; background: {t['gold']};
}}
QSlider#EQBandSlider::handle:vertical {{
    height: 14px; width: 14px; margin: 0 -4px; border-radius: 7px;
    background: {t['gold_light']};
    border: 2px solid {t['panel_bg']};
}}
QSpinBox {{
    background: {t['panel_bg_2']}; border: 1px solid {t['border']};
    border-radius: 9px; padding: 5px 4px 5px 10px; color: {t['text']};
}}
QSpinBox:focus {{ border-color: {t['gold']}; }}
QSpinBox::up-button {{
    subcontrol-origin: border; subcontrol-position: top right;
    width: 18px; background: transparent;
}}
QSpinBox::down-button {{
    subcontrol-origin: border; subcontrol-position: bottom right;
    width: 18px; background: transparent;
}}
QPushButton#DangerBtn {{
    background: transparent; color: #e05050;
    border: 1px solid #603030; border-radius: 9px; padding: 7px 14px;
}}
QPushButton#DangerBtn:hover {{
    background: rgba(224, 80, 80, 40); color: #ff7070;
}}
QTableWidget#ShortcutTable {{
    background: {t['panel_bg']}; border: 1px solid {t['border']};
    border-radius: 10px;
}}
QTableWidget#ShortcutTable::item {{ padding: 6px 8px; }}
QKeySequenceEdit {{
    background: {t['panel_bg_2']}; border: 1px solid {t['border']};
    border-radius: 9px; padding: 6px 10px;
}}
QKeySequenceEdit:focus {{ border-color: {t['gold']}; }}
"""

    def _retheme_icons(self) -> None:
        """Re-render every themed icon with the current accent color."""
        is_dark = bool(self._theme.get("is_dark", True))
        color = self._theme["gold"] if is_dark else self._theme["gold_deep"]
        for btn, icon_name in self._icon_targets:
            btn.setIcon(make_icon(icon_name, 16, color))
