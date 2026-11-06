"""
Regression suite — keyboard shortcut scoping.

Bare convenience keys (F, M, Space, Enter, arrows) must never steal keys
from text inputs, list type-ahead/navigation, or sliders; on neutral
focus they still control the player. Dialogs are separate windows and
must be completely unaffected.

Run:  QT_QPA_PLATFORM=offscreen python scripts/test_shortcuts.py
"""
import os
import sys
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
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

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QInputDialog, QLineEdit

MainWindow = main.MainWindow

app = QApplication(sys.argv)
app.setQuitOnLastWindowClosed(False)

w = MainWindow()
w.show()
app.processEvents()

results = []


def check(name, ok, detail=""):
    results.append((ok, name, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}"
          + (f"  ->  {detail}" if detail and not ok else ""))


# Populate the library list so navigation/type-ahead are meaningful
w.library = [str(Path(os.environ["HOME"]) / f"song{i}.mp3") for i in range(5)]
w._rebuild_library_list()
app.processEvents()
lw = w.library_list

# --- F with a list focused: type-ahead, NOT fullscreen ------------------------
lw.setFocus()
lw.setCurrentRow(0)
app.processEvents()
from PyQt6.QtTest import QTest
QTest.keyClick(lw, Qt.Key.Key_F)
app.processEvents()
fs = getattr(w, "_fullscreen_player", None)
opened = fs is not None and fs.isVisible()
check("F with list focused does not open fullscreen", not opened)
check("F with list focused does not change selection", lw.currentRow() == 0)

# --- arrows with a list focused: native navigation, volume untouched ----------
v0 = w.player_bar.vol_slider.value()
QTest.keyClick(lw, Qt.Key.Key_Down)
app.processEvents()
check("Down with list focused moves selection", lw.currentRow() == 1)
check("Down with list focused leaves volume alone",
      w.player_bar.vol_slider.value() == v0)
QTest.keyClick(lw, Qt.Key.Key_Up)
app.processEvents()
check("Up with list focused moves selection back", lw.currentRow() == 0)

# --- search box: typing and spaces insert characters ---------------------------
sb = w.search_edit
sb.setFocus()
sb.clear()
app.processEvents()
QTest.keyClicks(sb, "golden")
QTest.keyClick(sb, Qt.Key.Key_Space)
QTest.keyClicks(sb, "m")
app.processEvents()
check("typing 'golden m' in search inserts characters incl. F/M/Space",
      sb.text() == "golden m", repr(sb.text()))

# --- seek slider keeps its own arrow keys --------------------------------------
sliders = w.player_bar.findChildren(type(w.player_bar).__mro__[-1])  # fallback below
from PyQt6.QtWidgets import QSlider
sliders = w.player_bar.findChildren(QSlider)
check("player bar has sliders", bool(sliders))
if sliders:
    s = sliders[0]
    s.setFocus()
    s.setValue(s.maximum() // 3)
    app.processEvents()
    before = s.value()
    QTest.keyClick(s, Qt.Key.Key_Right)
    app.processEvents()
    check("Right on seek slider seeks instead of skipping track",
          s.value() > before)

# --- neutral focus: player shortcuts work ---------------------------------------
lw.clearFocus()
w.setFocus()
app.processEvents()
QTest.keyClick(w, Qt.Key.Key_Up)
app.processEvents()
check("Up on neutral focus raises volume",
      w.player_bar.vol_slider.value() == v0 + 5)
QTest.keyClick(w, Qt.Key.Key_F)
app.processEvents()
fs = getattr(w, "_fullscreen_player", None)
check("F on neutral focus opens fullscreen",
      fs is not None and fs.isVisible())
if fs is not None and fs.isVisible():
    fs.close()
    app.processEvents()

# --- dialogs are separate windows: characters only -------------------------------
w._reveal_window()
app.processEvents()
# playlist-name dialog (QInputDialog.getText) — feed keys with no accept
from playlist_dialog import PlaylistStore
from PyQt6.QtWidgets import QDialog
dlg = QInputDialog()
# Use a line edit directly to emulate the dialog's input field behavior:
le = QLineEdit()
le.show()
app.processEvents()
le.setFocus()
QTest.keyClicks(le, "My Mix f m ")
app.processEvents()
check("dialog-style line edit accepts f/m/space as characters",
      le.text() == "My Mix f m ", repr(le.text()))
le.close()

w._force_quit = True
w.close()
app.processEvents()

failed = sum(1 for ok_, *_ in results if not ok_)
print(f"\nshortcuts: {len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
