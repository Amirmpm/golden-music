"""Test: brush icon, dark/light sections, taskbar-to-tray setting."""
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
from PyQt6.QtCore import Qt

app = QApplication(sys.argv)
app.setApplicationName('GM-ThemeTray-Test')

import main as m
from icons import get_svg_source, Icon
from theme_picker import ThemePickerPopup
from config import Theme

# 1. Brush icon exists and loads
src = get_svg_source('brush')
assert '<svg' in src, 'brush svg missing'
print('1. brush icon loads: OK', flush=True)

w = m.MainWindow()
w.show()
app.processEvents()

# 2. Theme button uses brush (not moon/sun) even after theme changes
for name in ('royal_gold', 'ivory'):
    w.theme_name = name
    w._apply_theme()
    assert w.rail.btn_theme.svg_string == Icon.BRUSH, \
        f'theme btn is {w.rail.btn_theme.svg_string} in {name}'
print('2. theme button = brush in dark & light themes: OK', flush=True)

# 3. Picker has DARK/LIGHT section labels
w._show_theme_picker()
tp = w.theme_popup
labels = [lbl.text() for lbl in tp.findChildren(type(tp.findChildren(object)[0]))
          if lbl.text() in ('DARK', 'LIGHT')] if False else None
# simpler: count QLabel texts
texts = []
for lbl in tp.findChildren(m.QLabel):
    if hasattr(lbl, 'text'):
        texts.append(lbl.text())
has_dark = 'DARK' in texts
has_light = 'LIGHT' in texts
n_darks = sum(1 for t in Theme.ALL if t.get('is_dark', True))
n_lights = len(Theme.ALL) - n_darks
print(f'3. picker sections: DARK={has_dark} LIGHT={has_light} '
      f'(darks={n_darks}, lights={n_lights})', flush=True)
assert has_dark and has_light and n_lights >= 2
tp.close()

# 4. taskbar-to-tray setting round-trips through changeEvent
assert w.taskbar_close_to_tray is False
w.taskbar_close_to_tray = True
w.setWindowState(w.windowState() | Qt.WindowState.WindowMinimized)
app.processEvents()
import time
t0 = time.time()
while time.time() - t0 < 0.5:
    app.processEvents()
print('   minimized -> visible:', w.isVisible(), '(expect False)', flush=True)
assert not w.isVisible(), 'window should hide to tray'
w.taskbar_close_to_tray = False
w.show()  # restore for next steps
app.processEvents()

# 5. Settings checkbox wired
from settings_dialog import SettingsDialog
dlg = SettingsDialog(w, w)
dlg._load_current_settings()
assert dlg.tray_min_cb.isChecked() == w.taskbar_close_to_tray
w.taskbar_close_to_tray = False
dlg.tray_min_cb.setChecked(True)
dlg._on_ok()
assert w.taskbar_close_to_tray is True, 'setting not applied from dialog'
print('4. settings checkbox applies to main window: OK', flush=True)
w.taskbar_close_to_tray = False  # leave default off

w.close()
print('ALL THEME/TRAY CHECKS PASSED', flush=True)
