"""
Golden Music — Auto theme: follow Windows dark/light mode.

Polls the registry value AppsUseLightTheme every 10 s (cheap, no win32
events plumbing) and emits a Qt signal when it flips. Windows-only; on
other platforms the module simply never reports a change.
"""
import logging

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

log = logging.getLogger("app.autottheme")

try:
    import winreg
    HAVE_WINREG = True
except ImportError:
    HAVE_WINREG = False


def windows_prefers_dark() -> bool:
    if not HAVE_WINREG:
        return True
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return val == 0
    except OSError:
        return True


class AutoThemeWatcher(QObject):
    changed = pyqtSignal(bool)   # True = switch to dark

    def __init__(self, parent=None):
        super().__init__(parent)
        self._last = windows_prefers_dark() if HAVE_WINREG else None
        self._timer = QTimer(self)
        self._timer.setInterval(10_000)
        self._timer.timeout.connect(self._poll)

    def start(self):
        if HAVE_WINREG:
            self._timer.start()

    def stop(self):
        self._timer.stop()

    def _poll(self):
        cur = windows_prefers_dark()
        if cur != self._last:
            log.info(f"Windows theme flipped -> {'dark' if cur else 'light'}")
            self._last = cur
            self.changed.emit(cur)
