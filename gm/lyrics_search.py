"""Golden Music 3.0 — multi-provider lyrics search (Qt-free).

Search layer between the UI/worker and the network:

* ``LyricsProvider`` — abstract source returning candidate dicts
  ``{title, artist, duration (s|None), synced (bool),
     lines: [(ms|None, str)], source: str}``;
* ``LrclibProvider`` — wraps lrclib.net with an injectable fetcher
  (tests pass fakes; the default fetcher is HTTPS-only urllib with a
  2 MiB cap and never touches the network at import time);
* ``Aggregator`` — queries every provider across title_variants until a
  GOOD-threshold best is found (early exit), deduping by normalized
  (title, artist, synced) and returning ranked ``[(candidate, score)]``;
* ``plan_searches`` — the pure, network-free list of attempts a worker
  will run for a file (or metadata dict), for UI display and tests.

Providers are always injected, so everything here is testable offline.
"""
from __future__ import annotations

import abc
import json
import logging
import os
import re
import urllib.parse
from pathlib import Path

from gm.lyrics_query import (
    GOOD,
    clean_artist,
    clean_title,
    normalize_for_match,
    score_candidate,
    title_from_filename,
    title_variants,
)

log = logging.getLogger("app.lyrics.search")

__all__ = [
    "MAX_RESPONSE_BYTES", "DEFAULT_TIMEOUT", "USER_AGENT", "LRCLIB_BASE",
    "LyricsProvider", "LrclibProvider", "Aggregator", "plan_searches",
]

MAX_RESPONSE_BYTES = 2 * 1024 * 1024   # 2 MiB — a lyrics payload ceiling
DEFAULT_TIMEOUT = 8.0
USER_AGENT = "GoldenMusic/3.0"
LRCLIB_BASE = "https://lrclib.net"

_LRC_TS = re.compile(r"\[(\d{1,2}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
_LRC_META = re.compile(r"^\[(a[rl]|ti|al|by|offset|length|re|ve):", re.I)


def _stamp_ms(mm: str, ss: str, frac: str | None) -> int:
    return int(mm) * 60_000 + int(ss) * 1000 + int((frac or "0").ljust(3, "0")[:3])


def _parse_lrc(text: str) -> tuple[list[tuple[int | None, str]], bool]:
    """Minimal LRC parser (local copy, keeps this module independent of
    the config-importing legacy lyrics.py). Returns (lines, synced)."""
    entries: dict[int, str] = {}
    plain: list[str] = []
    any_ts = False
    for raw in (text or "").splitlines():
        stamps = _LRC_TS.findall(raw)
        content = _LRC_TS.sub("", raw).strip()
        if not content:
            continue
        if stamps:
            any_ts = True
            for m in _LRC_TS.finditer(raw):
                entries.setdefault(_stamp_ms(*m.groups()), content)
        elif not _LRC_META.match(raw.strip()):
            plain.append(content)
    if any_ts and entries:
        return sorted(entries.items()), True
    return [(None, ln) for ln in plain], False


# ---------------------------------------------------------------------------
# providers
# ---------------------------------------------------------------------------

class LyricsProvider(abc.ABC):
    """A lyrics source. Implementations must never raise from search()."""

    @abc.abstractmethod
    def name(self) -> str:
        """Human-readable provider name."""

    @abc.abstractmethod
    def search(self, query_title: str, query_artist: str,
               duration_s: float | None = None) -> list[dict]:
        """Return candidate dicts (see module docstring). Errors are
        swallowed by the implementations and surface as []."""


def _default_fetch_json(url: str, timeout: float = DEFAULT_TIMEOUT):
    """HTTPS-only JSON fetch with a 2 MiB cap.

    The only place network I/O happens in the lyrics stack; everything
    else receives injectable fetchers. Raises on any error (the provider
    catches). urllib.request is imported lazily so importing this module
    never pulls in the network stack.
    """
    import urllib.request  # lazy on purpose

    if urllib.parse.urlparse(url).scheme != "https":
        raise ValueError("lyrics lookup requires https")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec B310 — https enforced above, hardcoded trusted host
        data = r.read(MAX_RESPONSE_BYTES)
    return json.loads(data.decode("utf-8", errors="replace"))


class LrclibProvider(LyricsProvider):
    """lrclib.net public API (free, no key).

    Strategy per query: exact ``/api/get`` (artist+title+duration) when
    both are known, then ``/api/search`` qualified by artist, then a bare
    ``q=`` search so wrong artist tags can't poison the lookup.
    """

    def __init__(self, fetch_json=None, timeout: float = DEFAULT_TIMEOUT,
                 base_url: str = LRCLIB_BASE) -> None:
        self._fetch = fetch_json or _default_fetch_json
        self.timeout = float(timeout)
        base = str(base_url or LRCLIB_BASE).strip()
        if not base.lower().startswith("https://"):
            # Bandit-safe by construction: http:// prefixes are coerced.
            base = "https://" + re.sub(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", "", base)
            log.debug("coerced lrclib base url to https: %s", base)
        self.base = base.rstrip("/")

    def name(self) -> str:
        return "lrclib.net"

    def _url(self, path: str, params: dict) -> str:
        return f"{self.base}{path}?{urllib.parse.urlencode(params)}"

    def search(self, query_title: str, query_artist: str,
               duration_s: float | None = None) -> list[dict]:
        out: list[dict] = []
        try:
            title = (query_title or "").strip()
            artist = (query_artist or "").strip()
            if not title:
                return out
            # Pass 1: exact endpoint — cheapest and most accurate shot.
            if artist and duration_s:
                try:
                    res = self._fetch(self._url("/api/get", {
                        "artist_name": artist,
                        "track_name": title,
                        "duration": int(duration_s),
                    }), self.timeout)
                    cand = self._to_candidate(res)
                    if cand is not None:
                        out.append(cand)
                        return out
                except Exception as e:
                    log.debug("lrclib /api/get miss: %s", e)
            # Pass 2: search qualified by artist.
            if artist:
                try:
                    res = self._fetch(self._url("/api/search", {
                        "track_name": title, "artist_name": artist,
                    }), self.timeout)
                    out.extend(self._to_candidates(res))
                except Exception as e:
                    log.debug("lrclib search (artist) miss: %s", e)
            # Pass 3: bare search — junk artist tags must not poison it.
            if not out:
                try:
                    res = self._fetch(self._url("/api/search", {"q": title}),
                                      self.timeout)
                    out.extend(self._to_candidates(res))
                except Exception as e:
                    log.debug("lrclib search (bare) miss: %s", e)
        except Exception as e:  # never-crash policy
            log.debug("lrclib provider failed: %s", e)
        return out

    def _to_candidates(self, payload) -> list[dict]:
        if not isinstance(payload, list):
            return []
        out = []
        for item in payload:
            cand = self._to_candidate(item)
            if cand is not None:
                out.append(cand)
        return out

    def _to_candidate(self, item) -> dict | None:
        if not isinstance(item, dict) or item.get("instrumental"):
            return None
        synced_text = str(item.get("syncedLyrics") or "")
        plain_text = str(item.get("plainLyrics") or "")
        if synced_text.strip():
            lines, synced = _parse_lrc(synced_text)
        else:
            lines = [(None, ln) for ln in plain_text.splitlines() if ln.strip()]
            synced = False
        if not lines:
            return None
        duration = item.get("duration")
        try:
            duration = int(duration) if duration is not None else None
        except (TypeError, ValueError):
            duration = None
        return {
            "title": str(item.get("trackName") or ""),
            "artist": str(item.get("artistName") or ""),
            "duration": duration,
            "synced": synced,
            "lines": lines,
            "source": "lrclib.net",
        }


# ---------------------------------------------------------------------------
# aggregation
# ---------------------------------------------------------------------------

class Aggregator:
    """Query every provider across title_variants until a GOOD match.

    Pure coordination logic — providers do all I/O and are injected, so
    the aggregator is fully testable offline.
    """

    def __init__(self, providers: list[LyricsProvider] | None = None) -> None:
        self.providers: list[LyricsProvider] = list(providers or [])

    def search_all(self, title: str, artist: str,
                   duration_s: float | None = None,
                   progress_cb=None) -> list[tuple[dict, float]]:
        """Return ranked ``[(candidate, score)]`` (best first).

        Builds the title variants (which clean/fix mojibake), queries each
        provider per variant, dedupes by normalized (title, artist, synced)
        and stops after the first variant round whose best score reaches
        GOOD. ``progress_cb(stage, detail)`` is best-effort.
        """
        base_title = clean_title(title or "")
        base_artist = clean_artist(artist or "")
        ranked: list[tuple[dict, float]] = []
        seen: set[tuple[str, str, bool]] = set()
        try:
            variants = title_variants(title or "", artist or "")
        except Exception as e:  # pragma: no cover — defensive
            log.warning("variant building failed: %s", e)
            variants = []
        for query_title, query_artist in variants:
            for provider in self.providers:
                self._notify(progress_cb, "query",
                             f"{provider.name()}: {query_title!r} / {query_artist!r}")
                try:
                    results = provider.search(query_title, query_artist,
                                              duration_s) or []
                except Exception as e:  # a broken provider never stops search
                    log.debug("provider %s failed: %s", provider.name(), e)
                    results = []
                for cand in results:
                    if not isinstance(cand, dict):
                        continue
                    key = (normalize_for_match(str(cand.get("title") or "")),
                           normalize_for_match(str(cand.get("artist") or "")),
                           bool(cand.get("synced")))
                    if key in seen:
                        continue
                    seen.add(key)
                    score = score_candidate(cand, base_title, base_artist,
                                            duration_s)
                    ranked.append((cand, score))
            best = max((score for _cand, score in ranked), default=0.0)
            if best >= GOOD:
                self._notify(progress_cb, "hit", f"score {best:.2f}")
                break
        ranked.sort(key=lambda pair: pair[1], reverse=True)
        return ranked

    @staticmethod
    def _notify(progress_cb, stage: str, detail: str) -> None:
        if progress_cb is None:
            return
        try:
            progress_cb(stage, detail)
        except Exception as e:
            log.debug("progress callback failed: %s", e)


# ---------------------------------------------------------------------------
# search planning (pure helper for the UI worker)
# ---------------------------------------------------------------------------

def _probe_tags(path: str) -> dict:
    """Best-effort offline tag probe (title/artist/duration) via mutagen."""
    meta = {"title": "", "artist": "", "duration": None}
    if not path or not os.path.isfile(path):
        return meta
    try:
        from mutagen import File as MutagenFile  # lazy

        m = MutagenFile(path, easy=True)
        if m is None:
            return meta
        for key in ("title", "artist"):
            try:
                val = m[key]
            except Exception:
                continue
            if isinstance(val, (list, tuple)):
                val = val[0] if val else ""
            meta[key] = str(val or "").strip()
        length = getattr(getattr(m, "info", None), "length", None)
        if length:
            meta["duration"] = int(length)
    except Exception as e:
        log.debug("tag probe failed for %s: %s", path, e)
    return meta


def plan_searches(source: str | dict) -> list[tuple[str, str, int | None]]:
    """The exact ordered (title, artist, duration) attempts a worker runs.

    ``source`` is either a filepath (tags probed offline via mutagen, with
    filename parsing as fallback) or a metadata dict with optional keys
    ``title``/``artist``/``duration``/``path``. Mojibake and filename junk
    are cleaned inside title_variants, so the plan shows real queries.
    """
    if isinstance(source, dict):
        meta = dict(source)
        path = str(meta.get("path") or "")
        title = str(meta.get("title") or "")
        artist = str(meta.get("artist") or "")
        duration = meta.get("duration")
    else:
        path = str(source or "")
        meta = _probe_tags(path)
        title = str(meta.get("title") or "")
        artist = str(meta.get("artist") or "")
        duration = meta.get("duration")
    if not title and path:
        file_artist, file_title = title_from_filename(Path(path).stem)
        title = title or file_title
        artist = artist or file_artist
    try:
        duration = int(duration) if duration else None
    except (TypeError, ValueError):
        duration = None
    return [(qt, qa, duration) for qt, qa in title_variants(title, artist)]
