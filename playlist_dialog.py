"""
Golden Music — Personal playlists.

PlaylistStore: named playlists persisted in config (dict[str, list[str]]).
PlaylistDialog: manage (create / rename / delete / view) playlists.
Playlists live in MainWindow.playlists and are saved with _save_config().
"""
import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget,
    QListWidgetItem, QInputDialog, QMessageBox, QWidget, QGridLayout
)

from config import APP_NAME
from icons import Icon, render_icon, get_dpr


class PlaylistStore:
    """Thin wrapper over the {name: [paths]} mapping with validation."""

    MAX_PLAYLISTS = 100
    MAX_TRACKS_PER = 10_000
    MAX_NAME_LEN = 80

    @staticmethod
    def sanitize_name(name: str) -> str:
        return name.strip()[:PlaylistStore.MAX_NAME_LEN]

    @staticmethod
    def valid_new_name(name: str, playlists: dict) -> str:
        """Return cleaned name or '' if invalid/duplicate.

        Duplicate check is case-insensitive so 'My Mix' and 'my mix' can't
        coexist and confuse the config file."""
        name = PlaylistStore.sanitize_name(name)
        if not name:
            return ""
        lowered = {k.casefold() for k in playlists}
        if name.casefold() in lowered:
            return ""
        return name


class PlaylistDialog(QDialog):
    """Browse and manage personal playlists."""

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.setWindowTitle(f"{APP_NAME} — Playlists")
        self.setModal(True)
        self.setMinimumSize(520, 420)
        self.theme = main_window.theme
        self._build_ui()
        self._apply_style()
        self._reload()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)

        head = QLabel("Your playlists")
        head.setObjectName("NowTitle")
        layout.addWidget(head)

        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self._play_playlist)
        layout.addWidget(self.list_widget, 1)

        hint = QLabel("Double-click a playlist to play it. Right-click for more.")
        hint.setStyleSheet(f"color: {self.theme['muted']}; font-size: 11px;")
        layout.addWidget(hint)
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._context_menu)

        btn_row = QHBoxLayout()
        new_btn = QPushButton("New Playlist...")
        new_btn.setObjectName("GoldBtn")
        new_btn.clicked.connect(self._new_playlist)
        btn_row.addWidget(new_btn)
        play_btn = QPushButton("Play Selected")
        play_btn.clicked.connect(lambda: self._play_playlist(self.list_widget.currentItem()))
        btn_row.addWidget(play_btn)
        btn_row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _apply_style(self):
        t = self.theme
        self.setStyleSheet(f"""
            QDialog {{ background: {t['window_bg']}; color: {t['text']}; }}
            QLabel#NowTitle {{ color: {t['gold_light']}; font-size: 17px; font-weight: 700; }}
            QListWidget {{
                background: {t['panel_bg']}; border: 1px solid {t['border']};
                border-radius: 8px; color: {t['text']}; font-size: 13px; padding: 4px;
            }}
            QListWidget::item {{ padding: 7px 10px; border-radius: 5px; }}
            QListWidget::item:selected {{ background: {t['active']}; color: {t['gold_light']}; }}
            QPushButton {{
                background: {t['panel_bg_2']}; color: {t['text']};
                border: 1px solid {t['border']}; border-radius: 6px;
                padding: 7px 14px; font-size: 12px;
            }}
            QPushButton:hover {{ background: {t['border']}; }}
            QPushButton#GoldBtn {{ background: {t['gold']}; color: {t['window_bg']}; border: none; font-weight: 600; }}
            QPushButton#GoldBtn:hover {{ background: {t['gold_light']}; }}
            QMenu {{ background: {t['panel_bg']}; color: {t['text']};
                     border: 1px solid {t['border']}; }}
            QMenu::item:selected {{ background: {t['active']}; }}
        """)

    def _reload(self):
        self.list_widget.clear()
        dpr = get_dpr()
        icon = QIcon(render_icon(Icon.LIBRARY, 16, self.theme["gold"], dpr))
        names = self.main_window.sort_playlists_names()
        for name in names:
            n = len(self.main_window.playlists.get(name, []))
            item = QListWidgetItem(icon, f"{name}   ({n} tracks)")
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.list_widget.addItem(item)

    # -- actions -------------------------------------------------------
    def _new_playlist(self):
        name, ok = QInputDialog.getText(self, APP_NAME, "Playlist name:")
        if not ok:
            return
        clean = PlaylistStore.valid_new_name(name, self.main_window.playlists)
        if not clean:
            if PlaylistStore.sanitize_name(name):
                QMessageBox.information(self, APP_NAME,
                    "A playlist with this name already exists.")
            return
        self.main_window.playlists[clean] = []
        self.main_window._save_config()
        self._reload()

    def _play_playlist(self, item):
        if item is None:
            return
        name = item.data(Qt.ItemDataRole.UserRole)
        tracks = [p for p in self.main_window.playlists.get(name, [])
                  if os.path.exists(p)]
        if not tracks:
            QMessageBox.information(self, APP_NAME, "This playlist is empty "
                "(or its files are missing). Add tracks from a right-click menu.")
            return
        self.accept()
        self.main_window._play_from_list(tracks, 0, view=self.main_window.current_view)

    def _delete_playlist(self, item):
        name = item.data(Qt.ItemDataRole.UserRole)
        reply = QMessageBox.question(self, APP_NAME,
            f"Delete playlist '{name}'? The audio files themselves are not touched.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.main_window.playlists.pop(name, None)
        self.main_window._save_config()
        self._reload()

    def _rename_playlist(self, item):
        old = item.data(Qt.ItemDataRole.UserRole)
        name, ok = QInputDialog.getText(self, APP_NAME, "New name:", text=old)
        if not ok:
            return
        clean = PlaylistStore.valid_new_name(name, self.main_window.playlists)
        if not clean:
            if PlaylistStore.sanitize_name(name):
                QMessageBox.information(self, APP_NAME, "Name is empty or already taken.")
            return
        self.main_window.playlists[clean] = self.main_window.playlists.pop(old)
        self.main_window._save_config()
        self._reload()

    def _context_menu(self, pos):
        item = self.list_widget.itemAt(pos)
        if item is None:
            return
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        act_play = menu.addAction("Play")
        act_rename = menu.addAction("Rename...")
        act_delete = menu.addAction("Delete")
        chosen = menu.exec(self.list_widget.mapToGlobal(pos))
        if chosen == act_play:
            self._play_playlist(item)
        elif chosen == act_rename:
            self._rename_playlist(item)
        elif chosen == act_delete:
            self._delete_playlist(item)


def add_track_to_playlist_dialog(main_window, path: str):
    """Right-click helper: pick a playlist and append the track to it."""
    mw = main_window
    names = mw.sort_playlists_names()
    if not names:
        name, ok = QInputDialog.getText(mw, APP_NAME,
            "No playlists yet. Name for a new playlist:")
        if not ok:
            return
        clean = PlaylistStore.valid_new_name(name, mw.playlists)
        if not clean:
            return
        mw.playlists[clean] = [path]
        mw._save_config()
        QMessageBox.information(mw, APP_NAME, f"Added to '{clean}'.")
        return
    # Let the user pick one of the existing playlists (or type a new name)
    choice, ok = QInputDialog.getItem(
        mw, APP_NAME, "Add track to playlist:", names, 0, True)  # editable=True
    if not ok:
        return
    if choice in mw.playlists:
        target = choice
    else:
        target = PlaylistStore.valid_new_name(choice, mw.playlists)
        if not target:
            QMessageBox.information(mw, APP_NAME, "Invalid or duplicate name.")
            return
        mw.playlists[target] = []
    pl = mw.playlists[target]
    if path in pl:
        QMessageBox.information(mw, APP_NAME, "Track is already in this playlist.")
        return
    if len(pl) >= PlaylistStore.MAX_TRACKS_PER:
        QMessageBox.information(mw, APP_NAME, "This playlist is full.")
        return
    pl.append(path)
    mw._save_config()
    QMessageBox.information(mw, APP_NAME, f"Added to '{target}'.")
