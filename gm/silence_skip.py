"""Golden Music 3.0 — silence detection and skipping (Qt-free).

- :func:`detect_silence_windows` computes RMS over 50 ms windows and
  returns merged ``(start_ms, end_ms)`` silence intervals; windows shorter
  than ``min_ms`` are dropped.
- :class:`SilenceSkipper` decides, from the current playback position,
  whether to jump to the end of the silence window the player is currently
  inside.
"""
import logging
import math
import struct

log = logging.getLogger("app.silence")

#: RMS window length in milliseconds.
WINDOW_MS = 50

#: Default silence threshold in dBFS (RMS per window).
DEFAULT_THRESHOLD_DB = -40.0

#: Default minimum silence length to be considered skippable.
DEFAULT_MIN_MS = 700


def _to_floats(samples) -> list[float]:
    """Normalise sample input to floats on a -1..1 scale.

    bytes/bytearray are int16 LE PCM; integer sequences with values beyond
    ±1 are treated as int16 PCM as well (the app's analyzers hand int16
    buffers around).  Other sequences are used as-is.
    """
    if isinstance(samples, (bytes, bytearray)):
        data = bytes(samples)
        n = len(data) // 2
        if n == 0:
            return []
        return [s / 32768.0 for s in struct.unpack("<%dh" % n, data[: n * 2])]
    values = [float(v) for v in samples]
    if values and all(v.is_integer() for v in values) \
            and max(abs(v) for v in values) > 1.0:
        values = [v / 32768.0 for v in values]
    return values


def detect_silence_windows(samples, sample_rate: float,
                           threshold_db: float = DEFAULT_THRESHOLD_DB,
                           min_ms: int = DEFAULT_MIN_MS) -> list[tuple[int, int]]:
    """Detect silence windows as a list of ``(start_ms, end_ms)`` tuples.

    RMS is computed per 50 ms window; a window is silent when
    ``20*log10(rms) <= threshold_db``.  Adjacent silent windows are merged
    and runs shorter than ``min_ms`` are dropped.  Returns [] for empty
    input or an invalid sample rate.
    """
    try:
        sr = float(sample_rate)
    except (TypeError, ValueError):
        return []
    if sr <= 0:
        return []
    vals = _to_floats(samples)
    if not vals:
        return []
    win = max(1, int(sr * WINDOW_MS / 1000.0))
    n = len(vals)
    step_ms = win * 1000.0 / sr
    duration_ms = n * 1000.0 / sr
    n_windows = (n + win - 1) // win
    runs: list[tuple[int, int]] = []
    run_start_win = None
    for wi in range(n_windows):
        lo = wi * win
        hi = min(lo + win, n)
        total = 0.0
        for v in vals[lo:hi]:
            total += v * v
        rms = math.sqrt(total / (hi - lo))
        silent = rms <= 0.0 or 20.0 * math.log10(rms) <= threshold_db
        if silent:
            if run_start_win is None:
                run_start_win = wi
        elif run_start_win is not None:
            runs.append((run_start_win, wi))
            run_start_win = None
    if run_start_win is not None:
        runs.append((run_start_win, n_windows))
    out: list[tuple[int, int]] = []
    for start_w, end_w in runs:
        start_ms = int(round(start_w * step_ms))
        end_ms = int(round(min(end_w * step_ms, duration_ms)))
        if end_ms - start_ms >= int(min_ms):
            out.append((start_ms, end_ms))
    return out


class SilenceSkipper:
    """Skips over long silence windows during playback.

    ``next_skip_position`` returns the end of the silence window the
    position is currently inside (jump target), or None when skipping is
    disabled or the position is not inside silence.
    """

    def __init__(self, enabled: bool = False,
                 min_ms: int = DEFAULT_MIN_MS,
                 threshold_db: float = DEFAULT_THRESHOLD_DB) -> None:
        self.enabled = bool(enabled)
        self.min_ms = max(0, int(min_ms))
        self.threshold_db = float(threshold_db)

    def next_skip_position(self, position_ms: int,
                           windows) -> int | None:
        """First window end > position whose window start <= position.

        In other words: when the player is currently inside a silence
        window, jump to that window's end; otherwise None.
        """
        if not self.enabled:
            return None
        try:
            pos = int(position_ms)
        except (TypeError, ValueError):
            return None
        for start, end in list(windows or []):
            try:
                s, e = int(start), int(end)
            except (TypeError, ValueError):
                continue
            if s <= pos < e:
                return e
        return None

    def to_dict(self) -> dict:
        """Serialize for settings persistence."""
        return {"enabled": self.enabled, "min_ms": self.min_ms,
                "threshold_db": self.threshold_db}

    @classmethod
    def from_dict(cls, data: dict) -> "SilenceSkipper":
        """Restore from a settings dict; tolerant of missing/invalid keys."""
        d = dict(data or {})
        try:
            min_ms = int(d.get("min_ms", DEFAULT_MIN_MS))
        except (TypeError, ValueError):
            min_ms = DEFAULT_MIN_MS
        try:
            threshold = float(d.get("threshold_db", DEFAULT_THRESHOLD_DB))
        except (TypeError, ValueError):
            threshold = DEFAULT_THRESHOLD_DB
        return cls(enabled=bool(d.get("enabled", False)),
                   min_ms=min_ms, threshold_db=threshold)
