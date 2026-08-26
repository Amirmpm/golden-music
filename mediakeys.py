"""
Golden Music — Global media key support (Windows).

Registers the standard multimedia hotkeys (Play/Pause, Stop, Next, Prev)
with RegisterHotKey and translates the WM_HOTKEY messages into Qt signals
via a native event filter. Works with keyboard media keys and most headset
controls. Degrades silently when a key is already claimed by another app.

PyQt6 notes (why this class looks unusual):
1. PyQt6 delivers `message` to nativeEventFilter() as a raw sip.voidptr —
   it exposes NO `.message` / `.wParam` attributes. We must cast the
   address to a real MSG structure with ctypes.
2. QAbstractNativeEventFilter is NOT a QObject subclass in PyQt6, so
   pyqtSignal attributes on it can never connect or emit. The signals
   therefore live on a tiny QObject sidecar; the filter forwards the
   parsed hotkey id to it.
"""
import ctypes
import logging

from PyQt6.QtCore import (
    QAbstractNativeEventFilter, QObject, pyqtSignal
)

log = logging.getLogger("app.mediakeys")

try:
    import win32con
    import win32api
    import win32gui
    # RegisterHotKey lives in win32gui (not win32api) in pywin32 builds
    HAVE_WIN32 = hasattr(win32gui, "RegisterHotKey")
except ImportError:
    HAVE_WIN32 = False

# Windows MSG structure — needed to decode the raw native event pointer.
# Prefer the canonical wintypes layout; fall back to an identical manual
# definition for environments where ctypes.wintypes is unavailable.
try:
    import ctypes.wintypes as _wintypes
    _MSG = _wintypes.MSG
except Exception:                                    # pragma: no cover
    class _MSG(ctypes.Structure):
        _fields_ = [
            ("hwnd", ctypes.c_void_p),
            ("message", ctypes.c_uint),
            ("wParam", ctypes.c_size_t),     # WPARAM  = UINT_PTR
            ("lParam", ctypes.c_ssize_t),    # LPARAM  = LONG_PTR
            ("time", ctypes.c_uint),
            ("pt_x", ctypes.c_long),
            ("pt_y", ctypes.c_long),
        ]

# Virtual-key codes for standard media keys
VK_MEDIA_PLAY_PAUSE = 0xB3
VK_MEDIA_STOP = 0xB2
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1

_HOTKEY_ID_BASE = 0xB00F  # arbitrary app-local range
_THREAD_MESSAGE = 0x0312  # WM_HOTKEY


class _MediaKeyEmitter(QObject):
    """QObject sidecar that actually owns the hotkey signals."""

    play_pause = pyqtSignal()
    stop = pyqtSignal()
    next = pyqtSignal()
    prev = pyqtSignal()


class MediaKeyFilter(QAbstractNativeEventFilter):
    """Installs Windows media hotkeys; emits Qt signals on press.

    Connect to the play_pause / stop / next / prev bound signals exposed
    as properties — e.g. ``filter.play_pause.connect(handler)``.
    """

    def __init__(self):
        super().__init__()
        self._registered = False
        self._emitter = _MediaKeyEmitter()

    # -- signal surface (bound Signal objects: support connect & emit) ---
    @property
    def play_pause(self):
        return self._emitter.play_pause

    @property
    def stop(self):
        return self._emitter.stop

    @property
    def next(self):
        return self._emitter.next

    @property
    def prev(self):
        return self._emitter.prev

    # -- hotkey registration --------------------------------------------
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

    # -- native message pump --------------------------------------------
    def nativeEventFilter(self, event_type, message):
        """Translate WM_HOTKEY thread messages into signals.

        `message` arrives as sip.voidptr (a plain address in PyQt6); cast
        it to a MSG struct before touching any fields.
        """
        try:
            try:
                et = bytes(event_type)
            except Exception:
                et = b""
            if et and et != b"windows_generic_MSG":
                return False, 0
            addr = int(message)
            if not addr:
                return False, 0
            msg = _MSG.from_address(addr)
            if int(msg.message) != _THREAD_MESSAGE:
                return False, 0
            offset = int(msg.wParam) - _HOTKEY_ID_BASE
            if offset == 0:
                self._emitter.play_pause.emit()
            elif offset == 1:
                self._emitter.stop.emit()
            elif offset == 2:
                self._emitter.next.emit()
            elif offset == 3:
                self._emitter.prev.emit()
        except Exception:
            pass  # never break the event loop from a message hook
        return False, 0
