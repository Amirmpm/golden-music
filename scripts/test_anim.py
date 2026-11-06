"""Temporary test: hover animation on IconButton."""
import os
import sys
from pathlib import Path
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
# --- Test isolation: sandbox HOME + portable config -------------------------
# MUST run before any project import (config_path() reads HOME at call time).
import testenv as _testenv
_testenv.install()
del _testenv
# ------------------------------------------------------------------------------
from PyQt6.QtWidgets import QApplication

app = QApplication(sys.argv)
app.setApplicationName('GM-Test-Anim9')

import main as m

w = m.MainWindow()
b = w.player_bar.shuffle_btn
print('base:', b.icon_size_base, flush=True)
b.enterEvent(None)
end = time.time() + 0.35
while time.time() < end:
    app.processEvents()
    time.sleep(0.01)
print('after hover:', b.icon_size, flush=True)
b.leaveEvent(None)
end = time.time() + 0.35
while time.time() < end:
    app.processEvents()
    time.sleep(0.01)
print('after leave:', b.icon_size, flush=True)
w.close()
print('DONE', flush=True)
