"""
Golden Music 2.3 — optional Telegram bot remote (stdlib only, no Qt).

A long-polling Telegram bot that drives the player through a bridge
object supplied by the app.  The bot is OFF by default; the user must
provide a bot token (from BotFather) and the bot only ever reacts to an
allow-list of numeric user IDs.

App integration
---------------
    from gm.telegrambot import TelegramRemote

    bot = TelegramRemote(
        bot_token="123456789:AAE...",       # user-supplied
        allowed_user_ids={12345678},         # user-supplied allow-list
        bridge=my_bridge,                    # TelegramBridge implementation
    )
    bot.start()
    ...
    bot.stop()        # idempotent, joins the polling thread

Thread-safety contract (IMPORTANT)
----------------------------------
The polling thread calls bridge methods from its own (non-GUI) thread.
The APP must make the bridge thread-safe — the recommended pattern is a
``queue.Queue`` marshalling actions into the Qt main loop (drained by a
``QTimer``) and a lock-guarded snapshot dict for ``get_status()``.
Never touch widgets from bridge methods.

Secrecy of the bot token
------------------------
The token is never logged and never appears in exception text: the class
masks it in ``__repr__`` and filters it out of every error string before
logging (``_sanitize``).  Tests exercise exactly that.

Testing seam
------------
ALL network traffic goes through the single method ``_api_call(method,
params)`` which returns parsed JSON or ``None``.  Tests subclass (or
monkeypatch) it — no test ever talks to the real Telegram API.
"""
from __future__ import annotations

import abc
import json
import logging
import re
import threading
import time
import urllib.request
from collections import deque
from typing import Any, Iterable

from gm.webremote import PlayerBridge, sanitize_status

__all__ = ["TelegramBridge", "TelegramRemote", "validate_token_format",
           "mask_secret", "TELEGRAM_TOKEN_RE"]

log = logging.getLogger("app.remote.telegram")

TELEGRAM_TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,60}$")

RATE_MAX_COMMANDS = 20          # commands per user ...
RATE_WINDOW_S = 60.0            # ... per this window (dropped silently beyond)
POLL_IDLE_SLEEP_S = 0.05        # idle nap when getUpdates returns instantly
POLL_ERROR_SLEEP_S = 2.0        # back-off after a transport failure
SEARCH_LIMIT = 8                # results shown by /search
QUEUE_PREVIEW = 10              # entries shown by /list
MAX_MESSAGE_LEN = 4000          # sendMessage truncation (Telegram caps 4096)
UNAUTHORIZED_NOTICE_CAP = 10_000  # memory cap for the "already notified" set

HELP_TEXT = (
    "Golden Music remote commands:\n"
    "/status - now playing\n"
    "/play - toggle play/pause\n"
    "/pause - pause (if playing)\n"
    "/next - next track\n"
    "/prev - previous track\n"
    "/stop - stop playback\n"
    "/volume 0-100 - set volume\n"
    "/search <query> - search the library\n"
    "/playindex <n> - play result n from the last list\n"
    "/list - queue preview"
)


def validate_token_format(token: Any) -> bool:
    """True when *token* looks like a Telegram bot token (no network use)."""
    try:
        return bool(TELEGRAM_TOKEN_RE.fullmatch(str(token)))
    except Exception:
        return False


def mask_secret(secret: str) -> str:
    """Mask a bot token for display: keep the public bot-id prefix."""
    if not secret:
        return "<empty>"
    prefix = secret.split(":", 1)[0]
    return "%s:***" % prefix


def _fmt_ms(ms: Any) -> str:
    """Format milliseconds as m:ss / h:mm:ss (junk-tolerant)."""
    try:
        total = max(0, int(ms)) // 1000
    except (TypeError, ValueError):
        total = 0
    minutes, seconds = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return "%d:%02d:%02d" % (hours, minutes, seconds)
    return "%d:%02d" % (minutes, seconds)


# ---------------------------------------------------------------------------
# Bridge protocol (implemented by the app)
# ---------------------------------------------------------------------------
class TelegramBridge(PlayerBridge):
    """The player surface the app must implement for the Telegram bot.

    Same surface as ``gm.webremote.PlayerBridge`` (see its docstring for
    the thread-safety contract — the polling thread calls these methods
    concurrently with the GUI) plus:

    * ``search(query, limit)`` — return up to *limit* dicts
      ``{"title": str, "artist": str}``.
    * ``play_index(index)`` — play the item at 0-based *index* of the
      list context the bot last showed the user (a ``/search`` result
      list or the ``/list`` queue preview).  Track the last list your
      bridge produced to resolve the index.
    * ``queue_items(limit)`` — OPTIONAL, concrete default returns ``[]``.
      Override it to give ``/list`` real queue titles; with the default
      the bot replies with a queue summary from ``get_status()``.
    """

    @abc.abstractmethod
    def search(self, query: str, limit: int = SEARCH_LIMIT) -> list[dict[str, Any]]:
        """Search the library; return [{"title": ..., "artist": ...}, ...]."""

    @abc.abstractmethod
    def play_index(self, index: int) -> bool:
        """Play the 0-based *index* of the last list shown to the user."""

    def queue_items(self, limit: int = QUEUE_PREVIEW) -> list[dict[str, Any]]:
        """Queue preview for /list. Optional override; default: no titles."""
        return []


# ---------------------------------------------------------------------------
# The bot
# ---------------------------------------------------------------------------
_BRIDGE_METHODS = (
    "get_status", "play_pause", "next", "prev", "stop", "seek_ms",
    "set_volume", "toggle_shuffle", "cycle_repeat", "search", "play_index",
)


class TelegramRemote(threading.Thread):
    """Long-polling Telegram remote control (getUpdates loop).

    All network I/O funnels through ``_api_call`` — the seam tests fake.
    The polling loop never crashes: any failure is logged (sanitized) and
    the loop continues.  ``stop()`` is idempotent and joins the thread.
    """

    def __init__(self, bot_token: str, allowed_user_ids: Iterable[int],
                 bridge: TelegramBridge, poll_timeout: int = 25,
                 api_base: str = "https://api.telegram.org") -> None:
        if not validate_token_format(bot_token):
            raise ValueError("bot_token does not look like a Telegram bot token")
        # HTTPS-only invariant, enforced at construction and before every call.
        if not str(api_base).startswith("https://"):
            raise ValueError("api_base must be an https:// URL (HTTPS only)")
        missing = [m for m in _BRIDGE_METHODS if not callable(getattr(bridge, m, None))]
        if missing:
            raise TypeError(
                "bridge is missing TelegramBridge methods: %s" % ", ".join(missing))
        try:
            allowed = {int(u) for u in (allowed_user_ids or ())}
        except (TypeError, ValueError) as exc:
            raise ValueError("allowed_user_ids must contain integers") from exc

        super().__init__(name="TelegramRemote", daemon=True)
        self._bot_token = str(bot_token)
        self._allowed = allowed
        self._bridge = bridge
        self._poll_timeout = max(0, int(poll_timeout))
        self._api_base = str(api_base).rstrip("/")
        self._net_timeout = max(10.0, float(poll_timeout) + 10.0)
        self._stop_event = threading.Event()
        self._offset = 0
        # Per-user command rate limiting.
        self._rate_lock = threading.Lock()
        self._rate: dict[int, deque[float]] = {}
        # One "Not authorized" notice per unknown user, then silence.
        self._notified: set[int] = set()
        # Last list shown per chat (for /playindex).
        self._last_list: dict[int, list[dict[str, Any]]] = {}

    # -- introspection / secrecy --------------------------------------------
    def __repr__(self) -> str:
        return ("TelegramRemote(bot_token=%s, allowed_user_ids=%r, alive=%r)"
                % (mask_secret(self._bot_token), sorted(self._allowed),
                   self.is_alive()))

    def _sanitize(self, text: Any) -> str:
        """Strip the bot token from *text* before it may reach a log."""
        out = text if isinstance(text, str) else repr(text)
        try:
            if self._bot_token and self._bot_token in out:
                out = out.replace(self._bot_token, mask_secret(self._bot_token))
        except Exception:
            pass
        return out

    # -- network layer (THE test seam) ---------------------------------------
    def _api_call(self, method: str, params: dict[str, Any]) -> dict[str, Any] | None:
        """Perform one Telegram API call. Returns parsed JSON or None.

        This is the ONLY method that touches the network: it POSTs JSON to
        ``{api_base}/bot<token>/<method>`` over HTTPS with a timeout and
        one retry.  It never raises — failures return ``None`` after
        being logged with the token filtered out.  Tests override it.
        """
        url = "%s/bot%s/%s" % (self._api_base, self._bot_token, method)
        # HTTPS-only invariant (asserted at construction, re-checked here).
        if not url.startswith("https://"):
            log.error("refusing non-HTTPS Telegram API url for method %s", method)
            return None
        body = json.dumps(params or {}).encode("utf-8")
        for attempt in (1, 2):
            try:
                request = urllib.request.Request(
                    url, data=body, method="POST",
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(request, timeout=self._net_timeout) as resp:  # nosec B310
                    raw = resp.read(1024 * 1024)
                return json.loads(raw.decode("utf-8", "replace"))
            except Exception as exc:
                log.warning("telegram api %s failed (attempt %d/2): %s",
                            method, attempt, self._sanitize(exc))
                if self._stop_event.is_set():
                    return None
        return None

    # -- outbound -------------------------------------------------------------
    def send(self, chat_id: int, text: str) -> bool:
        """sendMessage to *chat_id* (plain text, no parse_mode). Never raises."""
        try:
            data = self._api_call("sendMessage",
                                  {"chat_id": chat_id,
                                   "text": str(text)[:MAX_MESSAGE_LEN]})
            return isinstance(data, dict) and bool(data.get("ok"))
        except Exception as exc:
            log.warning("sendMessage failed: %s", self._sanitize(exc))
            return False

    # -- lifecycle --------------------------------------------------------------
    def run(self) -> None:
        """getUpdates long-poll loop (internal — use start/stop)."""
        log.info("telegram remote loop started")
        while not self._stop_event.is_set():
            try:
                count = self._poll_once()
            except Exception as exc:  # belt & braces — the loop must never die
                log.warning("telegram poll error: %s", self._sanitize(exc))
                count = -1
            if count <= 0:
                # 0: long poll returned nothing; <0: transport failure.
                self._stop_event.wait(
                    POLL_ERROR_SLEEP_S if count < 0 else POLL_IDLE_SLEEP_S)
        log.info("telegram remote loop stopped")

    def stop(self, timeout: float = 10.0) -> bool:
        """Signal the loop to stop and join it. Idempotent.

        An in-flight long poll (up to ``poll_timeout`` seconds) cannot be
        interrupted, so the thread may linger briefly after stop(); it is
        a daemon thread, so process exit is never blocked by it.
        """
        self._stop_event.set()
        if self.is_alive() and threading.current_thread() is not self:
            self.join(timeout)
        return not self.is_alive()

    # -- polling -----------------------------------------------------------------
    def _poll_once(self) -> int:
        """One getUpdates round. Returns updates handled, 0 none, -1 failure."""
        params: dict[str, Any] = {
            "offset": self._offset,
            "timeout": self._poll_timeout,
            "allowed_updates": ["message"],
        }
        data = self._api_call("getUpdates", params)
        if not isinstance(data, dict) or not data.get("ok"):
            return -1
        updates = data.get("result")
        if not isinstance(updates, list):
            return -1
        handled = 0
        for update in updates:
            if not isinstance(update, dict):
                continue
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                self._offset = max(self._offset, update_id + 1)
            try:
                self._process_update(update)
                handled += 1
            except Exception as exc:
                log.warning("update handling failed: %s", self._sanitize(exc))
        return handled

    def _process_update(self, update: dict[str, Any]) -> None:
        """Route one raw update (public-ish: tests call it directly)."""
        message = update.get("message")
        if not isinstance(message, dict):
            return
        chat = message.get("chat")
        chat_id = chat.get("id") if isinstance(chat, dict) else None
        sender = message.get("from")
        user_id = sender.get("id") if isinstance(sender, dict) else None
        if not isinstance(chat_id, int) or not isinstance(user_id, int):
            return
        text = message.get("text")
        if not isinstance(text, str) or not text.strip():
            return
        self._handle_message(chat_id, user_id, text)

    # -- command handling -----------------------------------------------------------
    def _handle_message(self, chat_id: int, user_id: int, text: str) -> None:
        if not self._command_allowed(user_id):
            return  # rate-limited: drop silently
        if user_id not in self._allowed:
            self._refuse_unauthorized(chat_id, user_id)
            return
        parts = text.strip().split()
        command = parts[0].lstrip("/").split("@", 1)[0].lower()
        args = " ".join(parts[1:]).strip()
        handler = self._COMMANDS.get(command)
        if handler is None:
            self.send(chat_id, HELP_TEXT)
            return
        handler(self, chat_id, args)

    def _refuse_unauthorized(self, chat_id: int, user_id: int) -> None:
        """Unauthorized users get ONE notice, then silence (ids only in logs)."""
        log.info("unauthorized telegram user id=%s", user_id)
        if user_id in self._notified:
            return
        if len(self._notified) >= UNAUTHORIZED_NOTICE_CAP:
            self._notified.clear()
        self._notified.add(user_id)
        self.send(chat_id, "Not authorized.")

    def _command_allowed(self, user_id: int) -> bool:
        """Sliding-window rate limit: max RATE_MAX_COMMANDS per minute."""
        now = time.monotonic()
        with self._rate_lock:
            dq = self._rate.setdefault(user_id, deque())
            while dq and dq[0] <= now - RATE_WINDOW_S:
                dq.popleft()
            if len(dq) >= RATE_MAX_COMMANDS:
                return False
            dq.append(now)
            return True

    # -- bridge helpers (never raise) --------------------------------------------
    def _bridge_status(self) -> dict[str, Any] | None:
        try:
            return sanitize_status(self._bridge.get_status())
        except Exception as exc:
            log.warning("bridge get_status failed: %s", self._sanitize(exc))
            return None

    def _bridge_action(self, name: str) -> bool:
        try:
            return bool(getattr(self._bridge, name)())
        except Exception as exc:
            log.warning("bridge %s failed: %s", name, self._sanitize(exc))
            return False

    def _bridge_action_value(self, name: str, value: Any) -> bool:
        try:
            return bool(getattr(self._bridge, name)(value))
        except Exception as exc:
            log.warning("bridge %s failed: %s", name, self._sanitize(exc))
            return False

    # -- commands -------------------------------------------------------------------
    def _cmd_help(self, chat_id: int, args: str) -> None:
        self.send(chat_id, HELP_TEXT)

    def _cmd_status(self, chat_id: int, args: str) -> None:
        status = self._bridge_status()
        if status is None:
            self.send(chat_id, "Player status unavailable.")
            return
        lines = ["Now %s: %s" % (status["state"], status["title"] or "(unknown)")]
        if status["artist"]:
            lines.append("Artist: %s" % status["artist"])
        if status["album"]:
            lines.append("Album: %s" % status["album"])
        lines.append("Position: %s / %s"
                     % (_fmt_ms(status["position_ms"]), _fmt_ms(status["duration_ms"])))
        lines.append("Volume: %d%%" % status["volume"])
        if status["queue_count"] > 0:
            lines.append("Queue: %d/%d"
                         % (status["queue_position"] + 1, status["queue_count"]))
        lines.append("Shuffle: %s | Repeat: %s"
                     % ("on" if status["shuffle"] else "off", status["repeat"]))
        self.send(chat_id, "\n".join(lines))

    def _cmd_play(self, chat_id: int, args: str) -> None:
        ok = self._bridge_action("play_pause")
        status = self._bridge_status()
        if ok:
            state = status["state"] if status else "unknown"
            self.send(chat_id, "Playback toggled (now: %s)." % state)
        else:
            self.send(chat_id, "Could not toggle playback.")

    def _cmd_pause(self, chat_id: int, args: str) -> None:
        status = self._bridge_status()
        if status is None:
            self.send(chat_id, "Player status unavailable.")
            return
        if status["state"] == "playing":
            ok = self._bridge_action("play_pause")
            self.send(chat_id, "Paused." if ok else "Could not pause.")
        else:
            self.send(chat_id, "Not playing.")

    def _cmd_next(self, chat_id: int, args: str) -> None:
        ok = self._bridge_action("next")
        self.send(chat_id, "Skipped to next track." if ok else "Could not skip.")

    def _cmd_prev(self, chat_id: int, args: str) -> None:
        ok = self._bridge_action("prev")
        self.send(chat_id, "Went back one track." if ok else "Could not go back.")

    def _cmd_stop(self, chat_id: int, args: str) -> None:
        ok = self._bridge_action("stop")
        self.send(chat_id, "Playback stopped." if ok else "Could not stop.")

    def _cmd_volume(self, chat_id: int, args: str) -> None:
        usage = "Invalid volume. Usage: /volume 0-100"
        candidate = (args or "").strip()
        if not re.fullmatch(r"[+-]?\d+", candidate):
            self.send(chat_id, usage)
            return
        value = int(candidate)
        if not 0 <= value <= 100:
            self.send(chat_id, usage)
            return
        if self._bridge_action_value("set_volume", value):
            self.send(chat_id, "Volume set to %d%%." % value)
        else:
            self.send(chat_id, "Could not set volume.")

    def _cmd_search(self, chat_id: int, args: str) -> None:
        if not args:
            self.send(chat_id, "Usage: /search <query>")
            return
        try:
            results = self._bridge.search(args, SEARCH_LIMIT)
        except Exception as exc:
            log.warning("bridge search failed: %s", self._sanitize(exc))
            self.send(chat_id, "Search failed.")
            return
        items = [r for r in (results or []) if isinstance(r, dict)][:SEARCH_LIMIT]
        if not items:
            self.send(chat_id, "No results for '%s'." % args)
            return
        self._last_list[chat_id] = items
        lines = [_list_line(i, item) for i, item in enumerate(items, start=1)]
        lines.append("Play with: /playindex <1-%d>" % len(items))
        self.send(chat_id, "\n".join(lines))

    def _cmd_playindex(self, chat_id: int, args: str) -> None:
        if not re.fullmatch(r"\d+", (args or "").strip()):
            self.send(chat_id,
                      "Usage: /playindex <n> (number from the last /search or /list)")
            return
        number = int(args)
        items = self._last_list.get(chat_id) or []
        if not items:
            self.send(chat_id, "Nothing to play yet - use /search <query> first.")
            return
        if not 1 <= number <= len(items):
            self.send(chat_id, "Index out of range (1-%d)." % len(items))
            return
        item = items[number - 1]
        if self._bridge_action_value("play_index", number - 1):
            self.send(chat_id, "Playing: %s" % _list_line(number, item))
        else:
            self.send(chat_id, "Could not play that item.")

    def _cmd_list(self, chat_id: int, args: str) -> None:
        status = self._bridge_status()
        try:
            items = self._bridge.queue_items(QUEUE_PREVIEW)
        except Exception as exc:
            log.warning("bridge queue_items failed: %s", self._sanitize(exc))
            items = []
        clean = [r for r in (items or []) if isinstance(r, dict)][:QUEUE_PREVIEW]
        if clean:
            self._last_list[chat_id] = clean
            lines = [_list_line(i, item) for i, item in enumerate(clean, start=1)]
            if status and status["queue_count"] > len(clean):
                lines.append("(showing first %d of %d)"
                             % (len(clean), status["queue_count"]))
            lines.append("Play with: /playindex <1-%d>" % len(clean))
            self.send(chat_id, "\n".join(lines))
            return
        if status is None:
            self.send(chat_id, "Player status unavailable.")
        elif status["queue_count"] > 0:
            self.send(chat_id, "Queue: %d tracks (now #%d)."
                      % (status["queue_count"], status["queue_position"] + 1))
        else:
            self.send(chat_id, "Queue is empty.")

    _COMMANDS = {
        "start": _cmd_help,
        "help": _cmd_help,
        "status": _cmd_status,
        "play": _cmd_play,
        "pause": _cmd_pause,
        "next": _cmd_next,
        "prev": _cmd_prev,
        "stop": _cmd_stop,
        "volume": _cmd_volume,
        "search": _cmd_search,
        "playindex": _cmd_playindex,
        "list": _cmd_list,
    }


def _list_line(number: int, item: dict[str, Any]) -> str:
    """Format one numbered list entry: 'N. Title - Artist'."""
    title = str(item.get("title") or "(untitled)")
    artist = str(item.get("artist") or "").strip()
    if artist:
        return "%d. %s - %s" % (number, title, artist)
    return "%d. %s" % (number, title)
