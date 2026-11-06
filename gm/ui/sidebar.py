"""Golden Music v3.0 — the navigation sidebar.

Full-height navigation column: brand header, LIBRARY group (destinations),
TOOLS group (stats + library actions + overflow "More" menu), stretch, then
the pinned bottom group (theme / settings / mini player / command palette).

All visuals come from the application stylesheet (gm.theme_qss.py keys:
``Sidebar``, ``SidebarHeader``, ``SidebarBtn``, ``SidebarSep``) — this module
only sets objectNames, layout, icons and behaviour. Hover/checked styling is
therefore QSS-driven; the single per-widget exception is the icon pixmap
color swap for checked rows (QSS cannot recolor a QIcon).

Emits :attr:`nav_requested` with one of the exact keys::

    library, favorites, albums, artists, genres, playlists, radio, stats,
    addfolder, refresh, theme, settings, mini, command,
    more_duplicates, more_backup, more_export
"""
from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from config import Theme
from icons import Icon, get_dpr, render_icon

# ---------------------------------------------------------------------------
# geometry / row model
# ---------------------------------------------------------------------------
WIDTH_EXPANDED = 232
WIDTH_COLLAPSED = 68
ICON_PX = 18
INK_DARK = "#141414"  # text/icon color on the gold checked gradient (dark themes)

# (key, icon, label, checkable)
_NAV_ROWS = [
    ("library", Icon.LIBRARY, "Library", True),
    ("favorites", Icon.FAVORITES_NAV, "Favorites", True),
    ("albums", Icon.ALBUMS, "Albums", True),
    ("artists", Icon.USER, "Artists", True),
    ("genres", Icon.DISC, "Genres", True),
    ("playlists", Icon.PLAYLISTS, "Playlists", True),
    ("radio", Icon.RADIO, "Radio", True),
]
_TOOL_ROWS = [
    ("stats", Icon.STATS, "Stats", True),
    ("addfolder", Icon.FOLDER_ADD, "Add Folder", False),
    ("refresh", Icon.REFRESH, "Rescan", False),
]
_BOTTOM_ROWS = [
    ("theme", Icon.BRUSH, "Theme", False),
    ("settings", Icon.SETTINGS, "Settings", False),
    ("mini", Icon.MINIMIZE, "Mini Player", False),
    ("command", Icon.COMMAND, "Command Palette", False),
]
# rare/secondary destinations — overflow "More" menu
_MORE_ROWS = [
    ("more_duplicates", Icon.COPY, "Duplicates"),
    ("more_backup", Icon.SHIELD, "Backup / Restore"),
    ("more_export", Icon.DOWNLOAD, "Export Library (M3U)"),
]

ALL_KEYS = (
    [k for k, _, _, _ in _NAV_ROWS + _TOOL_ROWS]
    + [k for k, _, _, _ in _BOTTOM_ROWS]
    + [k for k, _, _ in _MORE_ROWS]
)


class Sidebar(QFrame):
    """v3 navigation sidebar (see module docstring)."""

    #: emitted with an exact destination/action key (see ALL_KEYS)
    nav_requested = pyqtSignal(str)

    def __init__(self, theme: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")

        self._theme: dict = dict(theme) if theme else {}
        self._collapsed = False
        self._counts = {"library": 0, "favorites": 0}
        self._buttons: dict[str, QPushButton] = {}
        self._icons: dict[str, str] = {}
        self._labels: dict[str, str] = {}
        self._badges: dict[str, QLabel] = {}
        self._hints: list[QLabel] = []
        self._group_headers: list[QLabel] = []
        self._theme_label: QLabel | None = None

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        self.setFixedWidth(WIDTH_EXPANDED)
        self._build_ui()
        self.set_theme(self._theme)

    # ------------------------------------------------------------------ ui --
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        header = QLabel("GOLDEN MUSIC", self)
        header.setObjectName("SidebarHeader")
        self._header = header
        layout.addWidget(header)

        layout.addWidget(self._make_group_header("LIBRARY"))
        for key, icon, label, checkable in _NAV_ROWS:
            layout.addWidget(self._make_row(key, icon, label, checkable,
                                            badge_key=key if key in
                                            ("library", "favorites") else None))

        layout.addWidget(self._make_separator())

        layout.addWidget(self._make_group_header("TOOLS"))
        for key, icon, label, checkable in _TOOL_ROWS:
            layout.addWidget(self._make_row(key, icon, label, checkable))
        # overflow submenu for rare destinations
        layout.addWidget(self._make_more_row())

        layout.addStretch(1)

        layout.addWidget(self._make_separator())
        for key, icon, label, checkable in _BOTTOM_ROWS:
            extras = "theme_name" if key == "theme" else (
                "ctrl_k" if key == "command" else None)
            layout.addWidget(self._make_row(key, icon, label, checkable,
                                            extra=extras))

    def _make_group_header(self, text: str) -> QLabel:
        lbl = QLabel(text, self)
        lbl.setObjectName("SidebarGroup")
        self._group_headers.append(lbl)
        return lbl

    def _make_separator(self) -> QFrame:
        sep = QFrame(self)
        sep.setObjectName("SidebarSep")
        sep.setFrameShape(QFrame.Shape.NoFrame)
        sep.setFixedHeight(1)
        return sep

    def _make_row(self, key: str, icon: str, label: str, checkable: bool,
                  badge_key: str | None = None,
                  extra: str | None = None) -> QWidget:
        """One sidebar row: [button][stretch][badge / hint label]."""
        row = QWidget(self)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(6)

        btn = QPushButton(label)
        btn.setObjectName("SidebarBtn")
        btn.setProperty("key", key)
        btn.setCheckable(checkable)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        btn.clicked.connect(lambda _checked=False, k=key: self.nav_requested.emit(k))
        if checkable:
            self._group.addButton(btn)
        self._buttons[key] = btn
        self._icons[key] = icon
        self._labels[key] = label
        btn.svg_string = icon  # v2 SideRail compatibility (tests read this)
        row_layout.addWidget(btn, 1)

        if badge_key:
            badge = QLabel("0", row)
            badge.setObjectName("SidebarBadge")
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.hide()
            self._badges[badge_key] = badge
            row_layout.addWidget(badge, 0, Qt.AlignmentFlag.AlignRight)

        if extra == "theme_name":
            hint = QLabel("", row)
            hint.setObjectName("SidebarHint")
            self._theme_label = hint
            self._hints.append(hint)
            row_layout.addWidget(hint, 0, Qt.AlignmentFlag.AlignRight)
        elif extra == "ctrl_k":
            hint = QLabel("Ctrl+K", row)
            hint.setObjectName("SidebarHint")
            self._hints.append(hint)
            row_layout.addWidget(hint, 0, Qt.AlignmentFlag.AlignRight)
        return row

    def _make_more_row(self) -> QWidget:
        """Overflow row: ELLIPSIS button opening the secondary QMenu."""
        row = QWidget(self)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(6)

        self._more_menu = QMenu(self)
        for key, icon, label in _MORE_ROWS:
            act = self._more_menu.addAction(label)
            act.setProperty("key", key)
            act.setIcon(QIcon(render_icon(icon, ICON_PX,
                                          self._theme.get("muted", "#979ca6"),
                                          get_dpr())))
            act.triggered.connect(
                lambda _checked=False, k=key: self.nav_requested.emit(k))

        btn = QPushButton("More")
        btn.setObjectName("SidebarBtn")
        btn.setProperty("key", "more")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        btn.clicked.connect(self._show_more_menu)
        self._buttons["more"] = btn
        self._icons["more"] = Icon.ELLIPSIS
        self._labels["more"] = "More"
        row_layout.addWidget(btn, 1)
        return row

    def _show_more_menu(self) -> None:
        """Popup the overflow menu under the More button (non-modal)."""
        btn = self._buttons.get("more")
        if btn is None:
            return
        pos = btn.mapToGlobal(btn.rect().bottomLeft())
        self._more_menu.popup(pos)

    # --------------------------------------------------------------- icons --
    def _apply_icon(self, btn: QPushButton, icon: str, checked: bool) -> None:
        color = self._checked_ink() if checked else self._theme.get("muted", "#979ca6")
        btn.setIcon(QIcon(render_icon(icon, ICON_PX, color, get_dpr())))
        btn.setIconSize(QSize(ICON_PX, ICON_PX))

    def _checked_ink(self) -> str:
        """Icon color matching the QSS checked text color on gold."""
        if self._theme and not self._theme.get("is_dark", True):
            return self._theme.get("window_bg", INK_DARK)
        return INK_DARK

    def _refresh_icons(self) -> None:
        for key, btn in self._buttons.items():
            self._apply_icon(btn, self._icons.get(key, Icon.MUSIC_NOTE),
                             btn.isChecked())

    # ---------------------------------------------------------------- theme --
    def set_theme(self, theme: dict) -> None:
        """Restyle for *theme* (icons, badge/menu colors, theme-name label)."""
        self._theme = dict(theme) if theme else {}
        t = self._theme

        if self._theme_label is not None:
            name = t.get("name", Theme.DEFAULT_NAME)
            self._theme_label.setText(Theme.display_name(name))

        badge_qss = (
            f"QLabel#SidebarBadge {{ background: {t.get('active', '#352918')};"
            f" color: {t.get('gold', '#e9b957')}; border-radius: 7px;"
            f" padding: 1px 7px; font-size: 10.5px; font-weight: 600; }}"
        )
        for badge in self._badges.values():
            badge.setStyleSheet(badge_qss)

        hint_qss = (
            f"QLabel#SidebarHint {{ color: {t.get('muted', '#979ca6')};"
            f" font-size: 10.5px; }}"
        )
        for hint in self._hints:
            hint.setStyleSheet(hint_qss)

        header_qss = (
            f"QLabel#SidebarGroup {{ color: {t.get('muted', '#979ca6')};"
            f" font-size: 10px; font-weight: 700; letter-spacing: 1.2px;"
            f" padding: 8px 10px 2px 10px; }}"
        )
        for gh in self._group_headers:
            gh.setStyleSheet(header_qss)

        self._more_menu.setStyleSheet(
            f"QMenu {{ background: {t.get('panel_bg', '#15110a')};"
            f" color: {t.get('text', '#f8f1de')};"
            f" border: 1px solid {t.get('border', '#372d1c')};"
            f" border-radius: 10px; padding: 6px; }}"
            f"QMenu::item {{ padding: 7px 24px 7px 14px; border-radius: 7px; }}"
            f"QMenu::item:selected {{ background: {t.get('active', '#352918')}; }}"
        )
        for key, icon, _label in _MORE_ROWS:
            for act in self._more_menu.actions():
                if act.property("key") == key:
                    act.setIcon(QIcon(render_icon(
                        icon, ICON_PX, t.get("muted", "#979ca6"), get_dpr())))

        self._refresh_icons()

    # ------------------------------------------------------------ navigation --
    # ------------------------------------------------------------ compat --
    # v2 SideRail attribute surface (mini_player / scripts / tests)
    @property
    def btn_library(self): return self._buttons.get("library")
    @property
    def btn_favorites(self): return self._buttons.get("favorites")
    @property
    def btn_albums(self): return self._buttons.get("albums")
    @property
    def btn_artists(self): return self._buttons.get("artists")
    @property
    def btn_genres(self): return self._buttons.get("genres")
    @property
    def btn_playlists(self): return self._buttons.get("playlists")
    @property
    def btn_radio(self): return self._buttons.get("radio")
    @property
    def btn_stats(self): return self._buttons.get("stats")
    @property
    def btn_add(self): return self._buttons.get("addfolder")
    @property
    def btn_refresh(self): return self._buttons.get("refresh")
    @property
    def btn_theme(self): return self._buttons.get("theme")
    @property
    def btn_settings(self): return self._buttons.get("settings")
    @property
    def btn_mini(self): return self._buttons.get("mini")

    def set_active(self, key: str) -> None:
        """Check the nav button for *key* exclusively (unknown -> none)."""
        # Qt refuses to uncheck the checked button of an exclusive group
        # programmatically, so exclusivity is lifted for the update.
        was_exclusive = self._group.exclusive()
        self._group.setExclusive(False)
        try:
            for k, btn in self._buttons.items():
                checked = (k == key) and btn.isCheckable()
                if btn.isChecked() != checked:
                    btn.blockSignals(True)
                    btn.setChecked(checked)
                    btn.blockSignals(False)
        finally:
            self._group.setExclusive(was_exclusive)
        self._refresh_icons()

    # ------------------------------------------------------------- collapsed --
    def set_collapsed(self, collapsed: bool) -> None:
        """Toggle the 68px icon-only rail mode (labels/hints/badges hidden)."""
        self._collapsed = bool(collapsed)
        self.setFixedWidth(WIDTH_COLLAPSED if self._collapsed else WIDTH_EXPANDED)

        # 68 - 2*12 margins = 44px of content; icon is 18px, so 13px padding
        # on each side centers the glyph exactly.
        center_qss = ("QPushButton#SidebarBtn { padding: 8px 13px;"
                      " text-align: center; }")
        for key, btn in self._buttons.items():
            btn.setText("" if self._collapsed else self._labels.get(key, ""))
            btn.setStyleSheet(center_qss if self._collapsed else "")
            btn.setToolTip(self._labels.get(key, "") if self._collapsed else "")

        self._header.setVisible(not self._collapsed)
        for gh in self._group_headers:
            gh.setVisible(not self._collapsed)
        for hint in self._hints:
            hint.setVisible(not self._collapsed)
        self._update_badges()

    def is_collapsed(self) -> bool:
        """True when the sidebar is in icon-only mode."""
        return self._collapsed

    # ----------------------------------------------------------------- badges --
    def set_library_counts(self, tracks: int, favorites: int) -> None:
        """Update the Library / Favorites count badges (hidden when 0)."""
        self._counts["library"] = max(0, int(tracks))
        self._counts["favorites"] = max(0, int(favorites))
        self._update_badges()

    def _update_badges(self) -> None:
        for key, badge in self._badges.items():
            count = self._counts.get(key, 0)
            badge.setText(str(count))
            badge.setVisible(count > 0 and not self._collapsed)
            badge.setToolTip(f"{count} items" if count else "")
