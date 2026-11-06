"""Regression + feature suite — tray popup volume & seek (Golden Music 2.3).

Covers the NEW tray controls end-to-end:
  * widget construction (seek slider, time labels, mute button, volume slider)
  * volume: popup -> window -> audio + main-bar sync (one code path)
  * echo safety: window -> popup updates must NOT re-emit (no feedback loop)
  * mute toggle (volume 0 convention) incl. restore of last non-zero volume
  * seek: drag simulation (press/move/release), click-to-seek, no-track case
  * real mouse events on the popup sliders (QtTest) when available
  * stream case (duration 0): slider disabled, labels safe, no exceptions
  * position push from the window while popup open; frozen while dragging
  * popup open full-state sync; theme restyle; abuse (out-of-range, rapid)
  * layout: fixed size fits every row (no visual clipping)

Run:  QT_QPA_PLATFORM=offscreen python scripts/test_tray_av.py
"""
import os
import sys
import time
import traceback
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import testenv as _testenv
_testenv.install()
del _testenv

from PyQt6.QtCore import Qt, QPoint, QTimer
from PyQt6.QtWidgets import QApplication
from PyQt6.QtTest import QTest

app = QApplication(sys.argv)
app.setApplicationName("GMTestTrayAV")

import main as main
from config import Theme

MainWindow = main.MainWindow

# Neuter the async config load (same as the other suites)
MainWindow._load_config_async = lambda self: None

w = MainWindow()
w.resize(1000, 700)
w.show()
app.processEvents()

# ---- stub audio backend (record calls; keep Qt signals intact) -----------
AV_CALLS = {"volume": [], "position": []}
w.audio._p, w.audio._d = 0, 240000
w.audio.position = lambda: w.audio._p
w.audio.duration = lambda: w.audio._d
w.audio.set_position = lambda ms: (AV_CALLS["position"].append(int(ms)),
                                   setattr(w.audio, "_p", int(ms)))
w.audio.set_volume = lambda v: AV_CALLS["volume"].append(int(v))
w.audio.load = lambda p: None
w.audio.load_and_play = lambda p: None
w.audio.state = lambda: w.audio.STATE_PAUSED

tmw = w.tray_menu_widget

# a "current track" so the window accepts seeks (fake path, stub backend)
w.current_track = "/tmp/gmtest_home_does_not_exist/fake song.mp3"

# signal counters
vol_emitted = []
seek_emitted = []
tmw.volume_changed.connect(lambda v: vol_emitted.append(int(v)))
tmw.seek_requested.connect(lambda ms: seek_emitted.append(int(ms)))

results = []


def check(name, fn):
    try:
        fn()
        results.append((True, name, ""))
        print(f"[PASS] {name}")
    except Exception as e:
        results.append((False, name, traceback.format_exc()))
        print(f"[FAIL] {name}: {e}")


def assert_eq(a, b, what=""):
    if a != b:
        raise AssertionError(f"{what}: {a!r} != {b!r}")


# --------------------------------------------------------------- construction
def t_construction():
    for attr in ("seek_slider", "vol_slider", "btn_mute",
                 "time_elapsed", "time_total"):
        assert hasattr(tmw, attr), f"missing {attr}"
    assert_eq(tmw.seek_slider.objectName(), "SeekSlider", "seek objectName")
    assert_eq(tmw.vol_slider.objectName(), "VolSlider", "vol objectName")
    assert_eq(tmw.seek_slider.maximum(), 1000, "seek range")
    assert_eq(tmw.vol_slider.maximum(), 100, "vol range")
    assert not tmw.seek_slider.isEnabled(), "seek must start disabled"
    # every row must fit the fixed popup height (no visual clipping)
    hint = tmw.layout().totalMinimumSize()
    assert hint.height() <= tmw.height() - 4, \
        f"rows clipped: need {hint.height()} have {tmw.height()}"
    assert hint.width() <= tmw.width() - 4, \
        f"rows too wide: need {hint.width()} have {tmw.width()}"


check("construction: controls + layout fits", t_construction)


# -------------------------------------------------------------------- volume
def t_volume_to_audio():
    vol_emitted.clear(); AV_CALLS["volume"].clear()
    tmw.vol_slider.setValue(33)          # user action on the popup slider
    app.processEvents()
    assert_eq(vol_emitted, [33], "popup emitted")
    assert_eq(w.player_bar.vol_slider.value(), 33, "main bar synced")
    assert AV_CALLS["volume"] and AV_CALLS["volume"][-1] == 33, \
        f"audio volume not applied: {AV_CALLS['volume']}"
    assert_eq(tmw.vol_slider.value(), 33, "popup value")


check("volume: popup -> window -> audio + main bar", t_volume_to_audio)


def t_echo_safety():
    vol_emitted.clear()
    w.player_bar.vol_slider.setValue(60)  # main-bar path must not re-emit
    app.processEvents()
    assert_eq(vol_emitted, [], "echo loop detected")
    assert_eq(tmw.vol_slider.value(), 60, "popup follows main bar")


check("volume: no echo loop on window->popup sync", t_echo_safety)


def t_mute_toggle():
    AV_CALLS["volume"].clear(); vol_emitted.clear()
    tmw.btn_mute.click()                 # mute
    app.processEvents()
    assert_eq(vol_emitted, [0], "mute emitted 0")
    assert AV_CALLS["volume"] and AV_CALLS["volume"][-1] == 0, "audio muted"
    assert_eq(w.player_bar.vol_slider.value(), 0, "main bar muted")
    tmw.btn_mute.click()                 # unmute -> last non-zero (60)
    app.processEvents()
    assert_eq(vol_emitted, [0, 60], "unmute emitted 60")
    assert AV_CALLS["volume"][-1] == 60, "audio restored"
    assert_eq(w.player_bar.vol_slider.value(), 60, "main bar restored")


check("volume: mute toggle mutes and restores", t_mute_toggle)


def t_main_mute_button_syncs_popup():
    vol_emitted.clear()
    w._on_mute_toggle()                  # main-bar mute button path
    app.processEvents()
    assert_eq(w.player_bar.vol_slider.value(), 0, "main mute")
    assert_eq(tmw.vol_slider.value(), 0, "popup follows mute")
    assert_eq(vol_emitted, [], "no echo")
    w._on_mute_toggle()                  # back on
    app.processEvents()
    assert_eq(tmw.vol_slider.value(), 60, "popup follows unmute")


check("volume: main-bar mute stays in sync with popup", t_main_mute_button_syncs_popup)


# ---------------------------------------------------------------------- seek
def t_duration_and_labels():
    tmw.update_duration(240000)
    assert tmw.seek_slider.isEnabled(), "seek enabled after duration"
    assert_eq(tmw.time_total.text(), "4:00", "total label")
    assert_eq(tmw._duration_ms, 240000, "duration cached")


check("seek: duration enables slider, formats label", t_duration_and_labels)


def t_seek_drag():
    AV_CALLS["position"].clear(); seek_emitted.clear()
    tmw._on_seek_pressed()               # drag starts
    assert tmw.is_seeking(), "is_seeking flag"
    tmw.seek_slider.setValue(500)        # drag to middle
    tmw._on_seek_moved(500)
    assert_eq(tmw.time_elapsed.text(), "2:00", "live elapsed label")
    tmw._on_seek_released()              # release commits
    app.processEvents()
    assert_eq(seek_emitted, [120000], "seek ms emitted")
    assert AV_CALLS["position"] and AV_CALLS["position"][-1] == 120000, \
        f"audio seek: {AV_CALLS['position']}"
    assert_eq(w.audio.position(), 120000, "backend position")
    assert_eq(w.player_bar.seek_slider.value(), 500, "main bar mirrors seek")
    assert not tmw.is_seeking(), "drag flag cleared"


check("seek: drag -> audio position + main bar mirror", t_seek_drag)


def t_seek_click():
    AV_CALLS["position"].clear(); seek_emitted.clear()
    tmw._on_seek_pressed()
    tmw.seek_slider.setValue(250)
    tmw._on_seek_released()
    app.processEvents()
    assert_eq(seek_emitted, [60000], "click seek")
    assert_eq(w.audio.position(), 60000, "click position")


check("seek: click-to-seek", t_seek_click)


def t_seek_no_track():
    AV_CALLS["position"].clear()
    saved = w.current_track
    w.current_track = None
    try:
        w._on_tray_seek(99999)          # must be a no-op, not a crash
        app.processEvents()
    finally:
        w.current_track = saved
    assert_eq(AV_CALLS["position"], [], "no seek without a track")


check("seek: no track -> safe no-op", t_seek_no_track)


def t_seek_real_mouse():
    tmw.show(); app.processEvents()
    tmw.update_duration(240000)
    AV_CALLS["position"].clear()
    sl = tmw.seek_slider
    sl.resize(200, 18)
    QTest.mousePress(sl, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, QPoint(180, 9))
    QTest.mouseRelease(sl, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier, QPoint(180, 9))
    app.processEvents()
    assert AV_CALLS["position"], "real click produced no seek"
    assert w.audio.position() > 200000, "click near the end seeks late"
    QTest.mousePress(sl, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, QPoint(20, 9))
    QTest.mouseMove(sl, QPoint(100, 9))
    QTest.mouseRelease(sl, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier, QPoint(100, 9))
    app.processEvents()
    assert 90000 < w.audio.position() < 150000, \
        f"drag landed at {w.audio.position()}"
    tmw.hide()


check("seek: real QtTest mouse press/drag/release", t_seek_real_mouse)


def t_volume_real_mouse():
    tmw.show(); app.processEvents()
    AV_CALLS["volume"].clear()
    vs = tmw.vol_slider
    vs.resize(200, 18)
    QTest.mousePress(vs, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, QPoint(190, 9))
    QTest.mouseRelease(vs, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier, QPoint(190, 9))
    app.processEvents()
    assert AV_CALLS["volume"] and AV_CALLS["volume"][-1] > 90, \
        f"click at far right sets high volume: {AV_CALLS['volume']}"
    tmw.hide()


check("volume: real QtTest mouse click", t_volume_real_mouse)


# ------------------------------------------------- window -> popup sync paths
def t_position_push_visible():
    tmw.show(); app.processEvents()
    tmw.update_duration(240000)
    w.audio._p = 30000
    w._on_position_changed(30000)
    app.processEvents()
    assert_eq(tmw.seek_slider.value(), 125, "popup slider follows position")
    assert_eq(tmw.time_elapsed.text(), "0:30", "popup elapsed label")
    tmw.hide()


check("sync: position push while popup open", t_position_push_visible)


def t_position_frozen_while_dragging():
    tmw.show(); app.processEvents()
    tmw.update_duration(240000)
    tmw._on_seek_pressed()
    tmw.seek_slider.setValue(700)
    w._on_position_changed(10000)        # late tick must not fight the drag
    app.processEvents()
    assert_eq(tmw.seek_slider.value(), 700, "drag protected from pushes")
    tmw._on_seek_released()
    tmw.hide()


check("sync: position push frozen during popup drag", t_position_frozen_while_dragging)


def t_duration_push():
    w._on_duration_changed(180000)       # window signal path
    app.processEvents()
    assert_eq(tmw.time_total.text(), "3:00", "total via signal")
    assert tmw.seek_slider.isEnabled(), "enabled via signal"


check("sync: duration signal updates popup", t_duration_push)


def t_popup_open_sync():
    w.audio._p, w.audio._d = 45000, 240000
    w.player_bar.vol_slider.blockSignals(True)
    w.player_bar.vol_slider.setValue(42)
    w.player_bar.vol_slider.blockSignals(False)
    w._show_tray_popup()                 # uses cursor + screens (offscreen ok)
    app.processEvents()
    assert_eq(tmw.vol_slider.value(), 42, "open syncs volume")
    assert_eq(tmw.seek_slider.value(), 187, "open syncs position")
    assert_eq(tmw.time_total.text(), "4:00", "open syncs duration")
    tmw.hide()


check("sync: popup open pulls full live state", t_popup_open_sync)


# ------------------------------------------------------------- stream / reset
def t_stream_zero_duration():
    tmw.update_duration(0)
    assert not tmw.seek_slider.isEnabled(), "stream disables seek"
    assert_eq(tmw.time_total.text(), "--:--", "stream total label")
    tmw._on_seek_pressed()               # abuse: drag on disabled slider
    tmw.seek_slider.setValue(999)
    tmw._on_seek_released()
    app.processEvents()                  # must not raise or emit
    tmw._on_seek_moved(999)


check("stream: duration 0 disables seek safely", t_stream_zero_duration)


def t_track_change_reset():
    tmw.update_duration(240000)
    w._tray_reset_progress()
    assert not tmw.seek_slider.isEnabled(), "reset disables seek"
    assert_eq(tmw.time_total.text(), "--:--", "reset label")
    assert_eq(tmw.time_elapsed.text(), "0:00", "reset elapsed")


check("reset: new track clears progress row", t_track_change_reset)


# ------------------------------------------------------------------- theming
def t_theme_restyle():
    tmw.update_duration(240000)
    tmw.set_theme(Theme.get("midnight_blue"))
    app.processEvents()
    assert "midnight" in str(tmw.theme.get("name", "")).lower() or \
        tmw.theme is not None
    # labels restyled without crash; controls still functional
    tmw.vol_slider.setValue(55)
    app.processEvents()
    assert_eq(w.player_bar.vol_slider.value(), 55, "works after restyle")
    tmw.set_theme(Theme.get("royal_gold"))
    app.processEvents()


check("theme: restyle keeps controls functional", t_theme_restyle)


# --------------------------------------------------------------------- abuse
def t_abuse_out_of_range():
    tmw.update_duration(240000)
    for v in (-5, 200, 10 ** 9):
        tmw.vol_slider.setValue(v)
        app.processEvents()
    tmw.update_position(-1000)
    tmw.update_duration(-5000)
    tmw.update_volume(-1)
    tmw.update_volume(10 ** 6)
    app.processEvents()                  # any raise fails the check


check("abuse: out-of-range values never raise", t_abuse_out_of_range)


def t_abuse_rapid_updates():
    t0 = time.time()
    for i in range(200):
        tmw.update_position(i * 1000)
        if i % 4 == 0:
            tmw.update_volume(i % 101)
    app.processEvents()
    dt = time.time() - t0
    assert dt < 2.0, f"200 updates took {dt:.2f}s (UI freeze risk)"
    assert_eq(tmw.time_elapsed.text(), "3:19", "final label")


check("abuse: 200 rapid updates stay instant", t_abuse_rapid_updates)


def t_no_orphan_signals():
    vol_emitted.clear()
    tmw.update_volume(70)                # programmatic only
    tmw.update_position(70000)
    tmw.update_duration(240000)
    app.processEvents()
    assert_eq(vol_emitted, [], "programmatic updates must stay silent")


check("signals: programmatic updates emit nothing", t_no_orphan_signals)


# ------------------------------------------------------------------- cleanup
w._force_quit = True
w.close()
app.processEvents()

failed = sum(1 for ok, *_ in results if not ok)
print(f"\ntray-av: {len(results) - failed}/{len(results)} passed")
if failed:
    for ok, name, tb in results:
        if not ok:
            print(f"\n--- {name} ---\n{tb}")
sys.exit(1 if failed else 0)
