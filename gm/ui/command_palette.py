"""Golden Music v3.0 — Ctrl+K command palette.

A frameless popup (Qt.Popup) that centers on the top third of the parent
window, offering fuzzy command search plus an optional live search provider
(e.g. track lookup). Styling comes from gm.theme_qss.build_qss_v3 applied to
the palette subtree only, so it looks identical whether the host app has its
stylesheet loaded or not.

Interaction contract:
* Up/Down move the selection (group headers are skipped — they are
  non-selectable rows).
* Enter runs the selected action AFTER the palette is closed, via
  QTimer.singleShot(0) — never re-entrantly from inside a key handler.
* Esc closes. Clicking a row runs it.
* A query starting with ">" filters commands only (command mode); any other
  non-empty query also calls the (debounced, 120ms) search provider.

Rows are setItemWidget composites (icon + title + optional hint chip +
subtitle) with symmetric margins, so RTL layout direction mirrors cleanly.
"""
from __future__ import annotations

import difflib
import logging
from typing import Any, Callable

from PyQt6.QtCore import QEvent, QObject, QSize, Qt, QTimer
from PyQt6.QtGui import QHideEvent
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gm.theme_qss import build_qss_v3
from icons import Icon, get_dpr, render_icon

log = logging.getLogger(__name__)

#: provider type: () -> list[dict(icon, title, hint?, group, run)]
ActionProvider = Callable[[], list[dict[str, Any]]]
#: provider type: (query: str) -> list[dict(icon, title, subtitle?, run)] | None
SearchProvider = Callable[[str], "list[dict[str, Any]] | None"]

SEARCH_DEBOUNCE_MS = 120
FUZZY_THRESHOLD = 0.4
DEFAULT_ROWS = 12           # commands shown when the query is empty
FILTERED_ROWS = 20          # sanity cap while typing
ICON_PX = 18
GROUP_ORDER = ("Command", "Navigation", "Go to", "Search")


class _KeyFilter(QObject):
    """Routes palette keys (arrows/Enter/Esc) from the line edit."""

    def __init__(self, palette: "CommandPalette", parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._palette = palette

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._palette.activate_current()
                return True
            if key == Qt.Key.Key_Escape:
                self._palette.hide()
                return True
            if key == Qt.Key.Key_Up:
                self._palette.move_selection(-1)
                return True
            if key == Qt.Key.Key_Down:
                self._palette.move_selection(1)
                return True
        return super().eventFilter(watched, event)


class CommandPalette(QFrame):
    """Ctrl+K palette. QFrame (not bare QWidget) so the existing
    ``QFrame#CommandPalette`` QSS rule applies. All look lives in QSS.
    """

    def __init__(self, parent_window: QWidget,
                 actions_provider: ActionProvider,
                 search_provider: SearchProvider | None = None) -> None:
        super().__init__(parent_window)
        self.setObjectName("CommandPalette")

        self._parent_window = parent_window
        self._actions_provider = actions_provider
        self._search_provider = search_provider
        self._theme: dict = {}
        self._actions: list[dict[str, Any]] = []
        self._search_results: list[dict[str, Any]] = []
        self._filter = ""

        self.setWindowFlags(Qt.WindowType.Popup
                            | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedWidth(560)

        self._build_ui()

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(self._run_search)

        self._key_filter = _KeyFilter(self, self)
        self.input.installEventFilter(self._key_filter)
        self.list.installEventFilter(self._key_filter)
        self.list.itemClicked.connect(self._on_item_clicked)

        self.input.textChanged.connect(self._on_query_changed)

    # ------------------------------------------------------------------ ui --
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.input = QLineEdit(self)
        self.input.setObjectName("CommandInput")
        self.input.setPlaceholderText("Type a command or search\u2026")
        self.input.setClearButtonEnabled(True)
        root.addWidget(self.input)

        self.list = QListWidget(self)
        self.list.setObjectName("CommandList")
        self.list.setMinimumHeight(160)
        self.list.setMaximumHeight(320)
        self.list.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        root.addWidget(self.list, 1)

        self.footer = QLabel("\u2191\u2193 navigate \u00b7 Enter run \u00b7 Esc close", self)
        self.footer.setObjectName("CommandFooter")
        self.footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.footer)

    # --------------------------------------------------------------- theme --
    def set_theme(self, theme: dict) -> None:
        """Restyle the palette (and rebuild rows with new icon colors)."""
        self._theme = dict(theme) if theme else {}
        self.setStyleSheet(build_qss_v3(self._theme))
        muted = self._theme.get("muted", "#979ca6")
        self.footer.setStyleSheet(
            f"QLabel#CommandFooter {{ color: {muted}; font-size: 11px;"
            f" padding: 10px 12px; }}")
        self._rebuild()

    # -------------------------------------------------------------- showing --
    def popup(self) -> None:
        """Reset, position over the parent window's top third and show."""
        self._refresh_actions()
        self._filter = ""
        self._search_results = []
        self._search_timer.stop()
        self.input.blockSignals(True)
        self.input.clear()
        self.input.blockSignals(False)
        self._rebuild()
        self._resize_to_content()
        self._position_over_parent()
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._select_first()

    def hideEvent(self, event: QHideEvent) -> None:
        self._search_timer.stop()
        super().hideEvent(event)

    def _position_over_parent(self) -> None:
        """Center horizontally; sit in the upper third of the parent window."""
        win = self._parent_window.window() if self._parent_window else None
        if win is not None and win.isVisible():
            g = win.geometry()
            x = g.x() + (g.width() - self.width()) // 2
            y = g.y() + max(24, int(g.height() * 0.12))
        else:
            screen = QApplication.primaryScreen()
            g = screen.availableGeometry() if screen is not None else self.geometry()
            x = g.x() + (g.width() - self.width()) // 2
            y = g.y() + max(24, int(g.height() * 0.12))

        screen = QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            x = max(avail.left(), min(x, avail.right() - self.width()))
            y = max(avail.top(), min(y, avail.bottom() - self.height()))
        self.move(x, y)

    def _resize_to_content(self) -> None:
        rows = self.list.count()
        row_h = 44
        self.list.setFixedHeight(min(320, max(160, rows * row_h + 8)))
        self.adjustSize()

    # ------------------------------------------------------------ providers --
    def _refresh_actions(self) -> None:
        self._actions = []
        if not callable(self._actions_provider):
            return
        try:
            raw = self._actions_provider() or []
        except Exception:  # provider boundary — never break the palette
            log.exception("command palette: actions_provider failed")
            return
        for item in raw:
            if isinstance(item, dict) and item.get("title"):
                self._actions.append(item)

    def _run_search(self) -> None:
        if (self._search_provider is None or not self._filter
                or self._filter.startswith(">")):
            return
        try:
            results = self._search_provider(self._filter) or []
        except Exception:  # provider boundary
            log.exception("command palette: search_provider failed")
            return
        self._search_results = [
            r for r in results if isinstance(r, dict) and r.get("title")]
        self._rebuild()

    # ------------------------------------------------------------ filtering --
    def _on_query_changed(self, text: str) -> None:
        self._filter = text.strip()
        self._search_timer.stop()
        if not self._filter:
            self._search_results = []
            self._rebuild()
            return
        self._rebuild()  # immediate command filtering
        if self._search_provider is not None and not self._filter.startswith(">"):
            self._search_timer.start()

    def _filter_commands(self, query: str) -> list[dict[str, Any]]:
        """Fuzzy match: casefold substring OR difflib ratio above threshold."""
        q = query.casefold()
        scored: list[tuple[float, dict[str, Any]]] = []
        for act in self._actions:
            title = str(act.get("title", "")).casefold()
            if not q:
                score = 1.0
            elif q in title:
                score = 1.0 + difflib.SequenceMatcher(None, q, title).ratio()
            else:
                ratio = difflib.SequenceMatcher(None, q, title).ratio()
                if ratio <= FUZZY_THRESHOLD:
                    continue
                score = ratio
            scored.append((score, act))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [act for _score, act in scored]

    # --------------------------------------------------------------- rebuild --
    def _rebuild(self) -> None:
        self.list.clear()
        command_mode = self._filter.startswith(">")
        query = self._filter.lstrip(">").strip() if command_mode else self._filter

        if not self._filter:
            self._add_grouped_commands(self._actions[:DEFAULT_ROWS])
        else:
            self._add_grouped_commands(
                self._filter_commands(query)[:FILTERED_ROWS],
                with_headers=False)
        if self._search_results:
            self._add_header("SEARCH")
            for act in self._search_results:
                self._add_action_row(act)
        if self.list.count() == 0:
            self._add_empty_row()
        self._resize_to_content()
        self._select_first()

    def _add_grouped_commands(self, actions: list[dict[str, Any]],
                              with_headers: bool = True) -> None:
        if not actions:
            return
        grouped: dict[str, list[dict[str, Any]]] = {}
        for act in actions:
            grouped.setdefault(str(act.get("group", "Command")), []).append(act)
        last_group = None
        for group in GROUP_ORDER:
            for act in grouped.pop(group, []):
                if with_headers and group != last_group:
                    self._add_header(group.upper())
                    last_group = group
                self._add_action_row(act)
        # any non-standard groups trail at the end
        for group, acts in grouped.items():
            if with_headers and group != last_group:
                self._add_header(group.upper())
                last_group = group
            for act in acts:
                self._add_action_row(act)

    def _add_header(self, text: str) -> None:
        item = QListWidgetItem(self.list)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        muted = self._theme.get("muted", "#979ca6")
        label = QLabel(text, self.list)
        label.setStyleSheet(
            f"color: {muted}; font-size: 10px; font-weight: 700;"
            f" letter-spacing: 1.2px; padding: 10px 14px 4px 14px;"
            " background: transparent;")
        item.setSizeHint(label.sizeHint())
        self.list.addItem(item)
        self.list.setItemWidget(item, label)

    def _add_action_row(self, action: dict[str, Any]) -> None:
        item = QListWidgetItem(self.list)
        item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
        item.setData(Qt.ItemDataRole.UserRole, action)
        row = self._make_row_widget(action)
        item.setSizeHint(row.sizeHint())
        self.list.addItem(item)
        self.list.setItemWidget(item, row)

    def _add_empty_row(self) -> None:
        item = QListWidgetItem(self.list)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        muted = self._theme.get("muted", "#979ca6")
        label = QLabel("No matching commands", self.list)
        label.setStyleSheet(f"color: {muted}; padding: 14px;"
                            " background: transparent;")
        item.setSizeHint(label.sizeHint())
        self.list.addItem(item)
        self.list.setItemWidget(item, label)

    def _make_row_widget(self, action: dict[str, Any]) -> QWidget:
        t = self._theme
        muted = t.get("muted", "#979ca6")
        icon_name = action.get("icon") or Icon.COMMAND

        row = QWidget(self.list)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)

        icon_label = QLabel(row)
        icon_label.setPixmap(render_icon(icon_name, ICON_PX, muted, get_dpr()))
        icon_label.setFixedSize(QSize(ICON_PX, ICON_PX))
        layout.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignVCenter)

        text_col = QWidget(row)
        text_layout = QVBoxLayout(text_col)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(1)
        title = QLabel(str(action.get("title", "")), text_col)
        title.setStyleSheet("font-weight: 600; background: transparent;")
        text_layout.addWidget(title)
        subtitle = action.get("subtitle")
        if subtitle:
            sub = QLabel(str(subtitle), text_col)
            sub.setStyleSheet(
                f"color: {muted}; font-size: 11.5px; background: transparent;")
            text_layout.addWidget(sub)
        layout.addWidget(text_col, 1)

        hint = action.get("hint")
        if hint:
            chip = QLabel(str(hint), row)
            chip.setStyleSheet(
                f"color: {muted}; font-size: 11px;"
                f" background: {t.get('panel_bg_2', '#201910')};"
                f" border: 1px solid {t.get('border', '#372d1c')};"
                " border-radius: 6px; padding: 2px 8px;")
            layout.addWidget(chip, 0, Qt.AlignmentFlag.AlignVCenter)
        return row

    # ------------------------------------------------------------ selection --
    def _is_selectable(self, row: int) -> bool:
        item = self.list.item(row)
        return bool(item is not None and
                    item.flags() & Qt.ItemFlag.ItemIsSelectable)

    def _select_first(self) -> None:
        for i in range(self.list.count()):
            if self._is_selectable(i):
                self.list.setCurrentRow(i)
                return
        self.list.setCurrentRow(-1)

    def move_selection(self, step: int) -> None:
        """Move the selection by *step* rows, skipping group headers."""
        count = self.list.count()
        if count == 0:
            return
        index = self.list.currentRow()
        if index < 0:
            index = -1 if step > 0 else count
        for _ in range(count):
            index = (index + step) % count
            if self._is_selectable(index):
                self.list.setCurrentRow(index)
                return

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        if item is not None and (item.flags() & Qt.ItemFlag.ItemIsSelectable):
            self.activate_current()

    # -------------------------------------------------------------- execution --
    def activate_current(self) -> None:
        """Run the currently selected row (deferred until after closing)."""
        item = self.list.currentItem()
        if item is None:
            return
        action = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(action, dict):
            return
        run = action.get("run")
        self.hide()
        if callable(run):
            QTimer.singleShot(0, lambda: self._safe_run(run))

    @staticmethod
    def _safe_run(run: Callable[[], None]) -> None:
        try:
            run()
        except Exception:  # action boundary — a bad command must not crash UI
            log.exception("command palette: action raised")
