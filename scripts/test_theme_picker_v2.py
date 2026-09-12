"""Test v2: theme picker — 3-per-row grid, persistent menu, ✕ button.

Covers the UX contract added in 2.0.0:
  1. Grid is laid out 3 swatches per row (two 3+3 blocks).
  2. Clicking a swatch applies the theme but NEVER closes the popup
     (users may audition several themes); popup re-skins itself live.
  3. The ✕ button (Lucide close icon) closes the picker.
"""
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
app.setApplicationName('GM-ThemePicker-v2-Test')

import main as m
from theme_picker import ThemeSwatch
from config import Theme

w = m.MainWindow()
w.show()
app.processEvents()

w._show_theme_picker()
tp = w.theme_popup
app.processEvents()
assert tp.isVisible(), 'popup did not open'

# --- 1. layout: exactly 3 per row ----------------------------------------
swatches = tp.findChildren(ThemeSwatch)
print('swatches:', len(swatches), flush=True)
assert len(swatches) == len(Theme.ALL) == 12, 'expected 12 themes'

rows = {}
for s in swatches:
    rows.setdefault(s.y(), []).append(s.x())
for y, xs in sorted(rows.items()):
    xs.sort()
    print(f'row y={y}: x={xs}', flush=True)
    assert len(xs) == 3, f'row has {len(xs)} swatches, expected 3'
    gaps = {b - a for a, b in zip(xs, xs[1:])}
    assert gaps == {110}, f'unexpected column gaps: {gaps}'

# popup still fits inside the screen
geo = app.primaryScreen().availableGeometry()
assert geo.contains(tp.geometry()), 'popup spills off-screen!'
print('grid OK: 12 themes, 3-per-row, size',
      f'{tp.width()}x{tp.height()}', flush=True)

# --- 2. persistent menu across all 12 selections -------------------------
initial = w.theme_name
picked = []
for th in Theme.ALL:
    target = th['name']
    btn = next(s for s, n in tp._swatches if n == target)
    btn.click()
    app.processEvents()
    picked.append(target)
    if not tp.isVisible():
        print(f'FAILED: popup closed itself when picking "{target}"', flush=True)
        sys.exit(1)
    if w.theme_name != target:
        print(f'FAILED: theme not applied ({w.theme_name} != {target})', flush=True)
        sys.exit(1)

sel = [n for s, n in tp._swatches if s._selected]
final = picked[-1]
assert sel == [final] == [tp.current_theme], \
    f'selection ring wrong: {sel}, expected [{final}]'
print('persistent menu OK: all', len(picked),
      'themes applied live, popup stayed open', flush=True)

# clicking the ACTIVE theme again must also keep it open
btn = next(s for s, n in tp._swatches if n == tp.current_theme)
btn.click(); app.processEvents()
assert tp.isVisible(), 're-selecting active theme closed the popup'

# --- 3. ✕ button closes ---------------------------------------------------
tp._close_btn.click()
app.processEvents()
assert not tp.isVisible(), '✕ button did not close the popup'
print('close button OK', flush=True)

# fresh picker must start pre-marked with the active theme
w._show_theme_picker()
tp2 = w.theme_popup
app.processEvents()
marked = [n for s, n in tp2._swatches if s._selected]
assert marked == [tp2.current_theme], f'fresh picker marking wrong: {marked}'
tp2.close()

# leave the machine as we found it
w.theme_name = initial
w._apply_theme()
w._save_config()
w.close()
print('THEME PICKER V2 OK', flush=True)
