"""Golden Music 2.3 — 10-band ISO equalizer (Qt-free, pure python math).

- 10 ISO band centres: 31, 62, 125, 250, 500, 1k, 2k, 4k, 8k, 16 kHz.
- Band gains in -12..+12 dB.
- :func:`biquad_peaking_coeffs` implements the standard RBJ (audio EQ
  cookbook) peaking filter; gain 0 short-circuits to the exact identity
  filter so flat bands never colour the signal.
- :func:`mpv_superequalizer_string` formats the curve for the mpv
  ``superequalizer`` audio filter (used by gm.audio_engines.MpvAudioEngine
  via ``engine.set_af(...)``).

numpy is NOT required by this module (it is only used by tests/analysis
helpers that accept plain sequences).
"""
import logging
import math

log = logging.getLogger("app.eq")

#: ISO 266-style band centre frequencies in Hz (10 bands).
BAND_FREQS: list[float] = [31.0, 62.0, 125.0, 250.0, 500.0,
                           1000.0, 2000.0, 4000.0, 8000.0, 16000.0]

#: Number of bands (== len(BAND_FREQS)).
BAND_COUNT = len(BAND_FREQS)

#: Supported gain range in dB.
MIN_GAIN_DB = -12.0
MAX_GAIN_DB = 12.0

#: Default Q factor for the peaking filters.
DEFAULT_Q = 1.0

#: Default sample rate for coefficient maths.
DEFAULT_SAMPLE_RATE = 44100

#: Named presets; "Custom" is the user curve managed by the app.
PRESETS: dict[str, list[float]] = {
    "Flat": [0.0] * 10,
    "Bass Boost": [7.0, 5.0, 3.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    "Rock": [4.0, 3.0, -1.0, -2.0, 0.0, 1.0, 3.0, 4.0, 4.0, 4.0],
    "Night": [-2.0, -1.0, 0.0, 1.0, 2.0, 2.0, 1.0, 0.0, -1.0, -2.0],
    "Vocal": [-2.0, -3.0, 0.0, 2.0, 4.0, 4.0, 3.0, 0.0, -1.0, -2.0],
    "Treble Boost": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 2.0, 4.0, 7.0],
    "Custom": [0.0] * 10,
}


def clamp_gain(db: float) -> float:
    """Clamp a band gain to the supported -12..+12 dB range."""
    try:
        value = float(db)
    except (TypeError, ValueError):
        log.warning("clamp_gain: non-numeric gain %r; using 0 dB", db)
        return 0.0
    return max(MIN_GAIN_DB, min(MAX_GAIN_DB, value))


def validate_gains(gains) -> list[float]:
    """Validate a gain sequence: exactly 10 entries, values clamped.

    Raises ValueError on a wrong length (None included).
    """
    if gains is None:
        raise ValueError("gains must be a sequence of 10 values, got None")
    gains = list(gains)
    if len(gains) != BAND_COUNT:
        raise ValueError(f"gains must have {BAND_COUNT} entries, got {len(gains)}")
    return [clamp_gain(g) for g in gains]


def biquad_peaking_coeffs(freq: float, gain_db: float,
                           q: float = DEFAULT_Q,
                           sample_rate: float = DEFAULT_SAMPLE_RATE
                           ) -> tuple[float, float, float, float, float]:
    """RBJ audio-EQ-cookbook peaking filter coefficients.

    Returns ``(b0, b1, b2, a1, a2)`` normalised by ``a0``.  ``gain_db == 0``
    short-circuits to the exact identity filter ``(1, 0, 0, 0, 0)`` so flat
    bands apply a true unity transfer function.

    Raises ValueError for out-of-range frequency / q / sample_rate.
    """
    freq = float(freq)
    gain_db = float(gain_db)
    q = float(q)
    sample_rate = float(sample_rate)
    if sample_rate <= 0:
        raise ValueError("sample_rate must be > 0")
    if q <= 0:
        raise ValueError("q must be > 0")
    if freq <= 0 or freq >= sample_rate / 2.0:
        raise ValueError("need 0 < freq < sample_rate/2")
    if abs(gain_db) < 1e-9:
        # Flat band: exact identity (the RBJ formula would yield a filter
        # equal to 1 in transfer function, but with non-zero coefficients).
        return (1.0, 0.0, 0.0, 0.0, 0.0)
    a = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * math.pi * freq / sample_rate
    cw = math.cos(w0)
    sw = math.sin(w0)
    alpha = sw / (2.0 * q)
    a0 = 1.0 + alpha / a
    b0 = (1.0 + alpha * a) / a0
    b1 = (-2.0 * cw) / a0
    b2 = (1.0 - alpha * a) / a0
    a1 = (-2.0 * cw) / a0
    a2 = (1.0 - alpha / a) / a0
    return (b0, b1, b2, a1, a2)


def apply_biquad(samples, coeffs: tuple[float, float, float, float, float]) -> list[float]:
    """Apply a biquad filter (direct form II transposed) to a sample sequence.

    Pure python; used by analysis/tests to verify filter shapes.
    """
    b0, b1, b2, a1, a2 = coeffs
    out: list[float] = []
    z1 = 0.0
    z2 = 0.0
    for x in samples:
        y = b0 * float(x) + z1
        z1 = b1 * float(x) + z2 - a1 * y
        z2 = b2 * float(x) - a2 * y
        out.append(y)
    return out


def mpv_superequalizer_string(gains) -> str:
    """Format gains as an mpv ``superequalizer=eq1=..:eq10=..`` af string.

    Gains are clamped to ±12 dB and formatted with one decimal.
    Raises ValueError when the sequence does not have exactly 10 entries.
    """
    clamped = validate_gains(gains)
    parts = [f"eq{i + 1}={v:.1f}" for i, v in enumerate(clamped)]
    return "superequalizer=" + ":".join(parts)


class EQController:
    """10-band EQ state: gains, enabled flag, preset name, persistence.

    The controller is engine-agnostic; :meth:`apply_to_engine` pushes the
    curve to any engine exposing ``set_af`` (mpv) and returns False for
    engines without EQ support (Qt).
    """

    def __init__(self, gains: list[float] | None = None,
                 enabled: bool = True, preset: str = "Flat") -> None:
        if gains is None:
            gains = PRESETS.get(str(preset), PRESETS["Flat"])
        self.gains: list[float] = validate_gains(gains)
        self.enabled = bool(enabled)
        self.preset = str(preset)

    # -- band manipulation ------------------------------------------------
    def set_band(self, index: int, db: float) -> float:
        """Set one band's gain (clamped); moving a band switches to Custom.

        Returns the clamped value.  Raises ValueError for a bad index.
        """
        i = int(index)
        if not 0 <= i < BAND_COUNT:
            raise ValueError(f"band index out of range: {index}")
        self.gains[i] = clamp_gain(db)
        if self.preset != "Custom":
            self.preset = "Custom"
        return self.gains[i]

    def set_preset(self, name: str) -> bool:
        """Apply a named preset; returns False when the name is unknown."""
        if name in PRESETS:
            self.gains = list(PRESETS[name])
            self.preset = str(name)
            return True
        log.debug("unknown EQ preset %r", name)
        return False

    def flat(self) -> None:
        """Reset all bands to 0 dB."""
        self.gains = [0.0] * BAND_COUNT

    # -- engine application --------------------------------------------------
    def apply_to_engine(self, engine) -> bool:
        """Push the curve to an engine; returns False when unsupported.

        mpv engines expose ``set_af``; engines without EQ support (e.g. the
        Qt engine) are a no-op returning False.  A disabled EQ clears the
        engine's af string.
        """
        try:
            caps = getattr(engine, "capabilities", None)
            if callable(caps) and "eq" not in caps():
                return False
            setter = getattr(engine, "set_af", None)
            if not callable(setter):
                return False
            af = "" if not self.enabled else mpv_superequalizer_string(self.gains)
            setter(af)
            return True
        except Exception as exc:
            log.warning("apply_to_engine failed: %s", exc)
            return False

    def to_mpv_string(self) -> str:
        """The mpv af string for the current gains (regardless of enabled)."""
        return mpv_superequalizer_string(self.gains)

    # -- persistence ------------------------------------------------------
    def to_dict(self) -> dict:
        """Serialize for settings persistence."""
        return {"enabled": self.enabled, "preset": self.preset,
                "gains": list(self.gains)}

    @classmethod
    def from_dict(cls, data: dict) -> "EQController":
        """Restore from a settings dict; tolerant of missing keys.

        Raises ValueError when the stored gains have a wrong length (the
        caller decides whether to fall back to defaults).
        """
        d = dict(data or {})
        gains = d.get("gains")
        if gains is None:
            preset = str(d.get("preset", "Flat"))
            gains = PRESETS.get(preset, PRESETS["Flat"])
        return cls(gains=gains,
                   enabled=bool(d.get("enabled", True)),
                   preset=str(d.get("preset", "Flat")))
