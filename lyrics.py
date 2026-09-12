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

# A real .lrc / embedded lyrics payload is a few KB at most. Anything far
# larger is a bloated or maliciously crafted file — skip it so a giant
# sidecar next to one track can't blow up memory when its album is browsed.
MAX_LYRICS_BYTES = 2 * 1024 * 1024   # 2 MiB


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
        try:
            if not os.path.isfile(p) or os.path.getsize(p) > MAX_LYRICS_BYTES:
                continue
            parsed = parse_lrc(Path(p).read_text(encoding="utf-8", errors="replace"))
            parsed.source = os.path.basename(p)
            return parsed
        except OSError as e:
            log.warning(f"sidecar read failed {p}: {e}")
    return None


def find_embedded(filepath: str):
    """Embedded lyrics: ID3 SYLT (synced) first, then USLT, MP4 ©lyr,
    WM/Lyrics (WMA), or generic Vorbis/FLAC lyrics tags.

    SYLT frames carry (text, timestamp) tuples straight from the tagger —
    exact sync with zero guessing, so they outrank everything embedded.
    """
    try:
        from mutagen import File as MutagenFile
        from mutagen.mp4 import MP4
        if filepath.lower().endswith(".mp3"):
            from mutagen.id3 import ID3
            try:
                tags = ID3(filepath)
            except Exception:
                return None
            for key in tags.keys():
                if key.startswith("SYLT"):
                    parsed = _parse_sylt_frame(tags[key])
                    if parsed is not None and not parsed.is_empty():
                        parsed.source = "embedded·sync"
                        return parsed
            for key in tags.keys():
                if key.startswith("USLT"):
                    text = str(tags[key].text) \
                        if hasattr(tags[key], "text") else str(tags[key])
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
        elif filepath.lower().endswith(".wma"):
            from mutagen.asf import ASF
            try:
                audio = ASF(filepath)
                vals = audio.get("WM/Lyrics")
                if vals and str(vals[0]).strip():
                    parsed = parse_lrc(str(vals[0]))
                    parsed.source = "embedded"
                    return parsed
            except Exception:
                pass
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


def _parse_sylt_frame(frame) -> "Lyrics | None":
    """Decode an ID3 SYLT frame → synced Lyrics.

    frame.text = [(text, timestamp_ms_or_mpegframes), ...].
    format==1 → milliseconds already; format==2 → MPEG frames
    (1/75 s each — the ID3v2 spec's SYLT unit).
    """
    try:
        entries = getattr(frame, "text", None) or []
        fmt = int(getattr(frame, "format", 1) or 1)
        lines = []
        for item in entries:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                text, ts = str(item[0]).strip(), item[1]
            else:
                continue
            if not text:
                continue
            try:
                ts = int(ts)
            except (TypeError, ValueError):
                continue
            ms = ts if fmt == 1 else int(ts * 1000 / 75)
            lines.append((max(0, ms), text))
        if not lines:
            return None
        lines.sort(key=lambda e: e[0])
        # Collapse duplicate timestamps (taggers sometimes emit the same
        # line twice) — keep the first occurrence.
        deduped = []
        seen_ts = set()
        for ms, text in lines:
            if ms in seen_ts and deduped and deduped[-1][1] == text:
                continue
            seen_ts.add(ms)
            deduped.append((ms, text))
        return Lyrics(deduped, synced=True, source="embedded·sync")
    except Exception as e:
        log.debug(f"SYLT decode failed: {e}")
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


def _title_variants(title: str):
    """Yield cleaned title candidates, most-specific first.

    Taggers stuff titles with edition/remix/feat clutter
    ("California Love (Original Version)", "Song - Remix") that lrclib
    won't match verbatim — each variant gets its own search pass.
    """
    seen = set()

    def _emit(s: str):
        # Edge trim is dash/tilde/whitespace ONLY — stripping brackets here
        # would eat the closing paren of a balanced title like
        # "California Love (Original Version)". Unbalanced leftovers from
        # partial strips are collapsed explicitly below.
        s = re.sub(r"\s+", " ", s or "").strip().strip("-–—~").strip()
        s = re.sub(r"\s*[\(\[]\s*$", "", s).strip()
        s = re.sub(r"^\s*[\)\]]\s*", "", s).strip()
        if s and s.lower() not in seen:
            seen.add(s.lower())
            return s
        return None

    base = title or ""
    v = _emit(base)
    if v:
        yield v
    # Strip parenthetical/bracket suffixes: (Original Version), [Official]
    no_paren = re.sub(r"\s*[\(\[].*?[\)\]]\s*", " ", base).strip()
    v = _emit(no_paren)
    if v:
        yield v
    # Strip dash suffixes: "Song - Remix", "Song — Live"
    dash_cut = re.split(r"\s+[-–—]\s+", base, maxsplit=1)[0]
    v = _emit(dash_cut)
    if v:
        yield v
    # Strip featured-artist clauses: "Song feat. X", "Song ft X"
    feat_cut = re.split(r"\s+(?:feat\.?|ft\.?|featuring|with)\s+",
                        base, flags=re.I)[0]
    v = _emit(feat_cut)
    if v:
        yield v


def _lyrics_from_result(best: dict):
    """Build a Lyrics from one lrclib result dict (synced preferred)."""
    if not isinstance(best, dict):
        return None
    synced = best.get("syncedLyrics") or ""
    plain = best.get("plainLyrics") or ""
    if synced.strip():
        parsed = parse_lrc(synced)
        parsed.source = "lrclib.net"
        return parsed
    if plain.strip():
        lines = [(None, ln) for ln in plain.splitlines() if ln.strip()]
        return Lyrics(lines, synced=False, source="lrclib.net")
    return None


def _pick_best(results, duration_s=None):
    """Pick the best lrclib candidate.

    Duration match (±8 s) wins — same-titled covers/remixes are the top
    cause of wrong-lyrics. Among duration matches (or when duration is
    unknown), synced lyrics outrank plain.
    """
    cands = [c for c in (results or [])[:10]
             if isinstance(c, dict)
             and (c.get("syncedLyrics") or c.get("plainLyrics"))]
    if not cands:
        return None
    if duration_s:
        for want_synced in (True, False):
            for c in cands:
                try:
                    dt = abs(int(c.get("duration", 0) or 0) - duration_s)
                except (TypeError, ValueError):
                    continue
                if dt <= 8 and bool(c.get("syncedLyrics")) == want_synced:
                    return c
    for c in cands:
        if c.get("syncedLyrics"):
            return c
    return cands[0]


def fetch_online(title: str, artist: str, duration_s=None, timeout=8):
    """Query lrclib.net (free public lyrics API). Returns synced lyrics when
    available, otherwise plain. Network errors return None quietly.

    Strategy: exact /api/get (artist+title+duration, server-side match)
    first, then per-title-variant searches — artist-qualified, then bare
    (artist tags in personal libraries are often folder names like "2",
    which actively poison the qualified search).
    """
    variants = [v for v in _title_variants(title or "") if v]
    if not variants:
        return None
    artist = (artist or "").strip()
    if artist.lower() in ("", "unknown artist", "unknown"):
        artist = ""
    headers = {"User-Agent": "GoldenMusic/1.2 (desktop music player)"}

    def _get_json(url):
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read(MAX_LYRICS_BYTES).decode("utf-8"))

    try:
        # Pass 0: exact endpoint — cheapest + most accurate single shot.
        if duration_s and artist:
            try:
                url = ("https://lrclib.net/api/get?" +
                       urllib.parse.urlencode(
                           {"artist_name": artist,
                            "track_name": variants[0],
                            "duration": int(duration_s)}))
                res = _get_json(url)
                if isinstance(res, dict):
                    ly = _lyrics_from_result(res)
                    if ly is not None and not ly.is_empty():
                        return ly
            except Exception as e:
                log.debug(f"lrclib /get miss: {e}")
        # Passes 1..N: search per title variant.
        for tv in variants:
            results = None
            if artist:
                url = ("https://lrclib.net/api/search?" +
                       urllib.parse.urlencode(
                           {"track_name": tv, "artist_name": artist}))
                try:
                    results = _get_json(url)
                except Exception as e:
                    log.debug(f"lrclib search miss: {e}")
            if not results:
                # Bare query — no artist filter to be poisoned by.
                url = ("https://lrclib.net/api/search?" +
                       urllib.parse.urlencode({"q": tv}))
                try:
                    results = _get_json(url)
                except Exception as e:
                    log.debug(f"lrclib bare search miss: {e}")
                    continue
            if not results:
                continue
            ly = _lyrics_from_result(_pick_best(results, duration_s))
            if ly is not None and not ly.is_empty():
                return ly
    except Exception as e:
        log.info(f"online lyrics lookup failed: {e}")
    return None


def get_lyrics(filepath: str, title: str = "", artist: str = "",
               allow_online=True):
    """Full resolution chain. Returns a Lyrics object (may be empty).

    1. Sidecar .lrc/.txt next to the audio file
    2. Embedded SYLT (synced) → USLT → MP4 ©lyr → WM/Lyrics → Vorbis tags
    3. Disk lyrics cache (~/.goldenmusic/lyrics_cache, keyed by
       artist+title+duration) — survives restarts, zero network
    4. Online (lrclib.net), then saved to the disk cache AND written back
       as a <basename>.lrc sidecar so the next play is a local instant hit
    """
    import lrc_cache as _cache
    ly = find_local(filepath)
    if ly and not ly.is_empty():
        return ly
    ly = find_embedded(filepath)
    if ly and not ly.is_empty():
        return ly
    if allow_online:
        t = title or Path(filepath).stem
        dur = _duration_of(filepath)
        ly = _cache.load_cached(t, artist, dur)
        if ly is not None and not ly.is_empty():
            return ly   # source already "cache"
        ly = fetch_online(t, artist, dur)
        if ly and not ly.is_empty():
            _cache.save_cached(t, artist, dur, ly)
            _cache.save_sidecar(filepath, ly)
            return ly
    return Lyrics([], synced=False, source="")
