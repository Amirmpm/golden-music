#!/usr/bin/env python3
"""One-time v3.0 refactor helper: split the monolithic main.pyw into the
`gm/` package.  Uses AST node ranges so it is robust against line drift.
Kept for history/reproducibility of the 2.3.0 split."""
import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "main.pyw"
src = SRC.read_text(encoding="utf-8")
lines = src.splitlines(keepends=True)
tree = ast.parse(src)

# ---------------------------------------------------------------- collect defs
segs = {}   # name -> (start_line0, end_line0) zero-based inclusive
order = []  # preserve file order
for node in tree.body:
    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        name = node.name
    elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
        name = node.targets[0].id
    else:
        continue
    segs[name] = (node.lineno - 1, node.end_lineno)
    order.append(name)

# Keep only the LAST definition (main.pyw has a shadowed duplicate MarqueeLabel)
kept = {}
for name in order:
    kept[name] = name
DROP = {"MarqueeLabel_dup_first"}  # handled below by keeping last def


def seg_text(*names):
    out = []
    for n in names:
        if n not in segs:
            raise SystemExit(f"symbol not found: {n}")
        a, b = segs[n]
        out.append("".join(lines[a:b]))
    return "\n\n".join(out)


# ---------------------------------------------------------------- shared header
HEADER = '''"""
Golden Music v3.0 — split out of the former main.pyw monolith.
"""
import sys
import os

if sys.platform == 'win32':
    os.environ.setdefault('QT_MEDIA_BACKEND', 'windowsmediafoundation')

import json
import logging
import math
import random
import subprocess
import time
from pathlib import Path
from enum import Enum

from PyQt6.QtCore import (
    Qt, QSize, QRect, QRectF, QPointF, QTimer, QEvent, pyqtSignal, QUrl,
    QObject, QThread, QPropertyAnimation, QVariantAnimation, QPoint,
    QEasingCurve
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton,
    QVBoxLayout, QHBoxLayout, QGridLayout, QFrame, QSizePolicy,
    QSlider, QListWidget, QListWidgetItem, QStackedWidget,
    QSystemTrayIcon, QMenu, QFileDialog, QMessageBox,
    QStyle, QStyleFactory, QAbstractItemView,
    QToolButton, QButtonGroup, QProgressBar, QLineEdit, QComboBox,
    QSplitter, QTreeWidget, QTreeWidgetItem, QTextEdit, QPlainTextEdit,
    QGraphicsOpacityEffect
)
from PyQt6.QtGui import (
    QIcon, QPixmap, QPainter, QColor, QFont, QPalette, QAction,
    QLinearGradient, QBrush, QRadialGradient, QFontDatabase,
    QMouseEvent, QKeyEvent, QPaintEvent, QPen, QPainterPath,
    QShortcut, QKeySequence, QFontMetrics
)
from PyQt6.QtWidgets import QStyledItemDelegate

from config import (
    APP_NAME, APP_VERSION, ORG_NAME, AUDIO_EXTS, config_path,
    Theme, build_qss, fmt_time, scan_folder, extract_title_artist,
    HAVE_QT_AUDIO, load_json_file, load_tag_cache_file,
    restrict_file_permissions, path_within
)
from icons import Icon, render_icon, make_icon, get_dpr

'''

GM = REPO / "gm"
(GM / "ui").mkdir(parents=True, exist_ok=True)

# ------------------------------------------------------------------- gm/core.py
core = HEADER + '''log = logging.getLogger("app")


''' + seg_text("_scratch_prefixes", "_SCRATCH_PREFIXES", "_is_scratch_path",
              "RepeatMode", "View", "SortMode", "_track_item_text",
              "_list_subdirs", "_dir_has_subdirs", "_rgba") + "\n"
(GM / "core.py").write_text(core, encoding="utf-8")

# ------------------------------------------------------------ gm/ui/widgets.py
widgets = HEADER + '''from gm.core import _rgba, log
from widgets import ClickableSlider


''' + seg_text("IconButton", "CoverLabel", "MarqueeLabel",
               "LoadingOverlay", "_SpinnerCanvas") + "\n"
(GM / "ui" / "widgets.py").write_text(widgets, encoding="utf-8")

# ---------------------------------------------------------- gm/ui/delegates.py
delegates = HEADER + '''from gm.core import log


''' + seg_text("TrackRowDelegate") + "\n"
(GM / "ui" / "delegates.py").write_text(delegates, encoding="utf-8")

# ------------------------------------------------------------- gm/ui/rail.py
rail = HEADER + '''from gm.ui.widgets import IconButton


''' + seg_text("SideRail") + "\n"
(GM / "ui" / "rail.py").write_text(rail, encoding="utf-8")

# -------------------------------------------------------- gm/ui/player_bar.py
pbar = HEADER + '''from widgets import ClickableSlider
from gm.core import RepeatMode, log
from gm.ui.widgets import IconButton, MarqueeLabel


''' + seg_text("PlayerBar") + "\n"
(GM / "ui" / "player_bar.py").write_text(pbar, encoding="utf-8")

# ----------------------------------------------------- gm/ui/stats_widgets.py
statsw = HEADER + '''from gm.core import fmt_time, log
from gm.ui.widgets import IconButton


''' + seg_text("StatsChartBar", "StatTrackRow") + "\n"
(GM / "ui" / "stats_widgets.py").write_text(statsw, encoding="utf-8")

# ---------------------------------------------------------------- gm/workers.py
workers = HEADER + '''import coverart
from gm.core import extract_title_artist, log


''' + seg_text("CoverLoaderThread", "TagLoaderThread", "AlbumsWorkerThread") + "\n"
(GM / "workers.py").write_text(workers, encoding="utf-8")

# ---------------------------------------------------------------- gm/window.py
window = HEADER + '''from audio import AudioBackend
from scanner import FolderScanner
from settings_dialog import SettingsDialog
from mini_player import MiniPlayer
from widgets import ClickableSlider
from tray_menu import TrayMenuWidget
from theme_picker import ThemePickerPopup
import coverart
import ipc
import updater
from update_dialog import UpdateCheckWorker, UpdateBanner, UpdateDialog
from mediakeys import MediaKeyFilter

try:
    from autottheme import windows_prefers_dark as _os_prefers_dark
except ImportError:
    def _os_prefers_dark():
        return True

from gm.core import (
    RepeatMode, View, SortMode, _is_scratch_path, _track_item_text,
    _list_subdirs, _dir_has_subdirs, _rgba, log,
)
from gm.ui.widgets import (
    IconButton, CoverLabel, MarqueeLabel, LoadingOverlay, _SpinnerCanvas,
)
from gm.ui.delegates import TrackRowDelegate
from gm.ui.player_bar import PlayerBar
from gm.ui.rail import SideRail
from gm.ui.stats_widgets import StatsChartBar, StatTrackRow
from gm.workers import (
    CoverLoaderThread, TagLoaderThread, AlbumsWorkerThread,
)


''' + seg_text("MainWindow") + "\n"
(GM / "window.py").write_text(window, encoding="utf-8")

# ------------------------------------------------------------------- gm/app.py
app_py = HEADER + '''from applog import setup_logging

from gm.core import log
from gm.window import MainWindow


''' + seg_text("_acquire_single_instance_lock", "_forward_to_running",
               "_install_crash_guard", "_enable_windows_glass",
               "_audio_files_from_argv", "main") + "\n"
(GM / "app.py").write_text(app_py, encoding="utf-8")

# ---------------------------------------------------------------- __init__ files
(GM / "__init__.py").write_text(
    '"""Golden Music 2.3 — application package (split of the former main.pyw)."""\n'
    'from version import APP_VERSION  # noqa: F401\n', encoding="utf-8")
(GM / "ui" / "__init__.py").write_text(
    '"""Golden Music UI components."""\n', encoding="utf-8")

# ------------------------------------------------------------------ main.py shim
(REPO / "main.py").write_text('''"""Thin importable entry point (v3.0).

Windows can `import main` for the .pyw file directly; on Linux/macOS (and in
CI) this .py shim provides the same module surface.  main.pyw remains the
double-click entry point and simply delegates here.
"""
from gm.core import (  # noqa: F401
    RepeatMode, View, SortMode, _is_scratch_path, _track_item_text,
    _list_subdirs, _dir_has_subdirs, _rgba, log,
)
from gm.ui.widgets import IconButton, CoverLabel, MarqueeLabel, LoadingOverlay  # noqa: F401
from gm.ui.delegates import TrackRowDelegate  # noqa: F401
from gm.ui.player_bar import PlayerBar  # noqa: F401
from gm.ui.rail import SideRail  # noqa: F401
from gm.ui.stats_widgets import StatsChartBar, StatTrackRow  # noqa: F401
from gm.workers import CoverLoaderThread, TagLoaderThread, AlbumsWorkerThread  # noqa: F401
from gm.window import MainWindow, APP_NAME  # noqa: F401
from gm.app import (  # noqa: F401
    main, _acquire_single_instance_lock, _forward_to_running,
    _install_crash_guard, _enable_windows_glass, _audio_files_from_argv,
)
from random import choice, randrange  # noqa: F401  (legacy module surface)

if __name__ == "__main__":
    raise SystemExit(main())
''', encoding="utf-8")

# ------------------------------------------------------------------ main.pyw thin
(REPO / "main.pyw").write_text('''"""
Golden Music — v3.0 thin entry point.

The application now lives in the `gm/` package; this file only exists so
double-clicking it on Windows still starts the player exactly as before.
"""
from main import main

if __name__ == "__main__":
    raise SystemExit(main())
''', encoding="utf-8")

print("split complete")
for p in sorted(GM.rglob("*.py")):
    print(" ", p.relative_to(REPO), p.stat().st_size, "bytes")
