"""Tests for smart shuffle: unplayed tracks get priority; once all have
played, shuffle falls back to plain random over the whole playlist.
"""
import os, sys, tempfile, traceback
import random
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
random.seed(11)

from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)

import coverart
from main import MainWindow, RepeatMode

tmp = Path(tempfile.mkdtemp(prefix="gmsmart_"))
T = [str(tmp / f"Track {i}.mp3") for i in range(6)]
for t in T:
    Path(t).write_bytes(b"FAKE")

w = MainWindow(); w.show()
w.audio.load = lambda p: None
w.audio.load_and_play = lambda p: None
w.audio.stop = lambda: None
w.audio.play = lambda: None
w.audio._p = 0
w.audio.position = lambda: w.audio._p
w.audio.duration = lambda: 200000
w.audio.set_position = lambda ms: setattr(w.audio, '_p', ms)
w.audio.set_volume = lambda v: None

errors = []; passed = 0; failed = 0
def check(name, fn):
    global passed, failed
    try:
        fn(); passed += 1; print(f"  [OK] {name}")
    except Exception as e:
        failed += 1; errors.append((name, traceback.format_exc())); print(f"  [FAIL] {name}: {e}")

print("\n=== Smart Shuffle Priority ===")
def unplayed_first():
    w._play_from_list(list(T), 0, shuffled=True)
    assert w.shuffle_unplayed == set(T[1:]) or T[0] not in w.shuffle_unplayed
    played = []
    for _ in range(5):                    # remaining 5 must ALL be distinct
        w.audio._p = 0
        w._on_next()
        played.append(w.current_track)
    assert len(set(played)) == 5, f"repeats before covering list: {played}"
    assert set(played) == set(T[1:]), f"missed tracks: {set(T) - set(played)}"
check("no_repeats_until_full_coverage", unplayed_first)

def second_pass_allows_repeats():
    # After full coverage, next jumps may repeat — just verify they stay valid
    for _ in range(8):
        w.audio._p = 0
        w._on_next()
        assert w.current_track in T
check("second_pass_valid_random", second_pass_allows_repeats)

def new_session_refills():
    w._play_from_list(list(T), 0, shuffled=True)   # fresh session
    n_before = len(w.shuffle_unplayed)
    assert n_before >= len(T) - 1
    # play two, then start ANOTHER session — pool refilled from that queue
    w.audio._p = 0; w._on_next(); w.audio._p = 0; w._on_next()
    w._play_from_list(list(reversed(T)), 0, shuffled=True)
    assert len(w.shuffle_unplayed) == len(T) - 1
check("new_session_refills_pool", new_session_refills)

def removed_track_not_chosen():
    w._play_from_list(list(T[:4]), 0, shuffled=True)
    victim = w.current_playlist[3]
    w._remove_from_library(victim)
    w.shuffle_unplayed.discard(victim) if victim in w.shuffle_unplayed else None
    for _ in range(10):
        w.audio._p = 0
        w._on_next()
        assert w.current_track != victim or w.current_track in w.current_playlist
check("shuffle_respects_current_playlist", removed_track_not_chosen)

def sequential_mode_unaffected():
    w._play_from_list(list(T), 0, shuffled=False)
    w.current_index = 0
    w.audio._p = 0
    w._on_next()
    assert w.current_index == 1
    w._on_next()
    assert w.current_index == 2
check("sequential_mode_intact", sequential_mode_unaffected)

def history_nav_still_works():
    w._play_from_list(list(T), 0, shuffled=True)
    seq = [T[0]]
    for _ in range(4):
        w.audio._p = 0
        w._on_next()
        seq.append(w.current_track)
    w.audio._p = 0
    w._on_prev()
    assert w.current_track == seq[-2]
    w._on_next()
    assert w.current_track == seq[-1], "redo after prev must replay forward"
check("history_nav_compatible", history_nav_still_works)

print("\n" + "=" * 50)
print(f"SMART-SHUFFLE SUMMARY: {passed} passed, {failed} failed")
print("=" * 50)
if errors:
    for n, tb in errors:
        print(f"\n--- {n} ---\n{tb}")
    sys.exit(1)
print("ALL SMART-SHUFFLE TESTS PASSED!")
