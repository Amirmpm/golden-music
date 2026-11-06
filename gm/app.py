"""
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

from applog import setup_logging

import ipc
import updater
from gm.core import log
from gm.window import MainWindow


def _acquire_single_instance_lock() -> bool:
    """Hold a named mutex for the process lifetime.

    Returns True if we are the first instance. The handle is kept in a module
    global so it survives for the whole process — releasing on exit is optional
    since Windows cleans up abandoned mutexes automatically.
    """
    global _mutex_handle
    try:
        import win32event
        import win32api
        import winerror
        _mutex_handle = win32event.CreateMutex(None, False, "GoldenMusic_SingleInstance_Mutex")
        if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
            return False
        return True
    except ImportError:
        # pywin32 missing — fall back to a lock-file heuristic. A crashed run
        # leaves the file behind, so verify the recorded pid is still alive
        # before refusing to start.
        try:
            lock = config_path().parent / "instance.lock"
            if lock.exists():
                try:
                    pid = int(lock.read_text(encoding="utf-8").strip() or 0)
                except (OSError, ValueError):
                    pid = 0
                if pid and pid != os.getpid():
                    try:
                        os.kill(pid, 0)     # raises OSError if pid is gone
                        return False        # a live instance really is running
                    except PermissionError:
                        return False        # exists but not ours — treat as running
                    except OSError:
                        pass                # stale lock from a crashed run
                lock.write_text(str(os.getpid()), encoding="utf-8")
                return True
            lock.write_text(str(os.getpid()), encoding="utf-8")
            return True
        except OSError:
            return True  # never block the app over a lock failure
    except Exception as e:
        log.warning(f"Single-instance lock unavailable: {e}")
        return True


def _forward_to_running(files: list) -> bool:
    """Hand a second launch over to the already-running instance.

    Files (from "Open with") are forwarded as an open command; a bare
    relaunch sends a show command so an instance hidden in the tray is
    revealed. Returns True only when the running instance acknowledged.
    """
    log.info("Second launch detected — forwarding to the running instance")
    try:
        return ipc.forward_launch(files)
    except Exception as e:
        log.warning(f"Could not forward to running instance: {e}")
        return False


def _install_crash_guard(app):
    """Log unhandled exceptions AND exceptions raised inside Qt slots.

    sys.excepthook alone never fires for exceptions inside Qt slots in
    PyQt6 — they propagate through QApplication.notify(). A subclassed
    QApplication overrides notify() (the official interception point):
    a slot that throws is logged with full traceback and swallowed so one
    bad handler can't kill the player. threading.excepthook +
    unraisablehook cover worker-thread deaths (scanner, tag/cover loaders).
    Truly fatal conditions (MemoryError) are re-raised.
    """
    import traceback as _tb
    import threading as _threading

    def excepthook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, (KeyboardInterrupt, SystemExit, MemoryError)):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        log.error("Unhandled exception:\n" +
                  "".join(_tb.format_exception(exc_type, exc_value, exc_tb)))

    sys.excepthook = excepthook

    def thread_excepthook(args):
        try:
            if issubclass(args.exc_type,
                           (KeyboardInterrupt, SystemExit, MemoryError)):
                sys.__excepthook__(args.exc_type, args.exc_value,
                                   args.exc_traceback)
                return
            log.error("Unhandled thread exception "
                      f"({getattr(args, 'thread', None) and args.thread.name}):\n" +
                      "".join(_tb.format_exception(args.exc_type, args.exc_value,
                                                   args.exc_traceback)))
        except Exception:
            pass

    _threading.excepthook = thread_excepthook

    try:
        def _unraisable(args):
            try:
                log.error("Unraisable exception "
                          f"({getattr(args, 'where', '')}): "
                          f"{args.exc_value!r}")
            except Exception:
                pass
        sys.unraisablehook = _unraisable
    except Exception:
        pass

    # Qt 6.5+: exceptions in slots propagate through the event loop — route
    # notify() failures to the excepthook above.
    cls = type(app)
    if not getattr(cls, "_gm_notify_guarded", False):
        cls._gm_notify_guarded = True
        _orig_notify = cls.notify

        def _guarded_notify(self, receiver, event):
            try:
                return _orig_notify(self, receiver, event)
            except (KeyboardInterrupt, SystemExit, MemoryError):
                raise
            except Exception:
                excepthook(*sys.exc_info())
                return False

        cls.notify = _guarded_notify


def _enable_windows_glass(widget):
    """Best-effort Mica/Acrylic backdrop (Win 11 22H2+) + dark titlebar.
    Falls back silently on older systems."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = int(widget.winId())
        dwm = ctypes.windll.dwmapi
        try:
            DWMSBT_MAINWINDOW = 2
            dwm.DwmSetWindowAttribute(hwnd, 38,
                ctypes.byref(ctypes.c_int(DWMSBT_MAINWINDOW)), 4)
        except Exception:
            pass
        try:
            dwm.DwmSetWindowAttribute(hwnd, 20,
                ctypes.byref(ctypes.c_int(1)), 4)   # dark chrome
        except Exception:
            pass
        try:
            margins = ctypes.c_int(-1)
            dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
        except Exception:
            pass
    except Exception as e:
        log.debug(f"glass backdrop unavailable: {e}")


def _audio_files_from_argv(argv: list) -> list:
    """Collect "Open with" file arguments: absolute, existing, audio
    extensions only, deduplicated, bounded count (ipc.sanitize_paths
    enforces the exact same rules as the IPC channel)."""
    return ipc.sanitize_paths(list(argv)[:ipc.MAX_PATHS_PER_MESSAGE])


def main():
    log_path = setup_logging()
    # Build smoke test: `GoldenMusic.exe --smoke` boots the real window,
    # reports OK, and exits — used by build_release.py to prove the bundle
    # is not missing DLLs/plugins after trimming.
    smoke = "--smoke" in sys.argv
    if smoke:
        sys.argv.remove("--smoke")
    # Passed by the Inno [Run] entry after a successful self-update:
    # celebrate once + evaluate the pending marker + resume last track.
    updated_flag = "--updated" in sys.argv
    if updated_flag:
        sys.argv.remove("--updated")
    # "Open with" / file-association launches pass media files in argv.
    argv_files = _audio_files_from_argv(sys.argv[1:])
    if sys.platform == 'win32' and not smoke:
        # Single-instance: forward to the running window instead of
        # launching a copy (files are handed over too).
        if not _acquire_single_instance_lock():
            if _forward_to_running(argv_files):
                sys.exit(0)
            # Mutex held but the socket is unreachable — stale lock from a
            # crashed/killed run; start normally rather than dead-locking
            # the user out of their player.
            log.warning("single-instance lock held but IPC is unreachable "
                        "— starting anyway")
    QApplication.setStyle(QStyleFactory.create("Fusion"))
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(ORG_NAME)
    app.setQuitOnLastWindowClosed(False)
    # Use custom logo as app icon
    icon_path = Path(__file__).parent / "assets" / "icon.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    w = MainWindow()
    w._updated_flag = updated_flag
    _install_crash_guard(app)
    w.show()
    _enable_windows_glass(w)
    if QSystemTrayIcon.isSystemTrayAvailable():
        w.tray.show()
    w._handle_update_startup()
    # Cold-start "Open with": give async init (config load, theme apply) a
    # moment, then queue and play the handed-over files.
    if argv_files and not smoke:
        QTimer.singleShot(400, lambda: w.open_external_files(argv_files))
    if smoke:
        # Let pending async init (config load, theme apply) run, then quit
        # through the NORMAL close path: a bare app.quit() tears down the
        # interpreter while scanner/tag/cover workers are mid-emit
        # ("QThread: Destroyed while thread is still running" → segfault
        # on large libraries). close() runs closeEvent, which cancels and
        # joins every worker first.
        from PyQt6.QtCore import QTimer
        w._smoke = True
        print(f"SMOKE OK: window visible={w.isVisible()}, "
              f"theme={getattr(w, 'theme', {}).get('name', '?')}, "
              f"tracks={len(getattr(w, 'library', []))}, "
              f"rate_popup_icons=6")

        def _smoke_quit():
            try:
                w._force_quit = True
                w.close()
            except Exception as e:
                print(f"smoke close: {e}")
            app.quit()

        QTimer.singleShot(2500, _smoke_quit)
    code = app.exec()
    if smoke:
        print("SMOKE DONE")
    sys.exit(code)

