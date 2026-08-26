"""
Golden Music — Duplicates browser dialog.

Lists duplicate groups found in the library; the user keeps one track per
group and removes the rest from the library (files on disk are untouched).
"""
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTreeWidget,
    QTreeWidgetItem, QMessageBox, QProgressBar, QCheckBox
)

from config import APP_NAME
from icons import Icon, render_icon, get_dpr
import duplicates as dups


class DuplicatesDialog(QDialog):
    """Scan + review + cleanup UI for duplicate tracks."""

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.setWindowTitle(f"{APP_NAME} — Duplicate Finder")
        self.setModal(True)
        self.setMinimumSize(640, 480)
        self.theme = main_window.theme
        self.groups = []
        self._build_ui()
        self._apply_style()
        # Kick off the scan after the dialog is visible
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(50, self._run_scan)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)

        head = QLabel("Duplicate tracks")
        head.setObjectName("NowTitle")
        layout.addWidget(head)

        self.status_label = QLabel("Scanning library...")
        self.status_label.setStyleSheet(f"color: {self.theme['muted']}; font-size: 12px;")
        layout.addWidget(self.status_label)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setFixedHeight(6)
        layout.addWidget(self.progress)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Keep", "Track", "Location"])
        self.tree.setColumnWidth(0, 60)
        self.tree.itemChanged.connect(self._on_check_changed)
        layout.addWidget(self.tree, 1)

        # Keep the first copy checked by default per group; user can switch.
        self._default_keep_first = True

        btn_row = QHBoxLayout()
        remove_btn = QPushButton("Remove Checked From Library")
        remove_btn.setObjectName("GoldBtn")
        remove_btn.clicked.connect(self._remove_checked)
        btn_row.addWidget(remove_btn)
        btn_row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _apply_style(self):
        t = self.theme
        self.setStyleSheet(f"""
            QDialog {{ background: {t['window_bg']}; color: {t['text']}; }}
            QLabel#NowTitle {{ color: {t['gold_light']}; font-size: 17px; font-weight: 700; }}
            QTreeWidget {{
                background: {t['panel_bg']}; border: 1px solid {t['border']};
                border-radius: 8px; color: {t['text']}; font-size: 12px; padding: 4px;
            }}
            QTreeWidget::item {{ padding: 5px; border-radius: 4px; }}
            QTreeWidget::item:selected {{ background: {t['active']}; }}
            QHeaderView::section {{
                background: {t['panel_bg_2']}; color: {t['muted']};
                border: none; padding: 5px;
            }}
            QPushButton {{
                background: {t['panel_bg_2']}; color: {t['text']};
                border: 1px solid {t['border']}; border-radius: 6px;
                padding: 7px 14px; font-size: 12px;
            }}
            QPushButton:hover {{ background: {t['border']}; }}
            QPushButton#GoldBtn {{ background: {t['gold']}; color: {t['window_bg']}; border: none; font-weight: 600; }}
        """)

    # ------------------------------------------------------------------
    def _run_scan(self):
        lib = list(self.main_window.library)
        if not lib:
            self.status_label.setText("Library is empty.")
            return
        self.progress.setVisible(True)
        self.progress.setRange(0, len(lib))

        def on_progress(done, total):
            self.progress.setValue(done)

        self.groups = dups.find_duplicates(lib, dict(self.main_window._tag_cache),
                                           progress=on_progress)
        self.progress.setVisible(False)
        self._fill_tree()

    def _fill_tree(self):
        self.tree.blockSignals(True)
        self.tree.clear()
        mw = self.main_window
        dpr = get_dpr()
        note_icon = None
        if not self.groups:
            self.status_label.setText("No duplicates found. 🎉")
            self.tree.blockSignals(False)
            return
        n_tracks = sum(len(g) for g in self.groups)
        extra = n_tracks - len(self.groups)
        self.status_label.setText(
            f"{len(self.groups)} duplicate group(s) — {extra} redundant track(s). "
            f"Pick which copy to keep in each group.")
        for gi, group in enumerate(self.groups):
            label = dups.group_label(group, dict(mw._tag_cache))
            top = QTreeWidgetItem([f"", f"Group {gi+1}: {label}", f"{len(group)} copies"])
            top.setFlags(top.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            f = top.font(1); f.setBold(True); top.setFont(1, f)
            top.setForeground(1, _qcolor(self.theme["gold_light"]))
            self.tree.addTopLevelItem(top)
            for ti, path in enumerate(group):
                title, artist = mw._tag_cache.get(path, ("", ""))
                dur = dups.fmt_duration(path)
                name = title or Path(path).stem
                display = name + (f" — {artist}" if artist else "") + \
                          (f"   [{dur}]" if dur else "")
                child = QTreeWidgetItem(["", display, path])
                child.setData(0, Qt.ItemDataRole.UserRole, path)
                child.setToolTip(2, path)
                child.setCheckState(0, Qt.CheckState.Checked if ti == 0
                                    else Qt.CheckState.Unchecked)
                top.addChild(child)
        self.tree.expandAll()
        self.tree.blockSignals(False)

    def _on_check_changed(self, item, column):
        """Enforce exactly-one keep per group: checking one unchecks siblings."""
        if column != 0:
            return
        parent = item.parent()
        if parent is None:
            return
        if item.checkState(0) == Qt.CheckState.Checked:
            self.tree.blockSignals(True)
            for i in range(parent.childCount()):
                sib = parent.child(i)
                if sib is not item:
                    sib.setCheckState(0, Qt.CheckState.Unchecked)
            self.tree.blockSignals(False)

    def _remove_checked(self):
        to_remove = []
        for gi in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(gi)
            kept_any = False
            group_removals = []
            for ci in range(top.childCount()):
                child = top.child(ci)
                path = child.data(0, Qt.ItemDataRole.UserRole)
                if child.checkState(0) == Qt.CheckState.Checked:
                    group_removals.append(path)
                else:
                    kept_any = True
            if not kept_any and group_removals:
                # User checked every copy of a group — refuse to empty it
                group_removals.pop()  # keep one
            to_remove.extend(group_removals)
        if not to_remove:
            QMessageBox.information(self, APP_NAME, "Nothing checked to remove.")
            return
        reply = QMessageBox.question(self, APP_NAME,
            f"Remove {len(to_remove)} track(s) from the library?\n"
            f"(Files on disk are NOT deleted.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        for p in to_remove:
            self.main_window._remove_from_library(p)
        self._run_scan()


def _qcolor(hex_color):
    from PyQt6.QtGui import QColor
    return QColor(hex_color)
