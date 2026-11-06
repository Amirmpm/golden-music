"""Offline tests for the v3.0 SQLite layer: migration safety, FTS search,
smart playlists, store facade, stats store.

Run:  python scripts/test_sqlite.py   (no Qt, no network, sandboxed tmp dirs)
"""
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gm.db import LibraryDB, db_path_for_config
from gm.migration import migrate_json_to_sqlite
from gm.store import LibraryStore, StatsStore
from gm import smart

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


def make_legacy_home(tmp: Path) -> Path:
    """Build a fake v2.x user dir with config/tag_cache/stats JSON."""
    home = tmp / "home"
    (home / ".goldenmusic").mkdir(parents=True)
    gd = home / ".goldenmusic"
    tracks = []
    for i in range(12):
        p = tmp / "music" / f"Artist{i % 3} - Song{i:02d}.mp3"
        p.parent.mkdir(exist_ok=True)
        p.write_bytes(b"FAKE" * 10)
        tracks.append(str(p))
    cfg = {
        "version": "2.2.0",
        "theme": "royal_gold",
        "library": tracks[:10],
        "favorites": tracks[:3],
        "playlists": {"Mix": tracks[4:8], "Empty": []},
        "volume": 80,
        "last_track": tracks[0],
    }
    (gd / "config.json").write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    tags = {p: [f"Song {i:02d}", f"Artist{i % 3}"]
            for i, p in enumerate(tracks)}
    (gd / "tag_cache.json").write_text(
        json.dumps(tags, ensure_ascii=False), encoding="utf-8")
    stats = {
        "total_seconds": 12345.6,
        "play_counts": {tracks[0]: 7, tracks[1]: 3, tracks[9]: 1,
                        str(tmp / "music" / "gone.mp3"): 2},
        "artist_seconds": {"Artist0": 5000.0},
        "sessions": 42,
        "first_played": "2024-01-01",
    }
    (gd / "stats.json").write_text(json.dumps(stats), encoding="utf-8")
    return home


def test_migration_success():
    print("=== migration: happy path ===")
    with tempfile.TemporaryDirectory(prefix="gmdb_") as td:
        tmp = Path(td)
        home = make_legacy_home(tmp)
        os.environ["HOME"] = str(home)
        cfg_file = home / ".goldenmusic" / "config.json"
        db = LibraryDB(db_path_for_config(cfg_file))
        res = migrate_json_to_sqlite(db, cfg_file)
        check("status ok", res.status == "ok", res.message)
        # 10 library + t10/t11 (tag-cache only) + gone.mp3 (play-count only)
        check("tracks migrated incl. referenced-only paths",
              db.track_count() == 13, str(db.track_count()))
        check("favorites migrated", len(db.favorite_set()) == 3)
        pls = {p["name"]: p for p in db.playlists()}
        check("playlists migrated", set(pls) == {"Mix", "Empty"})
        check("playlist tracks", len(db.playlist_tracks(pls["Mix"]["id"])) == 4)
        t = db.get_track(str(tmp / "music" / "Artist0 - Song00.mp3"))
        check("play_count preserved", t["play_count"] == 7)
        check("title/artist from tag cache", t["title"] == "Song 00")
        check("total_seconds preserved",
              abs(db.total_seconds() - 12345.6) < 0.01, str(db.total_seconds()))
        check("backup created", res.backup_dir != "" and
              Path(res.backup_dir).is_dir())
        check("originals untouched",
              (home / ".goldenmusic" / "config.json").is_file() and
              (home / ".goldenmusic" / "tag_cache.json").is_file() and
              (home / ".goldenmusic" / "stats.json").is_file())
        # idempotency
        res2 = migrate_json_to_sqlite(db, cfg_file)
        check("second run -> already", res2.status == "already", res2.status)
        check("idempotent track count", db.track_count() == 13)
        t2 = db.get_track(str(tmp / "music" / "Artist0 - Song00.mp3"))
        check("idempotent play_count", t2["play_count"] == 7)


def test_migration_fresh_install():
    print("=== migration: fresh install ===")
    with tempfile.TemporaryDirectory(prefix="gmdb_") as td:
        tmp = Path(td)
        gd = tmp / "home" / ".goldenmusic"
        gd.mkdir(parents=True)
        cfg_file = gd / "config.json"
        db = LibraryDB(db_path_for_config(cfg_file))
        res = migrate_json_to_sqlite(db, cfg_file)
        check("fresh -> not_needed", res.status == "not_needed", res.status)
        check("fresh: 0 tracks", db.track_count() == 0)


def test_migration_failure_rollback():
    print("=== migration: corrupt source -> failed, DB left un-flagged ===")
    with tempfile.TemporaryDirectory(prefix="gmdb_") as td:
        tmp = Path(td)
        gd = tmp / "home" / ".goldenmusic"
        gd.mkdir(parents=True)
        # config.json that fails validation (not a dict at root)
        (gd / "config.json").write_text('["not", "a", "dict"]',
                                        encoding="utf-8")
        cfg_file = gd / "config.json"
        db = LibraryDB(db_path_for_config(cfg_file))
        res = migrate_json_to_sqlite(db, cfg_file)
        # non-dict root: load_json_file returns None -> treated as empty cfg
        check("handled without crash", res.status in
              ("ok", "failed", "not_needed"), res.status)
        check("db still usable", db.track_count() >= 0)
        # exception mid-transaction -> rollback, failed, flag unset,
        # original JSON files still intact and authoritative
        import gm.db as dbmod
        import gm.migration as migmod

        class SabotageConn:
            """Wraps the real connection; the first cursor works for the
            cleanup DELETEs but blows up on the first executemany (the
            bulk track INSERT), simulating a mid-transaction disk error."""
            def __init__(self, real):
                self._real = real
            def __getattr__(self, name):
                return getattr(self._real, name)
            def cursor(self):
                cur = self._real.cursor()
                class BadCur:
                    def __getattr__(self, n):
                        return getattr(cur, n)
                    def executemany(self, *a, **k):
                        raise sqlite3.DatabaseError("disk sabotage")
                return BadCur()

        home2 = tmp / "home2" / ".goldenmusic"
        home2.mkdir(parents=True)
        (home2 / "config.json").write_text(json.dumps(
            {"library": [str(tmp / "music" / "x1.mp3")]}), encoding="utf-8")
        cfg2 = home2 / "config.json"
        db2 = LibraryDB(home2 / "x.db")
        orig_conn = dbmod.LibraryDB._conn
        dbmod.LibraryDB._conn = lambda self: SabotageConn(orig_conn(db2))
        try:
            res2 = migrate_json_to_sqlite(db2, cfg2)
            check("exception -> failed", res2.status == "failed", res2.status)
            check("failed: flag not set",
                  db2.meta_get("json_migration") != "done")
            check("failed: source JSON intact", cfg2.is_file())
        finally:
            dbmod.LibraryDB._conn = orig_conn
        # and a retry with the sabotage removed succeeds (idempotent retry)
        res3 = migrate_json_to_sqlite(db2, cfg2)
        check("retry after failure succeeds", res3.status == "ok", res3.status)


def test_fts_search():
    print("=== FTS5 search ===")
    with tempfile.TemporaryDirectory(prefix="gmdb_") as td:
        db = LibraryDB(Path(td) / "t.db")
        rows = [
            {"path": "/m/a1.mp3", "title": "Vaveyla", "artist": "Mohammadreza Shajarian", "album": "Bavar", "genre": "Persian Traditional"},
            {"path": "/m/a2.mp3", "title": "Gol-e Sang", "artist": "Googoosh", "album": "Derakht", "genre": "Pop"},
            {"path": "/m/a3.mp3", "title": "Booye Pirahan-e Yusef", "artist": "Googoosh", "album": None, "genre": "Pop"},
            {"path": "/m/a4.mp3", "title": "Back in Black", "artist": "AC/DC", "album": "Back in Black", "genre": "Rock"},
            {"path": "/m/a5.mp3", "title": "The Sound of Silence", "artist": "Simon & Garfunkel", "album": None, "genre": "Folk"},
        ]
        db.bulk_ingest(rows)
        r = db.search_tracks("vaveyla")
        check("title prefix search", r == ["/m/a1.mp3"], str(r))
        r = db.search_tracks("googoosh")
        check("artist search, ranked", set(r) == {"/m/a2.mp3", "/m/a3.mp3"})
        r = db.search_tracks("back")
        check("multi-word partial", "/m/a4.mp3" in r)
        r = db.search_tracks("")
        check("empty query -> none", r == [])
        r = db.search_tracks('"weird" (injection)')
        check("fts injection safe", isinstance(r, list))
        # diacritics-insensitive
        db.upsert_tracks([{"path": "/m/a6.mp3", "title": "Café Blue",
                           "artist": "Björk"}])
        r = db.search_tracks("cafe blue")
        check("diacritics-insensitive", r == ["/m/a6.mp3"], str(r))
        r = db.search_tracks("bjork")
        check("diacritic artist", r == ["/m/a6.mp3"], str(r))
        # update keeps fts in sync
        db.upsert_tracks([{"path": "/m/a1.mp3", "title": "Renamed Title"}])
        r = db.search_tracks("Renamed")
        check("fts sync on update", r == ["/m/a1.mp3"], str(r))
        r = db.search_tracks("vaveyla")
        check("old title gone after update", r == [], str(r))
        db.remove_tracks(["/m/a2.mp3"])
        r = db.search_tracks("Gol-e Sang")
        check("fts sync on delete", r == [], str(r))
        check("track_count", db.track_count() == 5, str(db.track_count()))


def test_ratings_favorites_playlists():
    print("=== ratings / favorites / playlists ops ===")
    with tempfile.TemporaryDirectory(prefix="gmdb_") as td:
        db = LibraryDB(Path(td) / "t.db")
        db.bulk_ingest([{"path": f"/m/t{i}.mp3", "title": f"T{i}",
                         "artist": "A"} for i in range(5)])
        db.set_rating("/m/t1.mp3", 5)
        db.set_rating("/m/t2.mp3", 3)
        check("rating stored", db.get_track("/m/t1.mp3")["rating"] == 5)
        db.set_rating("/m/t1.mp3", 0)
        check("rating cleared", db.get_track("/m/t1.mp3")["rating"] == 0)
        db.set_favorite("/m/t3.mp3", True, "Workout")
        db.set_favorite("/m/t4.mp3", True)
        favs = db.favorites()
        check("favorites with categories",
              dict(favs)["/m/t3.mp3"] == "Workout" and
              dict(favs)["/m/t4.mp3"] == "")
        pid = db.ensure_playlist("My List")
        db.replace_playlist_tracks(pid, ["/m/t1.mp3", "/m/t2.mp3"])
        db.append_playlist_tracks(pid, ["/m/t3.mp3"])
        check("playlist order", db.playlist_tracks(pid) ==
              ["/m/t1.mp3", "/m/t2.mp3", "/m/t3.mp3"])
        pid2 = db.ensure_playlist("My List")
        check("ensure idempotent", pid2 == pid)
        db.delete_playlist(pid)
        check("playlist deleted", db.playlists() == [])
        # play stats
        db.record_play("/m/t1.mp3", 200.0)
        db.record_play("/m/t1.mp3", 150.0)
        check("play_count", db.get_track("/m/t1.mp3")["play_count"] == 2)
        check("total_seconds", abs(db.total_seconds() - 350.0) < 0.01)
        rows = db.top_tracks(3)
        check("top_tracks", rows[0]["path"] == "/m/t1.mp3" and
              rows[0]["play_count"] == 2)
        db.add_listen_seconds(10)
        check("add_listen_seconds", abs(db.total_seconds() - 360.0) < 0.01)


def test_smart_playlists():
    print("=== smart playlists ===")
    now = time.time()
    spec = smart.validate_spec(
        {"rules": [{"field": "play_count", "op": ">", "value": 2}],
         "sort": "play_count", "order": "desc", "limit": 50})
    check("spec validated", spec is not None)
    tracks = [
        {"play_count": 5, "rating": 4, "last_played_at": now - 1000,
         "added_at": now - 99999, "genre": "Rock"},
        {"play_count": 1, "rating": 2, "last_played_at": now - 5 * 86400,
         "added_at": now - 100, "genre": "Pop"},
        {"play_count": 0, "rating": 0, "last_played_at": None,
         "added_at": now - 10, "genre": "Jazz"},
    ]
    check("most played match",
          smart.matches(tracks[0], spec) and
          not smart.matches(tracks[1], spec))
    never = smart.validate_spec(smart.BUILTINS["never_played"]["spec"])
    check("never played (null last_played)",
          smart.matches(tracks[2], never) and
          not smart.matches(tracks[0], never))
    nhr = smart.validate_spec(smart.BUILTINS["not_heard_recently"]["spec"])
    check("not heard recently (null counts)",
          smart.matches(tracks[2], nhr) and not smart.matches(tracks[0], nhr))
    bad = smart.validate_spec(
        {"rules": [{"field": "evil; DROP TABLE", "op": ">", "value": 1}]})
    check("invalid field rejected", bad is None)
    bad2 = smart.validate_spec("not json at all [")
    check("garbage spec rejected", bad2 is None)
    where, args = smart.sql_where(
        smart.validate_spec(smart.BUILTINS["not_heard_recently"]["spec"]))
    check("sql where safe", "COALESCE" in where and ";" not in where, where)
    # full pipeline through the DB
    with tempfile.TemporaryDirectory(prefix="gmdb_") as td:
        db = LibraryDB(Path(td) / "t.db")
        rows = []
        for i in range(10):
            rows.append({"path": f"/m/x{i}.mp3", "title": f"X{i}",
                         "artist": "A", "play_count": i,
                         "added_at": now - i * 100,
                         "last_played_at": None if i < 5 else now - i})
        db.bulk_ingest(rows)
        where, args = smart.sql_where(spec)
        got = db.tracks_where(where, args, order="play_count DESC")
        check("sql pipeline filter", [g["play_count"] for g in got] ==
              [9, 8, 7, 6, 5, 4, 3], str([g["play_count"] for g in got]))
        ra = smart.validate_spec(smart.BUILTINS["recently_added"]["spec"])
        got2 = db.tracks_where(*smart.sql_where(ra),
                               order="added_at DESC", limit=3)
        check("recently added order",
              [g["added_at"] for g in got2] ==
              sorted([g["added_at"] for g in got2], reverse=True))


def test_store_facade_and_stats():
    print("=== store facade + stats store ===")
    with tempfile.TemporaryDirectory(prefix="gmdb_") as td:
        tmp = Path(td)
        home = make_legacy_home(tmp)
        cfg_file = home / ".goldenmusic" / "config.json"
        store = LibraryStore(cfg_file)
        check("store migration ok", store.migration.ok)
        lib = store.load_library()
        check("store library", len(lib) == 13, str(len(lib)))
        favs = store.load_favorites()
        check("store favorites", len(favs) == 3)
        pls = store.load_playlists()
        check("store playlists", set(pls) == {"Mix", "Empty"})
        tags = store.load_tag_cache()
        check("store tag cache", tags[lib[0]][0] == "Song 00")
        # sync semantics
        added, removed = store.sync_library(lib + [str(tmp / "new.mp3")])
        check("sync adds", added == [str(tmp / "new.mp3")])
        store.sync_remove([str(tmp / "new.mp3")])
        check("sync removes", str(tmp / "new.mp3") not in
              store.load_library())
        store.persist_playlists({"Mix": lib[:2]})
        check("persist playlists", store.load_playlists() ==
              {"Mix": lib[:2]})
        store.persist_favorites({lib[0]}, {lib[0]: "Chill"})
        favs2 = dict(store.load_favorites()) if False else dict(
            store.db.favorites())
        check("persist favorites", store.db.favorite_set() == {lib[0]} and
              favs2[lib[0]] == "Chill")
        # smart ensure / remove
        store.ensure_smart_playlist("most_played")
        sm = store.smart_playlists()
        check("smart created", len(sm) == 1 and sm[0]["name"] == "Most Played")
        tid = sm[0]["id"]
        got = store.smart_tracks(tid)
        check("smart tracks resolved", len(got) >= 1 and
              got[0]["play_count"] > 0)
        store.remove_smart_playlists()
        check("smart removed", store.smart_playlists() == [])
        # StatsStore
        st = StatsStore(store.db)
        st.add_seconds(3)
        st.add_seconds(3)
        check("pending seconds flushed at 5s threshold",
              abs(store.db.total_seconds() - (12345.6 + 6)) < 0.01)
        credited = st.credit_track(lib[0], 210.0, "Artist0",
                                   duration_seconds=200.0,
                                   listened_seconds=180.0)
        check("70% rule credit", credited is True)
        credited2 = st.credit_track(lib[1], 5.0, "Artist1",
                                    duration_seconds=200.0,
                                    listened_seconds=5.0)
        check("below threshold no credit", credited2 is False)
        tt = st.top_tracks(n=3)
        check("top_tracks tuple shape", len(tt[0]) == 4 and tt[0][2] == 8)
        st.save()
        check("fmt_total", isinstance(StatsStore.fmt_total(12345.6), str))


def test_bookmarks_radio_lyrics_waveform():
    print("=== bookmarks / radio / lyrics offset / waveform blobs ===")
    with tempfile.TemporaryDirectory(prefix="gmdb_") as td:
        db = LibraryDB(Path(td) / "t.db")
        db.set_bookmark("/m/book.mp3", 123456)
        check("bookmark roundtrip", db.get_bookmark("/m/book.mp3") == 123456)
        check("bookmark missing -> None",
              db.get_bookmark("/m/nope.mp3") is None)
        db.set_bookmark("/m/book.mp3", 999)
        check("bookmark update", db.get_bookmark("/m/book.mp3") == 999)
        rid = db.add_radio("SomaFM Groove", "https://ice1.somafm.com/groovesalad-128-mp3",
                           "https://somafm.com", "Ambient")
        stations = db.radio_stations()
        check("radio stored", len(stations) == 1 and
              stations[0]["url"].startswith("https://"))
        check("radio id", isinstance(rid, int))
        db.set_lyrics_offset("/m/a1.mp3", -500)
        check("lyrics offset", db.lyrics_offset("/m/a1.mp3") == -500)
        peaks = bytes(range(256)) * 4
        db.store_waveform("/m/a1.mp3", 200000, peaks, "miniaudio")
        check("waveform blob roundtrip",
              db.get_waveform("/m/a1.mp3") == peaks)
        db.store_replaygain("/m/a1.mp3", -6.2, -3.1, 1.0, 1.0, "tag")
        rg = db.get_replaygain("/m/a1.mp3")
        check("replaygain roundtrip", abs(rg["track_gain_db"] + 6.2) < 1e-6)


def test_scratch_paths_never_persisted():
    print("=== never-wipe: scratch paths filtered ===")
    with tempfile.TemporaryDirectory(prefix="gmdb_") as td:
        import gm.store as st_mod
        tmp = Path(td)
        home = make_legacy_home(tmp)
        cfg_file = home / ".goldenmusic" / "config.json"
        # put the DATA dir in a non-scratch location (fixture dir, NOT the
        # real ~/.goldenmusic) so the never-wipe filter engages
        fixture = Path("/home/z/gm_scratch_fixture/.goldenmusic")
        if fixture.exists():
            import shutil as _sh
            _sh.rmtree(fixture, ignore_errors=True)
        fixture.parent.mkdir(parents=True, exist_ok=True)
        import shutil as _sh2
        gd = home / ".goldenmusic"
        fixture.mkdir(parents=True, exist_ok=True)
        _sh2.copy2(gd / "config.json", fixture / "config.json")
        _sh2.copy2(gd / "tag_cache.json", fixture / "tag_cache.json")
        _sh2.copy2(gd / "stats.json", fixture / "stats.json")
        store = LibraryStore(fixture / "config.json")
        lib = store.load_library()
        scratch = str(Path(tempfile.mkdtemp(prefix="gmtest_home_")) /
                      "evil.mp3")
        added, _ = store.sync_library(lib + [scratch])
        check("scratch path not added", added == [], str(added))
        store.persist_favorites(set(lib[:1]) | {scratch})
        check("scratch path not favorited", scratch not in
              store.db.favorite_set())
        store.persist_playlists({"Bad": [scratch]})
        check("scratch path not in playlist",
              store.load_playlists().get("Bad") == [])


print("Golden Music SQLite test suite")
test_migration_success()
test_migration_fresh_install()
test_migration_failure_rollback()
test_fts_search()
test_ratings_favorites_playlists()
test_smart_playlists()
test_store_facade_and_stats()
test_bookmarks_radio_lyrics_waveform()
test_scratch_paths_never_persisted()
import shutil as _final_sh
_final_sh.rmtree("/home/z/gm_scratch_fixture", ignore_errors=True)
print(f"\nSUMMARY: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
