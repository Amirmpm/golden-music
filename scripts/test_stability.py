"""
Stability test for Golden Music v4.2.
Tests for crash scenarios:
- Click on seek bar at various positions
- Click on volume slider
- Rapid clicks
- Theme switching during playback
- Slider drag simulation
"""
import os, sys, time, traceback, tempfile
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# --- Test isolation: sandbox HOME + portable config -------------------------
# MUST run before any project import (config_path() reads HOME at call time).
import testenv as _testenv
_testenv.install()
del _testenv
# ------------------------------------------------------------------------------

from PyQt6.QtCore import Qt, QTimer, QBuffer, QPoint, QPointF
from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtGui import QPixmap, QColor, QPainter, QLinearGradient, QBrush, QMouseEvent

app = QApplication(sys.argv)
app.setApplicationName("GMStabilityTest")

_orig = QMessageBox.information
def auto_info(*a, **k):
    QTimer.singleShot(50, lambda: app.activeModalWidget().close() if app.activeModalWidget() else None)
    return _orig(*a, **k)
QMessageBox.information = auto_info

import coverart
from main import MainWindow, APP_NAME, View

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
tmp = Path(tempfile.mkdtemp(prefix="gm_stab_"))
tracks = []
for i in range(5):
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

w.library = list(tracks); w.favorites = {tracks[1]}
w.current_playlist = list(tracks); w.current_index = 1; w.current_track = tracks[1]
w._rebuild_library_list(); w._rebuild_favorites_list()

errors = []; passed = 0; failed = 0
def check(name, fn):
    global passed, failed
    try: fn(); passed += 1; print(f"  [OK] {name}")
    except Exception as e:
        failed += 1; errors.append((name, traceback.format_exc())); print(f"  [FAIL] {name}: {e}")

def make_mouse_event(pos, button=Qt.MouseButton.LeftButton, buttons=None, type_=QMouseEvent.Type.MouseButtonPress):
    if buttons is None: buttons = button
    return QMouseEvent(type_, QPointF(pos), QPointF(pos), button, buttons, Qt.KeyboardModifier.NoModifier)

print("\n=== Seek Bar Click Tests ===")

def click_seek_middle():
    slider = w.player_bar.seek_slider
    rect = slider.rect()
    center = QPoint(rect.width() // 2, rect.height() // 2)
    e = make_mouse_event(center)
    slider.mousePressEvent(e)
    e2 = make_mouse_event(center, type_=QMouseEvent.Type.MouseButtonRelease)
    slider.mouseReleaseEvent(e2)
    # Value should be around 500 (50% of 1000)
    assert 400 < slider.value() < 600, f"expected ~500, got {slider.value()}"
check("click_seek_middle", click_seek_middle)

def click_seek_quarter():
    slider = w.player_bar.seek_slider
    rect = slider.rect()
    pos = QPoint(rect.width() // 4, rect.height() // 2)
    e = make_mouse_event(pos)
    slider.mousePressEvent(e)
    e2 = make_mouse_event(pos, type_=QMouseEvent.Type.MouseButtonRelease)
    slider.mouseReleaseEvent(e2)
    assert 150 < slider.value() < 350, f"expected ~250, got {slider.value()}"
check("click_seek_quarter", click_seek_quarter)

def click_seek_far_right():
    slider = w.player_bar.seek_slider
    rect = slider.rect()
    pos = QPoint(rect.width() - 5, rect.height() // 2)
    e = make_mouse_event(pos)
    slider.mousePressEvent(e)
    e2 = make_mouse_event(pos, type_=QMouseEvent.Type.MouseButtonRelease)
    slider.mouseReleaseEvent(e2)
    assert slider.value() > 900, f"expected >900, got {slider.value()}"
check("click_seek_far_right", click_seek_far_right)

print("\n=== Volume Slider Click Tests ===")

def click_vol_middle():
    slider = w.player_bar.vol_slider
    rect = slider.rect()
    pos = QPoint(rect.width() // 2, rect.height() // 2)
    e = make_mouse_event(pos)
    slider.mousePressEvent(e)
    e2 = make_mouse_event(pos, type_=QMouseEvent.Type.MouseButtonRelease)
    slider.mouseReleaseEvent(e2)
    assert 40 < slider.value() < 60, f"expected ~50, got {slider.value()}"
check("click_vol_middle", click_vol_middle)

def click_vol_far_left():
    slider = w.player_bar.vol_slider
    rect = slider.rect()
    pos = QPoint(2, rect.height() // 2)
    e = make_mouse_event(pos)
    slider.mousePressEvent(e)
    e2 = make_mouse_event(pos, type_=QMouseEvent.Type.MouseButtonRelease)
    slider.mouseReleaseEvent(e2)
    assert slider.value() < 10, f"expected <10, got {slider.value()}"
check("click_vol_far_left", click_vol_far_left)

print("\n=== Rapid Click Tests ===")

def rapid_seek_clicks():
    slider = w.player_bar.seek_slider
    rect = slider.rect()
    for i in range(20):
        x = (i * 37) % rect.width()  # pseudo-random positions
        pos = QPoint(x, rect.height() // 2)
        e = make_mouse_event(pos)
        slider.mousePressEvent(e)
        e2 = make_mouse_event(pos, type_=QMouseEvent.Type.MouseButtonRelease)
        slider.mouseReleaseEvent(e2)
    # Should not crash
check("rapid_seek_clicks_no_crash", rapid_seek_clicks)

def rapid_vol_clicks():
    slider = w.player_bar.vol_slider
    rect = slider.rect()
    for i in range(20):
        x = (i * 13) % rect.width()
        pos = QPoint(x, rect.height() // 2)
        e = make_mouse_event(pos)
        slider.mousePressEvent(e)
        e2 = make_mouse_event(pos, type_=QMouseEvent.Type.MouseButtonRelease)
        slider.mouseReleaseEvent(e2)
check("rapid_vol_clicks_no_crash", rapid_vol_clicks)

print("\n=== Drag Tests ===")

def drag_seek():
    slider = w.player_bar.seek_slider
    rect = slider.rect()
    # Press at left
    pos1 = QPoint(5, rect.height() // 2)
    e = make_mouse_event(pos1, type_=QMouseEvent.Type.MouseButtonPress)
    slider.mousePressEvent(e)
    # Move to middle
    for i in range(5):
        x = 5 + int((rect.width() - 10) * (i + 1) / 10)
        pos = QPoint(x, rect.height() // 2)
        e = make_mouse_event(pos, type_=QMouseEvent.Type.MouseMove, buttons=Qt.MouseButton.LeftButton)
        slider.mouseMoveEvent(e)
    # Release
    e2 = make_mouse_event(QPoint(rect.width() // 2, rect.height() // 2), type_=QMouseEvent.Type.MouseButtonRelease)
    slider.mouseReleaseEvent(e2)
    # Just verify no crash (don't check exact value — QSlider internal state varies)
check("drag_seek_no_crash", drag_seek)

print("\n=== Theme Switch Stability ===")

def switch_all_themes():
    for name in ["obsidian", "royal_gold", "midnight_blue", "amethyst",
                 "neon_rose", "emerald_night", "porcelain", "ivory",
                 "azure", "lilac", "blush", "sage"]:
        w.theme_name = name
        w._apply_theme()
        # Click slider after theme change
        slider = w.player_bar.seek_slider
        rect = slider.rect()
        pos = QPoint(rect.width() // 2, rect.height() // 2)
        e = make_mouse_event(pos)
        slider.mousePressEvent(e)
        e2 = make_mouse_event(pos, type_=QMouseEvent.Type.MouseButtonRelease)
        slider.mouseReleaseEvent(e2)
check("switch_all_themes_with_slider_click", switch_all_themes)

print("\n=== Play Button Tests ===")

def click_play_button():
    # Just verify it doesn't crash
    w.player_bar.play_btn.click()
check("click_play_button_no_crash", click_play_button)

def hover_play_button():
    # Just verify the button exists and can be updated
    w.player_bar.play_btn.update()
    app.processEvents()
check("hover_play_button_no_crash", hover_play_button)

print("\n" + "="*50)
print(f"STABILITY SUMMARY: {passed} passed, {failed} failed")
print("="*50)
if errors:
    for n, tb in errors: print(f"\n--- {n} ---\n{tb}")
    sys.exit(1)
else:
    print("ALL STABILITY TESTS PASSED!")
    sys.exit(0)
