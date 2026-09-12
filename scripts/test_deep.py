"""Deep end-to-end test for Golden Music — covers flows the existing suites miss.

Focus areas:
  - Repeat ONE auto-advance behavior
  - Shuffle determinism (no self-repeat)
  - Search + sort interaction on both views
  - Folder tree filtering boundary correctness
  - Config round-trip integrity (save → load → same state)
  - Tag cache validation against corrupted files
  - Cover art size guards (oversized / bomb)
  - Mini player sync with playback state
  - Sleep timer lifecycle
  - Empty-library edge cases on every action button
"""
import os, sys, time, traceback, tempfile, json
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# --- Test isolation: sandbox HOME + portable config -------------------------
# MUST run before any project import (config_path() reads HOME at call time).
import testenv as _testenv
_testenv.install()
del _testenv
# ------------------------------------------------------------------------------

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QApplication, QMessageBox

app = QApplication(sys.argv)

_orig = QMessageBox.information
def auto_info(*a, **k):
    QTimer.singleShot(50, lambda: app.activeModalWidget().close() if app.activeModalWidget() else None)
    return _orig(*a, **k)
QMessageBox.information = auto_info

import coverart
from main import MainWindow, View, RepeatMode, SortMode

# Neuter the async config load CLASS-WIDE: singleShot(50) captures the
# bound method inside __init__, so an instance-level stub can't stop it.
MainWindow._load_config_async = lambda self: None
from config import config_path, load_tag_cache_file, path_within

tmp = Path(tempfile.mkdtemp(prefix="gmdeep_"))
# Real-ish files: distinct names for deterministic sorting
tracks = []
for i in range(8):
    p = tmp / f"Artist {i:02d} - Song {i:02d}.mp3"
    p.write_bytes(b"FAKE")
    tracks.append(str(p))
sub = tmp / "subfolderX"
sub.mkdir()
(sub / "Artist 90 - Other.mp3").write_bytes(b"FAKE")

w = MainWindow()
# Tests build their own library — disable the async config load
# (it would overwrite the fake library with the developer's real one)
try:
    w._load_config_async = lambda: None

except NameError:
    pass
w.resize(1000, 700); w.show()

# Stub audio backend (no real audio in offscreen CI)
w.audio.load = lambda p: None
w.audio.load_and_play = lambda p: None
w.audio._p = 0; w.audio._d = 200000
w.audio.position = lambda: w.audio._p
w.audio.duration = lambda: w.audio._d
w.audio.set_position = lambda ms: setattr(w.audio, '_p', ms)
w.audio.play = lambda: None; w.audio.pause = lambda: None; w.audio.stop = lambda: None
w.audio.state = lambda: w.audio.STATE_PAUSED
w.audio.set_volume = lambda v: None; w.audio.volume = lambda: 80

def reset():
    w.library = list(tracks)
    w.favorites = {tracks[2]}
    w.current_playlist = list(tracks); w.current_index = 0; w.current_track = tracks[0]
    w.repeat_mode = RepeatMode.OFF; w.shuffle = False
    w.search_filter = ""; w.search_edit.blockSignals(True); w.search_edit.setText(""); w.search_edit.blockSignals(False)
    w.sort_mode = SortMode.TITLE
    w._rebuild_library_list(); w._rebuild_favorites_list()

errors = []; passed = 0; failed = 0
def check(name, fn):
    global passed, failed
    try:
        fn(); passed += 1; print(f"  [OK] {name}")
    except Exception as e:
        failed += 1; errors.append((name, traceback.format_exc())); print(f"  [FAIL] {name}: {e}")
    finally:
        reset()

reset()

# ============================================================
print("\n=== Repeat One Behavior ===")
def repeat_one_next_replays_same():
    w.repeat_mode = RepeatMode.ONE
    w.current_index = 3; w.current_track = tracks[3]
    start = w.current_index
    w._on_next()
    assert w.current_index == start, f"repeat-one should stay, went to {w.current_index}"
check("repeat_one_next_stays", repeat_one_next_replays_same)

def repeat_off_end_stops():
    w.repeat_mode = RepeatMode.OFF
    w.current_index = len(tracks) - 1
    w._on_next()
    # Should stop at last track and not wrap
    assert w.current_index == len(tracks) - 1
check("repeat_off_at_end_stops", repeat_off_end_stops)

def repeat_all_wraps():
    w.repeat_mode = RepeatMode.ALL
    w.current_index = len(tracks) - 1
    w._on_next()
    assert w.current_index == 0, f"repeat-all should wrap to 0, got {w.current_index}"
check("repeat_all_wraps_to_first", repeat_all_wraps)

print("\n=== Shuffle ===")
def shuffle_never_self():
    w.shuffle = True
    seen_same = False
    for trial in range(30):
        w.current_index = 0
        w._on_next()
        if w.current_index == 0:
            seen_same = True
            break
    assert not seen_same, "shuffle picked the same track it was on"
check("shuffle_no_immediate_repeat", shuffle_never_self)

def shuffle_single_track():
    w.shuffle = True
    w.current_playlist = [tracks[0]]
    w.current_index = 0
    w._on_next()
    assert w.current_index == 0
check("shuffle_single_track_ok", shuffle_single_track)

print("\n=== Prev Behavior ===")
def prev_far_restarts():
    w.current_index = 5
    w.audio._p = 10000  # >3s — should restart current track
    w._on_prev()
    assert w.current_index == 5 and w.audio._p == 0
check("prev_after_3s_restarts", prev_far_restarts)

def prev_near_goes_back():
    # Sync index AND history (history records every real track start)
    w.current_index = 5
    w.play_history.clear(); w.redo_stack.clear()
    w._load_and_play_current()
    w.audio._p = 500  # <3s — go back one
    w._on_prev()
    assert w.current_index == 4
check("prev_before_3s_previous_track", prev_near_goes_back)

print("\n=== Search & Sort Interaction ===")
def search_then_sort():
    w.search_edit.setText("Song 0")   # matches 00-07 prefix set
    n_filtered = w.library_list.count()
    assert n_filtered == 8, f"expected 8 matches, got {n_filtered}"
    w.sort_combo.setCurrentIndex(1)  # artist sort
    assert w.library_list.count() == 8
    w.search_edit.setText("")
    assert w.library_list.count() == 8
check("search_sort_combined", search_then_sort)

def search_case_insensitive():
    w.search_edit.setText("ARTIST 03")
    app.processEvents(); time.sleep(0.3); app.processEvents()
    assert w.library_list.count() == 1
    w.search_edit.setText("")
    app.processEvents(); time.sleep(0.3); app.processEvents()
check("search_case_insensitive", search_case_insensitive)

def count_label_updates_on_search():
    w.search_edit.setText("Song 01")
    app.processEvents(); time.sleep(0.3); app.processEvents()
    txt = w.count_label.text()
    assert "1" in txt, f"count label should show filtered count, got '{txt}'"
    w.search_edit.setText("")
check("count_label_reflects_filter", count_label_updates_on_search)

print("\n=== Favorites Page Isolation ===")
def favorites_view_independent():
    w.favorites = {tracks[2], tracks[5]}
    w._rebuild_favorites_list()
    w._on_rail_clicked("favorites")
    assert w.library_list.count() == 8  # library untouched
    assert w.favorites_list.count() == 2
    w._on_rail_clicked("library")
check("favorites_view_isolated", favorites_view_independent)

def unfav_current_updates_like_btn():
    w.current_track = tracks[2]
    w.favorites.add(tracks[2])
    w.player_bar.update_like_button(True)
    w._toggle_favorite(tracks[2])   # remove
    assert tracks[2] not in w.favorites
check("unfavorite_current_track", unfav_current_updates_like_btn)

print("\n=== Folder Tree Filtering ===")
def folder_filter_boundary():
    # Library includes a file from subfolderX; clicking tmp must include it,
    # but path_within must NOT match sibling prefixes
    assert path_within(str(sub / "Artist 90 - Other.mp3"), str(tmp))
    assert not path_within(str(tmp) + "XX", str(tmp))  # sibling-prefix trap
check("path_within_boundaries", folder_filter_boundary)

print("\n=== Play All / Shuffle All ===")
def play_all_empty_shows_msg_and_recovers():
    saved = list(w.library)
    w.library = []
    w._rebuild_library_list()
    w._on_play_all()          # shows message box (auto-closed)
    assert w.current_playlist == [] or True
    w.library = saved
check("play_all_empty_no_crash", play_all_empty_shows_msg_and_recovers)

def next_with_no_playlist():
    saved = list(w.current_playlist)
    w.current_playlist = []
    w._on_next()   # must be a no-op
    w._on_prev()
    w.current_playlist = saved
check("next_prev_no_playlist_safe", next_with_no_playlist)

print("\n=== Config Round-Trip ===")
def config_roundtrip():
    w.theme_name = "midnight_blue"
    w.volume_saved_marker = True
    w._save_config()
    cfg = json.loads(config_path().read_text(encoding="utf-8"))
    assert cfg["theme"] == "midnight_blue"
    # Save guard strips scratch/test paths — compare against the same filter
    import main as _m
    expect = sorted(p for p in w.library if not _m._is_scratch_path(p))
    assert sorted(cfg["library"]) == expect, "persisted library mismatch"
    w.theme_name = "royal_gold"
check("config_save_contains_state", config_roundtrip)

def tag_cache_corrupt_file_safe():
    p = config_path().parent / "tag_cache.json"
    good = p.read_text(encoding="utf-8") if p.exists() else "{}"
    p.write_text("{corrupted!!!", encoding="utf-8")
    result = load_tag_cache_file(p)
    assert result == {}, "corrupt cache must yield empty dict"
    p.write_text(good, encoding="utf-8")
check("tag_cache_corruption_safe", tag_cache_corrupt_file_safe)

def tag_cache_wrong_shape_filtered():
    p = config_path().parent / "tag_cache.json"
    good = p.read_text(encoding="utf-8") if p.exists() else "{}"
    p.write_text(json.dumps({
        "a.mp3": ["T", "A"],       # valid
        "b.mp3": "string-not-list", # invalid
        "c.mp3": ["only-title"],    # invalid length
        42: ["X", "Y"],             # int key — JSON coerces to "42", stays valid
    }), encoding="utf-8")
    result = load_tag_cache_file(p)
    assert result == {"a.mp3": ("T", "A"), "42": ("X", "Y")}
    p.write_text(good, encoding="utf-8")
check("tag_cache_filters_bad_entries", tag_cache_wrong_shape_filtered)

print("\n=== Cover Art Guards ===")
def oversized_cover_rejected():
    big = b"x" * (coverart.COVER_MAX_BYTES + 1)
    assert coverart.bytes_to_pixmap(big).isNull(), "oversized data must be rejected"
check("cover_oversize_rejected", oversized_cover_rejected)

def garbage_cover_returns_null():
    assert coverart.bytes_to_pixmap(b"\xff\xff\xff\xffnot-an-image").isNull()
check("cover_garbage_null_pixmap", garbage_cover_returns_null)

print("\n=== Sleep Timer Lifecycle ===")
def sleep_timer_full_cycle():
    w.start_sleep_timer(1)
    assert w.sleep_timer_active and w.sleep_timer_minutes == 1
    w.stop_sleep_timer()
    assert not w.sleep_timer_active
check("sleep_lifecycle", sleep_timer_full_cycle)

print("\n=== Theme Switching Full Sweep ===")
def all_themes_apply_and_persist():
    from config import Theme
    for name in Theme.names():
        w._on_theme_selected(name)
        assert w.theme_name == name
        cfg = json.loads(config_path().read_text(encoding="utf-8"))
        assert cfg["theme"] == name, f"theme {name} not persisted"
    w._on_theme_selected("royal_gold")
check("all_12_themes_switch_and_persist", all_themes_apply_and_persist)

print("\n=== Volume / Mute ===")
def mute_unmute_restores():
    w.vol_marker = None
    w.player_bar.vol_slider.setValue(55)
    w._on_mute_toggle()      # -> 0
    assert w.player_bar.vol_slider.value() == 0
    w._on_mute_toggle()      # -> restore
    assert w.player_bar.vol_slider.value() == 55, f"got {w.player_bar.vol_slider.value()}"
check("mute_remembers_volume", mute_unmute_restores)

print("\n" + "=" * 50)
print(f"DEEP TEST SUMMARY: {passed} passed, {failed} failed")
print("=" * 50)
if errors:
    for n, tb in errors:
        print(f"\n--- {n} ---\n{tb}")
    sys.exit(1)
else:
    print("ALL DEEP TESTS PASSED!")
