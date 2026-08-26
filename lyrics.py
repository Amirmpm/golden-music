"""
Golden Music — Lyrics provider.

Order of resolution for a track:
  1. Sidecar file: <audio basename>.lrc next to the audio file (synced or plain)
  2. Embedded USLT/SYLT tag (ID3) / ©lyr (MP4) via mutagen
  3. Online: lrclib.net public API (free, no key) using artist+title+duration

Returns a Lyrics object: plain lines, optionally with per-line timestamps.
The GUI highlights the active line during playback when synced data exists.
"""
import json
import logging
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger("app.lyrics")

LRC_TS = re.compile(r"\[(\d{1,2}):(\d{1,2})(?:[.:](\d{1,3}))?\]")


class Lyrics:
    def __init__(self, lines, synced=False, source=""):
        self.lines = lines            # [(time_ms_or_None, text), ...]
        self.synced = synced
        self.source = source

    def is_empty(self):
        return not self.lines

    def line_for_time(self, ms: int) -> int:
        """Index of the active line at playback position `ms` (-1 before first)."""
        if not self.synced:
            return -1
        idx = -1
        for i, (t, _txt) in enumerate(self.lines):
            if t is not None and t <= ms:
                idx = i
            else:
                break
        return idx


def parse_lrc(text: str):
    """Parse LRC content → (lines, synced). Handles multi-timestamp lines."""
    entries = {}
    plain = []
    any_ts = False
    for raw in text.splitlines():
        stamps = LRC_TS.findall(raw)
        content = LRC_TS.sub("", raw).strip()
        if not content:
            continue
        if stamps:
            any_ts = True
            for m in LRC_TS.finditer(raw):
                mm, ss, frac = m.groups()
                ms = int(mm) * 60_000 + int(ss) * 1000 + int((frac or "0").ljust(3, "0")[:3])
                entries.setdefault(ms, content)
        else:
            # skip metadata tags like [ar:], [ti:]
            if re.match(r"^\[(a[rl]|ti|al|by|offset|length|re|ve):", raw.strip(), re.I):
                continue
            plain.append(content)
    if any_ts and entries:
        lines = sorted(entries.items())
        return Lyrics([(t, txt) for t, txt in lines], synced=True, source="lrc")
    return Lyrics([(None, ln) for ln in plain], synced=False, source="text")


def find_local(filepath: str):
    """Sidecar .lrc / .txt next to the audio file."""
    base = os.path.splitext(filepath)[0]
    for ext in (".lrc", ".LRC", ".txt"):
        p = base + ext
        if os.path.isfile(p):
            try:
                parsed = parse_lrc(Path(p).read_text(encoding="utf-8", errors="replace"))
                parsed.source = os.path.basename(p)
                return parsed
            except OSError as e:
                log.warning(f"sidecar read failed {p}: {e}")
    return None


def find_embedded(filepath: str):
    """Embedded unsynced lyrics from ID3 USLT or MP4 ©lyr."""
    try:
        from mutagen import File as MutagenFile
        from mutagen.id3 import ID3, USLT
        from mutagen.mp4 import MP4
        if filepath.lower().endswith(".mp3"):
            tags = ID3(filepath)
            for key in tags.keys():
                if key.startswith("USLT"):
                    text = str(tags[key])
                    if text.strip():
                        parsed = parse_lrc(text)
                        parsed.source = "embedded"
                        return parsed
        elif filepath.lower().endswith((".m4a", ".mp4")):
            m = MP4(filepath)
            lyr = (m.tags or {}).get("\xa9lyr")
            if lyr and str(lyr[0]).strip():
                parsed = parse_lrc(str(lyr[0]))
                parsed.source = "embedded"
                return parsed
        else:
            m = MutagenFile(filepath)
            if m is not None:
                for k in ("lyrics", "LYRICS", "LYRIC"):
                    vals = (m.tags or {}).get(k)
                    if vals:
                        parsed = parse_lrc(str(vals[0]))
                        parsed.source = "embedded"
                        return parsed
    except Exception as e:
        log.debug(f"embedded lyrics probe failed: {e}")
    return None


def _duration_of(filepath: str):
    try:
        from mutagen import File as MutagenFile
        m = MutagenFile(filepath)
        if m is not None and m.info is not None:
            return int(getattr(m.info, "length", 0) or 0)
    except Exception:
        pass
    return None


def fetch_online(title: str, artist: str, duration_s=None, timeout=8):
    """Query lrclib.net (free public lyrics API). Returns synced lyrics when
    available, otherwise plain. Network errors return None quietly."""
    if not title:
        return None
    q = urllib.parse.quote(title)
    artist_q = urllib.parse.quote(artist or "")
    headers = {"User-Agent": "GoldenMusic/1.2 (desktop music player)"}
    try:
        # 1st: exact search including duration
        url = ("https://lrclib.net/api/search?"
               f"track_name={q}&artist_name={artist_q}")
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            results = json.loads(r.read().decode("utf-8"))
        if not results and duration_s:
            # 2nd: bare query
            url = f"https://lrclib.net/api/search?q={q}"
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                results = json.loads(r.read().decode("utf-8"))
        if not results:
            return None
        best = results[0]
        if duration_s:
            for cand in results[:5]:
                if abs(int(cand.get("duration", 0)) - duration_s) <= 5:
                    best = cand
                    break
        synced = best.get("syncedLyrics") or ""
        plain = best.get("plainLyrics") or ""
        if synced.strip():
            parsed = parse_lrc(synced)
            parsed.source = "lrclib.net"
            return parsed
        if plain.strip():
            lines = [(None, ln) for ln in plain.splitlines() if ln.strip()]
            return Lyrics(lines, synced=False, source="lrclib.net")
    except Exception as e:
        log.info(f"online lyrics lookup failed: {e}")
    return None


def get_lyrics(filepath: str, title: str = "", artist: str = "",
               allow_online=True):
    """Full resolution chain. Returns a Lyrics object (may be empty)."""
    ly = find_local(filepath)
    if ly and not ly.is_empty():
        return ly
    ly = find_embedded(filepath)
    if ly and not ly.is_empty():
        return ly
    if allow_online:
        t = title or Path(filepath).stem
        ly = fetch_online(t, artist, _duration_of(filepath))
        if ly and not ly.is_empty():
            return ly
    return Lyrics([], synced=False, source="")
