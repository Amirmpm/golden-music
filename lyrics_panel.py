"""
Golden Music — Floating lyrics panel.

Shows the current track's lyrics (synced highlight when timestamps are
available). Loads local .lrc first, then embedded tags, then lrclib.net.
Runs the online lookup in a QThread so the UI never blocks.
"""
import os

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QFrame
)

from config import APP_NAME
from icons import Icon, render_icon, get_dpr


class LyricsLoaderThread(QThread):
    """Fetch lyrics off the UI thread."""
    ready = pyqtSignal(str, object)   # (path, Lyrics|None)

    def __init__(self, path, title, artist, allow_online, parent=None):
        super().__init__(parent)
        self.path = path
        self.title = title
        self.artist = artist
        self.allow_online = allow_online
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            from lyrics import get_lyrics
            ly = get_lyrics(self.path, self.title, self.artist,
                            allow_online=self.allow_online)
            if not self._cancel:
                self.ready.emit(self.path, ly)
        except Exception:
            if not self._cancel:
                self.ready.emit(self.path, None)


class LyricsPanel(QFrame):
    """Floating glass panel with synced lyric lines."""

    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        self.setObjectName("LyricsPanel")
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(420, 380)
        self._path = None
        self._lyrics = None
        self._active_line = -1
        self._loader = None
        self._line_labels = []
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)

        holder = QFrame()
        holder.setObjectName("GlassCard")
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(8)

        head = QHBoxLayout()
        self.title_label = QLabel("Lyrics")
        self.title_label.setObjectName("NowTitle")
        head.addWidget(self.title_label)
        head.addStretch(1)
        self.source_label = QLabel("")
        head.addWidget(self.source_label)
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(26, 26)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.hide)
        head.addWidget(close_btn)
        lay.addLayout(head)

        # Scrollable lyric lines
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent;")
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        self.lines_lay = QVBoxLayout(content)
        self.lines_lay.setContentsMargins(4, 4, 4, 4)
        self.lines_lay.setSpacing(10)
        self.lines_lay.addStretch(1)
        scroll.setWidget(content)
        lay.addWidget(scroll, 1)
        outer.addWidget(holder)

    def _apply_style(self):
        t = self.main_window.theme
        self.title_label.setStyleSheet(
            f"color: {t['gold_light']}; font-size: 16px; font-weight: 700;")
        self.source_label.setStyleSheet(
            f"color: {t['muted']}; font-size: 11px;")
        self.setStyleSheet(f"""
            QFrame#GlassCard {{
                background-color: rgba({self._rgb(t['panel_bg'])}, 232);
                border: 1px solid {t['border']};
                border-radius: 18px;
            }}
            QLabel {{ color: {t['text']}; background: transparent; }}
            QPushButton {{
                background: {t['panel_bg_2']}; color: {t['muted']};
                border: none; border-radius: 13px; font-size: 12px;
            }}
            QPushButton:hover {{ color: {t['text']}; }}
        """)
        self._restyle_lines()

    @staticmethod
    def _rgb(hex_color: str) -> str:
        from PyQt6.QtGui import QColor
        c = QColor(hex_color)
        return f"{c.red()},{c.green()},{c.blue()}"

    def _clear_lines(self):
        while self.lines_lay.count() > 1:   # keep trailing stretch
            item = self.lines_lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._line_labels = []
        self._active_line = -1

    def _show_message(self, text):
        self._clear_lines()
        lbl = QLabel(text)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setWordWrap(True)
        self.lines_lay.insertWidget(0, lbl)
        self._line_labels = []

    def load_for_track(self, path, allow_online=True):
        self._apply_style()
        if not path:
            self.title_label.setText("Lyrics")
            self._show_message("No track is playing.")
            return
        if path == self._path and self._lyrics is not None:
            return  # already loaded for this track
        self._path = path
        self._lyrics = None
        title, artist = self.main_window._tag_cache.get(path, ("", ""))
        if not title:
            title = os.path.splitext(os.path.basename(path))[0]
        self.title_label.setText(title)
        self.source_label.setText("loading…")
        self._show_message("Looking up lyrics…")
        # Cancel any in-flight loader
        if self._loader is not None and self._loader.isRunning():
            self._loader.cancel()
            self._loader.wait(200)
        self._loader = LyricsLoaderThread(path, title, artist, allow_online)
        self._loader.ready.connect(self._on_lyrics_ready)
        self._loader.start()

    def _on_lyrics_ready(self, path, ly):
        if path != self._path or ly is None:
            if path == self._path:
                self.source_label.setText("")
                self._show_message("No lyrics found for this track.")
            return
        self._lyrics = ly
        self.source_label.setText(f"{ly.source} · {'synced' if ly.synced else 'plain'}")
        self._clear_lines()
        t = self.main_window.theme
        for _ts, text in ly.lines:
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(
                f"color: {t['muted']}; font-size: 14px; background: transparent;")
            self.lines_lay.insertWidget(self.lines_lay.count() - 1, lbl)
            self._line_labels.append(lbl)

    def sync_position(self, pos_ms: int):
        """Highlight the active line when synced lyrics are shown, scrolling
        it into view (centered) so long lyrics follow the song."""
        ly = self._lyrics
        if not ly or not ly.synced or not self._line_labels:
            return
        idx = ly.line_for_time(pos_ms)
        if idx == self._active_line or idx < 0:
            return
        t = self.main_window.theme
        if 0 <= self._active_line < len(self._line_labels):
            self._line_labels[self._active_line].setStyleSheet(
                f"color: {t['muted']}; font-size: 14px; background: transparent;")
        if idx < len(self._line_labels):
            self._line_labels[idx].setStyleSheet(
                f"color: {t['gold_light']}; font-size: 15px; font-weight: 700;"
                f"background: transparent;")
            # Auto-scroll the new active line to the vertical center.
            try:
                scroll = self.findChild(QScrollArea)
                if scroll is not None:
                    scroll.ensureWidgetVisible(
                        self._line_labels[idx], 0,
                        max(60, scroll.viewport().height() // 2 - 30))
            except Exception:
                pass
        self._active_line = idx

    def _restyle_lines(self):
        if not self._line_labels:
            return
        t = self.main_window.theme
        for i, lbl in enumerate(self._line_labels):
            active = i == self._active_line
            lbl.setStyleSheet(
                f"color: {t['gold_light'] if active else t['muted']}; "
                f"font-size: {15 if active else 14}px; "
                f"font-weight: {700 if active else 400}; background: transparent;")
