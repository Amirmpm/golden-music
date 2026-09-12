"""
Golden Music — Lyrics disk cache + sidecar write-back.

Two persistence layers sit between the embedded-tag probe and the network:

1. Disk cache  (~/.goldenmusic/lyrics_cache/*.json):
   keyed by normalized artist+title; stores the lrclib result plus the
   track duration it was fetched for. A hit whose stored duration differs
   by more than ±8 s from the current file is ignored (same-title
   covers/remixes must not steal each other's lyrics). Survives restarts,
   zero network. Capped at 500 entries (LRU by mtime) so it can't bloat.

2. Sidecar write-back (<basename>.lrc next to the audio file):
   after a successful online fetch the lyrics are ALSO written next to the
   track, so the next play is a local instant hit via find_local() — and
   any other player on the machine benefits too. Never overwrites an
   existing .lrc/.txt sidecar.

Both layers are best-effort: any I/O failure returns None / no-ops and
must never break playback or the lookup chain.
"""
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path

log = logging.getLogger("app.lyrics_cache")

MAX_CACHE_FILES = 500
MAX_CACHE_BYTES = 2 * 1024 * 1024   # same ceiling as lyrics.MAX_LYRICS_BYTES
DURATION_TOLERANCE_S = 8


def _cache_dir() -> Path:
    from config import config_path
    d = config_path().parent / "lyrics_cache"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log.debug(f"lyrics cache mkdir failed: {e}")
    return d


def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def _key(title: str, artist: str) -> str:
    raw = f"{_norm(artist)}|{_norm(title)}".encode("utf-8", "replace")
    return hashlib.sha1(raw).hexdigest()[:32]


def _to_lrc_text(ly) -> str:
    """Serialize a Lyrics object back to LRC/plain text for the sidecar."""
    out = []
    if getattr(ly, "synced", False):
        for ms, text in ly.lines:
            if ms is None or not (text or "").strip():
                continue
            ms = max(0, int(ms))
            mm, rest = divmod(ms, 60_000)
            ss, mmm = divmod(rest, 1000)
            out.append(f"[{mm:02d}:{ss:02d}.{mmm:03d}]{text}")
    else:
        for _ms, text in ly.lines:
            if (text or "").strip():
                out.append(text)
    return "\n".join(out) + ("\n" if out else "")


def load_cached(title: str, artist: str, duration_s=None):
    """Return cached Lyrics for artist+title (duration-validated), else None."""
    from lyrics import Lyrics, parse_lrc
    try:
        p = _cache_dir() / (_key(title, artist) + ".json")
        if not p.is_file() or p.stat().st_size > MAX_CACHE_BYTES:
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        if duration_s:
            try:
                if abs(int(data.get("duration", 0) or 0)
                       - int(duration_s)) > DURATION_TOLERANCE_S:
                    return None   # same title, different recording
            except (TypeError, ValueError):
                pass
        lines = data.get("lines") or []
        clean = []
        for item in lines:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                ms, text = item[0], str(item[1])
                clean.append((int(ms) if ms is not None else None, text))
        if not clean:
            return None
        ly = Lyrics(clean, synced=bool(data.get("synced")),
                    source="cache")
        # Touch for LRU
        try:
            os.utime(p, None)
        except OSError:
            pass
        # Re-validate synced payloads through the LRC parser so a
        # hand-edited cache file can't inject malformed timestamps.
        if ly.synced:
            lrc_text = _to_lrc_text(ly)
            if lrc_text.strip():
                ly = parse_lrc(lrc_text)
                ly.source = "cache"
        return ly
    except Exception as e:
        log.debug(f"lyrics cache load miss: {e}")
        return None


def save_cached(title: str, artist: str, duration_s, ly) -> bool:
    """Persist an online result to the disk cache. Returns success."""
    try:
        if ly is None or getattr(ly, "is_empty", lambda: True)():
            return False
        d = _cache_dir()
        payload = {
            "title": title or "",
            "artist": artist or "",
            "duration": int(duration_s or 0),
            "synced": bool(getattr(ly, "synced", False)),
            "lines": [[ms, text] for ms, text in ly.lines[:500]],
            "saved_at": int(time.time()),
        }
        blob = json.dumps(payload, ensure_ascii=False)
        if len(blob.encode("utf-8")) > MAX_CACHE_BYTES:
            return False
        tmp = d / (_key(title, artist) + ".tmp")
        final = d / (_key(title, artist) + ".json")
        tmp.write_text(blob, encoding="utf-8")
        tmp.replace(final)   # atomic-ish
        _prune(d)
        return True
    except Exception as e:
        log.debug(f"lyrics cache save failed: {e}")
        return False


def save_sidecar(filepath: str, ly) -> bool:
    """Write <basename>.lrc next to the audio file. Never overwrites."""
    try:
        if ly is None or getattr(ly, "is_empty", lambda: True)():
            return False
        base = os.path.splitext(filepath)[0]
        for ext in (".lrc", ".LRC", ".txt"):
            if os.path.isfile(base + ext):
                return False   # user content wins, always
        text = _to_lrc_text(ly)
        if not text.strip():
            return False
        if len(text.encode("utf-8")) > MAX_CACHE_BYTES:
            return False
        target = base + ".lrc"
        tmp = target + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, target)
        return True
    except Exception as e:
        log.debug(f"lyrics sidecar write failed: {e}")
        return False


def _prune(d: Path):
    """Keep the cache bounded: drop oldest files beyond MAX_CACHE_FILES."""
    try:
        files = [p for p in d.glob("*.json") if p.is_file()]
        if len(files) <= MAX_CACHE_FILES:
            return
        files.sort(key=lambda p: p.stat().st_mtime)
        for old in files[:len(files) - MAX_CACHE_FILES]:
            try:
                old.unlink()
            except OSError:
                pass
    except Exception:
        pass
