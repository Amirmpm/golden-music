"""
Golden Music — Play statistics.

Persists to stats.json next to the config:
  total_seconds   lifetime listening time
  play_counts     {path: count}
  artist_counts   {artist: seconds}
  sessions        number of app runs
  first_played    ISO date of the very first recorded play

Debounced save: counters update in memory every tick, flushed to disk
every 30 s and on shutdown. User data is NEVER touched by updates.
"""
import json
import logging
import math
import time
from datetime import date
from pathlib import Path

log = logging.getLogger("app.stats")

# Same hardening class as the config loader in config.py: a huge or tampered
# store must never bloat memory or poison the UI with NaN.
MAX_STATS_BYTES = 4 * 1024 * 1024   # 4 MiB
MAX_ENTRIES = 100_000


def _finite_float(v, default=0.0) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


_resolve_cache = {}   # stale path -> living path (or None); bounded below
_RESOLVE_CACHE_MAX = 2000

# Search roots injected by MainWindow at startup (added_folders + library
# parents). Module-level + setter keeps playstats importable without Qt
# (unit tests) while the live app supplies real roots. No __main__ hacks.
_search_roots = set()


def set_search_roots(roots) -> None:
    """Replace the filesystem roots used for moved-file resolution."""
    import os
    global _search_roots
    clean = set()
    for r in roots or []:
        try:
            if r and os.path.isdir(r):
                clean.add(os.path.normcase(os.path.abspath(r)))
        except Exception:
            pass
    _search_roots = clean


def add_search_root(root) -> None:
    """Add one root (new folder added to the library)."""
    import os
    try:
        if root and os.path.isdir(root):
            _search_roots.add(os.path.normcase(os.path.abspath(root)))
    except Exception:
        pass


def _resolve_moved(path: str):
    """Map a stale stats path to its living twin, or None.

    Users rename files ("(320)" suffix swaps) and reorganize folders; the
    play count must follow the SONG, not die with the old filename.
    Strategy (cheapest first, memoized):
      1. exact path exists → path itself
      2. same basename / same stem anywhere under the known music roots
         (added_folders + library parents) → newest biggest match
    Returns None when nothing living matches (truly deleted → prune it).
    """
    import os
    if not path:
        return None
    if os.path.exists(path):
        return path
    if path in _resolve_cache:
        hit = _resolve_cache[path]
        if hit is None or os.path.exists(hit):
            return hit
    found = _search_living_twin(path)
    if len(_resolve_cache) >= _RESOLVE_CACHE_MAX:
        try:
            _resolve_cache.pop(next(iter(_resolve_cache)))
        except StopIteration:
            pass
    _resolve_cache[path] = found
    return found


def _search_living_twin(path: str):
    """Filesystem search for a renamed/moved track under known roots."""
    import os
    try:
        from pathlib import Path as _P
        base = _P(path).name
        stem_l = _P(path).stem.strip().lower()
        roots = set(_search_roots)
        if not roots:
            # No roots injected (unit tests) — search the stale path's own
            # parent, which covers renames inside the same folder.
            parent = os.path.dirname(os.path.abspath(path))
            if os.path.isdir(parent):
                roots.add(os.path.normcase(parent))
        best = None
        best_score = None
        for root in roots:
            # Walk only 2 levels deep: music libraries are Artist/Album
            # shaped; a full recursive walk would stall the stats page.
            for dirpath, dirnames, filenames in os.walk(root):
                depth = os.path.normcase(dirpath).count(os.sep) - \
                    root.count(os.sep)
                if depth > 2:
                    dirnames[:] = []
                    continue
                if base in filenames:
                    cand = os.path.join(dirpath, base)
                    if os.path.isfile(cand):
                        return cand
                for fn in filenames:
                    if _P(fn).stem.strip().lower() == stem_l:
                        cand = os.path.join(dirpath, fn)
                        try:
                            score = (os.path.getmtime(cand),
                                     os.path.getsize(cand))
                        except OSError:
                            continue
                        if best_score is None or score > best_score:
                            best, best_score = cand, score
                if best is not None and depth >= 2:
                    break
            if best is not None:
                return best
        return best
    except Exception:
        return None


def note_moved(old_path: str, new_path: str):
    """Direct rename hint (cheaper than a search when the app did the move)."""
    if old_path and new_path and old_path != new_path:
        _resolve_cache[old_path] = new_path


class PlayStats:
    def __init__(self, store_path: Path):
        self.path = store_path
        self.total_seconds = 0.0
        self.play_counts = {}      # path -> completed plays
        self.artist_seconds = {}   # artist -> listening seconds
        self.sessions = 0
        self.first_played = ""
        self._dirty = False
        self._load()
        self.sessions += 1
        self._dirty = True

    # ------------------------------------------------------------------
    def _load(self):
        try:
            if self.path.is_file():
                if self.path.stat().st_size > MAX_STATS_BYTES:
                    log.warning("stats file too large, ignoring")
                    return
                # parse_constant guards against NaN/Infinity literals, which
                # would poison total_seconds and crash the stats page later.
                data = json.loads(self.path.read_text(encoding="utf-8"),
                                  parse_constant=lambda c: None)
                if isinstance(data, dict):
                    self.total_seconds = _finite_float(data.get("total_seconds", 0))
                    pc = data.get("play_counts", {})
                    if isinstance(pc, dict):
                        self.play_counts = {str(k): int(v) for k, v in
                                            list(pc.items())[:MAX_ENTRIES]
                                            if isinstance(v, (int, float))}
                    ac = data.get("artist_seconds", {})
                    if isinstance(ac, dict):
                        self.artist_seconds = {str(k): _finite_float(v) for k, v in
                                               list(ac.items())[:MAX_ENTRIES]}
                    self.sessions = self._validated_int(data.get("sessions", 0))
                    self.first_played = str(data.get("first_played", ""))
        except Exception as e:
            log.warning(f"stats load failed: {e}")

    @staticmethod
    def _validated_int(v, default=0) -> int:
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "total_seconds": round(self.total_seconds, 1),
                "play_counts": self.play_counts,
                "artist_seconds": {k: round(v, 1)
                                   for k, v in self.artist_seconds.items()},
                "sessions": self.sessions,
                "first_played": self.first_played,
            }
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False),
                           encoding="utf-8")
            tmp.replace(self.path)      # atomic-ish write
            self._dirty = False
        except Exception as e:
            log.warning(f"stats save failed: {e}")

    # ------------------------------------------------------------------
    def add_seconds(self, sec: float):
        """Accumulate listening time (called ~2x/sec from the UI tick)."""
        self.total_seconds += sec
        self._dirty = True

    def credit_track(self, path: str, seconds: float, artist: str = "",
                     duration_seconds: float = 0.0,
                     listened_seconds: float = 0.0) -> bool:
        """Count a play of `path`.

        Credit rule (user-set): a track counts as ONE play only when the
        user heard MORE THAN 70% of it this session. The MainWindow feeds
        `listened_seconds`/`duration_seconds` from its per-track listen
        meter; when duration is unknown, falling back to >=20 s of listening
        keeps short clips from being free wins while staying forgiving.

        Returns True when a play was credited.
        """
        credited = False
        if duration_seconds > 0 and listened_seconds >= 0:
            ratio = listened_seconds / float(duration_seconds)
            if ratio > 0.70:
                self.play_counts[path] = self.play_counts.get(path, 0) + 1
                credited = True
        elif listened_seconds >= 20.0:
            # No reliable duration (stream probe failed) — 20 s heuristic
            self.play_counts[path] = self.play_counts.get(path, 0) + 1
            credited = True
        if artist:
            self.artist_seconds[artist] = \
                self.artist_seconds.get(artist, 0.0) + max(0.0, seconds)
        if not self.first_played:
            self.first_played = date.today().isoformat()
        if credited:
            self._dirty = True
        return credited

    # ------------------------------------------------------------------
    @staticmethod
    def fmt_total(seconds: float) -> str:
        seconds = int(seconds)
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        parts = []
        if h:
            parts.append(f"{h} h")
        if m or h:
            parts.append(f"{m} min")
        parts.append(f"{s} s")
        return " ".join(parts)

    def top_tracks(self, tag_cache: dict, n=10):
        """[(title, artist, count, path), ...] — path rides along so callers
        can resolve covers directly instead of guessing by title.

        Stale entries (file renamed/moved/deleted after being counted) are
        resolved to their living twin via _resolve_moved() — covers + ▶
        keep working after library reorganizations instead of showing a
        dead placeholder row."""
        from pathlib import Path as _Path
        ranked = sorted(self.play_counts.items(), key=lambda kv: -kv[1])
        out = []
        for path, cnt in ranked:
            live = _resolve_moved(path)
            key = live or path
            title, artist = tag_cache.get(key, ("", ""))
            if not title:
                import os
                title = os.path.splitext(os.path.basename(key))[0]
            out.append((title, artist, cnt, key))
            if len(out) >= n:
                break
        return out

    def prune_missing(self, library_set) -> int:
        """Drop play_counts entries with no living file and not in
        `library_set`. Returns the number removed. Called after scans so
        the Most Played list can't fill with ghost rows."""
        dead = [p for p in self.play_counts
                if p not in library_set and _resolve_moved(p) is None]
        for p in dead:
            del self.play_counts[p]
        if dead:
            self._dirty = True
        return len(dead)

    def top_artists(self, n=5):
        ranked = sorted(self.artist_seconds.items(), key=lambda kv: -kv[1])[:n]
        return [(a, PlayStats.fmt_total(s)) for a, s in ranked]
