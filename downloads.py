"""Golden Music v3 — persistent download manager.

Features (PART 6 of the product spec):
  * one-time download folder configuration — the folder is chosen ONCE and
    reused automatically; changeable later in Settings only
  * queue with percentage progress, speed (B/s), downloaded/total size
  * pause + resume (HTTP Range), cancel, retry with bounded backoff
  * duplicate detection (never silently overwrite: "  (n)" suffixing)
  * safe filenames, 150-char cap, metadata kept in job records
  * .part temporary file + size verification + atomic rename into place
  * disk-space floor (default 500 MB free required)
  * library integration: on completion the caller gets ``file_added`` with
    the final path so the track enters the EXISTING local library, Home
    page and tag cache exactly like a scanned file (no duplicate indexing:
    the path is checked for membership before adding)

Everything network-ish runs in a dedicated QThread; the GUI thread only
receives signals. Resume survives app restarts: the queue (with .part
byte counts) is persisted by the caller through ``serialise_state()``.
"""
from __future__ import annotations

import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from PyQt6.QtCore import QObject, QThread, pyqtSignal, QMutex, QWaitCondition

import online as online_mod

log = logging.getLogger("goldenmusic.downloads")

CHUNK = 64 * 1024
MAX_RETRIES = 3
RETRY_BACKOFF_S = 0.8
DEFAULT_MIN_FREE_BYTES = 500 * 1024 * 1024     # 500 MB floor
MAX_FILENAME_LEN = 150
HTTP_OK = 200
HTTP_PARTIAL = 206


# ---------------------------------------------------------------------------
# Job model
# ---------------------------------------------------------------------------

class JobState:
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DownloadJob:
    """One download. Owned by the manager; state mutated only by the worker
    thread + GUI-thread pause/cancel requests (mutex-protected)."""

    _next_id = 1

    def __init__(self, url: str, title: str, artist: str = "",
                 ext: str = ".mp3", album: str = "",
                 size_hint: int = 0, downloads_allowed: bool = True):
        self.id = DownloadJob._next_id
        DownloadJob._next_id += 1
        self.url = url
        self.title = str(title or "unknown")
        self.artist = str(artist or "")
        self.album = str(album or "")
        self.ext = str(ext or ".mp3") or ".mp3"
        self.size_hint = int(size_hint or 0)
        self.downloads_allowed = bool(downloads_allowed)
        self.state = JobState.QUEUED
        self.bytes_done = 0
        self.total_bytes = int(size_hint or 0)
        self.speed_bps = 0
        self.error = ""
        self.retries_left = MAX_RETRIES
        self.dest_path = ""          # set by manager when the folder is known
        self._pause = False
        self._cancel = False
        self._resume_from = 0        # persisted for restart-resume

    # ---------------- naming ----------------
    def base_filename(self) -> str:
        safe = sanitize_filename(
            f"{self.artist} - {self.title}" if self.artist else self.title)
        if not safe:
            safe = "download"
        if not safe.lower().endswith(self.ext.lower()):
            safe = safe[: MAX_FILENAME_LEN - len(self.ext)] + self.ext
        return safe[:MAX_FILENAME_LEN]

    def as_dict(self) -> dict:
        return {
            "id": self.id, "url": self.url, "title": self.title,
            "artist": self.artist, "album": self.album, "ext": self.ext,
            "state": self.state, "bytes_done": self.bytes_done,
            "total_bytes": self.total_bytes, "error": self.error,
            "retries_left": self.retries_left,
            "dest_path": self.dest_path,
        }

    def progress_pct(self) -> int:
        if self.total_bytes > 0:
            return min(100, round(self.bytes_done * 100 / self.total_bytes))
        return 0 if self.state != JobState.COMPLETED else 100


_SANITIZE_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(name: str) -> str:
    """Windows-safe filename without inventing content: strip illegal
    characters + trailing dots/spaces, collapse whitespace, cap length."""
    name = _SANITIZE_RE.sub(" ", str(name or ""))
    name = re.sub(r"\s+", " ", name).strip().rstrip(". ")
    return name[:MAX_FILENAME_LEN]


def unique_destination(folder: Path, filename: str) -> Path:
    """folder/filename, never overwriting: ' (n)' before the extension."""
    stem, dot, ext = filename.rpartition(".")
    if not dot:
        stem, ext = filename, ""
    else:
        ext = "." + ext
    candidate = folder / filename
    n = 1
    while candidate.exists():
        candidate = folder / f"{stem} ({n}){ext}"
        n += 1
    return candidate


def free_bytes(path: Path) -> int | None:
    try:
        st = os.statvfs(str(path))
        return st.f_bavail * st.f_frsize
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

class _JobRequest:
    """Control commands the GUI thread posts to the worker."""

    def __init__(self, kind, job=None):
        self.kind = kind            # 'start' | 'pause' | 'resume' | 'cancel' | 'retry'
        self.job = job


class DownloadWorker(QThread):
    job_updated = pyqtSignal(object)          # DownloadJob (copy semantics: same obj)
    file_added = pyqtSignal(str, object)      # final path, DownloadJob
    job_failed = pyqtSignal(object)
    state_changed = pyqtSignal()

    def __init__(self, folder: Path, parent=None):
        super().__init__(parent)
        self.folder = Path(folder)
        self.min_free_bytes = DEFAULT_MIN_FREE_BYTES
        self._queue: list[DownloadJob] = []
        self._mutex = QMutex()
        self._wake = QWaitCondition()
        self._running = True
        self._current: DownloadJob | None = None

    # ---------------- control (GUI thread) ----------------
    def submit(self, job: DownloadJob):
        self._mutex.lock()
        try:
            self._queue.append(job)
            self._wake.wakeAll()
        finally:
            self._mutex.unlock()

    def set_folder(self, folder: Path):
        self._mutex.lock()
        try:
            self.folder = Path(folder)
        finally:
            self._mutex.unlock()

    def pause(self, job_id: int) -> bool:
        self._mutex.lock()
        try:
            for j in self._queue:
                if j.id == job_id and j.state == JobState.QUEUED:
                    j.state = JobState.PAUSED
                    self.job_updated.emit(j)
                    return True
        finally:
            self._mutex.unlock()
        cur = self._current
        if cur is not None and cur.id == job_id:
            cur._pause = True
            return True
        return False

    def resume(self, job_id: int) -> bool:
        self._mutex.lock()
        try:
            for j in self._queue:
                if j.id == job_id and j.state == JobState.PAUSED:
                    j.state = JobState.QUEUED
                    j._pause = False
                    self._wake.wakeAll()
                    return True
        finally:
            self._mutex.unlock()
        return False

    def cancel(self, job_id: int) -> bool:
        self._mutex.lock()
        try:
            for j in self._queue:
                if j.id == job_id:
                    j.state = JobState.CANCELLED
                    if j.dest_path:
                        Path(j.dest_path + ".part").unlink(missing_ok=True)
                    self.job_updated.emit(j)
                    return True
        finally:
            self._mutex.unlock()
        cur = self._current
        if cur is not None and cur.id == job_id:
            cur._cancel = True
            return True
        return False

    def retry(self, job_id: int) -> bool:
        self._mutex.lock()
        try:
            for j in self._queue:
                if j.id == job_id and j.state in (JobState.FAILED,
                                                  JobState.CANCELLED):
                    j.state = JobState.QUEUED
                    j.retries_left = MAX_RETRIES
                    j.error = ""
                    self._wake.wakeAll()
                    return True
        finally:
            self._mutex.unlock()
        return False

    def stop_and_join(self):
        self._mutex.lock()
        try:
            self._running = False
            self._wake.wakeAll()
        finally:
            self._mutex.unlock()
        self.wait(4000)

    # ---------------- thread body ----------------
    def run(self):
        while True:
            job = None
            self._mutex.lock()
            try:
                while self._running and not self._queue:
                    self._wake.wait(self._mutex, 500)
                if not self._running:
                    return
                for j in self._queue:
                    if j.state == JobState.QUEUED:
                        job = j
                        break
            finally:
                self._mutex.unlock()
            if job is None:
                time.sleep(0.05)
                continue
            if not self._run_job(job):
                continue

    def _run_job(self, job: DownloadJob) -> bool:
        """Execute one job to a terminal state. Returns True when it left
        the queue (completed/failed/cancelled)."""
        if job.state != JobState.QUEUED:
            self._drop(job)
            return True
        job.state = JobState.RUNNING
        # Track the in-flight job so pause()/cancel() (GUI thread) can reach
        # it and jobs() keeps the RUNNING row visible — `_current` was read
        # by those paths but never assigned, so pausing/cancelling a RUNNING
        # download used to be a silent no-op.
        self._current = job
        self.job_updated.emit(job)
        try:
            folder = self.folder
            folder.mkdir(parents=True, exist_ok=True)
            free = free_bytes(folder)
            if free is not None and free < self.min_free_bytes:
                raise RuntimeError(
                    f"not enough disk space (need {self.min_free_bytes // (1024*1024)} MB free)")
            dest = unique_destination(folder, job.base_filename())
            job.dest_path = str(dest)
            part = Path(str(dest) + ".part")
            job._resume_from = part.stat().st_size if part.exists() else 0
            completed = self._download_to_part(job, part)
            if not completed:
                # paused mid-flight: keep the .part for resume; job stays in
                # the queue as PAUSED
                return False
            # verify + atomic rename
            if job.total_bytes and part.stat().st_size != job.total_bytes:
                raise RuntimeError(
                    f"size mismatch: got {part.stat().st_size} of "
                    f"{job.total_bytes}")
            os.replace(str(part), str(dest))
            job.state = JobState.COMPLETED
            job.bytes_done = job.total_bytes or dest.stat().st_size
            self.job_updated.emit(job)
            self.file_added.emit(str(dest), job)
        except Exception as e:
            job.error = str(e)
            if job._cancel:
                job.state = JobState.CANCELLED
                self.job_updated.emit(job)
            elif job.retries_left > 0 and not job._pause:
                job.retries_left -= 1
                time.sleep(RETRY_BACKOFF_S)
                job.state = JobState.QUEUED        # retry via queue
                self.job_updated.emit(job)
                return False
            else:
                job.state = JobState.FAILED
                self.job_failed.emit(job)
                self.job_updated.emit(job)
        finally:
            if job.state in (JobState.COMPLETED, JobState.FAILED,
                            JobState.CANCELLED):
                self._drop(job)
            if self._current is job:
                self._current = None
        return job.state != JobState.QUEUED

    def _drop(self, job: DownloadJob):
        self._mutex.lock()
        try:
            try:
                self._queue.remove(job)
            except ValueError:
                pass
        finally:
            self._mutex.unlock()
        self.state_changed.emit()

    # ---------------- actual HTTP ----------------
    def _download_to_part(self, job: DownloadJob, part: Path):
        url = job.url
        # https in production; plain http only for loopback (tests/tools)
        loopback = (url.startswith("http://127.0.0.1")
                    or url.startswith("http://localhost"))
        if not (online_mod.valid_stream_url(url) or loopback
                or url.startswith("file:")):
            raise ValueError("unsupported download URL (https/file only)")
        headers = {"User-Agent": online_mod.InternetArchiveProvider.UA}
        resume_from = part.stat().st_size if part.exists() else 0
        if resume_from:
            headers["Range"] = f"bytes={resume_from}-"
        req = urllib.request.Request(url, headers=headers)   # noqa: S310
        last_err = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310
                    if resume_from and r.status not in (HTTP_PARTIAL, HTTP_OK):
                        raise RuntimeError("server ignored resume range")
                    total = r.headers.get("Content-Length")
                    if resume_from and r.status == HTTP_OK:
                        total = None               # full body: restart file
                        resume_from = 0
                    if total and not resume_from:
                        job.total_bytes = int(total)
                    job.bytes_done = resume_from
                    job._resume_from = resume_from
                    window_start = time.time()
                    window_bytes = 0
                    mode = "wb" if not resume_from else "ab"
                    with open(part, mode) as fh:
                        while True:
                            if job._cancel:
                                raise _Cancelled()
                            if job._pause:
                                job.state = JobState.PAUSED
                                job.bytes_done = resume_from + fh.tell()
                                self.job_updated.emit(job)
                                return False        # paused: keep .part
                            chunk = r.read(CHUNK)
                            if not chunk:
                                break
                            fh.write(chunk)
                            window_bytes += len(chunk)
                            job.bytes_done = resume_from + fh.tell()
                            now = time.time()
                            if now - window_start >= 0.5:
                                job.speed_bps = window_bytes / (now - window_start)
                                window_start, window_bytes = now, 0
                                self.job_updated.emit(job)
                    job.speed_bps = 0
                if job.total_bytes and job.bytes_done < job.total_bytes:
                    raise RuntimeError("truncated download")
                return True
            except _Cancelled:
                part.unlink(missing_ok=True)
                raise
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_err = e
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_BACKOFF_S * (attempt + 1))
        raise RuntimeError(f"download failed after retries: {last_err}")


class _Cancelled(Exception):
    pass


# ---------------------------------------------------------------------------
# Manager (GUI thread facade)
# ---------------------------------------------------------------------------

class DownloadManager(QObject):
    """GUI-facing API. Lives on the GUI thread; I/O happens on the worker
    thread. ``file_added`` is the library-integration hook."""

    job_updated = pyqtSignal(object)
    file_added = pyqtSignal(str, object)
    job_failed = pyqtSignal(object)
    queue_changed = pyqtSignal()

    def __init__(self, folder: Path, min_free_bytes: int =
                 DEFAULT_MIN_FREE_BYTES, parent=None):
        super().__init__(parent)
        self.worker = DownloadWorker(Path(folder))
        self.worker.min_free_bytes = int(min_free_bytes)
        self.worker.job_updated.connect(self.job_updated)
        self.worker.file_added.connect(self.file_added)
        self.worker.job_failed.connect(self.job_failed)
        self.worker.state_changed.connect(self.queue_changed)
        self.worker.start()
        self._history: list[dict] = []      # completed records (persisted)

    # ---------------- API ----------------
    def set_folder(self, folder: Path):
        self.worker.set_folder(Path(folder))

    def folder(self) -> Path:
        return self.worker.folder

    def download_track(self, track) -> DownloadJob:
        """Queue an OnlineTrack download (refused when the source forbids
        downloads)."""
        if not getattr(track, "downloads_allowed", True):
            raise PermissionError(
                f"{getattr(track, 'provider', 'source')} does not permit downloads")
        job = DownloadJob(
            url=track.stream_url, title=track.title, artist=track.artist,
            ext=getattr(track, "ext", "") or ".mp3",
            album=getattr(track, "album", ""),
            size_hint=getattr(track, "size_bytes", 0) or 0,
            downloads_allowed=True)
        self.worker.submit(job)
        self.queue_changed.emit()
        return job

    def download_url(self, url: str, title: str, artist: str = "",
                     ext: str = ".mp3", size_hint: int = 0) -> DownloadJob:
        job = DownloadJob(url=url, title=title, artist=artist, ext=ext,
                          size_hint=size_hint)
        self.worker.submit(job)
        self.queue_changed.emit()
        return job

    def pause(self, job_id: int) -> bool:
        ok = self.worker.pause(job_id)
        self.queue_changed.emit()
        return ok

    def resume(self, job_id: int) -> bool:
        ok = self.worker.resume(job_id)
        self.queue_changed.emit()
        return ok

    def cancel(self, job_id: int) -> bool:
        ok = self.worker.cancel(job_id)
        self.queue_changed.emit()
        return ok

    def retry(self, job_id: int) -> bool:
        ok = self.worker.retry(job_id)
        self.queue_changed.emit()
        return ok

    def jobs(self) -> list:
        # A RUNNING job stays in the queue until it reaches a terminal
        # state, so `_current` (now actually tracked) must be de-duplicated
        # against the queue — otherwise the Downloads page renders the same
        # job as two rows.
        q = list(self.worker._queue)
        cur = self.worker._current
        if cur is not None and all(j is not cur for j in q):
            q.append(cur)
        return q

    def record_completed(self, job: DownloadJob):
        rec = job.as_dict()
        rec["completed_at"] = time.time()
        self._history.insert(0, rec)
        self._history = self._history[:200]
        self.queue_changed.emit()

    def history(self) -> list:
        return list(self._history)

    def load_history(self, raw):
        if isinstance(raw, list):
            self._history = [r for r in raw if isinstance(r, dict)][:200]

    def stop(self):
        self.worker.stop_and_join()
