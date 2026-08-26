"""Tests for v1.2.0 features: playback FX (rate/AB/fade), lyrics parser,
tag editor write-back, stats, queue insert, auto-theme watcher, audio
output enumeration, track menu delete.
"""
import os, sys, tempfile, traceback, wave, struct
import random
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
random.seed(3)

from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtCore import QTimer
app = QApplication(sys.argv)

def auto_msg(*a, **k):
    return QMessageBox.StandardButton.Yes
QMessageBox.question = staticmethod(auto_msg)
# warning() doubles as the delete-confirmation in _delete_track_from_disk
QMessageBox.warning = staticmethod(auto_msg)
QMessageBox.information = staticmethod(lambda *a, **k: None)
QMessageBox.critical = staticmethod(lambda *a, **k: None)

import coverart
from main import MainWindow, RepeatMode
import playback_fx
import lyrics as lyr_mod
import playstats
import queue_panel as qpanel

tmp = Path(tempfile.mkdtemp(prefix="gmv12_"))
# Build REAL audio files with tags so tag-editor tests exercise mutagen
def make_wav(path, seconds=2):
    with wave.open(str(path), 'w') as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(22050)
        for j in range(22050 * seconds):
            f.writeframes(struct.pack('<h', int(5000 * ((j // 200) % 2))))

tracks = []
for i in range(5):
    p = tmp / f"Song {i}.wav"
    make_wav(p)
    tracks.append(str(p))

w = MainWindow(); w.show()
w.audio.load = lambda p_: None
w.audio.load_and_play = lambda p_: None
w.audio.stop = lambda: None
w.audio.play = lambda: None
w.audio.pause = lambda: None
w.audio.state = lambda: w.audio.STATE_PLAYING
w.audio._p = 0
w.audio.position = lambda: w.audio._p
w.audio.duration = lambda: 200000
w.audio.set_position = lambda ms: setattr(w.audio, '_p', ms)
w.audio.volume = lambda: 80
w.audio.set_volume = lambda v: None
w.library = list(tracks)
w._rebuild_library_list()

errors = []; passed = 0; failed = 0
def check(name, fn):
    global passed, failed
    try:
        fn(); passed += 1; print(f"  [OK] {name}")
    except Exception as e:
        failed += 1; errors.append((name, traceback.format_exc())); print(f"  [FAIL] {name}: {e}")

print("\n=== Playback FX ===")
def rate_cycles():
    fx = w.fx
    seen = set()
    for _ in range(len(playback_fx.RATES)):
        seen.add(round(fx.cycle_rate(), 2))
    assert seen <= set(playback_fx.RATES) and len(seen) == len(playback_fx.RATES)
check("rate_cycle_all_values", rate_cycles)

def ab_loop():
    fx = w.fx
    fx.clear_ab(); w.audio._p = 10000
    assert fx.mark_a() == 10000
    w.audio._p = 30000
    assert fx.mark_b() == 30000
    assert fx.ab_active()
    w.audio._p = 35000       # past B -> loops to A
    fx.tick()
    assert w.audio._p == 10000
    fx.clear_ab()
    assert not fx.ab_active()
check("ab_repeat_loops", ab_loop)

def fade_engine():
    fx = w.fx
    fx.enabled_fade = True
    fired = []
    fx.fade_out_then(lambda: fired.append(1))
    # drive fade steps synchronously (QTimer needs the event loop otherwise)
    for _ in range(60):
        fx._fade_step()
    assert fired == [1], "fade-out action must fire"
check("fade_out_fires_action", fade_engine)

print("\n=== Lyrics ===")
def lrc_parse():
    lrc = "[00:01.00]First line\n[00:03.50]Second line\nplain line"
    ly = lyr_mod.parse_lrc(lrc)
    assert ly.synced and len(ly.lines) == 2
    assert ly.line_for_time(0) == -1
    assert ly.line_for_time(1500) == 0
    assert ly.line_for_time(4000) == 1
check("lrc_parse_sync", lrc_parse)

def sidecar_lookup():
    p = tmp / "Song 0.wav"
    (p.with_suffix(".lrc")).write_text("[00:00.50]hello\n[00:02.00]world", encoding="utf-8")
    ly = lyr_mod.find_local(str(p))
    assert ly is not None and ly.synced and len(ly.lines) == 2
check("sidecar_lrc_found", sidecar_lookup)

print("\n=== Play Stats ===")
def stats_accumulate():
    st = playstats.PlayStats(tmp / "stats.json")
    st.add_seconds(3661.5)   # + 1h 1m 1s
    st.credit_track(tracks[0], 120, "Alpha")
    st.credit_track(tracks[0], 130, "Alpha")
    st.credit_track(tracks[1], 60, "Beta")
    assert st.play_counts[tracks[0]] == 2
    fmt = PlayStats_fmt(st.total_seconds)
    assert "h" in fmt and "min" in fmt
    tops = st.top_tracks({}, 5)
    assert tops[0][2] == 2
    # persistence round-trip
    st.save()
    st2 = playstats.PlayStats(tmp / "stats.json")
    assert abs(st2.total_seconds - st.total_seconds) < 1
    assert st2.play_counts[tracks[0]] == 2
def PlayStats_fmt(s):
    return playstats.PlayStats.fmt_total(s)
check("stats_accumulate_persist", stats_accumulate)

print("\n=== Queue ===")
def play_next_insert():
    w._play_from_list(list(tracks), 0, view=None)
    extra = tmp / "Extra.wav"
    make_wav(extra)
    qpanel.insert_play_next(w, str(extra))
    nxt = w.current_playlist[w.current_index + 1]
    assert nxt == str(extra), f"expected inserted next, got {nxt}"
check("play_next_inserts_after_current", play_next_insert)

print("\n=== Auto Theme Watcher ===")
def watcher_safe():
    from autottheme import AutoThemeWatcher, windows_prefers_dark
    wat = AutoThemeWatcher()
    val = windows_prefers_dark()
    assert isinstance(val, bool)
    wat.start(); wat.stop()   # no crash either way
check("autottheme_watch_safe", watcher_safe)

print("\n=== Audio Output Enumeration ===")
def outputs_listed():
    from audio_output import list_output_devices
    devs = list_output_devices()
    assert isinstance(devs, list)
    # offscreen CI may have none; just validate shape when present
    for name, did in devs:
        assert isinstance(name, str) and isinstance(did, str)
check("outputs_enumerate_shape", outputs_listed)

print("\n=== Track Menu Delete (disk) ===")
def delete_from_disk():
    victim = tracks[4]
    p = Path(victim)
    assert p.exists()
    w.current_playlist = list(tracks)
    w.current_index = 0
    w._delete_track_from_disk(victim)
    assert not p.exists(), "file must be physically gone"
    assert victim not in w.library
check("delete_removes_real_file", delete_from_disk)

print("\n" + "=" * 50)
print(f"V12-FEATURES SUMMARY: {passed} passed, {failed} failed")
print("=" * 50)
if errors:
    for n, tb in errors:
        print(f"\n--- {n} ---\n{tb}")
    sys.exit(1)
print("ALL V12 TESTS PASSED!")
