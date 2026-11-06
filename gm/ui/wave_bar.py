"""Golden Music 2.3 — waveform seekbar + spectrum visualizer widgets."""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from widgets import ClickableSlider


class WaveformSeekbar(ClickableSlider):
    """Seek slider that paints the track's waveform peaks behind the groove.

    Without peaks it renders exactly like the plain themed slider (the
    setting 'waveform_seekbar' simply controls whether peaks are fed in)."""

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._peaks: list[float] | None = None
        self._hover_pos: float | None = None

    def set_peaks(self, peaks):
        self._peaks = peaks if peaks else None
        self.update()

    def peaks(self):
        return self._peaks

    def clear_peaks(self):
        self._peaks = None
        self.update()

    def setHoverPosition(self, ratio: float | None):
        self._hover_pos = ratio
        self.update()

    def paintEvent(self, e):
        if not self._peaks:
            super().paintEvent(e)
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        mid = h / 2.0
        groove = QRectF(0, mid - 2.5, w, 5.0)
        # played ratio
        rng = self.maximum() - self.minimum()
        ratio = (self.value() - self.minimum()) / rng if rng else 0.0
        _p = self.parentWidget()
        t = getattr(_p, "theme", None) if _p else None
        base = QColor(t.get("panel_bg_2", "#2a2a30")) if t else QColor("#2a2a30")
        accent = QColor(t.get("gold", "#d4a85a")) if t else QColor("#d4a85a")
        played = accent
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(base)
        p.drawRoundedRect(groove, 2.5, 2.5)
        # waveform bars
        peaks = self._peaks
        n = len(peaks)
        if n:
            bar_w = max(1.0, w / (n * 1.6))
            step = w / n
            amp = mid - 4.0
            for i, v in enumerate(peaks):
                x = i * step
                bh = max(1.5, v * amp)
                col = played if x <= ratio * w else base
                col = QColor(col)
                if x > ratio * w:
                    col.setAlpha(190)
                p.setBrush(col)
                p.drawRoundedRect(QRectF(x, mid - bh, bar_w, bh * 2), 1.0, 1.0)
        # hover preview line
        if self._hover_pos is not None:
            pen = QPen(accent)
            pen.setStyle(Qt.PenStyle.DashLine)
            pen.setWidthF(1.0)
            p.setPen(pen)
            x = self._hover_pos * w
            p.drawLine(QPointF(x, mid - 7), QPointF(x, mid + 7))
        # handle
        hx = ratio * w
        p.setBrush(accent)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(hx, mid), 5.5, 5.5)

    def mouseMoveEvent(self, e):
        if self._peaks is not None:
            self.setHoverPosition(max(0.0, min(1.0, e.position().x() / max(1, self.width()))))
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        self.setHoverPosition(None)
        super().leaveEvent(e)


class SpectrumVisualizer(QWidget):
    """20-band spectrum canvas fed by real FFT magnitudes (0..1).

    Idle state renders nothing (keeps idle CPU at zero). When the tap is
    unavailable the widget hides itself (see gm.audio_tap.ContentTap)."""

    def __init__(self, parent=None, bands: int = 20):
        super().__init__(parent)
        self._mags = [0.0] * bands
        self._peaks = [0.0] * bands
        self._theme = None
        self._active = False
        self.setMinimumHeight(26)
        self.setMaximumHeight(48)
        self._decay = QTimer(self)
        self._decay.setInterval(50)
        self._decay.timeout.connect(self._tick_decay)

    def set_theme(self, t):
        self._theme = t
        self.update()

    def set_active(self, active: bool):
        self._active = bool(active)
        if active:
            self._decay.start()
        else:
            self._decay.stop()
            self._mags = [0.0] * len(self._mags)
            self._peaks = [0.0] * len(self._peaks)
        self.update()

    def set_magnitudes(self, mags):
        if mags is None:
            self.hide()
            return
        self.show()
        self._mags = list(mags)[:len(self._mags)] + \
            [0.0] * max(0, len(self._mags) - len(mags))
        if not self._decay.isActive():
            self._decay.start()

    def _tick_decay(self):
        changed = False
        for i, v in enumerate(self._mags):
            if v > 0.005:
                self._mags[i] = v * 0.82
                changed = True
        if self._peaks:
            for i, pk in enumerate(self._peaks):
                target = self._mags[i] if i < len(self._mags) else 0.0
                self._peaks[i] = max(target, pk - 0.012)
        if changed or any(v > 0.01 for v in self._mags):
            self.update()
        else:
            self._decay.stop()

    def paintEvent(self, e):
        if not any(v > 0.01 for v in self._mags):
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        t = self._theme or {}
        gold = QColor(t.get("gold", "#d4a85a"))
        gold_l = QColor(t.get("gold_light", "#e8c98a"))
        n = len(self._mags)
        gap = 2.0
        bar_w = max(2.0, (w - gap * (n - 1)) / n)
        grad = QLinearGradient(0, h, 0, 0)
        grad.setColorAt(0.0, gold)
        grad.setColorAt(1.0, gold_l)
        p.setPen(Qt.PenStyle.NoPen)
        for i, v in enumerate(self._mags):
            x = i * (bar_w + gap)
            bh = max(2.0, v * (h - 2))
            p.setBrush(QColor(gold) if i % 2 else QColor(gold_l))
            p.drawRoundedRect(QRectF(x, h - bh, bar_w, bh), 1.5, 1.5)
            pk = self._peaks[i] if self._peaks else 0.0
            if pk > 0.02:
                py = h - pk * (h - 2)
                p.setBrush(QColor(t.get("text", "#fff")))
                p.drawRoundedRect(QRectF(x, py, bar_w, 1.5), 0.5, 0.5)
