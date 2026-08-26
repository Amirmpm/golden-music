"""
Golden Music — Track properties dialog.

Shows technical file info (format, duration, bitrate, size, path) with a
copyable path row and an "Open File Location" button.
"""
import os
import subprocess
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QGridLayout, QLabel, QPushButton, QHBoxLayout,
    QApplication
)

from config import APP_NAME


class TrackInfoDialog(QDialog):
    """Read-only properties sheet for one audio file."""

    def __init__(self, info: dict, theme: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} — Track Properties")
        self.setModal(True)
        self.setMinimumWidth(440)
        self.theme = theme
        self.info = info
        self._build_ui()
        self._apply_style()

    def _build_ui(self):
        t = self.theme
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        title = self.info.get("title") or os.path.splitext(
            self.info["filename"])[0]
        head = QLabel(title)
        head.setObjectName("NowTitle")
        head.setWordWrap(True)
        layout.addWidget(head)

        artist = self.info.get("artist") or "Unknown Artist"
        sub = QLabel(artist)
        sub.setStyleSheet(f"color: {t['muted']}; font-size: 12px;")
        sub.setWordWrap(True)
        layout.addWidget(sub)

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        rows = [
            ("Format", self.info.get("format")),
            ("Duration", self.info.get("duration")),
            ("Bitrate", self.info.get("bitrate")),
            ("Sample rate", self.info.get("sample_rate")),
            ("Channels", self.info.get("channels")),
            ("Size", self.info.get("size")),
            ("Album", self.info.get("album")),
            ("Year", self.info.get("year")),
        ]
        r = 0
        for label, value in rows:
            if not value:
                continue
            lab = QLabel(label)
            lab.setStyleSheet(f"color: {t['muted']}; font-size: 11px;")
            val = QLabel(str(value))
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            val.setStyleSheet(f"color: {t['text']}; font-size: 12px; font-weight: 600;")
            grid.addWidget(lab, r, 0)
            grid.addWidget(val, r, 1)
            r += 1

        # Path row — selectable + copy button
        path_lab = QLabel("Location")
        path_lab.setStyleSheet(f"color: {t['muted']}; font-size: 11px;")
        path_val = QLabel(self.info["path"])
        path_val.setWordWrap(True)
        path_val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        path_val.setStyleSheet(f"color: {t['text']}; font-size: 11px; font-family: Consolas, monospace;")
        grid.addWidget(path_lab, r, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(path_val, r, 1)
        r += 1
        layout.addLayout(grid)

        btn_row = QHBoxLayout()
        copy_btn = QPushButton("Copy Path")
        copy_btn.clicked.connect(self._copy_path)
        btn_row.addWidget(copy_btn)

        open_btn = QPushButton("Open File Location")
        open_btn.setObjectName("GoldBtn")
        open_btn.clicked.connect(self._open_location)
        btn_row.addWidget(open_btn)

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
            QPushButton {{
                background: {t['panel_bg_2']}; color: {t['text']};
                border: 1px solid {t['border']}; border-radius: 6px;
                padding: 7px 14px; font-size: 12px;
            }}
            QPushButton:hover {{ background: {t['border']}; }}
            QPushButton#GoldBtn {{
                background: {t['gold']}; color: {t['window_bg']};
                border: none; font-weight: 600;
            }}
            QPushButton#GoldBtn:hover {{ background: {t['gold_light']}; }}
        """)

    def _copy_path(self):
        cb = QApplication.clipboard()
        cb.setText(self.info["path"])

    def _open_location(self):
        """Reveal the file in Windows Explorer (or the platform equivalent)."""
        path = self.info["path"]
        try:
            if os.name == "nt":
                # /select highlights the file itself in its folder
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", path])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(path)])
        except Exception as e:
            log_err = f"Could not open location: {e}"
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, APP_NAME, log_err)


def _folder_icon(theme):  # text-only button; slot kept for future art
    return None
