"""
Golden Music — Custom widgets v6.0.

ClickableSlider: a QSlider with click-to-seek AND reliable drag-to-seek.
The drag is handled entirely here (press/move/release) so the app can
reliably know when the user is seeking — QSlider's built-in drag tracking
fires sliderPressed/sliderReleased at unpredictable times when the press
lands on the groove instead of the handle, which broke dragging.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QSlider
from PyQt6.QtGui import QMouseEvent


class ClickableSlider(QSlider):
    """A QSlider where clicking anywhere jumps to that position and
    dragging scrubs smoothly. Emits dragStarted / dragFinished so the
    owner can pause position updates while the user is seeking."""

    def __init__(self, orientation=Qt.Orientation.Horizontal, parent=None):
        super().__init__(orientation, parent)
        self._dragging = False
        self.setTracking(True)

    def set_smooth(self, smooth: bool):
        """For compatibility — tracking is always on."""
        self.setTracking(True)

    def set_theme(self, theme: dict):
        """For compatibility — styling is done via QSS."""
        pass

    # ------------------------------------------------------------------
    def _value_for_pos(self, x: float, y: float) -> int:
        if self.orientation() == Qt.Orientation.Horizontal:
            w = max(1, self.width())
            ratio = min(1.0, max(0.0, x / w))
        else:
            h = max(1, self.height())
            ratio = min(1.0, max(0.0, 1.0 - (y / h)))
        return round(self.minimum() + ratio * (self.maximum() - self.minimum()))

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            val = self._value_for_pos(event.position().x(), event.position().y())
            self.setSliderDown(True)          # native handle-pressed look
            self.setValue(val)
            self.sliderPressed.emit()
            self.sliderMoved.emit(val)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._dragging:
            val = self._value_for_pos(event.position().x(), event.position().y())
            self.setValue(val)
            self.sliderMoved.emit(val)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self.setSliderDown(False)
            self.sliderReleased.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def is_dragging(self) -> bool:
        return self._dragging
