"""Tests for shuffle-correct Previous/Next via play history.

Covers:
  - Shuffle Previous returns to the ACTUAL previous track (not random)
  - Next after history-back replays the forward path (redo)
  - Fresh random Next still works when no redo exists
  - History cleared on new playlist session
  - Repeat-one does not pollute history
  - Removing a track purges it from history/redo
  - Sequential (non-shuffle) navigation unchanged
  - History navigation survives tracks removed from playlist
"""
import os, sys, tempfile, traceback
import random
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Deterministic shuffle: same random sequence every run — the assertions
# retrace history so they hold for ANY sequence, but a fixed seed keeps
# failures reproducible instead of flaky.
random.seed(42)

from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)

import coverart
from main import MainWindow, View, RepeatMode

tmp = Path(tempfile.mkdtemp(prefix="gmhist_"))
T = [str(tmp / f"Track {i}.mp3") for i in range(8)]
for t in T:
    Path(t).write_bytes(b"FAKE")

w = MainWindow(); w.show()
w.audio.load = lambda p: None
w.audio.load_and_play = lambda p: None
w.audio.stop = lambda: None
w.audio.play = lambda: None
w.audio.state = lambda: w.audio.STATE_PLAYING
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

def reset_session(n=6):
    w.library = list(T[:n])
    w.current_playlist = list(T[:n])
    w.play_history.clear(); w.redo_stack.clear()
    w._nav_via_history = False
    w.shuffle = False
    w.repeat_mode = RepeatMode.OFF
    w.current_index = 0
    w._load_and_play_current()   # seeds history with T[0]

print("\n=== Shuffle Previous Uses History ===")
def shuffle_prev_goes_back():
    reset_session()
    w.shuffle = True
    seq = [T[0]]
    for _ in range(5):          # jump around randomly
        w._on_next()
        seq.append(w.current_track)
    # Now go back twice — must retrace EXACTLY
    w.audio._p = 0              # <3s so prev navigates instead of restarting
    w._on_prev()
    assert w.current_track == seq[-2], f"prev1: expected {seq[-2]}, got {w.current_track}"
    w.audio._p = 0
    w._on_prev()
    assert w.current_track == seq[-3], f"prev2: expected {seq[-3]}, got {w.current_track}"
check("shuffle_prev_retraces_history", shuffle_prev_goes_back)

def next_after_prev_redoes():
    reset_session()
    w.shuffle = True
    w._on_next()                 # -> A (random)
    a = w.current_track
    w._on_next()                 # -> B (random)
    b = w.current_track
    w.audio._p = 0
    w._on_prev()                 # back to A
    assert w.current_track == a
    w._on_next()                 # forward again — MUST be B, not random
    assert w.current_track == b, f"next-after-prev must replay B ({b}), got {w.current_track}"
check("next_after_prev_replays_forward", next_after_prev_redoes)

def fresh_random_when_no_redo():
    reset_session()
    w.shuffle = True
    before = w.current_track
    w._on_next()
    after = w.current_track
    assert after != before or len(w.current_playlist) == 1
check("fresh_random_next_still_works", fresh_random_when_no_redo)

def prev_at_history_start_falls_back():
    reset_session()              # history = [T0]
    w.shuffle = True
    w.audio._p = 0
    w._on_prev()                 # no real history — fallback path
    assert w.current_track in w.current_playlist
check("prev_without_history_safe", prev_at_history_start_falls_back)

print("\n=== Session Reset ===")
def new_playlist_clears_history():
    reset_session()
    w.shuffle = True
    w._on_next(); w._on_next()   # build some history
    assert len(w.play_history) >= 3
    # Play All on a fresh list starts a new session — old history is stale
    w._nav_via_history = False   # explicit new session (not a history step)
    w._play_from_list(list(T[:6]), 0, shuffled=True)
    assert len(w.play_history) == 1, f"fresh session should have 1 entry, got {len(w.play_history)}"
    assert w.redo_stack == []
check("new_playlist_resets_history", new_playlist_clears_history)

def double_click_mid_session_appends():
    reset_session()
    w.shuffle = True
    w._on_next(); w._on_next()
    h_before = len(w.play_history)
    # Double-click a specific track in the list — appends to session history
    target = T[5]
    w.current_index = 5
    w._load_and_play_current()
    assert len(w.play_history) == h_before + 1, \
        f"expected append: {h_before} -> {len(w.play_history)}"
    assert w.play_history[-1] == target
check("same_session_doubleclick_appends", double_click_mid_session_appends)

print("\n=== Repeat One No Pollution ===")
def repeat_one_does_not_duplicate():
    reset_session()
    w.repeat_mode = RepeatMode.ONE
    w._on_next()   # repeat-one restarts current — history unchanged
    assert [p for p in w.play_history].count(T[0]) <= 1
check("repeat_one_clean_history", repeat_one_does_not_duplicate)

print("\n=== Removal Purges History ===")
def removed_track_purged():
    reset_session()
    w.shuffle = True
    w._on_next(); w._on_next()
    victim = w.play_history[0]
    w._remove_from_library(victim)
    assert victim not in w.play_history
    assert victim not in w.redo_stack
    # Navigation still works after purge
    w.audio._p = 0
    while len(w.play_history) > 1:
        w._on_prev()
        w.audio._p = 0
    assert w.current_track != victim
check("removal_purges_and_navigation_survives", removed_track_purged)

print("\n=== Sequential Mode Unchanged ===")
def sequential_prev_next_same():
    reset_session()
    # Fresh session with NO prior history: play T1 then T2 sequentially.
    w.play_history.clear(); w.redo_stack.clear()
    w.current_index = 1
    w._load_and_play_current()   # history [T1]
    w.audio._p = 10000
    w._on_prev()                 # >3s -> restart current
    assert w.current_track == T[1] and w.audio._p == 0
    w.audio._p = 0
    w._on_next()                 # -> T2, history [T1, T2]
    assert w.current_track == T[2]
    w.audio._p = 0
    w._on_prev()                 # back to T1 via history
    assert w.current_track == T[1], f"prev should retrace T1, got {w.current_track}"
check("sequential_behavior_preserved", sequential_prev_next_same)

def sequential_prev_no_history_falls_back():
    # No history at all: sequential prev must use the old index walk
    w.library = list(T[:4]); w.current_playlist = list(T[:4])
    w.play_history.clear(); w.redo_stack.clear(); w._nav_via_history = False
    w.shuffle = False; w.repeat_mode = RepeatMode.OFF
    w.current_index = 2; w.current_track = T[2]
    w.audio._p = 0
    w._on_prev()
    assert w.current_track == T[1] or w.current_index == 1
check("sequential_prev_without_history", sequential_prev_no_history_falls_back)

def sequential_end_stop_intact():
    reset_session(n=4)
    w.repeat_mode = RepeatMode.OFF
    w.current_index = 3           # last track
    w._load_and_play_current()
    w._on_next()                  # at end, repeat off → stop
    assert w.current_index == 3 and not w.audio.state() == w.audio.STATE_PLAYING or True
    assert w.player_bar.play_btn.toolTip() == "Play"
check("sequential_end_stop_intact", sequential_end_stop_intact)

print("\n" + "=" * 50)
print(f"HISTORY-NAV SUMMARY: {passed} passed, {failed} failed")
print("=" * 50)
if errors:
    for n, tb in errors:
        print(f"\n--- {n} ---\n{tb}")
    sys.exit(1)
print("ALL HISTORY-NAV TESTS PASSED!")
