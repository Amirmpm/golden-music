"""Golden Music 3.0 — waveform peak extraction and cache (Qt-free).

- :func:`decode_peaks` decodes a track via python-miniaudio (lazy import;
  returns ``(None, None)`` when unavailable) and computes per-bucket
  max-|sample| peaks normalised to 0..255 uint8.  Decoding is mono 16-bit
  at 44.1 kHz and capped to the first 10 minutes for speed.
- :func:`encode_peaks` / :func:`decode_peaks_blob` convert between the
  0..1 float and uint8 byte representations.
- :class:`WaveformCache` wraps injectable get/store callables (the app
  wires its DB), is thread-safe with single-flight per path, and drops
  stale results via a generation counter.
"""
import logging
import struct
import threading

log = logging.getLogger("app.waveform")

try:
    import numpy as _np
except ImportError:  # optional speed path only
    _np = None

#: Default number of waveform buckets per track.
DEFAULT_BUCKETS = 400

#: Analyse at most the first 10 minutes of a track (speed cap).
MAX_ANALYZE_SECONDS = 600

#: Decode parameters used for waveform analysis.
_ANALYZE_SAMPLE_RATE = 44100
_ANALYZE_CHANNELS = 1

#: Decoder backends recognised by decode_peaks().
SUPPORTED_ENGINES = ("auto", "miniaudio")


def encode_peaks(peaks) -> bytes:
    """Encode 0..1 floats as normalised uint8 bytes (0..255)."""
    out = bytearray()
    for p in peaks:
        try:
            v = float(p)
        except (TypeError, ValueError):
            v = 0.0
        if v < 0.0:
            v = 0.0
        elif v > 1.0:
            v = 1.0
        out.append(int(round(v * 255.0)))
    return bytes(out)


def decode_peaks_blob(blob: bytes) -> list[float]:
    """Decode uint8 peak bytes back to 0..1 floats (value / 255)."""
    return [b / 255.0 for b in bytes(blob)]


def decode_peaks(path, buckets: int = DEFAULT_BUCKETS,
                 engine: str = "auto") -> tuple[bytes | None, int | None]:
    """Decode ``path`` and compute waveform peaks.

    Returns ``(peaks_bytes, duration_ms)`` — ``(None, None)`` when the
    decoder is unavailable (python-miniaudio not installed, read error or
    unknown engine).  ``engine`` is ``"auto"`` or ``"miniaudio"`` (reserved
    for future decoder backends).

    Files longer than :data:`MAX_ANALYZE_SECONDS` are analysed only over
    the first 10 minutes and the reported duration is capped accordingly so
    the bucket->time mapping stays consistent.
    """
    try:
        buckets = int(buckets)
    except (TypeError, ValueError):
        log.warning("decode_peaks: non-numeric buckets %r", buckets)
        return (None, None)
    if buckets <= 0:
        log.warning("decode_peaks: buckets must be > 0")
        return (None, None)
    if str(engine).lower() not in SUPPORTED_ENGINES:
        log.warning("decode_peaks: unknown engine %r", engine)
        return (None, None)
    try:
        import miniaudio  # lazy: optional dependency
    except ImportError:
        log.debug("python-miniaudio not installed; waveform peaks unavailable")
        return (None, None)
    try:
        decoded = miniaudio.decode_file(
            str(path),
            output_format=miniaudio.SampleFormat.SIGNED16,
            nchannels=_ANALYZE_CHANNELS,
            sample_rate=_ANALYZE_SAMPLE_RATE,
        )
        raw = bytes(decoded.samples)
    except Exception as exc:  # decoder/file IO boundary
        log.warning("waveform decode failed for %s: %s", path, exc)
        return (None, None)
    frames = len(raw) // 2  # mono int16 after the nchannels=1 downmix
    duration_ms = int(frames * 1000 / _ANALYZE_SAMPLE_RATE)
    cap_frames = MAX_ANALYZE_SECONDS * _ANALYZE_SAMPLE_RATE
    if frames > cap_frames:
        frames = cap_frames
        raw = raw[: frames * 2]
        duration_ms = MAX_ANALYZE_SECONDS * 1000
    if frames == 0:
        return (b"\x00" * buckets, 0)
    peaks = _peaks_from_int16(raw, frames, buckets)
    return (peaks, duration_ms)


def _bucket_edges(frames: int, buckets: int) -> list[int]:
    """Inclusive-exclusive bucket edges covering ``[0, frames)``."""
    return [int(i * frames / buckets) for i in range(buckets + 1)]


def _byte_peak(peak: int, global_max: int) -> int:
    """Normalise one bucket peak against the global max, as a uint8."""
    if global_max <= 0:
        return 0
    v = int(round(peak * 255.0 / global_max))
    if v > 255:
        return 255
    if v < 0:
        return 0
    return v


def _peaks_from_int16(raw: bytes, frames: int, buckets: int) -> bytes:
    """Per-bucket max-|sample| normalised to 0..255 (mono int16 input).

    Uses numpy when available (module-level optional import); otherwise a
    pure-python struct/array path.
    """
    if frames <= 0 or buckets <= 0:
        return b"\x00" * max(1, buckets)
    edges = _bucket_edges(frames, buckets)
    out = bytearray(buckets)
    if _np is not None:
        arr = _np.frombuffer(raw, dtype="<i2", count=frames)
        abs_arr = _np.abs(arr.astype(_np.int32))
        global_max = int(abs_arr.max())
        for i in range(buckets):
            lo, hi = edges[i], edges[i + 1]
            if hi > lo:
                out[i] = _byte_peak(int(abs_arr[lo:hi].max()), global_max)
        return bytes(out)
    values = struct.unpack("<%dh" % frames, raw[: frames * 2])
    global_max = 0
    for v in values:
        a = -v if v < 0 else v
        if a > global_max:
            global_max = a
    for i in range(buckets):
        lo, hi = edges[i], edges[i + 1]
        if hi > lo:
            peak = 0
            for v in values[lo:hi]:
                a = -v if v < 0 else v
                if a > peak:
                    peak = a
            out[i] = _byte_peak(peak, global_max)
    return bytes(out)


class WaveformCache:
    """Thread-safe waveform cache over injectable get/store callables.

    - ``get(path) -> bytes | None`` / ``store(path, blob)`` are injected by
      the app (wired to its database).
    - Single-flight per path: concurrent :meth:`load` calls for the same
      path share one compute (per-path lock, double-checked get).
    - Generation counter: :meth:`invalidate` bumps it; results computed
      against an older generation are dropped (not stored, load returns
      None) so stale decodes never pollute the store.
    """

    def __init__(self, get=None, store=None, buckets: int = DEFAULT_BUCKETS) -> None:
        self._get = get
        self._store = store
        self.buckets = int(buckets)
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        self._generation = 0

    # -- configuration ------------------------------------------------------
    @property
    def generation(self) -> int:
        """Current generation counter (bumped by :meth:`invalidate`)."""
        with self._guard:
            return self._generation

    def invalidate(self) -> int:
        """Bump the generation; in-flight computations become stale."""
        with self._guard:
            self._generation += 1
            return self._generation

    # -- loading ---------------------------------------------------------------
    def load(self, path, compute=None) -> list[float] | None:
        """Return the 0..1 peak list for ``path`` or None when unavailable.

        ``compute(path) -> bytes | None`` overrides the decoder (tests /
        alternate backends); the default decodes via :func:`decode_peaks`.
        """
        key = str(path)
        blob = self._underlying_get(key)
        if blob is not None:
            return decode_peaks_blob(blob)
        with self._lock_for(key):
            # double-check: another thread may have stored while we waited
            blob = self._underlying_get(key)
            if blob is not None:
                return decode_peaks_blob(blob)
            gen0 = self.generation
            blob = self._compute(key, compute)
            if blob is None:
                return None
            if self.generation != gen0:
                log.debug("dropping stale waveform result for %s", key)
                return None
            self._underlying_store(key, blob)
            return decode_peaks_blob(blob)

    def _compute(self, key: str, compute):
        try:
            if callable(compute):
                return compute(key)
            return decode_peaks(key, self.buckets)[0]
        except Exception as exc:  # decoder boundary
            log.warning("waveform compute failed for %s: %s", key, exc)
            return None

    def _lock_for(self, key: str) -> threading.Lock:
        """Per-path lock (single-flight), guarded by the global lock."""
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            return lock

    def _underlying_get(self, key: str) -> bytes | None:
        if not callable(self._get):
            return None
        try:
            return self._get(key)
        except Exception as exc:
            log.warning("waveform cache get failed for %s: %s", key, exc)
            return None

    def _underlying_store(self, key: str, blob: bytes) -> None:
        if not callable(self._store):
            return
        try:
            self._store(key, blob)
        except Exception as exc:
            log.warning("waveform cache store failed for %s: %s", key, exc)
