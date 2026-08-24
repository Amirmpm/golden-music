"""
Golden Music — Global media key support (Windows).

Registers the standard multimedia hotkeys (Play/Pause, Stop, Next, Prev)
with RegisterHotKey and translates the WM_HOTKEY messages into Qt signals
via a native event filter. Works with keyboard media keys and most headset
controls. Degrades silently when a key is already claimed by another app.
"""
import logging

from PyQt6.QtCore import QAbstractNativeEventFilter, QObject, pyqtSignal

log = logging.getLogger("app.mediakeys")

try:
    import win32con
    import win32api
    import win32gui
    # RegisterHotKey lives in win32gui (not win32api) in pywin32 builds
    HAVE_WIN32 = hasattr(win32gui, "RegisterHotKey")
except ImportError:
    HAVE_WIN32 = False

# Virtual-key codes for standard media keys
VK_MEDIA_PLAY_PAUSE = 0xB3
VK_MEDIA_STOP = 0xB2
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1

_HOTKEY_ID_BASE = 0xB00F  # arbitrary app-local range
_THREAD_MESSAGE = 0x0312  # WM_HOTKEY


class MediaKeyFilter(QAbstractNativeEventFilter):
    """Installs Windows media hotkeys; emits Qt signals on press."""

    play_pause = pyqtSignal()
    stop = pyqtSignal()
    next = pyqtSignal()
    prev = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._registered = False

    def install(self) -> bool:
        if not HAVE_WIN32:
            log.info("Media keys unavailable (pywin32 not installed)")
            return False
        keys = [
            (_HOTKEY_ID_BASE + 0, VK_MEDIA_PLAY_PAUSE),
            (_HOTKEY_ID_BASE + 1, VK_MEDIA_STOP),
            (_HOTKEY_ID_BASE + 2, VK_MEDIA_NEXT_TRACK),
            (_HOTKEY_ID_BASE + 3, VK_MEDIA_PREV_TRACK),
        ]
        ok_any = False
        for hotkey_id, vk in keys:
            try:
                if win32gui.RegisterHotKey(None, hotkey_id, 0, vk):
                    ok_any = True
                else:
                    log.info(f"Media hotkey VK_{vk:#04x} already owned by another app")
            except Exception as e:
                # pywin32 raises (1409 'Hot key is already registered.') when
                # another player owns the key — that's a soft failure here.
                log.info(f"Media hotkey VK_{vk:#04x} unavailable: {e}")
        self._registered = ok_any
        if ok_any:
            log.info("Media key hotkeys registered")
        return ok_any

    def uninstall(self):
        if not HAVE_WIN32 or not self._registered:
            return
        for i in range(4):
            try:
                win32gui.UnregisterHotKey(None, _HOTKEY_ID_BASE + i)
            except Exception:
                pass
        self._registered = False

    def nativeEventFilter(self, event_type, message):
        """Translate WM_HOTKEY thread messages into signals."""
        try:
            if int(message.message) == _THREAD_MESSAGE:
                hotkey_id = int(message.wParam)
                offset = hotkey_id - _HOTKEY_ID_BASE
                if offset == 0:
                    self.play_pause.emit()
                elif offset == 1:
                    self.stop.emit()
                elif offset == 2:
                    self.next.emit()
                elif offset == 3:
                    self.prev.emit()
        except Exception:
            pass  # never break the event loop from a message hook
        return False, 0
