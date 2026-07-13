"""
Golden Music — Custom widgets v5.0 (STABLE).

ClickableSlider: a QSlider subclass with click-to-seek.
NO custom painting — uses QSlider's built-in rendering via QSS.
Only overrides mousePressEvent to compute value from click position.
"""
from PyQt6.QtCore import Qt, QPoint, QPointF
from PyQt6.QtWidgets import QSlider, QStyle, QStyleOptionSlider
from PyQt6.QtGui import QMouseEvent


class ClickableSlider(QSlider):
    """A QSlider where clicking anywhere on the groove jumps to that position.
    Uses QSlider's built-in painting — no custom paint code.
    """

    def __init__(self, orientation=Qt.Orientation.Horizontal, parent=None):
        super().__init__(orientation, parent)

    def set_smooth(self, smooth: bool):
        """For compatibility — QSlider already tracks smoothly."""
        self.setTracking(True)

    def set_theme(self, theme: dict):
        """For compatibility — styling is done via QSS."""
        pass

    def mousePressEvent(self, event: QMouseEvent):
        """Click anywhere to jump to that position."""
        if event.button() == Qt.MouseButton.LeftButton:
            # Compute value from click position using simple math
            if self.orientation() == Qt.Orientation.Horizontal:
                w = self.width()
                if w > 0:
                    ratio = event.position().x() / w
                else:
                    ratio = 0.0
            else:
                h = self.height()
                if h > 0:
                    ratio = 1.0 - (event.position().y() / h)
                else:
                    ratio = 0.0
            ratio = max(0.0, min(1.0, ratio))
            val = int(self.minimum() + ratio * (self.maximum() - self.minimum()))
            self.setValue(val)
            # Emit sliderMoved so connected slots fire
            self.sliderMoved.emit(val)
            # Let QSlider handle the rest (start dragging from new position)
            super().mousePressEvent(event)
        else:
            super().mousePressEvent(event)
