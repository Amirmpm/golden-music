"""Golden Music 3.0 — SQLite library database (FTS5 search).

Design (see docs/DECISIONS.md #d1):
  * One database file next to the old JSON files: ``library.db`` inside the
    config dir (portable-mode aware, sandbox-aware — same rules as
    ``config_path()``). Settings *stay* in config.json: they are small,
    human-editable and already covered by the existing never-wipe guards.
  * WAL journal + NORMAL sync: safe enough for a media library, fast for the
    100k-track target, and lets a background writer thread commit without
    blocking the UI.
  * One connection per thread (SQLite default); all writes go through a
    single lock so background threads can batch safely.
  * FTS5 is maintained explicitly (single-row ops + ``rebuild_fts()`` after
    bulk operations) with a dirty flag in ``meta`` so a crash mid-bulk can
    never leave silently stale search results.
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Iterable, Sequence

SCHEMA_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS tracks (
    path           TEXT PRIMARY KEY,
    title          TEXT NOT NULL DEFAULT '',
    artist         TEXT NOT NULL DEFAULT '',
    album          TEXT NOT NULL DEFAULT '',
    genre          TEXT NOT NULL DEFAULT '',
    year           INTEGER,
    track_no       INTEGER,
    disc_no        INTEGER,
    duration_ms    INTEGER,
    bitrate_kbps   INTEGER,
    sample_rate    INTEGER,
    channels       INTEGER,
    rating         INTEGER NOT NULL DEFAULT 0,     -- 0..5 stars
    play_count     INTEGER NOT NULL DEFAULT 0,
    skip_count     INTEGER NOT NULL DEFAULT 0,
    last_played_at REAL,
    added_at       REAL    NOT NULL DEFAULT 0,
    mtime          REAL,
    filesize       INTEGER,
    updated_at     REAL
);
CREATE INDEX IF NOT EXISTS idx_tracks_artist ON tracks(artist);
CREATE INDEX IF NOT EXISTS idx_tracks_album  ON tracks(album);
CREATE INDEX IF NOT EXISTS idx_tracks_genre  ON tracks(genre);
CREATE INDEX IF NOT EXISTS idx_tracks_added  ON tracks(added_at);
CREATE INDEX IF NOT EXISTS idx_tracks_plays ON tracks(play_count);
CREATE VIRTUAL TABLE IF NOT EXISTS tracks_fts USING fts5(
    title, artist, album, genre, path,
    content='tracks', content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TABLE IF NOT EXISTS favorites (
    path     TEXT PRIMARY KEY,
    category TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS playlists (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL DEFAULT 'user',     -- user | smart | radio
    smart_query TEXT,
    created_at  REAL,
    updated_at  REAL
);
CREATE TABLE IF NOT EXISTS playlist_tracks (
    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    path        TEXT NOT NULL,
    PRIMARY KEY (playlist_id, position)
);
CREATE TABLE IF NOT EXISTS play_events (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    path     TEXT NOT NULL,
    played_at REAL NOT NULL,
    seconds  REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_play_events_time ON play_events(played_at);
CREATE INDEX IF NOT EXISTS idx_play_events_path ON play_events(path);
CREATE TABLE IF NOT EXISTS bookmarks (
    path        TEXT PRIMARY KEY,
    position_ms INTEGER NOT NULL DEFAULT 0,
    note        TEXT,
    updated_at  REAL
);
CREATE TABLE IF NOT EXISTS lyrics_state (
    path        TEXT PRIMARY KEY,
    offset_ms   INTEGER NOT NULL DEFAULT 0,
    confirmed   INTEGER NOT NULL DEFAULT 0,
    updated_at  REAL
);
CREATE TABLE IF NOT EXISTS radio_stations (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL,
    url       TEXT NOT NULL,
    homepage  TEXT,
    genre     TEXT,
    added_at  REAL
);
CREATE TABLE IF NOT EXISTS waveform_cache (
    path        TEXT PRIMARY KEY,
    duration_ms INTEGER,
    peaks       BLOB,
    generated_at REAL,
    engine      TEXT
);
CREATE TABLE IF NOT EXISTS replaygain_cache (
    path          TEXT PRIMARY KEY,
    track_gain_db REAL, album_gain_db REAL,
    track_peak    REAL, album_peak    REAL,
    analyzed_at   REAL, source        TEXT
);
"""


class LibraryDB:
    """Thread-safe wrapper around the library database."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._local = threading.local()
        self._write_lock = threading.RLock()
        self._open_connections: list[sqlite3.Connection] = []
        self._conn_guard = threading.Lock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._tx():
            self._conn().executescript(SCHEMA)
            cur = self._conn().execute(
                "SELECT value FROM meta WHERE key='schema_version'")
            row = cur.fetchone()
            if row is None or int(row[0]) < SCHEMA_VERSION:
                self._conn().execute(
                    "INSERT INTO meta(key,value) VALUES('schema_version',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(SCHEMA_VERSION),))

    # ------------------------------------------------------------- connections
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.db_path), timeout=30.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA temp_store=MEMORY")
            self._local.conn = conn
            with self._conn_guard:
                self._open_connections.append(conn)
        return conn

    class _Tx:
        def __init__(self, db: "LibraryDB"):
            self.db = db

        def __enter__(self):
            self.db._write_lock.acquire()
            self.conn = self.db._conn()
            self.conn.execute("BEGIN IMMEDIATE")
            return self.conn

        def __exit__(self, exc_type, exc, tb):
            try:
                if exc_type is None:
                    self.conn.commit()
                else:
                    self.conn.rollback()
            finally:
                self.db._write_lock.release()
            return False

    def _tx(self) -> "LibraryDB._Tx":
        return LibraryDB._Tx(self)

    def close(self):
        with self._conn_guard:
            for c in self._open_connections:
                try:
                    c.close()
                except Exception:
                    pass
            self._open_connections.clear()

    # ------------------------------------------------------------------- meta
    def meta_get(self, key: str, default=None):
        row = self._conn().execute(
            "SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return default if row is None else row[0]

    def meta_set(self, key: str, value: str):
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)))

    # ----------------------------------------------------------------- tracks
    TRACK_COLS = ("path, title, artist, album, genre, year, track_no, disc_no,"
                  " duration_ms, bitrate_kbps, sample_rate, channels, rating,"
                  " play_count, skip_count, last_played_at, added_at, mtime,"
                  " filesize, updated_at")

    def upsert_tracks(self, rows: Iterable[Sequence]) -> int:
        """Insert/update tracks. Each row: dict-like mapping with at least
        ``path``. Unknown keys are ignored; missing keys keep old values."""
        now = time.time()
        n = 0
        with self._tx() as conn:
            for row in rows:
                d = dict(row)
                path = d.get("path")
                if not path:
                    continue
                existing = conn.execute(
                    "SELECT * FROM tracks WHERE path=?", (path,)).fetchone()
                if existing is None:
                    d.setdefault("added_at", now)
                    d["updated_at"] = now
                    cols = [c for c in self._track_columns() if c in d]
                    vals = [d[c] for c in cols]
                    conn.execute(
                        f"INSERT INTO tracks({','.join(cols)}) "
                        f"VALUES({','.join('?' * len(cols))})", vals)
                    conn.execute(
                        "INSERT INTO tracks_fts(rowid,title,artist,album,"
                        "genre,path) VALUES((SELECT rowid FROM tracks WHERE "
                        "path=?),?,?,?,?,?)",
                        (path, d.get("title", ""), d.get("artist", ""),
                         d.get("album", ""), d.get("genre", ""), path))
                else:
                    d.pop("added_at", None)      # never clobber added_at
                    d.pop("play_count", None)    # stats-owned columns
                    d.pop("rating", None)
                    d["updated_at"] = now
                    sets, vals = [], []
                    for c in self._track_columns():
                        if c in d:
                            sets.append(f"{c}=?")
                            vals.append(d[c])
                    if sets:
                        vals.append(path)
                        conn.execute(
                            f"UPDATE tracks SET {','.join(sets)} WHERE path=?",
                            vals)
                        conn.execute(
                            "INSERT INTO tracks_fts(tracks_fts,rowid,title,"
                            "artist,album,genre,path) VALUES('delete',"
                            "(SELECT rowid FROM tracks WHERE path=?),"
                            "?,?,?,?,?)",
                            (path, existing["title"], existing["artist"],
                             existing["album"], existing["genre"], path))
                        conn.execute(
                            "INSERT INTO tracks_fts(rowid,title,artist,album,"
                            "genre,path) VALUES((SELECT rowid FROM tracks "
                            "WHERE path=?),?,?,?,?,?)",
                            (path, d.get("title", existing["title"]),
                             d.get("artist", existing["artist"]),
                             d.get("album", existing["album"]),
                             d.get("genre", existing["genre"]), path))
                n += 1
        return n

    @staticmethod
    def _track_columns() -> list[str]:
        return ["path", "title", "artist", "album", "genre", "year",
                "track_no", "disc_no", "duration_ms", "bitrate_kbps",
                "sample_rate", "channels", "rating", "play_count",
                "skip_count", "last_played_at", "added_at", "mtime",
                "filesize", "updated_at"]

    def bulk_ingest(self, rows: Iterable[Sequence]) -> int:
        """Fast path for initial migration / big rescans (plain dicts with
        all simple columns; FTS rebuilt once at the end)."""
        cols = [c for c in self._track_columns()
                if c not in ("updated_at",)]
        placeholder = "(" + ",".join("?" * len(cols)) + ")"
        n = 0
        with self._tx() as conn:
            conn.execute("INSERT INTO meta(key,value) "
                         "VALUES('fts_dirty','1') "
                         "ON CONFLICT(key) DO UPDATE SET value='1'")
            for chunk in _chunked(rows, 2000):
                payload = []
                for d in chunk:
                    dd = dict(d)
                    dd["updated_at"] = time.time()
                    payload.append(tuple(dd.get(c) for c in cols))
                conn.executemany(
                    f"INSERT OR REPLACE INTO tracks({','.join(cols)}) "
                    f"VALUES {placeholder}", payload)
                n += len(payload)
            # preserve first-seen added_at semantics on re-ingest handled by
            # caller (migration passes original data through).
        self.rebuild_fts()
        return n

    def rebuild_fts(self):
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO tracks_fts(tracks_fts) VALUES('rebuild')")
            conn.execute(
                "INSERT INTO meta(key,value) VALUES('fts_dirty','0') "
                "ON CONFLICT(key) DO UPDATE SET value='0'")

    def remove_tracks(self, paths: Iterable[str]) -> int:
        paths = list(paths)
        if not paths:
            return 0
        with self._tx() as conn:
            for p in paths:
                row = conn.execute(
                    "SELECT rowid,title,artist,album,genre FROM tracks "
                    "WHERE path=?", (p,)).fetchone()
                if row is None:
                    continue
                conn.execute(
                    "INSERT INTO tracks_fts(tracks_fts,rowid,title,artist,"
                    "album,genre,path) VALUES('delete',?,?,?,?,?,?)",
                    (row["rowid"], row["title"], row["artist"],
                     row["album"], row["genre"], p))
                conn.execute("DELETE FROM tracks WHERE path=?", (p,))
                conn.execute("DELETE FROM favorites WHERE path=?", (p,))
                conn.execute(
                    "DELETE FROM playlist_tracks WHERE path=?", (p,))
        return len(paths)

    def all_paths(self) -> list[str]:
        return [r[0] for r in self._conn().execute(
            "SELECT path FROM tracks ORDER BY path")]

    def all_tracks(self) -> list[dict]:
        return [dict(r) for r in self._conn().execute(
            f"SELECT {self.TRACK_COLS} FROM tracks ORDER BY path")]

    def get_track(self, path: str) -> dict | None:
        row = self._conn().execute(
            f"SELECT {self.TRACK_COLS} FROM tracks WHERE path=?",
            (path,)).fetchone()
        return dict(row) if row else None

    def tag_pairs(self) -> dict[str, tuple[str, str]]:
        """Legacy ``tag_cache`` shape: path -> (title, artist)."""
        out = {}
        for r in self._conn().execute(
                "SELECT path,title,artist FROM tracks"):
            out[r["path"]] = (r["title"], r["artist"])
        return out

    # --------------------------------------------------------------- searchers
    _FTS_SAFE = re.compile(r'^[()\s"*$^:]')

    @staticmethod
    def fts_query(query: str) -> str:
        """Turn user input into a safe FTS5 prefix query."""
        query = (query or "").strip()
        if not query:
            return ""
        # strip FTS operators users didn't mean to type
        for ch in '"*()':
            query = query.replace(ch, " ")
        terms = [t for t in re.split(r"\s+", query) if t]
        if not terms:
            return ""
        return " ".join(f"{t}*" for t in terms[:8])

    def search_tracks(self, query: str, limit: int = 500) -> list[str]:
        """FTS5 search across title/artist/album/genre; falls back to LIKE
        when FTS is unavailable. Returns matching paths."""
        q = self.fts_query(query)
        if not q:
            return []
        try:
            rows = self._conn().execute(
                "SELECT path FROM tracks_fts WHERE tracks_fts MATCH ? "
                "ORDER BY rank LIMIT ?", (q, limit)).fetchall()
            return [r[0] for r in rows]
        except sqlite3.OperationalError:
            pass
        like = f"%{query.strip()}%"
        return [r[0] for r in self._conn().execute(
            "SELECT path FROM tracks WHERE title LIKE ? OR artist LIKE ? OR "
            "album LIKE ? OR genre LIKE ? LIMIT ?",
            (like, like, like, like, limit))]

    def artist_counts(self) -> list[tuple[str, int]]:
        return [(r[0], r[1]) for r in self._conn().execute(
            "SELECT artist, COUNT(*) c FROM tracks WHERE artist<>'' "
            "GROUP BY artist ORDER BY c DESC, artist")]

    def genre_counts(self) -> list[tuple[str, int]]:
        return [(r[0], r[1]) for r in self._conn().execute(
            "SELECT genre, COUNT(*) c FROM tracks WHERE genre<>'' "
            "GROUP BY genre ORDER BY c DESC, genre")]

    def album_groups(self) -> list[dict]:
        """One row per album with a representative path (for cover load)."""
        rows = self._conn().execute(
            "SELECT album, artist, COUNT(*) n, MIN(path) rep, "
            "SUM(duration_ms) dur FROM tracks WHERE album<>'' "
            "GROUP BY album, artist ORDER BY album COLLATE NOCASE").fetchall()
        return [dict(r) for r in rows]

    def tracks_where(self, where: str = "", args: Sequence = (),
                     order: str = "title COLLATE NOCASE",
                     limit: int | None = None) -> list[dict]:
        sql = f"SELECT {self.TRACK_COLS} FROM tracks"
        if where:
            sql += f" WHERE {where}"
        if order:
            sql += f" ORDER BY {order}"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [dict(r) for r in self._conn().execute(sql, tuple(args))]

    # ------------------------------------------------------ ratings & favorites
    def set_rating(self, path: str, rating: int) -> bool:
        rating = max(0, min(5, int(rating)))
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE tracks SET rating=?, updated_at=? WHERE path=?",
                (rating, time.time(), path))
            if cur.rowcount == 0:
                conn.execute(
                    "INSERT INTO tracks(path,rating,added_at,updated_at) "
                    "VALUES(?,?,?,?)", (path, rating, time.time(), time.time()))
        return True

    def set_favorite(self, path: str, favorite: bool, category: str = ""):
        with self._tx() as conn:
            if favorite:
                conn.execute(
                    "INSERT INTO favorites(path,category) VALUES(?,?) "
                    "ON CONFLICT(path) DO UPDATE SET category=excluded.category",
                    (path, category or ""))
            else:
                conn.execute("DELETE FROM favorites WHERE path=?", (path,))

    def favorites(self) -> list[tuple[str, str]]:
        return [(r[0], r[1]) for r in self._conn().execute(
            "SELECT path,category FROM favorites ORDER BY path")]

    def favorite_set(self) -> set[str]:
        return {r[0] for r in self._conn().execute("SELECT path FROM favorites")}

    def favorite_categories(self) -> list[str]:
        return [r[0] for r in self._conn().execute(
            "SELECT DISTINCT category FROM favorites WHERE category<>'' "
            "ORDER BY category")]

    # -------------------------------------------------------------- playlists
    def playlists(self) -> list[dict]:
        return [dict(r) for r in self._conn().execute(
            "SELECT id,name,kind,smart_query,created_at,updated_at "
            "FROM playlists ORDER BY name COLLATE NOCASE")]

    def playlist_tracks(self, playlist_id: int) -> list[str]:
        return [r[0] for r in self._conn().execute(
            "SELECT path FROM playlist_tracks WHERE playlist_id=? "
            "ORDER BY position", (playlist_id,))]

    def ensure_playlist(self, name: str, kind: str = "user",
                        smart_query: str | None = None) -> int:
        now = time.time()
        with self._tx() as conn:
            row = conn.execute(
                "SELECT id FROM playlists WHERE name=?", (name,)).fetchone()
            if row:
                return row["id"]
            cur = conn.execute(
                "INSERT INTO playlists(name,kind,smart_query,created_at,"
                "updated_at) VALUES(?,?,?,?,?)",
                (name, kind, smart_query, now, now))
            return cur.lastrowid

    def rename_playlist(self, playlist_id: int, new_name: str) -> bool:
        with self._tx() as conn:
            cur = conn.execute(
                "UPDATE playlists SET name=?, updated_at=? WHERE id=?",
                (new_name, time.time(), playlist_id))
            return cur.rowcount > 0

    def delete_playlist(self, playlist_id: int) -> bool:
        with self._tx() as conn:
            cur = conn.execute(
                "DELETE FROM playlists WHERE id=?", (playlist_id,))
            return cur.rowcount > 0

    def replace_playlist_tracks(self, playlist_id: int, paths: Sequence[str]):
        with self._tx() as conn:
            conn.execute(
                "DELETE FROM playlist_tracks WHERE playlist_id=?",
                (playlist_id,))
            conn.executemany(
                "INSERT OR REPLACE INTO playlist_tracks(playlist_id,position,"
                "path) VALUES(?,?,?)",
                [(playlist_id, i, p) for i, p in enumerate(paths)])
            conn.execute(
                "UPDATE playlists SET updated_at=? WHERE id=?",
                (time.time(), playlist_id))

    def append_playlist_tracks(self, playlist_id: int, paths: Sequence[str]):
        if not paths:
            return
        with self._tx() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(position),-1)+1 AS next FROM "
                "playlist_tracks WHERE playlist_id=?",
                (playlist_id,)).fetchone()
            start = row["next"]
            conn.executemany(
                "INSERT OR REPLACE INTO playlist_tracks(playlist_id,position,"
                "path) VALUES(?,?,?)",
                [(playlist_id, start + i, p) for i, p in enumerate(paths)])

    # ------------------------------------------------------------- play stats
    def record_play(self, path: str, seconds: float, artist: str = "") -> bool:
        """Returns True when this credit incremented play_count (70%-rule
        decision is made by the caller — mirrors legacy PlayStats)."""
        now = time.time()
        with self._tx() as conn:
            row = conn.execute(
                "SELECT play_count FROM tracks WHERE path=?", (path,)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO tracks(path,play_count,last_played_at,"
                    "added_at,updated_at) VALUES(?,?,?,?,?)",
                    (path, 1, now, now, now))
                counted = True
            else:
                conn.execute(
                    "UPDATE tracks SET play_count=play_count+1,"
                    "last_played_at=?,updated_at=? WHERE path=?",
                    (now, now, path))
                counted = True
            conn.execute(
                "INSERT INTO play_events(path,played_at,seconds) "
                "VALUES(?,?,?)", (path, now, max(0.0, float(seconds))))
            conn.execute(
                "INSERT INTO meta(key,value) VALUES('total_seconds',?) "
                "ON CONFLICT(key) DO UPDATE SET value="
                "CAST(CAST(value AS REAL)+? AS TEXT)",
                (str(max(0.0, float(seconds))), str(max(0.0, float(seconds)))))
        return counted

    def add_listen_seconds(self, seconds: float):
        if seconds <= 0:
            return
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO meta(key,value) VALUES('total_seconds',?) "
                "ON CONFLICT(key) DO UPDATE SET value="
                "CAST(CAST(value AS REAL)+? AS TEXT)",
                (str(max(0.0, float(seconds))), str(max(0.0, float(seconds)))))

    def total_seconds(self) -> float:
        try:
            return float(self.meta_get("total_seconds", "0") or 0)
        except ValueError:
            return 0.0

    def prune_missing(self, existing: set[str]) -> int:
        """Drop play_events/counts for paths no longer anywhere on disk.
        Never deletes tracks rows themselves (library-owned)."""
        gone = 0
        all_paths = set(self.all_paths())
        for p in all_paths - existing:
            tr = self.get_track(p)
            if tr and tr["play_count"]:
                with self._tx() as conn:
                    conn.execute(
                        "UPDATE tracks SET play_count=0, skip_count=0 WHERE "
                        "path=?", (p,))
                gone += 1
        return gone

    # -------------------------------------------------- stats queries (charts)
    def plays_over_time(self, days: int = 30) -> list[tuple[str, int]]:
        since = time.time() - days * 86400
        rows = self._conn().execute(
            "SELECT date(played_at,'unixepoch','localtime') d, COUNT(*) c "
            "FROM play_events WHERE played_at>=? GROUP BY d ORDER BY d",
            (since,)).fetchall()
        return [(r[0], r[1]) for r in rows]

    def minutes_by_month(self, months: int = 12) -> list[tuple[str, float]]:
        rows = self._conn().execute(
            "SELECT strftime('%Y-%m', played_at,'unixepoch','localtime') m, "
            "SUM(seconds)/60.0 c FROM play_events GROUP BY m ORDER BY m "
            "LIMIT ?", (months,)).fetchall()
        return [(r[0], r[1]) for r in rows]

    def daily_heatmap(self, days: int = 365) -> list[tuple[str, float]]:
        since = time.time() - days * 86400
        rows = self._conn().execute(
            "SELECT date(played_at,'unixepoch','localtime') d, "
            "SUM(seconds) s FROM play_events WHERE played_at>=? GROUP BY d "
            "ORDER BY d", (since,)).fetchall()
        return [(r[0], r[1]) for r in rows]

    def top_tracks(self, n: int = 10) -> list[dict]:
        return [dict(r) for r in self._conn().execute(
            f"SELECT {self.TRACK_COLS} FROM tracks WHERE play_count>0 "
            "ORDER BY play_count DESC, last_played_at DESC LIMIT ?", (n,))]

    def top_artists(self, n: int = 10) -> list[tuple[str, int, float]]:
        return [(r[0], r[1], r[2]) for r in self._conn().execute(
            "SELECT t.artist, SUM(t.play_count) c, "
            "(SELECT SUM(seconds) FROM play_events pe "
            " JOIN tracks t2 ON t2.path=pe.path WHERE t2.artist=t.artist) s "
            "FROM tracks t WHERE t.artist<>'' GROUP BY t.artist "
            "ORDER BY c DESC LIMIT ?", (n,))]

    def top_genres(self, n: int = 10) -> list[tuple[str, int]]:
        return [(r[0], r[1]) for r in self._conn().execute(
            "SELECT genre, SUM(play_count) c FROM tracks WHERE genre<>'' "
            "GROUP BY genre ORDER BY c DESC LIMIT ?", (n,))]

    # --------------------------------------------------------------- bookmarks
    def set_bookmark(self, path: str, position_ms: int, note: str | None = None):
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO bookmarks(path,position_ms,note,updated_at) "
                "VALUES(?,?,?,?) ON CONFLICT(path) DO UPDATE SET "
                "position_ms=excluded.position_ms, note=excluded.note, "
                "updated_at=excluded.updated_at",
                (path, int(position_ms), note, time.time()))

    def get_bookmark(self, path: str) -> int | None:
        row = self._conn().execute(
            "SELECT position_ms FROM bookmarks WHERE path=?",
            (path,)).fetchone()
        return None if row is None else int(row[0])

    def all_bookmarks(self) -> list[dict]:
        return [dict(r) for r in self._conn().execute(
            "SELECT path,position_ms,note,updated_at FROM bookmarks "
            "ORDER BY updated_at DESC")]

    def delete_bookmark(self, path: str):
        with self._tx() as conn:
            conn.execute("DELETE FROM bookmarks WHERE path=?", (path,))

    # ------------------------------------------------------------ radio / misc
    def add_radio(self, name: str, url: str, homepage: str = "",
                  genre: str = "") -> int:
        with self._tx() as conn:
            cur = conn.execute(
                "INSERT INTO radio_stations(name,url,homepage,genre,added_at)"
                " VALUES(?,?,?,?,?)",
                (name, url, homepage, genre, time.time()))
            return cur.lastrowid

    def radio_stations(self) -> list[dict]:
        return [dict(r) for r in self._conn().execute(
            "SELECT id,name,url,homepage,genre,added_at FROM radio_stations "
            "ORDER BY name COLLATE NOCASE")]

    def remove_radio(self, station_id: int) -> bool:
        with self._tx() as conn:
            cur = conn.execute(
                "DELETE FROM radio_stations WHERE id=?", (station_id,))
            return cur.rowcount > 0

    def set_lyrics_offset(self, path: str, offset_ms: int):
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO lyrics_state(path,offset_ms,confirmed,updated_at)"
                " VALUES(?,?,1,?) ON CONFLICT(path) DO UPDATE SET "
                "offset_ms=excluded.offset_ms, confirmed=1, "
                "updated_at=excluded.updated_at",
                (path, int(offset_ms), time.time()))

    def lyrics_offset(self, path: str) -> int:
        row = self._conn().execute(
            "SELECT offset_ms FROM lyrics_state WHERE path=?",
            (path,)).fetchone()
        return 0 if row is None else int(row[0])

    # --------------------------------------------------- waveform / replaygain
    def store_waveform(self, path: str, duration_ms: int, peaks: bytes,
                       engine: str):
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO waveform_cache(path,duration_ms,peaks,"
                "generated_at,engine) VALUES(?,?,?,?,?) "
                "ON CONFLICT(path) DO UPDATE SET duration_ms=excluded."
                "duration_ms, peaks=excluded.peaks, generated_at=excluded."
                "generated_at, engine=excluded.engine",
                (path, int(duration_ms), peaks, time.time(), engine))

    def get_waveform(self, path: str) -> bytes | None:
        row = self._conn().execute(
            "SELECT peaks FROM waveform_cache WHERE path=?",
            (path,)).fetchone()
        return None if row is None else row[0]

    def store_replaygain(self, path: str, track_gain_db, album_gain_db,
                         track_peak, album_peak, source: str):
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO replaygain_cache(path,track_gain_db,album_gain_db,"
                "track_peak,album_peak,analyzed_at,source) VALUES(?,?,?,?,?,?,?)"
                " ON CONFLICT(path) DO UPDATE SET "
                "track_gain_db=excluded.track_gain_db, album_gain_db=excluded."
                "album_gain_db, track_peak=excluded.track_peak, album_peak="
                "excluded.album_peak, analyzed_at=excluded.analyzed_at, "
                "source=excluded.source",
                (path, track_gain_db, album_gain_db, track_peak, album_peak,
                 time.time(), source))

    def get_replaygain(self, path: str) -> dict | None:
        row = self._conn().execute(
            "SELECT track_gain_db,album_gain_db,track_peak,album_peak,source "
            "FROM replaygain_cache WHERE path=?", (path,)).fetchone()
        return dict(row) if row else None

    def track_count(self) -> int:
        return int(self._conn().execute(
            "SELECT COUNT(*) FROM tracks").fetchone()[0])


def _chunked(it: Iterable, size: int):
    buf = []
    for item in it:
        buf.append(item)
        if len(buf) >= size:
            yield buf
            buf = []
    if buf:
        yield buf


def db_path_for_config(config_file: Path) -> Path:
    """library.db lives beside config.json (portable/sandbox aware)."""
    return Path(config_file).parent / "library.db"
