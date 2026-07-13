"""
Golden Music — Custom tray menu widget.
A two-row popup window with icon buttons (no text menu).
Row 1: [prev] [play/pause] [next]
Row 2: [show] [library icon] [quit]
"""
from PyQt6.QtCore import Qt, pyqtSignal, QPoint
from PyQt6.QtWidgets import (
    QWidget, QPushButton, QHBoxLayout, QVBoxLayout, QFrame, QLabel
)
from PyQt6.QtGui import (
    QPixmap, QPainter, QColor, QLinearGradient, QBrush, QIcon,
    QPaintEvent, QMouseEvent
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
        self.setFixedSize(180, 120)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

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
