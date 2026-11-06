"""Golden Music 3.0 — bridges between MainWindow and the remote servers.

The servers (gm.webremote / gm.telegrambot) call a duck-typed bridge from
their own threads; the bridges below marshal every call onto the Qt main
thread via QMetaObject.invokeMethod-ish QTimer.singleShot(0) queueing, so
the player state is only ever touched from the GUI thread (thread-safe by
construction).
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("app.remote")


def _on_main(fn):
    """Run fn on the Qt main loop (best-effort; drop if app is gone)."""
    try:
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, fn)
    except Exception:
        try:
            fn()
        except Exception:
            pass


def _marshal(fn, *args):
    return lambda: _on_main(lambda: fn(*args))


class WindowPlayerBridge:
    """Implements gm.webremote.PlayerBridge against MainWindow."""

    def __init__(self, mw):
        self.mw = mw
        self._cover_png = None

    # -- status snapshot (reads simple attrs; safe cross-thread) ----------
    def get_status(self):
        mw = self.mw
        try:
            state = 0
            try:
                state = int(mw.audio.state())
            except Exception:
                state = 0
            title, artist = "", ""
            cur = mw.current_track
            if cur:
                if str(cur).startswith(("http://", "https://")):
                    title = mw.player_bar.title_label.text() or "Radio"
                    artist = "Internet radio"
                else:
                    title, artist = mw._tag_cache.get(
                        cur, (os.path.splitext(os.path.basename(cur))[0], ""))
            dur = 0
            pos = 0
            vol = 80
            try:
                dur = int(mw.audio.duration())
                pos = int(mw.audio.position())
            except Exception:
                pass
            try:
                vol = int(mw.player_bar.vol_slider.value())
            except Exception:
                pass
            return {
                "state": ("playing" if state == 1 else
                          "paused" if state == 2 else "stopped"),
                "title": title or "",
                "artist": artist or "",
                "album": "",
                "position_ms": pos,
                "duration_ms": dur,
                "volume": vol,
                "shuffle": bool(getattr(mw, "shuffle", False)),
                "repeat": (getattr(mw, "repeat_mode", None).name
                           if getattr(mw, "repeat_mode", None) else "OFF"),
                "queue_count": len(getattr(mw, "current_playlist", []) or []),
                "queue_position": int(getattr(mw, "current_index", -1)) + 1,
                "can_seek": True,
            }
        except Exception:
            log.exception("bridge get_status failed")
            return {"state": "stopped", "title": "", "artist": "",
                    "album": "", "position_ms": 0, "duration_ms": 0,
                    "volume": 0, "shuffle": False, "repeat": "OFF",
                    "queue_count": 0, "queue_position": 0, "can_seek": False}

    def get_cover_png(self):
        try:
            pm = getattr(self.mw, "_last_cover_pm", None)
            if pm is None or pm.isNull():
                return None
            from PyQt6.QtCore import QBuffer, QIODevice
            buf = QBuffer()
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            pm.save(buf, "PNG")
            data = bytes(buf.data())
            buf.close()
            return data or None
        except Exception:
            return None

    # -- actions (marshaled to the GUI thread) ----------------------------
    def play_pause(self):
        _on_main(self._pp)
        return True

    def _pp(self):
        mw = self.mw
        try:
            if mw.audio.state() == 1:
                mw.audio.pause()
            else:
                mw.audio.play()
        except Exception:
            pass

    def next(self):
        _on_main(self.mw._on_next)
        return True

    def prev(self):
        _on_main(self.mw._on_prev)
        return True

    def stop(self):
        _on_main(self._stop)
        return True

    def _stop(self):
        try:
            self.mw.audio.stop()
        except Exception:
            pass

    def seek_ms(self, ms):
        try:
            ms = max(0, int(ms))
        except (TypeError, ValueError):
            return False
        _on_main(lambda: self.mw.audio.set_position(ms))
        return True

    def set_volume(self, vol):
        try:
            vol = max(0, min(100, int(vol)))
        except (TypeError, ValueError):
            return False
        _on_main(lambda: self.mw.player_bar.vol_slider.setValue(vol))
        return True

    def toggle_shuffle(self):
        _on_main(self.mw._on_shuffle_toggled)
        return True

    def cycle_repeat(self):
        _on_main(self.mw._on_repeat_toggled)
        return True


class WindowTelegramBridge(WindowPlayerBridge):
    """Adds search/play_index for the Telegram bot."""

    def __init__(self, mw):
        super().__init__(mw)
        self._last_list = []

    def search(self, query, limit=8):
        try:
            mw = self.mw
            hits = []
            if getattr(mw, "store", None) is not None:
                hits = mw.store.search(query, limit)
            else:
                q = (query or "").lower()
                hits = [p for p in mw.library
                        if q in os.path.basename(p).lower()][:limit]
            self._last_list = []
            out = []
            for p in hits[:limit]:
                title, artist = mw._tag_cache.get(
                    p, (os.path.splitext(os.path.basename(p))[0], ""))
                self._last_list.append(p)
                out.append({"title": title or os.path.basename(p),
                            "artist": artist or ""})
            return out
        except Exception:
            return []

    def play_index(self, i):
        try:
            i = int(i)
        except (TypeError, ValueError):
            return False
        if 0 <= i < len(self._last_list):
            path = self._last_list[i]
            _on_main(lambda p=path: self.mw._play_path_standalone(p))
            return True
        return False

    def queue_items(self, limit=10):
        try:
            mw = self.mw
            out = []
            pl = list(getattr(mw, "current_playlist", []) or [])
            idx = int(getattr(mw, "current_index", -1))
            for i, p in enumerate(pl):
                if i > idx and len(out) < limit:
                    t, a = mw._tag_cache.get(
                        p, (os.path.splitext(os.path.basename(p))[0], ""))
                    out.append({"title": t or os.path.basename(p),
                                "artist": a or ""})
            return out
        except Exception:
            return []


_web_server = None
_tg_remote = None


def sync_remotes(mw):
    """Start/stop both remotes according to mw.prefs (called on apply)."""
    global _web_server, _tg_remote
    prefs = getattr(mw, "prefs", {}) or {}

    # ---- web remote ----
    want_web = bool(prefs.get("webremote_enabled", False))
    if want_web:
        if _web_server is None:
            try:
                from gm.webremote import WebRemoteServer, TokenAuth
                token = str(prefs.get("webremote_token", "") or "")
                if not token:
                    token = TokenAuth.generate_token()
                    mw.prefs["webremote_token"] = token
                bridge = WindowPlayerBridge(mw)
                _web_server = WebRemoteServer(
                    bridge,
                    host=str(prefs.get("webremote_host", "127.0.0.1")),
                    port=int(prefs.get("webremote_port", 8642)),
                    token=token)
                host, port = _web_server.start()
                log.info("web remote on %s:%s", host, port)
                mw._show_toast(f"Web remote: {host}:{port}")
            except Exception:
                log.exception("web remote start failed")
                _web_server = None
        else:
            # token/host changes: cheap approach — restart
            pass
    elif _web_server is not None:
        try:
            _web_server.stop()
        except Exception:
            pass
        _web_server = None

    # ---- telegram ----
    want_tg = bool(prefs.get("telegram_enabled", False))
    token = str(prefs.get("telegram_bot_token", "") or "")
    allowed = set()
    for part in str(prefs.get("telegram_allowed_ids", "") or "").replace(
            " ", "").split(","):
        if part.strip().isdigit():
            allowed.add(int(part))
    if want_tg and token:
        if _tg_remote is None:
            try:
                from gm.telegrambot import (TelegramRemote,
                                            validate_token_format)
                if not validate_token_format(token):
                    mw._show_toast("Telegram token invalid — remote off")
                    return
                bridge = WindowTelegramBridge(mw)
                _tg_remote = TelegramRemote(token, allowed, bridge)
                _tg_remote.start()
                log.info("telegram remote started (%d allowed users)",
                         len(allowed))
            except Exception:
                log.exception("telegram start failed")
                _tg_remote = None
    elif _tg_remote is not None:
        try:
            _tg_remote.stop()
        except Exception:
            pass
        _tg_remote = None


def stop_all():
    """App shutdown hook."""
    global _web_server, _tg_remote
    for srv in (_web_server, _tg_remote):
        if srv is not None:
            try:
                srv.stop()
            except Exception:
                pass
    _web_server = None
    _tg_remote = None


def web_server_info():
    """(host, port, token) for the pairing QR, or None."""
    if _web_server is None:
        return None
    try:
        return (_web_server.lan_ip(), _web_server.port, _web_server.token)
    except Exception:
        return None
