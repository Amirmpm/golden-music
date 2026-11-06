"""Golden Music 3.0 — star ratings in audio tags (Qt-free, mutagen lazy).

Reads and writes the 0-5 star rating using each format's native field:

* MP3  -> ID3 ``POPM`` frame (email "goldenmusic@local", the de-facto
  Windows Media Player value scale);
* FLAC / OGG -> Vorbis comment ``RATING`` (0-5; values on a 0-100 scale
  from foreign taggers are tolerated on read);
* MP4 / M4A -> ``rate`` atom (0-100 = stars*20).

Both entry points follow the never-crash policy: ``write_rating``
returns True/False, ``read_rating`` returns 0-5 (0 when unknown or on
any error).
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("app.lyrics.rating")

__all__ = [
    "POPM_EMAIL", "stars_to_popm", "popm_to_stars", "stars_to_mp4",
    "mp4_to_stars", "vorbis_to_stars", "write_rating", "read_rating",
]

POPM_EMAIL = "goldenmusic@local"

# De-facto POPM quantization: 0=unrated, 1..31=1 star, 64=2, 128=3,
# 192=4, 255=5 (Windows Media Player / MusicBee / MediaMonkey agree).
_POPM_FORWARD = {0: 0, 1: 1, 2: 64, 3: 128, 4: 192, 5: 255}


def stars_to_popm(stars: int) -> int:
    """Map 0-5 stars onto the POPM byte (out-of-range values clamp)."""
    try:
        stars = int(stars)
    except (TypeError, ValueError):
        return 0
    return _POPM_FORWARD[max(0, min(5, stars))]


def popm_to_stars(value: int) -> int:
    """Map a POPM byte back to 0-5 stars with zone tolerance."""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return 0
    if v <= 0:
        return 0
    if v < 64:
        return 1
    if v < 128:
        return 2
    if v < 192:
        return 3
    if v < 225:
        return 4
    return 5


def stars_to_mp4(stars: int) -> int:
    """Map 0-5 stars onto the MP4 0-100 rate scale."""
    try:
        stars = int(stars)
    except (TypeError, ValueError):
        return 0
    return max(0, min(5, stars)) * 20


def mp4_to_stars(value) -> int:
    """Map an MP4 rate value (0-100, int or str) back to 0-5 stars."""
    try:
        v = int(str(value).strip() or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, min(5, round(v / 20)))


def vorbis_to_stars(value) -> int:
    """Map a Vorbis RATING comment to 0-5 stars.

    Native scale is 0-5; foreign taggers write 0-100 (or 0-10), which is
    tolerated by scaling anything above 5 down to stars.
    """
    try:
        v = int(str(value).strip() or 0)
    except (TypeError, ValueError):
        return 0
    if v <= 0:
        return 0
    if v <= 5:
        return v
    if v >= 100:
        return 5
    return max(1, min(5, round(v / 20)))


def _clamp_stars(stars) -> int:
    try:
        return max(0, min(5, int(stars)))
    except (TypeError, ValueError):
        return 0


def write_rating(path: str, stars: int) -> bool:
    """Write the 0-5 star rating into the file's native tag field.

    Returns True on success, False on any failure — never raises.
    """
    stars = _clamp_stars(stars)
    try:
        if not path or not os.path.isfile(path):
            return False
        ext = os.path.splitext(path)[1].lower()
        if ext == ".mp3":
            return _write_mp3(path, stars)
        if ext in (".m4a", ".mp4"):
            return _write_mp4(path, stars)
        return _write_vorbis(path, stars)
    except Exception as e:  # never-crash policy
        log.warning("write_rating failed %s: %s", path, e)
        return False


def read_rating(path: str) -> int:
    """Read the 0-5 star rating (0 when unrated, missing or on error)."""
    try:
        if not path or not os.path.isfile(path):
            return 0
        ext = os.path.splitext(path)[1].lower()
        if ext == ".mp3":
            return _read_mp3(path)
        if ext in (".m4a", ".mp4"):
            return _read_mp4(path)
        return _read_vorbis(path)
    except Exception as e:  # never-crash policy
        log.debug("read_rating failed %s: %s", path, e)
        return 0


# ---------------------------------------------------------------------------
# MP3 / POPM
# ---------------------------------------------------------------------------

def _write_mp3(path: str, stars: int) -> bool:
    from mutagen.id3 import ID3, POPM, ID3NoHeaderError   # lazy

    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    frame = None
    for f in tags.getall("POPM"):
        if getattr(f, "email", "") == POPM_EMAIL:
            frame = f
            break
    if frame is None:
        frame = POPM(email=POPM_EMAIL, rating=0, count=0)
        tags.add(frame)
    frame.rating = stars_to_popm(stars)
    tags.save(path)
    return True


def _read_mp3(path: str) -> int:
    from mutagen.id3 import ID3, ID3NoHeaderError   # lazy

    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        return 0
    best = 0
    for f in tags.getall("POPM"):
        best = max(best, popm_to_stars(int(getattr(f, "rating", 0) or 0)))
    return best


# ---------------------------------------------------------------------------
# MP4 / rate
# ---------------------------------------------------------------------------

def _write_mp4(path: str, stars: int) -> bool:
    from mutagen.mp4 import MP4   # lazy

    m = MP4(path)
    if m.tags is None:
        m.add_tags()
    # mutagen renders 'rate' as a text atom; the 0-100 value is preserved
    # verbatim for tools that read the numeric payload.
    m.tags["rate"] = [str(stars_to_mp4(stars))]
    m.save()
    return True


def _read_mp4(path: str) -> int:
    from mutagen.mp4 import MP4   # lazy

    m = MP4(path)
    if not m.tags:
        return 0
    val = m.tags.get("rate")
    if isinstance(val, (list, tuple)):
        val = val[0] if val else None
    return mp4_to_stars(val) if val is not None else 0


# ---------------------------------------------------------------------------
# FLAC / OGG / Vorbis comments
# ---------------------------------------------------------------------------

def _write_vorbis(path: str, stars: int) -> bool:
    from mutagen import File as MutagenFile   # lazy

    m = MutagenFile(path)
    if m is None or not callable(getattr(m, "save", None)):
        return False
    if m.tags is None:
        add_tags = getattr(m, "add_tags", None)
        if not callable(add_tags):
            return False
        add_tags()
    m.tags["RATING"] = [str(stars)]
    m.save()
    return True


def _read_vorbis(path: str) -> int:
    from mutagen import File as MutagenFile   # lazy

    m = MutagenFile(path)
    if m is None or not m.tags:
        return 0
    try:
        val = m.tags["RATING"]   # VComment lookups are case-insensitive
    except Exception:
        return 0
    if isinstance(val, (list, tuple)):
        val = val[0] if val else None
    return vorbis_to_stars(val) if val is not None else 0
