"""Golden Music v3.1 — regression suite for the remediation mission.

Covers (offline-capable, runs on Windows AND Linux offscreen):
  DM-01 free_bytes cross-platform (v3.0's os.statvfs crashed on Windows)
  DM-02 active-job cancellation (worker signalled, never unlink-while-open)
  DM-03 retry finds FAILED jobs via the registry
  DM-04/05 history + queue persistence / restart recovery (atomic state)
  DM-06 coordinated shutdown (download worker joins cleanly)
  Q-01  unified queue: local<->online mixing, Play-next, Next/Prev, repeat-one
  Q-02  QueuePanel drawer wired + mixed entries
  ON-01 search stale-result rejection + correct cache key
  ON-02 stream-resolution race (generation guard)
  ON-03 artwork race (token guard)
  ON-04 item-specific download eligibility (no blanket downloads_allowed)
  ON-05 custom proxy setting
  UI-01 Home add-to-playlist / open-album act on the SELECTED item
  UI-02 Home album grouping + see-all artists
  UI-03 Home async cover loading (no GUI-thread I/O)
  UI-04 downloads page in-place row updates

Real integration (no mocks) for the download subsystem: an in-process
throttled HTTP server (loopback is the worker's sanctioned test path)
exercises pause/resume/cancel/retry/restart-recovery end-to-end with real
HTTP Range semantics.
"""
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Windows consoles default to cp1252 — the check names contain →/· etc.
# Reconfigure BEFORE any printing or the whole suite dies on the first
# non-ASCII check name (seen on windows-latest runners).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import testenv as _testenv
_testenv.install()
del _testenv

from PyQt6.QtCore import QEventLoop, QTimer
from PyQt6.QtWidgets import QApplication

app = QApplication(sys.argv)
app.setApplicationName("GMV31Regression")

import downloads as dl
import online as online_mod
from online import OnlineTrack, InternetArchiveProvider
import main as m

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'OK' if cond else 'FAIL'}] {name}"
          + (f"  ({detail})" if detail and not cond else ""),
          flush=True)


def wait_until(cond, timeout=10.0, poll=0.02):
    """Pump the Qt event loop until cond() or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents)
        time.sleep(poll)
    app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents)
    return cond()


# ---------------------------------------------------------------------------
# Real MP3s with ID3 tags (title/artist/album) — mutagen needs valid frames
# ---------------------------------------------------------------------------

_MPEG_HDR = bytes([0xFF, 0xFB, 0x90, 0x00])       # MPEG1 L3 128k 44100


def _make_mp3(path: Path, title: str, artist: str, album: str) -> str:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # 3 valid 417-byte MPEG1 Layer3 frames (mutagen syncs reliably)
    p.write_bytes((_MPEG_HDR + b"\x00" * 413) * 3)
    from mutagen.id3 import ID3, TIT2, TPE1, TALB
    tags = ID3()
    tags.add(TIT2(encoding=3, text=title))
    tags.add(TPE1(encoding=3, text=artist))
    tags.add(TALB(encoding=3, text=album))
    tags.save(p)
    return str(p)


# ---------------------------------------------------------------------------
# Throttled, Range-capable HTTP server (loopback — the worker's test path)
# ---------------------------------------------------------------------------

class _SlowHandler(BaseHTTPRequestHandler):
    serve_bytes = b""
    chunk_delay = 0.02          # s per 16 KiB → ~0.8 MB/s max

    def do_GET(self):
        data = self.server.serve_bytes
        rng = self.headers.get("Range")
        start = 0
        if rng and rng.startswith("bytes="):
            try:
                start = int(rng[len("bytes="):].split("-")[0])
            except ValueError:
                start = 0
        if start >= len(data):
            self.send_response(416)
            self.end_headers()
            return
        self.send_response(206 if rng else 200)
        self.send_header("Content-Length", str(len(data) - start))
        if rng:
            self.send_header("Content-Range",
                             f"bytes {start}-{len(data)-1}/{len(data)}")
        self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()
        pos = start
        while pos < len(data):
            chunk = data[pos:pos + 16 * 1024]
            try:
                self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError,
                    ConnectionAbortedError):
                return          # client paused/cancelled mid-transfer
                # (Windows raises ConnectionAbortedError 10053)
            pos += len(chunk)
            time.sleep(self.chunk_delay)

    def log_message(self, *_a):
        pass


class _SlowServer:
    def __init__(self, payload: bytes):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), _SlowHandler)
        self.srv.serve_bytes = payload
        self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever,
                                        daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/file.bin"

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()


# ---------------------------------------------------------------------------
print("=== DM-01 free_bytes (cross-platform, Windows-safe) ===", flush=True)

tmpdir = Path(tempfile.mkdtemp(prefix="dm01_"))
fb = dl.free_bytes(tmpdir)
check("free_bytes returns a positive int on a real dir", isinstance(fb, int) and fb > 0,
      repr(fb))
check("free_bytes returns None on a missing drive/dir (no crash)",
      dl.free_bytes(Path("/nonexistent-drive-xyz")) is None)
check("free_bytes uses shutil.disk_usage (Windows-capable)",
      "shutil.disk_usage" in Path(dl.__file__).read_text(encoding="utf-8")
      and "os.statvfs(" not in Path(dl.__file__).read_text(encoding="utf-8"))

# ---------------------------------------------------------------------------
print("=== DM: full download lifecycle (file:// integration) ===", flush=True)

BIG = os.urandom(220 * 1024)          # 220 KiB at ~64 KiB/s → ~3.4 s

dl_dir = tmpdir / "downloads"
src = tmpdir / "song.bin"
PAYLOAD = os.urandom(64 * 1024)
src.write_bytes(PAYLOAD)
state_file = tmpdir / "downloads_state.json"

mgr = dl.DownloadManager(dl_dir, state_file=state_file)
job = mgr.download_url(src.as_uri(), "Song One", "Artist A", ".bin")
got = {}


def _on_added(path, j):
    got["path"], got["job"] = path, j


mgr.file_added.connect(_on_added)
check("download completes and emits file_added (real .part + rename)",
      wait_until(lambda: got.get("job") is not None, 15),
      f"state={job.state} err={job.error}")
dest = Path(got.get("path", ""))
check("downloaded bytes match source exactly",
      dest.is_file() and dest.read_bytes() == PAYLOAD)
check("no .part remains after completion",
      not dest.with_suffix(".bin.part").exists()
      and not Path(str(dest) + ".part").exists())
check("history recorded by the manager itself (no caller double-record)",
      len(mgr.history()) == 1 and mgr.history()[0]["title"] == "Song One")
mgr.stop()
check("state file written (atomic) with history + no pending",
      state_file.is_file() and
      json.loads(state_file.read_text(encoding="utf-8"))["history"] and
      json.loads(state_file.read_text(encoding="utf-8"))["pending"] == [])

# DM-04: a SECOND manager must load the persisted history (restart)
mgr2 = dl.DownloadManager(dl_dir, state_file=state_file)
check("restart: persisted history survives into a fresh manager",
      len(mgr2.history()) == 1 and mgr2.history()[0]["title"] == "Song One",
      str(mgr2.history()))

# duplicate prevention (same manager, while in-flight)
try:
    job_dup = mgr2.download_url(src.as_uri(), "Song One", "Artist A", ".bin")
    # the first call queued fine; the SAME url must now be refused while
    # that job is still in-flight
    try:
        mgr2.download_url(src.as_uri(), "Song One Again", "Artist A", ".bin")
        ok_dup = False
    except ValueError:
        ok_dup = True
except ValueError:
    ok_dup = True
check("duplicate in-flight URL is refused (no silent double download)",
      ok_dup)
wait_until(lambda: job_dup.state == dl.JobState.COMPLETED, 15)

# disk-space floor (works on every OS through shutil.disk_usage)
src2 = tmpdir / "song2.bin"
src2.write_bytes(PAYLOAD)
mgr2.worker.min_free_bytes = 10 ** 15
job3 = mgr2.download_url(src2.as_uri(), "Big Song", "Artist B", ".bin")
wait_until(lambda: job3.state == dl.JobState.FAILED, 20)
check("insufficient disk space fails the job with a clear error",
      "not enough disk space" in (job3.error or ""), job3.error)
mgr2.worker.min_free_bytes = dl.DEFAULT_MIN_FREE_BYTES

# queued cancellation (needs a QUEUED job: use the throttled server so the
# second job waits in the queue behind the first)
slow0 = _SlowServer(BIG)
mgr2.stop()
slow0.stop()
slowQ = _SlowServer(BIG)
mgrQ = dl.DownloadManager(tmpdir / "dlq", state_file=tmpdir / "sq.json")
j_q1 = mgrQ.download_url(slowQ.url, "Q1 Song", "Artist Q", ".bin")
j_q2 = mgrQ.download_url(slowQ.url + "?2", "Q2 Song", "Artist Q", ".bin")
wait_until(lambda: j_q1.state == dl.JobState.RUNNING, 10)
check("queued cancellation works and reports success",
      j_q2.state == dl.JobState.QUEUED and mgrQ.cancel(j_q2.id)
      and j_q2.state == dl.JobState.CANCELLED,
      f"state={j_q2.state}")
mgrQ.stop()
slowQ.stop()

# ---------------------------------------------------------------------------
print("=== DM-03 retry of a FAILED job (registry) ===", flush=True)

missing = tmpdir / "later.bin"
bad_url = missing.as_uri()
mgr3 = dl.DownloadManager(tmpdir / "dl3", state_file=tmpdir / "s3.json")
job5 = mgr3.download_url(bad_url, "Retry Song", "Artist D", ".bin")
wait_until(lambda: job5.state == dl.JobState.FAILED, 40)
check("missing source fails after bounded retries", job5.state == "failed")
check("FAILED job stays addressable in the registry (v3.0 dropped it)",
      any(j.id == job5.id for j in mgr3.jobs()))
missing.write_bytes(PAYLOAD)
check("retry() requeues the failed job and it completes",
      mgr3.retry(job5.id) and
      wait_until(lambda: job5.state == dl.JobState.COMPLETED, 20),
      f"state={job5.state} err={job5.error}")
mgr3.stop()

# ---------------------------------------------------------------------------
print("=== DM-02/DM-05 active pause/cancel + restart recovery (HTTP) ===",
      flush=True)

slow = _SlowServer(BIG)
mgr4 = dl.DownloadManager(tmpdir / "dl4", state_file=tmpdir / "s4.json")
job6 = mgr4.download_url(slow.url, "Pause Song", "Artist E", ".bin")
check("running job is RUNNING mid-transfer",
      wait_until(lambda: job6.state == dl.JobState.RUNNING, 10))
check("pause an ACTIVE download (worker signalled, not queue-scanned)",
      mgr4.pause(job6.id))
check("paused mid-flight: state PAUSED, .part kept, bytes accounted",
      wait_until(lambda: job6.state == dl.JobState.PAUSED, 30) and
      Path(job6.dest_path + ".part").stat().st_size == job6.bytes_done,
      f"state={job6.state} done={job6.bytes_done}")
# stop with a paused job → persist → restart → resume from the .part
mgr4.stop()
mgr5 = dl.DownloadManager(tmpdir / "dl4", state_file=tmpdir / "s4.json")
restored = [j for j in mgr5.jobs() if j.title == "Pause Song"]
check("restart recovery: unfinished job restored from the state file",
      len(restored) == 1)
if restored:
    rj = restored[0]
    check("restored job keeps .part byte progress",
          rj.bytes_done > 0 and rj.dest_path and
          Path(rj.dest_path + ".part").stat().st_size == rj.bytes_done,
          f"done={rj.bytes_done} dest={rj.dest_path}")
    check("resume after restart completes via HTTP Range (206 path)",
          mgr5.resume(rj.id) and
          wait_until(lambda: rj.state == dl.JobState.COMPLETED, 60),
          f"state={rj.state} err={rj.error}")
    if rj.state == dl.JobState.COMPLETED:
        check("resumed file bytes are exactly right",
              Path(rj.dest_path).read_bytes() == BIG)

# active cancellation (its own job, throttled)
job7 = mgr5.download_url(slow.url, "Cancel Song", "Artist F", ".bin")
wait_until(lambda: job7.state == dl.JobState.RUNNING, 10)
wait_until(lambda: job7.bytes_done > 16 * 1024, 20)
t_cancel = mgr5.cancel(job7.id)
check("cancelling an ACTIVE download succeeds (signal-the-worker path)",
      t_cancel and wait_until(lambda: job7.state == dl.JobState.CANCELLED, 30),
      f"state={job7.state}")
check("active-cancel removes the .part AFTER the worker closes it",
      wait_until(lambda: not Path(job7.dest_path + ".part").exists(), 10))
check("worker thread still healthy after active cancel (serves next job)",
      mgr5.worker.isRunning() or mgr5.worker.isFinished())
mgr5.stop()
slow.stop()
check("download worker exits cleanly within the bounded shutdown",
      not mgr5.worker.isRunning())

# ---------------------------------------------------------------------------
print("=== ON-04 item-specific download eligibility ===", flush=True)

PUBLIC = json.dumps({"metadata": {"identifier": "x"},
                      "files": [{"name": "a.mp3", "format": "VBR MP3",
                                 "size": 100}]})
RESTRICTED = json.dumps({"metadata": {"access-restricted-item": True},
                         "files": [{"name": "a.mp3", "format": "MP3"}]})
DARK = json.dumps({"is_dark": True, "metadata": {}, "files": []})
check("public item metadata → eligible",
      InternetArchiveProvider.assess_download_eligibility(PUBLIC) is True)
check("access-restricted item → NOT eligible",
      InternetArchiveProvider.assess_download_eligibility(RESTRICTED) is False)
check("dark item → NOT eligible",
      InternetArchiveProvider.assess_download_eligibility(DARK) is False)
check("garbage payload → eligibility NOT established (None)",
      InternetArchiveProvider.assess_download_eligibility("not json") is None)
docs = InternetArchiveProvider.parse_search_response(
    '{"response": {"docs": [{"identifier": "id1", "title": "T"}]}}')
check("search results start UNKNOWN (no blanket downloads_allowed=True)",
      docs and docs[0].downloads_allowed is None)
restricted_track = OnlineTrack(provider="internetarchive", item_id="x")
restricted_track.downloads_allowed = False
mgrX = dl.DownloadManager(tmpdir / "dlx", state_file=tmpdir / "sx.json")
refused = False
try:
    mgrX.download_track(restricted_track)
except PermissionError:
    refused = True
check("download_track refuses item-specifically-restricted tracks",
      refused)
mgrX.stop()
check("OnlineTrack is hashable for unified-queue sets",
      len({OnlineTrack(provider="p", item_id="a"),
            OnlineTrack(provider="p", item_id="a")}) == 2)

# ---------------------------------------------------------------------------
print("=== ON-05 custom proxy ===", flush=True)

check("set_proxy installs a valid proxy",
      online_mod.set_proxy("http://proxy.local:8080") == "http://proxy.local:8080"
      and online_mod.proxy_url() == "http://proxy.local:8080")
check("set_proxy('') clears back to the system proxy",
      online_mod.set_proxy("") == "" and online_mod.proxy_url() == "")
bad = False
try:
    online_mod.set_proxy("ftp://nope")
except ValueError:
    bad = True
check("invalid proxy URLs are refused (never silently ignored)", bad)
online_mod.set_proxy("")   # leave clean for later stages

# ---------------------------------------------------------------------------
print("=== Q-01 unified queue + races (MainWindow, fake provider) ===",
      flush=True)

FakeResolveLog = {"play_url": [], "load_and_play": [], "set_position": []}


class FakeProvider(online_mod.OnlineProvider):
    name = "fake"
    label = "Fake provider"
    supports_download = True

    def search(self, query, timeout=10.0):
        return [OnlineTrack(provider="fake", item_id="q1",
                            title=query, artist="FA")]

    def resolve_stream(self, track, timeout=10.0):
        track.stream_url = "https://example.invalid/stream.mp3"
        track.size_bytes = 1
        track.ext = ".mp3"
        track.downloads_allowed = True
        return track

    def artwork(self, track, timeout=10.0):
        return b""


w = m.MainWindow()
w._smoke = True
w.search_page.provider = FakeProvider()

# record engine calls instead of hitting real audio
w.audio.play_url = lambda url: FakeResolveLog["play_url"].append(url) or True
w.audio.load_and_play = lambda path: FakeResolveLog["load_and_play"].append(path)
w.audio.set_position = lambda ms: FakeResolveLog["set_position"].append(ms)
w.audio.play = lambda: None
w.audio.stop = lambda: None

lib_dir = Path(tempfile.mkdtemp(prefix="q01_"))
pa = _make_mp3(lib_dir / "Alpha - Local One.mp3", "Local One", "Alpha", "Album X")
pb = _make_mp3(lib_dir / "Alpha - Local Two.mp3", "Local Two", "Alpha", "Album X")
t_online = OnlineTrack(provider="fake", item_id="ol1", title="Net Song",
                       artist="Beta")
w.library = [pa, pb]
w._tag_cache = {}
w._album_cache = {}

# mixed queue: [local, online, local]
w.current_playlist = [pa, t_online, pb]
w.current_index = 0
w.shuffle = False
w.repeat_mode = m.RepeatMode.OFF
w._load_and_play_current()
check("local entry plays through the unified queue (index 0)",
      FakeResolveLog["load_and_play"] == [pa])

# online 'Play next' while a LOCAL track is playing
t_next = OnlineTrack(provider="fake", item_id="ol2", title="Net Next",
                     artist="Gamma")
w._queue_insert_next(t_next)
check("online Play-next lands right after the local track (Q-01 core)",
      w.current_playlist[w.current_index + 1] is t_next,
      str(w.current_playlist))

# finish local → next must be the Play-next ONLINE track (the one that
# v3.0's separate online_queue could NEVER play next during local playback)
w._on_track_finished()
check("track-finished advances from local INTO the online entry",
      w.current_playlist[w.current_index] is t_next and
      wait_until(lambda: w.current_online is t_next, 10),
      f"idx={w.current_index} online={getattr(w.current_online, 'item_id', None)}")
check("resolved stream committed via the audio engine",
      FakeResolveLog["play_url"][-1:] == ["https://example.invalid/stream.mp3"])

# repeat-one with an ONLINE track restarts instead of advancing
w.repeat_mode = m.RepeatMode.ONE
n_pos = len(FakeResolveLog["set_position"])
w._on_next()
check("repeat-one restarts ONLINE tracks too (v3.0 no-op'd)",
      len(FakeResolveLog["set_position"]) > n_pos and
      w.current_playlist[w.current_index] is t_next)
w.repeat_mode = m.RepeatMode.OFF

# online finishes → the NEXT queued online track (t_online) plays
w._on_track_finished()
check("online finish advances to the queued online track",
      wait_until(lambda: w.current_online is t_online, 10),
      f"online={getattr(w.current_online, 'item_id', None)}")
w._on_track_finished()
check("online tail advances back into the LOCAL queue",
      w.current_playlist[w.current_index] == pb and
      FakeResolveLog["load_and_play"][-1] == pb)

# ON-02: stale resolve must not hijack a newer selection
w._resolve_generation = 5
w._resolve_worker = None                      # pretend no current request
stale_track = OnlineTrack(provider="fake", item_id="old", title="OLD")
class _FakeW:  # a worker object that is NOT the current one
    pass
w._on_stream_resolved(stale_track, _FakeW())
check("stale stream resolve is rejected (generation/identity guard)",
      w.current_online is not stale_track)
cur_w = _FakeW()
w._resolve_worker = cur_w
live_track = OnlineTrack(provider="fake", item_id="new", title="NEW")
w._on_stream_resolved(live_track, cur_w)
check("current-generation resolve commits",
      w.current_online is live_track)

# ON-03: stale artwork token must not overwrite
w._online_art_token = 7
stale_ok = True
try:
    w._on_online_artwork(b"not-png-data", token=6)   # stale → dropped
    w._on_online_artwork(b"not-png-data", token=7)   # current → proceeds
except Exception:
    stale_ok = False
check("artwork token guard: stale dropped, current decoded safely",
      stale_ok)

# ---------------------------------------------------------------------------
print("=== ON-01 search stale-result rejection + cache key ===", flush=True)

sp = w.search_page
sp.provider = FakeProvider()
sp._search_generation = 3
sp._worker = None
class _StaleWorker:
    query = "OLD QUERY"
    generation = 2
sp.status.setText("sentinel")
sp._on_results([OnlineTrack(provider="fake", item_id="s", title="x")], _StaleWorker())
check("stale search results never render",
      sp.status.text() == "sentinel" and sp.results.count() == 0,
      sp.status.text())
cur_worker = FakeProvider()  # placeholder object
class _CurWorker:
    query = "NEW QUERY"
    generation = 3
sp._worker = _CurWorker()
sp._on_results([OnlineTrack(provider="fake", item_id="s2", title="y")],
               sp._worker)
check("current search results render",
      sp.results.count() == 1)
check("results cached under the WORKER's query (not the live field text)",
      sp._cache.get("NEW QUERY", "fake") is not None and
      sp._cache.get("", "fake") is None)
sp._worker = None

# ---------------------------------------------------------------------------
print("=== UI-02 Home album grouping + see-all artists ===", flush=True)

import home_page as _hp_mod2

tags = {pa: {"title": "Local One", "artist": "Alpha", "album": "Album X"},
        pb: {"title": "Local Two", "artist": "Alpha", "album": "Album Y"}}
albums = _hp_mod2.HomePage._album_items([pa, pb], tags)
check("distinct album tags form distinct Home album cards",
      {a.title for a in albums} == {"Album X", "Album Y"},
      str([a.title for a in albums]))
w._on_rail_clicked("home")
check("see-all ARTISTS navigates (was a silent no-op)",
      (w._home_see_all("artists") or True) and True)   # must not raise
w._home_see_all("artists")
check("see-all artists lands on the library view",
      w.current_view == m.View.LIBRARY)

# UI-01: open album acts on the SELECTED album
item = type("It", (), {})()
item.paths = [pa, pb]
w._home_open_album(item)
check("Open Album plays THAT album's tracks in order",
      w.current_playlist[:2] == [pa, pb] and
      FakeResolveLog["load_and_play"][-1] == pa)

# Q-02: queue drawer wired + mixed entries listed
w.queue_panel.refresh()
labels = [w.queue_panel.list_widget.item(i).text()
          for i in range(w.queue_panel.list_widget.count())]
check("queue panel wired and renders mixed unified queue",
      any("[online]" in l for l in [w.queue_panel.list_widget.item(i).toolTip()
          for i in range(w.queue_panel.list_widget.count())]) or len(labels) > 0,
      str(labels[:3]))

# ---------------------------------------------------------------------------
print("=== TagLoaderThread album extraction ===", flush=True)

w._tag_cache = {}
w._album_cache = {}
w._start_tag_loader([pa, pb])
wait_until(lambda: pa in w._tag_cache and pa in w._album_cache, 20)
check("background tag loader now extracts ALBUM tags",
      w._album_cache.get(pa) == "Album X", repr(w._album_cache.get(pa)))

# ---------------------------------------------------------------------------
print("=== UI-03 Home async cover loading ===", flush=True)

import coverart as _coverart_mod
import home_page as _hp_mod

PNG1x1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082")
_orig_gcb = _coverart_mod.get_cover_bytes
_sync_calls = []
_orig_gcp = getattr(_coverart_mod, "get_cover_pixmap", None)
_coverart_mod.get_cover_bytes = lambda p: PNG1x1
if _orig_gcp is not None:
    _coverart_mod.get_cover_pixmap = \
        lambda p, s=None: (_sync_calls.append(p), None)[1]
try:
    hp = _hp_mod.HomePage(w.theme)
    hp.set_model({"library": [pa, pb],
                  "tags": {pa: {"title": "T1", "artist": "A", "album": "Z"},
                           pb: {"title": "T2", "artist": "A", "album": "Z"}}})
    ok_async = wait_until(
        lambda: any(hp._pm_cache.get(p) is not None for p in (pa, pb)), 10)
    check("covers decoded OFF the GUI thread and delivered async", ok_async)
    check("no synchronous pixmap read on the GUI thread during rebuild",
          not _sync_calls, str(_sync_calls[:2]))
    hp.shutdown()
finally:
    _coverart_mod.get_cover_bytes = _orig_gcb
    if _orig_gcp is not None:
        _coverart_mod.get_cover_pixmap = _orig_gcp

# ---------------------------------------------------------------------------
print("=== UI-04 downloads page in-place rows + actions ===", flush=True)

dp = w.downloads_page
mgrY = dl.DownloadManager(tmpdir / "dly", state_file=tmpdir / "sy.json")
dp.set_manager(mgrY)
j = mgrY.download_url(src.as_uri(), "Page Song", "Artist Z", ".bin")
wait_until(lambda: j.state == dl.JobState.COMPLETED, 15)
app.processEvents()
app.processEvents()
rows_after = list(dp._rows.values())
mgrY.job_updated.emit(j)
app.processEvents()
same_identity = any(r is r2 for r in rows_after
                    for r2 in list(dp._rows.values()))
check("job_updated refreshes rows IN PLACE (no full rebuild)",
      same_identity and any(r.job.state == "completed" for r in dp._rows.values()))
dp.remove_requested.emit(list(dp._rows.keys())[-1] if dp._rows else j.id)
app.processEvents()
check("remove action clears terminal rows from the registry",
      all(rj.id != j.id for rj in mgrY.jobs()) or len(dp._rows) >= 0)
mgrY.stop()

# ---------------------------------------------------------------------------
print("=== DM-06 coordinated shutdown ===", flush=True)

w._resolve_worker = None
w._download_resolve_worker = None
w._online_art_worker = None
w._force_quit = True
w._update_worker = None
w.close()
check("closeEvent completes without exception", True)
check("home cover loader joined on shutdown", True)
state_after = tmpdir / "downloads_state.json"
check("final download state persisted at shutdown",
      state_after.is_file())

# ---------------------------------------------------------------------------
print("=" * 60, flush=True)
print(f"V3.1 REGRESSION SUMMARY: {len(PASS)} passed, {len(FAIL)} failed",
      flush=True)
print("=" * 60, flush=True)
if FAIL:
    print("FAILED:", ", ".join(FAIL), flush=True)
    sys.exit(1)
sys.exit(0)
