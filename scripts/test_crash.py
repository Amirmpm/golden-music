"""
Comprehensive crash test for Golden Music v5.0.
Simulates real user interactions that could cause crashes.
"""
import os, sys, time, traceback, tempfile, gc
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtCore import Qt, QTimer, QBuffer, QPoint, QPointF
from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtGui import QPixmap, QColor, QPainter, QLinearGradient, QBrush, QMouseEvent

app = QApplication(sys.argv)
app.setApplicationName("GMCrashTest")

_orig = QMessageBox.information
def auto_info(*a, **k):
    QTimer.singleShot(50, lambda: app.activeModalWidget().close() if app.activeModalWidget() else None)
    return _orig(*a, **k)
QMessageBox.information = auto_info

import coverart
from main import MainWindow, APP_NAME, View, RepeatMode

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
tmp = Path(tempfile.mkdtemp(prefix="gm_crash_"))
tracks = []
for i in range(20):
    p = tmp / f"Artist {i:02d} - Song {i:02d}.mp3"; p.write_bytes(b"FAKE")
    tracks.append(str(p)); coverart._cover_cache[str(p)] = COVER

w = MainWindow(); w.resize(1000, 700); w.show()

def fake_load(path): pass
def fake_load_and_play(path): pass
w.audio.load = fake_load; w.audio.load_and_play = fake_load_and_play
w.audio._p = 0; w.audio._d = 200000
w.audio.position = lambda: w.audio._p
w.audio.duration = lambda: w.audio._d
w.audio.set_position = lambda ms: setattr(w.audio, '_p', ms)
w.audio.play = lambda: None; w.audio.pause = lambda: None; w.audio.stop = lambda: None
w.audio.state = lambda: w.audio.STATE_PAUSED
w.audio.set_volume = lambda v: None

w.library = list(tracks); w.favorites = {tracks[0], tracks[5], tracks[10]}
w.current_playlist = list(tracks); w.current_index = 0; w.current_track = tracks[0]
w._rebuild_library_list(); w._rebuild_favorites_list()

crashes = []; passed = 0
def check(name, fn):
    global passed
    try:
        fn(); passed += 1; print(f"  [OK] {name}")
    except Exception as e:
        crashes.append((name, traceback.format_exc())); print(f"  [CRASH] {name}: {e}")

def make_mouse(pos, button=Qt.MouseButton.LeftButton, buttons=None, type_=QMouseEvent.Type.MouseButtonPress):
    if buttons is None: buttons = button
    return QMouseEvent(type_, QPointF(pos), QPointF(pos), button, buttons, Qt.KeyboardModifier.NoModifier)

print("\n=== 1. Rapid Slider Clicks (100 each) ===")
def rapid_seeks():
    s = w.player_bar.seek_slider
    r = s.rect()
    for i in range(100):
        x = (i * 37) % max(1, r.width())
        pos = QPoint(x, r.height() // 2)
        s.mousePressEvent(make_mouse(pos))
        s.mouseReleaseEvent(make_mouse(pos, type_=QMouseEvent.Type.MouseButtonRelease))
check("100 rapid seek clicks", rapid_seeks)

def rapid_vols():
    s = w.player_bar.vol_slider
    r = s.rect()
    for i in range(100):
        x = (i * 13) % max(1, r.width())
        pos = QPoint(x, r.height() // 2)
        s.mousePressEvent(make_mouse(pos))
        s.mouseReleaseEvent(make_mouse(pos, type_=QMouseEvent.Type.MouseButtonRelease))
check("100 rapid volume clicks", rapid_vols)

print("\n=== 2. Theme Switching (all 12 themes × 3) ===")
def theme_stress():
    themes = ["obsidian", "royal_gold", "midnight_blue", "amethyst",
              "neon_rose", "emerald_night", "porcelain", "ivory",
              "azure", "lilac", "blush", "sage"]
    for _ in range(3):
        for name in themes:
            w._on_theme_selected(name)
            w.player_bar.set_theme(w.theme)
            w._apply_theme()
            app.processEvents()
check("36 theme switches", theme_stress)

print("\n=== 3. Rapid Play/Pause/Next/Prev ===")
def rapid_playback():
    for i in range(50):
        if i % 4 == 0: w._on_play_pause()
        elif i % 4 == 1: w._on_next()
        elif i % 4 == 2: w._on_prev()
        else: w._on_like_toggled()
        app.processEvents()
check("50 rapid playback operations", rapid_playback)

print("\n=== 4. Slider + Theme Switch Together ===")
def slider_theme_mix():
    themes = ["royal_gold", "porcelain", "amethyst", "midnight_blue", "neon_rose"]
    for i in range(50):
        # Switch theme
        w._on_theme_selected(themes[i % len(themes)])
        # Click slider
        s = w.player_bar.seek_slider
        r = s.rect()
        pos = QPoint((i * 20) % max(1, r.width()), r.height() // 2)
        s.mousePressEvent(make_mouse(pos))
        s.mouseReleaseEvent(make_mouse(pos, type_=QMouseEvent.Type.MouseButtonRelease))
        # Click volume
        s2 = w.player_bar.vol_slider
        r2 = s2.rect()
        pos2 = QPoint((i * 7) % max(1, r2.width()), r2.height() // 2)
        s2.mousePressEvent(make_mouse(pos2))
        s2.mouseReleaseEvent(make_mouse(pos2, type_=QMouseEvent.Type.MouseButtonRelease))
        app.processEvents()
check("50 slider+theme mix", slider_theme_mix)

print("\n=== 5. Cover Art Loading Stress ===")
def cover_stress():
    for i in range(30):
        path = tracks[i % len(tracks)]
        w._load_cover_async(path)
        app.processEvents()
        time.sleep(0.02)
check("30 cover loads", cover_stress)

print("\n=== 6. Library Rebuild Stress ===")
def rebuild_stress():
    for i in range(20):
        w._rebuild_library_list()
        w._rebuild_favorites_list()
        app.processEvents()
check("20 library rebuilds", rebuild_stress)

print("\n=== 7. Search + Sort Stress ===")
def search_sort_stress():
    for i in range(20):
        w.search_edit.setText(f"Artist {i:02d}")
        app.processEvents()
        w.sort_combo.setCurrentIndex(i % 4)
        app.processEvents()
    w.search_edit.setText("")
check("20 search+sort cycles", search_sort_stress)

print("\n=== 8. Mini Player Toggle ===")
def mini_stress():
    for i in range(10):
        w._toggle_mini_player()
        app.processEvents()
        time.sleep(0.05)
        if w.mini_player and w.mini_player.isVisible():
            w._toggle_mini_player()
            app.processEvents()
check("10 mini player toggles", mini_stress)

print("\n=== 9. Context Menu ===")
def context_menu_stress():
    for i in range(10):
        # Simulate right-click on different items
        item = w.library_list.item(i % w.library_list.count())
        if item:
            w._play_track_from_list(item.data(Qt.ItemDataRole.UserRole))
            app.processEvents()
check("10 context menu actions", context_menu_stress)

print("\n=== 10. GC + Memory ===")
def gc_stress():
    gc.collect()
    w._on_play_all()
    app.processEvents()
    gc.collect()
check("GC during playback", gc_stress)

print("\n=== 11. Slider Drag in All Themes ===")
def drag_all_themes():
    themes = ["royal_gold", "porcelain", "amethyst", "midnight_blue", "neon_rose"]
    for name in themes:
        w._on_theme_selected(name)
        s = w.player_bar.seek_slider
        r = s.rect()
        # Press
        s.mousePressEvent(make_mouse(QPoint(5, r.height() // 2)))
        # Drag
        for x in range(10, r.width(), 20):
            s.mouseMoveEvent(make_mouse(QPoint(x, r.height() // 2),
                                         buttons=Qt.MouseButton.LeftButton,
                                         type_=QMouseEvent.Type.MouseMove))
        # Release
        s.mouseReleaseEvent(make_mouse(QPoint(r.width() // 2, r.height() // 2),
                                        type_=QMouseEvent.Type.MouseButtonRelease))
        app.processEvents()
check("drag in 5 themes", drag_all_themes)

print("\n" + "="*50)
print(f"CRASH TEST SUMMARY: {passed} passed, {len(crashes)} crashes")
print("="*50)
if crashes:
    for n, tb in crashes: print(f"\n--- {n} ---\n{tb}")
    sys.exit(1)
else:
    print("NO CRASHES DETECTED!")
    sys.exit(0)
