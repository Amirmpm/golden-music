"""
Golden Music 2.3 — LAN web remote control server (stdlib only, no Qt).

A tiny, dependency-free remote-control channel for the player: one
mobile-friendly single-page HTML UI plus a small JSON API.  The module is
completely inert until the app creates and starts a server — the web
remote is OFF by default.

App integration
---------------
    from gm.webremote import WebRemoteServer

    remote = WebRemoteServer(bridge=my_bridge, host="0.0.0.0", port=8642)
    host, port = remote.start()      # binds the socket, returns real address
    ...                              # show remote.token / the /api/pair QR page
    remote.stop()                    # idempotent, joins the worker thread

``host="127.0.0.1"`` (default) keeps the remote private to the machine;
``host="0.0.0.0"`` exposes it to the LAN.  ``port=0`` asks the OS for an
ephemeral port.

Thread-safety contract (IMPORTANT)
----------------------------------
``ThreadingHTTPServer`` spawns one thread per request and the UI polls,
so several bridge calls can run concurrently on different threads.  The
APP must therefore make its ``PlayerBridge`` implementation thread-safe.
Recommended pattern: a ``queue.Queue`` that marshals actions into the Qt
main loop (drained by a ``QTimer``) and a lock-guarded snapshot dict for
``get_status()``.  Bridge methods must never touch widgets directly —
they execute on HTTP worker threads, not the GUI thread.

Security notes
--------------
* Every data endpoint requires the pairing token (query ``?token=``,
  ``X-GM-Token`` header or ``Authorization: Bearer``); failed attempts
  are rate-limited per client IP.
* Routes are exact-match strings — no filesystem serving, no traversal.
* No CORS headers are emitted; browser requests carrying a foreign
  ``Origin`` are refused outright.
* Tokens never reach the logs: query strings are stripped before any
  logging and ``TokenAuth`` masks itself in ``repr``.
"""
from __future__ import annotations

import abc
import base64
import hmac
import html as html_mod
import io
import json
import logging
import re
import secrets
import socket
import threading
import time
import urllib.parse
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

__all__ = [
    "PlayerBridge",
    "TokenAuth",
    "WebRemoteServer",
    "TOKEN_RE",
    "sanitize_status",
    "qrcode_png",
    "lan_ip",
]

log = logging.getLogger("app.remote")

MAX_BODY_BYTES = 16 * 1024            # /api/control JSON body cap
RATE_MAX_FAILURES = 10                # consecutive auth failures ...
RATE_WINDOW_S = 60.0                  # ... within this window ...
RATE_BLOCK_S = 60.0                   # ... block the client for this long
TOKEN_RE = re.compile(r"^GM-[0-9a-f]{4}(?:-[0-9a-f]{4}){2,}$")

_VALID_STATES = frozenset({"playing", "paused", "stopped"})
_VALID_REPEAT = frozenset({"off", "all", "one"})

# Actions that take no "value" (whitelist shared by /api/control).
_SIMPLE_ACTIONS: tuple[str, ...] = (
    "play_pause", "next", "prev", "stop", "toggle_shuffle", "cycle_repeat",
)
CONTROL_ACTIONS = frozenset(_SIMPLE_ACTIONS + ("set_volume", "seek_ms"))


# ---------------------------------------------------------------------------
# Bridge protocol (implemented by the app)
# ---------------------------------------------------------------------------
class PlayerBridge(abc.ABC):
    """The player surface the app must implement for the web remote.

    The HTTP server calls these methods from its worker threads — possibly
    several at once (status polling races with control posts).  The app
    MUST make the implementation thread-safe; the recommended pattern is
    marshalling actions through a queue into the Qt main loop and serving
    ``get_status()`` from a lock-guarded snapshot.  All action methods
    return ``bool`` success and must not raise (log internally instead);
    if they do raise anyway the server answers 500 and keeps running.

    ``get_cover_png`` is optional — the default returns ``None`` (no art).
    """

    @abc.abstractmethod
    def get_status(self) -> dict[str, Any]:
        """Return a snapshot dict with the keys:

        ``state`` ("playing" | "paused" | "stopped"), ``title``, ``artist``,
        ``album`` (str), ``position_ms``, ``duration_ms`` (int, ms),
        ``volume`` (int 0-100), ``shuffle`` (bool), ``repeat``
        ("off" | "all" | "one"), ``queue_count`` (int), ``queue_position``
        (int, 0-based; -1 when nothing plays), ``can_seek`` (bool).
        """

    @abc.abstractmethod
    def play_pause(self) -> bool:
        """Toggle playback. Returns True on success."""

    @abc.abstractmethod
    def next(self) -> bool:
        """Skip to the next track. Returns True on success."""

    @abc.abstractmethod
    def prev(self) -> bool:
        """Go back to the previous track. Returns True on success."""

    @abc.abstractmethod
    def stop(self) -> bool:
        """Stop playback. Returns True on success."""

    @abc.abstractmethod
    def seek_ms(self, ms: int) -> bool:
        """Seek to an absolute position in milliseconds. Returns True."""

    @abc.abstractmethod
    def set_volume(self, volume: int) -> bool:
        """Set volume (0-100). Returns True on success."""

    @abc.abstractmethod
    def toggle_shuffle(self) -> bool:
        """Toggle shuffle mode. Returns True on success."""

    @abc.abstractmethod
    def cycle_repeat(self) -> bool:
        """Cycle repeat mode (off -> all -> one). Returns True."""

    def get_cover_png(self) -> bytes | None:
        """Current cover art as PNG bytes, or ``None`` when unavailable.

        Optional hook — the base implementation reports "no cover".
        """
        return None


# ---------------------------------------------------------------------------
# Token authentication
# ---------------------------------------------------------------------------
def _mask_token(token: str) -> str:
    """Mask a token for safe display/logging (never the real value)."""
    if not token:
        return "<none>"
    return token.split("-", 1)[0] + "-****-****-****"


class TokenAuth:
    """Pairing-token holder: generation, constant-time verify, rotation.

    The token is a secret: it is never logged (``__repr__`` masks it) and
    ``rotate()`` logs only the masked form.  Verification uses
    ``hmac.compare_digest`` so timing does not leak the value.
    """

    def __init__(self, token: str | None = None) -> None:
        self._lock = threading.Lock()
        self._token: str = token if token else self.generate_token()

    @property
    def token(self) -> str:
        """The current token (secret — do not log this value)."""
        with self._lock:
            return self._token

    @staticmethod
    def generate_token(nbytes: int = 16) -> str:
        """Return a fresh ``GM-xxxx-xxxx-...`` token.

        ``secrets.token_hex`` based, grouped into 4-hex-char blocks
        (``GM-XXXX-XXXX-XXXX`` style).  ``nbytes`` is clamped so the
        result always has at least three groups.
        """
        nbytes = max(6, int(nbytes))
        raw = secrets.token_hex(nbytes)
        groups = [raw[i:i + 4] for i in range(0, len(raw), 4)]
        return "GM-" + "-".join(groups)

    def verify(self, token: Any) -> bool:
        """Constant-time check of a candidate token."""
        if not isinstance(token, str):
            return False
        with self._lock:
            current = self._token.encode("utf-8")
        candidate = token.encode("utf-8", "replace")[:256]
        return hmac.compare_digest(current, candidate)

    def rotate(self) -> str:
        """Generate, store and return a new token (old one stops working)."""
        new_token = self.generate_token()
        with self._lock:
            old = self._token
            self._token = new_token
        log.info("pairing token rotated (previous %s)", _mask_token(old))
        return new_token

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"TokenAuth(token={_mask_token(self._token)})"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_STATUS_DEFAULTS: dict[str, Any] = {
    "state": "stopped", "title": "", "artist": "", "album": "",
    "position_ms": 0, "duration_ms": 0, "volume": 0, "shuffle": False,
    "repeat": "off", "queue_count": 0, "queue_position": -1,
    "can_seek": False,
}


def _clean_int(value: Any, default: int,
               lo: int | None = None, hi: int | None = None) -> int:
    """Coerce to int with clamping; bools and junk fall back to default."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    try:
        out = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if lo is not None and out < lo:
        out = lo
    if hi is not None and out > hi:
        out = hi
    return out


def _clean_str(value: Any, default: str, limit: int = 500) -> str:
    if not isinstance(value, str):
        return default
    return value[:limit]


def sanitize_status(raw: Any) -> dict[str, Any]:
    """Coerce whatever the bridge produced into a safe, complete status dict.

    Missing keys get defaults, wrong types get cast or replaced — the wire
    format is always well-formed JSON with bounded string lengths.
    """
    out = dict(_STATUS_DEFAULTS)
    if not isinstance(raw, dict):
        return out
    state = raw.get("state")
    out["state"] = state if state in _VALID_STATES else "stopped"
    out["title"] = _clean_str(raw.get("title"), "")
    out["artist"] = _clean_str(raw.get("artist"), "")
    out["album"] = _clean_str(raw.get("album"), "")
    out["position_ms"] = _clean_int(raw.get("position_ms"), 0, lo=0)
    out["duration_ms"] = _clean_int(raw.get("duration_ms"), 0, lo=0)
    out["volume"] = _clean_int(raw.get("volume"), 0, lo=0, hi=100)
    out["shuffle"] = bool(raw.get("shuffle", False))
    repeat = raw.get("repeat")
    out["repeat"] = repeat if repeat in _VALID_REPEAT else "off"
    out["queue_count"] = _clean_int(raw.get("queue_count"), 0, lo=0)
    out["queue_position"] = _clean_int(raw.get("queue_position"), -1, lo=-1)
    out["can_seek"] = bool(raw.get("can_seek", False))
    return out


def qrcode_png(url: str) -> bytes | None:
    """QR-code PNG bytes for *url*, or ``None`` when unavailable.

    The ``qrcode`` package is an OPTIONAL runtime dependency: it is
    imported lazily here only, so the module itself stays stdlib-only.
    Any failure returns ``None`` and the pair page falls back to text.
    """
    try:
        import qrcode  # lazy on purpose — optional dependency
    except ImportError:
        log.info("qrcode module unavailable; pair page uses text fallback")
        return None
    except Exception:  # pragma: no cover - defensive
        log.warning("qrcode import failed unexpectedly", exc_info=True)
        return None
    try:
        buf = io.BytesIO()
        img = qrcode.make(url)
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        log.warning("qrcode generation failed", exc_info=True)
        return None


def lan_ip() -> str:
    """Best-effort LAN IPv4 of this machine (no packets are sent).

    Uses the UDP ``connect`` trick — the route is looked up locally.
    Falls back to ``127.0.0.1``.  Never raises.
    """
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0)
        sock.connect(("10.255.255.255", 1))
        return str(sock.getsockname()[0])
    except OSError:
        return "127.0.0.1"
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# The single-page UI (inline everything; no external resources at all)
# ---------------------------------------------------------------------------
_INDEX_HTML = """<!DOCTYPE html>
<html lang="en" dir="ltr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="dark">
<title>Golden Music Remote</title>
<style>
  :root {
    --bg: #0e1013; --card: #171a20; --card2: #21262f; --line: #262c36;
    --text: #e8e6e1; --dim: #9aa0a8; --accent: #d4a437; --accent2: #f0c05a;
  }
  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  html, body {
    margin: 0; padding: 0; background: var(--bg); color: var(--text);
    font: 16px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  }
  /* RTL-friendly: only logical properties; text runs use dir="auto" */
  body {
    min-height: 100vh; display: flex; justify-content: center;
    padding: max(10px, env(safe-area-inset-top)) 10px
             calc(14px + env(safe-area-inset-bottom));
  }
  #app { width: 100%; max-width: 480px; display: flex; flex-direction: column; gap: 12px; }
  header { display: flex; align-items: center; gap: 10px; padding: 6px 4px; }
  header .logo {
    width: 30px; height: 30px; border-radius: 8px; flex: none;
    background: linear-gradient(135deg, var(--accent), #8a6a1f);
    display: grid; place-items: center; font-weight: 700; color: #141414;
  }
  header h1 { font-size: 1.05rem; margin: 0; letter-spacing: .3px; }
  header .dot {
    margin-inline-start: auto; width: 10px; height: 10px;
    border-radius: 50%; background: #5a6270;
  }
  header .dot.on { background: #3fb96a; box-shadow: 0 0 8px #3fb96a88; }
  .card { background: var(--card); border: 1px solid var(--line); border-radius: 16px; padding: 14px; }
  .now { display: flex; gap: 12px; align-items: center; }
  .cover, .ph {
    width: 84px; height: 84px; border-radius: 12px; flex: none;
    background: var(--card2); object-fit: cover;
  }
  .ph { display: grid; place-items: center; font-size: 2rem; color: var(--dim); }
  .meta { min-width: 0; flex: 1; text-align: start; }
  .title { font-size: 1.1rem; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .artist, .album { color: var(--dim); font-size: .9rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .queue { color: var(--dim); font-size: .8rem; margin-top: 4px; }
  .times {
    display: flex; justify-content: space-between; color: var(--dim);
    font-size: .8rem; font-variant-numeric: tabular-nums; margin-top: 6px;
  }
  input[type=range] {
    -webkit-appearance: none; appearance: none; width: 100%; height: 44px;
    background: transparent; margin: 0; cursor: pointer;
  }
  input[type=range]::-webkit-slider-runnable-track { height: 8px; border-radius: 6px; background: var(--card2); }
  input[type=range]::-webkit-slider-thumb {
    -webkit-appearance: none; width: 26px; height: 26px; border-radius: 50%;
    background: var(--accent); margin-top: -9px; border: none;
  }
  input[type=range]::-moz-range-track { height: 8px; border-radius: 6px; background: var(--card2); }
  input[type=range]::-moz-range-thumb { width: 26px; height: 26px; border-radius: 50%; background: var(--accent); border: none; }
  input[type=range]:disabled { opacity: .4; }
  .transport { display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; }
  .tbtn {
    height: 60px; border-radius: 14px; border: 1px solid var(--line);
    background: var(--card2); color: var(--text); font-size: 1.15rem;
    display: grid; place-items: center; touch-action: manipulation;
    user-select: none; padding: 0;
  }
  .tbtn:active { transform: scale(.96); background: #262b34; }
  .tbtn.play { background: var(--accent); color: #141414; font-size: 1.5rem; }
  .tbtn.small { height: 44px; font-size: .78rem; }
  .tbtn.active { border-color: var(--accent); color: var(--accent2); }
  .subrow { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 8px; }
  .voldisp { text-align: end; color: var(--dim); font-size: .8rem; font-variant-numeric: tabular-nums; }
  .overlay {
    position: fixed; inset: 0; background: rgba(14, 16, 19, .95);
    display: none; place-items: center; text-align: center; padding: 24px; z-index: 10;
  }
  .overlay.show { display: grid; }
  .overlay .box { max-width: 340px; display: grid; gap: 12px; }
  .overlay p { color: var(--dim); margin: 0; }
  .btn {
    background: var(--accent); color: #141414; border: none; border-radius: 12px;
    padding: 14px 18px; font-size: 1rem; font-weight: 600;
  }
  code { background: var(--card2); padding: 2px 6px; border-radius: 6px; }
</style>
</head>
<body>
<div id="app">
  <header>
    <div class="logo">G</div>
    <h1>Golden Music</h1>
    <div class="dot" id="dot" title="connection"></div>
  </header>

  <section class="card now">
    <img id="cover" class="cover" alt="" hidden>
    <div id="ph" class="ph">&#9835;</div>
    <div class="meta">
      <div class="title" id="title" dir="auto">&#8212;</div>
      <div class="artist" id="artist" dir="auto"></div>
      <div class="album" id="album" dir="auto"></div>
      <div class="queue" id="queue"></div>
    </div>
  </section>

  <section class="card">
    <input type="range" id="seek" min="0" max="1000" value="0" step="1" aria-label="Seek">
    <div class="times"><span id="pos">0:00</span><span id="dur">0:00</span></div>
  </section>

  <section class="card">
    <div class="transport">
      <button class="tbtn" id="prev" aria-label="Previous">&#9198;</button>
      <button class="tbtn play" id="play" aria-label="Play or pause">&#9654;</button>
      <button class="tbtn" id="next" aria-label="Next">&#9197;</button>
      <button class="tbtn" id="stopb" aria-label="Stop">&#9209;</button>
      <button class="tbtn" id="shuf" aria-label="Toggle shuffle">shuf</button>
    </div>
    <div class="subrow">
      <button class="tbtn small" id="rep">repeat: off</button>
      <button class="tbtn small" id="refresh">refresh</button>
    </div>
  </section>

  <section class="card">
    <input type="range" id="vol" min="0" max="100" value="0" aria-label="Volume">
    <div class="voldisp" id="voldisp">vol &#8212;</div>
  </section>
</div>

<div class="overlay" id="pairOverlay">
  <div class="box">
    <b>Pairing required</b>
    <p>Open the pairing page on the machine running Golden Music and scan
       the QR code, or append <code>?token=&#8230;</code> to this address.</p>
    <button class="btn" id="retry">Retry</button>
  </div>
</div>

<script>
"use strict";
(function () {
  var KEY = "gm_webremote_token";
  var token = null;

  // Bootstrap: token arrives via ?token=... (pairing) and is remembered.
  var qs = new URLSearchParams(location.search);
  var fromUrl = qs.get("token");
  if (fromUrl) {
    token = fromUrl;
    try { localStorage.setItem(KEY, token); } catch (e) { /* private mode */ }
    qs.delete("token");
    var rest = qs.toString();
    try {
      history.replaceState(null, "", location.pathname + (rest ? "?" + rest : ""));
    } catch (e) { /* old browsers keep the query; harmless */ }
  } else {
    try { token = localStorage.getItem(KEY); } catch (e) { token = null; }
  }

  function $(id) { return document.getElementById(id); }
  var els = {
    title: $("title"), artist: $("artist"), album: $("album"),
    queue: $("queue"), cover: $("cover"), ph: $("ph"),
    seek: $("seek"), pos: $("pos"), dur: $("dur"),
    vol: $("vol"), voldisp: $("voldisp"),
    play: $("play"), prev: $("prev"), next: $("next"),
    stopb: $("stopb"), shuf: $("shuf"), rep: $("rep"), refresh: $("refresh"),
    dot: $("dot"), pair: $("pairOverlay"), retry: $("retry")
  };

  function fmt(ms) {
    ms = Math.max(0, Math.floor(Number(ms) || 0));
    var s = Math.floor(ms / 1000);
    var m = Math.floor(s / 60);
    var h = Math.floor(m / 60);
    function p(n) { return (n < 10 ? "0" : "") + n; }
    return h ? (h + ":" + p(m % 60) + ":" + p(s % 60)) : (m + ":" + p(s % 60));
  }

  function showPair() { els.pair.classList.add("show"); }

  function api(path, method, bodyObj) {
    var opts = { method: method || "GET", headers: { "X-GM-Token": token || "" } };
    if (bodyObj !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(bodyObj);
    }
    return fetch(path, opts).then(function (r) {
      if (r.status === 401 || r.status === 403) { showPair(); }
      return r;
    });
  }

  function control(action, value) {
    var body = (value === undefined) ? { action: action }
                                      : { action: action, value: value };
    return api("/api/control", "POST", body).then(function (r) { return r.json(); })
      .then(function (j) {
        if (j && j.status) { render(j.status); } else { refresh(); }
      })
      .catch(function () { /* offline; next poll retries */ });
  }

  var seeking = false;
  var coverKey = "";
  var coverUrl = null;

  function refreshCover(s) {
    var key = (s.title || "") + "|" + (s.artist || "") + "|" + (s.album || "");
    if (key === coverKey) { return; }
    coverKey = key;
    api("/api/cover").then(function (r) {
      if (!r.ok) { return null; }
      return r.blob();
    }).then(function (blob) {
      if (!blob) {
        els.cover.hidden = true; els.ph.hidden = false;
        if (coverUrl) { URL.revokeObjectURL(coverUrl); coverUrl = null; }
        return;
      }
      if (coverUrl) { URL.revokeObjectURL(coverUrl); }
      coverUrl = URL.createObjectURL(blob);
      els.cover.src = coverUrl;
      els.cover.hidden = false; els.ph.hidden = true;
    }).catch(function () {
      els.cover.hidden = true; els.ph.hidden = false;
    });
  }

  function render(s) {
    els.title.textContent = s.title || "\u2014";
    els.artist.textContent = s.artist || "";
    els.album.textContent = s.album || "";
    els.queue.textContent = s.queue_count > 0
      ? ("queue " + (s.queue_position + 1) + " / " + s.queue_count) : "";
    var dur = Math.max(0, s.duration_ms | 0);
    els.seek.disabled = !s.can_seek || dur <= 0;
    els.seek.max = String(dur);
    if (!seeking) { els.seek.value = String(Math.min(Math.max(0, s.position_ms | 0), dur)); }
    els.pos.textContent = fmt(s.position_ms);
    els.dur.textContent = fmt(dur);
    if (document.activeElement !== els.vol) { els.vol.value = String(s.volume | 0); }
    els.voldisp.textContent = "vol " + (s.volume | 0);
    els.play.textContent = s.state === "playing" ? "\u23F8" : "\u25B6";
    els.shuf.classList.toggle("active", !!s.shuffle);
    els.rep.classList.toggle("active", s.repeat !== "off");
    els.rep.textContent = "repeat: " + (s.repeat || "off");
    els.dot.classList.add("on");
    refreshCover(s);
  }

  function refresh() {
    if (!token) { showPair(); return Promise.resolve(false); }
    return api("/api/status").then(function (r) {
      if (!r.ok) { throw new Error(String(r.status)); }
      return r.json();
    }).then(function (s) {
      render(s);
      return s.state === "playing";
    }).catch(function () {
      els.dot.classList.remove("on");
      return false;
    });
  }

  // Auto-refresh: 2 s while playing, 5 s when idle.
  function loop() {
    refresh().then(function (playing) {
      setTimeout(loop, playing ? 2000 : 5000);
    }, function () {
      setTimeout(loop, 5000);
    });
  }

  function bind(id, fn) { $(id).addEventListener("click", fn); }
  bind("prev", function () { control("prev"); });
  bind("play", function () { control("play_pause"); });
  bind("next", function () { control("next"); });
  bind("stopb", function () { control("stop"); });
  bind("shuf", function () { control("toggle_shuffle"); });
  bind("rep", function () { control("cycle_repeat"); });
  bind("refresh", function () { refresh(); });
  bind("retry", function () {
    els.pair.classList.remove("show");
    try { token = localStorage.getItem(KEY); } catch (e) { token = token; }
    refresh();
  });

  els.seek.addEventListener("input", function () {
    seeking = true;
    els.pos.textContent = fmt(els.seek.value);
  });
  els.seek.addEventListener("change", function () {
    seeking = false;
    control("seek_ms", Math.round(Number(els.seek.value) || 0));
  });
  els.vol.addEventListener("input", function () {
    els.voldisp.textContent = "vol " + els.vol.value;
  });
  els.vol.addEventListener("change", function () {
    control("set_volume", Math.round(Number(els.vol.value) || 0));
  });

  loop();
})();
</script>
</body>
</html>
"""

_PAIR_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>Pair a device &#8212; Golden Music</title>
<style>
  body {
    margin: 0; background: #0e1013; color: #e8e6e1;
    font: 16px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    display: flex; justify-content: center; padding: 24px 12px;
  }
  .card {
    background: #171a20; border: 1px solid #262c36; border-radius: 16px;
    padding: 20px; max-width: 420px; width: 100%; text-align: center;
  }
  h1 { font-size: 1.2rem; margin: 0 0 8px; }
  p { color: #9aa0a8; }
  .qr { width: 240px; height: 240px; background: #fff; border-radius: 10px;
        padding: 8px; margin: 6px auto; display: block; }
  .noqr { color: #f0c05a; background: #21262f; border-radius: 10px;
          padding: 14px; margin: 6px 0; }
  .lbl { text-align: start; margin: 14px 0 4px; font-size: .8rem;
         text-transform: uppercase; letter-spacing: .5px; }
  code {
    display: block; background: #21262f; border-radius: 8px;
    padding: 10px; text-align: start; word-break: break-all;
    user-select: all; font-size: .95rem;
  }
  .btn {
    background: #d4a437; color: #141414; border: none; border-radius: 12px;
    padding: 14px 18px; font-size: 1rem; font-weight: 600; margin-top: 14px;
    width: 100%;
  }
  .note { font-size: .8rem; }
</style>
</head>
<body>
<main class="card">
  <h1>Pair a device</h1>
  <p>Scan the code with your phone while it is on the same network as the
     player, then confirm the page loads.</p>
  @@QR@@
  <p class="lbl">Pairing address</p>
  <code id="url">@@URL@@</code>
  <p class="lbl">Token</p>
  <code id="tok">@@TOKEN@@</code>
  <button class="btn" id="copy">Copy address</button>
  <p class="note">Anyone holding this token can control playback. Rotate it
     from the player if it leaks.</p>
</main>
<script>
"use strict";
(function () {
  document.getElementById("copy").addEventListener("click", function () {
    var text = document.getElementById("url").textContent;
    function fallback() {
      var rng = document.createRange();
      rng.selectNodeContents(document.getElementById("url"));
      var sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(rng);
      try { document.execCommand("copy"); } catch (e) { /* selected for manual copy */ }
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () {
        var b = document.getElementById("copy");
        b.textContent = "Copied";
        setTimeout(function () { b.textContent = "Copy address"; }, 1500);
      }, fallback);
    } else {
      fallback();
    }
  });
})();
</script>
</body>
</html>
"""


def _pair_page_html(pairing_url: str, token: str, qr_png: bytes | None) -> str:
    """Build the /api/pair page (QR image when available, text fallback)."""
    if qr_png:
        b64 = base64.b64encode(qr_png).decode("ascii")
        qr_html = (
            '<img class="qr" src="data:image/png;base64,%s" '
            'alt="QR code with the pairing address">' % b64
        )
    else:
        qr_html = (
            '<div class="noqr">QR module unavailable &#8212; '
            'type the address below by hand.</div>'
        )
    return (_PAIR_PAGE_TEMPLATE
            .replace("@@QR@@", qr_html)
            .replace("@@URL@@", html_mod.escape(pairing_url, quote=True))
            .replace("@@TOKEN@@", html_mod.escape(token, quote=True)))


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------
class _RemoteHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer carrying a back-reference to its owner."""

    daemon_threads = True

    def __init__(self, address, handler_cls: type[BaseHTTPRequestHandler],
                 owner: "WebRemoteServer") -> None:
        self.owner = owner
        super().__init__(address, handler_cls)

    def handle_error(self, request: Any, client_address: Any) -> None:
        # Default prints a full traceback to stderr; keep it quiet and token-free.
        try:
            ip = client_address[0] if isinstance(client_address, tuple) else "?"
        except Exception:
            ip = "?"
        log.debug("client %s connection error", ip)


class _RequestHandler(BaseHTTPRequestHandler):
    """Exact-match router for the remote API. Never raises out of a request."""

    server_version = "GoldenMusic"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 60  # drop dead keep-alive connections

    # -- plumbing ----------------------------------------------------------
    def version_string(self) -> str:  # pragma: no cover - cosmetic
        return self.server_version

    def _log_path(self) -> str:
        """Clean request path for logging (query string stripped, always)."""
        try:
            sp = getattr(self, "_safe_path", None)
            if sp:
                return sp
            return urllib.parse.urlsplit(self.path).path or "/"
        except Exception:
            return "/"

    def log_request(self, code: Any = "-", size: Any = "-") -> None:
        """Log method + path + status. Query strings are NEVER logged."""
        try:
            log.info("%s %s -> %s (client %s)",
                     self.command, self._log_path(), code,
                     self.client_address[0])
        except Exception:
            pass

    def log_error(self, format: str, *args: Any) -> None:  # noqa: A002
        """Stdlib error hook (send_error/parse failures).

        The default formats the raw request line — which includes the
        query string and therefore possibly the token — so it is replaced
        with a sanitized line that only carries method + clean path.
        """
        try:
            log.warning("%s %s -> request error (client %s)",
                        self.command, self._log_path(),
                        self.client_address[0])
        except Exception:
            pass

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Last-resort hook: never forwards raw request lines to the log."""
        try:
            log.info("%s %s (client %s)",
                     self.command, self._log_path(),
                     self.client_address[0])
        except Exception:
            pass

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        """Route a request. Any unexpected failure becomes a 500 JSON."""
        self._safe_path = "/"
        try:
            split = urllib.parse.urlsplit(self.path)
            self._safe_path = split.path or "/"
            route = _ROUTES.get((method, self._safe_path))
            if route is None:
                self._send_json(404, {"error": "not found"})
                return
            if not self._origin_ok():
                self._send_json(403, {"error": "cross-origin request denied"})
                return
            query = urllib.parse.parse_qs(split.query)
            route(self, query)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            log.exception("error serving %s %s",
                          method, getattr(self, "_safe_path", "/"))
            try:
                self._send_json(500, {"error": "internal error"})
            except Exception:
                self.close_connection = True

    def _origin_ok(self) -> bool:
        """Same-origin policy: refuse browser requests with foreign Origin."""
        try:
            origin = (self.headers.get("Origin") or "").strip()
            if not origin:
                return True  # non-browser client
            origin_host = urllib.parse.urlsplit(origin).netloc
            host = (self.headers.get("Host") or "").strip()
            if not origin_host or not host:
                return True
            return origin_host == host
        except Exception:
            return True

    def _extract_token(self, query: dict[str, list[str]]) -> str | None:
        """Token from ?token=..., X-GM-Token or Authorization: Bearer."""
        vals = query.get("token")
        if vals and isinstance(vals[0], str):
            return vals[0]
        header = self.headers.get("X-GM-Token")
        if header:
            return header.strip()
        auth = (self.headers.get("Authorization") or "").strip()
        if auth[:7].lower() == "bearer ":
            return auth[7:].strip()
        return None

    def _authorized(self, query: dict[str, list[str]]) -> bool:
        """Rate-limited token check; sends the 401/429 response itself."""
        owner: WebRemoteServer = self.server.owner  # type: ignore[attr-defined]
        ip = self.client_address[0]
        if owner._is_rate_limited(ip):
            self._send_json(429, {"error": "too many failed attempts; retry later"})
            return False
        token = self._extract_token(query)
        if token and owner.auth.verify(token):
            owner._note_auth_success(ip)
            return True
        owner._note_auth_failure(ip)
        self._send_json(401, {"error": "unauthorized"})
        return False

    def _send_bytes(self, code: int, body: bytes, ctype: str,
                    no_store: bool = True) -> None:
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            if no_store:
                self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.close_connection = True

    def _send_json(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(code, body, "application/json; charset=utf-8")

    def _read_body(self) -> bytes | None:
        """Read a capped JSON request body; responds 400 and returns None on error."""
        te = (self.headers.get("Transfer-Encoding") or "").lower()
        if "chunked" in te:
            self.close_connection = True
            self._send_json(400, {"error": "chunked bodies not supported"})
            return None
        cl = self.headers.get("Content-Length")
        if cl is None:
            self._send_json(400, {"error": "Content-Length required"})
            return None
        try:
            n = int(cl, 10)
        except ValueError:
            self._send_json(400, {"error": "invalid Content-Length"})
            return None
        if n < 0 or n > MAX_BODY_BYTES:
            self.close_connection = True
            self._send_json(
                400, {"error": "body too large (max %d bytes)" % MAX_BODY_BYTES})
            return None
        try:
            data = self.rfile.read(n) if n else b""
        except (OSError, ValueError):
            self.close_connection = True
            self._send_json(400, {"error": "failed reading body"})
            return None
        if len(data) != n:
            self.close_connection = True
            self._send_json(400, {"error": "truncated body"})
            return None
        return data

    # -- endpoints ---------------------------------------------------------
    def _serve_health(self, query: dict[str, list[str]]) -> None:
        """GET /health — public, minimal info leak."""
        self._send_json(200, {"ok": True, "app": "GoldenMusic"})

    def _serve_index(self, query: dict[str, list[str]]) -> None:
        """GET / — the single-page UI (contains no secrets)."""
        self._send_bytes(200, _INDEX_HTML.encode("utf-8"),
                         "text/html; charset=utf-8", no_store=False)

    def _serve_pair(self, query: dict[str, list[str]]) -> None:
        """GET /api/pair — QR + text pairing helper.

        Intentionally unauthenticated (bootstrap page: it is what shows the
        token).  Open it on the machine running the player; rotate the
        token afterwards if the address might have been observed.
        """
        owner: WebRemoteServer = self.server.owner  # type: ignore[attr-defined]
        display = owner.pairing_host(self.headers.get("Host"))
        token = owner.auth.token
        url = "http://%s/?token=%s" % (display, token)
        # Module-level lookup on purpose so tests can monkeypatch qrcode_png.
        qr = qrcode_png(url)
        page = _pair_page_html(url, token, qr)
        self._send_bytes(200, page.encode("utf-8"),
                         "text/html; charset=utf-8", no_store=False)

    def _serve_status(self, query: dict[str, list[str]]) -> None:
        """GET /api/status — sanitized player snapshot (auth)."""
        if not self._authorized(query):
            return
        owner: WebRemoteServer = self.server.owner  # type: ignore[attr-defined]
        try:
            raw = owner.bridge.get_status()
        except Exception:
            log.exception("bridge get_status failed")
            self._send_json(500, {"error": "player status unavailable"})
            return
        self._send_json(200, sanitize_status(raw))

    def _serve_cover(self, query: dict[str, list[str]]) -> None:
        """GET /api/cover — PNG bytes or 404 (auth)."""
        if not self._authorized(query):
            return
        owner: WebRemoteServer = self.server.owner  # type: ignore[attr-defined]
        try:
            png = owner.bridge.get_cover_png()
        except Exception:
            log.exception("bridge get_cover_png failed")
            self._send_json(500, {"error": "cover unavailable"})
            return
        if not png:
            self._send_json(404, {"error": "no cover"})
            return
        self._send_bytes(200, png, "image/png")

    def _serve_control(self, query: dict[str, list[str]]) -> None:
        """POST /api/control — {action, value} with whitelist + range checks."""
        if not self._authorized(query):
            return
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype not in ("", "application/json"):
            self._send_json(400, {"error": "Content-Type must be application/json"})
            return
        raw = self._read_body()
        if raw is None:
            return
        try:
            payload = json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            self._send_json(400, {"error": "invalid JSON body"})
            return
        if not isinstance(payload, dict):
            self._send_json(400, {"error": "JSON object expected"})
            return
        action = payload.get("action")
        value = payload.get("value")
        if not isinstance(action, str) or action not in CONTROL_ACTIONS:
            self._send_json(400, {"error": "unknown action"})
            return
        if action in _SIMPLE_ACTIONS:
            if value is not None:
                self._send_json(400, {"error": "action '%s' takes no value" % action})
                return
        elif action == "set_volume":
            if not _is_int(value) or not 0 <= value <= 100:
                self._send_json(400, {"error": "volume must be an integer 0-100"})
                return
        elif action == "seek_ms":
            if not _is_int(value) or value < 0:
                self._send_json(400,
                                {"error": "seek value must be a non-negative integer"})
                return
            if not self._seek_allowed(value):
                return  # response already sent
        owner: WebRemoteServer = self.server.owner  # type: ignore[attr-defined]
        bridge = owner.bridge
        try:
            if action in _SIMPLE_ACTIONS:
                ok = bool(getattr(bridge, action)())
            else:
                ok = bool(getattr(bridge, action)(value))
        except Exception:
            log.exception("bridge action '%s' failed", action)
            self._send_json(500, {"error": "player action '%s' failed" % action})
            return
        response: dict[str, Any] = {"ok": ok, "action": action}
        try:
            response["status"] = sanitize_status(bridge.get_status())
        except Exception:
            pass  # status is optional sugar; the action result still stands
        self._send_json(200, response)

    def _seek_allowed(self, value: int) -> bool:
        """Validate seek range against the live status; sends 400 itself."""
        owner: WebRemoteServer = self.server.owner  # type: ignore[attr-defined]
        try:
            status = sanitize_status(owner.bridge.get_status())
        except Exception:
            log.exception("bridge get_status failed while validating seek")
            self._send_json(500, {"error": "player status unavailable"})
            return False
        if not status["can_seek"]:
            self._send_json(400, {"error": "seeking not supported right now"})
            return False
        if value > status["duration_ms"]:
            self._send_json(400, {"error": "seek position beyond track end"})
            return False
        return True


def _is_int(value: Any) -> bool:
    """True for real ints (bool excluded — it is an int subclass)."""
    return isinstance(value, int) and not isinstance(value, bool)


_ROUTES: dict[tuple[str, str], Callable[[_RequestHandler, dict], None]] = {
    ("GET", "/"): _RequestHandler._serve_index,
    ("GET", "/health"): _RequestHandler._serve_health,
    ("GET", "/api/pair"): _RequestHandler._serve_pair,
    ("GET", "/api/status"): _RequestHandler._serve_status,
    ("GET", "/api/cover"): _RequestHandler._serve_cover,
    ("POST", "/api/control"): _RequestHandler._serve_control,
}


# ---------------------------------------------------------------------------
# The server
# ---------------------------------------------------------------------------
_BRIDGE_METHODS = (
    "get_status", "play_pause", "next", "prev", "stop", "seek_ms",
    "set_volume", "toggle_shuffle", "cycle_repeat", "get_cover_png",
)


class WebRemoteServer(threading.Thread):
    """LAN web remote: start/stop lifecycle around a ThreadingHTTPServer.

    Usage::

        server = WebRemoteServer(bridge, host="0.0.0.0", port=8642)
        host, port = server.start()
        print(server.token)          # pairing token (show in UI)
        server.rotate_token()        # on demand
        server.stop()                # idempotent

    Call ``stop()`` from the app (GUI) thread — never from inside a bridge
    callback (those run on HTTP worker threads and ``shutdown()`` would
    deadlock waiting for itself).
    """

    def __init__(self, bridge: PlayerBridge, host: str = "127.0.0.1",
                 port: int = 0, token: str | None = None,
                 auth: TokenAuth | None = None) -> None:
        missing = [m for m in _BRIDGE_METHODS if not callable(getattr(bridge, m, None))]
        if missing:
            raise TypeError(
                "bridge is missing PlayerBridge methods: %s" % ", ".join(missing))
        super().__init__(name="WebRemoteServer", daemon=True)
        self.bridge = bridge
        self._host = str(host)
        self._port = int(port)
        self.auth = auth if auth is not None else TokenAuth(token)
        self.bound_host = ""
        self.bound_port = 0
        self._httpd: _RemoteHTTPServer | None = None
        self._bind_done = False
        self._stop_done = False
        self._lifecycle_lock = threading.Lock()
        # Per-IP failed-auth tracking (in-memory, pruned by time).
        self._rl_lock = threading.Lock()
        self._rl_failures: dict[str, deque[float]] = {}
        self._rl_blocked: dict[str, float] = {}

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> tuple[str, int]:
        """Bind, spawn the serve thread and return ``(host, port)``.

        ``port=0`` yields an ephemeral port.  Raises ``OSError`` when the
        address cannot be bound (the caller decides how to surface that).
        """
        with self._lifecycle_lock:
            if self._bind_done:
                raise RuntimeError("WebRemoteServer.start() called twice")
            self._bind_done = True
        try:
            httpd = _RemoteHTTPServer((self._host, self._port),
                                      _RequestHandler, self)
        except OSError:
            with self._lifecycle_lock:
                self._bind_done = False
            log.exception("web remote failed to bind %s:%s", self._host, self._port)
            raise
        with self._lifecycle_lock:
            # A stray stop() before start() must not latch the flag.
            self._stop_done = False
        self._httpd = httpd
        self.bound_host, self.bound_port = httpd.server_address[:2]
        super().start()
        log.info("web remote listening on %s:%s", self.bound_host, self.bound_port)
        return self.bound_host, self.bound_port

    def run(self) -> None:
        """Serve forever until ``stop()`` (internal — use start/stop)."""
        httpd = self._httpd
        if httpd is None:
            return
        try:
            httpd.serve_forever(poll_interval=0.25)
        except Exception:  # pragma: no cover - loop must never crash silently
            log.exception("web remote serve loop crashed")
        finally:
            try:
                httpd.server_close()
            except Exception:
                log.debug("server_close failed", exc_info=True)

    def stop(self, timeout: float = 5.0) -> bool:
        """Stop the server. Idempotent, safe before ``start()``.

        Returns True when actual shutdown work was done, False when there
        was nothing to stop (never started, or already stopped).  Joins the
        serve thread for up to *timeout* seconds.
        """
        with self._lifecycle_lock:
            if self._stop_done:
                return False
            self._stop_done = True
        if not self._bind_done:
            # Never started — nothing to shut down.
            return False
        httpd = self._httpd
        if httpd is not None and self._bind_done:
            try:
                httpd.shutdown()
            except Exception:
                log.debug("shutdown failed", exc_info=True)
            try:
                httpd.server_close()
            except Exception:
                log.debug("server_close failed", exc_info=True)
        if self.is_alive() and threading.current_thread() is not self:
            self.join(timeout)
        log.info("web remote stopped")
        return True

    # -- conveniences --------------------------------------------------------
    @property
    def token(self) -> str:
        """Current pairing token (secret — display it, never log it)."""
        return self.auth.token

    def rotate_token(self) -> str:
        """Rotate the pairing token; open sessions must re-pair."""
        return self.auth.rotate()

    def pairing_host(self, host_header: str | None) -> str:
        """Best host string for building pairing URLs (no scheme)."""
        host = (host_header or "").strip()
        if host and not host.startswith(("127.", "localhost", "[::1]")):
            return host
        if self._host and self._host not in ("", "0.0.0.0", "::"):  # nosec B104
            return "%s:%d" % (self._host, self.bound_port or self._port)
        return "%s:%d" % (lan_ip(), self.bound_port or self._port)

    # -- rate limiting (failed auth) -----------------------------------------
    def _is_rate_limited(self, ip: str) -> bool:
        now = time.monotonic()
        with self._rl_lock:
            until = self._rl_blocked.get(ip, 0.0)
            if now < until:
                return True
            if ip in self._rl_blocked:
                del self._rl_blocked[ip]
            return False

    def _note_auth_failure(self, ip: str) -> None:
        now = time.monotonic()
        with self._rl_lock:
            dq = self._rl_failures.setdefault(ip, deque())
            dq.append(now)
            while dq and dq[0] <= now - RATE_WINDOW_S:
                dq.popleft()
            if len(dq) >= RATE_MAX_FAILURES:
                self._rl_blocked[ip] = now + RATE_BLOCK_S
                dq.clear()
                log.warning("rate limiting %s after %d consecutive auth failures",
                            ip, RATE_MAX_FAILURES)

    def _note_auth_success(self, ip: str) -> None:
        with self._rl_lock:
            self._rl_failures.pop(ip, None)
