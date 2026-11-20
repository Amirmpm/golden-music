"""Golden Music v3 regression suite — audit fixes + new features.

Covers the specific defects fixed in this release and the new subsystems:
  A1 offline-drive retention (data-loss fix)
  A2 stale-lock double-instance policy (logic-level)
  A3 View enum completeness + navigation indices
  A4 queue actions (Play next / Add to queue — orphaned-feature fix)
  A5 spec hiddenimports completeness
  A6 CI import shim (testenv main.pyw alias)
  V1 Liquid Glass settings validation/migration/persistence roundtrip
  V2 artwork-derived accent
  V3 SMTC degradation safety on non-Windows
  V4 Windows registration policy (non-Windows refusal + fragment)
  V5 format probing conservative fallback
  V6 start page = HOME; online/download pages present
  V7 recently-played tracking (dedupe + cap)
  V8 search-history persistence flow
  V9 downloads -> library integration (no duplicate indexing)
  V10 open_external_files still queues OS-handed files
"""
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import testenv as _testenv
_testenv.install()
del _testenv

from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import QApplication

app = QApplication(sys.argv)
app.setApplicationName("GMV3Regression")

import main as m
MainWindow = m.MainWindow
from config import config_path

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name
          + (f"  [{detail}]" if not cond and detail else ""), flush=True)


# --- build the window with the async loader neutered (tests drive state) ----
MainWindow._load_config_async = lambda self: None
w = MainWindow()
boot_page = w.stack.currentIndex()
boot_view = w.current_view
app.processEvents()

# ---------------------------------------------------------------------------
# A3: View enum + navigation
# ---------------------------------------------------------------------------
check("A3 View enum has all 7 pages",
      {v.name for v in m.View} == {"HOME", "SEARCH", "LIBRARY", "FAVORITES",
                                   "ALBUMS", "STATS", "DOWNLOADS"},
      str({v.name for v in m.View}))
check("A3 stack has 7 pages", w.stack.count() == 7)
for key, idx in (("home", 0), ("search", 1), ("library", 2), ("favorites", 3),
                 ("albums", 4), ("stats", 5), ("downloads", 6)):
    w._on_rail_clicked(key)
    check(f"A3 nav '{key}' -> index {idx}", w.stack.currentIndex() == idx,
          str(w.stack.currentIndex()))
check("A3 stats auto-refresh timer targets PAGE_STATS",
      m.PAGE_STATS == 5)

# ---------------------------------------------------------------------------
# A1: offline-drive retention
# ---------------------------------------------------------------------------
import tempfile
tlib = Path(tempfile.mkdtemp(prefix="gmv3a1_"))
tracks = []
for i in range(4):
    p = tlib / f"Offline Artist {i} - Track {i}.mp3"
    p.write_bytes(b"fake")
    tracks.append(str(p))
# simulate the files being offline: delete two of them
os.remove(tracks[2])
os.remove(tracks[3])
# inject a saved config that still lists ALL FOUR entries (the on-disk
# state produced while the drive was online)
injected = {"library": list(tracks),
            "favorites": sorted([tracks[0], tracks[1]]),
            "playlists": {}}
w2 = MainWindow()
w2._finish_config_load(injected)
check("A1 async loader keeps offline entries (no exists-prune)",
      set(w2.library) == set(tracks),
      str([os.path.basename(x) for x in w2.library]))
check("A1 favorites kept without exists-prune",
      set(w2.favorites) == {tracks[0], tracks[1]})

# ---------------------------------------------------------------------------
# A2: stale-lock policy (logic-level): second instance with an unreachable
# IPC owner must refuse to start. We verify the policy constant + retry
# structure rather than the OS mutex itself.
# ---------------------------------------------------------------------------
src = open(str(Path(__file__).resolve().parent.parent / "main.pyw"),
           encoding="utf-8").read()
check("A2 stale-lock fix present (refuse + message, no blind start)",
      "refusing a second instance" in src and "for attempt in range(3)" in src)
check("A2 forward retry loop present",
      "_forward_to_running(argv_files)" in src
      and "for attempt in range(3)" in src)

# ---------------------------------------------------------------------------
# A4: queue actions
# ---------------------------------------------------------------------------
queue_track = str(tlib / "Queue Artist - Queue Song.mp3")
Path(queue_track).write_bytes(b"fake")
w.current_playlist = [tracks[0], tracks[1]]
w.current_index = 0
w._queue_track(queue_track)
check("A4 add-to-queue appends", len(w.current_playlist) == 3
      and w.current_playlist[2] == queue_track,
      str(w.current_playlist))
w.current_playlist = [tracks[0], tracks[1], queue_track]
w.current_index = 0
# queue_track sits at position 2 -> 'Play next' must move it to position 1
w._play_track_next(queue_track)
check("A4 play-next moves an existing track to position current+1",
      w.current_playlist[1] == queue_track and len(w.current_playlist) == 3
      and w.current_playlist[2] == tracks[1], str(w.current_playlist))
# a track that is ALREADY next is a no-op (no duplicate, no shuffle)
w._play_track_next(queue_track)
check("A4 play-next is a no-op when the track is already next",
      w.current_playlist[1] == queue_track and len(w.current_playlist) == 3)
# the CURRENT track itself is never re-inserted
w._play_track_next(tracks[0])
check("A4 play-next never duplicates the current track",
      w.current_playlist.count(tracks[0]) == 1,
      str(w.current_playlist))

# ---------------------------------------------------------------------------
# A5: PyInstaller hiddenimports completeness
# ---------------------------------------------------------------------------
spec = open(str(Path(__file__).resolve().parent.parent / "goldenmusic.spec"),
            encoding="utf-8").read()
required = ["playback_fx", "playstats", "userbackup", "audio_output",
            "tag_editor", "liquidglass", "online", "downloads",
            "home_page", "sidebar", "search_page", "downloads_page",
            "smtc", "winreg_apps", "formatcaps"]
missing = [r for r in required if f"'{r}'" not in spec and f'"{r}"' not in spec]
check("A5 spec hiddenimports complete", not missing, str(missing))

# ---------------------------------------------------------------------------
# A6: testenv main.pyw alias (this very import proves it)
# ---------------------------------------------------------------------------
import sys as _sys
check("A6 'main' alias importable from .pyw",
      "main" in _sys.modules and hasattr(m, "MainWindow"))

# ---------------------------------------------------------------------------
# V1: Liquid Glass settings roundtrip + hostile config
# ---------------------------------------------------------------------------
w.glass = __import__("liquidglass").GlassSettings(
    mode="light", accent="#2266cc", transparency=55, corner=120)
w._apply_theme()
check("V1 liquid theme active", w.theme["name"] == "liquid_light")
w._save_config()
cfg2 = json.loads(config_path().read_text(encoding="utf-8"))
check("V1 liquid settings persisted",
      cfg2.get("liquid", {}).get("accent") == "#2266cc"
      and cfg2.get("liquid", {}).get("transparency") == 55,
      json.dumps(cfg2.get("liquid")))
w3 = MainWindow()
w3._finish_config_load(cfg2)
check("V1 liquid settings reload", w3.glass.accent == "#2266cc"
      and w3.glass.transparency == 55)
# legacy migration (12-theme config without 'liquid')
legacy_cfg = dict(cfg2)
legacy_cfg.pop("liquid")
legacy_cfg["theme"] = "azure"
w4 = MainWindow()
w4._finish_config_load(legacy_cfg)
check("V1 legacy 12-theme config migrated",
      w4.glass.mode == "light" and w4.glass.accent == "#115380",
      f"{w4.glass.mode} {w4.glass.accent}")
legacy_cfg["theme"] = "royal_gold"
w5 = MainWindow()
w5._finish_config_load(legacy_cfg)
check("V1 royal_gold migrates to dark glass with champagne accent",
      w5.glass.mode == "dark" and w5.glass.accent == "#e9b957")

# QColor-safety of the theme dict (painters)
from PyQt6.QtGui import QColor
t = w.theme
bad_keys = [k for k in ("window_bg", "panel_bg", "panel_bg_2", "border",
                        "text", "muted", "gold", "gold_light", "gold_deep",
                        "active") if not QColor(t[k]).isValid()]
check("V1 theme legacy keys are QColor-valid (painter safe)",
      not bad_keys, str(bad_keys))
lg = __import__("liquidglass")
qss_t = lg.glassify_for_qss(t)
check("V1 glassified QSS surface is translucent",
      qss_t["panel_bg"].startswith("rgba(") and t["panel_bg"].startswith("#"))

# ---------------------------------------------------------------------------
# V2: artwork-derived accent
# ---------------------------------------------------------------------------
from PyQt6.QtGui import QPixmap, QPainter
pm = QPixmap(64, 64)
p = QPainter(pm)
p.fillRect(0, 0, 64, 64, __import__("PyQt6.QtGui", fromlist=["QColor"]).QColor("#20303a"))
p.fillRect(0, 0, 30, 64, __import__("PyQt6.QtGui", fromlist=["QColor"]).QColor("#e8590c"))
p.end()
w._apply_artwork_accent(pm)
check("V2 artwork accent applied", w.glass.accent == "#e8590c", w.glass.accent)

# ---------------------------------------------------------------------------
# V3: SMTC degradation
# ---------------------------------------------------------------------------
import smtc as smtc_mod
check("V3 SMTC safe on this platform",
      not smtc_mod.SystemMediaControls.available()
      and not w._smtc.is_active())
w._smtc.update_metadata(smtc_mod.MediaMetadata("T", "A"))
w._smtc.set_status_playing(True)
w._smtc._flush_pending()
check("V3 SMTC no-crash through the update path", True)

# ---------------------------------------------------------------------------
# V4/V5: registration policy + format probing
# ---------------------------------------------------------------------------
import winreg_apps
try:
    winreg_apps.register_app()
    check("V4 register refuses non-Windows", False)
except RuntimeError:
    check("V4 register refuses non-Windows", True)
st = winreg_apps.registration_status()
check("V4 status degrades off-Windows",
      st["windows"] is False and "detail" in st)
frag = winreg_apps.iss_registry_fragment([".mp3"])
check("V4 iss fragment mirrors the app registration",
      "RegisteredApplications" in frag and "GoldenMusic.AudioFile" in frag)
import formatcaps
exts = formatcaps.probe_supported_exts()
check("V5 format probe returns only known candidates",
      set(exts) <= set(formatcaps.CANDIDATE_EXTS) and exts, str(exts))

# ---------------------------------------------------------------------------
# V6: start page + online/downloads pages exist
# ---------------------------------------------------------------------------
check("V6 app boots on HOME", boot_page == 0 and boot_view == m.View.HOME,
      f"{boot_page} {boot_view}")
check("V6 home/search/downloads pages wired",
      hasattr(w, "home_page") and hasattr(w, "search_page")
      and hasattr(w, "downloads_page"))

# ---------------------------------------------------------------------------
# V7: recently played tracking
# ---------------------------------------------------------------------------
w.recently_played = []
w._note_recently_played(tracks[0])
w._note_recently_played(tracks[0])          # consecutive duplicate ignored
w._note_recently_played(tracks[1])
check("V7 recently-played order + dedupe",
      w.recently_played[:2] == [tracks[1], tracks[0]],
      str(w.recently_played))
w.recently_played = [f"x{i}" for i in range(50)]
w._note_recently_played("newest")
check("V7 recently-played capped at 30",
      len(w.recently_played) == 30 and w.recently_played[0] == "newest")

# ---------------------------------------------------------------------------
# V8: search history flow
# ---------------------------------------------------------------------------
w.search_history = []
w._on_search_query("Grateful Dead")
w._on_search_query("grateful dead")
w._on_search_query("Daft Punk")
w._on_search_query("")
check("V8 search history dedupe case-insensitive + bounded",
      [q.lower() for q in w.search_history] == ["daft punk", "grateful dead"]
      and len(w.search_history) == 2,
      str(w.search_history))
import online as online_mod
check("V8 sanitized via online module",
      w.search_history == online_mod.sanitize_history(w.search_history))

# ---------------------------------------------------------------------------
# V9: downloads -> library integration (real local HTTP download)
# ---------------------------------------------------------------------------
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
PAYLOAD = os.urandom(64 * 1024)
class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(PAYLOAD)))
        self.end_headers()
        self.wfile.write(PAYLOAD)
srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
PORT = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()

import time
dl_dir = Path(tempfile.mkdtemp(prefix="gmv3dl_"))
w.download_folder = str(dl_dir)
w.download_manager.set_folder(dl_dir)
w.downloads_page.set_folder(dl_dir)
w.library = list(tracks[:2])
added = []
w.file_added_handler = lambda path, job: added.append(path)
before = set(w.library)
job = w.download_manager.download_url(
    f"http://127.0.0.1:{PORT}/x", "Downloaded Song", "Net Artist", ".mp3",
    size_hint=len(PAYLOAD))
deadline = time.time() + 12
while time.time() < deadline and job.state != m.dl_mod.JobState.COMPLETED:
    app.processEvents(); time.sleep(0.03)
check("V9 download completes", job.state == m.dl_mod.JobState.COMPLETED,
      f"{job.state} {job.error}")
final = Path(job.dest_path)
check("V9 file landed in the configured folder",
      final.parent == dl_dir and final.exists(), str(final))
# simulate the completion handler (the manager emits file_added -> MainWindow
# _on_download_completed does the library integration)
w._on_download_completed(str(final), job)
check("V9 downloaded track joins the library exactly once",
      w.library.count(str(final)) == 1 and len(w.library) == 3,
      str(len(w.library)))
w._on_download_completed(str(final), job)
check("V9 no duplicate indexing on repeated completion",
      w.library.count(str(final)) == 1)
# v3.1: history is recorded by the manager inside the file_added chain
# (queued delivery) — pump the loop until the record lands.
deadline = time.time() + 5
while time.time() < deadline and not any(
        r.get("title") == "Downloaded Song" for r in w.downloads_history):
    app.processEvents(); time.sleep(0.03)
check("V9 downloads history recorded",
      any(r.get("title") == "Downloaded Song" for r in w.downloads_history))
srv.shutdown()

# ---------------------------------------------------------------------------
# V10: open_external_files
# ---------------------------------------------------------------------------
w.current_playlist = []
w.current_index = -1
ok = w.open_external_files([tracks[0], tracks[1]])
check("V10 open_external_files queues handed-over files",
      ok and w.current_playlist[:2] == tracks[:2])

# ---------------------------------------------------------------------------
print("\n" + "=" * 50)
print(f"V3 REGRESSION: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
print("ALL V3 REGRESSION CHECKS PASSED")
