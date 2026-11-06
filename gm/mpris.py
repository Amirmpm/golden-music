"""Golden Music 3.0 — MPRIS D-Bus integration (Linux).

Registers org.mpris.MediaPlayer2.goldenmusic on the session bus when
dbus-python is available, exposing the standard Root + Player interfaces:
metadata (title/artist/artUrl/duration), Play/Pause/Next/Previous/Seeked,
Can* capability properties, and the desktop-entry identity. Media keys and
desktop shells (GNOME/KDE) control the player through this surface.

The service is optional and degrades silently (no dbus / headless / CI).
Thread model: dbus mainloop integration is avoided on purpose — the bridge
polls a snapshot getter on a QTimer and pushes property changes; commands
arrive on the dbus dispatch thread and are marshaled to the GUI thread via
Qt signals.
"""
from __future__ import annotations

import logging
import os
import urllib.parse

log = logging.getLogger("app.mpris")

SERVICE = "org.mpris.MediaPlayer2.goldenmusic"
PATH = "/org/mpris/MediaPlayer2"

_IFACE_ROOT = "org.mpris.MediaPlayer2"
_IFACE_PLAYER = "org.mpris.MediaPlayer2.Player"

_META = {
    "mpris:trackid": ("/org/mpris/MediaPlayer2/Track/%d" % 0),
    "xesam:title": "",
    "xesam:artist": [""],
    "xesam:album": "",
    "mpris:length": 0,
}


class MprisBridge:
    """Snapshot/command surface between MainWindow and the dbus service."""

    def __init__(self, mw):
        self.mw = mw

    def status(self) -> dict:
        mw = self.mw
        try:
            state = 0
            try:
                state = int(mw.audio.state())
            except Exception:
                state = 0
            title, artist = "", ""
            if mw.current_track and not str(mw.current_track).startswith(
                    ("http://", "https://")):
                title, artist = mw._tag_cache.get(
                    mw.current_track,
                    (os.path.splitext(os.path.basename(mw.current_track))[0],
                     ""))
            dur = 0
            pos = 0
            try:
                dur = int(mw.audio.duration()) * 1000      # MPRIS: µs
                pos = int(mw.audio.position()) * 1000
            except Exception:
                pass
            return {
                "playback_status": ("Playing" if state == 1 else
                                    "Paused" if state == 2 else "Stopped"),
                "title": title or "",
                "artist": [artist] if artist else [],
                "album": "",
                "length_us": dur,
                "position_us": max(0, pos),
                "can_go_next": True,
                "can_go_previous": True,
                "can_play": True,
                "can_pause": True,
                "can_seek": True,
                "shuffle": bool(getattr(mw, "shuffle", False)),
                "loop_status": ("Track" if getattr(mw, "repeat_mode", None)
                                and mw.repeat_mode.name == "ONE" else
                                "Playlist" if getattr(mw, "repeat_mode", None)
                                and mw.repeat_mode.name == "ALL" else "None"),
                "art_url": ("file://" + urllib.parse.quote(mw.current_track)
                            if mw.current_track and os.path.isfile(
                                mw.current_track) else ""),
            }
        except Exception:
            return {"playback_status": "Stopped", "title": "",
                    "artist": [], "album": "", "length_us": 0,
                    "position_us": 0, "can_go_next": True,
                    "can_go_previous": True, "can_play": True,
                    "can_pause": True, "can_seek": True, "shuffle": False,
                    "loop_status": "None", "art_url": ""}


class MprisService:
    """Owns the dbus connection. start() returns False when unavailable."""

    def __init__(self, bridge: MprisBridge):
        self.bridge = bridge
        self._bus = None
        self._conn = None

    def start(self) -> bool:
        try:
            import dbus
            import dbus.service
        except Exception:
            log.info("dbus-python unavailable; MPRIS off")
            return False
        try:
            from PyQt6.QtCore import QObject, pyqtSignal, QTimer
        except Exception:
            return False

        bridge = self.bridge
        mw = bridge.mw

        class _Player(dbus.service.Object):
            _cmd = pyqtSignal(str)

            def __init__(self, bus):
                super().__init__(bus, PATH)
                self._cmd.connect(self._dispatch)

            # ---------------- Root interface ----------------
            @dbus.service.method(_IFACE_ROOT, in_signature="", out_signature="")
            def Raise(self):
                self._cmd.emit("raise")

            @dbus.service.method(_IFACE_ROOT, in_signature="", out_signature="")
            def Quit(self):
                self._cmd.emit("quit")

            @dbus.service.property(_IFACE_ROOT, signature="s")
            def Identity(self):
                return "Golden Music"

            @dbus.service.property(_IFACE_ROOT, signature="s")
            def DesktopEntry(self):
                return "goldenmusic"

            @dbus.service.property(_IFACE_ROOT, signature="b")
            def CanQuit(self):
                return True

            @dbus.service.property(_IFACE_ROOT, signature="b")
            def CanRaise(self):
                return True

            @dbus.service.property(_IFACE_ROOT, signature="b")
            def HasTrackList(self):
                return False

            @dbus.service.property(_IFACE_ROOT, signature="as")
            def SupportedUriSchemes(self):
                return dbus.Array(["file"], signature="s")

            @dbus.service.property(_IFACE_ROOT, signature="as")
            def SupportedMimeTypes(self):
                return dbus.Array(
                    ["audio/mpeg", "audio/flac", "audio/ogg", "audio/mp4",
                     "audio/wav"], signature="s")

            # ---------------- Player interface ----------------
            @dbus.service.method(_IFACE_PLAYER, in_signature="", out_signature="")
            def Next(self):
                self._cmd.emit("next")

            @dbus.service.method(_IFACE_PLAYER, in_signature="", out_signature="")
            def Previous(self):
                self._cmd.emit("prev")

            @dbus.service.method(_IFACE_PLAYER, in_signature="", out_signature="")
            def PlayPause(self):
                self._cmd.emit("playpause")

            @dbus.service.method(_IFACE_PLAYER, in_signature="", out_signature="")
            def Play(self):
                self._cmd.emit("play")

            @dbus.service.method(_IFACE_PLAYER, in_signature="", out_signature="")
            def Pause(self):
                self._cmd.emit("pause")

            @dbus.service.method(_IFACE_PLAYER, in_signature="", out_signature="")
            def Stop(self):
                self._cmd.emit("stop")

            @dbus.service.method(_IFACE_PLAYER, in_signature="x", out_signature="")
            def Seek(self, offset):
                self._cmd.emit("seek:%d" % int(offset))

            @dbus.service.method(_IFACE_PLAYER, in_signature="ox", out_signature="")
            def SetPosition(self, track_id, position):
                self._cmd.emit("seekabs:%d" % int(position))

            @dbus.service.property(_IFACE_PLAYER, signature="s")
            def PlaybackStatus(self):
                return bridge.status()["playback_status"]

            @dbus.service.property(_IFACE_PLAYER, signature="d")
            def Rate(self):
                return 1.0

            @dbus.service.property(_IFACE_PLAYER, signature="b")
            def Shuffle(self):
                return bridge.status()["shuffle"]

            @dbus.service.property(_IFACE_PLAYER, signature="s")
            def LoopStatus(self):
                return bridge.status()["loop_status"]

            @dbus.service.property(_IFACE_PLAYER, signature="x")
            def Position(self):
                return bridge.status()["position_us"]

            @dbus.service.property(_IFACE_PLAYER, signature="a{sv}")
            def Metadata(self):
                st = bridge.status()
                m = {
                    "mpris:trackid": dbus.ObjectPath(
                        "/org/mpris/MediaPlayer2/Track/current"),
                    "mpris:length": dbus.Int64(st["length_us"]),
                    "xesam:title": st["title"] or "Golden Music",
                    "xesam:artist": dbus.Array(st["artist"], signature="s"),
                    "xesam:album": st["album"],
                }
                if st["art_url"]:
                    m["mpris:artUrl"] = st["art_url"]
                return dbus.Dictionary(m, signature="sv")

            @dbus.service.property(_IFACE_PLAYER, signature="b")
            def CanGoNext(self):
                return True

            @dbus.service.property(_IFACE_PLAYER, signature="b")
            def CanGoPrevious(self):
                return True

            @dbus.service.property(_IFACE_PLAYER, signature="b")
            def CanPlay(self):
                return True

            @dbus.service.property(_IFACE_PLAYER, signature="b")
            def CanPause(self):
                return True

            @dbus.service.property(_IFACE_PLAYER, signature="b")
            def CanSeek(self):
                return True

            @dbus.service.property(_IFACE_PLAYER, signature="b")
            def CanControl(self):
                return True

            @dbus.service.signal(_IFACE_PLAYER, signature="")
            def Seeked(self):
                pass

            # ---------------- command dispatch (GUI thread) --------------
            def _dispatch(self, cmd):
                try:
                    if cmd == "next":
                        mw._on_next()
                    elif cmd == "prev":
                        mw._on_prev()
                    elif cmd == "playpause":
                        mw._on_play_pause()
                    elif cmd == "play":
                        mw.audio.play()
                    elif cmd == "pause":
                        mw.audio.pause()
                    elif cmd == "stop":
                        mw.audio.stop()
                    elif cmd == "raise":
                        mw._reveal_window()
                    elif cmd == "quit":
                        mw._quit()
                    elif cmd.startswith("seek:"):
                        pos = mw.audio.position() + int(cmd[5:]) / 1000
                        mw.audio.set_position(max(0, int(pos)))
                    elif cmd.startswith("seekabs:"):
                        mw.audio.set_position(int(cmd[8:]) // 1000)
                except Exception:
                    log.exception("mpris command failed")

        try:
            import dbus.mainloop.glib  # noqa: F401  (avoid mainloop takeover)
        except Exception:
            pass
        try:
            self._bus = dbus.SessionBus()
            name = dbus.service.BusName(SERVICE, self._bus)
            self._player = _Player(self._bus)
            # poll-based property change notifications (cheap: only while
            # the poll detects a change does it emit PropertiesChanged)
            self._last_sig = None

            def _poll():
                try:
                    st = bridge.status()
                    sig = (st["playback_status"], st["title"],
                           st["length_us"], st["shuffle"], st["loop_status"])
                    if sig != self._last_sig:
                        self._last_sig = sig
                        self._emit_properties_changed()
                except Exception:
                    pass

            self._timer = QTimer(mw)
            self._timer.setInterval(1000)
            self._timer.timeout.connect(_poll)
            self._timer.start()
            log.info("MPRIS service active: %s", SERVICE)
            return True
        except Exception:
            log.info("MPRIS unavailable (no session bus?)")
            return False

    def _emit_properties_changed(self):
        try:
            import dbus
            self._bus.emit_signal(
                PATH, "org.freedesktop.DBus.Properties",
                "PropertiesChanged", "sa{sv}as",
                _IFACE_PLAYER,
                {"PlaybackStatus": dbus.String(
                    self.bridge.status()["playback_status"], variant_level=1),
                 "Metadata": dbus.Dictionary(
                     self._player.Metadata, signature="sv", variant_level=1)},
                [])
        except Exception:
            pass

    def stop(self):
        try:
            if getattr(self, "_timer", None) is not None:
                self._timer.stop()
        except Exception:
            pass


def start_mpris(mw):
    """Convenience: create + start (returns service or None)."""
    try:
        from gm.platforms import mpris_available
        if not mpris_available():
            return None
        svc = MprisService(MprisBridge(mw))
        return svc if svc.start() else None
    except Exception:
        log.exception("mpris start failed")
        return None
