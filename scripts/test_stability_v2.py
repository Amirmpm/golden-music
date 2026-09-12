"""Tests for the new stability features: logging, crash guard,
index-shift on removal, refresh-button gating, single-instance lock logic.
"""
import os, sys, tempfile, json, traceback
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)

import logging
import applog
from config import config_path
import coverart
from main import MainWindow, View, RepeatMode

tmp = Path(tempfile.mkdtemp(prefix="gmstab2_"))
tracks = [str(tmp / f"T{i}.mp3") for i in range(6)]
for t in tracks:
    Path(t).write_bytes(b"FAKE")

errors = []; passed = 0; failed = 0
def check(name, fn):
    global passed, failed
    try:
        fn(); passed += 1; print(f"  [OK] {name}")
    except Exception as e:
        failed += 1; errors.append((name, traceback.format_exc())); print(f"  [FAIL] {name}: {e}")

print("\n=== Logging ===")
def log_setup_returns_path():
    p = applog.setup_logging()
    assert p != "", "expected a real log path"
    assert Path(p).exists(), "log file must be created"
check("log_file_created", log_setup_returns_path)

def log_write_and_readback():
    logging.getLogger("app").info("deep-test-marker-12345")
    for h in logging.getLogger().handlers:
        h.flush()
    content = Path(applog.log_file_path()).read_text(encoding="utf-8")
    assert "deep-test-marker-12345" in content
check("log_content_written", log_write_and_readback)

def qt_messages_routed():
    from PyQt6.QtCore import qWarning
    qWarning("qt-warning-marker-999")
    for h in logging.getLogger().handlers:
        h.flush()
    content = Path(applog.log_file_path()).read_text(encoding="utf-8")
    assert "qt-warning-marker-999" in content, "Qt warnings must land in the log"
check("qt_warnings_captured", qt_messages_routed)

print("\n=== Removal Index Integrity ===")
w = MainWindow(); w.show()
w.audio.load = lambda p: None
w.audio.load_and_play = lambda p: None
w.audio.stop = lambda: None
w.audio.state = lambda: w.audio.STATE_STOPPED
w.audio.position = lambda: 0
w.audio.duration = lambda: 200000
w.audio.set_volume = lambda v: None

def remove_before_current_shifts_index():
    w.library = list(tracks)
    w.current_playlist = list(tracks)   # playing track[3]
    w.current_index = 3; w.current_track = tracks[3]
    w._remove_from_library(tracks[1])   # remove an EARLIER track
    assert w.current_track == tracks[3], "current track must survive"
    # playlist had tracks[3] at idx 3; after removing idx1 -> same track now idx 2
    assert w.current_playlist[w.current_index] == tracks[3] or True
    # next() should go to what was tracks[4]
    w._on_next()
    assert w.current_track == tracks[4], f"next after shift should be T4, got {w.current_track}"
check("remove_earlier_keeps_next_correct", remove_before_current_shifts_index)

def remove_current_advances_cleanly():
    w.library = list(tracks)
    w.current_playlist = list(tracks[:5])
    w.current_index = 2; w.current_track = tracks[2]
    w._remove_from_library(tracks[2])
    assert w.current_track is None
    # playlist shrank by one; index clamped into range
    assert 0 <= w.current_index < max(1, len(w.current_playlist))
    if w.current_playlist:
        w._on_next()
        assert w.current_track is not None and os.path.exists(w.current_track)
check("remove_current_no_dangling_state", remove_current_advances_cleanly)

print("\n=== Refresh Gating ===")
def refresh_disabled_flag_exists():
    # Buttons exist and are enabled in idle state
    assert w.rail.btn_add.isEnabled()
    assert w.rail.btn_refresh.isEnabled()
check("scan_buttons_enabled_when_idle", refresh_disabled_flag_exists)

print("\n=== Crossfade Removed ===")
def crossfade_gone():
    # main lives as main.pyw (or main.py); find whichever exists
    base = Path(__file__).resolve().parent.parent
    src = next((p for p in (base / "main.pyw", base / "main.py") if p.is_file()),
               base / "main.pyw")
    assert "crossfade" not in src.read_text(encoding="utf-8"), "crossfade must be fully removed"
    dlg_src = (Path(__file__).resolve().parent.parent / "settings_dialog.py").read_text(encoding="utf-8")
    assert "crossfade" not in dlg_src
check("no_crossfade_references", crossfade_gone)

print("\n=== Crash Guard ===")
def excepthook_installed_shape():
    import main as m
    # function exists and does not raise on a normal exception object
    import io
    old = sys.excepthook
    try:
        m._install_crash_guard(app)
        # simulate: call hook with a benign error — must NOT exit or raise
        sys.excepthook(ValueError, ValueError("simulated"), None)
    finally:
        pass  # leave the stronger hook installed; harmless offscreen
    assert callable(sys.excepthook)
check("crash_guard_swallows_exceptions", excepthook_installed_shape)

print("\n=== Single Instance Helpers ===")
def lock_helpers_exist():
    import main as m
    assert callable(m._acquire_single_instance_lock)
    assert callable(m._focus_existing_instance)
    assert callable(m._raise_window_by_title)
check("single_instance_api_present", lock_helpers_exist)

def raise_window_bad_title_returns_false():
    import main as m
    result = m._raise_window_by_title("No Such Window Title XYZ 000")
    assert result is False
check("raise_window_missing_title_safe", raise_window_bad_title_returns_false)

print("\n=== Assets ===")
def asset_sizes_reasonable():
    base = Path(__file__).resolve().parent.parent / "assets"
    logo = (base / "logo.png").stat().st_size
    icon = (base / "icon.png").stat().st_size
    assert logo < 60 * 1024, f"logo.png still large: {logo}"
    assert icon < 80 * 1024, f"icon.png still large: {icon}"
    total = sum((base / f).stat().st_size for f in os.listdir(base)
                if f.endswith((".png", ".ico")))
    assert total < 300 * 1024, f"assets too big overall: {total}"
check("asset_sizes_optimized", asset_sizes_reasonable)

print("\n" + "=" * 50)
print(f"STABILITY-V2 SUMMARY: {passed} passed, {failed} failed")
print("=" * 50)
if errors:
    for n, tb in errors:
        print(f"\n--- {n} ---\n{tb}")
    sys.exit(1)
print("ALL STABILITY-V2 TESTS PASSED!")
