"""Golden Music v3 — Downloads page (Nuvx Downloads layout).

  * one-time download-folder configuration: when the folder is unset a
    clear banner asks ONCE, then the choice is reused for every future
    download (changeable later in Settings only)
  * active queue rows: title/artist, progress bar with %, speed, size
    (downloaded / total), pause / resume / cancel / retry
  * completed list: "N songs · folder" line + open-folder action +
    show-in-folder per item
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                              QPushButton, QProgressBar, QFrame,
                              QScrollArea, QSizePolicy)
from PyQt6.QtGui import QGuiApplication

import downloads as dl

JobState = dl.JobState


def _fmt_size(n: int) -> str:
    n = max(0, int(n or 0))
    if n < 1024:
        return f"{n} B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f} KB"
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.1f} MB"
    return f"{n / 1024 ** 3:.2f} GB"


def _fmt_speed(bps: float) -> str:
    return f"{_fmt_size(int(bps))}/s"


class JobRow(QFrame):
    """One active/queued download."""

    def __init__(self, job: "dl.DownloadJob", theme: dict, parent=None):
        super().__init__(parent)
        self.job = job
        self.theme = dict(theme or {})
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(6)

        head = QHBoxLayout()
        self.title = QLabel(f"{job.artist + ' — ' if job.artist else ''}{job.title}")
        head.addWidget(self.title, 1)
        self.state_lbl = QLabel(job.state.capitalize())
        head.addWidget(self.state_lbl)
        lay.addLayout(head)

        self.progress = QProgressBar()
        self.progress.setFixedHeight(6)
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 100)
        lay.addWidget(self.progress)

        info = QHBoxLayout()
        self.info = QLabel("")
        info.addWidget(self.info, 1)
        lay.addLayout(info)

        btns = QHBoxLayout()
        btns.addStretch(1)
        self.pause_btn = QPushButton("Pause")
        self.resume_btn = QPushButton("Resume")
        self.cancel_btn = QPushButton("Cancel")
        self.retry_btn = QPushButton("Retry")
        for b in (self.pause_btn, self.resume_btn, self.cancel_btn,
                  self.retry_btn):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            btns.addWidget(b)
        lay.addLayout(btns)
        self.set_theme(self.theme)
        self.refresh(job)

    def set_theme(self, theme: dict):
        self.theme = dict(theme or {})
        t = self.theme
        accent = t.get("gold", "#e9b957")
        text = t.get("text", "#f2f0f5")
        muted = t.get("muted", "#b2acba")
        border = t.get("border", "rgba(255,255,255,0.14)")
        fill = t.get("glass_fill", "rgba(255,255,255,0.08)")
        self.setStyleSheet(f"""
            QFrame {{ background: {fill}; border: 1px solid {border};
                      border-radius: 10px; }}
        """)
        self.title.setStyleSheet(f"color: {text}; font-size: 14px; "
                                f"font-weight: 600; background: transparent;")
        self.state_lbl.setStyleSheet(f"color: {accent}; font-size: 12px; "
                                     f"background: transparent;")
        self.info.setStyleSheet(f"color: {muted}; font-size: 12px; "
                                f"background: transparent;")
        for b in (self.pause_btn, self.resume_btn, self.cancel_btn,
                  self.retry_btn):
            b.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {accent};
                    border: 1px solid {border}; border-radius: 14px;
                    padding: 4px 14px; font-size: 12px; font-weight: 600;
                }}
                QPushButton:hover {{ background: {t.get('glass_hover',
                                          'rgba(255,255,255,0.2)')}; }}
            """)
        self.progress.setStyleSheet(f"""
            QProgressBar {{ background: {border}; border: none;
                            border-radius: 3px; }}
            QProgressBar::chunk {{ background: {accent}; border-radius: 3px; }}
        """)

    def refresh(self, job: "dl.DownloadJob"):
        self.job = job
        self.progress.setValue(job.progress_pct())
        self.info.setText(
            f"{_fmt_size(job.bytes_done)}"
            + (f" of {_fmt_size(job.total_bytes)}" if job.total_bytes else "")
            + (f"  ·  {_fmt_speed(job.speed_bps)}" if job.speed_bps else "")
            + (f"  ·  {job.error}" if job.error else ""))
        self.state_lbl.setText({
            JobState.QUEUED: "Queued", JobState.RUNNING: "Downloading",
            JobState.PAUSED: "Paused", JobState.COMPLETED: "Completed",
            JobState.FAILED: "Failed", JobState.CANCELLED: "Cancelled",
        }.get(job.state, job.state.capitalize()))
        self.pause_btn.setVisible(job.state in (JobState.QUEUED,
                                                JobState.RUNNING))
        self.resume_btn.setVisible(job.state == JobState.PAUSED)
        self.retry_btn.setVisible(job.state in (JobState.FAILED,
                                                JobState.CANCELLED))
        self.cancel_btn.setVisible(job.state not in (JobState.COMPLETED,
                                                     JobState.FAILED,
                                                     JobState.CANCELLED))


class DownloadsPage(QWidget):
    """Downloads page: folder config + queue + completed."""

    choose_folder_requested = pyqtSignal()          # one-time config
    open_folder_requested = pyqtSignal()
    show_in_folder_requested = pyqtSignal(str)     # path

    def __init__(self, theme: dict, manager=None, parent=None):
        super().__init__(parent)
        self.theme = dict(theme or {})
        self.manager = manager
        self._folder: Path | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.setSpacing(12)

        self.title = QLabel("Downloads")
        lay.addWidget(self.title)

        # folder banner (config once)
        self.folder_banner = QFrame()
        bl = QVBoxLayout(self.folder_banner)
        bl.setContentsMargins(16, 12, 16, 12)
        self.folder_line = QLabel("")
        self.folder_line.setWordWrap(True)
        bl.addWidget(self.folder_line)
        btns = QHBoxLayout()
        self.choose_btn = QPushButton("Choose download folder")
        self.open_btn = QPushButton("Open folder")
        for b in (self.choose_btn, self.open_btn):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            btns.addWidget(b)
        btns.addStretch(1)
        bl.addLayout(btns)
        lay.addWidget(self.folder_banner)

        # queue
        self.queue_title = QLabel("Active")
        lay.addWidget(self.queue_title)
        self.queue_host = QWidget()
        self.queue_lay = QVBoxLayout(self.queue_host)
        self.queue_lay.setContentsMargins(0, 0, 0, 0)
        self.queue_lay.setSpacing(8)
        self.queue_scroll = QScrollArea()
        self.queue_scroll.setWidgetResizable(True)
        self.queue_scroll.setWidget(self.queue_host)
        self.queue_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }")
        lay.addWidget(self.queue_scroll, 1)

        # completed
        self.done_title = QLabel("Recently downloaded")
        lay.addWidget(self.done_title)
        self.done_list = QLabel("")
        self.done_list.setWordWrap(True)
        lay.addWidget(self.done_list)

        self.choose_btn.clicked.connect(self.choose_folder_requested.emit)
        self.open_btn.clicked.connect(self._open_folder)
        self.set_theme(self.theme)

    # ------------------------------------------------------------------
    def set_theme(self, theme: dict):
        self.theme = dict(theme or {})
        t = self.theme
        accent = t.get("gold", "#e9b957")
        text = t.get("text", "#f2f0f5")
        muted = t.get("muted", "#b2acba")
        border = t.get("border", "rgba(255,255,255,0.14)")
        fill = t.get("glass_fill", "rgba(255,255,255,0.08)")
        self.title.setStyleSheet(
            f"color: {text}; font-size: 34px; font-weight: 700; "
            f"background: transparent;")
        self.folder_banner.setStyleSheet(f"""
            QFrame {{ background: {fill}; border: 1px solid {border};
                      border-radius: 12px; }}
        """)
        self.folder_line.setStyleSheet(f"color: {muted}; font-size: 13px; "
                                       f"background: transparent;")
        self.queue_title.setStyleSheet(
            f"color: {text}; font-size: 21px; font-weight: 700; "
            f"background: transparent;")
        self.done_title.setStyleSheet(
            f"color: {muted}; font-size: 11.5px; font-weight: 600; "
            f"letter-spacing: 1.2px; background: transparent;")
        self.done_list.setStyleSheet(f"color: {muted}; font-size: 13px; "
                                      f"background: transparent;")
        for b in (self.choose_btn, self.open_btn):
            b.setStyleSheet(f"""
                QPushButton {{
                    background: {accent};
                    color: {'#111' if t.get('is_dark', True) else '#fff'};
                    border: none; border-radius: 19px; padding: 8px 20px;
                    font-size: 13px; font-weight: 600;
                }}
            """)

    def set_folder(self, folder):
        self._folder = Path(folder) if folder else None
        if self._folder:
            self.folder_line.setText(
                f"Download folder: {self._folder}  —  used automatically "
                f"for every download (change it in Settings).")
        else:
            self.folder_line.setText(
                "Choose a download folder once — it is reused for all "
                "future downloads.")
        self.open_btn.setVisible(bool(self._folder))

    def set_manager(self, manager):
        self.manager = manager
        manager.job_updated.connect(lambda _j: self.refresh())
        manager.queue_changed.connect(self.refresh)
        manager.file_added.connect(
            lambda path, _job: self._note_completed(path))

    def set_completed(self, records: list):
        if not records:
            self.done_list.setText("Nothing downloaded yet.")
            return
        lines = []
        for r in records[:12]:
            title = r.get("title") or "—"
            artist = r.get("artist") or ""
            lines.append(f"{' · '.join(x for x in (artist, title) if x)}")
        self.done_list.setText("\n".join(lines))
        self.done_title.setText(
            f"Recently downloaded — {len(records)} song"
            + ("s" if len(records) != 1 else ""))

    def refresh(self):
        if self.manager is None:
            return
        while self.queue_lay.count():
            item = self.queue_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        jobs = [j for j in self.manager.jobs()
                if j.state not in (JobState.COMPLETED,)]
        for job in jobs:
            row = JobRow(job, self.theme)
            row.pause_btn.clicked.connect(
                lambda _c, j=job: self.manager.pause(j.id))
            row.resume_btn.clicked.connect(
                lambda _c, j=job: self.manager.resume(j.id))
            row.cancel_btn.clicked.connect(
                lambda _c, j=job: self.manager.cancel(j.id))
            row.retry_btn.clicked.connect(
                lambda _c, j=job: self.manager.retry(j.id))
            self.queue_lay.addWidget(row)
        if not jobs:
            empty = QLabel("No active downloads.")
            empty.setStyleSheet("color: #b2acba; font-size: 13px;")
            self.queue_lay.addWidget(empty)
            self.queue_title.setText("Active")
        else:
            self.queue_title.setText(
                f"Active — {len(jobs)} download"
                + ("s" if len(jobs) != 1 else ""))

    def _note_completed(self, path: str):
        records = self.manager.history() if self.manager else []
        self.set_completed(records)

    def _open_folder(self):
        if self._folder:
            self.open_folder_requested.emit()
