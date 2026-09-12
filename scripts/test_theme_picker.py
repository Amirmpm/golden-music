"""Test: theme picker popup stays fully inside the screen."""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
# --- Test isolation: sandbox HOME + portable config -------------------------
# MUST run before any project import (config_path() reads HOME at call time).
import testenv as _testenv
_testenv.install()
del _testenv
# ------------------------------------------------------------------------------
from PyQt6.QtWidgets import QApplication

app = QApplication(sys.argv)
app.setApplicationName('GM-ThemePicker-Test')

import main as m
from theme_picker import ThemeSwatch

w = m.MainWindow()
w.show()
app.processEvents()

w._show_theme_picker()
tp = w.theme_popup
geo = app.primaryScreen().availableGeometry()
print('popup size:', tp.width(), 'x', tp.height(), flush=True)
print('popup pos:', tp.x(), tp.y(), flush=True)
inside = (geo.left() <= tp.x() and geo.top() <= tp.y() and
          tp.x() + tp.width() <= geo.right() and
          tp.y() + tp.height() <= geo.bottom())
print('fully inside screen:', inside, flush=True)
print('swatches:', len(tp.findChildren(ThemeSwatch)), flush=True)
assert inside, 'theme picker still spills off-screen!'
tp.close()
w.close()
print('THEME PICKER OK', flush=True)
