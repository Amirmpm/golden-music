"""
Golden Music — Fullscreen player.

Immersive split view: giant cover + transport on the left, synced lyrics
on the right. Toggle with F11 / F from the main window; Esc, F, F11 or
double-click exits. Space/Enter toggles play, ←/→ changes track,
↑/↓ adjusts volume.

Keys are handled HERE (not by main-window QShortcuts) because this is a
top-level window — the main window's shortcuts are inactive while it has
focus.
"""
import os

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSizePolicy, QFrame
)
from PyQt6.QtGui import (
    QPixmap, QPainter, QColor, QLinearGradient, QBrush, QPainterPath,
    QIcon
)

from config import APP_NAME, Theme, fmt_time
from icons import Icon, render_icon, get_dpr
from widgets import ClickableSlider


class FullscreenPlayer(QWidget):
    """Full-screen now-playing stage: cover left, synced lyrics right."""

    def __init__(self, main_window):
        super().__init__(None)   # top-level window, not docked inside the UI
        self.main_window = main_window
        self.theme = main_window.theme
        self.setWindowTitle(f"{APP_NAME} — Now Playing")
        self.setWindowFlags(Qt.WindowType.Window)
        self.setCursor(Qt.CursorShape.ArrowCursor)

        self._cover = None
        self._path = None
        self._lyrics = None
        self._active_line = -1
        self._line_labels = []
        self._loader = None
        # Dynamic visualizer (WMP-legacy beat): animated gradient + pulse.
        # Toggleable here and mirrored to the main window attribute so the
        # setting survives; cheap QTimer animation, no audio FFT needed.
        self.visualizer_enabled = bool(
            getattr(main_window, "visualizer_enabled", True))
        self._phase = 0.0
        self._viz_timer = QTimer(self)
        self._viz_timer.setInterval(66)   # ~15 fps — smooth but cheap
        self._viz_timer.timeout.connect(self._on_viz_tick)
        self._lyrics_scroll = None

        self._build_ui()
        self._apply_theme()

    # ------------------------------------------------------------------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(48, 24, 48, 24)
        root.setSpacing(8)

        # ---- Top bar: visualizer + theme toggle … close ----------------
        top = QHBoxLayout()
        top.addStretch(1)
        self.viz_btn = QPushButton()
        self.viz_btn.setFixedSize(36, 36)
        self.viz_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.viz_btn.setToolTip("Toggle visualizer (animated background)")
        self.viz_btn.setCheckable(True)
        self.viz_btn.setChecked(self.visualizer_enabled)
        self.viz_btn.clicked.connect(self._on_viz_toggle)
        top.addWidget(self.viz_btn)
        self.theme_btn = QPushButton()
        self.theme_btn.setFixedSize(36, 36)
        self.theme_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_btn.setToolTip("Next theme")
        self.theme_btn.clicked.connect(self._on_next_theme)
        top.addWidget(self.theme_btn)
        self.close_btn = QPushButton()
        self.close_btn.setFixedSize(36, 36)
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.setToolTip("Exit fullscreen (Esc)")
        self.close_btn.clicked.connect(self.close)
        top.addWidget(self.close_btn)
        root.addLayout(top)

        # ---- Body: cover column + lyrics column ------------------------
        body = QHBoxLayout()
        body.setSpacing(36)

        left = QVBoxLayout()
        left.setSpacing(10)
        self.title_label = QLabel("No track selected")
        self.title_label.setObjectName("FS_Title")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.title_label.setWordWrap(True)
        left.addWidget(self.title_label)

        self.artist_label = QLabel("—")
        self.artist_label.setObjectName("FS_Artist")
        self.artist_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        left.addWidget(self.artist_label)

        self.cover_holder = QLabel()
        self.cover_holder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_holder.setSizePolicy(QSizePolicy.Policy.Expanding,
                                        QSizePolicy.Policy.Expanding)
        self.cover_holder.setMinimumSize(220, 220)
        # Single click on the cover toggles play; double-click exits.
        self.cover_holder.mousePressEvent = self._on_cover_click
        self.cover_holder.mouseDoubleClickEvent = self._on_double_click
        left.addWidget(self.cover_holder, 1)

        seek_row = QHBoxLayout()
        self.time_current = QLabel("0:00")
        self.time_current.setObjectName("FS_Time")
        self.seek = ClickableSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 1000)
        self.seek.setCursor(Qt.CursorShape.PointingHandCursor)
        self.seek.setObjectName("FS_Seek")
        self.time_total = QLabel("0:00")
        self.time_total.setObjectName("FS_Time")
        seek_row.addWidget(self.time_current)
        seek_row.addWidget(self.seek, 1)
        seek_row.addWidget(self.time_total)
        left.addLayout(seek_row)

        ctl = QHBoxLayout()
        ctl.addStretch(1)
        self.prev_btn = QPushButton()
        self.prev_btn.setObjectName("FS_Ctrl")
        self.prev_btn.setFixedSize(56, 56)
        self.prev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_btn = QPushButton()
        self.play_btn.setObjectName("FS_Play")
        self.play_btn.setFixedSize(84, 84)
        self.play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.next_btn = QPushButton()
        self.next_btn.setObjectName("FS_Ctrl")
        self.next_btn.setFixedSize(56, 56)
        self.next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ctl.addWidget(self.prev_btn)
        ctl.addSpacing(28)
        ctl.addWidget(self.play_btn)
        ctl.addSpacing(28)
        ctl.addWidget(self.next_btn)
        ctl.addStretch(1)
        left.addLayout(ctl)

        hint = QLabel("Esc — exit · F / F11 — toggle · Space — play · ←/→ — track · ↑/↓ — volume · click cover — play/pause")
        hint.setObjectName("FS_Hint")
        hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        hint.setWordWrap(True)
        left.addWidget(hint)

        left_w = QWidget()
        left_w.setLayout(left)
        left_w.setSizePolicy(QSizePolicy.Policy.Expanding,
                             QSizePolicy.Policy.Expanding)
        body.addWidget(left_w, 3)

        # ---- Right: synced lyrics --------------------------------------
        right = QVBoxLayout()
        right.setSpacing(8)
        lhead = QHBoxLayout()
        self.lyrics_title = QLabel("Lyrics")
        self.lyrics_title.setObjectName("FS_LyricsTitle")
        lhead.addWidget(self.lyrics_title)
        lhead.addStretch(1)
        self.lyrics_source = QLabel("")
        self.lyrics_source.setObjectName("FS_Hint")
        lhead.addWidget(self.lyrics_source)
        right.addLayout(lhead)

        self._lyrics_scroll = QScrollArea()
        self._lyrics_scroll.setWidgetResizable(True)
        self._lyrics_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._lyrics_scroll.setStyleSheet("background: transparent;")
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        self.lines_lay = QVBoxLayout(content)
        self.lines_lay.setContentsMargins(4, 8, 4, 24)
        self.lines_lay.setSpacing(12)
        self.lines_lay.addStretch(1)
        self._lyrics_scroll.setWidget(content)
        right.addWidget(self._lyrics_scroll, 1)

        right_w = QWidget()
        right_w.setLayout(right)
        right_w.setSizePolicy(QSizePolicy.Policy.Expanding,
                              QSizePolicy.Policy.Expanding)
        body.addWidget(right_w, 2)

        root.addLayout(body, 1)

        # Wiring (main window owns real playback state)
        self.prev_btn.clicked.connect(self.main_window._on_prev)
        self.play_btn.clicked.connect(self.main_window._on_play_pause)
        self.next_btn.clicked.connect(self.main_window._on_next)
        self.seek.sliderReleased.connect(self._on_seek_released)
        self.seek.sliderMoved.connect(self._on_seek_preview)
        self.seek.valueChanged.connect(self._on_seek_preview)
        self.main_window.audio.position_changed.connect(self._on_position)
        self.main_window.audio.duration_changed.connect(self._on_duration)
        self.main_window.audio.state_changed.connect(self._on_state)

    # ------------------------------------------------------------------
    def _apply_theme(self):
        self.theme = self.main_window.theme
        t = self.theme
        # NOTE: no opaque background here — paintEvent draws the (possibly
        # animated) gradient so the visualizer can move behind the content.
        self.setStyleSheet(f"""
            FullscreenPlayer {{ background: transparent; }}
            QLabel#FS_Title {{
                color: {t['gold_light']}; font-size: 32px; font-weight: 700;
                background: transparent;
            }}
            QLabel#FS_Artist {{
                color: {t['muted']}; font-size: 17px; background: transparent;
            }}
            QLabel#FS_LyricsTitle {{
                color: {t['gold_light']}; font-size: 18px; font-weight: 700;
                background: transparent;
            }}
            QLabel#FS_Time {{ color: {t['muted']}; font-size: 12px; background: transparent; }}
            QSlider#FS_Seek::groove:horizontal {{
                height: 6px; background: {t['panel_bg_2']}; border-radius: 3px;
            }}
            QSlider#FS_Seek::sub-page:horizontal {{
                background: {t['gold']}; border-radius: 3px;
            }}
            QSlider#FS_Seek::handle:horizontal {{
                width: 16px; height: 16px; margin: -5px 0;
                background: #ffffff; border-radius: 8px;
            }}
            QPushButton {{
                background: {t['panel_bg_2']}; border: 1px solid {t['border']};
                border-radius: 18px;
            }}
            QPushButton:hover {{ background: {t['active']}; }}
            QPushButton:checked {{ border: 2px solid {t['gold']}; }}
            QPushButton#FS_Play {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 {t['gold_light']}, stop:1 {t['gold']});
                border: none; border-radius: 42px;
            }}
            QPushButton#FS_Play:hover {{ background: {t['gold_light']}; }}
            QPushButton#FS_Ctrl {{ border-radius: 28px; }}
            QLabel#FS_Hint {{
                color: {t['muted']}; font-size: 11px; background: transparent;
            }}
        """)
        dpr = get_dpr()
        for btn, svg, size in ((self.prev_btn, Icon.PREV, 22),
                               (self.next_btn, Icon.NEXT, 22),
                               (self.play_btn, Icon.PLAY, 34),
                               (self.viz_btn, Icon.EQUALIZER, 18),
                               (self.theme_btn, Icon.BRUSH, 18),
                               (self.close_btn, Icon.CLOSE, 16)):
            try:
                pm = render_icon(svg, size, t["gold_light"], dpr)
                btn.setIcon(QIcon(pm))
                btn.setIconSize(pm.size() / dpr if dpr else pm.size())
            except Exception:
                pass
        self.viz_btn.setChecked(self.visualizer_enabled)
        self._restyle_lines()
        self._update_cover_label()

    # ------------------------------------------------------------------
    # Visualizer — animated gradient background (no FFT available from
    # QMediaPlayer, so rhythm is implied: slow drift + beat pulse while
    # playing). Cheap by design: ~15 fps repaint of one gradient.
    # ------------------------------------------------------------------
    def _on_viz_toggle(self):
        self.visualizer_enabled = bool(self.viz_btn.isChecked())
        try:
            self.main_window.visualizer_enabled = self.visualizer_enabled
        except Exception:
            pass
        if self.visualizer_enabled:
            self._viz_timer.start()
        else:
            self._viz_timer.stop()
            self.update()

    def _on_viz_tick(self):
        if not self.isVisible():
            return
        try:
            from audio import AudioBackend
            playing = (self.main_window.audio.state()
                       == AudioBackend.STATE_PLAYING)
        except Exception:
            playing = False
        self._phase += 0.12 if (playing and self.visualizer_enabled) else 0.02
        if self.visualizer_enabled:
            self.update()

    def paintEvent(self, e):
        try:
            from math import sin, cos
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            r = self.rect()
            t = self.theme
            base_top = QColor(t["window_bg"])
            base_bottom = QColor(t["panel_bg"])
            accent = QColor(t["gold"])
            if self.visualizer_enabled:
                # Drift the gradient stops with time; pulse with a fake beat
                # (two detuned sines ≈ breathing rhythm while playing).
                import time as _time
                beat = 0.5 + 0.5 * sin(self._phase * 2.1) * cos(self._phase * 0.7)
                shift = int(18 * sin(self._phase))
                top = QColor(
                    max(0, min(255, base_top.red() + shift)),
                    max(0, min(255, base_top.green() + shift // 2)),
                    max(0, min(255, base_top.blue() + shift)))
                grad = QLinearGradient(0, 0, r.width(), r.height())
                grad.setColorAt(0.0, top)
                grad.setColorAt(1.0, base_bottom)
                p.fillRect(r, QBrush(grad))
                # Soft accent glow, breathing with the beat
                glow_r = int(min(r.width(), r.height())
                             * (0.28 + 0.06 * beat))
                cx, cy = r.width() // 2, int(r.height() * 0.42)
                for i in range(3):
                    a = int(26 - i * 8 + beat * 10)
                    c = QColor(accent)
                    c.setAlpha(max(0, min(60, a)))
                    p.setBrush(QBrush(c))
                    p.setPen(Qt.PenStyle.NoPen)
                    rr = glow_r + i * 46
                    p.drawEllipse(cx - rr, cy - rr // 2, rr * 2, rr)
                _ = _time  # (kept local: no wall-clock needed, phase drives it)
            else:
                grad = QLinearGradient(0, 0, 0, r.height())
                grad.setColorAt(0.0, base_top)
                grad.setColorAt(1.0, base_bottom)
                p.fillRect(r, QBrush(grad))
            p.end()
        except Exception:
            pass
        super().paintEvent(e)

    # ------------------------------------------------------------------
    def showEvent(self, e):
        self.showFullScreen()
        self.refresh_state()
        if self.visualizer_enabled:
            self._viz_timer.start()
        super().showEvent(e)

    def hideEvent(self, e):
        try:
            self._viz_timer.stop()
        except Exception:
            pass
        super().hideEvent(e)

    def keyPressEvent(self, e):
        k = e.key()
        if k in (Qt.Key.Key_Escape, Qt.Key.Key_F, Qt.Key.Key_F11):
            self.close()
            e.accept()
            return
        if k in (Qt.Key.Key_Space, Qt.Key.Key_Enter, Qt.Key.Key_Return):
            self.main_window._on_play_pause()
            e.accept()
            return
        if k in (Qt.Key.Key_Right, Qt.Key.Key_MediaNext):
            self.main_window._on_next()
            e.accept()
            return
        if k in (Qt.Key.Key_Left, Qt.Key.Key_MediaPrevious):
            self.main_window._on_prev()
            e.accept()
            return
        if k == Qt.Key.Key_Up:
            self.main_window._change_volume(5)
            e.accept()
            return
        if k == Qt.Key.Key_Down:
            self.main_window._change_volume(-5)
            e.accept()
            return
        super().keyPressEvent(e)

    def mouseDoubleClickEvent(self, e):
        self.close()
        super().mouseDoubleClickEvent(e)

    def _on_cover_click(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.main_window._on_play_pause()
            e.accept()

    def _on_double_click(self, e):
        self.close()

    def _on_next_theme(self):
        """Cycle to the next theme (same order as the picker)."""
        try:
            names = Theme.names()
            cur = getattr(self.main_window, "theme_name",
                          Theme.DEFAULT_NAME)
            nxt = names[(names.index(cur) + 1) % len(names)] if cur in names \
                else names[0]
            self.main_window._on_theme_selected(nxt)
            self._apply_theme()
        except Exception:
            pass

    # ------------------------------------------------------------------
    def refresh_state(self):
        """Sync everything with live main-window state (called on show)."""
        mw = self.main_window
        if mw.current_track:
            title, artist = mw._current_track_tags()
            self.title_label.setText(title or "Unknown Title")
            self.artist_label.setText(artist or "—")
            try:
                self._cover = mw.player_bar._cover_thumb_pm
            except Exception:
                pass
            self._update_cover_label()
            self._load_lyrics_for_current()
        else:
            self.title_label.setText("No track selected")
            self.artist_label.setText("—")
        pos = mw.audio.position()
        dur = mw.audio.duration()
        self.time_current.setText(fmt_time(pos))
        self.time_total.setText(fmt_time(dur))
        if dur > 0:
            self.seek.blockSignals(True)
            self.seek.setValue(int(pos * 1000 / dur))
            self.seek.blockSignals(False)
        self._on_state(mw.audio.state())

    def _update_cover_label(self):
        """Scale the cover as large as the holder allows (rounded corners)."""
        if not self.isVisible() and self.cover_holder.width() <= 0:
            return
        side = max(220, int(min(self.cover_holder.width() or 420,
                                self.cover_holder.height() or 420) * 0.92))
        pm = self._cover
        if pm is not None and not pm.isNull():
            scaled = pm.scaled(side, side,
                               Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
            rounded = QPixmap(scaled.size())
            rounded.fill(Qt.GlobalColor.transparent)
            p = QPainter(rounded)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            path = QPainterPath()
            path.addRoundedRect(0, 0, scaled.width(), scaled.height(), 22, 22)
            p.setClipPath(path)
            p.drawPixmap(0, 0, scaled)
            p.end()
            self.cover_holder.setPixmap(rounded)
        else:
            dpr = get_dpr()
            t = self.theme
            grad_pm = QPixmap(side, side)
            grad_pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(grad_pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            grad = QLinearGradient(0, 0, side, side)
            grad.setColorAt(0.0, QColor(t["gold_deep"]))
            grad.setColorAt(0.5, QColor(t["gold"]))
            grad.setColorAt(1.0, QColor(t["gold_light"]))
            p.setBrush(QBrush(grad))
            p.setPen(Qt.PenStyle.NoPen)
            path = QPainterPath()
            path.addRoundedRect(0, 0, side, side, 22, 22)
            p.drawPath(path)
            note = render_icon(Icon.MUSIC_NOTE, max(64, side // 3),
                               "rgba(26,20,16,120)", dpr)
            p.drawPixmap((side - note.width()) // 2,
                         (side - note.height()) // 2, note)
            p.end()
            self.cover_holder.setPixmap(grad_pm)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._update_cover_label()

    # ------------------------------------------------------------------
    # Lyrics (right column) — same chain as the floating panel, with
    # auto-scroll of the active synced line to vertical center.
    # ------------------------------------------------------------------
    def _load_lyrics_for_current(self):
        mw = self.main_window
        path = mw.current_track
        if not path:
            self._show_lyrics_message("No track is playing.")
            return
        if path == self._path and self._lyrics is not None:
            return
        self._path = path
        self._lyrics = None
        self._active_line = -1
        title, artist = mw._current_track_tags()
        if not title:
            title = os.path.splitext(os.path.basename(path))[0]
        self.lyrics_title.setText(title)
        self.lyrics_source.setText("loading…")
        self._show_lyrics_message("Looking up lyrics…")
        if self._loader is not None and self._loader.isRunning():
            try:
                self._loader.cancel()
                self._loader.wait(200)
            except Exception:
                pass
        try:
            from lyrics_panel import LyricsLoaderThread
            self._loader = LyricsLoaderThread(
                path, title, artist,
                bool(getattr(mw, "lyrics_online_enabled", True)))
            self._loader.ready.connect(self._on_lyrics_ready)
            self._loader.start()
        except Exception:
            self._show_lyrics_message("No lyrics found for this track.")

    def _on_lyrics_ready(self, path, ly):
        if path != self._path:
            return
        if ly is None or ly.is_empty():
            self.lyrics_source.setText("")
            self._show_lyrics_message("No lyrics found for this track.")
            self._lyrics = None
            return
        self._lyrics = ly
        try:
            self.lyrics_source.setText(
                f"{ly.source} · {'synced' if ly.synced else 'plain'}")
        except Exception:
            pass
        self._clear_lyric_lines()
        t = self.theme
        for _ts, text in ly.lines:
            lbl = QLabel(text or "…")
            lbl.setWordWrap(True)
            lbl.setStyleSheet(
                f"color: {t['muted']}; font-size: 15px; background: transparent;")
            self.lines_lay.insertWidget(self.lines_lay.count() - 1, lbl)
            self._line_labels.append(lbl)
        self._active_line = -1
        # Jump to the live position immediately (track changed mid-play).
        try:
            self.sync_lyrics(self.main_window.audio.position())
        except Exception:
            pass

    def _clear_lyric_lines(self):
        while self.lines_lay.count() > 1:   # keep trailing stretch
            item = self.lines_lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._line_labels = []
        self._active_line = -1

    def _show_lyrics_message(self, text):
        self._clear_lyric_lines()
        lbl = QLabel(text)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setWordWrap(True)
        try:
            lbl.setStyleSheet(
                f"color: {self.theme['muted']}; font-size: 14px;"
                f"background: transparent;")
        except Exception:
            pass
        self.lines_lay.insertWidget(0, lbl)
        self._line_labels = []

    def sync_lyrics(self, pos_ms: int):
        """Highlight + center the active synced line."""
        ly = self._lyrics
        if not ly or not ly.synced or not self._line_labels:
            return
        try:
            idx = ly.line_for_time(pos_ms)
        except Exception:
            return
        if idx == self._active_line or idx < 0:
            return
        t = self.theme
        if 0 <= self._active_line < len(self._line_labels):
            try:
                self._line_labels[self._active_line].setStyleSheet(
                    f"color: {t['muted']}; font-size: 15px;"
                    f"background: transparent;")
            except Exception:
                pass
        if 0 <= idx < len(self._line_labels):
            try:
                self._line_labels[idx].setStyleSheet(
                    f"color: {t['gold_light']}; font-size: 17px;"
                    f"font-weight: 700; background: transparent;")
                if self._lyrics_scroll is not None:
                    self._lyrics_scroll.ensureWidgetVisible(
                        self._line_labels[idx], 0,
                        max(80, self._lyrics_scroll.viewport().height() // 2 - 40))
            except Exception:
                pass
        self._active_line = idx

    def _restyle_lines(self):
        if not self._line_labels or self._lyrics is None:
            return
        t = self.theme
        for i, lbl in enumerate(self._line_labels):
            try:
                active = (i == self._active_line)
                lbl.setStyleSheet(
                    f"color: {t['gold_light'] if active else t['muted']}; "
                    f"font-size: {17 if active else 15}px; "
                    f"font-weight: {700 if active else 400};"
                    f"background: transparent;")
            except Exception:
                pass

    # ------------------------------------------------------------------
    def _on_position(self, pos):
        # Track changed while fullscreen is open → refresh cover + lyrics.
        try:
            mw = self.main_window
            if mw.current_track != self._path:
                title, artist = mw._current_track_tags()
                self.title_label.setText(title or "Unknown Title")
                self.artist_label.setText(artist or "—")
                try:
                    self._cover = mw.player_bar._cover_thumb_pm
                except Exception:
                    pass
                self._update_cover_label()
                self._load_lyrics_for_current()
            else:
                # Cover arrives async — pick it up once the loader finishes.
                try:
                    fresh = mw.player_bar._cover_thumb_pm
                    if fresh is not self._cover and fresh is not None \
                            and not fresh.isNull():
                        self._cover = fresh
                        self._update_cover_label()
                except Exception:
                    pass
        except Exception:
            pass
        if not self.isVisible():
            return
        self.time_current.setText(fmt_time(pos))
        dur = self.main_window.audio.duration()
        if dur > 0 and not self.seek.isSliderDown():
            self.seek.blockSignals(True)
            self.seek.setValue(int(pos * 1000 / dur))
            self.seek.blockSignals(False)
        self.sync_lyrics(pos)

    def _on_duration(self, dur):
        if not self.isVisible():
            return
        self.time_total.setText(fmt_time(dur))

    def _on_state(self, state):
        if not self.isVisible():
            return
        from audio import AudioBackend
        svg = Icon.PAUSE if state == AudioBackend.STATE_PLAYING else Icon.PLAY
        dpr = get_dpr()
        try:
            pm = render_icon(svg, 34, self.theme["window_bg"], dpr)
            self.play_btn.setIcon(QIcon(pm))
            self.play_btn.setIconSize(pm.size() / dpr if dpr else pm.size())
        except Exception:
            pass

    def _on_seek_preview(self, val):
        try:
            dur = self.main_window.audio.duration()
        except Exception:
            dur = 0
        if dur > 0:
            self.time_current.setText(fmt_time(int(dur * val / 1000.0)))

    def _on_seek_released(self):
        try:
            dur = self.main_window.audio.duration()
        except Exception:
            dur = 0
        if dur > 0:
            try:
                pos = int(dur * self.seek.value() / 1000.0)
                self.main_window.audio.set_position(pos)
                self.time_current.setText(fmt_time(pos))
            except Exception:
                pass

    def closeEvent(self, e):
        # Returning from fullscreen just hides; state stays with the main
        # window so reopening is instant.
        try:
            self._viz_timer.stop()
        except Exception:
            pass
        e.accept()
