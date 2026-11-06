"""Golden Music 2.3 — user preferences (the ``prefs`` dict).

Small scalar/UI settings live in config.json under the ``prefs`` key so the
existing validated-config machinery keeps protecting them. Library data is
in SQLite (see gm/db.py); these are behavior toggles and values only.
"""
from __future__ import annotations

DEFAULT_PREFS: dict = {
    # Appearance
    "ui_scale": 1.0,
    "reduce_animations": False,
    "density": "normal",            # compact | normal | relaxed
    "show_covers_in_list": True,
    "list_view": "rows",            # rows | table
    "table_columns": ["index", "title", "artist", "album", "duration"],
    "dynamic_album_theme": False,
    "language": "en",               # en | fa
    # Playback
    "crossfade_ms": 0,
    "gapless": True,
    "pitch_preserve_speed": True,
    "silence_skip": False,
    "bookmarks_enabled": True,
    "fade_enabled": True,
    "fade_ms": 300,
    # Audio / EQ
    "audio_engine": "auto",         # auto | mpv | qt
    "normalization": "off",         # off | tags
    "eq_enabled": False,
    "eq_preset": "Flat",
    "eq_gains": [0.0] * 10,
    # Library
    "auto_rescan": True,
    "folder_watch": True,
    "folder_watch_debounce_ms": 2000,
    "smart_most_played": False,
    "smart_recently_added": False,
    "smart_never_played": False,
    "smart_not_heard_recently": False,
    "smart_top_rated": False,
    "m3u_relative_paths": True,
    # Lyrics
    "lyrics_online": True,
    "lyrics_show_translation": True,
    "lyrics_save_mode": "ask",      # ask | embed | sidecar
    "lyrics_quality_check": True,
    "floating_lyrics": False,
    "lyrics_font_scale": 1.0,
    "lyrics_offset": {},            # per-path manual offsets (path -> ms)
    # Stats
    "stats_charts_enabled": True,
    "stats_heatmap_enabled": True,
    "stats_wrapped_enabled": True,
    # Integrations
    "webremote_enabled": False,
    "webremote_host": "127.0.0.1",
    "webremote_port": 8642,
    "webremote_token": "",
    "telegram_enabled": False,
    "telegram_bot_token": "",
    "telegram_allowed_ids": "",
    # Advanced
    "toast_enabled": True,
    "taskbar_close_to_tray": False,
    # Shortcuts: {action_id: "Ctrl+Shift+X"} — only non-default entries
    "custom_shortcuts": {},
}

# prefs keys that are lists/dicts get deep-merged; scalars replaced.
_CONTAINER_KEYS = {"table_columns", "eq_gains", "custom_shortcuts",
                   "lyrics_offset"}

_ALLOWED_ENUMS = {
    "density": {"compact", "normal", "relaxed"},
    "list_view": {"rows", "table"},
    "language": {"en", "fa"},
    "audio_engine": {"auto", "mpv", "qt"},
    "normalization": {"off", "tags"},
    "lyrics_save_mode": {"ask", "embed", "sidecar"},
    "webremote_host": {"127.0.0.1", "0.0.0.0"},  # nosec B104
}


def merge_prefs(stored) -> dict:
    """Overlay a stored prefs dict (from config.json) onto defaults.

    Unknown keys are dropped (typo/legacy protection), enum-valued keys with
    invalid values fall back to the default, numbers are range-clamped.
    """
    out = dict(DEFAULT_PREFS)
    if not isinstance(stored, dict):
        return out
    for key, value in stored.items():
        if key not in DEFAULT_PREFS or key == "___reset___":
            continue
        allowed = _ALLOWED_ENUMS.get(key)
        if allowed is not None and value not in allowed:
            continue
        if key in _CONTAINER_KEYS:
            if isinstance(value, (list, dict)):
                out[key] = value
            continue
        default = DEFAULT_PREFS[key]
        try:
            if isinstance(default, bool):
                out[key] = bool(value)
            elif isinstance(default, float):
                out[key] = float(value)
            elif isinstance(default, int) and not isinstance(default, bool):
                out[key] = int(value)
            else:
                out[key] = str(value) if value is not None else default
        except (TypeError, ValueError):
            continue
    # range clamps
    out["ui_scale"] = max(0.75, min(1.5, out["ui_scale"]))
    out["crossfade_ms"] = max(0, min(12000, out["crossfade_ms"]))
    out["fade_ms"] = max(100, min(2000, out["fade_ms"]))
    out["folder_watch_debounce_ms"] = max(250, min(10000,
                                                   out["folder_watch_debounce_ms"]))
    out["webremote_port"] = max(1024, min(65535, out["webremote_port"]))
    out["lyrics_font_scale"] = max(0.8, min(1.6, out["lyrics_font_scale"]))
    if not isinstance(out["eq_gains"], list) or len(out["eq_gains"]) != 10:
        out["eq_gains"] = [0.0] * 10
    else:
        out["eq_gains"] = [max(-12.0, min(12.0, float(g or 0)))
                           for g in out["eq_gains"]]
    if not isinstance(out["table_columns"], list):
        out["table_columns"] = list(DEFAULT_PREFS["table_columns"])
    if not isinstance(out["custom_shortcuts"], dict):
        out["custom_shortcuts"] = {}
    return out
