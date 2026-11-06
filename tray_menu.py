"""
Golden Music — Custom tray menu widget.
A polished popup window: now-playing header (cover + title + artist),
transport controls, a seek bar with elapsed/total time, a volume row
(mute toggle + slider), and a bottom action row.
Row 0: [cover] Track title          (now playing)
Row 1: [prev]    [play/pause]    [next]
Row 2: [0:00] [======= seek =======] [3:45]
Row 3: [mute] [======= volume ==========]
Row 4: [show]  [library icon]  [quit]

The sliders are plain ClickableSlider instances with objectName
"SeekSlider" / "VolSlider" so the application-wide theme QSS styles them
exactly like the main player bar. All programmatic updates go through
the guarded update_*() methods (self._syncing) so no signal echo loops
are possible; user edits leave through exactly one signal each
(seek_requested / volume_changed).
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
from widgets import ClickableSlider


def _fmt_mmss(ms: int) -> str:
    """Compact m:ss label (no import from config — keep widget standalone)."""
    try:
        s = max(0, int(ms)) // 1000
        return f"{s // 60}:{s % 60:02d}"
    except Exception:
        return "0:00"


class TrayIconButton(QPushButton):
    """Glass circular icon button with an optional label under it."""

    def __init__(self, svg_string: str, size: int = 24, label: str = "",
                 parent=None, dims=None):
        super().__init__(parent)
        self.svg_string = svg_string
        self.icon_size = size
        self.label_text = label
        if label:
            self.setFixedSize(68, 60)
            self.setText(label)
        elif dims:
            self.setFixedSize(*dims)
        else:
            self.setFixedSize(48, 48)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover = False
        self._color = "#d4a85a"
        self._label_color = "rgba(240, 230, 210, 200)"
        self._apply_base_style()

    def _apply_base_style(self):
        r = 14
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent; border: none;
                border-radius: {r}px; padding: 2px; margin: 0;
                font-size: 11px; font-weight: 600; color: {self._label_color};
            }}
            QPushButton:hover {{
                background-color: rgba(255,255,255,26);
                border: 1px solid rgba(255,255,255,34);
            }}
        """)

    def set_label_color(self, color: str):
        """Keep the caption readable in both states."""
        self._label_color = color
        self._apply_base_style()

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
        from PyQt6.QtCore import QSize
        self.setIconSize(QSize(self.icon_size, self.icon_size))


class TrayMenuWidget(QFrame):
    """Custom popup tray menu with transport, seek and volume rows."""

    prev_clicked = pyqtSignal()
    play_clicked = pyqtSignal()
    next_clicked = pyqtSignal()
    show_clicked = pyqtSignal()
    close_clicked = pyqtSignal()
    quit_clicked = pyqtSignal()
    seek_requested = pyqtSignal(int)      # ms into the track
    volume_changed = pyqtSignal(int)      # 0..100 user volume

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._dividers = []          # kept so set_theme() can restyle them
        self._duration_ms = 0        # cached track duration for label math
        self._seek_drag = False      # user is dragging the popup seek bar
        self._syncing = False        # programmatic update in progress
        self._last_nonzero_vol = 80  # restore point for the mute toggle
        self.setWindowFlags(
            Qt.WindowType.Popup |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(300, 366)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 12)
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
        info_col.setSpacing(2)
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

        # Divider (attribute kept so set_theme() can restyle it)
        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background: {theme['border']};")
        layout.addWidget(divider)
        self._dividers.append(divider)

        # Row 1: prev / play / next
        row1 = QHBoxLayout()
        row1.setSpacing(4)
        self.btn_prev = TrayIconButton(Icon.PREV, 24, "Prev")
        self.btn_play = TrayIconButton(Icon.PLAY, 28, "Play")
        self.btn_next = TrayIconButton(Icon.NEXT, 24, "Next")
        row1.addWidget(self.btn_prev)
        row1.addWidget(self.btn_play)
        row1.addWidget(self.btn_next)
        row1_w = QWidget()
        row1_w.setLayout(row1)
        layout.addWidget(row1_w)

        # Row 2: seek — [elapsed] [======== slider ========] [total]
        seek_row = QHBoxLayout()
        seek_row.setSpacing(8)
        self.time_elapsed = QLabel("0:00")
        self.time_elapsed.setFixedWidth(34)
        self.time_elapsed.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.time_total = QLabel("0:00")
        self.time_total.setFixedWidth(34)
        self.time_total.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.seek_slider = ClickableSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setObjectName("SeekSlider")   # app QSS match
        self.seek_slider.setRange(0, 1000)
        self.seek_slider.setValue(0)
        self.seek_slider.setEnabled(False)             # no duration yet
        self.seek_slider.setCursor(Qt.CursorShape.PointingHandCursor)
        seek_row.addWidget(self.time_elapsed)
        seek_row.addWidget(self.seek_slider, 1)
        seek_row.addWidget(self.time_total)
        seek_row_w = QWidget()
        seek_row_w.setLayout(seek_row)
        layout.addWidget(seek_row_w)

        # Row 3: volume — [mute] [======== slider ========]
        vol_row = QHBoxLayout()
        vol_row.setSpacing(8)
        self.btn_mute = TrayIconButton(Icon.VOLUME_HIGH, 18, "",
                                       None, dims=(30, 30))
        self.btn_mute.setToolTip("Mute / unmute")
        self.vol_slider = ClickableSlider(Qt.Orientation.Horizontal)
        self.vol_slider.setObjectName("VolSlider")     # app QSS match
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(80)
        self.vol_slider.setCursor(Qt.CursorShape.PointingHandCursor)
        vol_row.addWidget(self.btn_mute)
        vol_row.addWidget(self.vol_slider, 1)
        vol_row_w = QWidget()
        vol_row_w.setLayout(vol_row)
        layout.addWidget(vol_row_w)

        # Divider (attribute kept so set_theme() can restyle it)
        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background: {theme['border']};")
        layout.addWidget(divider)
        self._dividers.append(divider)

        # Row 2: show/hide / quit / close-menu
        row2 = QHBoxLayout()
        row2.setSpacing(4)
        self.btn_show = TrayIconButton(Icon.CHEVRON_RIGHT, 22, "Show/Hide")
        self.btn_show.setToolTip("Show or hide the window")
        self.btn_quit = TrayIconButton(Icon.CLOSE, 22, "Quit")
        self.btn_quit.setToolTip("Quit")
        self.btn_close = TrayIconButton(Icon.CHEVRON_DOWN, 22, "Close")
        self.btn_close.setToolTip("Close this menu")
        row2.addWidget(self.btn_show)
        row2.addWidget(self.btn_quit)
        row2.addWidget(self.btn_close)
        row2_w = QWidget()
        row2_w.setLayout(row2)
        layout.addWidget(row2_w)

        # Wire up
        self.btn_prev.clicked.connect(self.prev_clicked.emit)
        self.btn_play.clicked.connect(self.play_clicked.emit)
        self.btn_next.clicked.connect(self.next_clicked.emit)
        self.btn_show.clicked.connect(self.show_clicked.emit)
        self.btn_close.clicked.connect(self.close_clicked.emit)
        self.btn_quit.clicked.connect(self.quit_clicked.emit)

        # Seek + volume wiring (single exit path per control)
        self.seek_slider.sliderPressed.connect(self._on_seek_pressed)
        self.seek_slider.sliderMoved.connect(self._on_seek_moved)
        self.seek_slider.sliderReleased.connect(self._on_seek_released)
        self.seek_slider.valueChanged.connect(self._on_seek_value)
        self.vol_slider.valueChanged.connect(self._on_vol_value)
        self.btn_mute.clicked.connect(self._on_mute_clicked)

        self._apply_theme(theme)

    def _apply_theme(self, theme: dict):
        self.theme = theme
        accent = theme["gold"]
        for btn in [self.btn_prev, self.btn_play, self.btn_next,
                    self.btn_show, self.btn_close, self.btn_quit,
                    self.btn_mute]:
            btn.set_color(accent)
            if btn.label_text:
                btn.set_label_color(theme["text"])
        # Restyle the header with the active theme colors
        self.track_title.setStyleSheet(
            f"color: {theme['gold_light']}; font-size: 13px; font-weight: 600;")
        self.track_artist.setStyleSheet(
            f"color: {theme['muted']}; font-size: 11px;")
        for lab in (self.time_elapsed, self.time_total):
            lab.setStyleSheet(f"color: {theme['muted']}; font-size: 10px;")
        self.cover_label.setStyleSheet(
            f"border-radius: 8px; border: 1px solid {theme['border']};"
            f"background: {theme['panel_bg_2']};")
        for d in self._dividers:
            d.setStyleSheet(f"background: {theme['border']};")
        self.update()                       # repaint glass bg w/ new theme

    def set_theme(self, theme: dict):
        """Public re-style entry point (same API as rail / player_bar)."""
        self._apply_theme(theme)

    def update_now_playing(self, title: str = "", artist: str = "",
                           cover_pm: QPixmap = None):
        """Refresh the header text and cover thumbnail."""
        try:
            t = (title or "").strip()
            a = (artist or "").strip()
            # Elide long strings so the popup keeps its size
            fm = self.track_title.fontMetrics()
            self.track_title.setText(fm.elidedText(t, Qt.TextElideMode.ElideRight, 190))
            fm2 = self.track_artist.fontMetrics()
            self.track_artist.setText(fm2.elidedText(a or "—", Qt.TextElideMode.ElideRight, 190))
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
            # Glassy background: semi-transparent panel
            bg = QColor(self.theme["panel_bg"])
            bg.setAlpha(185)
            p.setBrush(QBrush(bg))
            p.setPen(QPen(QColor(self.theme["border"]), 1))
            p.drawRoundedRect(rect, 12, 12)
            p.end()
        except Exception as ex:
            print(f"TrayMenuWidget paint error: {ex}")

    def update_play_state(self, playing: bool):
        self.btn_play.set_svg(Icon.PAUSE if playing else Icon.PLAY)

    # -------------------------------------------------------- progress / volume
    def is_seeking(self) -> bool:
        """True while the user drags the popup seek bar (window uses this to
        pause its position push so the handle doesn't fight the drag)."""
        return self._seek_drag

    def update_position(self, ms: int):
        """Programmatic position sync (from the window's position signal)."""
        if self._seek_drag:
            return
        self._syncing = True
        try:
            if self._duration_ms > 0:
                v = int(max(0, min(1000, int(ms) * 1000 / self._duration_ms)))
                self.seek_slider.setValue(v)
            self.time_elapsed.setText(_fmt_mmss(ms))
        except Exception:
            pass
        finally:
            self._syncing = False

    def update_duration(self, ms: int):
        """Programmatic duration sync; disables seeking for streams (0 ms)."""
        self._duration_ms = max(0, int(ms))
        self._syncing = True
        try:
            if self._duration_ms > 0:
                self.time_total.setText(_fmt_mmss(self._duration_ms))
                self.seek_slider.setEnabled(True)
            else:
                self.time_total.setText("--:--")
                self.seek_slider.setEnabled(False)
                self.seek_slider.setValue(0)
                self.time_elapsed.setText("0:00")
        except Exception:
            pass
        finally:
            self._syncing = False

    def update_volume(self, vol: int):
        """Programmatic volume sync (echo-safe)."""
        vol = max(0, min(100, int(vol)))
        self._syncing = True
        try:
            self.vol_slider.setValue(vol)
        except Exception:
            pass
        finally:
            self._syncing = False
        if vol > 0:
            self._last_nonzero_vol = vol
        self._refresh_mute_icon(vol)

    # ------------------------------------------------------------- internals
    def _on_seek_pressed(self):
        self._seek_drag = True

    def _on_seek_moved(self, val: int):
        # Live elapsed label while dragging / clicking
        if self._duration_ms > 0:
            self.time_elapsed.setText(
                _fmt_mmss(int(self._duration_ms * val / 1000.0)))

    def _on_seek_released(self):
        self._seek_drag = False
        self._commit_seek(self.seek_slider.value())

    def _on_seek_value(self, val: int):
        # Only keyboard interaction reaches this without a press first;
        # drags/clicks are handled by press/move/release above.
        if self._syncing or self._seek_drag:
            return
        self._commit_seek(val)

    def _commit_seek(self, val: int):
        if self._duration_ms <= 0:
            return
        try:
            ms = int(self._duration_ms * val / 1000.0)
            self.time_elapsed.setText(_fmt_mmss(ms))
            self.seek_requested.emit(ms)
        except Exception:
            pass

    def _on_vol_value(self, val: int):
        if self._syncing:
            return
        val = max(0, min(100, int(val)))
        if val > 0:
            self._last_nonzero_vol = val
        self._refresh_mute_icon(val)
        self.volume_changed.emit(val)

    def _on_mute_clicked(self):
        """Mute is 'volume 0' — the same convention as the main player bar,
        so there is exactly ONE volume code path in the window."""
        cur = self.vol_slider.value()
        target = 0 if cur > 0 else (self._last_nonzero_vol or 80)
        if target != cur:
            self.vol_slider.setValue(target)   # -> _on_vol_value -> emit
        else:
            self._refresh_mute_icon(cur)       # defensive: keep icon truthful

    def _refresh_mute_icon(self, vol: int):
        try:
            if vol == 0:
                self.btn_mute.set_svg(Icon.VOLUME_MUTE)
            elif vol < 40:
                self.btn_mute.set_svg(Icon.VOLUME_LOW)
            else:
                self.btn_mute.set_svg(Icon.VOLUME_HIGH)
        except Exception:
            pass
