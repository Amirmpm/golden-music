"""Golden Music v3 — Windows System Media Transport Controls (SMTC).

Real Windows media-session integration (media overlay, lock screen, taskbar
flyout, hardware OSD) — distinct from the global media KEYS implemented by
``mediakeys.py`` (RegisterHotKey), which stays in place and is tested
independently.

Implementation notes
--------------------
* Uses the official WinRT API ``Windows.Media.SystemMediaTransportControls``
  bound to our top-level window through the
  ``ISystemMediaTransportControlsInterop`` factory (Win32 apps must use
  GetForWindow — SMTC cannot be activated directly without a CoreWindow).
* The WinRT bindings are OPTIONAL dependencies installed with the app
  (``winrt-runtime`` + ``winrt-Windows.Media`` + friends — see
  requirements.txt). When they are missing, or on non-Windows platforms,
  this module degrades to a no-op and the app keeps working with tray +
  global media keys only. Nothing here runs at import time.
* All WinRT calls happen on the GUI thread (SMTC is apartment-threaded);
  the public API is thread-safe via a queued-update cache: worker threads
  may call ``update_metadata`` freely — values land on the next GUI tick.
* The logic (state machine, metadata formatting, test plumbing) is plain
  Python and unit-tested on every platform; only the thin WinRT binding
  layer requires a real Windows runtime.
"""
from __future__ import annotations

import logging
import sys

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

log = logging.getLogger("goldenmusic.smtc")

PlaybackStatusClosed = 0
PlaybackStatusChanging = 1
PlaybackStatusPlaying = 2
PlaybackStatusPaused = 3
PlaybackStatusStopped = 4


class MediaMetadata:
    """Plain, testable value object for the SMTC display update."""

    __slots__ = ("title", "artist", "album", "position_ms", "duration_ms",
                 "thumbnail_png")

    def __init__(self, title="", artist="", album="",
                 position_ms=0, duration_ms=0, thumbnail_png=None):
        self.title = str(title or "")
        self.artist = str(artist or "")
        self.album = str(album or "")
        self.position_ms = max(0, int(position_ms or 0))
        self.duration_ms = max(0, int(duration_ms or 0))
        self.thumbnail_png = thumbnail_png  # bytes | None

    def as_dict(self):
        return {k: getattr(self, k) for k in self.__slots__}


class SystemMediaControls(QObject):
    """Qt-facing SMTC controller.

    Signals are emitted when the user presses Play/Pause/Next/Previous in
    the Windows media overlay, lock screen or taskbar flyout.
    """

    play_pause_requested = pyqtSignal()      # overlay Play OR Pause button
    next_requested = pyqtSignal()
    prev_requested = pyqtSignal()

    def __init__(self, window, enabled: bool = True, parent=None):
        super().__init__(parent or window)
        self._window = window
        self._impl = None                 # _SmtcInterop or None
        self._enabled = bool(enabled)
        self._last_status = PlaybackStatusStopped
        self._last_meta = MediaMetadata()
        self._pending_meta = None
        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(250)
        self._flush_timer.timeout.connect(self._flush_pending)
        self._flush_timer.start()
        if self._enabled:
            self.start()

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    @staticmethod
    def available() -> bool:
        """True when the WinRT bindings import AND we are on Windows."""
        if sys.platform != "win32":
            return False
        try:
            import winrt.windows.media          # noqa: F401
            import winrt.windows.storage.streams  # noqa: F401
            return True
        except Exception:
            return False

    def start(self) -> bool:
        """Bind SMTC to our window. Safe to call repeatedly; returns True
        when the native session is live."""
        self._enabled = True
        if self._impl is not None:
            return True
        if sys.platform != "win32":
            log.debug("SMTC skipped: not Windows")
            return False
        try:
            impl = _SmtcInterop(self._window)
            impl.button_pressed.connect(self._on_button)
            self._impl = impl
            self.set_enabled(True)
            log.info("SMTC session bound to window")
            return True
        except Exception as e:
            log.warning(f"SMTC unavailable (non-fatal): {e}")
            self._impl = None
            return False

    def stop(self):
        if self._impl is not None:
            try:
                self._impl.set_status(PlaybackStatusClosed)
            except Exception:
                pass
            self._impl = None
        self._enabled = False

    def set_enabled(self, on: bool):
        self._enabled = bool(on)
        if self._impl is not None:
            self._impl.set_button_flags(on)

    # ------------------------------------------------------------------
    # updates (thread-safe: worker threads cache, GUI timer flushes)
    # ------------------------------------------------------------------
    def update_metadata(self, meta: MediaMetadata):
        self._pending_meta = meta

    def set_status_playing(self, playing: bool):
        self._set_status(PlaybackStatusPlaying if playing
                         else PlaybackStatusPaused)

    def set_status_stopped(self):
        self._set_status(PlaybackStatusStopped)

    def _set_status(self, status: int):
        self._last_status = status
        if self._impl is not None:
            try:
                self._impl.set_status(status)
            except Exception as e:
                log.debug(f"SMTC status update failed: {e}")

    def _on_button(self, button: str):
        if button == "play" or button == "pause":
            self.play_pause_requested.emit()
        elif button == "next":
            self.next_requested.emit()
        elif button == "previous":
            self.prev_requested.emit()

    def _flush_pending(self):
        meta = self._pending_meta
        if meta is None or self._impl is None:
            return
        self._pending_meta = None
        self._last_meta = meta
        try:
            self._impl.update(meta)
        except Exception as e:
            log.debug(f"SMTC metadata update failed: {e}")

    # ------------------------------------------------------------------
    def is_active(self) -> bool:
        return self._impl is not None

    def state_summary(self) -> dict:
        """For Settings display + tests."""
        return {
            "active": self.is_active(),
            "available": self.available(),
            "status": self._last_status,
            "metadata": self._last_meta.as_dict(),
        }


# ---------------------------------------------------------------------------
# Native binding (Windows-only; imported lazily, never at module import)
# ---------------------------------------------------------------------------

class _SmtcInterop(QObject):
    """Thin WinRT binding. Kept 100% inside start()/update() so a missing
    package or an API difference degrades into a logged warning, never a
    crash. All calls must run on the GUI thread."""

    button_pressed = pyqtSignal(str)

    def __init__(self, window):
        super().__init__(window)
        import winrt.windows.media as wm
        import winrt.windows.storage.streams as wstreams

        self._wm = wm
        self._wstreams = wstreams
        self._smc = self._get_for_window(window)
        self._updater = self._smc.display_updater
        self._updater.type = wm.MediaPlaybackType.MUSIC
        self._smc.button_pressed.add(self._on_button_pressed)
        self._music = self._updater.music_properties

    # -- activation ---------------------------------------------------
    def _get_for_window(self, window):
        """Activate SMTC for a Win32 (non-CoreWindow) app.

        The projection exposes the interop factory either as a first-class
        member or via the module-level interop class — both shapes are
        supported (winsdk / winrt ≥3).
        """
        wm = self._wm
        hwnd = int(window.winId())
        interop = getattr(wm, "SystemMediaTransportControlsInterop", None)
        smc = None
        if interop is not None:
            try:
                smc = interop.get_for_window(hwnd)
            except Exception as e:
                log.debug(f"interop.get_for_window failed: {e}")
        if smc is None:
            # Older projections wrap the factory differently.
            factory = getattr(wm, "get_system_media_transport_controls_for_window", None)
            if factory is not None:
                smc = factory(hwnd)
        if smc is None:
            raise RuntimeError(
                "ISystemMediaTransportControlsInterop not reachable "
                "(winrt projection shape unknown)")
        return smc

    # -- state --------------------------------------------------------
    def set_button_flags(self, on: bool):
        self._smc.is_enabled = bool(on)
        self._smc.is_play_enabled = True
        self._smc.is_pause_enabled = True
        self._smc.is_next_enabled = True
        self._smc.is_previous_enabled = True

    def set_status(self, status: int):
        from winrt.windows.media import SystemMediaTransportControlsPlaybackStatus
        m = {
            PlaybackStatusClosed: SystemMediaTransportControlsPlaybackStatus.CLOSED,
            PlaybackStatusChanging: SystemMediaTransportControlsPlaybackStatus.CHANGING,
            PlaybackStatusPlaying: SystemMediaTransportControlsPlaybackStatus.PLAYING,
            PlaybackStatusPaused: SystemMediaTransportControlsPlaybackStatus.PAUSED,
            PlaybackStatusStopped: SystemMediaTransportControlsPlaybackStatus.STOPPED,
        }
        self._smc.playback_status = m[status]

    def update(self, meta: MediaMetadata):
        music = self._music
        music.title = meta.title or "Unknown title"
        music.artist = meta.artist or "Unknown artist"
        music.album_title = meta.album or ""
        if meta.duration_ms:
            music.track_number = 0
        if meta.thumbnail_png:
            thumb = self._thumbnail_from_png(meta.thumbnail_png)
            if thumb is not None:
                self._updater.thumbnail = thumb
        # timeline
        try:
            t = self._smc.timeline_properties
            t.position = winrt_TimeSpan_from_ms(meta.position_ms)
            t.end_time = winrt_TimeSpan_from_ms(meta.duration_ms)
            self._smc.update_timeline_properties(t)
        except Exception as e:
            log.debug(f"timeline update failed: {e}")
        self._updater.update()

    def _thumbnail_from_png(self, png: bytes):
        try:
            from winrt.windows.storage.streams import (
                InMemoryRandomAccessStream, DataWriter)
            stream = InMemoryRandomAccessStream()
            writer = DataWriter(stream)
            writer.write_bytes(png)
            writer.store_async().get_results()
            writer.flush_async().get_results()
            writer.detach_stream()
            from winrt.windows.storage.streams import RandomAccessStreamReference
            return RandomAccessStreamReference.create_from_stream(stream)
        except Exception as e:
            log.debug(f"thumbnail conversion failed: {e}")
            return None

    def _on_button_pressed(self, sender, args):
        try:
            btn = str(args.button).rsplit(".", 1)[-1].lower()
            self.button_pressed.emit(btn)
        except Exception:
            self.button_pressed.emit("play")


def winrt_TimeSpan_from_ms(ms: float):     # noqa: N802 (winrt naming)
    """winrt TimeSpan (100ns ticks) from milliseconds."""
    from winrt.windows.foundation import TimeSpan
    return TimeSpan(int(ms * 10000))
