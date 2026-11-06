"""Golden Music 3.0 — JSON -> SQLite migration.

Safety contract (non-negotiable):
  1. BACK UP FIRST — config.json / tag_cache.json / stats.json are copied to
     ``backups/pre_sqlite_<timestamp>/`` before anything is touched.
  2. IDEMPOTENT — running twice leaves the database byte-for-byte equivalent
     in content terms (meta flag ``json_migration`` guards re-entry).
  3. ATOMIC — the whole ingest runs in ONE transaction on a dedicated
     connection; a crash mid-migration leaves the JSON files authoritative
     and the database un-flagged, so the next start simply retries.
  4. VERIFIED — row counts are compared against the JSON sources before the
     ``done`` flag is written. Any mismatch -> rollback, status ``failed``,
     and the app keeps operating in legacy JSON mode (data loss impossible).
  5. NON-DESTRUCTIVE — original JSON files are never deleted or renamed.
"""
from __future__ import annotations

import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from config import load_json_file, load_tag_cache_file

log = logging.getLogger("app.migration")

STATS_KEYS = ("total_seconds", "play_counts", "artist_seconds", "sessions",
              "first_played")


@dataclass
class MigrationResult:
    status: str = "not_needed"      # not_needed|ok|already|failed
    tracks: int = 0
    favorites: int = 0
    playlists: int = 0
    playlist_tracks: int = 0
    play_counts: int = 0
    total_seconds: float = 0.0
    message: str = ""
    backup_dir: str = ""
    checks: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status in ("ok", "already", "not_needed")


def _backup_sources(config_file: Path, tag_cache: Path, stats: Path) -> Path:
    backup_dir = config_file.parent / "backups" / (
        "pre_sqlite_" + time.strftime("%Y%m%d_%H%M%S"))
    backup_dir.mkdir(parents=True, exist_ok=True)
    for src in (config_file, tag_cache, stats):
        if src.is_file():
            try:
                shutil.copy2(str(src), str(backup_dir / src.name))
            except OSError as e:      # pragma: no cover - disk level
                log.warning("backup copy failed for %s: %s", src, e)
    return backup_dir


def _read_stats(stats_path: Path) -> dict:
    data, _ok = load_json_file(stats_path, "stats")
    if not isinstance(data, dict):
        return {}
    return data


def _f(value, default=0.0) -> float:
    try:
        v = float(value)
        return v if v == v and abs(v) != float("inf") else default
    except (TypeError, ValueError):
        return default


def migrate_json_to_sqlite(db, config_file: Path) -> MigrationResult:
    """Migrate library/tag-cache/stats from the legacy JSON trio into ``db``
    (a gm.db.LibraryDB). Safe to call on every startup."""
    res = MigrationResult()
    if db.meta_get("json_migration") == "done":
        res.status = "already"
        try:
            res.tracks = db.track_count()
            res.message = "migration previously verified"
        except Exception:                            # pragma: no cover
            pass
        return res

    config_file = Path(config_file)
    tag_cache_path = config_file.parent / "tag_cache.json"
    stats_path = config_file.parent / "stats.json"

    cfg, ok = load_json_file(config_file, "config")
    cfg = cfg or {}
    if not ok and not config_file.is_file():
        res.status = "not_needed"          # fresh install
        db.meta_set("json_migration", "done")
        db.meta_set("json_migration_at", str(time.time()))
        res.message = "fresh install — no legacy data"
        return res

    library = [p for p in (cfg.get("library") or []) if isinstance(p, str)]
    favorites = [p for p in (cfg.get("favorites") or []) if isinstance(p, str)]
    playlists = {k: [p for p in v if isinstance(p, str)]
                 for k, v in (cfg.get("playlists") or {}).items()
                 if isinstance(k, str) and isinstance(v, list)}
    tags = load_tag_cache_file(tag_cache_path) if tag_cache_path.is_file() else {}
    stats = _read_stats(stats_path) if stats_path.is_file() else {}
    play_counts = {k: int(v) for k, v in
                   (stats.get("play_counts") or {}).items()
                   if isinstance(k, str) and _f(v) > 0}

    res.backup_dir = str(_backup_sources(config_file, tag_cache_path,
                                         stats_path))

    # -------------------------------------------------- single atomic transaction
    conn = db._conn()
    with db._write_lock:
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.cursor()
            # wipe only the tables this migration owns (retry-safe)
            for table in ("tracks", "favorites", "playlists",
                          "playlist_tracks", "play_events"):
                cur.execute(f"DELETE FROM {table}")  # nosec B608
            cur.execute("DELETE FROM tracks_fts")
            cur.execute("DELETE FROM meta WHERE key IN "
                        "('total_seconds','stats_legacy','json_migration',"
                        "'json_migration_at')")

            now = time.time()
            rows = []
            for p in library:
                title, artist = tags.get(p, ("", ""))
                rows.append({
                    "path": p,
                    "title": title or "",
                    "artist": artist or "",
                    "play_count": play_counts.get(p, 0),
                    "added_at": now,
                    "updated_at": now,
                })
            # tracks referenced only via tag_cache/stats/favorites/playlists
            # are preserved too (never lose data):
            extra = (set(tags) | set(play_counts) | set(favorites)
                     | {p for ps in playlists.values() for p in ps})
            extra -= set(library)
            for p in sorted(extra):
                title, artist = tags.get(p, ("", ""))
                rows.append({
                    "path": p, "title": title or "", "artist": artist or "",
                    "play_count": play_counts.get(p, 0),
                    "added_at": now, "updated_at": now,
                })
            cols = ("path, title, artist, play_count, added_at, updated_at")
            cur.executemany(
                f"INSERT OR REPLACE INTO tracks({cols}) VALUES(?,?,?,?,?,?)",
                [(r["path"], r["title"], r["artist"], r["play_count"],
                  r["added_at"], r["updated_at"]) for r in rows])

            cur.executemany(
                "INSERT OR REPLACE INTO favorites(path,category) VALUES(?,?)",
                [(p, "") for p in favorites])

            playlist_total = 0
            for name, paths in playlists.items():
                cur.execute(
                    "INSERT INTO playlists(name,kind,smart_query,created_at,"
                    "updated_at) VALUES(?,?,NULL,?,?)",
                    (name, "user", now, now))
                pid = cur.lastrowid
                cur.executemany(
                    "INSERT OR REPLACE INTO playlist_tracks(playlist_id,"
                    "position,path) VALUES(?,?,?)",
                    [(pid, i, p) for i, p in enumerate(paths)])
                playlist_total += len(paths)

            cur.executemany(
                "INSERT INTO play_events(path,played_at,seconds) "
                "VALUES(?,?,?)",
                [(p, now, 0.0) for p, c in play_counts.items() for _ in range(0)])
            # NOTE: historical play *events* are not derivable from the old
            # format; play_counts live on the tracks rows. play_events accrue
            # from 3.0 onward (charts/heatmap start at migration time).

            total_seconds = _f(stats.get("total_seconds"))
            cur.execute(
                "INSERT INTO meta(key,value) VALUES('total_seconds',?)",
                (str(total_seconds),))
            legacy = {k: stats.get(k) for k in STATS_KEYS if k in stats}
            cur.execute(
                "INSERT INTO meta(key,value) VALUES('stats_legacy',?)",
                (json.dumps(legacy, ensure_ascii=False),))

            # ---------------------------------------------------- verification
            checks = []
            v_tracks = cur.execute(
                "SELECT COUNT(*) FROM tracks").fetchone()[0]
            checks.append(("tracks", v_tracks == len(rows),
                           f"{v_tracks}/{len(rows)}"))
            v_favs = cur.execute(
                "SELECT COUNT(*) FROM favorites").fetchone()[0]
            checks.append(("favorites", v_favs == len(favorites),
                           f"{v_favs}/{len(favorites)}"))
            v_pls = cur.execute(
                "SELECT COUNT(*) FROM playlists WHERE kind='user'"
                ).fetchone()[0]
            checks.append(("playlists", v_pls == len(playlists),
                           f"{v_pls}/{len(playlists)}"))
            v_plt = cur.execute(
                "SELECT COUNT(*) FROM playlist_tracks").fetchone()[0]
            checks.append(("playlist_tracks", v_plt == playlist_total,
                           f"{v_plt}/{playlist_total}"))
            v_pc = cur.execute(
                "SELECT COALESCE(SUM(play_count),0) FROM tracks"
                ).fetchone()[0]
            checks.append(("play_counts", v_pc == sum(play_counts.values()),
                           f"{v_pc}/{sum(play_counts.values())}"))
            v_ts = _f(cur.execute(
                "SELECT value FROM meta WHERE key='total_seconds'"
                ).fetchone()[0])
            checks.append(("total_seconds",
                           abs(v_ts - total_seconds) < 0.51,
                           f"{v_ts:.1f}/{total_seconds:.1f}"))

            res.tracks, res.favorites, res.playlists = (
                v_tracks, v_favs, v_pls)
            res.playlist_tracks = v_plt
            res.play_counts = v_pc
            res.total_seconds = v_ts
            res.checks = [(n, ok) for n, ok, _ in checks]

            failed = [c for c in checks if not c[1]]
            if failed:
                conn.rollback()
                res.status = "failed"
                res.message = ("verification failed: "
                               + ", ".join(f"{c[0]} {c[2]}" for c in failed))
                log.error("JSON->SQLite migration aborted: %s", res.message)
                return res

            cur.execute(
                "INSERT INTO meta(key,value) VALUES('json_migration','done')")
            cur.execute(
                "INSERT INTO meta(key,value) VALUES('json_migration_at',?)",
                (str(now),))
            conn.commit()
        except Exception as e:                     # pragma: no cover
            try:
                conn.rollback()
            except Exception:
                pass
            res.status = "failed"
            res.message = f"exception during migration: {e}"
            log.exception("migration failed")
            return res

    # FTS rebuild outside the critical transaction
    try:
        db.rebuild_fts()
    except Exception:                              # pragma: no cover
        log.exception("FTS rebuild after migration failed (retry on next start)")

    res.status = "ok"
    res.message = (f"migrated {res.tracks} tracks, {res.favorites} favorites, "
                   f"{res.playlists} playlists; backup in {res.backup_dir}")
    log.info("JSON->SQLite migration OK: %s", res.message)
    return res
