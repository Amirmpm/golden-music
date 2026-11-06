"""
Golden Music — Playback queue panel.

A slide-in panel listing the upcoming tracks of the current playlist.
"Play Next" (track context menu) inserts a track right after the current
one; the queue shows the resulting order and lets the user remove items
or jump to any entry.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QPushButton, QFrame
)

from config import APP_NAME
from icons import Icon, render_icon, get_dpr


class QueuePanel(QFrame):
    """Right-side overlay panel showing the upcoming playback order."""

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.setObjectName("QueuePanel")
        self.setFixedWidth(300)
        self.hide()  # toggled by the queue button

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        head = QHBoxLayout()
        title = QLabel("Up Next")
        title.setObjectName("NowTitle")
        head.addWidget(title)
        head.addStretch(1)
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(28, 28)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.hide)
        head.addWidget(close_btn)
        layout.addLayout(head)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("TrackList")
        self.list_widget.itemDoubleClicked.connect(self._jump_to_item)
        # v3: drag-and-drop reordering + multi-select
        self.list_widget.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list_widget.setDragEnabled(True)
        self.list_widget.setAcceptDrops(True)
        self.list_widget.setDropIndicatorShown(True)
        self.list_widget.setDragDropMode(
            QAbstractItemView.DragDropMode.InternalMove)
        self.list_widget.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list_widget.model().rowsMoved.connect(self._on_rows_moved)
        layout.addWidget(self.list_widget, 1)

        hint = QLabel("Drag to reorder · double-click to jump · "
                      "right-click for options")
        hint.setStyleSheet(f"color: {self.main_window.theme['muted']}; font-size: 11px;")
        layout.addWidget(hint)
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._context_menu)
        self._apply_style()

    # ------------------------------------------------------------------
    def _apply_style(self):
        t = self.main_window.theme
        self.setStyleSheet(f"""
            QFrame#QueuePanel {{
                background: {t['panel_bg']};
                border-left: 1px solid {t['border']};
                border-radius: 0;
            }}
        """)

    def refresh(self):
        """Rebuild from main window's playlist state."""
        mw = self.main_window
        self.list_widget.clear()
        dpr = get_dpr()
        icon = None
        try:
            from PyQt6.QtGui import QIcon as _QIcon
            icon = _QIcon(render_icon(Icon.MUSIC_NOTE, 16, mw.theme["gold"], dpr))
        except Exception:
            pass
        pl = mw.current_playlist
        cur = mw.current_index
        if not pl:
            item = QListWidgetItem("Queue is empty — play something!")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.list_widget.addItem(item)
            return
        # Upcoming = everything after the current index
        order = list(range(cur + 1, len(pl)))
        for pos in order:
            path = pl[pos]
            title, artist = mw._tag_cache.get(
                path, (QueuePanel.Path_stem(path), ""))
            label = f"{pos - cur}.  {title}" + (f"  —  {artist}" if artist else "")
            item = QListWidgetItem(icon, label) if icon else QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, pos)
            item.setToolTip(path)
            self.list_widget.addItem(item)
        if not order:
            item = QListWidgetItem("End of queue")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.list_widget.addItem(item)

    @staticmethod
    def Path_stem(path: str) -> str:
        import os
        return os.path.splitext(os.path.basename(path))[0]

    def toggle(self):
        if self.isVisible():
            self.hide()
        else:
            self.refresh()
            self.show()

    def _jump_to_item(self, item):
        pos = item.data(Qt.ItemDataRole.UserRole)
        if pos is None:
            return
        mw = self.main_window
        mw.current_index = pos
        mw._load_and_play_current()
        self.refresh()

    def _selected_paths(self):
        out = []
        for item in self.list_widget.selectedItems():
            data = item.data(Qt.ItemDataRole.UserRole)
            if data is not None:
                out.append((data, item))
        return out

    def _on_rows_moved(self, parent, start, end, dest, dest_row):
        """User dragged rows: the visible list is the UPCOMING slice, so
        apply its new order onto mw.current_playlist[cur+1:]. Item paths
        are recovered from the row tooltips (UserRole holds stale indices
        after the move)."""
        try:
            mw = self.main_window
            pl = list(mw.current_playlist or [])
            cur = int(mw.current_index or -1)
            if cur + 1 >= len(pl):
                return
            upcoming = []
            for i in range(self.list_widget.count()):
                it = self.list_widget.item(i)
                if it is None:
                    continue
                tip = it.toolTip()
                if tip and (it.flags() & Qt.ItemFlag.ItemIsSelectable):
                    upcoming.append(tip)
            if len(upcoming) != len(pl) - (cur + 1):
                return          # structural mismatch — bail out safely
            mw.current_playlist = pl[:cur + 1] + upcoming
            self.refresh()
        except Exception:
            pass

    def _context_menu(self, pos):
        from PyQt6.QtWidgets import QMenu
        item = self.list_widget.itemAt(pos)
        if item is None or item.data(Qt.ItemDataRole.UserRole) is None:
            return
        sel = self._selected_paths()
        multi = [d for d, _it in sel if d not in (None, '__current__')]
        menu = QMenu(self)
        act_jump = menu.addAction("Play now")
        act_remove = menu.addAction("Remove from queue")
        act_play_next = None
        act_to_playlist = None
        if multi:
            menu.addSeparator()
            act_play_next = menu.addAction(
                f"Play next ({len(multi)})" if len(multi) > 1 else "Play next")
            act_to_playlist = menu.addAction(
                f"Send {len(multi)} to playlist..." if len(multi) > 1
                else "Send to playlist...")
        chosen = menu.exec(self.list_widget.mapToGlobal(pos))
        if chosen == act_jump:
            self._jump_to_item(item)
        elif chosen == act_remove:
            pos_idx = item.data(Qt.ItemDataRole.UserRole)
            if 0 <= pos_idx < len(self.main_window.current_playlist):
                self.main_window.current_playlist.pop(pos_idx)
                if pos_idx < self.main_window.current_index:
                    self.main_window.current_index -= 1
            self.refresh()
        elif chosen == act_play_next and multi:
            self.main_window._insert_next(multi)
            self.refresh()
        elif chosen == act_to_playlist and multi:
            try:
                from playlist_dialog import add_track_to_playlist_dialog
                for pth in multi:
                    add_track_to_playlist_dialog(self.main_window, pth)
            except Exception:
                pass


def insert_play_next(main_window, path: str):
    """Context-menu helper: schedule `path` immediately after the current track."""
    mw = main_window
    if not mw.current_playlist:
        # Nothing playing — just start it as a single-track session
        mw._play_from_list([path], 0, view=mw.current_view)
        return True
    if path in mw.current_playlist:
        cur = mw.current_playlist.index(mw.current_track) \
            if mw.current_track in mw.current_playlist else -1
        existing = mw.current_playlist.index(path)
        if existing == cur + 1:
            return False  # already next
        mw.current_playlist.pop(existing)
        if existing < cur:
            cur -= 1
        mw.current_playlist.insert(cur + 1, path)
    else:
        cur = mw.current_playlist.index(mw.current_track) \
            if mw.current_track in mw.current_playlist else len(mw.current_playlist) - 1
        mw.current_playlist.insert(cur + 1, path)
    return True
