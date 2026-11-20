"""Golden Music v3.1 — persistent download manager.

Features:
  * one-time download folder configuration — the folder is chosen ONCE and
    reused automatically; changeable later in Settings only
  * queue with percentage progress, speed (B/s), downloaded/total size
  * pause + resume (HTTP Range), cancel, retry with bounded backoff
  * duplicate detection (in-flight URL dedup + never silently overwrite:
    "  (n)" suffixing)
  * safe filenames, 150-char cap, metadata kept in job records
  * .part temporary file + size verification + atomic rename into place
  * disk-space floor (default 500 MB free required) — cross-platform
    (shutil.disk_usage; v3.0's os.statvfs crashed on Windows with an
    uncaught AttributeError)
  * persistent job REGISTRY: every job (queued/running/paused/completed/
    failed/cancelled) stays addressable by id until removed/cleared —
    retry() can find failed jobs (v3.0 dropped them from the queue, so
    retry was a silent no-op)
  * lifecycle ownership: the WORKER thread is the only writer of a
    running job's state; the GUI thread only posts requests — active
    cancellation signals the worker (never unlinks a file the worker
    still holds open — v3.0 unlinked the active job's .part, which is a
    PermissionError on Windows and silent corruption elsewhere)
  * restart recovery: queue + history persist atomically to
    downloads_state.json; unfinished jobs are restored QUEUED and resume
    from their .part via HTTP Range when the source honours it (a server
    that ignores Range restarts the transfer cleanly; a .part larger than
    the known total is dropped as corrupt)
  * library integration: on completion the caller gets ``file_added``
    with the final path so the track enters the EXISTING local library,
    Home page and tag cache exactly like a scanned file (no duplicate
    indexing: the path is checked for membership before adding)

Everything network-ish runs in a dedicated QThread; the GUI thread only
receives signals. One download runs at a time (serial queue) — a
deliberate, honest concurrency limit.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
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
MAX_REGISTRY = 50          # terminal jobs kept addressable (bounded memory)
HISTORY_MAX = 200
STATE_VERSION = 1


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

    TERMINAL = (COMPLETED, FAILED, CANCELLED)


class DownloadJob:
    """One download. Registered in the worker's registry for its whole
    lifecycle; state of a RUNNING job is written ONLY by the worker thread
    (GUI-thread requests go through the worker's command methods)."""

    _next_id = 1

    def __init__(self, url: str, title: str, artist: str = "",
                 ext: str = ".mp3", album: str = "",
                 size_hint: int = 0, downloads_allowed: bool | None = None):
        self.id = DownloadJob._next_id
        DownloadJob._next_id += 1
        self.url = url
        self.title = str(title or "unknown")
        self.artist = str(artist or "")
        self.album = str(album or "")
        self.ext = str(ext or ".mp3") or ".mp3"
        self.size_hint = int(size_hint or 0)
        self.downloads_allowed = (None if downloads_allowed is None
                                  else bool(downloads_allowed))
        self.state = JobState.QUEUED
        self.bytes_done = 0
        self.total_bytes = int(size_hint or 0)
        self.speed_bps = 0
        self.error = ""
        self.retries_left = MAX_RETRIES
        self.dest_path = ""          # set by worker when the folder is known
        self.updated_at = time.time()
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

    @staticmethod
    def from_dict(d: dict) -> "DownloadJob | None":
        """Rebuild a job from a persisted record. Returns None when the
        record is unusable (corrupt/quarantined state file entries)."""
        if not isinstance(d, dict):
            return None
        url = d.get("url")
        if not isinstance(url, str) or not url.strip():
            return None
        title = d.get("title")
        if not isinstance(title, str):
            title = "unknown"
        try:
            j = DownloadJob(
                url=url, title=title, artist=str(d.get("artist", "") or ""),
                ext=str(d.get("ext", ".mp3") or ".mp3"),
                album=str(d.get("album", "") or ""),
                size_hint=int(d.get("total_bytes", 0) or 0))
            j.id = int(d.get("id", 0) or 0) or j.id
            try:
                j.bytes_done = max(0, int(d.get("bytes_done", 0) or 0))
            except (TypeError, ValueError):
                j.bytes_done = 0
            j.error = str(d.get("error", "") or "")
            dp = d.get("dest_path")
            j.dest_path = str(dp) if isinstance(dp, str) else ""
            return j
        except (TypeError, ValueError):
            return None

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
    """Free bytes under `path`, cross-platform.

    v3.0 used os.statvfs — a POSIX-only API that does not exist on
    Windows, so downloads on the primary target platform raised an
    uncaught AttributeError. shutil.disk_usage works everywhere.
    """
    try:
        return shutil.disk_usage(str(path)).free
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

class DownloadWorker(QThread):
    job_updated = pyqtSignal(object)          # DownloadJob (snapshot moment)
    file_added = pyqtSignal(str, object)      # final path, DownloadJob
    job_failed = pyqtSignal(object)
    state_changed = pyqtSignal()

    def __init__(self, folder: Path, parent=None):
        super().__init__(parent)
        self.folder = Path(folder)
        self.min_free_bytes = DEFAULT_MIN_FREE_BYTES
        self._queue: list[DownloadJob] = []          # runnable ordering
        self._jobs: dict[int, DownloadJob] = {}      # registry: id -> job
        self._mutex = QMutex()
        self._wake = QWaitCondition()
        self._running = True
        self._current: DownloadJob | None = None

    # ---------------- control (GUI thread) ----------------
    def submit(self, job: DownloadJob):
        self._mutex.lock()
        try:
            self._jobs[job.id] = job
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
        # RUNNING job first — the worker owns the running state machine and
        # performs the transition itself (v3.0 scanned the queue first and
        # could mark the active job PAUSED while the worker kept writing).
        cur = self._current
        if cur is not None and cur.id == job_id:
            self._mutex.lock()
            try:
                cur._pause = True
            finally:
                self._mutex.unlock()
            return True
        self._mutex.lock()
        try:
            for j in self._queue:
                if j.id == job_id and j.state == JobState.QUEUED:
                    j.state = JobState.PAUSED
                    j.updated_at = time.time()
                    self.job_updated.emit(j)
                    return True
        finally:
            self._mutex.unlock()
        return False

    def resume(self, job_id: int) -> bool:
        self._mutex.lock()
        try:
            for j in self._queue:
                if j.id == job_id and j.state == JobState.PAUSED:
                    j.state = JobState.QUEUED
                    j._pause = False
                    j.updated_at = time.time()
                    self._wake.wakeAll()
                    return True
            return False
        finally:
            self._mutex.unlock()

    def cancel(self, job_id: int) -> bool:
        # RUNNING job first: signal the worker only — IT closes the handle
        # and removes the .part (never unlink a file the worker holds open:
        # PermissionError on Windows, silent corruption elsewhere).
        cur = self._current
        if cur is not None and cur.id == job_id and \
                cur.state == JobState.RUNNING:
            self._mutex.lock()
            try:
                cur._cancel = True
            finally:
                self._mutex.unlock()
            return True
        self._mutex.lock()
        try:
            for j in self._queue:
                if j.id == job_id and j.state in (JobState.QUEUED,
                                                  JobState.PAUSED):
                    j.state = JobState.CANCELLED
                    j.updated_at = time.time()
                    part = j.dest_path + ".part" if j.dest_path else ""
                    if part:
                        try:
                            Path(part).unlink(missing_ok=True)
                        except OSError:
                            pass          # read-only dir etc.: state stands
                    self._queue.remove(j)  # stays in the registry
                    self.job_updated.emit(j)
                    self.state_changed.emit()
                    return True
            return False
        finally:
            self._mutex.unlock()

    def retry(self, job_id: int) -> bool:
        # Registry lookup: failed/cancelled jobs are NOT in the runnable
        # queue anymore (v3.0 searched only the queue, so retry could
        # never find them — silent no-op).
        self._mutex.lock()
        try:
            j = self._jobs.get(job_id)
            if j is None or j.state not in (JobState.FAILED,
                                            JobState.CANCELLED):
                return False
            if j.id == getattr(self._current, "id", None):
                return False
            j.state = JobState.QUEUED
            j.error = ""
            j.speed_bps = 0
            j.retries_left = MAX_RETRIES
            j.updated_at = time.time()
            j._cancel = False
            j._pause = False
            if j.dest_path:
                part = j.dest_path + ".part"
                try:
                    if Path(part).exists():
                        j.bytes_done = Path(part).stat().st_size
                except OSError:
                    j.bytes_done = 0
            if j not in self._queue:
                self._queue.append(j)
            self._wake.wakeAll()
            self.job_updated.emit(j)
            self.state_changed.emit()
            return True
        finally:
            self._mutex.unlock()

    def remove(self, job_id: int) -> bool:
        """Remove a TERMINAL job from the registry (history records are
        kept by the manager)."""
        self._mutex.lock()
        try:
            j = self._jobs.get(job_id)
            if j is None or j.state not in JobState.TERMINAL:
                return False
            del self._jobs[job_id]
            try:
                self._queue.remove(j)
            except ValueError:
                pass
            self.state_changed.emit()
            return True
        finally:
            self._mutex.unlock()

    def clear_terminal(self) -> int:
        """Remove all terminal jobs from the registry. Returns the count."""
        self._mutex.lock()
        try:
            doomed = [jid for jid, j in self._jobs.items()
                      if j.state in JobState.TERMINAL]
            for jid in doomed:
                j = self._jobs.pop(jid)
                try:
                    self._queue.remove(j)
                except ValueError:
                    pass
            if doomed:
                self.state_changed.emit()
            return len(doomed)
        finally:
            self._mutex.unlock()

    def job_by_id(self, job_id: int) -> DownloadJob | None:
        self._mutex.lock()
        try:
            return self._jobs.get(job_id)
        finally:
            self._mutex.unlock()

    def stop_and_join(self):
        """Coordinated shutdown: stop accepting work, cancel the active
        transfer so the read loop exits promptly, then join bounded."""
        self._mutex.lock()
        try:
            self._running = False
            cur = self._current
            if cur is not None:
                cur._cancel = True      # read loop checks before each chunk
            self._wake.wakeAll()
        finally:
            self._mutex.unlock()
        self.wait(10000)               # bounded: socket read timeout is 20 s
        if self.isRunning():
            log.critical("download worker did not exit within 10 s")

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
        """Execute one job to a terminal state (or PAUSED). Returns True
        when the job stopped running (any state but RUNNING)."""
        if job.state != JobState.QUEUED:
            return True
        self._mutex.lock()
        try:
            job.state = JobState.RUNNING
            job.updated_at = time.time()
            self._current = job
        finally:
            self._mutex.unlock()
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
            # corrupt-partial guard: a .part larger than a known total can
            # only be stale/corrupt — drop it and restart cleanly
            try:
                if job.total_bytes and part.exists() and \
                        part.stat().st_size > job.total_bytes:
                    part.unlink()
                    job.bytes_done = 0
            except OSError:
                pass
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
            job.updated_at = time.time()
            self.job_updated.emit(job)
            self.file_added.emit(str(dest), job)
        except Exception as e:
            job.error = str(e)
            if job._cancel:
                job.state = JobState.CANCELLED
                job.updated_at = time.time()
                self.job_updated.emit(job)
            elif job.retries_left > 0 and not job._pause:
                job.retries_left -= 1
                time.sleep(RETRY_BACKOFF_S)
                job.state = JobState.QUEUED        # retry via queue
                job.updated_at = time.time()
                self.job_updated.emit(job)
                return False
            else:
                job.state = JobState.FAILED
                job.updated_at = time.time()
                self.job_failed.emit(job)
                self.job_updated.emit(job)
        finally:
            if job.state in JobState.TERMINAL:
                self._drop_from_queue(job)
                self._bound_registry()
            if self._current is job:
                self._current = None
        return job.state != JobState.RUNNING

    def _drop_from_queue(self, job: DownloadJob):
        self._mutex.lock()
        try:
            try:
                self._queue.remove(job)
            except ValueError:
                pass
        finally:
            self._mutex.unlock()
        self.state_changed.emit()

    def _bound_registry(self):
        self._mutex.lock()
        try:
            terminal = [j for j in self._jobs.values()
                        if j.state in JobState.TERMINAL]
            excess = len(terminal) - MAX_REGISTRY
            for j in terminal[:max(0, excess)]:
                self._jobs.pop(j.id, None)
        finally:
            self._mutex.unlock()

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
                with online_mod.urlopen(req, timeout=20) as r:  # nosec B310 (scheme audited above)
                    if resume_from and r.status not in (HTTP_PARTIAL, HTTP_OK):
                        raise RuntimeError("server ignored resume range")
                    total = r.headers.get("Content-Length")
                    if resume_from and r.status == HTTP_OK:
                        # server ignored the range: restart the transfer
                        # cleanly instead of appending (content could have
                        # changed, the part could be incompatible)
                        total = None
                        resume_from = 0
                        job.bytes_done = 0
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
                                fh.close()
                                part.unlink(missing_ok=True)
                                job.bytes_done = 0
                                raise _Cancelled()
                            if job._pause:
                                fh.close()
                                job.state = JobState.PAUSED
                                job.updated_at = time.time()
                                # the .part contains ALL bytes so far (resumed
                                # prefix + this session's writes)
                                job.bytes_done = part.stat().st_size
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
                job.bytes_done = 0
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
    thread. ``file_added`` is the library-integration hook; ``state_file``
    gives restart recovery + history persistence."""

    job_updated = pyqtSignal(object)
    file_added = pyqtSignal(str, object)
    job_failed = pyqtSignal(object)
    queue_changed = pyqtSignal()

    def __init__(self, folder: Path, min_free_bytes: int =
                 DEFAULT_MIN_FREE_BYTES, parent=None,
                 state_file: Path | None = None,
                 history: list | None = None):
        super().__init__(parent)
        self.worker = DownloadWorker(Path(folder))
        self.worker.min_free_bytes = int(min_free_bytes)
        self.worker.job_updated.connect(self.job_updated)
        self.worker.file_added.connect(self._on_file_added)
        self.worker.job_failed.connect(self.job_failed)
        self.worker.state_changed.connect(self.queue_changed)
        self.worker.state_changed.connect(self._persist_async)
        self.worker.job_failed.connect(lambda _j: self._persist_async())
        self._history: list[dict] = []      # completed records (persisted)
        self._state_file = (Path(state_file) if state_file is not None
                            else None)
        self._save_scheduled = False
        restored_pending = self._load_state()
        if not self._history and history:
            # migration: v3.0.0 kept completed records in config.json only
            self.load_history(history)
        self.worker.start()
        for job in restored_pending:
            # PAUSED jobs keep their .part; the worker scans for QUEUED
            # jobs only, so submitting a PAUSED job parks it correctly
            self.worker.submit(job)
        if restored_pending:
            self.queue_changed.emit()

    # ---------------- state persistence (atomic, schema-validated) ------
    def _load_state(self) -> list:
        """Read downloads_state.json: validate schema, restore unfinished
        jobs (QUEUED/PAUSED) and the history. Corrupt entries are
        quarantined (skipped), never fatal."""
        pending: list = []
        if self._state_file is None:
            return pending
        try:
            raw = self._state_file.read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else {}
        except (OSError, ValueError) as e:
            log.warning(f"downloads state unreadable (ignored): {e}")
            return pending
        if not isinstance(data, dict) or \
                int(data.get("version", 0) or 0) != STATE_VERSION:
            log.warning("downloads state: unknown version — ignored")
            return pending
        raw_hist = data.get("history")
        if isinstance(raw_hist, list):
            self._history = [r for r in raw_hist if isinstance(r, dict)
                             ][:HISTORY_MAX]
        raw_pending = data.get("pending")
        if isinstance(raw_pending, list):
            for d in raw_pending:
                job = DownloadJob.from_dict(d)
                if job is None:
                    continue        # quarantined corrupt entry
                state = str(d.get("state", "") or "")
                # a job that was RUNNING at save time resumes as QUEUED
                # (Range resume from its .part when the server honours it)
                job.state = (JobState.PAUSED if state == JobState.PAUSED
                             else JobState.QUEUED)
                if job.id >= DownloadJob._next_id:
                    DownloadJob._next_id = job.id + 1
                pending.append(job)
        log.info(f"downloads state restored: {len(pending)} pending, "
                 f"{len(self._history)} history records")
        return pending

    def serialise_state(self) -> dict:
        pending = []
        for j in list(self.worker._jobs.values()):
            if j.state in (JobState.QUEUED, JobState.PAUSED,
                           JobState.RUNNING):
                pending.append(j.as_dict())
        return {"version": STATE_VERSION,
                "history": list(self._history)[:HISTORY_MAX],
                "pending": pending}

    def save_state(self):
        """Atomic write (tmp file + os.replace) — no partial JSON on
        crash. Called on every state transition (debounced) + shutdown."""
        if self._state_file is None:
            return
        try:
            tmp = self._state_file.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.serialise_state(), indent=1),
                           encoding="utf-8")
            os.replace(str(tmp), str(self._state_file))
        except OSError as e:
            log.warning(f"downloads state save failed: {e}")
        finally:
            self._save_scheduled = False

    def _persist_async(self):
        """Debounce persistence off the hot path (progress ticks never
        trigger saves — only real transitions do)."""
        if self._save_scheduled or self._state_file is None:
            return
        self._save_scheduled = True
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(400, self.save_state)

    # ---------------- API ----------------
    def set_folder(self, folder: Path):
        self.worker.set_folder(Path(folder))

    def folder(self) -> Path:
        return self.worker.folder

    def download_track(self, track) -> DownloadJob:
        """Queue an OnlineTrack download (refused when the source forbids
        downloads). Eligibility is item-specific — established by the
        provider at resolve time (online.py), never assumed."""
        if getattr(track, "downloads_allowed", None) is False:
            raise PermissionError(
                f"{getattr(track, 'provider', 'source')} does not permit "
                "downloads of this item")
        job = DownloadJob(
            url=track.stream_url, title=track.title, artist=track.artist,
            ext=getattr(track, "ext", "") or ".mp3",
            album=getattr(track, "album", ""),
            size_hint=getattr(track, "size_bytes", 0) or 0,
            downloads_allowed=getattr(track, "downloads_allowed", None))
        return self._submit_unique(job)

    def download_url(self, url: str, title: str, artist: str = "",
                     ext: str = ".mp3", size_hint: int = 0) -> DownloadJob:
        job = DownloadJob(url=url, title=title, artist=artist, ext=ext,
                         size_hint=size_hint)
        return self._submit_unique(job)

    def _submit_unique(self, job: DownloadJob) -> DownloadJob:
        """Duplicate prevention: one in-flight job per URL."""
        for j in list(self.worker._jobs.values()):
            if j.url == job.url and j.state in (JobState.QUEUED,
                                                JobState.RUNNING,
                                                JobState.PAUSED):
                raise ValueError("already in the download queue")
        self.worker.submit(job)
        self.queue_changed.emit()
        return job

    def pause(self, job_id: int) -> bool:
        ok = self.worker.pause(job_id)
        if ok:
            self.queue_changed.emit()
        return ok

    def resume(self, job_id: int) -> bool:
        ok = self.worker.resume(job_id)
        if ok:
            self.queue_changed.emit()
        return ok

    def cancel(self, job_id: int) -> bool:
        ok = self.worker.cancel(job_id)
        if ok:
            self.queue_changed.emit()
        return ok

    def retry(self, job_id: int) -> bool:
        ok = self.worker.retry(job_id)
        if ok:
            self.queue_changed.emit()
        return ok

    def remove(self, job_id: int) -> bool:
        ok = self.worker.remove(job_id)
        if ok:
            self.queue_changed.emit()
        return ok

    def clear_completed(self) -> int:
        n = self.worker.clear_terminal()
        if n:
            self.queue_changed.emit()
        return n

    def jobs(self) -> list:
        """Registry snapshot in submission order (includes terminal jobs
        until removed/cleared)."""
        return list(self.worker._jobs.values())

    def record_completed(self, job: DownloadJob):
        rec = job.as_dict()
        rec["completed_at"] = time.time()
        self._history.insert(0, rec)
        self._history = self._history[:HISTORY_MAX]
        self.queue_changed.emit()
        self._persist_async()

    def _on_file_added(self, path, job):
        self.record_completed(job)
        # let the real file_added signal through unchanged
        self.file_added.emit(path, job)

    def history(self) -> list:
        return list(self._history)

    def load_history(self, raw):
        """Adopt persisted history (validated; older list shapes are
        migrated, garbage entries are skipped)."""
        if isinstance(raw, list):
            self._history = [r for r in raw if isinstance(r, dict)
                            ][:HISTORY_MAX]
            self._persist_async()

    def stop(self):
        """Coordinated shutdown: persist final state, then join the worker
        with its active transfer cancelled."""
        self.save_state()
        self.worker.stop_and_join()
        self.save_state()
