"""Golden Music 3.0 — PCM content tap + waveform/spectrum workers.

Real-audio analysis helpers that run OFF the UI thread:

  * WaveformWorker — decodes a file once (miniaudio when installed) and
    emits ~400 normalized peaks for the seekbar; caches in SQLite.
  * ContentTap — while playing, decodes short windows just AHEAD of the
    playback position and emits real FFT band magnitudes for the spectrum
    visualizer (content-driven analysis — see docs/DECISIONS.md #d8 for why
    a literal output-device loopback is not used).

Both degrade gracefully (no miniaudio -> worker reports unavailability once
and the UI falls back to the classic seekbar / hides the visualizer).
"""
from __future__ import annotations

import logging
import os
import struct
import time

from PyQt6.QtCore import QThread, pyqtSignal

log = logging.getLogger("app.tap")

try:
    import miniaudio                      # optional
    HAVE_MINIAUDIO = True
except Exception:                         # pragma: no cover
    HAVE_MINIAUDIO = False

import array


def _decode_window(path: str, start_ms: int, ms: int) -> tuple | None:
    """Decode [start_ms, start_ms+ms) as mono int16 at the file's rate.

    Returns (samples: array[h], sample_rate) or None.
    """
    if not HAVE_MINIAUDIO:
        return None
    try:
        src = miniaudio.decode_file(path, output_format=miniaudio.SampleFormat.SIGNED16,
                                    nchannels=1, sample_rate=0,
                                    dither=miniaudio.DitherMode.NONE)
        sr = src.sample_rate
        total = len(src.samples)
        start = int(start_ms / 1000.0 * sr)
        count = int(ms / 1000.0 * sr)
        if start >= total:
            return (array.array("h"), sr)
        chunk = src.samples[start:start + count]
        return (array.array("h", chunk), sr)
    except Exception:
        return None


class WaveformWorker(QThread):
    """Generate + cache seekbar peaks for one file."""
    peaks_ready = pyqtSignal(str, object)      # (path, list[float] | None)

    def __init__(self, path: str, db=None, buckets: int = 400, parent=None):
        super().__init__(parent)
        self._path = str(path)
        self._db = db
        self._buckets = buckets
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        path = self._path
        try:
            if self._db is not None:
                blob = None
                try:
                    blob = self._db.get_waveform(path)
                except Exception:
                    blob = None
                if blob:
                    try:
                        vals = [b / 255.0 for b in blob]
                        if not self._cancelled:
                            self.peaks_ready.emit(path, vals)
                        return
                    except Exception:
                        pass
            if not HAVE_MINIAUDIO or not os.path.isfile(path):
                if not self._cancelled:
                    self.peaks_ready.emit(path, None)
                return
            from gm.waveform import decode_peaks
            peaks, duration_ms = decode_peaks(path, self._buckets)
            if peaks and self._db is not None:
                try:
                    self._db.store_waveform(path, duration_ms or 0, peaks,
                                            "miniaudio")
                except Exception:
                    pass
            if not self._cancelled:
                self.peaks_ready.emit(
                    path, [b / 255.0 for b in peaks] if peaks else None)
        except Exception:
            log.exception("waveform worker failed")
            if not self._cancelled:
                self.peaks_ready.emit(path, None)


class ContentTap(QThread):
    """Emit real FFT magnitudes for the playing position (20 fps cap)."""

    spectrum_ready = pyqtSignal(object)         # list[float] 0..1 x20

    def __init__(self, parent=None):
        super().__init__(parent)
        self._path = None
        self._get_position = None                 # callable -> ms
        self._get_state = None                    # callable -> 0/1/2
        self._running = False
        self._stop = False

    def start_tap(self, path: str, get_position, get_state):
        self._path = str(path)
        self._get_position = get_position
        self._get_state = get_state
        self._stop = False
        if not self.isRunning():
            self.start()

    def stop_tap(self):
        self._stop = True

    def run(self):
        try:
            from gm.spectrum import SpectrumAnalyzer
        except Exception:
            return
        analyzer = SpectrumAnalyzer()
        last_emit = 0.0
        idle_since = time.monotonic()
        while not self._stop:
            try:
                time.sleep(0.05)
                if self._get_state is None or self._get_state() != 1:
                    if time.monotonic() - idle_since > 90.0:
                        break               # zombie guard: long-idle tap dies
                    continue
                idle_since = time.monotonic()
                if not HAVE_MINIAUDIO:
                    self.spectrum_ready.emit(None)     # signal unavailability
                    self._stop = True
                    break
                pos = self._get_position() or 0
                window = _decode_window(self._path, pos, 250)
                if window is None:
                    continue
                samples, sr = window
                if len(samples) < 256:
                    continue
                now = time.monotonic()
                if now - last_emit < 0.045:            # ~20 fps
                    continue
                last_emit = now
                mags = analyzer.update(samples, sr)
                self.spectrum_ready.emit(mags)
            except Exception:
                time.sleep(0.25)
        self._path = None
