"""
Golden Music — Duplicate track finder.

Groups library tracks that look like the same song. Matching is metadata
first (normalized title + artist), falling back to a normalized filename
when tags are missing. Duration (±2 s) refines groups when available so
different recordings sharing a title don't get flagged.

Normalization strips numbers, bracketed noise, and punctuation so
"01 - Song.mp3" and "Song (Live Copy).mp3" with equal tags still match.
"""
import os
import re
import unicodedata

from config import fmt_time


def _normalize(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text).casefold()
    text = re.sub(r"[\[\(\{].*?[\]\)\}]", " ", text)   # (…), […], {…}
    text = re.sub(r"[^\w\s]", " ", text)               # punctuation
    text = re.sub(r"\b\d{1,3}\b", " ", text)           # track numbers
    return re.sub(r"\s+", " ", text).strip()


def _duration_of(filepath: str):
    try:
        from mutagen import File as MutagenFile
        m = MutagenFile(filepath)
        if m is not None and m.info is not None:
            return int(getattr(m.info, "length", 0) or 0)
    except Exception:
        pass
    return None


def _key_for(path: str, tag_cache: dict, durations: dict):
    """Grouping key: (norm_title, norm_artist[, duration_bucket]) — or
    filename fallback when tags are absent."""
    title, artist = "", ""
    if path in tag_cache:
        title, artist = tag_cache[path]
    if _normalize(title):
        key = (_normalize(title), _normalize(artist))
    else:
        stem = os.path.splitext(os.path.basename(path))[0]
        # strip common 'NN - ' prefixes before normalizing the filename
        stem = re.sub(r"^\s*\d{1,3}\s*[-._]\s*", "", stem)
        key = ("__file__", _normalize(stem))
    dur = durations.get(path)
    if dur:
        # 30-second buckets make the match tolerant to small encode diffs
        key = key + (dur // 30,)
    return key


def find_duplicates(paths, tag_cache: dict, progress=None):
    """Return list of duplicate groups; each group is a list of ≥2 paths.

    `progress(done, total)` is optional and called periodically.
    """
    paths = [p for p in paths if os.path.exists(p)]
    total = len(paths)
    durations = {}
    for i, p in enumerate(paths):
        d = _duration_of(p)
        if d:
            durations[p] = d
        if progress and (i % 25 == 0 or i == total - 1):
            progress(i + 1, total)

    groups = {}
    for p in paths:
        groups.setdefault(_key_for(p, tag_cache, durations), []).append(p)

    dupes = [g for g in groups.values() if len(g) >= 2]
    # Stable, readable ordering inside each group
    for g in dupes:
        g.sort(key=lambda x: os.path.basename(x).lower())
    dupes.sort(key=lambda g: os.path.basename(g[0]).lower())
    return dupes


def group_label(group, tag_cache: dict) -> str:
    """Human label for one duplicate group."""
    if group:
        t, a = tag_cache.get(group[0], ("", ""))
        if t:
            return f"{t} — {a}" if a else t
    return os.path.basename(group[0]) if group else ""


def fmt_duration(path) -> str:
    """'m:ss' for a path using a quick mutagen probe ('' when unknown)."""
    try:
        from mutagen import File as MutagenFile
        m = MutagenFile(path)
        if m is not None and m.info is not None:
            return fmt_time(int(m.info.length * 1000))
    except Exception:
        pass
    return ""
