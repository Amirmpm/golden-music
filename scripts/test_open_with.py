"""
Regression suite — "Open with" / file arguments + single-instance IPC.

Covers:
  * argv file collection (cold start): absolute, existing, extension
    allow-list, dedup, non-path args ignored
  * open_external_files: ad-hoc queue + play first + reveal, and NEVER
    mutates library/config (never-wipe guard)
  * IPC roundtrip to a running instance (separate process), including
    the reveal-when-hidden-in-tray path
  * malformed / hostile IPC payloads are rejected

Run:  QT_QPA_PLATFORM=offscreen python scripts/test_open_with.py
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import testenv as _testenv
_testenv.install()
del _testenv

# main.pyw: load by explicit loader — '.pyw' is a source suffix on
# Windows but not on POSIX, so 'import main' is not portable.
import importlib.util as _ilu
from importlib.machinery import SourceFileLoader as _SFL

REPO = Path(__file__).resolve().parent.parent
_main_loader = _SFL("main", str(REPO / "main.pyw"))
_main_spec = _ilu.spec_from_loader("main", _main_loader)
main = _ilu.module_from_spec(_main_spec)
_main_loader.exec_module(main)

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

import ipc
MainWindow = main.MainWindow
_audio_files_from_argv = main._audio_files_from_argv

app = QApplication(sys.argv)
app.setQuitOnLastWindowClosed(False)

results = []


def check(name, ok, detail=""):
    results.append((ok, name, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}"
          + (f"  ->  {detail}" if detail and not ok else ""))


home = Path(os.environ["HOME"])          # testenv sandbox
songs = home / "music"
songs.mkdir(parents=True, exist_ok=True)


def make_song(name: str) -> str:
    p = songs / name
    p.write_bytes(b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\x00" * 64)
    return str(p)


song1 = make_song("one.mp3")
song2 = make_song("two.flac")
not_audio = str(home / "notes.txt")
Path(not_audio).write_text("hi", encoding="utf-8")
missing = str(songs / "gone.mp3")

# --- argv collection ---------------------------------------------------------
picked = _audio_files_from_argv([song1, not_audio, missing, "--updated",
                                 song2, song1, "relative.mp3"])
check("argv: keeps valid audio files",
      song1 in picked and song2 in picked)
check("argv: drops non-audio, missing and relative paths",
      not_audio not in picked and missing not in picked
      and "relative.mp3" not in picked)
check("argv: ignores flags like --updated",
      all(not p.startswith("-") for p in picked))
check("argv: deduplicates",
      len([p for p in picked if p == song1]) == 1)

# --- in-process server + external open ---------------------------------------
w = MainWindow()
w.show()
app.processEvents()

lib_before = list(w.library)
cfg_before = (w.added_folders and list(w.added_folders)) or []
played = []
orig_load_and_play = w.audio.load_and_play
w.audio.load_and_play = lambda p: played.append(p) or orig_load_and_play(p)

reveal_calls = []
orig_reveal = w._reveal_window
w._reveal_window = lambda: (reveal_calls.append(1), orig_reveal())[1]

ok = w.open_external_files([song1, not_audio, missing, song2])
app.processEvents()
check("open_external_files: accepted the valid subset", ok is True)
check("open_external_files: queue == the two valid files (ad-hoc order kept)",
      w.current_playlist[:2] == [song1, song2])
check("open_external_files: first file started playing",
      bool(played) and played[0] == song1)
check("open_external_files: revealed the window", len(reveal_calls) >= 1)
app.processEvents()
check("open_external_files: library untouched",
      w.library == lib_before)
check("open_external_files: added_folders untouched",
      list(w.added_folders or []) == cfg_before)

# --- second-instance forwarding (separate process, like a real relaunch) ------
# The client runs in its own process (exactly how a second launch behaves);
# while it talks to us we keep pumping the Qt event loop so the in-process
# IPC server can accept and answer.
env = dict(os.environ)
env["QT_QPA_PLATFORM"] = "offscreen"


def run_client(code, timeout=20):
    """Run a client subprocess while pumping our event loop."""
    import time as _time
    proc = subprocess.Popen([sys.executable, "-c", code], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True)
    deadline = _time.time() + timeout
    while proc.poll() is None and _time.time() < deadline:
        app.processEvents()          # serve the IPC server side
        _time.sleep(0.02)
    app.processEvents()
    try:
        out, err = proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
    return out, err


song3 = make_song("three.mp3")
client_open = (
    "import sys, os; sys.path.insert(0, %r);"
    "os.environ['QT_QPA_PLATFORM']='offscreen';"
    "from PyQt6.QtCore import QCoreApplication; import ipc;"
    "app=QCoreApplication([]);"
    "r=ipc.send_message({'cmd':'open','paths':[%r, %r]}, timeout_ms=6000);"
    "print('CLIENT_OK' if r else 'CLIENT_FAIL'); sys.exit(0 if r else 1)"
) % (str(REPO), song3, song1)

reveal_before = len(reveal_calls)
out, err = run_client(client_open)
check("IPC: second instance got an ack",
      "CLIENT_OK" in out, out[-200:] + err[-200:])
app.processEvents()
check("IPC: running instance queued the forwarded files",
      w.current_playlist[:2] == [song3, song1])

# --- reveal-when-hidden-in-tray ------------------------------------------------
w.hide()
app.processEvents()
reveal_before = len(reveal_calls)
client_show = (
    "import sys, os; sys.path.insert(0, %r);"
    "os.environ['QT_QPA_PLATFORM']='offscreen';"
    "from PyQt6.QtCore import QCoreApplication; import ipc;"
    "app=QCoreApplication([]);"
    "r=ipc.send_message({'cmd':'show'}, timeout_ms=6000);"
    "print('CLIENT_OK' if r else 'CLIENT_FAIL'); sys.exit(0 if r else 1)"
) % str(REPO)
out, err = run_client(client_show)
app.processEvents()
check("IPC: show acknowledged while hidden in tray",
      "CLIENT_OK" in out, out[-200:] + err[-200:])
# The server holds the REAL _reveal_window bound method (captured in
# MainWindow.__init__), so visibility is the honest evidence here.
check("IPC: hidden instance was revealed (visible again)",
      w.isVisible() and not w.isMinimized())

# --- hostile / malformed payloads ----------------------------------------------
check("IPC: unknown cmd rejected",
      ipc.validate_payload(json.dumps({"cmd": "nuke", "paths": [song1]}).encode()) is None)
check("IPC: relative paths rejected",
      ipc.validate_payload(json.dumps({"cmd": "open", "paths": ["x/song.mp3"]}).encode()) is None)
check("IPC: non-audio extensions rejected",
      ipc.validate_payload(json.dumps({"cmd": "open", "paths": [not_audio]}).encode()) is None)
check("IPC: non-string paths rejected",
      ipc.validate_payload(b'{"cmd":"open","paths":[1,2,3]}') is None)
check("IPC: oversize frame rejected",
      ipc.validate_payload(b'{"cmd":"open","paths":[]}' + b" " * (ipc.MAX_FRAME_BYTES + 1)) is None)
check("IPC: garbage rejected", ipc.validate_payload(b"\xff\xfe not json") is None)
check("IPC: nonexistent file rejected",
      ipc.validate_payload(json.dumps({"cmd": "open", "paths": [missing]}).encode()) is None)

w._force_quit = True
w.close()
app.processEvents()

failed = sum(1 for ok_, *_ in results if not ok_)
print(f"\nopen-with: {len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
