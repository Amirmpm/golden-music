"""
Golden Music — Theme picker popup.
Shows a compact grid of theme swatches, clamped inside the screen so it
can never spill off-monitor.

UX contract (v2.0.0):
* 12 themes laid out 3 per row under DARK / LIGHT section headers.
* Selecting a theme does NOT close the popup — the user may audition
  several themes in one session; the popup even re-skins itself live.
* The popup closes only when the user clicks outside it (standard Qt
  Popup grab) or presses the round ✕ button (Lucide `close` glyph,
  matching every other icon in the app).
"""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QPushButton, QVBoxLayout, QHBoxLayout, QGridLayout,
    QFrame, QLabel
)
from PyQt6.QtGui import (
    QPainter, QColor, QLinearGradient, QBrush, QIcon,
    QPaintEvent, QPen
)

from config import Theme
from icons import Icon, render_icon


class ThemeSwatch(QPushButton):
    """A clickable theme preview swatch."""

    def __init__(self, theme: dict, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.setFixedSize(104, 44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(Theme.display_name(theme["name"]))
        self._hover = False
        self._selected = False
        self.setStyleSheet("border: none; padding: 0; margin: 0; border-radius: 8px;")

    def set_selected(self, selected: bool):
        """Mark this swatch as the currently active theme."""
        if self._selected != selected:
            self._selected = selected
            name = Theme.display_name(self.theme["name"])
            suffix = " — current ✓" if selected else ""
            self.setToolTip(name + suffix)
            self.update()

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
            grad.setColorAt(1.0, QColor(self.theme["panel_bg_2"]))
            p.setBrush(QBrush(grad))
            if self._selected:
                # Solid accent ring for the active theme
                p.setPen(QPen(QColor(self.theme["gold"]), 2))
            elif self._hover:
                p.setPen(QPen(QColor(self.theme["gold_light"]), 2))
            else:
                p.setPen(QPen(QColor(self.theme["border"]), 1))
            p.drawRoundedRect(rect, 8, 8)
            # Accent dot + theme name in the theme's own text color
            dot_r = 5
            p.setBrush(QBrush(QColor(self.theme["gold"])))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(rect.left() + 10,
                          rect.center().y() - dot_r // 2, dot_r * 2, dot_r * 2)
            font = p.font()
            font.setPointSizeF(8.5)
            font.setBold(True)
            p.setFont(font)
            name = Theme.display_name(self.theme["name"])
            text_rect = rect.adjusted(24, 0, -4, 0)
            p.setPen(QColor(self.theme["gold" if self._selected else "text"]))
            fm = p.fontMetrics()
            elided = fm.elidedText(name, Qt.TextElideMode.ElideRight,
                                   text_rect.width())
            p.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter, elided)
            p.end()
        except Exception as ex:
            print(f"ThemeSwatch paint error: {ex}")


class _CloseButton(QPushButton):
    """Round flat ✕ button using the app's Lucide close icon."""

    SIZE = 24

    def __init__(self, theme: dict, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._hover = False
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Close")
        self.setAccessibleName("Close theme picker")
        self.setStyleSheet(
            "QPushButton { border: none; padding: 0; margin: 0;"
            " border-radius: %dpx; background: transparent; }" % (self.SIZE // 2))
        self._refresh_icon()

    def _refresh_icon(self):
        color = self.theme["text"] if self._hover else self.theme["muted"]
        self.setIcon(QIcon(render_icon(Icon.CLOSE, 13, color)))
        self.setIconSize(self.size())

    def enterEvent(self, e):
        self._hover = True
        self._refresh_icon()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self._refresh_icon()
        super().leaveEvent(e)


class ThemePickerPopup(QFrame):
    """Popup window showing all themes as swatches, grouped into
    Dark and Light sections (clamped to screen)."""

    theme_selected = pyqtSignal(str)

    COLS = 3          # 12 themes -> two tidy 3+3 blocks
    SWATCH_W = 104    # wide enough for names to stay readable
    HEADER_H = 28     # title row height (houses the ✕ button)

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
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(6)

        # --- Header row: [spacer][title][✕] keeps the title optically centered.
        header = QHBoxLayout()
        header.setSpacing(0)
        spacer = QWidget()
        spacer.setFixedSize(_CloseButton.SIZE, self.HEADER_H)
        spacer.setStyleSheet("background: transparent;")
        self._title_label = QLabel("Choose Theme")
        self._title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._close_btn = _CloseButton(self.theme)
        self._close_btn.clicked.connect(self.close)
        header.addWidget(spacer)
        header.addWidget(self._title_label, 1)
        header.addWidget(self._close_btn)
        layout.addLayout(header)

        self._section_labels: list[QLabel] = []
        self._swatches: list[tuple[ThemeSwatch, str]] = []

        darks = [t for t in Theme.ALL if t.get("is_dark", True)]
        lights = [t for t in Theme.ALL if not t.get("is_dark", True)]

        grid = QGridLayout()
        grid.setSpacing(6)

        row = 0
        if darks:
            grid.addWidget(self._make_section_label("DARK"), row, 0, 1, self.COLS)
            row += 1
            for idx, th in enumerate(darks):
                grid.addWidget(self._make_swatch(th),
                               row + idx // self.COLS, idx % self.COLS)
            row += (len(darks) + self.COLS - 1) // self.COLS
        if lights:
            grid.addWidget(self._make_section_label("LIGHT"), row, 0, 1, self.COLS)
            row += 1
            for idx, th in enumerate(lights):
                grid.addWidget(self._make_swatch(th),
                               row + idx // self.COLS, idx % self.COLS)
            row += (len(lights) + self.COLS - 1) // self.COLS
        layout.addLayout(grid)

        n_sections = sum(1 for g in (darks, lights) if g)
        total_rows = ((len(darks) + self.COLS - 1) // self.COLS if darks else 0) \
            + ((len(lights) + self.COLS - 1) // self.COLS if lights else 0)
        self.setFixedSize(
            12 * 2 + self.COLS * self.SWATCH_W + (self.COLS - 1) * 6,
            10 * 2 + self.HEADER_H + n_sections * 16 + total_rows * 44 +
            (total_rows - 1 + n_sections) * 6 + 8)

        # Reflect which swatch is active right away.
        self._sync_selection()

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------
    def _make_section_label(self, text) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color: {self.theme['muted']}; font-size: 10px;"
            f"font-weight: 600; background: transparent;"
            f"letter-spacing: 1px; border: none;")
        self._section_labels.append(lbl)
        return lbl

    def _make_swatch(self, th) -> ThemeSwatch:
        swatch = ThemeSwatch(th)
        swatch.clicked.connect(
            lambda checked=False, name=th["name"]: self._on_select(name))
        self._swatches.append((swatch, th["name"]))
        return swatch

    # ------------------------------------------------------------------
    # Behaviour
    # ------------------------------------------------------------------
    def _on_select(self, name: str):
        """Apply the chosen theme but KEEP the popup open so several
        themes can be auditioned back-to-back. Closing is left to an
        outside click or the ✕ button."""
        self.theme_selected.emit(name)
        self.current_theme = name
        self._restyle_to(name)
        self._sync_selection()

    def _restyle_to(self, name: str):
        """Live re-skin of the popup itself after a selection."""
        try:
            self.theme = Theme.get(name)
        except Exception:
            return
        t = self.theme
        self._title_label.setStyleSheet(
            f"color: {t['gold_light']}; font-size: 13px;"
            f"font-weight: 600; background: transparent;")
        for lbl in self._section_labels:
            lbl.setStyleSheet(
                f"color: {t['muted']}; font-size: 10px;"
                f"font-weight: 600; background: transparent;"
                f"letter-spacing: 1px; border: none;")
        self._close_btn.theme = t
        self._close_btn._refresh_icon()
        self.update()   # repaint rounded frame with new panel colors

    def _sync_selection(self):
        for swatch, name in self._swatches:
            swatch.set_selected(name == self.current_theme)

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
