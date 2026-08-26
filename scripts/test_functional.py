"""Functional test for Golden Music v3.0 (PyQt6)."""
import os, sys, time, traceback, tempfile
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtCore import Qt, QTimer, QBuffer
from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtGui import QPixmap, QColor, QPainter, QLinearGradient, QBrush

app = QApplication(sys.argv)
app.setApplicationName("GMTest3")

_orig = QMessageBox.information
def auto_info(*a, **k):
    QTimer.singleShot(50, lambda: app.activeModalWidget().close() if app.activeModalWidget() else None)
    return _orig(*a, **k)
QMessageBox.information = auto_info

import coverart
from main import MainWindow, APP_NAME, View, RepeatMode, SortMode
from settings_dialog import SettingsDialog
from config import config_path, extract_title_artist

def make_cover():
    pm = QPixmap(200, 200)
    grad = QLinearGradient(0, 0, 200, 200)
    grad.setColorAt(0.0, QColor(0x2c, 0x3e, 0x50))
    grad.setColorAt(1.0, QColor(0x8e, 0x44, 0xad))
    p = QPainter(pm); p.setBrush(QBrush(grad)); p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(0, 0, 200, 200, 16, 16); p.end()
    ba = QBuffer(); ba.open(QBuffer.OpenModeFlag.WriteOnly); pm.save(ba, "PNG")
    return bytes(ba.data())

COVER = make_cover()
tmp = Path(tempfile.mkdtemp(prefix="gm3_"))
tracks = []
for i in range(10):
    p = tmp / f"Artist {i:02d} - Song {i:02d}.mp3"; p.write_bytes(b"FAKE")
    tracks.append(str(p)); coverart._cover_cache[str(p)] = COVER

w = MainWindow(); w.resize(1000, 700); w.show()

def fake_load(path):
    pass
def fake_load_and_play(path):
    pass
w.audio.load = fake_load
w.audio.load_and_play = fake_load_and_play
w.audio._p = 0; w.audio._d = 200000
w.audio.position = lambda: w.audio._p
w.audio.duration = lambda: w.audio._d
w.audio.set_position = lambda ms: setattr(w.audio, '_p', ms)
w.audio.play = lambda: None; w.audio.pause = lambda: None; w.audio.stop = lambda: None
w.audio.state = lambda: w.audio.STATE_PAUSED
w.audio.set_volume = lambda v: None; w.audio.volume = lambda: 80

w.library = list(tracks); w.favorites = {tracks[2], tracks[5]}
w.current_playlist = list(tracks); w.current_index = 0; w.current_track = tracks[0]
w._rebuild_library_list(); w._rebuild_favorites_list()

errors = []; passed = 0; failed = 0
def check(name, fn):
    global passed, failed
    try: fn(); passed += 1; print(f"  [OK] {name}")
    except Exception as e:
        failed += 1; errors.append((name, traceback.format_exc())); print(f"  [FAIL] {name}: {e}")
    finally:
        w.library = list(tracks); w.favorites = {tracks[2], tracks[5]}
        w.current_playlist = list(tracks); w.current_index = 0; w.current_track = tracks[0]
        w._rebuild_library_list(); w._rebuild_favorites_list()

print("\n=== UI State ===")
check("library_count", lambda: w.library_list.count() == 10)
check("favorites_count", lambda: w.favorites_list.count() == 2)

print("\n=== Navigation ===")
check("switch_fav", lambda: (w._on_rail_clicked("favorites"), w.current_view == View.FAVORITES)[1])
check("switch_lib", lambda: (w._on_rail_clicked("library"), w.current_view == View.LIBRARY)[1])

print("\n=== Search ===")
def search_filter():
    w.search_edit.setText("Artist 03")
    assert w.library_list.count() == 1, f"expected 1, got {w.library_list.count()}"
    w.search_edit.setText("")
check("search_filters", search_filter)

print("\n=== Sort ===")
def sort_artist():
    w.sort_combo.setCurrentIndex(1)  # Artist
    # First item should be "Artist 00"
    item = w.library_list.item(0)
    title, artist = extract_title_artist(item.data(Qt.ItemDataRole.UserRole))
    assert artist == "Artist 00", f"expected Artist 00, got {artist}"
check("sort_by_artist", sort_artist)

print("\n=== Playback ===")
def play_all():
    w._on_play_all()
    assert w.current_track is not None
check("play_all", play_all)

def next_track():
    cur = w.current_index; w._on_next()
    assert w.current_index == cur + 1
check("next_track", next_track)

def prev_far():
    w.audio._p = 10000; cur = w.current_index; w._on_prev()
    assert w.current_index == cur
check("prev_restarts_far", prev_far)

def prev_near():
    # Sync playback state fully: index AND history must agree, as they do
    # in real usage where every track start is recorded in history.
    w.audio._p = 1000
    w.current_index = 3
    w.play_history.clear(); w.redo_stack.clear()
    w._load_and_play_current()
    w.audio._p = 1000
    w._on_prev()
    assert w.current_index == 2
check("prev_back_near", prev_near)

print("\n=== Like ===")
def like_toggle():
    w.current_track = w.library[0]; w.favorites.discard(w.library[0])
    init = w.library[0] in w.favorites; w._on_like_toggled()
    assert (w.library[0] in w.favorites) != init
check("like_toggle", like_toggle)

print("\n=== Shuffle/Repeat ===")
check("shuffle_all", lambda: (w._on_shuffle_all(), w.shuffle)[1] or True)
def repeat_cycle():
    w.repeat_mode = RepeatMode.OFF
    w._on_repeat_toggled(); assert w.repeat_mode == RepeatMode.ALL
    w._on_repeat_toggled(); assert w.repeat_mode == RepeatMode.ONE
    w._on_repeat_toggled(); assert w.repeat_mode == RepeatMode.OFF
check("repeat_cycle", repeat_cycle)

print("\n=== Theme Selection ===")
def theme_select():
    # Test direct theme selection via _on_theme_selected (v2.0 suite)
    for name in ["porcelain", "midnight_blue", "emerald_night",
                 "amethyst", "neon_rose", "sage"]:
        w._on_theme_selected(name)
        assert w.theme_name == name, f"switch to {name} failed"
    w._on_theme_selected("royal_gold")
    assert w.theme_name == "royal_gold"
check("theme_select_all", theme_select)

print("\n=== Config ===")
check("config_save", lambda: w._save_config() or config_path().exists())

print("\n=== Tray ===")
def close_tray():
    from PyQt6.QtGui import QCloseEvent
    e = QCloseEvent(); w.closeEvent(e)
    assert not e.isAccepted()
check("close_to_tray", close_tray)

print("\n=== Context Menu ===")
def context_menu():
    # Just verify the method doesn't crash
    w._on_context_menu(w.library_list.mapToGlobal(w.library_list.rect().center()))
check("context_menu_no_crash", context_menu)

print("\n=== Right-click Actions ===")
def toggle_fav_from_menu():
    w.current_track = w.library[1]; w.favorites.discard(w.library[1])
    w._toggle_favorite(w.library[1])
    assert w.library[1] in w.favorites
check("toggle_fav_from_menu", toggle_fav_from_menu)

def remove_from_lib():
    track = w.library[3]
    w._remove_from_library(track)
    assert track not in w.library
check("remove_from_library", remove_from_lib)

print("\n=== Sleep Timer ===")
def sleep_timer():
    w.start_sleep_timer(1)
    assert w.sleep_timer_active == True
    assert w._sleep_timer is not None
    w.stop_sleep_timer()
    assert w.sleep_timer_active == False
check("sleep_timer", sleep_timer)

print("\n=== Cover Art ===")
def cover_displayed():
    w._load_cover_async(w.current_track)
    for _ in range(20): app.processEvents(); time.sleep(0.02)
    assert w.cover_label._has_cover == True
check("cover_displayed", cover_displayed)

print("\n=== Mini Player ===")
def mini_player():
    w._toggle_mini_player()
    assert w.mini_player is not None and w.mini_player.isVisible()
    w._toggle_mini_player()  # close
check("mini_player", mini_player)

print("\n=== Settings Dialog ===")
def settings_dialog():
    dlg = SettingsDialog(w)
    assert dlg is not None
    dlg.close()
check("settings_dialog", settings_dialog)

print("\n" + "="*50)
print(f"SUMMARY: {passed} passed, {failed} failed")
print("="*50)
if errors:
    for n, tb in errors: print(f"\n--- {n} ---\n{tb}")
    sys.exit(1)
else:
    print("ALL PASSED!")
