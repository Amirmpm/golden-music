"""
Golden Music — Track file info.

Reads technical metadata (format, duration, bitrate, sample rate, size)
via mutagen for the track-properties dialog. All getters are defensive:
a malformed file yields "Unknown" fields, never an exception.
"""
import os
from pathlib import Path

from config import fmt_time


def get_track_info(filepath: str) -> dict:
    """Return display-ready info dict for the properties dialog."""
    info = {
        "path": filepath,
        "filename": os.path.basename(filepath),
        "folder": os.path.dirname(filepath),
        "format": Path(filepath).suffix.lstrip(".").upper() or "?",
        "size": "",
        "duration": "",
        "bitrate": "",
        "sample_rate": "",
        "channels": "",
        "title": "",
        "artist": "",
        "album": "",
        "year": "",
    }
    try:
        st = os.stat(filepath)
        info["size"] = _human_size(st.st_size)
    except OSError:
        pass

    try:
        from mutagen import File as MutagenFile
        m = MutagenFile(filepath, easy=True)
        if m is not None:
            if m.info is not None:
                dur = int(getattr(m.info, "length", 0) or 0) * 1000
                info["duration"] = fmt_time(dur) if dur else ""
                br = getattr(m.info, "bitrate", 0)
                info["bitrate"] = f"{br // 1000} kbps" if br else ""
                sr = getattr(m.info, "sample_rate", 0)
                info["sample_rate"] = f"{sr:,} Hz" if sr else ""
                ch = getattr(m.info, "channels", 0)
                info["channels"] = str(ch) if ch else ""
            tags = m.tags or {}
            info["title"] = _first(tags.get("title"))
            info["artist"] = _first(tags.get("artist"))
            info["album"] = _first(tags.get("album"))
            year = (_first(tags.get("date")) or _first(tags.get("year")) or "")
            info["year"] = year[:4] if year else ""
    except Exception:
        pass  # unreadable file — show what we have
    return info


def _first(value):
    """mutagen easy=True returns lists; take the first non-empty item."""
    if isinstance(value, (list, tuple)):
        for v in value:
            if v:
                return str(v)
        return ""
    return str(value) if value else ""


def _human_size(nbytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if nbytes < 1024 or unit == "GB":
            return f"{nbytes:.1f} {unit}" if unit != "B" else f"{nbytes} B"
        nbytes /= 1024
    return ""
