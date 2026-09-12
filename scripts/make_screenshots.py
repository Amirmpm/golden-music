"""Regenerate README screenshots from the live app (offscreen Qt).

Captures the four themes referenced in README.md at a realistic window
size, with the demo library loaded so lists/cover art look real.
"""
import os
import sys
import json
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# --- Test isolation: sandbox HOME + portable config -------------------------
# MUST run before any project import (config_path() reads HOME at call time).
import testenv as _testenv
_testenv.install()
del _testenv
# ------------------------------------------------------------------------------

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer

app = QApplication(sys.argv)
import coverart
from main import MainWindow

SHOTS = Path(__file__).resolve().parent.parent / "screenshots"
SHOTS.mkdir(exist_ok=True)

# theme name -> output file (the four used in README.md)
from config import Theme

# Every theme -> screenshots/<theme>.png
CAPTURES = [(name, f"{name}.png") for name in Theme.names()]

w = MainWindow()
w.resize(1120, 720)
w.show()

# Fake audio backend state so the player bar looks "live"
w.audio.load = lambda p: None
w.audio.load_and_play = lambda p: None


def render(widget) -> bytes:
    widget.repaint()
    pm = widget.grab()
    return bytes(pm.save(str(pm.cacheKey()), "PNG")) and b"" or pm.toImage().bitPlaneCount() and b""  # noqa


def capture(idx):
    if idx >= len(CAPTURES):
        app.quit()
        return
    theme_name, fname = CAPTURES[idx]
    w._on_theme_selected(theme_name)
    w.current_view = __import__("main").View.LIBRARY
    w.stack.setCurrentIndex(0)
    w.page_title.setText("Library")

    def snap():
        w.repaint()
        app.processEvents()
        pix = w.grab()
        out = SHOTS / fname
        pix.save(str(out), "PNG")
        print(f"saved {out.name} ({pix.width()}x{pix.height()})")
        QTimer.singleShot(250, lambda: capture(idx + 1))

    # let tag loader populate the list first
    QTimer.singleShot(700, snap)


def start():
    # wait for async config load to finish building the list
    QTimer.singleShot(1500, lambda: capture(0))


QTimer.singleShot(200, start)
app.exec()
print("done")
