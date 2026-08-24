"""
Golden Music — Custom tray menu widget.
A polished popup window: now-playing header (cover + title + artist),
transport controls, and a bottom action row.
Row 0: [cover] Track title          (now playing)
Row 1: [prev]    [play/pause]    [next]
Row 2: [show]  [library icon]  [quit]
"""
from PyQt6.QtCore import Qt, pyqtSignal, QPoint
from PyQt6.QtWidgets import (
    QWidget, QPushButton, QHBoxLayout, QVBoxLayout, QFrame, QLabel
)
from PyQt6.QtGui import (
    QPixmap, QPainter, QColor, QLinearGradient, QBrush, QIcon,
    QPaintEvent, QMouseEvent, QFont, QPen
)
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtCore import QByteArray

from icons import Icon, render_icon, get_dpr


class TrayIconButton(QPushButton):
    """A flat icon button for the tray menu."""

    def __init__(self, svg_string: str, size: int = 24, parent=None):
        super().__init__(parent)
        self.svg_string = svg_string
        self.icon_size = size
        self.setFixedSize(48, 48)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover = False
        self._color = "#d4a85a"
        self.setStyleSheet("""
            QPushButton {
                background: transparent; border: none; border-radius: 10px;
                padding: 0; margin: 0;
            }
            QPushButton:hover { background: rgba(255,255,255,0.08); }
        """)

    def set_color(self, color: str):
        self._color = color
        self.update_icon()

    def set_svg(self, svg_string: str):
        self.svg_string = svg_string
        self.update_icon()

    def enterEvent(self, e):
        self._hover = True
        self.update_icon()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update_icon()
        super().leaveEvent(e)

    def update_icon(self):
        color = "#ffffff" if self._hover else self._color
        dpr = get_dpr()
        pm = render_icon(self.svg_string, self.icon_size, color, dpr)
        self.setIcon(QIcon(pm))
        self.setIconSize(self.icon_size * 0.8 and self.rect().size() * 0)


class TrayMenuWidget(QFrame):
    """Custom popup tray menu with two rows of icon buttons."""

    prev_clicked = pyqtSignal()
    play_clicked = pyqtSignal()
    next_clicked = pyqtSignal()
    show_clicked = pyqtSignal()
    library_clicked = pyqtSignal()
    quit_clicked = pyqtSignal()

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.setWindowFlags(
            Qt.WindowType.Popup |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(260, 168)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 10)
        layout.setSpacing(8)

        # ---- Now-playing header: [cover] title / artist ----
        header = QHBoxLayout()
        header.setSpacing(10)

        self.cover_label = QLabel()
        self.cover_label.setFixedSize(44, 44)
        self.cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_label.setStyleSheet(
            f"border-radius: 8px; border: 1px solid {theme['border']};"
            f"background: {theme['panel_bg_2']};")
        header.addWidget(self.cover_label)

        info_col = QVBoxLayout()
        info_col.setSpacing(1)
        self.track_title = QLabel("No track playing")
        self.track_title.setStyleSheet(
            f"color: {theme['gold_light']}; font-size: 13px; font-weight: 600;")
        self.track_artist = QLabel("—")
        self.track_artist.setStyleSheet(
            f"color: {theme['muted']}; font-size: 11px;")
        info_col.addWidget(self.track_title)
        info_col.addWidget(self.track_artist)
        header.addLayout(info_col, 1)
        layout.addLayout(header)

        # Divider
        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background: {theme['border']};")
        layout.addWidget(divider)

        # Row 1: prev / play / next
        row1 = QHBoxLayout()
        row1.setSpacing(4)
        self.btn_prev = TrayIconButton(Icon.PREV, 24)
        self.btn_play = TrayIconButton(Icon.PLAY, 28)
        self.btn_next = TrayIconButton(Icon.NEXT, 24)
        row1.addWidget(self.btn_prev)
        row1.addWidget(self.btn_play)
        row1.addWidget(self.btn_next)
        row1_w = QWidget()
        row1_w.setLayout(row1)
        layout.addWidget(row1_w)

        # Divider
        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background: {theme['border']};")
        layout.addWidget(divider)

        # Row 2: show / library / quit
        row2 = QHBoxLayout()
        row2.setSpacing(4)
        self.btn_show = TrayIconButton(Icon.CHEVRON_RIGHT, 22)
        self.btn_show.setToolTip("Show / Hide")
        self.btn_library = TrayIconButton(Icon.MUSIC_NOTE, 24)
        self.btn_library.setToolTip("Open Library")
        self.btn_quit = TrayIconButton(Icon.CLOSE, 22)
        self.btn_quit.setToolTip("Quit")
        row2.addWidget(self.btn_show)
        row2.addWidget(self.btn_library)
        row2.addWidget(self.btn_quit)
        row2_w = QWidget()
        row2_w.setLayout(row2)
        layout.addWidget(row2_w)

        # Wire up
        self.btn_prev.clicked.connect(self.prev_clicked.emit)
        self.btn_play.clicked.connect(self.play_clicked.emit)
        self.btn_next.clicked.connect(self.next_clicked.emit)
        self.btn_show.clicked.connect(self.show_clicked.emit)
        self.btn_library.clicked.connect(self.library_clicked.emit)
        self.btn_quit.clicked.connect(self.quit_clicked.emit)

        self._apply_theme(theme)

    def _apply_theme(self, theme: dict):
        self.theme = theme
        accent = theme["gold"]
        for btn in [self.btn_prev, self.btn_play, self.btn_next,
                    self.btn_show, self.btn_library, self.btn_quit]:
            btn.set_color(accent)
        # Restyle the header with the active theme colors
        self.track_title.setStyleSheet(
            f"color: {theme['gold_light']}; font-size: 13px; font-weight: 600;")
        self.track_artist.setStyleSheet(
            f"color: {theme['muted']}; font-size: 11px;")
        self.cover_label.setStyleSheet(
            f"border-radius: 8px; border: 1px solid {theme['border']};"
            f"background: {theme['panel_bg_2']};")

    def update_now_playing(self, title: str = "", artist: str = "",
                           cover_pm: QPixmap = None):
        """Refresh the header text and cover thumbnail."""
        try:
            t = (title or "").strip()
            a = (artist or "").strip()
            # Elide long strings so the popup keeps its size
            fm = self.track_title.fontMetrics()
            self.track_title.setText(fm.elidedText(t, Qt.TextElideMode.ElideRight, 170))
            fm2 = self.track_artist.fontMetrics()
            self.track_artist.setText(fm2.elidedText(a or "—", Qt.TextElideMode.ElideRight, 170))
            if cover_pm is not None and not cover_pm.isNull():
                scaled = cover_pm.scaled(
                    44, 44, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation)
                side = min(scaled.width(), scaled.height())
                x = (scaled.width() - side) // 2
                y = (scaled.height() - side) // 2
                self.cover_label.setPixmap(scaled.copy(x, y, side, side))
            else:
                self._draw_placeholder_cover()
        except Exception as ex:
            print(f"TrayMenu update_now_playing error: {ex}")

    def _draw_placeholder_cover(self):
        """Golden gradient square with a music note when there is no art."""
        try:
            dpr = get_dpr()
            pm = QPixmap(int(44 * dpr), int(44 * dpr))
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            grad = QLinearGradient(0, 0, int(44 * dpr), int(44 * dpr))
            grad.setColorAt(0.0, QColor(self.theme["gold_deep"]))
            grad.setColorAt(1.0, QColor(self.theme["gold_light"]))
            p.setBrush(QBrush(grad))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(0, 0, int(44 * dpr), int(44 * dpr),
                              int(8 * dpr), int(8 * dpr))
            note = render_icon(Icon.MUSIC_NOTE, int(22 * dpr),
                               "rgba(26,20,16,150)", dpr)
            p.drawPixmap(int(11 * dpr), int(11 * dpr), note)
            p.end()
            pm.setDevicePixelRatio(dpr)
            self.cover_label.setPixmap(pm)
        except Exception:
            pass

    def paintEvent(self, e: QPaintEvent):
        """Draw a rounded, semi-transparent background."""
        try:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = self.rect().adjusted(2, 2, -2, -2)
            # Background
            bg = QColor(self.theme["panel_bg"])
            bg.setAlpha(245)
            p.setBrush(QBrush(bg))
            p.setPen(QPen(QColor(self.theme["border"]), 1))
            p.drawRoundedRect(rect, 12, 12)
            p.end()
        except Exception as ex:
            print(f"TrayMenuWidget paint error: {ex}")

    def update_play_state(self, playing: bool):
        self.btn_play.set_svg(Icon.PAUSE if playing else Icon.PLAY)
