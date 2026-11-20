"""
Golden Music — Mini Player.
A small floating window with cover + title + controls.
"""
from PyQt6.QtCore import Qt, pyqtSignal, QSize
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
        self.setFixedSize(400, 112)
        self._dragging = False
        self._drag_offset = None

        self._build_ui()
        self._apply_theme()

    def _build_ui(self):
        # Nuvx mini player: ONE row — cover · title/artist · transport ·
        # expand affordance (no second row of chrome).
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(0)

        row = QHBoxLayout()
        row.setSpacing(12)

        # Cover
        self.cover_label = QLabel()
        self.cover_label.setFixedSize(84, 84)
        self.cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_label.setStyleSheet(
            f"border-radius: 8px; border: 1px solid "
            f"{self.theme.get('glass_border', self.theme['border'])};")
        row.addWidget(self.cover_label)

        # Title/Artist
        info_col = QVBoxLayout()
        info_col.setContentsMargins(0, 0, 0, 0)
        info_col.setSpacing(3)
        self.title_label = QLabel("No track")
        self.title_label.setWordWrap(True)
        self.artist_label = QLabel("—")
        info_col.addWidget(self.title_label)
        info_col.addWidget(self.artist_label)
        info_col.addStretch(1)
        info_w = QWidget()
        info_w.setLayout(info_col)
        row.addWidget(info_w, 1)

        # Controls
        from main import IconButton
        self.prev_btn = IconButton(Icon.PREV, icon_size=16, button_size=30)
        self.play_btn = QPushButton()
        self.play_btn.setFixedSize(34, 34)
        self.play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_btn._svg = Icon.PLAY
        self.next_btn = IconButton(Icon.NEXT, icon_size=16, button_size=30)

        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(4)
        ctrl_row.addWidget(self.prev_btn)
        ctrl_row.addWidget(self.play_btn)
        ctrl_row.addWidget(self.next_btn)
        ctrl_w = QWidget()
        ctrl_w.setLayout(ctrl_row)
        row.addWidget(ctrl_w)

        # Expand affordance (single row, right edge)
        self.expand_btn = QPushButton("Expand ▶")
        self.expand_btn.setFixedHeight(24)
        self.expand_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.expand_btn.clicked.connect(self._expand)
        self.expand_btn.setToolTip("Open the full window")
        row.addWidget(self.expand_btn)

        layout.addLayout(row)

        # Wire up
        self.prev_btn.clicked.connect(self.main_window._on_prev)
        self.play_btn.clicked.connect(self.main_window._on_play_pause)
        self.next_btn.clicked.connect(self.main_window._on_next)

    def _apply_theme(self):
        self.theme = self.main_window.theme
        text = self.theme.get("text", "#f2f0f5")
        muted = self.theme.get("muted", "#b2acba")
        glass = self.theme.get("glass_panel", self.theme["panel_bg"])
        border = self.theme.get("glass_border", self.theme["border"])
        active = self.theme.get("active", self.theme.get("panel_bg_2"))
        # White title (never gold), glass play button (never gold fill).
        self.title_label.setStyleSheet(
            f"color: {text}; font-size: 14px; font-weight: 600; "
            f"background: transparent;")
        self.artist_label.setStyleSheet(
            f"color: {muted}; font-size: 12px; background: transparent;")
        self.expand_btn.setStyleSheet(
            f"color: {muted}; background: transparent; border: none; "
            f"font-size: 11px;")
        self.setStyleSheet(f"""
            MiniPlayer {{
                background: {glass};
                border: 1px solid {border};
                border-radius: 14px;
            }}
            QPushButton#MiniPlay {{
                background: {active}; border: 1px solid {border};
                border-radius: 17px;
            }}
            QPushButton#MiniPlay:hover {{ background: {self.theme.get('panel_bg_2')}; }}
        """)
        self.play_btn.setObjectName("MiniPlay")
        self.play_btn.style().unpolish(self.play_btn)
        self.play_btn.style().polish(self.play_btn)
        self._update_play_icon()
        for btn in [self.prev_btn, self.next_btn]:
            btn.set_theme(self.theme)

    def _update_play_icon(self):
        dpr = get_dpr()
        color = self.theme.get("text", "#f2f0f5")
        pm = render_icon(self.play_btn._svg, 16, color, dpr)
        self.play_btn.setIcon(QIcon(pm))
        self.play_btn.setIconSize(QSize(16, 16))

    def update_track(self, title, artist, cover_pm):
        try:
            self.title_label.setText(title)
            self.artist_label.setText(artist)
            if cover_pm and not cover_pm.isNull():
                scaled = cover_pm.scaled(84, 84,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation)
                side = min(scaled.width(), scaled.height())
                x = (scaled.width() - side) // 2
                y = (scaled.height() - side) // 2
                self.cover_label.setPixmap(scaled.copy(x, y, side, side))
            else:
                # Placeholder
                dpr = get_dpr()
                pm = QPixmap(84, 84)
                pm.fill(Qt.GlobalColor.transparent)
                p = QPainter(pm)
                grad = QLinearGradient(0, 0, 84, 84)
                grad.setColorAt(0, QColor(self.theme["gold_deep"]))
                grad.setColorAt(1, QColor(self.theme["gold_light"]))
                p.setBrush(QBrush(grad))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(0, 0, 84, 84, 8, 8)
                note = render_icon(Icon.MUSIC_NOTE, 42, "rgba(26,20,16,140)", dpr)
                p.drawPixmap(21, 21, note)
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
