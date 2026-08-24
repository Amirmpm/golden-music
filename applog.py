"""
Golden Music — Application logging.

File-based logging next to the config directory with size-based rotation
(5 files x 1 MiB). Every module logs through `logging.getLogger(__name__)`;
this module only needs to be configured once at startup (main.py).

Also installs a Qt message handler so qWarning/qCritical from the Qt side
(media backend errors, plugin problems) land in the same file.
"""
import logging
import logging.handlers
import os
import sys

LOG_MAX_BYTES = 1 * 1024 * 1024   # 1 MiB per file
LOG_BACKUP_COUNT = 4              # + up to 4 rotated files


def log_file_path() -> "os.PathLike":
    """Log lives beside the config so it follows the same portable/home rule."""
    from config import config_path
    return config_path().parent / "goldenmusic.log"


def setup_logging(level=logging.INFO) -> str:
    """Configure root logger. Returns the log file path ("" on failure).

    Safe to call in any environment — if the log dir is not writable the app
    still runs (console output only).
    """
    root = logging.getLogger()
    root.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S")

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(fmt)
    root.addHandler(console)

    path = ""
    try:
        path = str(log_file_path())
        handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8")
        handler.setFormatter(fmt)
        root.addHandler(handler)
    except OSError as e:
        # Never let logging failure break the app
        print(f"Logging to file unavailable ({e}); console-only.", file=sys.stderr)

    _install_qt_message_handler()
    # Announce session start (helps correlate multi-run log files)
    logging.getLogger("app").info("=== Golden Music session started ===")
    return path


def _qt_log_level(msg_type, context, message):
    """Qt message hook. Signature per qInstallMessageHandler:
    (QtMsgType, QMessageLogContext, str)."""
    from PyQt6.QtCore import QtMsgType
    try:
        if msg_type == QtMsgType.QtDebugMsg:
            lvl = logging.DEBUG
        elif msg_type == QtMsgType.QtInfoMsg:
            lvl = logging.INFO
        elif msg_type == QtMsgType.QtWarningMsg:
            lvl = logging.WARNING
        else:  # Critical / Fatal
            lvl = logging.ERROR
        where = ""
        try:
            if context.function:
                where = f" ({context.function.split('::')[-1]})"
        except Exception:
            pass
        logging.getLogger("qt").log(lvl, f"{str(message)[:2000]}{where}")
    except Exception:
        pass  # never crash the app from a log hook


def _install_qt_message_handler():
    try:
        from PyQt6.QtCore import qInstallMessageHandler
        qInstallMessageHandler(_qt_log_level)
    except Exception:
        pass


# Convenience: swap every bare `print(...)` call-site pattern to this
logger = logging.getLogger("app")
