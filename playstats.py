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
import time
from datetime import date
from pathlib import Path

log = logging.getLogger("app.stats")


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
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self.total_seconds = float(data.get("total_seconds", 0))
                    pc = data.get("play_counts", {})
                    if isinstance(pc, dict):
                        self.play_counts = {str(k): int(v) for k, v in pc.items()
                                            if isinstance(v, (int, float))}
                    ac = data.get("artist_seconds", {})
                    if isinstance(ac, dict):
                        self.artist_seconds = {str(k): float(v)
                                               for k, v in ac.items()}
                    self.sessions = int(data.get("sessions", 0))
                    self.first_played = str(data.get("first_played", ""))
        except Exception as e:
            log.warning(f"stats load failed: {e}")

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

    def credit_track(self, path: str, seconds: float, artist: str = ""):
        """Count a finished/meaningful listen of a track."""
        self.total_seconds = self.total_seconds  # time already accumulated
        self.play_counts[path] = self.play_counts.get(path, 0) + 1
        if artist:
            self.artist_seconds[artist] = \
                self.artist_seconds.get(artist, 0.0) + max(0.0, seconds)
        if not self.first_played:
            self.first_played = date.today().isoformat()
        self._dirty = True

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
        ranked = sorted(self.play_counts.items(), key=lambda kv: -kv[1])[:n]
        out = []
        for path, cnt in ranked:
            title, artist = tag_cache.get(path, ("", ""))
            if not title:
                import os
                title = os.path.splitext(os.path.basename(path))[0]
            out.append((title, artist, cnt))
        return out

    def top_artists(self, n=5):
        ranked = sorted(self.artist_seconds.items(), key=lambda kv: -kv[1])[:n]
        return [(a, PlayStats.fmt_total(s)) for a, s in ranked]
