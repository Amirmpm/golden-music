"""
Golden Music — Mini Player.
A small floating window with cover + title + controls.
"""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QFrame, QSizeGrip
)
from PyQt6.QtGui import QPixmap, QPainter, QColor, QLinearGradient, QBrush, QIcon

from config import APP_NAME
from icons import Icon, render_icon, get_dpr


class MiniPlayer(QWidget):
    """Compact floating player window."""

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.theme = main_window.theme
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setFixedSize(320, 100)
        self._dragging = False
        self._drag_offset = None

        self._build_ui()
        self._apply_theme()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        # Main row: cover | title/artist | controls
        row = QHBoxLayout()
        row.setSpacing(10)

        # Cover
        self.cover_label = QLabel()
        self.cover_label.setFixedSize(64, 64)
        self.cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_label.setStyleSheet(f"border-radius: 8px; border: 1px solid {self.theme['border']};")
        row.addWidget(self.cover_label)

        # Title/Artist
        info_col = QVBoxLayout()
        info_col.setContentsMargins(0, 0, 0, 0)
        info_col.setSpacing(2)
        self.title_label = QLabel("No track")
        self.title_label.setStyleSheet(f"color: {self.theme['gold_light']}; font-size: 13px; font-weight: 600;")
        self.artist_label = QLabel("—")
        self.artist_label.setStyleSheet(f"color: {self.theme['muted']}; font-size: 11px;")
        info_col.addWidget(self.title_label)
        info_col.addWidget(self.artist_label)
        info_w = QWidget()
        info_w.setLayout(info_col)
        row.addWidget(info_w, 1)

        # Controls
        from main import IconButton
        self.prev_btn = IconButton(Icon.PREV, icon_size=16, button_size=28)
        self.play_btn = QPushButton()
        self.play_btn.setFixedSize(32, 32)
        self.play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_btn._svg = Icon.PLAY
        self.next_btn = IconButton(Icon.NEXT, icon_size=16, button_size=28)

        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(4)
        ctrl_row.addWidget(self.prev_btn)
        ctrl_row.addWidget(self.play_btn)
        ctrl_row.addWidget(self.next_btn)
        ctrl_w = QWidget()
        ctrl_w.setLayout(ctrl_row)
        row.addWidget(ctrl_w)

        layout.addLayout(row)

        # Bottom: expand button
        bottom_row = QHBoxLayout()
        bottom_row.addStretch(1)
        self.expand_btn = QPushButton("Expand ▼")
        self.expand_btn.setFixedHeight(18)
        self.expand_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.expand_btn.setStyleSheet(f"color: {self.theme['muted']}; background: transparent; border: none; font-size: 10px;")
        self.expand_btn.clicked.connect(self._expand)
        bottom_row.addWidget(self.expand_btn)
        layout.addLayout(bottom_row)

        # Wire up
        self.prev_btn.clicked.connect(self.main_window._on_prev)
        self.play_btn.clicked.connect(self.main_window._on_play_pause)
        self.next_btn.clicked.connect(self.main_window._on_next)

    def _apply_theme(self):
        self.theme = self.main_window.theme
        self.setStyleSheet(f"""
            MiniPlayer {{
                background: {self.theme['panel_bg']};
                border: 1px solid {self.theme['border']};
                border-radius: 12px;
            }}
            QPushButton {{
                background: {self.theme['gold']}; border: none; border-radius: 16px;
            }}
            QPushButton:hover {{ background: {self.theme['gold_light']}; }}
        """)
        self._update_play_icon()
        for btn in [self.prev_btn, self.next_btn]:
            btn.set_theme(self.theme)

    def _update_play_icon(self):
        dpr = get_dpr()
        color = self.theme["window_bg"]
        pm = render_icon(self.play_btn._svg, 16, color, dpr)
        self.play_btn.setIcon(QIcon(pm))
        self.play_btn.setIconSize(self.play_btn.size() - QSize(8, 8) if False else self.play_btn.size())

    def update_track(self, title, artist, cover_pm):
        try:
            self.title_label.setText(title)
            self.artist_label.setText(artist)
            if cover_pm and not cover_pm.isNull():
                scaled = cover_pm.scaled(64, 64,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation)
                side = min(scaled.width(), scaled.height())
                x = (scaled.width() - side) // 2
                y = (scaled.height() - side) // 2
                self.cover_label.setPixmap(scaled.copy(x, y, side, side))
            else:
                # Placeholder
                dpr = get_dpr()
                pm = QPixmap(64, 64)
                pm.fill(Qt.GlobalColor.transparent)
                p = QPainter(pm)
                grad = QLinearGradient(0, 0, 64, 64)
                grad.setColorAt(0, QColor(self.theme["gold_deep"]))
                grad.setColorAt(1, QColor(self.theme["gold_light"]))
                p.setBrush(QBrush(grad))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(0, 0, 64, 64, 8, 8)
                note = render_icon(Icon.MUSIC_NOTE, 32, "rgba(26,20,16,140)", dpr)
                p.drawPixmap(16, 16, note)
                p.end()
                self.cover_label.setPixmap(pm)
        except Exception as ex:
            print(f"MiniPlayer update_track error: {ex}")

    def update_play_state(self, playing):
        self.play_btn._svg = Icon.PAUSE if playing else Icon.PLAY
        self._update_play_icon()

    def _expand(self):
        self.hide()
        self.main_window._toggle_visible()

    # Dragging
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_offset = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._dragging and self._drag_offset is not None:
            self.move(e.globalPosition().toPoint() - self._drag_offset)
            e.accept()

    def mouseReleaseEvent(self, e):
        self._dragging = False
        self._drag_offset = None
