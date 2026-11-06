"""Golden Music 3.0 — smart playlists.

Each smart playlist is a JSON spec stored in ``playlists.smart_query``:
    {"rules": [{"field": "play_count", "op": ">=", "value": 5}, ...],
     "sort": "play_count", "order": "desc", "limit": 100}

Built-ins are OPT-IN: they only exist in the Playlists page when the matching
checkbox in Settings -> Library is enabled (see settings_dialog wiring).
"""
from __future__ import annotations

import json
import time

FIELDS = {"play_count", "rating", "added_at", "last_played_at", "year",
          "duration_ms", "genre", "artist", "album", "title", "path"}
OPS = {">=", "<=", ">", "<", "==", "!=", "contains", "not_contains"}
NUMERIC_FIELDS = {"play_count", "rating", "added_at", "last_played_at",
                  "year", "duration_ms"}

BUILTINS = {
    "most_played": {
        "label": "Most Played",
        "spec": {"rules": [{"field": "play_count", "op": ">", "value": 0}],
                 "sort": "play_count", "order": "desc", "limit": 100},
    },
    "recently_added": {
        "label": "Recently Added",
        "spec": {"rules": [], "sort": "added_at", "order": "desc",
                 "limit": 100},
    },
    "never_played": {
        "label": "Never Played",
        "spec": {"rules": [{"field": "play_count", "op": "==", "value": 0}],
                 "sort": "added_at", "order": "asc", "limit": 500},
    },
    "not_heard_recently": {
        "label": "Not Heard Recently",
        "spec": {"rules": [{"field": "last_played_at", "op": "<",
                            "value": "now-30d"}],
                 "sort": "last_played_at", "order": "asc", "limit": 100},
    },
    "top_rated": {
        "label": "Top Rated",
        "spec": {"rules": [{"field": "rating", "op": ">=", "value": 4}],
                 "sort": "rating", "order": "desc", "limit": 100},
    },
}


def validate_spec(spec) -> dict | None:
    """Return a cleaned spec dict or None if invalid/suspicious."""
    if isinstance(spec, str):
        try:
            spec = json.loads(spec)
        except (ValueError, TypeError):
            return None
    if not isinstance(spec, dict):
        return None
    rules = spec.get("rules", [])
    if not isinstance(rules, list) or len(rules) > 16:
        return None
    clean_rules = []
    for r in rules:
        if not isinstance(r, dict):
            return None
        f, op, v = r.get("field"), r.get("op"), r.get("value")
        if f not in FIELDS or op not in OPS:
            return None
        if f in NUMERIC_FIELDS and op in ("contains", "not_contains"):
            return None
        if f in NUMERIC_FIELDS and not isinstance(v, (int, float)) and not (
                isinstance(v, str) and v.startswith("now-")):
            try:
                v = float(v)
            except (TypeError, ValueError):
                return None
        clean_rules.append({"field": f, "op": op, "value": v})
    sort = spec.get("sort", "added_at")
    if sort not in FIELDS:
        sort = "added_at"
    order = spec.get("order", "desc")
    if order not in ("asc", "desc"):
        order = "desc"
    limit = spec.get("limit", 100)
    try:
        limit = max(1, min(int(limit), 5000))
    except (TypeError, ValueError):
        limit = 100
    return {"rules": clean_rules, "sort": sort, "order": order,
            "limit": limit}


def _resolve_value(value):
    if isinstance(value, str) and value.startswith("now-"):
        # e.g. "now-30d" -> epoch 30 days ago
        m = value[4:]
        try:
            if m.endswith("d"):
                return time.time() - float(m[:-1]) * 86400
            if m.endswith("h"):
                return time.time() - float(m[:-1]) * 3600
            if m.endswith("w"):
                return time.time() - float(m[:-1]) * 604800
        except ValueError:
            return None
        return None
    return value


def sql_where(spec) -> tuple[str, list]:
    """Compile a validated spec into a (where, args) SQL pair."""
    clauses, args = [], []
    for r in spec["rules"]:
        f, op, v = r["field"], r["op"], r["value"]
        rv = _resolve_value(v)
        if rv is None:
            continue
        if op in (">=", "<=", ">", "<", "==", "!="):
            clauses.append(f"{f} {op} ?")
            args.append(rv)
        elif op == "contains":
            clauses.append(f"{f} LIKE ?")
            args.append(f"%{rv}%")
        elif op == "not_contains":
            clauses.append(f"({f} IS NULL OR {f} NOT LIKE ?)")
            args.append(f"%{rv}%")
    where = " AND ".join(clauses) if clauses else "1=1"
    # never_played semantics: NULL last_played_at counts as "never"
    where = where.replace("last_played_at <", "(COALESCE(last_played_at,0) <")
    where = where.replace("last_played_at <=", "(COALESCE(last_played_at,0) <=")
    where = where.replace("last_played_at >", "(COALESCE(last_played_at,0) >")
    where = where.replace("last_played_at >=", "(COALESCE(last_played_at,0) >=")
    return where, args


def matches(track: dict, spec) -> bool:
    """Pure-python matcher (unit-testable without SQL)."""
    for r in spec["rules"]:
        v = track.get(r["field"])
        rv = _resolve_value(r["value"])
        if rv is None:
            continue
        op = r["op"]
        if op == "contains":
            if str(rv).lower() not in str(v or "").lower():
                return False
        elif op == "not_contains":
            if str(rv).lower() in str(v or "").lower():
                return False
        else:
            if v is None:
                v = 0 if r["field"] in NUMERIC_FIELDS else ""
            try:
                if op == ">=" and not v >= rv:
                    return False
                if op == "<=" and not v <= rv:
                    return False
                if op == ">" and not v > rv:
                    return False
                if op == "<" and not v < rv:
                    return False
                if op == "==" and v != rv:
                    return False
                if op == "!=" and v == rv:
                    return False
            except TypeError:
                return False
    return True
