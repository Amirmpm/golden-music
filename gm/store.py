"""Golden Music 2.3 — LibraryStore: the bridge between the SQLite database
and the MainWindow's in-memory structures.

The window keeps its battle-tested in-memory shapes (library list, favorites
set, playlists dict, tag-cache dict); this store loads them from the database
at startup and writes changes back incrementally. That keeps the 3.0 refactor
surgical while making SQLite the source of truth.

Never-wipe semantics (inherited from v2.x):
  * scratch paths are filtered before any persistence;
  * an empty in-memory library never replaces a non-empty database;
  * removals are explicit (sync_remove), never implied by absence.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from config import config_path
from gm.core import _is_scratch_path
from gm.db import LibraryDB, db_path_for_config
from gm import migration as migration_mod
from gm import smart as smart_mod

log = logging.getLogger("app.store")


def _sandboxed(config_file: Path) -> bool:
    """True when the app's own data dir lives under a scratch root
    (testenv sandbox). The never-wipe scratch filter exists to keep TEST
    paths out of a REAL user's config; inside a sandboxed data dir it must
    not apply, otherwise the sandbox could not exercise persistence at all."""
    return _is_scratch_path(str(config_file))


class LibraryStore:
    def __init__(self, config_file: Path | None = None):
        self.config_file = Path(config_file or config_path())
        self.db = LibraryDB(db_path_for_config(self.config_file))
        self.migration = migration_mod.migrate_json_to_sqlite(
            self.db, self.config_file)
        self.legacy_json_mode = not self.migration.ok
        # Never-wipe scratch filtering is only applied when the data dir
        # itself is NOT a sandbox (see _sandboxed docstring).
        self._filter_scratch = not _sandboxed(self.config_file)
        self._last_library_snapshot: set[str] | None = None

    def _keep(self, path: str) -> bool:
        if not self._filter_scratch:
            return True
        return not _is_scratch_path(path)

    # ------------------------------------------------------------- load side
    def load_library(self) -> list[str]:
        return self.db.all_paths()

    def load_favorites(self) -> set[str]:
        return self.db.favorite_set()

    def load_playlists(self) -> dict[str, list[str]]:
        out = {}
        for pl in self.db.playlists():
            if pl["kind"] == "user":
                out[pl["name"]] = self.db.playlist_tracks(pl["id"])
        return out

    def load_tag_cache(self) -> dict[str, tuple[str, str]]:
        return self.db.tag_pairs()

    def smart_playlists(self) -> list[dict]:
        return [pl for pl in self.db.playlists() if pl["kind"] == "smart"]

    # ----------------------------------------------------------- write side
    def sync_library(self, paths: list[str]) -> tuple[list, list]:
        """Reconcile the DB's track table with the in-memory library.

        Returns (added, removed) path lists. CONSERVATIVE by design:
          * paths missing from ``paths`` are dropped ONLY when their file
            is gone AND their parent directory still exists (offline
            drives keep their rows — dirname missing -> keep);
          * bulk_ingest never touches existing rows (metadata, ratings and
            play counts survive rescans) — only genuinely-new paths are
            inserted.
        """
        clean = [p for p in paths if self._keep(p)]
        new_set = set(clean)
        old_set = set(self.db.all_paths())
        added = [p for p in clean if p not in old_set]
        if added:
            self.db.bulk_ingest([{"path": p, "added_at": time.time()}
                                 for p in added])
        removed = []
        for p in old_set - new_set:
            try:
                if os.path.exists(p):
                    continue          # on-disk: explicit removal only
                if not os.path.isdir(os.path.dirname(os.path.abspath(p))):
                    continue          # offline drive / moved tree: keep
            except OSError:
                continue
            removed.append(p)
        if removed:
            self.db.remove_tracks(removed)
        return added, removed

    def sync_remove(self, paths: list[str]):
        clean = [p for p in paths if self._keep(p)]
        if clean:
            self.db.remove_tracks(clean)

    def persist_favorites(self, favorites: set[str],
                          categories: dict[str, str] | None = None):
        categories = categories or {}
        db_favs = set(self.db.favorite_set())
        want = {p for p in favorites if self._keep(p)}
        for p in db_favs - want:
            self.db.set_favorite(p, False)
        for p in want - db_favs:
            self.db.set_favorite(p, True, categories.get(p, ""))
        # update categories for kept rows
        for p in want & db_favs:
            cat = categories.get(p, "")
            self.db.set_favorite(p, True, cat)

    def persist_playlists(self, playlists: dict[str, list[str]]):
        existing = {pl["name"]: pl for pl in self.db.playlists()
                    if pl["kind"] == "user"}
        for name in existing:
            if name not in playlists:
                self.db.delete_playlist(existing[name]["id"])
        for name, paths in playlists.items():
            pid = self.db.ensure_playlist(name, "user")
            self.db.replace_playlist_tracks(
                pid, [p for p in paths if self._keep(p)])

    def persist_tags(self, tag_cache: dict):
        """Write-through for tag edits/new extractions (title/artist at
        minimum; richer metadata merged when present).

        UPDATE-ONLY by design: the tag cache may contain paths that were
        just removed from the library (entries are dropped lazily), and
        re-inserting them would resurrect deleted tracks. Library
        membership is owned exclusively by sync_library/sync_remove."""
        simple, rich = [], []
        for path, val in tag_cache.items():
            if not self._keep(path):
                continue
            if isinstance(val, (tuple, list)) and len(val) >= 2:
                simple.append((val[0] or "", val[1] or "", path))
            elif isinstance(val, dict):
                d = {"path": path}
                for k in ("title", "artist", "album", "genre", "year",
                          "duration_ms", "bitrate_kbps", "sample_rate",
                          "track_no"):
                    if val.get(k) is not None:
                        d[k] = val[k]
                rich.append(d)
        if simple:
            with self.db._tx() as conn:
                conn.executemany(
                    "UPDATE tracks SET title=?, artist=?, updated_at=? "
                    "WHERE path=?",
                    [(t, a, time.time(), p) for t, a, p in simple])
        if rich:
            with self.db._tx() as conn:
                for d in rich:
                    sets, vals = [], []
                    for c in ("title", "artist", "album", "genre", "year",
                              "duration_ms", "bitrate_kbps", "sample_rate",
                              "track_no"):
                        if c in d:
                            sets.append(f"{c}=?")
                            vals.append(d[c])
                    if sets:
                        sets.append("updated_at=?")
                        vals.append(time.time())
                        vals.append(d["path"])
                        conn.execute(
                            f"UPDATE tracks SET {','.join(sets)} WHERE path=?",  # nosec B608
                            vals)

    # ------------------------------------------------------- smart playlists
    def ensure_smart_playlist(self, key: str) -> bool:
        if key not in smart_mod.BUILTINS:
            return False
        b = smart_mod.BUILTINS[key]
        for pl in self.db.playlists():
            if pl["kind"] == "smart" and pl["smart_query"]:
                spec = smart_mod.validate_spec(pl["smart_query"])
                if spec == smart_mod.validate_spec(b["spec"]) and \
                        pl["name"] == b["label"]:
                    return True
        self.db.ensure_playlist(b["label"], "smart",
                                json_dumps(b["spec"]))
        return True

    def remove_smart_playlists(self):
        for pl in self.db.playlists():
            if pl["kind"] == "smart":
                self.db.delete_playlist(pl["id"])

    def smart_tracks(self, playlist_id: int) -> list[dict]:
        for pl in self.db.playlists():
            if pl["id"] == playlist_id and pl["smart_query"]:
                spec = smart_mod.validate_spec(pl["smart_query"])
                if not spec:
                    return []
                where, args = smart_mod.sql_where(spec)
                return self.db.tracks_where(
                    where, args,
                    order=f"{spec['sort']} {'DESC' if spec['order']=='desc' else 'ASC'}",
                    limit=spec["limit"])
        return []

    def search(self, query: str, limit: int = 2000) -> list[str]:
        return self.db.search_tracks(query, limit)


def json_dumps(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)


# ---------------------------------------------------------------------------
# StatsStore — DB-backed drop-in replacement for the legacy PlayStats engine
# (same method surface; keeps the 70 % credit rule and twin-file resolution).
# ---------------------------------------------------------------------------
import playstats as _legacy


class StatsStore:
    """Same API as legacy PlayStats, but persisted in SQLite."""

    def __init__(self, db: LibraryDB):
        self.db = db
        self._pending_seconds = 0.0

    # -- legacy surface -----------------------------------------------------
    def add_seconds(self, sec: float):
        self._pending_seconds += max(0.0, float(sec))
        if self._pending_seconds >= 5.0:
            self.db.add_listen_seconds(self._pending_seconds)
            self._pending_seconds = 0.0

    def credit_track(self, path: str, seconds: float, artist: str = "",
                     duration_seconds: float = 0.0,
                     listened_seconds: float = 0.0) -> bool:
        ratio_ok = False
        if duration_seconds and duration_seconds > 0:
            ratio_ok = (listened_seconds / duration_seconds) > 0.70
        if not ratio_ok and listened_seconds >= 20:
            ratio_ok = True
        if not ratio_ok:
            return False
        self.db.record_play(path, seconds, artist)
        self.db.add_listen_seconds(max(0.0, float(seconds)))
        return True

    def save(self):
        if self._pending_seconds > 0:
            self.db.add_listen_seconds(self._pending_seconds)
            self._pending_seconds = 0.0

    # -- legacy attribute surface used by the stats page ---------------
    @property
    def play_counts(self) -> dict:
        return {r["path"]: r["play_count"]
                for r in self.db.tracks_where("play_count>0", (),
                                              order=None)}

    @property
    def artist_seconds(self) -> dict:
        out = dict(self._legacy_artist_seconds())
        for artist, seconds in self.db._conn().execute(
                "SELECT t.artist, SUM(pe.seconds) FROM play_events pe "
                "JOIN tracks t ON t.path=pe.path WHERE t.artist<>'' "
                "GROUP BY t.artist"):
            out[artist] = out.get(artist, 0.0) + (seconds or 0.0)
        return out

    def _legacy_artist_seconds(self) -> dict:
        import json as _json
        try:
            blob = self.db.meta_get("stats_legacy")
            if blob:
                legacy = _json.loads(blob)
                v = legacy.get("artist_seconds")
                if isinstance(v, dict):
                    return {str(k): float(x) for k, x in v.items()
                            if isinstance(x, (int, float))}
        except Exception:
            pass
        return {}

    @property
    def sessions(self) -> int:
        try:
            return int(self.db.meta_get("sessions", "0") or 0)
        except ValueError:
            return 0

    def bump_session(self):
        current = self.sessions
        self.db.meta_set("sessions", str(current + 1))

    @property
    def first_played(self) -> str:
        import json as _json
        try:
            blob = self.db.meta_get("stats_legacy")
            if blob:
                v = _json.loads(blob).get("first_played")
                if isinstance(v, str):
                    return v
        except Exception:
            pass
        return ""

    @property
    def _dirty(self) -> bool:
        return self._pending_seconds > 0

    def total_seconds(self) -> float:
        return self.db.total_seconds()

    def top_tracks(self, tag_cache=None, n: int = 10):
        rows = self.db.top_tracks(n)
        out = []
        for r in rows:
            live = r["path"] if os.path.exists(r["path"]) else (
                _legacy._resolve_moved(r["path"]) or "")
            title = r["title"] or Path(r["path"]).stem
            out.append((title, r["artist"], r["play_count"], live))
        return out

    def top_artists(self, n: int = 5):
        rows = self.db.top_artists(n)
        return [(r[0], _legacy.fmt_total(r[2] or 0)) for r in rows]

    def prune_missing(self, library_set) -> int:
        return self.db.prune_missing(set(library_set))

    @staticmethod
    def fmt_total(seconds: float) -> str:
        return _legacy.PlayStats.fmt_total(seconds)
