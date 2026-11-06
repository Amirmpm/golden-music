"""
Regression suite — tray Show/Hide must always restore the window.

Reproduces the original defect: _toggle_visible() treated a minimized
window as visible (clicking tray HID it) and show() alone remembered the
minimized state (the window stayed minimized behind other windows).

Run:  QT_QPA_PLATFORM=offscreen python scripts/test_tray_toggle.py
"""
import os
import sys
import time
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
from PyQt6.QtWidgets import QApplication

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


# --- 1: minimized -> tray Show restores to normal, front --------------------
w.showNormal()
app.processEvents()
w.setWindowState(Qt.WindowState.WindowMinimized)
app.processEvents()
w._toggle_visible()                       # tray left-click / popup Show button
app.processEvents()
minimized = bool(w.windowState() & Qt.WindowState.WindowMinimized)
check("restore from minimized: visible", w.isVisible())
check("restore from minimized: not minimized", not minimized)
check("restore from minimized: normal geometry state",
      w.windowState() & Qt.WindowState.WindowNoState
      or not (w.windowState() & Qt.WindowState.WindowMinimized))

# --- 2: visible-but-behind window -> Show must NOT hide it -------------------
# A visible (non-minimized) window toggles to hidden — the toggle part.
# The regression here is the minimized case firing hide(); covered above.

# --- 3: hidden -> Show reveals -----------------------------------------------
w.hide()
app.processEvents()
w._toggle_visible()
app.processEvents()
check("reveal from hidden: visible", w.isVisible())
check("reveal from hidden: not minimized",
      not bool(w.windowState() & Qt.WindowState.WindowMinimized))

# --- 4: minimized-but-visible -> tray click RESTORES, never hides ------------
w.showNormal()
app.processEvents()
w.setWindowState(Qt.WindowState.WindowMinimized)
app.processEvents()
was_visible = w.isVisible()
w._toggle_visible()
app.processEvents()
check("minimized window: tray click did not hide it", not (was_visible and not w.isVisible()))
check("minimized window: tray click restored it",
      w.isVisible() and not bool(w.windowState() & Qt.WindowState.WindowMinimized))

# --- 5: hide() still works for a fully visible window (toggle semantics) ----
w._reveal_window()
app.processEvents()
check("pre-hide: window fully visible & not minimized",
      w.isVisible() and not w.isMinimized())
w._toggle_visible()
app.processEvents()
check("toggle on visible window hides it", not w.isVisible())

# --- 6: reveal-window is idempotent for an already-visible window -----------
w._reveal_window()
app.processEvents()
check("reveal on visible window keeps it visible",
      w.isVisible() and not w.isMinimized())

w._force_quit = True
w.close()
app.processEvents()

failed = sum(1 for ok, *_ in results if not ok)
print(f"\ntray-toggle: {len(results) - failed}/{len(results)} passed")
sys.exit(1 if failed else 0)
