"""Golden Music 2.3 — spectrum analyzer (Qt-free, numpy optional).

- :data:`BANDS` (20) log-spaced band centre frequencies from 40 Hz to
  16 kHz, computed at import (:data:`BAND_FREQS`).
- :func:`band_magnitudes` computes the 20 band magnitudes (0..1) for a
  mono frame: Hann window, then numpy FFT band energies when available,
  otherwise a pure-python Goertzel pass per band centre (documented slower
  path).  Bands are normalised by the max band.
- :class:`SpectrumAnalyzer` adds attack/decay smoothing (rise fast 0.6,
  fall slow 0.15) plus peak-hold decay on top of consecutive frames.
"""
import logging
import math
import struct

log = logging.getLogger("app.spectrum")

try:
    import numpy as _np
except ImportError:  # optional speed path only
    _np = None

#: Number of spectrum bands.
BANDS = 20

#: Lowest / highest band centre frequency in Hz.
F_MIN = 40.0
F_MAX = 16000.0

#: Log-spaced band centre frequencies (computed at import time).
BAND_FREQS: list[float] = [F_MIN * (F_MAX / F_MIN) ** (i / (BANDS - 1))
                           for i in range(BANDS)]

#: Smoothing weights (rise = fast attack, fall = slow decay).
RISE_WEIGHT = 0.6
FALL_WEIGHT = 0.15

#: Peak-hold decay per update (linear, in 0..1 units).
PEAK_DECAY = 0.02


def _to_floats(samples) -> list[float]:
    """Accept int16 LE bytes or any numeric sequence (values as-is).

    band magnitudes are scale-invariant (normalised by the max band), so
    int16 lists need no rescaling here.
    """
    if isinstance(samples, (bytes, bytearray)):
        data = bytes(samples)
        n = len(data) // 2
        if n == 0:
            return []
        return list(struct.unpack("<%dh" % n, data[: n * 2]))
    return [float(v) for v in samples]


def _band_edges() -> list[float]:
    """Band boundary frequencies: 0, geometric midpoints, +inf."""
    edges = [0.0]
    for i in range(BANDS - 1):
        edges.append(math.sqrt(BAND_FREQS[i] * BAND_FREQS[i + 1]))
    edges.append(float("inf"))
    return edges


def band_magnitudes(samples, sample_rate: float,
                    backend: str = "auto") -> list[float]:
    """20 band magnitudes in 0..1 for a mono sample frame.

    ``backend``: ``"auto"`` (numpy when available), ``"numpy"`` (falls back
    to Goertzel with a warning when numpy is missing) or ``"python"``
    (pure-python Goertzel per band centre — slower, documented fallback).

    The input is windowed (Hann); each band's magnitude is the RMS of the
    FFT bins inside the band (or the Goertzel magnitude at the band centre)
    and bands are normalised by the max band so the loudest band is 1.0.
    """
    try:
        sr = float(sample_rate)
    except (TypeError, ValueError):
        return [0.0] * BANDS
    if sr <= 0:
        return [0.0] * BANDS
    x = _to_floats(samples)
    if not x:
        return [0.0] * BANDS
    backend = str(backend).lower()
    use_numpy = _np is not None and backend != "python"
    if backend == "numpy" and _np is None:
        log.warning("numpy backend requested but numpy is missing; using Goertzel")
    mags = _magnitudes_fft(x, sr) if use_numpy else _magnitudes_goertzel(x, sr)
    peak = max(mags) if mags else 0.0
    if peak <= 1e-12:
        return [0.0] * BANDS
    return [min(1.0, max(0.0, m / peak)) for m in mags]


def _magnitudes_fft(x: list[float], sr: float) -> list[float]:
    """numpy FFT path: RMS magnitude per band (Hann-windowed)."""
    arr = _np.asarray(x, dtype="float64")
    n = arr.size
    # zero-pad to a power of two for a fast, well-resolved spectrum
    nfft = 1 << max(6, (n - 1).bit_length())
    window = _np.hanning(n)
    spectrum = _np.abs(_np.fft.rfft(arr * window, n=nfft))
    freqs = _np.fft.rfftfreq(nfft, d=1.0 / sr)
    edges = _band_edges()
    mags: list[float] = []
    for i in range(BANDS):
        lo, hi = edges[i], edges[i + 1]
        mask = (freqs >= lo) & (freqs < hi)
        if not bool(mask.any()):
            mags.append(0.0)
        else:
            mags.append(float(_np.sqrt(_np.mean(spectrum[mask] ** 2))))
    return mags


def _magnitudes_goertzel(x: list[float], sr: float) -> list[float]:
    """Pure-python path: Goertzel magnitude at each band centre.

    Slower than the FFT (O(bands * n)) but dependency-free; magnitudes are
    comparable to per-band RMS for steady tones.
    """
    n = len(x)
    denom = (n - 1) if n > 1 else 1
    # Hann window
    wx = [x[i] * (0.5 - 0.5 * math.cos(2.0 * math.pi * i / denom))
          for i in range(n)]
    mags: list[float] = []
    for f in BAND_FREQS:
        if f >= sr / 2.0:
            mags.append(0.0)
            continue
        omega = 2.0 * math.pi * f / sr
        coeff = 2.0 * math.cos(omega)
        s1 = 0.0
        s2 = 0.0
        for v in wx:
            s0 = v + coeff * s1 - s2
            s2 = s1
            s1 = s0
        power = s2 * s2 + s1 * s1 - coeff * s1 * s2
        mags.append(math.sqrt(power) / n if power > 0.0 else 0.0)
    return mags


class SpectrumAnalyzer:
    """Stateful analyzer: attack/decay smoothing + peak-hold decay.

    rise=0.6 (fast attack), fall=0.15 (slow decay) per :meth:`update`;
    peak values decay by ``peak_decay`` per update and never drop below
    the smoothed value.
    """

    def __init__(self, rise: float = RISE_WEIGHT, fall: float = FALL_WEIGHT,
                 peak_decay: float = PEAK_DECAY, backend: str = "auto") -> None:
        self.rise = float(rise)
        self.fall = float(fall)
        self.peak_decay = float(peak_decay)
        self.backend = str(backend)
        self._prev: list[float] = [0.0] * BANDS
        self._peaks: list[float] = [0.0] * BANDS

    def update(self, samples, sample_rate: float) -> list[float]:
        """Feed one frame; returns the smoothed 0..1 band values."""
        mags = band_magnitudes(samples, sample_rate, self.backend)
        smoothed: list[float] = []
        for i, m in enumerate(mags):
            prev = self._prev[i]
            weight = self.rise if m >= prev else self.fall
            v = prev + weight * (m - prev)
            if v < 0.0:
                v = 0.0
            elif v > 1.0:
                v = 1.0
            smoothed.append(v)
            peak = self._peaks[i] - self.peak_decay
            if v > peak:
                peak = v
            if peak < 0.0:
                peak = 0.0
            self._peaks[i] = peak
        self._prev = smoothed
        return list(smoothed)

    @property
    def peaks(self) -> list[float]:
        """Current peak-hold values (copy)."""
        return list(self._peaks)

    def reset(self) -> None:
        """Clear smoothing and peak-hold history."""
        self._prev = [0.0] * BANDS
        self._peaks = [0.0] * BANDS
