"""Golden Music 3.0 — loudness normalization (Qt-free, mutagen optional).

- :func:`read_replaygain_tags` reads ReplayGain tags across formats via
  mutagen (lazy import): MP3 TXXX frames, FLAC/OGG/Opus Vorbis comments and
  MP4 freeform atoms (``----:com.apple.iTunes:replaygain_*``).
- :class:`Normalizer` computes the gain to apply for a track in
  'tags' mode (ReplayGain + preamp, clamped so ``peak * linear(gain)``
  never exceeds the ceiling — classic ReplayGain clamping math) or in
  'analysis' mode using an injected analyzer callable (the app wires
  python-miniaudio).  Results ride on an injected cache dict.
"""
import array
import logging
import math
import struct

log = logging.getLogger("app.normalize")

#: Default loudness target for analysis mode (LUFS-ish).
DEFAULT_TARGET_LUFS = -18.0

#: Digital-silence floor used by the RMS/LUFS approximation.
SILENCE_FLOOR_DB = -70.0

#: Canonical ReplayGain tag names we look for.
RG_KEYS = ("replaygain_track_gain", "replaygain_album_gain",
           "replaygain_track_peak", "replaygain_album_peak")

#: Supported normalizer modes.
MODES = ("off", "tags", "analysis")


def parse_db_string(text) -> float | None:
    """Parse tag values like ``'-6.20 dB'`` / ``'+3'`` / ``'0.98'``.

    Returns the float value or None when unparseable.  Handles bytes,
    bytearrays and None.
    """
    if text is None:
        return None
    if isinstance(text, (bytes, bytearray)):
        text = bytes(text).decode("utf-8", "replace")
    s = str(text).strip()
    if not s:
        return None
    s = s.lower().replace("db", "").replace(" ", "").strip()
    if not s or s in {"-", "+", "."}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _first_text(value) -> str | None:
    """First textual value from a tag value.

    Handles: lists/tuples (Vorbis comments), bytes (MP4FreeForm), ID3
    frames (``.text``) and plain strings.  Never raises.
    """
    try:
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            for item in value:
                text = _first_text(item)
                if text is not None:
                    return text
            return None
        if isinstance(value, (bytes, bytearray)):
            text = bytes(value).decode("utf-8", "replace").strip()
            return text or None
        if hasattr(value, "text"):
            return _first_text(list(value.text))
        if hasattr(value, "data") and not isinstance(value, str):
            return _first_text(value.data)
        text = str(value).strip()
        return text or None
    except Exception as exc:  # defensive: exotic tag objects
        log.debug("tag value decode failed: %s", exc)
        return None


def _collect_rg_values(tags) -> dict[str, str]:
    """Extract ``replaygain_*`` key/value strings from any mutagen tags object.

    Handles Vorbis comments (FLAC/OGG/Opus: dict-like with list values),
    ID3 TXXX frames (keys look like ``TXXX:replaygain_track_gain``) and MP4
    freeform atoms (``----:com.apple.iTunes:replaygain_track_gain``).
    """
    out: dict[str, str] = {}
    if tags is None:
        return out
    try:
        items = list(tags.items())
    except Exception as exc:
        log.debug("cannot iterate tags %r: %s", type(tags).__name__, exc)
        return out
    for key, value in items:
        k = str(key).strip().lower()
        if ":" in k:
            # "TXXX:replaygain_track_gain" / "----:com.apple.iTunes:replaygain_track_gain"
            k = k.rsplit(":", 1)[-1].strip()
        if k not in RG_KEYS:
            continue
        text = _first_text(value)
        if text is not None and k not in out:
            out[k] = text
    return out


def read_replaygain_tags(path) -> dict:
    """Read ReplayGain tags from any format mutagen understands.

    Returns ``{'track_gain_db', 'album_gain_db', 'track_peak', 'album_peak'}``
    with float values or None when missing/unreadable.  mutagen is imported
    lazily; a missing mutagen yields an all-None dict.
    """
    result = {"track_gain_db": None, "album_gain_db": None,
              "track_peak": None, "album_peak": None}
    try:
        import mutagen  # lazy: optional dependency
    except ImportError:
        log.debug("mutagen not installed; replaygain tags unavailable")
        return result
    try:
        mf = mutagen.File(str(path), easy=False)
    except Exception as exc:
        log.debug("mutagen could not read %s: %s", path, exc)
        return result
    if mf is None:
        return result
    tags = getattr(mf, "tags", None)
    values = _collect_rg_values(tags)
    if values:
        result["track_gain_db"] = parse_db_string(values.get("replaygain_track_gain"))
        result["album_gain_db"] = parse_db_string(values.get("replaygain_album_gain"))
        result["track_peak"] = parse_db_string(values.get("replaygain_track_peak"))
        result["album_peak"] = parse_db_string(values.get("replaygain_album_peak"))
    return result


def compute_target_gain(current_lufs_or_rms: float,
                        target: float = DEFAULT_TARGET_LUFS) -> float:
    """dB of gain needed to move ``current`` to ``target`` (target - current)."""
    return float(target) - float(current_lufs_or_rms)


def _as_float_samples(samples) -> list[float]:
    """Normalise sample input to a list of floats.

    bytes/bytearray and ``array('h')`` are int16 LE PCM (divided by 32768);
    other sequences are used as-is (documented as -1..1 floats).  Integer
    sequences with values beyond ±1 are treated as int16 PCM for
    convenience (the app's analyzer hands int16 buffers around).
    """
    if isinstance(samples, (bytes, bytearray)):
        data = bytes(samples)
        n = len(data) // 2
        if n == 0:
            return []
        return [s / 32768.0 for s in struct.unpack("<%dh" % n, data[: n * 2])]
    if isinstance(samples, array.array):
        if samples.typecode == "h":
            return [float(v) / 32768.0 for v in samples]
        return [float(v) for v in samples]
    values = [float(v) for v in samples]
    if values and all(v.is_integer() for v in values) \
            and max(abs(v) for v in values) > 1.0:
        values = [v / 32768.0 for v in values]
    return values


def rms_lufs_approx(samples, sample_rate: float) -> float:
    """Energy-based LUFS approximation for a mono sample frame.

    ``LKFS ~= -0.691 + 10*log10(mean_square)`` (the -0.691 dB is the
    K-weighting RMS offset for a mono signal).  Pure python; numpy is used
    only when the input already is an ndarray (speed path).  Silence is
    clamped to :data:`SILENCE_FLOOR_DB`.
    """
    try:
        sr = float(sample_rate)
    except (TypeError, ValueError):
        return SILENCE_FLOOR_DB
    if sr <= 0:
        return SILENCE_FLOOR_DB
    mean_square = None
    try:
        import numpy as _np  # optional speed path
    except ImportError:
        _np = None
    if _np is not None and isinstance(samples, _np.ndarray):
        arr = samples.astype("float64").ravel()
        if arr.size == 0:
            return SILENCE_FLOOR_DB
        mean_square = float(_np.dot(arr, arr) / arr.size)
    if mean_square is None:
        values = _as_float_samples(samples)
        if not values:
            return SILENCE_FLOOR_DB
        total = 0.0
        for v in values:
            total += v * v
        mean_square = total / len(values)
    if mean_square <= 0.0:
        return SILENCE_FLOOR_DB
    lufs = -0.691 + 10.0 * math.log10(mean_square)
    return max(SILENCE_FLOOR_DB, lufs)


class Normalizer:
    """ReplayGain-style loudness normalizer.

    mode ``'off'``      -> gain_for always 0.0.
    mode ``'tags'``     -> replaygain track gain + preamp, clamped so
                           ``peak * linear(gain)`` stays under the ceiling
                           (classic ReplayGain clamping math).
    mode ``'analysis'`` -> gain = target_lufs - measured loudness + preamp,
                           peak-clamped the same way; the analyzer callable
                           is injected by the app (wired to python-miniaudio)
                           and returns ``(loudness_db, peak)`` or just a
                           loudness number.

    Caching rides on an injected dict (``cache=``); tag reads go through an
    injectable ``tag_reader=`` so the app can swap in fakes/DB lookups.
    """

    def __init__(self, mode: str = "off",
                 preamp_db: float = 0.0,
                 ceiling_db: float = 0.0,
                 target_lufs: float = DEFAULT_TARGET_LUFS,
                 cache: dict | None = None,
                 tag_reader=None,
                 analyzer=None) -> None:
        if mode in MODES:
            self.mode: str = mode
        else:
            log.warning("unknown normalizer mode %r; using 'off'", mode)
            self.mode = "off"
        self.preamp_db = max(-20.0, min(20.0, float(preamp_db)))
        self.ceiling_db = min(0.0, float(ceiling_db))  # never above 0 dBFS
        self.target_lufs = float(target_lufs)
        self._cache = cache if cache is not None else {}
        self._tag_reader = tag_reader or read_replaygain_tags
        self._analyzer = analyzer

    # -- gain computation -------------------------------------------------
    def gain_for(self, path) -> float:
        """dB gain for ``path`` (cached in the injected dict).

        Returns 0.0 when disabled or when no usable measurement exists.
        """
        key = str(path)
        cached = self._cache.get(key)
        if cached is not None:
            try:
                return float(cached)
            except (TypeError, ValueError):
                log.debug("bad cached gain for %s: %r", key, cached)
        gain = self._compute_gain(key)
        self._cache[key] = gain
        return gain

    def _compute_gain(self, path: str) -> float:
        if self.mode == "off":
            return 0.0
        if self.mode == "tags":
            try:
                tags = self._tag_reader(path) or {}
            except Exception as exc:
                log.warning("tag read failed for %s: %s", path, exc)
                return 0.0
            track_gain = tags.get("track_gain_db")
            if track_gain is None:
                return 0.0
            return self._clamp_to_ceiling(float(track_gain) + self.preamp_db,
                                          tags.get("track_peak"))
        # analysis mode
        if not callable(self._analyzer):
            log.debug("analysis mode requested without an analyzer; no gain")
            return 0.0
        try:
            result = self._analyzer(path)
        except Exception as exc:
            log.warning("analyzer failed for %s: %s", path, exc)
            return 0.0
        peak = None
        if isinstance(result, (tuple, list)):
            if not result:
                return 0.0
            loudness = float(result[0])
            if len(result) > 1 and result[1] is not None:
                peak = float(result[1])
        else:
            loudness = float(result)
        gain = compute_target_gain(loudness, self.target_lufs) + self.preamp_db
        return self._clamp_to_ceiling(gain, peak)

    def _clamp_to_ceiling(self, gain_db: float, peak) -> float:
        """Classic ReplayGain clamping: ``peak * linear(gain) <= ceiling``."""
        if peak is None:
            return gain_db
        try:
            peak = float(peak)
        except (TypeError, ValueError):
            return gain_db
        if peak <= 0:
            return gain_db
        max_db = self.ceiling_db - 20.0 * math.log10(peak)
        if gain_db > max_db:
            log.debug("clamping gain %.2f -> %.2f dB (peak %.3f, ceiling %.1f)",
                      gain_db, max_db, peak, self.ceiling_db)
            return max_db
        return gain_db

    # -- persistence ------------------------------------------------------
    def to_dict(self) -> dict:
        """Serialize for settings persistence."""
        return {"mode": self.mode, "preamp_db": self.preamp_db,
                "ceiling_db": self.ceiling_db, "target_lufs": self.target_lufs}

    @classmethod
    def from_dict(cls, data: dict) -> "Normalizer":
        """Restore from a settings dict; tolerant of missing/invalid keys."""
        d = dict(data or {})
        try:
            preamp = float(d.get("preamp_db", 0.0))
        except (TypeError, ValueError):
            preamp = 0.0
        try:
            ceiling = float(d.get("ceiling_db", 0.0))
        except (TypeError, ValueError):
            ceiling = 0.0
        try:
            target = float(d.get("target_lufs", DEFAULT_TARGET_LUFS))
        except (TypeError, ValueError):
            target = DEFAULT_TARGET_LUFS
        return cls(mode=str(d.get("mode", "off")), preamp_db=preamp,
                   ceiling_db=ceiling, target_lufs=target)
