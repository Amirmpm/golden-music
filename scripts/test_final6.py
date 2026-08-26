"""Final verification of the last 6 fixes."""
import sys
import time

sys.path.insert(0, '.')
from PyQt6.QtWidgets import QApplication, QAbstractItemView

app = QApplication(sys.argv)
app.setApplicationName('GM-Final-6')

import main as m

w = m.MainWindow()
w.show()
app.processEvents()

# 1. Red focus rectangle: folder tree QSS has outline:0
ft_ss = w.folder_tree.styleSheet()
assert 'outline: 0' in ft_ss, 'folder tree missing outline fix'
print('1. red focus rect fix: OK', flush=True)

# 2. Albums speed + loading overlay wiring
w._on_rail_clicked('albums')
app.processEvents()
t0 = time.time()
while time.time() - t0 < 0.5:
    app.processEvents()
    time.sleep(0.01)
print('2. albums opens (grid count:', w.albums_grid.count(), '), overlay exists:',
      hasattr(w, 'loading_overlay'), flush=True)
assert not w.loading_overlay.isVisible(), 'overlay should hide after load'

# 3. Multi-selection in albums grid
mode = w.albums_grid.selectionMode()
assert mode == QAbstractItemView.SelectionMode.SingleSelection, f'selection mode: {mode}'
print('3. albums single selection: OK', flush=True)

# 4. Playback speed dialog positions near more_btn (code-level check)
import inspect
src = inspect.getsource(m.MainWindow._cycle_playback_rate)
assert 'more_btn.mapToGlobal' in src, 'speed dialog still anchored to rate_btn'
print('4. playback speed anchored to more btn: OK', flush=True)

# 5. Settings dialog QSS readable in all themes
from config import Theme
from settings_dialog import SettingsDialog
for name in Theme.names():
    w.theme_name = name
    w._apply_theme()
    dlg = SettingsDialog(w, w)
    ss = dlg.styleSheet()
    assert "QLabel { color:" in ss or 'QLabel {{ color:' in ss
    dlg.deleteLater()
print('5. settings dialog styled for all', len(Theme.names()), 'themes: OK', flush=True)
w.theme_name = 'royal_gold'
w._apply_theme()

# 6. Loading overlay show/hide works
w.loading_overlay.show_loading('Testing')
app.processEvents()
assert w.loading_overlay.isVisible(), 'overlay should be visible'
w.loading_overlay.hide_loading()
app.processEvents()
assert not w.loading_overlay.isVisible(), 'overlay should hide'
print('6. loading overlay works: OK', flush=True)

w.close()
print('ALL 6 CHECKS PASSED', flush=True)
