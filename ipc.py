"""
Golden Music — single-instance IPC.

A second launch forwards its file arguments (or a bare "show") to the
already-running instance over a QLocalServer socket, then exits. The
server name is derived from the current OS user so two different
accounts on one machine never talk to each other's player.

Wire protocol — one frame per connection:

    4-byte little-endian payload length + UTF-8 JSON payload

    {"cmd": "open", "paths": ["C:\\...\\song.mp3", ...]}
    {"cmd": "show"}

The running instance replies with {"ok": true} / {"ok": false}.

Receiving side is strictly validating: frame size cap, JSON schema,
absolute paths only, audio-extension allow-list, existence check. The
channel can only ever enqueue media for playback — nothing is executed
and no shell is involved.
"""
import getpass
import hashlib
import json
import logging
import os

from PyQt6.QtCore import QByteArray, QTimer
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

from config import AUDIO_EXTS

log = logging.getLogger("app.ipc")

MAX_FRAME_BYTES = 64 * 1024        # protocol cap — a valid message is tiny
MAX_PATHS_PER_MESSAGE = 100        # sanity cap; "Open with" sends 1..few
HANDSHAKE_TIMEOUT_MS = 2000        # second instance waits ≤ 2 s for an ack


def server_name() -> str:
    """Per-user QLocalServer name (stable, filesystem-safe)."""
    try:
        user = getpass.getuser() or "default"
    except Exception:
        user = "default"
    digest = hashlib.sha256(user.encode("utf-8", "replace")).hexdigest()[:12]
    return f"GoldenMusic-{digest}"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def sanitize_paths(paths) -> list:
    """Filter a candidate path list down to playable audio files.

    Absolute paths only, must exist, extension must be in AUDIO_EXTS,
    bounded count. Shared by the IPC server and the argv parser so both
    entry points enforce exactly the same rules.
    """
    out = []
    if not isinstance(paths, list):
        return out
    for p in paths[:MAX_PATHS_PER_MESSAGE]:
        if not isinstance(p, str) or not p:
            continue
        p = os.path.normpath(p)
        if not os.path.isabs(p) or not os.path.isfile(p):
            continue
        if os.path.splitext(p)[1].lower() not in AUDIO_EXTS:
            continue
        if p not in out:
            out.append(p)
    return out


def validate_payload(raw: bytes):
    """Parse + validate one IPC frame.

    Returns ("open", [paths]) / ("show", None) or None when anything is
    off (malformed JSON, unknown cmd, oversize, bad types). Never raises.
    """
    if not raw or len(raw) > MAX_FRAME_BYTES:
        return None
    try:
        obj = json.loads(raw.decode("utf-8", "replace"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(obj, dict):
        return None
    cmd = obj.get("cmd")
    if cmd == "show":
        return ("show", None)
    if cmd == "open":
        paths = sanitize_paths(obj.get("paths"))
        if not paths:
            return None
        return ("open", paths)
    return None


# ---------------------------------------------------------------------------
# Client (second instance)
# ---------------------------------------------------------------------------
def send_message(payload: dict, timeout_ms: int = HANDSHAKE_TIMEOUT_MS) -> bool:
    """Connect to the running instance, send one frame, await its ack.

    Returns False on any failure (no server, timeout, bad ack) — the
    caller then falls back to a normal start.
    """
    sock = QLocalSocket()
    sock.connectToServer(server_name())
    if not sock.waitForConnected(timeout_ms):
        log.info(f"IPC: no running instance ({sock.errorString()})")
        return False
    try:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if len(raw) > MAX_FRAME_BYTES:
            log.warning("IPC: payload too large — refusing to send")
            return False
        sock.write(QByteArray(len(raw).to_bytes(4, "little") + raw))
        if not sock.waitForBytesWritten(timeout_ms):
            log.warning("IPC: write timed out")
            return False
        # The server acks and immediately disconnects — either event can
        # win the race, so wait for readyRead but ALWAYS drain whatever
        # arrived (data written before the disconnect stays in the buffer).
        sock.waitForReadyRead(timeout_ms)
        ack = bytes(sock.readAll())
        if not ack:
            sock.waitForDisconnected(500)
            ack = bytes(sock.readAll())
        try:
            parsed = json.loads(ack.decode("utf-8", "replace"))
            ok = isinstance(parsed, dict) and parsed.get("ok") is True
        except ValueError:
            ok = False
        if not ok:
            log.warning("IPC: unexpected ack")
        return ok
    finally:
        try:
            sock.disconnectFromServer()
            if sock.state() != QLocalSocket.LocalSocketState.UnconnectedState:
                sock.waitForDisconnected(500)
        except Exception:
            pass


def forward_launch(files: list) -> bool:
    """Second-launch entry point: forward argv files (or a bare "show")
    to the running instance. Returns True when the instance acked."""
    if files:
        return send_message({"cmd": "open", "paths": files})
    return send_message({"cmd": "show"})


# ---------------------------------------------------------------------------
# Server (first instance)
# ---------------------------------------------------------------------------
def start_server(on_open, on_show):
    """Start the per-user IPC server.

    on_open(paths) and on_show() are callables invoked with VALIDATED
    input only. Returns the QLocalServer (keep a reference for the
    process lifetime) or None when the socket could not be created —
    the app keeps working without IPC either way.
    """
    name = server_name()
    # Stale socket from a crashed run — QLocalServer.removeServer is a
    # no-op on Windows named pipes and cleans the file on POSIX.
    QLocalServer.removeServer(name)
    srv = QLocalServer()
    if not srv.listen(name):
        log.warning(f"IPC: listen failed on '{name}': {srv.errorString()}")
        return None
    srv.newConnection.connect(lambda: _accept(srv, on_open, on_show))
    log.info(f"IPC: listening on '{name}'")
    return srv


def _accept(srv: QLocalServer, on_open, on_show):
    sock = srv.nextPendingConnection()
    if sock is None:
        return
    state = {"buf": bytearray()}
    sock.readyRead.connect(lambda: _drain(sock, state, on_open, on_show))

    # A connection that never delivers a full frame is closed after the
    # handshake window so nothing lingers.
    def _timeout():
        try:
            if sock.state() == QLocalSocket.LocalSocketState.ConnectedState:
                log.warning("IPC: connection timed out mid-frame — closing")
                sock.disconnectFromServer()
        except Exception:
            pass

    QTimer.singleShot(HANDSHAKE_TIMEOUT_MS, _timeout)


def _drain(sock: QLocalSocket, state: dict, on_open, on_show):
    """Buffered frame reader: 4-byte LE length + JSON, then ack + close."""
    try:
        state["buf"] += bytes(sock.readAll())
        buf = state["buf"]
        if len(buf) < 4:
            return
        frame_len = int.from_bytes(buf[:4], "little")
        if frame_len > MAX_FRAME_BYTES:
            log.warning("IPC: frame exceeds cap — dropping connection")
            _ack_and_close(sock, False)
            return
        if len(buf) < 4 + frame_len:
            return  # wait for the rest of the frame
        payload = bytes(buf[4:4 + frame_len])
        parsed = validate_payload(payload)
        if parsed is None:
            log.warning("IPC: rejected malformed/invalid payload")
            _ack_and_close(sock, False)
            return
        cmd, paths = parsed
        if cmd == "open":
            on_open(paths)
        elif cmd == "show":
            on_show()
        _ack_and_close(sock, True)
    except Exception as e:
        log.warning(f"IPC: handler error: {e}")
        _ack_and_close(sock, False)


def _ack_and_close(sock: QLocalSocket, ok: bool):
    try:
        sock.write(QByteArray(json.dumps({"ok": ok}).encode("utf-8")))
        sock.flush()
        sock.disconnectFromServer()
    except Exception:
        pass
