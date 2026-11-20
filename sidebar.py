"""Golden Music v3 — Nuvx Desktop-style navigation sidebar.

Recreates the Nuvx Desktop sidebar proportions and behaviour:

  * 236 px expanded width, collapsing to a 76 px icon rail
  * 0.28 s smoothstep animated collapse (0 when reduced-motion)
  * 42 px nav rows: 20 px icon, 15 px label, semibold when selected
  * logo at the top (opens Home), Settings pinned at the bottom
  * playlists section with row height 48 px (36 px art slot in Nuvx; here
    a compact glyph keeps the same rhythm without album fetching)

Nav keys emitted on ``nav_clicked(str)`` match the MainWindow dispatcher:
home / search / library / favorites / albums / stats / downloads /
playlists / settings / mini / theme / collapse.
"""
from __future__ import annotations

from PyQt6.QtCore import (Qt, QSize, QTimer, QVariantAnimation,
                           QEasingCurve, pyqtSignal)
from PyQt6.QtWidgets import (QFrame, QVBoxLayout, QHBoxLayout,
                              QPushButton, QToolButton, QSizePolicy,
                              QScrollArea, QWidget)
from PyQt6.QtGui import QPixmap, QIcon

import liquidglass as lg
from icons import Icon, render_icon


EXPANDED_W = 236
RAIL_W = 76
ROW_H = 42


class NavRow(QPushButton):
    """One 42 px navigation row (icon + label)."""

    def __init__(self, key: str, icon_name: str, label: str, parent=None):
        super().__init__(parent)
        self.key = key
        self.icon_name = icon_name
        self.label_text = label
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(ROW_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)
        self.setText(label)
        self._theme = {}
        self._collapsed = False
        self._hovered = False

    def set_theme(self, theme: dict, collapsed: bool):
        self._theme = theme
        self._collapsed = collapsed
        self._refresh()

    def _refresh(self):
        """Nuvx nav row: neutral glass pill + white icon/label when active.

        No gold side bar, no gold icon — accent is reserved for real state
        (see NUVX_ANALYSIS §3.1 / §4)."""
        t = self._theme or {}
        lgx = t.get("lg", {}) or {}
        # Nuvx selection pill ≈ 11 px; ladder: card 8 (+3 for the pill).
        radius = max(6, int(lgx.get("radius_card", 8)) + 3)
        text = t.get("text", "#f2f0f5")
        muted = t.get("muted", "#b2acba")
        fill = t.get("glass_fill", "rgba(255,255,255,0.10)")
        hover_bg = lg.glass_alpha(fill, 0.10)
        checked_bg = lg.glass_alpha(fill, 0.16)
        on = self.isChecked() or self._hovered
        # TEXT_DIM → TEXT: brighten on hover/selected, never accent-tinted.
        ic = render_icon(self.icon_name, 20, text if on else muted)
        self.setIcon(QIcon(ic))
        self.setIconSize(QSize(20, 20))
        self.setStyleSheet(f"""
        QPushButton {{
            background: transparent;
            border: none;
            border-radius: {radius}px;
            text-align: {('center') if self._collapsed else 'left'};
            padding-left: {0 if self._collapsed else 24}px;
            padding-right: 8px;
            color: {text if self.isChecked() else muted};
            font-size: 15px;
            font-weight: {600 if self.isChecked() else 400};
        }}
        QPushButton:hover {{ background: {hover_bg}; }}
        QPushButton:checked {{
            background: {checked_bg};
            color: {text};
            border: none;
        }}
        """)
        if self._collapsed:
            self.setText("")
            self.setToolTip(self.label_text)
        else:
            self.setText(self.label_text)
            self.setToolTip("")

    def enterEvent(self, e):
        self._hovered = True
        self._refresh()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hovered = False
        self._refresh()
        super().leaveEvent(e)


class Sidebar(QFrame):
    """Left navigation shell (Nuvx layout)."""

    nav_clicked = pyqtSignal(str)
    OBJECT_NAME = "Sidebar"

    def __init__(self, theme: dict, logo_path: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName(self.OBJECT_NAME)
        self.theme = dict(theme or {})
        self._logo_path = logo_path
        self._collapsed = False
        self._anim = None

        self.setFixedWidth(EXPANDED_W)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 12, 0, 12)
        lay.setSpacing(6)

        # -- logo row --------------------------------------------------
        logo_row = QHBoxLayout()
        logo_row.setContentsMargins(22, 4, 12, 8)
        self.logo_btn = QToolButton()
        self.logo_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.logo_btn.setFixedSize(44, 30)
        self.logo_btn.setToolTip("Golden Music — Home")
        if logo_path:
            pm = QPixmap(logo_path)
            if not pm.isNull():
                self.logo_btn.setIcon(QIcon(pm.scaledToHeight(
                    24, Qt.TransformationMode.SmoothTransformation)))
                self.logo_btn.setIconSize(QSize(36, 24))
        self.logo_btn.clicked.connect(lambda: self.nav_clicked.emit("home"))
        self.logo_btn.setStyleSheet("background: transparent; border: none;")
        logo_row.addWidget(self.logo_btn)
        logo_row.addStretch(1)

        # collapse chevron (appears at right edge, always visible)
        self.collapse_btn = QToolButton()
        self.collapse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.collapse_btn.setFixedSize(24, 24)
        self.collapse_btn.setToolTip("Collapse sidebar")
        self.collapse_btn.clicked.connect(self.toggle_collapse)
        logo_row.addWidget(self.collapse_btn, alignment=Qt.AlignmentFlag.AlignTop)
        logo_row_w = QWidget()
        logo_row_w.setLayout(logo_row)
        lay.addWidget(logo_row_w)

        # -- nav rows --------------------------------------------------
        self.rows: dict[str, NavRow] = {}
        nav_items = [
            ("home", Icon.HOME, "Home"),
            ("search", Icon.SEARCH, "Search"),
            ("library", Icon.LIBRARY, "Library"),
            ("favorites", Icon.FAVORITES_NAV, "Favorites"),
            ("albums", Icon.ALBUMS, "Albums"),
            ("downloads", Icon.DOWNLOAD, "Downloads"),
            ("stats", Icon.STATS, "Listening Stats"),
        ]
        for key, icon, label in nav_items:
            row = NavRow(key, icon, label)
            row.clicked.connect(lambda _c, k=key: self.nav_clicked.emit(k))
            self.rows[key] = row
            lay.addWidget(row)

        # -- actions ----------------------------------------------------
        self.action_row_playlists = NavRow("playlists", Icon.PLAYLISTS, "Playlists")
        self.action_row_playlists.clicked.connect(
            lambda _c: self.nav_clicked.emit("playlists"))
        self.action_row_mini = NavRow("mini", Icon.MINIMIZE, "Mini Player")
        self.action_row_mini.clicked.connect(
            lambda _c: self.nav_clicked.emit("mini"))
        for r in (self.action_row_playlists, self.action_row_mini):
            self.rows[r.key] = r
            lay.addWidget(r)

        # -- library tools (Add folder / Refresh) ---------------------------
        self.row_addfolder = NavRow("addfolder", Icon.FOLDER_ADD, "Add Folder")
        self.row_addfolder.setCheckable(False)
        self.row_addfolder.clicked.connect(
            lambda _c: self.nav_clicked.emit("addfolder"))
        self.row_refresh = NavRow("refresh", Icon.REFRESH, "Refresh Library")
        self.row_refresh.setCheckable(False)
        self.row_refresh.clicked.connect(
            lambda _c: self.nav_clicked.emit("refresh"))
        for r in (self.row_addfolder, self.row_refresh):
            self.rows[r.key] = r
            lay.addWidget(r)

        lay.addStretch(1)

        # -- settings pinned bottom -------------------------------------
        self.row_settings = NavRow("settings", Icon.SETTINGS, "Settings")
        self.row_settings.clicked.connect(
            lambda _c: self.nav_clicked.emit("settings"))
        self.rows["settings"] = self.row_settings
        lay.addWidget(self.row_settings)

        self.set_theme(self.theme)

    # ------------------------------------------------------------------
    def set_theme(self, theme: dict):
        self.theme = dict(theme or {})
        t = self.theme
        collapsed = self._collapsed
        lgx = t.get("lg", {})
        radius = lgx.get("radius_panel", 14)
        panel = t.get("glass_panel", t.get("glass_fill",
                                           "rgba(255,255,255,0.28)"))
        muted = t.get("muted", "#b2acba")
        # Nuvx sidebar: a borderless glass panel (separation comes from the
        # player-bar hairline at the top and the content margin) — only the
        # window-chrome left radii are kept.
        self.setStyleSheet(f"""
        QFrame#Sidebar {{
            background: {panel};
            border: none;
            border-top-left-radius: {radius}px;
            border-bottom-left-radius: {radius}px;
        }}
        """)
        for row in self.rows.values():
            row.set_theme(t, collapsed)
        chev = render_icon(Icon.CHEVRON_LEFT if not collapsed else
                           Icon.CHEVRON_RIGHT, 16, muted)
        self.collapse_btn.setIcon(QIcon(chev))

    # ------------------------------------------------------------------
    def set_active(self, key: str):
        for k, row in self.rows.items():
            on = (k == key)
            if row.isChecked() != on:
                row.setChecked(on)
                row._refresh()

    def set_playlists(self, names: list):
        """Playlist quick list — kept for the expanded state badge count."""
        n = len(names or [])
        row = self.rows.get("playlists")
        if row is not None:
            row.setToolTip(f"{n} playlist(s)" if n else "Playlists")

    # ------------------------------------------------------------------
    def toggle_collapse(self):
        self._animate_to(not self._collapsed)

    def _animate_to(self, collapsed: bool):
        s = lg.GlassSettings.from_dict(self.theme.get("lg_settings") or {}) \
            if self.theme.get("lg_settings") else lg.GlassSettings()
        duration = s.anim_ms(280)
        start_w = self.width()
        end_w = RAIL_W if collapsed else EXPANDED_W
        for row in self.rows.values():
            row._collapsed = collapsed
        self._collapsed = collapsed
        if duration <= 0:
            self.setFixedWidth(end_w)
            self.set_theme(self.theme)
            return
        anim = QVariantAnimation(self)
        anim.setDuration(duration)
        anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        anim.setStartValue(start_w)
        anim.setEndValue(end_w)
        anim.valueChanged.connect(self._on_width)
        anim.finished.connect(lambda: self.set_theme(self.theme))
        self._anim = anim
        anim.start()

    def _on_width(self, v):
        self.setFixedWidth(int(v))
        mid = (RAIL_W + EXPANDED_W) / 2
        # swap icon/label mode at the midpoint for a clean transition
        collapsed_now = int(v) < mid
        if collapsed_now != self._collapsed_labels_now():
            for row in self.rows.values():
                row._collapsed = collapsed_now
                row._refresh()

    def _collapsed_labels_now(self) -> bool:
        return self.width() < (RAIL_W + EXPANDED_W) / 2

    def sizeHint(self):
        return QSize(EXPANDED_W, 700)
