"""
Headless tests for the 3.0 remote-control modules: gm/webremote.py and
gm/telegrambot.py.

Run:  python3 scripts/test_remote.py

Rules honored: no Qt, no pytest, stdlib only, no internet access
(localhost sockets ARE used for the web server; the Telegram tests fake
the network layer via _api_call), plain [OK]/[FAIL] output with a
SUMMARY and a non-zero exit code on failure.
"""
import http.client
import json
import logging
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gm.webremote as webremote
from gm.webremote import (
    TOKEN_RE, PlayerBridge, TokenAuth, WebRemoteServer, lan_ip,
    qrcode_png, sanitize_status,
)
from gm.telegrambot import TelegramBridge, TelegramRemote, validate_token_format

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


def section(title):
    print(f"\n=== {title} ===")


# ---------------------------------------------------------------------------
# HTTP helpers (localhost only)
# ---------------------------------------------------------------------------
def request(url, method="GET", data=None, headers=None, timeout=10):
    """Return (status, headers, body_bytes); never raises for 4xx/5xx."""
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.headers, err.read()


def post_json(url, obj, token=None, headers=None, timeout=10):
    hdrs = {"Content-Type": "application/json"}
    if token is not None:
        hdrs["X-GM-Token"] = token
    if headers:
        hdrs.update(headers)
    return request(url, method="POST", data=json.dumps(obj).encode("utf-8"),
                   headers=hdrs, timeout=timeout)


def raw_post(host, port, path, body=b"", content_type="application/json",
             content_length=None, timeout=10):
    """Hand-rolled POST for protocol-level tests.

    ``content_length``: None = len(body), -1 = omit the header entirely
    (for the missing-Content-Length case).
    """
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.putrequest("POST", path)
        if content_type:
            conn.putheader("Content-Type", content_type)
        if content_length == -1:
            pass  # deliberately omit Content-Length
        else:
            if content_length is None:
                content_length = len(body)
            conn.putheader("Content-Length", str(content_length))
        conn.endheaders()
        if body:
            conn.send(body)
        resp = conn.getresponse()
        return resp.status, resp.headers, resp.read()
    finally:
        conn.close()


def json_body(body):
    try:
        return json.loads(body.decode("utf-8", "replace"))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Fakes (web)
# ---------------------------------------------------------------------------
PNG_1x1 = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00"
           b"\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc8\x89\x00\x00\x00\n"
           b"IDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00"
           b"\x00\x00IEND\xaeB`\x82")


class FakeBridge(PlayerBridge):
    """In-memory PlayerBridge recording every call."""

    def __init__(self, cover=None):
        self.calls = []
        self.cover = cover
        self._status = {
            "state": "playing", "title": "Golden Test Song",
            "artist": "Test Artist", "album": "Test Album",
            "position_ms": 42000, "duration_ms": 200000, "volume": 55,
            "shuffle": True, "repeat": "all", "queue_count": 12,
            "queue_position": 3, "can_seek": True,
        }

    def get_status(self):
        self.calls.append(("get_status",))
        return dict(self._status)

    def play_pause(self):
        self.calls.append(("play_pause",))
        return True

    def next(self):
        self.calls.append(("next",))
        return True

    def prev(self):
        self.calls.append(("prev",))
        return True

    def stop(self):
        self.calls.append(("stop",))
        return True

    def seek_ms(self, ms):
        self.calls.append(("seek_ms", int(ms)))
        return True

    def set_volume(self, volume):
        self.calls.append(("set_volume", int(volume)))
        return True

    def toggle_shuffle(self):
        self.calls.append(("toggle_shuffle",))
        return True

    def cycle_repeat(self):
        self.calls.append(("cycle_repeat",))
        return True

    def get_cover_png(self):
        self.calls.append(("get_cover_png",))
        return self.cover


class ExplodingBridge(FakeBridge):
    """Bridge that raises — the server must answer 500 JSON, never crash."""

    def get_status(self):
        raise RuntimeError("bridge exploded: status")

    def next(self):
        raise RuntimeError("bridge exploded: next")


# ---------------------------------------------------------------------------
# Fakes (telegram)
# ---------------------------------------------------------------------------
VALID_TOKEN = "123456789:" + "AAE" + "x" * 37  # matches the token regex

TG_STATUS = {
    "state": "playing", "title": "Golden Test Song", "artist": "Test Artist",
    "album": "Test Album", "position_ms": 42000, "duration_ms": 200000,
    "volume": 55, "shuffle": True, "repeat": "all", "queue_count": 12,
    "queue_position": 3, "can_seek": True,
}

TG_SEARCH_RESULTS = [
    {"title": "Gold Song", "artist": "Aurum"},
    {"title": "Silver Song", "artist": "Argent"},
    {"title": "Bronze Song", "artist": "Cuprum"},
]


class FakeTelegramBridge(TelegramBridge):
    """In-memory TelegramBridge recording every call."""

    def __init__(self):
        self.calls = []
        self.queue = [{"title": f"Queue Song {i}", "artist": "Queue Artist"}
                      for i in range(10)]

    def get_status(self):
        self.calls.append(("get_status",))
        return dict(TG_STATUS)

    def play_pause(self):
        self.calls.append(("play_pause",))
        return True

    def next(self):
        self.calls.append(("next",))
        return True

    def prev(self):
        self.calls.append(("prev",))
        return True

    def stop(self):
        self.calls.append(("stop",))
        return True

    def seek_ms(self, ms):
        self.calls.append(("seek_ms", int(ms)))
        return True

    def set_volume(self, volume):
        self.calls.append(("set_volume", int(volume)))
        return True

    def toggle_shuffle(self):
        self.calls.append(("toggle_shuffle",))
        return True

    def cycle_repeat(self):
        self.calls.append(("cycle_repeat",))
        return True

    def get_cover_png(self):
        return PNG_1x1

    def search(self, query, limit=8):
        self.calls.append(("search", query, limit))
        return TG_SEARCH_RESULTS[:limit]

    def play_index(self, index):
        self.calls.append(("play_index", int(index)))
        return True

    def queue_items(self, limit=10):
        self.calls.append(("queue_items", limit))
        return self.queue[:limit]


class FakeRemote(TelegramRemote):
    """TelegramRemote with the network layer faked (no real API calls)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.api_calls = []
        self.responses = []  # queued getUpdates results

    def _api_call(self, method, params):
        self.api_calls.append((method, dict(params)))
        if method == "getUpdates":
            if self.responses:
                return {"ok": True, "result": self.responses.pop(0)}
            return {"ok": True, "result": []}
        return {"ok": True, "result": {"message_id": 1}}


def mk_update(user_id, chat_id, text, update_id=1):
    return {
        "update_id": update_id,
        "message": {
            "message_id": 100 + update_id,
            "date": 0,
            "chat": {"id": chat_id},
            "from": {"id": user_id, "first_name": "Tester"},
            "text": text,
        },
    }


def sent_texts(remote):
    """All sendMessage texts, in order."""
    return [params.get("text", "")
            for method, params in remote.api_calls
            if method == "sendMessage"]


class LogCapture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.lines = []

    def emit(self, record):
        try:
            self.lines.append(record.getMessage())
        except Exception:
            pass


# ===========================================================================
print("Golden Music 3.0 remote-control module tests")
print("=" * 60)

# ---------------------------------------------------------------------------
section("TokenAuth (webremote)")

tok = TokenAuth()
tok_str = tok.token
check("generated token matches TOKEN_RE",
      bool(TOKEN_RE.fullmatch(tok_str)), f"got {tok_str!r}")
check("token starts with GM- prefix", tok_str.startswith("GM-"))
check("token has at least 3 groups",
      len(tok_str.split("-")) >= 4, tok_str)
check("verify accepts the correct token", tok.verify(tok_str))
check("verify rejects a wrong token", not tok.verify("GM-dead-beef-ffff"))
check("verify rejects non-string input",
      not tok.verify(None) and not tok.verify(123))
check("token format stable for custom nbytes",
      bool(TOKEN_RE.fullmatch(TokenAuth.generate_token(8))))
rotated = tok.rotate()
check("rotate() returns a new valid token",
      rotated != tok_str and bool(TOKEN_RE.fullmatch(rotated)))
check("old token rejected after rotation", not tok.verify(tok_str))
check("new token accepted after rotation", tok.verify(rotated))
check("token never appears in TokenAuth repr",
      rotated not in repr(tok) and "****" in repr(tok))

# ---------------------------------------------------------------------------
section("webremote helpers")

st = sanitize_status({"volume": 999, "state": "weird", "title": 123,
                      "position_ms": -5, "repeat": "loop"})
check("sanitize_status clamps volume to 100", st["volume"] == 100)
check("sanitize_status coerces bad state", st["state"] == "stopped")
check("sanitize_status drops non-string title", st["title"] == "")
check("sanitize_status clamps negative position", st["position_ms"] == 0)
check("sanitize_status coerces bad repeat", st["repeat"] == "off")
check("sanitize_status fills defaults for junk input",
      sanitize_status(None)["queue_count"] == 0
      and sanitize_status(None)["can_seek"] is False)

try:
    import qrcode  # noqa: F401
    HAS_QRCODE = True
except ImportError:
    HAS_QRCODE = False
png = qrcode_png("http://127.0.0.1:9/?token=GM-test")
check("qrcode_png returns PNG bytes when qrcode installed"
      if HAS_QRCODE else "qrcode_png returns None without qrcode",
      (png is not None and png.startswith(b"\x89PNG")) if HAS_QRCODE
      else png is None)
check("lan_ip() returns a non-empty string", bool(lan_ip()))

# ---------------------------------------------------------------------------
section("WebRemoteServer basics (health, auth, status)")

bridge = FakeBridge(cover=PNG_1x1)
server = WebRemoteServer(bridge, host="127.0.0.1", port=0)
host, port = server.start()
base = f"http://{host}:{port}"
token = server.token
check("start() returns the bound loopback address", host == "127.0.0.1")
check("start() returns a real (non-zero) port", port > 0)
check("server token is the TokenAuth token", server.auth.verify(token))

status, headers, body = request(f"{base}/health")
payload = json_body(body)
check("GET /health is 200 without auth", status == 200)
check("GET /health returns minimal JSON",
      payload == {"ok": True, "app": "GoldenMusic"}, f"got {payload!r}")

status, _, _ = request(f"{base}/api/status")
check("/api/status without token -> 401", status == 401)
status, _, _ = request(f"{base}/api/status?token=GM-wrong-wrong-wrong")
check("/api/status with wrong token -> 401", status == 401)

status, headers, body = request(f"{base}/api/status?token={token}")
payload = json_body(body)
check("/api/status with correct token -> 200", status == 200)
match = payload is not None and all([
    payload.get("title") == bridge._status["title"],
    payload.get("artist") == bridge._status["artist"],
    payload.get("album") == bridge._status["album"],
    payload.get("state") == bridge._status["state"],
    payload.get("position_ms") == bridge._status["position_ms"],
    payload.get("duration_ms") == bridge._status["duration_ms"],
    payload.get("volume") == bridge._status["volume"],
    payload.get("shuffle") is True,
    payload.get("repeat") == bridge._status["repeat"],
    payload.get("queue_count") == bridge._status["queue_count"],
    payload.get("queue_position") == bridge._status["queue_position"],
    payload.get("can_seek") is True,
])
check("/api/status fields match the bridge", match, f"got {payload!r}")
check("authed responses set Cache-Control: no-store",
      (headers.get("Cache-Control") or "").lower() == "no-store")

status, _, body = request(f"{base}/api/status",
                          headers={"X-GM-Token": token})
check("auth via X-GM-Token header works", status == 200)
status, _, body = request(f"{base}/api/status",
                          headers={"Authorization": f"Bearer {token}"})
check("auth via Authorization: Bearer works", status == 200)
status, _, _ = request(f"{base}/api/status",
                       headers={"Authorization": f"Basic {token}"})
check("non-Bearer Authorization scheme is rejected", status == 401)

# ---------------------------------------------------------------------------
section("WebRemoteServer UI pages")

status, headers, body = request(f"{base}/")
html = body.decode("utf-8", "replace")
check("GET / is 200", status == 200)
check("GET / is HTML",
      "text/html" in (headers.get("Content-Type") or ""))
check("index page mentions Golden", "Golden" in html)
check("index page has no external http:// URL", "http://" not in html)
check("index page has no external https:// URL", "https://" not in html)
check("index page polls /api/status", "/api/status" in html)
check("index page drives /api/control", "/api/control" in html)
check("index page has transport + slider controls",
      all(x in html for x in ('id="seek"', 'id="vol"', 'id="play"',
                              'id="next"', 'id="prev"')))
check("index page works at 360px (viewport meta)",
      'width=device-width' in html)

status, _, body = request(f"{base}/api/pair")
pair_html = body.decode("utf-8", "replace")
check("GET /api/pair is 200", status == 200)
check("pair page shows the token", token in pair_html)
check("pair page shows host:port", f"{host}:{port}" in pair_html)
check("pair page has a copy button", "Copy" in pair_html)
if HAS_QRCODE:
    check("pair page embeds a QR data URI",
          "data:image/png;base64," in pair_html)

original_qr = webremote.qrcode_png
try:
    webremote.qrcode_png = lambda url: None  # simulate missing module
    status, _, body = request(f"{base}/api/pair")
    fallback = body.decode("utf-8", "replace")
    check("pair fallback without QR shows text + token",
          status == 200 and token in fallback and "Copy" in fallback
          and "QR module unavailable" in fallback)
finally:
    webremote.qrcode_png = original_qr

# ---------------------------------------------------------------------------
section("WebRemoteServer /api/cover")

status, headers, body = request(f"{base}/api/cover?token={token}")
check("cover endpoint returns the PNG bytes",
      status == 200 and body == PNG_1x1)
check("cover content-type is image/png",
      "image/png" in (headers.get("Content-Type") or ""))
bridge.cover = None
status, _, body = request(f"{base}/api/cover?token={token}")
check("cover endpoint returns 404 JSON when no art",
      status == 404 and json_body(body).get("error") == "no cover")
bridge.cover = PNG_1x1
status, _, _ = request(f"{base}/api/cover")
check("cover without auth -> 401", status == 401)

# ---------------------------------------------------------------------------
section("WebRemoteServer /api/control")

url = f"{base}/api/control"
status, _, body = post_json(url, {"action": "next"}, token=token)
payload = json_body(body)
check("action 'next' -> 200 ok", status == 200 and payload.get("ok") is True)
check("bridge received next()", ("next",) in bridge.calls)
status, _, _ = post_json(url, {"action": "play_pause"}, token=token)
check("action 'play_pause' dispatched",
      status == 200 and ("play_pause",) in bridge.calls)
status, _, _ = post_json(url, {"action": "toggle_shuffle"}, token=token)
check("action 'toggle_shuffle' dispatched",
      status == 200 and ("toggle_shuffle",) in bridge.calls)
status, _, _ = post_json(url, {"action": "cycle_repeat"}, token=token)
check("action 'cycle_repeat' dispatched",
      status == 200 and ("cycle_repeat",) in bridge.calls)

status, _, _ = post_json(url, {"action": "set_volume", "value": 50},
                         token=token)
check("volume 50 accepted", status == 200
      and ("set_volume", 50) in bridge.calls)
status, _, body = post_json(url, {"action": "set_volume", "value": 101},
                            token=token)
check("volume 101 -> 400", status == 400)
status, _, _ = post_json(url, {"action": "set_volume", "value": -1},
                         token=token)
check("volume -1 -> 400", status == 400)
status, _, _ = post_json(url, {"action": "set_volume", "value": "50"},
                         token=token)
check("volume as string -> 400", status == 400)
status, _, _ = post_json(url, {"action": "set_volume", "value": True},
                         token=token)
check("volume as bool -> 400", status == 400)
status, _, _ = post_json(url, {"action": "set_volume"}, token=token)
check("volume without value -> 400", status == 400)

status, _, _ = post_json(url, {"action": "seek_ms", "value": 1000},
                         token=token)
check("seek within range accepted", status == 200
      and ("seek_ms", 1000) in bridge.calls)
status, _, _ = post_json(url, {"action": "seek_ms", "value": 5_000_000},
                         token=token)
check("seek beyond duration -> 400", status == 400)
status, _, _ = post_json(url, {"action": "seek_ms", "value": -10},
                         token=token)
check("negative seek -> 400", status == 400)

status, _, body = post_json(url, {"action": "teleport"}, token=token)
check("unknown action -> 400", status == 400
      and "unknown action" in json_body(body).get("error", ""))
status, _, _ = post_json(url, {"action": 42}, token=token)
check("non-string action -> 400", status == 400)
status, _, _ = post_json(url, {"action": "next", "value": 5}, token=token)
check("stray value on a valueless action -> 400", status == 400)

status, _, body = request(url, method="POST", data=b"{not json",
                          headers={"Content-Type": "application/json",
                                   "X-GM-Token": token})
check("malformed JSON body -> 400", status == 400)
status, _, body = request(url, method="POST", data=b"[1, 2, 3]",
                          headers={"Content-Type": "application/json",
                                   "X-GM-Token": token})
check("non-object JSON body -> 400", status == 400)
status, _, body = raw_post(host, port, f"/api/control?token={token}",
                           body=b'{"action":"next"}',
                           content_type=None, content_length=-1)
check("missing Content-Length -> 400", status == 400)
status, _, body = raw_post(host, port, f"/api/control?token={token}",
                           body=b'{"action":"next"}',
                           content_type="text/plain")
check("non-JSON Content-Type -> 400", status == 400)
status, _, body = raw_post(host, port, f"/api/control?token={token}",
                           body=b"x" * 1024, content_length=20_000)
check("oversized body (20 KiB > 16 KiB cap) -> 400", status == 400)

big_ok = json.dumps({"action": "next", "pad": "x" * 15_000}).encode("utf-8")
status, _, _ = request(url, method="POST", data=big_ok,
                       headers={"Content-Type": "application/json",
                                "X-GM-Token": token})
check("body just under the cap is accepted", status == 200)

status, _, _ = post_json(url, {"action": "next"})
check("control without auth -> 401", status == 401)

status, _, _ = request(f"{base}/api/nope")
check("unknown route -> 404", status == 404)
status, _, _ = request(f"{base}/../../etc/passwd")
check("traversal-looking path -> 404", status == 404)
status, _, _ = request(f"{base}/", method="POST",
                       data=b"{}", headers={"Content-Type": "application/json"})
check("POST to a GET-only route -> 404", status == 404)

# ---------------------------------------------------------------------------
section("WebRemoteServer resilience (bridge exceptions)")

boom_bridge = ExplodingBridge()
boom_server = WebRemoteServer(boom_bridge, host="127.0.0.1", port=0)
bhost, bport = boom_server.start()
bbase = f"http://{bhost}:{bport}"
status, _, body = request(f"{bbase}/api/status?token={boom_server.token}")
payload = json_body(body)
check("bridge raising in get_status -> 500 JSON",
      status == 500 and isinstance(payload, dict) and "error" in payload)
status, _, body = post_json(f"{bbase}/api/control", {"action": "next"},
                            token=boom_server.token)
payload = json_body(body)
check("bridge raising in action -> 500 JSON",
      status == 500 and isinstance(payload, dict) and "error" in payload)
status, _, body = request(f"{bbase}/health")
check("server still alive after bridge errors",
      status == 200 and json_body(body).get("ok") is True)
boom_server.stop()

# Unsupported method + token in query: must not crash and must not log the token.
capture = LogCapture()
wr_logger = logging.getLogger("app.remote")
wr_logger.addHandler(capture)
try:
    conn = http.client.HTTPConnection(host, port, timeout=10)
    conn.request("OPTIONS", f"/api/control?token={token}")
    resp = conn.getresponse()
    resp.read()
    conn.close()
finally:
    wr_logger.removeHandler(capture)
logged = " | ".join(capture.lines)
check("unsupported HTTP method answered without crashing",
      resp.status in (501, 405, 400))
check("token never appears in request logs", token not in logged)
check("request logs carry the clean path only", "/api/control" in logged)
status, _, _ = request(f"{base}/health")
check("server alive after the unsupported method", status == 200)

# ---------------------------------------------------------------------------
section("WebRemoteServer LAN binding (0.0.0.0)")

lan_bridge = FakeBridge()
lan_server = WebRemoteServer(lan_bridge, host="0.0.0.0", port=0)
lan_host, lan_port = lan_server.start()
check("binds 0.0.0.0 when asked", lan_host == "0.0.0.0" and lan_port > 0)
status, _, body = request(f"http://127.0.0.1:{lan_port}/health")
check("0.0.0.0-bound server answers on loopback",
      status == 200 and json_body(body).get("ok") is True)
conn = http.client.HTTPConnection("127.0.0.1", lan_port, timeout=10)
conn.request("GET", "/api/pair",
             headers={"Host": f"192.168.1.5:{lan_port}"})
resp = conn.getresponse()
lan_pair = resp.read().decode("utf-8", "replace")
conn.close()
check("pair page uses the LAN Host header for the pairing URL",
      f"192.168.1.5:{lan_port}" in lan_pair)
check("pair page does not fall back to the loopback address",
      f"127.0.0.1:{lan_port}" not in lan_pair)
status, _, _ = request(f"http://127.0.0.1:{lan_port}/api/status"
                       f"?token={lan_server.token}")
check("auth works on a 0.0.0.0-bound server", status == 200)
lan_server.stop()

# ---------------------------------------------------------------------------
section("WebRemoteServer auth rate limiting")

rl_bridge = FakeBridge()
rl_server = WebRemoteServer(rl_bridge, host="127.0.0.1", port=0)
rl_host, rl_port = rl_server.start()
rl_base = f"http://{rl_host}:{rl_port}"
statuses = [request(f"{rl_base}/api/status?token=GM-bad-bad-bad")[0]
            for _ in range(10)]
check("first 10 bad tokens all get 401",
      statuses == [401] * 10, f"got {statuses}")
status, _, body = request(f"{rl_base}/api/status?token=GM-bad-bad-bad")
check("11th bad token within 60s -> 429", status == 429)
status, _, _ = request(f"{rl_base}/health")
check("/health is not affected by the auth block", status == 200)
status, _, _ = request(f"{rl_base}/api/status?token={rl_server.token}")
check("even a valid token is refused while blocked", status == 429)
rl_server.stop()

# ---------------------------------------------------------------------------
section("WebRemoteServer lifecycle")

check("server thread is alive while running", server.is_alive())
first = server.stop()
second = server.stop()
check("stop() returns True on first call", first is True)
check("stop() is idempotent (second call returns False)",
      second is False)
check("server thread joined after stop()", not server.is_alive())
refused = False
for _ in range(20):
    try:
        with socket.create_connection((host, port), timeout=0.3):
            pass
        time.sleep(0.05)
    except OSError:
        refused = True
        break
check("listening socket is closed after stop()", refused)

fresh = WebRemoteServer(FakeBridge())
check("stop() before start() is safe (returns False)",
      fresh.stop() is False)
_, fresh_port = fresh.start()
check("start() still works after a stray stop()", fresh_port > 0)
check("stop() still works after stray-stop + start()",
      fresh.stop() is True)

# ---------------------------------------------------------------------------
section("TelegramRemote token handling")

check("validate_token_format accepts 123456:AAE... style",
      validate_token_format(VALID_TOKEN))
check("validate_token_format rejects junk",
      not validate_token_format("junk"))
check("validate_token_format rejects short secret",
      not validate_token_format("123456789:AAEtooShort"))
check("validate_token_format rejects spaces",
      not validate_token_format("123456789:AAE " + "x" * 30))
check("validate_token_format rejects non-string",
      not validate_token_format(None) and not validate_token_format(12345))
try:
    TelegramRemote("junk-token", {42}, FakeTelegramBridge())
    bad_ctor = False
except ValueError:
    bad_ctor = True
check("constructor rejects a malformed bot token", bad_ctor)
try:
    TelegramRemote(VALID_TOKEN, {42}, FakeTelegramBridge(),
                   api_base="http://example.invalid")
    bad_https = False
except ValueError:
    bad_https = True
check("constructor rejects non-HTTPS api_base (HTTPS-only)", bad_https)


def make_tg(bridge=None, allowed=(42,)):
    bridge = bridge if bridge is not None else FakeTelegramBridge()
    remote = FakeRemote(VALID_TOKEN, set(allowed), bridge)
    return remote, bridge


# ---------------------------------------------------------------------------
section("TelegramRemote command dispatch")

remote, tg = make_tg()
remote._process_update(mk_update(42, 42, "/status"))
texts = sent_texts(remote)
check("/status replies once", len(texts) == 1)
check("/status reply contains the track title",
      any("Golden Test Song" in t for t in texts))
check("/status reply contains state and volume",
      any("playing" in t and "55%" in t for t in texts))
check("/status called bridge.get_status", ("get_status",) in tg.calls)

remote, tg = make_tg()
remote._process_update(mk_update(42, 42, "/next"))
check("/next calls bridge.next()", ("next",) in tg.calls)
check("/next replies", len(sent_texts(remote)) == 1)

remote, tg = make_tg()
remote._process_update(mk_update(42, 42, "/prev"))
check("/prev calls bridge.prev()", ("prev",) in tg.calls)
remote._process_update(mk_update(42, 42, "/stop"))
check("/stop calls bridge.stop()", ("stop",) in tg.calls)

remote, tg = make_tg()
remote._process_update(mk_update(42, 42, "/help"))
check("/help replies with the command list",
      any("/next" in t and "/volume" in t for t in sent_texts(remote)))
remote._process_update(mk_update(42, 42, "/frobnicate"))
check("unknown command replies with help text",
      any("/next" in t for t in sent_texts(remote)[1:]))

remote, tg = make_tg()
remote._process_update(mk_update(42, 42, "/play"))
check("/play (toggle) calls bridge.play_pause()",
      ("play_pause",) in tg.calls)

remote, tg = make_tg()
remote._process_update(mk_update(42, 42, "/volume 50"))
check("/volume 50 calls bridge.set_volume(50)",
      ("set_volume", 50) in tg.calls)
remote._process_update(mk_update(42, 42, "/volume 150"))
check("/volume 150 replies with a validation error",
      any("Invalid volume" in t for t in sent_texts(remote)[1:]))
check("/volume 150 never reaches the bridge",
      ("set_volume", 150) not in tg.calls)
remote._process_update(mk_update(42, 42, "/volume abc"))
check("/volume abc replies with a validation error",
      any("Invalid volume" in t for t in sent_texts(remote)[2:]))

# ---------------------------------------------------------------------------
section("TelegramRemote unauthorized users")

remote, tg = make_tg()
tg.calls.clear()
remote._process_update(mk_update(999, 999, "/next"))
texts = sent_texts(remote)
check("unauthorized user gets exactly one 'Not authorized' reply",
      texts == ["Not authorized."], f"got {texts!r}")
check("unauthorized command is NOT executed", tg.calls == [])
remote._process_update(mk_update(999, 999, "/status"))
check("repeat unauthorized message gets no reply",
      len(sent_texts(remote)) == 1)
remote._process_update(mk_update(1000, 1000, "/next"))
check("another unauthorized user also gets one notice",
      len([t for t in sent_texts(remote) if t == "Not authorized."]) == 2)

# ---------------------------------------------------------------------------
section("TelegramRemote search / playindex / list")

remote, tg = make_tg()
remote._process_update(mk_update(42, 42, "/search gold"))
texts = sent_texts(remote)
check("/search replies with a numbered list",
      any("1. Gold Song - Aurum" in t and "2. Silver Song - Argent" in t
          for t in texts))
check("/search called bridge.search('gold', 8)",
      ("search", "gold", 8) in tg.calls)
remote._process_update(mk_update(42, 42, "/playindex 2"))
check("/playindex 2 calls bridge.play_index(1)",
      ("play_index", 1) in tg.calls)
check("/playindex reply names the chosen track",
      any("Silver Song" in t for t in sent_texts(remote)[1:]))

remote, tg = make_tg()
remote._process_update(mk_update(42, 42, "/playindex 5"))
check("/playindex without prior search gets a hint",
      any("/search" in t for t in sent_texts(remote)))
remote, tg = make_tg()
remote._process_update(mk_update(42, 42, "/search gold"))
remote._process_update(mk_update(42, 42, "/playindex 99"))
check("/playindex out of range gets an error",
      any("out of range" in t for t in sent_texts(remote)))
remote, tg = make_tg()
remote._process_update(mk_update(42, 42, "/search"))
check("/search without query gets usage help",
      any("Usage" in t for t in sent_texts(remote)))

remote, tg = make_tg()
remote._process_update(mk_update(42, 42, "/list"))
texts = sent_texts(remote)
check("/list shows a numbered queue preview",
      any("1. Queue Song 0 - Queue Artist" in t and
          "10. Queue Song 9" in t for t in texts))
check("/list called bridge.queue_items(10)",
      ("queue_items", 10) in tg.calls)
tg.queue = []
remote._process_update(mk_update(42, 42, "/list"))
check("/list without titles falls back to a queue summary",
      any("Queue: 12 tracks" in t for t in sent_texts(remote)[1:]))

# ---------------------------------------------------------------------------
section("TelegramRemote rate limiting")

remote, tg = make_tg(allowed=(77,))
for i in range(25):
    remote._process_update(mk_update(77, 77, "/status", update_id=i + 1))
check("20 commands per minute are answered",
      len(sent_texts(remote)) == 20, f"got {len(sent_texts(remote))}")
check("commands beyond the limit are dropped silently",
      tg.calls.count(("get_status",)) == 20)
check("dropped messages still produce no late replies",
      len(sent_texts(remote)) == 20)

# ---------------------------------------------------------------------------
section("TelegramRemote loop, stop and secrecy")

remote, tg = make_tg()
remote.responses = [[mk_update(42, 42, "/next", update_id=100)], []]
remote.start()
deadline = time.time() + 5.0
while time.time() < deadline and ("next",) not in tg.calls:
    time.sleep(0.02)
check("run() loop processes a queued update",
      ("next",) in tg.calls)
first_get = next((p for m, p in remote.api_calls
                  if m == "getUpdates" and p.get("offset") == 101), None)
check("offset advances past the last update_id",
      first_get is not None)
check("getUpdates uses the long-poll timeout",
      any(m == "getUpdates" and p.get("timeout") == remote._poll_timeout
          for m, p in remote.api_calls))
stopped = remote.stop()
stopped2 = remote.stop()
check("stop() returns True and is idempotent",
      stopped is True and stopped2 is True and not remote.is_alive())

remote, tg = make_tg()
check("bot token masked in repr",
      VALID_TOKEN not in repr(remote) and ":***" in repr(remote))
sanitized = remote._sanitize("connection failed " + VALID_TOKEN)
check("_sanitize strips the token from error strings",
      VALID_TOKEN not in sanitized and ":***" in sanitized)

remote, tg = make_tg()
boom = RuntimeError("connection failed " + VALID_TOKEN)
remote._api_call = lambda method, params: (_ for _ in ()).throw(boom)
capture = LogCapture()
tg_logger = logging.getLogger("app.remote.telegram")
tg_logger.addHandler(capture)
try:
    remote.start()
    time.sleep(0.4)
    remote.stop()
finally:
    tg_logger.removeHandler(capture)
logged = " | ".join(capture.lines)
check("token never appears in logged exception text",
      VALID_TOKEN not in logged)
check("the (masked) failure is still logged",
      "connection failed" in logged and ":***" in logged)

# ===========================================================================
print("\n" + "=" * 60)
print(f"REMOTE TEST SUMMARY: {PASS} passed, {FAIL} failed")
print("=" * 60)
sys.exit(1 if FAIL else 0)
