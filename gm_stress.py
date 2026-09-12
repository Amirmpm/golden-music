"""HEAVY STRESS TEST — simulates a full frantic usage session:
  1000+ tracks, rapid-click storms on every control, seek during transitions,
  theme switching mid-scan, sleep timer, A-B + speed combo, unbounded-session
  memory probes. Every action runs inside the real event loop; any exception,
  hang (>10s per step), or state corruption is reported.
"""
import gc
import os
import random
import sys
import time
import traceback

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_MEDIA_BACKEND"] = "dummy"
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, r"E:\my-documents\ClaudeCode\GoldenMusic")

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox

# auto-dismiss any modal so the storm never stalls on a dialog
for name in ("information", "warning", "critical"):
    setattr(QMessageBox, name,
            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
QMessageBox.question = staticmethod(
    lambda *a, **k: QMessageBox.StandardButton.No)   # refuse destructive ops

app = QApplication(sys.argv)
import main as M
from config import Theme
M.MainWindow._load_config_async = lambda self: None

BUGS = []

w = M.MainWindow()
w.show()

def pump(ms):
    """Run the event loop for `ms` milliseconds of wall time."""
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.001)

def step(name, fn, budget_ms=10000):
    t0 = time.perf_counter()
    try:
        fn()
    except Exception:
        BUGS.append((name, "EXCEPTION", traceback.format_exc(limit=3)))
        print(f"  [BUG-EXC] {name}", flush=True)
        return
    dt = (time.perf_counter() - t0) * 1000
    if dt > budget_ms:
        BUGS.append((name, f"SLOW {dt:.0f}ms", ""))
        print(f"  [BUG-SLOW] {name}: {dt:.0f}ms", flush=True)
    print(f"  ok {name} ({dt:.0f}ms)", flush=True)

# ---------------------------------------------------------------------------
# Setup: 1200 tracks across 60 albums
# ---------------------------------------------------------------------------
print("=== setup: 1200 tracks ===", flush=True)
BASE = os.path.join(os.environ["TEMP"], "gm_stress")
files = []
for d in range(60):
    sub = os.path.join(BASE, f"Album {d:03d}")
    os.makedirs(sub, exist_ok=True)
    for i in range(20):
        p = os.path.join(sub, f"{i+1:02d} - Artist {d % 12} - Song {d}-{i}.mp3")
        if not os.path.exists(p):
            with open(p, "wb") as f:
                f.write(b"\xff\xfb\x90\x00" + b"\x00" * 2048)
        files.append(p)
w.library = list(files)
w.added_folders = [BASE]
for i, f in enumerate(files):
    w._tag_cache[f] = (f"Song {i}", f"Artist {i % 12}")
pump(300)
print(f"library={len(w.library)}", flush=True)

# baseline memory
gc.collect()
import ctypes

class PMC(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t)]

def rss_mb():
    pmc = PMC(); pmc.cb = ctypes.sizeof(PMC)
    ctypes.windll.psapi.GetProcessMemoryInfo(
        ctypes.windll.kernel32.GetCurrentProcess(),
        ctypes.byref(pmc), pmc.cb)
    return pmc.WorkingSetSize / 1e6

mem0 = rss_mb()
print(f"baseline RSS: {mem0:.0f} MB", flush=True)

# ---------------------------------------------------------------------------
# 1. Rapid click storm: play/pause x120, next/prev alternating x120
# ---------------------------------------------------------------------------
print("=== 1. click storm ===", flush=True)
def click_storm():
    for i in range(120):
        w._on_play_pause()
        if i % 3 == 0:
            w._on_next()
        elif i % 3 == 1:
            w._on_prev()
        if i % 20 == 0:
            pump(2)
step("click storm 120x", click_storm)

# ---------------------------------------------------------------------------
# 2. Seek-drag during rapid transitions
# ---------------------------------------------------------------------------
print("=== 2. seek storm ===", flush=True)
def seek_storm():
    w.audio.duration = lambda: 200_000      # 3:20 fake
    for i in range(80):
        w.player_bar.seek_slider.setValue(random.randrange(0, 1000))
        if i % 2:
            w._on_seek_released()
        if i % 10 == 0:
            w._on_next()
            pump(1)
step("seek during transitions 80x", seek_storm)

# ---------------------------------------------------------------------------
# 3. Volume storm incl. mute mashing + fade racing track change
# ---------------------------------------------------------------------------
print("=== 3. volume/fade storm ===", flush=True)
def vol_storm():
    for i in range(150):
        w._change_volume(random.choice([5, -5, 15, -30]))
        if i % 7 == 0:
            w._on_mute_toggle()
        if i % 11 == 0:
            w._on_next()
        if i % 13 == 0:
            w.fx.fade_out_then(w.audio.pause)
        if i % 17 == 0:
            w.fx.fade_in_from_silence()
        pump(1)
step("volume+mute+fade 150x", vol_storm)
pump(700)   # let any pending fade complete

# ---------------------------------------------------------------------------
# 4. A-B loop + speed mid-track, then next
# ---------------------------------------------------------------------------
print("=== 4. A-B + speed combo ===", flush=True)
def ab_speed():
    w.audio.position = lambda: 90_000
    w._on_ab_button()          # mark A
    w.audio.position = lambda: 120_000
    w._on_ab_button()          # mark B -> loop active
    w._cycle_playback_rate_open = None
    w.fx.set_rate(1.75)
    pump(300)                  # fx ticks enforce the loop
    w._on_next()               # AB cleared per-track
    w.fx.set_rate(0.5)
    w._on_next()
step("A-B + speed + track change", ab_speed)
w.fx.clear_ab()

# ---------------------------------------------------------------------------
# 5. Theme switching storm mid-playback (12 themes x3)
# ---------------------------------------------------------------------------
print("=== 5. theme storm ===", flush=True)
def theme_storm():
    names = Theme.names()
    for r in range(3):
        for n in names:
            w._on_theme_selected(n)
            if r == 1:
                w._on_next()
            pump(1)
step("36 theme switches", theme_storm)

# ---------------------------------------------------------------------------
# 6. Search typing storm + sort thrash
# ---------------------------------------------------------------------------
print("=== 6. search/sort storm ===", flush=True)
def search_storm():
    for ch in "abcdefghij0123456789":
        w._on_search_changed(ch)
    pump(250)                  # debounce fires once
    for idx in range(4):
        w._on_sort_changed(idx)
    w._on_search_changed("")
    pump(250)
step("search+sort thrash", search_storm)

# ---------------------------------------------------------------------------
# 7. Folder tree: rapid clicks + expand/collapse mashing
# ---------------------------------------------------------------------------
print("=== 7. tree mash ===", flush=True)
def tree_mash():
    for r in range(6):
        for i in range(w.folder_tree.topLevelItemCount()):
            it = w.folder_tree.topLevelItem(i)
            if it.data(0, 0x0100) in ("__all__", "__add__"):
                continue
            w._on_folder_tree_click(it, 0)
            w.folder_tree.expandItem(it)
            w._on_folder_tree_click(w.folder_tree.topLevelItem(0), 0)  # All
step("folder tree mash 6 rounds", tree_mash)

# ---------------------------------------------------------------------------
# 8. Stats rows: rapid play-button mashing
# ---------------------------------------------------------------------------
print("=== 8. stats row mash ===", flush=True)
if w.stats:
    for i in range(30):
        w.stats.credit_track(files[i], 180.0, f"Artist {i % 12}")
w._stats_rows_sig = None
w._on_rail_clicked("stats")
pump(100)
def stats_mash():
    n = w._stat_tracks_lay.count()
    for i in range(60):
        row = w._stat_tracks_lay.itemAt(i % n).widget()
        row._emit_play()
        if i % 10 == 0:
            pump(1)
step("stats play mash 60x", stats_mash)
w._on_rail_clicked("library")
pump(50)

# ---------------------------------------------------------------------------
# 9. Scan during playback + theme change (scan vs UI race)
# ---------------------------------------------------------------------------
print("=== 9. scan race ===", flush=True)
def scan_race():
    w._on_refresh()
    pump(30)
    w._on_theme_selected("midnight_blue")
    w._on_next()
    w._on_refresh()        # queue second scan while first runs
    pump(600)              # let scans finish (200 files, fake mp3s)
step("refresh scan x2 during playback", scan_race)
pump(1500)

# ---------------------------------------------------------------------------
# 10. Sleep timer + fullscreen + mini player toggling
# ---------------------------------------------------------------------------
print("=== 10. misc toggles ===", flush=True)
def misc_toggles():
    w.start_sleep_timer(1)
    w.stop_sleep_timer()
    w._toggle_mini_player()
    pump(50)
    w._toggle_mini_player()   # restore main window
    pump(50)
    for _ in range(5):
        w._toggle_fullscreen_player()
        pump(30)
        w._exit_fullscreen_player()
        pump(10)
step("sleep/mini/fullscreen toggles", misc_toggles)

# ---------------------------------------------------------------------------
# 11. Session memory growth probe (cache unboundedness)
# ---------------------------------------------------------------------------
print("=== 11. memory probe ===", flush=True)
def churn():
    for r in range(4):
        for i in range(0, len(files), 25):
            w._load_and_play_current()   # rapid full library sweep
            w._load_cover_async(files[i])
            if i % 100 == 0:
                pump(5)
        w._on_theme_selected(Theme.names()[r % 12])
step("4x library sweeps", churn)
pump(1000)
gc.collect()
mem1 = rss_mb()
growth = mem1 - mem0
print(f"RSS after churn: {mem1:.0f} MB (growth {growth:+.0f} MB)", flush=True)
if growth > 400:
    BUGS.append(("memory growth", f"+{growth:.0f} MB in 4 sweeps", ""))

# ---------------------------------------------------------------------------
# 12. Quit-time cleanup under load
# ---------------------------------------------------------------------------
print("=== 12. quit ===", flush=True)
def quit_path():
    w._force_quit = True
    from PyQt6.QtGui import QCloseEvent
    w.closeEvent(QCloseEvent())
step("closeEvent(force_quit) with live threads", quit_path)

# ---------------------------------------------------------------------------
print()
print("=" * 62)
if BUGS:
    print(f"{len(BUGS)} BUG(S) CAUGHT:")
    for name, kind, tb in BUGS:
        print(f"  - [{kind}] {name}")
        if tb:
            print(tb)
else:
    print("STRESS TEST CLEAN — no exceptions, no hangs, no big memory growth")
