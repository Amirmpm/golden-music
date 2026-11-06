"""Golden Music 2.3 — platform abstraction layer.

Every OS-specific capability the app uses is exposed behind a small function
here so the rest of the code stays platform-neutral (Windows / Linux /
macOS; Android is documented as best-effort — see docs/DECISIONS.md #d3).

Layers that keep their own platform files (kept for history/compat):
  * mediakeys.py  — global media keys (win32 RegisterHotKey)
  * autottheme.py — dark/light detection (winreg on Windows)
  * ipc.py        — single-instance socket hand-off (cross-platform)
This module wraps them where practical and adds the missing pieces.
"""
from __future__ import annotations

import os
import sys
import subprocess  # only for xdg-open / explorer / open (fixed argv, no shell)
import logging
from pathlib import Path

log = logging.getLogger("app.platform")

WINDOWS = sys.platform == "win32"
LINUX = sys.platform.startswith("linux")
MACOS = sys.platform == "darwin"
ANDROID = "android" in sys.getandroidapilevel.__name__ if hasattr(sys, "getandroidapilevel") else False
PLATFORM_ID = ("android" if ANDROID else
               "windows" if WINDOWS else
               "linux" if LINUX else
               "macos" if MACOS else "other")


# ---------------------------------------------------------------- file reveal
def open_file_location(path: str) -> bool:
    """Reveal a file in the platform file manager (Explorer / xdg / Finder)."""
    try:
        if not path or not os.path.exists(path):
            return False
        if WINDOWS:
            # fixed argv, no shell interpolation
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])  # nosec B603,B607 (fixed program, validated path)
            return True
        if MACOS:
            subprocess.Popen(["open", "-R", path])  # nosec B603 (fixed program)
            return True
        folder = os.path.dirname(os.path.abspath(path))
        subprocess.Popen(["xdg-open", folder])  # nosec B603 (fixed program)
        return True
    except Exception as e:
        log.warning("open_file_location failed: %s", e)
        return False


def open_url(url: str) -> bool:
    """Open an external URL in the default browser (validated https/http)."""
    try:
        if not url.startswith(("https://", "http://")):
            return False
        if WINDOWS:
            os.startfile(url)  # nosec B606 (validated scheme)  # noqa: S606
            return True
        if MACOS:
            subprocess.Popen(["open", url])  # nosec B603
            return True
        subprocess.Popen(["xdg-open", url])  # nosec B603
        return True
    except Exception as e:
        log.warning("open_url failed: %s", e)
        return False


# ------------------------------------------------------------------ windowing
def bring_to_foreground(window) -> bool:
    """Raise + focus a window across platforms (best effort)."""
    try:
        if WINDOWS:
            import ctypes
            hwnd = int(window.winId())
            ctypes.windll.user32.ShowWindow(hwnd, 9)      # SW_RESTORE
            ctypes.windll.user32.SetForegroundWindow(hwnd)
            return True
        window.show()
        window.raise_()
        window.activateWindow()
        return True
    except Exception as e:
        log.warning("bring_to_foreground failed: %s", e)
        return False


def enable_windows_glass(widget) -> bool:
    """Win11 Mica + dark titlebar. No-op (returns False) elsewhere."""
    if not WINDOWS:
        return False
    try:
        import ctypes
        from ctypes.wintypes import DWORD
        hwnd = int(widget.winId())
        dwm = ctypes.windll.dwmapi
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        DWMWA_SYSTEMBACKDROP_TYPE = 38
        for attr, val in ((DWMWA_USE_IMMERSIVE_DARK_MODE, 1),
                          (DWMWA_SYSTEMBACKDROP_TYPE, 2)):
            dwm.DwmSetWindowAttribute(hwnd, attr,
                                      ctypes.byref(DWORD(val)),
                                      ctypes.sizeof(DWORD()))
        margins = ctypes.c_int * 4
        dwm.DwmExtendFrameIntoClientArea(hwnd, margins(-1, -1, -1, -1))
        return True
    except Exception as e:
        log.warning("windows glass failed: %s", e)
        return False


# ------------------------------------------------------------- theme detection
def prefers_dark() -> bool:
    """OS dark-mode preference; conservative True on unknown platforms."""
    try:
        from autottheme import windows_prefers_dark
        return bool(windows_prefers_dark())
    except Exception:
        pass
    if LINUX:
        try:  # GNOME/gsettings (optional)
            out = subprocess.run(  # nosec B603,B607 (fixed program+args)
                ["gsettings", "get",
                 "org.gnome.desktop.interface", "color-scheme"],
                capture_output=True, text=True, timeout=3)
            return "dark" in (out.stdout or "").lower()
        except Exception:
            return True
    return True


# ------------------------------------------------------- OS media integration
def smtc_supported() -> bool:
    """System Media Transport Controls (Windows SMTC / Linux MPRIS)."""
    if WINDOWS:
        # QMediaPlayer's windowsmediafoundation backend registers SMTC
        # automatically when the Qt engine is active.
        return True
    return False


def mpris_available() -> bool:
    """True when a DBus session bus + dbus-python are usable (Linux)."""
    if not LINUX:
        return False
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        return False
    try:
        import dbus  # noqa: F401
        return True
    except Exception:
        return False


def global_media_keys_available() -> bool:
    return WINDOWS  # mediakeys.py uses win32 RegisterHotKey


# --------------------------------------------------------- platform utilities
def default_music_dir() -> str:
    try:
        from PyQt6.QtCore import QStandardPaths
        d = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.MusicLocation)
        if d:
            return d
    except Exception:
        pass
    return str(Path.home() / "Music")


def config_home_hint() -> str:
    """Human-readable location of the per-user data dir."""
    if WINDOWS:
        return "%USERPROFILE%\\.goldenmusic\\"
    return "~/.goldenmusic/"


def supports_tray() -> bool:
    try:
        from PyQt6.QtWidgets import QSystemTrayIcon
        return QSystemTrayIcon.isSystemTrayAvailable()
    except Exception:
        return False


def supports_dpi_scale_settings() -> bool:
    """UI Scale override is most relevant on Windows/Linux X11."""
    return True
