"""
Golden Music — Theme picker popup.
Shows a grid of theme swatches when hovering over the theme button.
"""
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtWidgets import (
    QWidget, QPushButton, QVBoxLayout, QHBoxLayout, QFrame, QLabel
)
from PyQt6.QtGui import (
    QPixmap, QPainter, QColor, QLinearGradient, QBrush, QIcon,
    QPaintEvent, QMouseEvent, QEnterEvent
)
from PyQt6.QtSvg import QSvgRenderer

from config import Theme
from icons import render_icon, get_dpr


class ThemeSwatch(QPushButton):
    """A clickable theme preview swatch."""

    def __init__(self, theme: dict, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.setFixedSize(80, 50)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(Theme.display_name(theme["name"]))
        self._hover = False
        self.setStyleSheet("border: none; padding: 0; margin: 0; border-radius: 8px;")

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, e: QPaintEvent):
        try:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = self.rect().adjusted(2, 2, -2, -2)
            # Gradient background using theme colors
            grad = QLinearGradient(0, 0, rect.width(), rect.height())
            grad.setColorAt(0.0, QColor(self.theme["window_bg"]))
            grad.setColorAt(0.5, QColor(self.theme["panel_bg"]))
            grad.setColorAt(1.0, QColor(self.theme["gold"]))
            p.setBrush(QBrush(grad))
            if self._hover:
                p.setPen(QPen(QColor(self.theme["gold_light"]), 2))
            else:
                p.setPen(QPen(QColor(self.theme["border"]), 1))
            p.drawRoundedRect(rect, 8, 8)
            # Theme name
            p.setPen(QColor(255, 255, 255, 220))
            font = p.font()
            font.setPointSize(8)
            font.setBold(True)
            p.setFont(font)
            name = Theme.display_name(self.theme["name"])
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, name)
            p.end()
        except Exception as ex:
            print(f"ThemeSwatch paint error: {ex}")


class ThemePickerPopup(QFrame):
    """Popup window showing all themes as swatches."""

    theme_selected = pyqtSignal(str)

    def __init__(self, current_theme_name: str, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Popup |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.current_theme = current_theme_name
        self.theme = Theme.get(current_theme_name)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # Title
        title = QLabel("Choose Theme")
        title.setStyleSheet(f"color: {self.theme['gold_light']}; font-size: 12px; font-weight: 600; background: transparent; border: none;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # Grid of swatches (3 columns)
        themes = Theme.ALL
        cols = 3
        rows = (len(themes) + cols - 1) // cols
        for r in range(rows):
            row_layout = QHBoxLayout()
            row_layout.setSpacing(6)
            for c in range(cols):
                idx = r * cols + c
                if idx < len(themes):
                    swatch = ThemeSwatch(themes[idx])
                    swatch.clicked.connect(lambda checked=False, name=themes[idx]["name"]: self._on_select(name))
                    row_layout.addWidget(swatch)
                else:
                    row_layout.addStretch()
            row_w = QWidget()
            row_w.setLayout(row_layout)
            row_w.setStyleSheet("background: transparent;")
            layout.addWidget(row_w)

    def _on_select(self, name: str):
        self.theme_selected.emit(name)
        self.close()

    def paintEvent(self, e: QPaintEvent):
        try:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = self.rect().adjusted(2, 2, -2, -2)
            bg = QColor(self.theme["panel_bg"])
            bg.setAlpha(250)
            p.setBrush(QBrush(bg))
            p.setPen(QPen(QColor(self.theme["border"]), 1))
            p.drawRoundedRect(rect, 12, 12)
            p.end()
        except Exception as ex:
            print(f"ThemePickerPopup paint error: {ex}")

    def _apply_theme(self, theme: dict):
        self.theme = theme


from PyQt6.QtGui import QPen
