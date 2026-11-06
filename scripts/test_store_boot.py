"""End-to-end boot test: real MainWindow + real SQLite store flow inside the
testenv sandbox (never the real user data dir).

Seeds a legacy 2.x-style JSON config in the sandbox HOME, lets the app boot
through the REAL _load_config_async -> StoreInitWorker -> migration path,
then verifies library/favorites/playlists/tags/stats all arrive from SQLite
and that _save_config round-trips through the store.

Run:  QT_QPA_PLATFORM=offscreen python scripts/test_store_boot.py
"""
import os
import sys
import json
import tempfile
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import testenv as _testenv
_testenv.install()
del _testenv

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

app = QApplication(sys.argv)
app.setApplicationName("GMStoreBoot")

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


# ---- seed a legacy v2.x user dir INSIDE the sandbox home ------------------
from config import config_path

music = Path(tempfile.mkdtemp(prefix="gmboot_music_"))
cfg_dir = config_path().parent
cfg_dir.mkdir(parents=True, exist_ok=True)
tracks = []
for i in range(6):
    p = music / f"Artist{i} - Song{i}.mp3"
    p.write_bytes(b"FAKE")
    tracks.append(str(p))
legacy = {
    "version": "2.2.0", "theme": "midnight_blue",
    "library": tracks,
    "favorites": tracks[:2],
    "playlists": {"Roadtrip": tracks[2:5]},
    "volume": 55, "last_track": tracks[0], "last_position": 42000,
}
(cfg_dir / "config.json").write_text(json.dumps(legacy, indent=1),
                                      encoding="utf-8")
(cfg_dir / "tag_cache.json").write_text(
    json.dumps({p: [f"Song {i}", f"Artist{i}"] for i, p in enumerate(tracks)}),
    encoding="utf-8")
(cfg_dir / "stats.json").write_text(json.dumps(
    {"total_seconds": 4321.0, "play_counts": {tracks[0]: 9},
     "artist_seconds": {"Artist0": 1000.0}, "sessions": 7,
     "first_played": "2023-05-05"}), encoding="utf-8")

from main import MainWindow

w = MainWindow()
w.show()

STATE = {"phase": "boot"}


def wait_for_store(deadline_ms=15000):
    if getattr(w, "store", None) is not None:
        STATE["phase"] = "ready"
        verify_store()
        return
    if deadline_ms <= 0:
        check("store became ready", False, "timeout waiting for StoreInitWorker")
        finish()
        return
    QTimer.singleShot(100, lambda: wait_for_store(deadline_ms - 100))


def verify_store():
    st = w.store
    check("store live", st is not None)
    check("migration verified", st.migration.ok, st.migration.message)
    check("library loaded from DB", set(w.library) == set(tracks),
          str(len(w.library)))
    check("favorites from DB", w.favorites == set(tracks[:2]))
    check("playlists from DB", w.playlists == {"Roadtrip": tracks[2:5]})
    check("tag cache from DB",
          w._tag_cache.get(tracks[0], ("", ""))[0] == "Song 0")
    check("library list populated",
          w.library_list.count() == len(tracks))
    check("stats switched to DB store",
          hasattr(w.stats, "db") and w.stats.db is st.db)
    check("sessions seeded+1", w.stats.sessions == 8, str(w.stats.sessions))
    check("total seconds migrated",
          abs(w.stats.total_seconds() - 4321.0) < 0.01,
          str(w.stats.total_seconds()))
    check("play counts migrated", w.stats.play_counts.get(tracks[0]) == 9)
    check("first_played kept", w.stats.first_played == "2023-05-05")
    # search through FTS
    hits = st.search("song 3")
    check("FTS finds Song 3", hits == [tracks[3]], str(hits))
    # favorites list page built from DB data
    w._on_rail_clicked("favorites")
    check("favorites page populated", w.favorites_list.count() == 2)
    w._on_rail_clicked("library")

    # save round-trip: remove one track the way the UI does (explicit
    # store removal + in-memory update), add a favorite, save, reload
    w.library.remove(tracks[5])
    w._store_remove([tracks[5]])
    w.favorites.add(tracks[4])
    w._save_config()
    check("explicit removal hits DB",
          tracks[5] not in st.load_library())
    # conservative reconcile: dropping an ON-DISK path from the in-memory
    # list must NOT remove it from the DB (never-wipe)
    w.library = [p for p in w.library if p != tracks[4]]
    w._save_config()
    check("reconcile keeps on-disk paths",
          tracks[4] in st.load_library())
    check("save favorite round-trip", st.load_favorites() ==
          set(tracks[:2]) | {tracks[4]})
    # empty-library never-wipe guard against DB
    removed = list(w.library)
    w.library = []
    w.favorites = set()
    w.playlists = {}
    w._save_config()
    # DB content before the empty save = removed + tracks[4] (on-disk,
    # kept by the conservative reconcile) — the empty save must keep it all
    check("never-wipe: DB library kept when memory empty",
          set(st.load_library()) == set(removed) | {tracks[4]},
          str(len(st.load_library())))
    check("never-wipe: favorites kept", len(st.load_favorites()) == 3)
    check("never-wipe: playlists kept", "Roadtrip" in st.load_playlists())
    # backup dir created before migration
    check("pre-sqlite backup exists",
          any(d.name.startswith("pre_sqlite_")
              for d in (cfg_dir / "backups").iterdir()))
    finish()


def finish():
    w._force_quit = True
    w.close()
    app.processEvents()
    print(f"\nSUMMARY: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


QTimer.singleShot(600, lambda: wait_for_store(15000))
sys.exit(app.exec())
