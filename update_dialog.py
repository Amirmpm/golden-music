"""
Golden Music — update UI: check worker, non-modal banner, update dialog.

The dialog is plain-text only (release notes are stripped to safe text —
never rendered HTML, never remote resources). All network work happens on
QThread workers; the UI thread only renders results. Non-installer
editions (portable / source) can never self-replace — they get an
"Open release page" flow instead.
"""
import re
import threading

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit,
    QProgressBar, QWidget
)

import updater

log_name = "app.update_ui"


def notes_to_plain_text(md: str, limit: int = 4000) -> str:
    """Reduce markdown release notes to safe plain text (no remote
    content, no markdown injection into Qt rich-text labels)."""
    if not md:
        return ""
    text = md
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)      # images
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)       # links -> text
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)          # headers
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)              # bold
    text = re.sub(r"\*([^*]+)\*", r"\1", text)                  # italic
    text = re.sub(r"`([^`]+)`", r"\1", text)                    # code
    text = re.sub(r"^>\s?", "", text, flags=re.M)               # quotes
    lines = [ln.rstrip() for ln in text.splitlines()]
    text = "\n".join(lines).strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + "\n…"
    return text


def human_size(n: int) -> str:
    f = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024 or unit == "GB":
            return f"{f:.1f} {unit}" if unit != "B" else f"{int(f)} B"
        f /= 1024.0
    return f"{f:.1f} GB"


class UpdateCheckWorker(QThread):
    """Background /releases/latest probe (auto + manual checks)."""

    finished_with = pyqtSignal(dict)

    def __init__(self, installed_version, etag=None, parent=None):
        super().__init__(parent)
        self._version = installed_version
        self._etag = etag

    def run(self):
        try:
            result = updater.check_for_update(
                updater.HttpFetcher(), self._version, etag=self._etag)
        except Exception as e:      # never let a worker thread die silently
            result = {"status": "offline", "detail": str(e)}
        self.finished_with.emit(result)


class _DownloadWorker(QThread):
    """Streams the verified installer into the updates dir."""

    progress = pyqtSignal(int, int)     # done, total
    done_ok = pyqtSignal(str)           # installer path
    done_err = pyqtSignal(str)

    def __init__(self, asset, dest_dir, parent=None):
        super().__init__(parent)
        self._asset = asset
        self._dest_dir = dest_dir
        self.cancel = threading.Event()

    def run(self):
        try:
            path = updater.download_installer(
                updater.HttpFetcher(), self._asset, self._dest_dir,
                progress_cb=lambda d, t: self.progress.emit(d, t),
                cancel=self.cancel)
            self.done_ok.emit(str(path))
        except updater.UpdaterError as e:
            self.done_err.emit(str(e))
        except Exception as e:
            self.done_err.emit(f"unexpected: {e}")


class UpdateBanner(QPushButton):
    """Small non-modal pill pinned above the player bar: 'Update to vX'."""

    def __init__(self, main_window):
        super().__init__(main_window)
        self._mw = main_window
        self.setVisible(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(main_window._open_update_dialog)
        self.setStyleSheet(self._style())

    def _style(self):
        t = self._mw.theme
        return (
            f"QPushButton {{ background: {t.get('panel_bg_2', '#2a2a2a')};"
            f" color: {t.get('gold_light', '#e8c87a')};"
            f" border: 1px solid {t.get('gold', '#c9a227')};"
            f" border-radius: 12px; padding: 5px 12px; font-size: 12px;"
            f" font-weight: 600; }}"
            f"QPushButton:hover {{ border: 1px solid {t.get('gold_light', '#e8c87a')}; }}")

    def refresh_theme(self):
        self.setStyleSheet(self._style())

    def show_for(self, release):
        self.setText(f"⭡  Update to v{release['version']} available")
        self.adjustSize()
        self._reposition()
        self.show()
        self.raise_()

    def _reposition(self):
        mw = self._mw
        pb = getattr(mw, "player_bar", None)
        bottom = pb.geometry().top() - 8 if pb is not None else mw.height() - 60
        self.move(mw.width() - self.width() - 20, max(8, bottom - self.height()))

    def reposition_if_visible(self):
        if self.isVisible():
            self._reposition()


class UpdateDialog(QDialog):
    """Current → new, size, plain-text notes, four explicit actions."""

    def __init__(self, main_window, release, asset):
        super().__init__(main_window)
        self._mw = main_window
        self._release = release
        self._asset = asset
        self._worker = None
        mode = updater.detect_install_mode()
        self.setWindowTitle("Update Golden Music")
        self.setModal(True)
        self.setMinimumWidth(520)

        t = main_window.theme
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        head = QLabel(
            f"<b>Golden Music v{release['version']}</b> is available "
            f"(you have v{main_window.APP_VERSION})."
            + (f"<br><span style='color:{t['muted']}'>"
               f"Download size: {human_size(asset.get('size') or 0)}</span>"
               if asset else ""))
        head.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(head)

        notes = QPlainTextEdit(notes_to_plain_text(release.get("notes", "")))
        notes.setReadOnly(True)
        notes.setFixedHeight(170)
        layout.addWidget(QLabel("What's new:"))
        layout.addWidget(notes)

        # Progress row (hidden until a download starts)
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        self._progress.setRange(0, 100)
        self._status = QLabel("")
        self._status.setStyleSheet(f"color: {t['muted']}; font-size: 11px;")
        self._status.setVisible(False)
        layout.addWidget(self._progress)
        layout.addWidget(self._status)

        buttons = QHBoxLayout()
        if mode == "installer" and asset is not None:
            btn_now = QPushButton("Update now")
            btn_now.setObjectName("GoldBtn")
            btn_now.clicked.connect(self._update_now)
            btn_later_install = QPushButton("Install when I quit")
            btn_later_install.clicked.connect(self._install_on_quit)
            buttons.addWidget(btn_now)
            buttons.addWidget(btn_later_install)
        else:
            btn_page = QPushButton("Open release page")
            btn_page.setObjectName("GoldBtn")
            btn_page.clicked.connect(self._open_release_page)
            buttons.addWidget(btn_page)
        btn_snooze = QPushButton("Remind me later")
        btn_snooze.clicked.connect(self._remind_later)
        btn_skip = QPushButton("Skip this version")
        btn_skip.clicked.connect(self._skip_version)
        buttons.addWidget(btn_snooze)
        buttons.addWidget(btn_skip)
        layout.addLayout(buttons)

    # ---- actions -------------------------------------------------------
    def _update_now(self):
        self._start_download(install_on_quit=False)

    def _install_on_quit(self):
        self._start_download(install_on_quit=True)

    def _start_download(self, install_on_quit: bool):
        if self._worker is not None:
            return
        self._install_on_quit_flag = install_on_quit
        self._progress.setVisible(True)
        self._progress.setValue(0)
        self._worker = _DownloadWorker(
            self._asset, self._mw.updates_dir(), parent=self)
        self._worker.progress.connect(self._on_progress)
        self._worker.done_ok.connect(lambda p: self._on_downloaded(p))
        self._worker.done_err.connect(self._on_download_error)
        self._worker.start()

    def _on_progress(self, done, total):
        if total:
            self._progress.setValue(int(done * 100 / total))
            self._status.setVisible(True)
            self._status.setText(f"{human_size(done)} / {human_size(total)}")

    def _on_downloaded(self, path: str):
        self._progress.setValue(100)
        if self._install_on_quit_flag:
            self._mw.set_pending_install(path)
            self._mw._show_toast(
                "Update downloaded — it installs when you quit Golden Music.")
            self.accept()
            return
        # Full self-update: persist state, launch installer, quit.
        try:
            updater.write_pending_marker(self._mw.updates_dir(),
                                         self._release["version"])
        except Exception:
            pass
        if self._mw.launch_update_and_quit(path):
            self.accept()
        else:
            self._on_download_error("could not launch the installer")

    def _on_download_error(self, msg: str):
        self._status.setVisible(True)
        self._status.setText(f"Download failed: {msg}")
        self._worker = None

    def cancelEvent(self, e):
        if self._worker is not None:
            self._worker.cancel.set()

    # ---- light actions ---------------------------------------------------
    def _remind_later(self):
        self._mw.snooze_update()
        self.reject()

    def _skip_version(self):
        self._mw.skip_update_version(self._release["version"])
        self.reject()

    def _open_release_page(self):
        QDesktopServices.openUrl(QUrl(self._release["html_url"]))
        self.accept()
