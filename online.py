"""Golden Music v3 — online music search + streaming (provider based).

Architecture
------------
``OnlineProvider`` is a small, well-defined interface; sources are separated
from the playback engine (the engine plays a resolved URL exactly like a
local file, so ONE playback system serves both local and online music).

Shipped provider: **Internet Archive** (archive.org) — public-domain and
freely-licensed audio, no authentication, no DRM, downloads allowed by the
site's terms. Adding another source (a licensed service with its own ToS)
means implementing the same interface — nothing else in the app changes.

Respect policy (enforced in code): HTTPS only, timeouts, bounded retries,
request cancellation, caching of search results, and no attempts to bypass
authentication, DRM or subscription requirements anywhere.

Pure parsing functions are separated from I/O so tests run without network.
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

log = logging.getLogger("goldenmusic.online")

DEFAULT_TIMEOUT_S = 10.0
MAX_RETRIES = 2
MAX_RESULTS = 60
HTTP_OK = 200


# ---------------------------------------------------------------------------
# Proxy-aware URL opener (shared by search/resolve/artwork/downloads)
# ---------------------------------------------------------------------------

_PROXY_OPENER: urllib.request.OpenerDirector | None = None
_PROXY_URL: str = ""


def set_proxy(url: str) -> str:
    """Install a custom HTTP(S) proxy for every request this module makes
    (search, stream resolve, artwork, and downloads via online_mod.urlopen).
    Empty string = clear (back to the system/env proxy). Returns the
    effective proxy URL ("" when cleared). Raises ValueError on an
    unusable proxy URL — never silently ignores a broken setting."""
    global _PROXY_OPENER, _PROXY_URL
    url = str(url or "").strip()
    if not url:
        _PROXY_OPENER, _PROXY_URL = None, ""
        return ""
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("proxy must be http:// or https://")
    parsed = urllib.parse.urlparse(url)
    if not parsed.netloc or "." not in parsed.netloc and ":" not in parsed.netloc:
        raise ValueError("proxy URL has no host")
    handler = urllib.request.ProxyHandler({"http": url, "https": url})
    _PROXY_OPENER = urllib.request.build_opener(handler)
    _PROXY_URL = url
    return _PROXY_URL


def proxy_url() -> str:
    return _PROXY_URL


def urlopen(req, timeout: float):
    """urlopen routed through the custom proxy when one is configured,
    otherwise the default opener (which honours the OS/env proxy)."""
    if _PROXY_OPENER is not None:
        return _PROXY_OPENER.open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)  # nosec B310 (callers audit schemes)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

@dataclass
class OnlineTrack:
    """One searchable online item, resolved lazily to a stream URL.

    ``downloads_allowed`` is tri-state: None = eligibility not yet
    established (search results start unknown); True/False = established
    from the source's actual item metadata at resolve time. The default is
    deliberately NOT True — v3.0.0 hard-marked every item downloadable,
    which was a false claim."""
    provider: str
    item_id: str
    title: str = ""
    artist: str = ""
    album: str = ""
    duration_ms: int = 0
    artwork_url: str = ""
    source_url: str = ""          # human page (credits / source info)
    downloads_allowed: bool | None = None
    stream_url: str = ""           # filled by resolve_stream()
    size_bytes: int = 0
    ext: str = ""
    extra: dict = field(default_factory=dict)

    def __hash__(self):
        # identity hash: unified-queue bookkeeping (smart-shuffle set,
        # history dedup) puts OnlineTrack objects in sets/dicts while the
        # dataclass __eq__ stays field-based for index()/remove().
        return id(self)

    def as_dict(self) -> dict:
        return {
            "provider": self.provider, "item_id": self.item_id,
            "title": self.title, "artist": self.artist, "album": self.album,
            "duration_ms": self.duration_ms, "artwork_url": self.artwork_url,
            "source_url": self.source_url,
            "downloads_allowed": self.downloads_allowed,
        }


# ---------------------------------------------------------------------------
# Provider interface
# ---------------------------------------------------------------------------

class OnlineProvider:
    """Interface for an online music source.

    Implementations MUST: use HTTPS, honour timeouts, return bounded result
    lists, never bypass authentication/DRM, and declare whether downloads
    are permitted by the source's terms.
    """

    name = "abstract"
    label = "Abstract provider"
    supports_download = False

    def search(self, query: str, timeout: float = DEFAULT_TIMEOUT_S) -> list:
        raise NotImplementedError

    def resolve_stream(self, track: OnlineTrack,
                        timeout: float = DEFAULT_TIMEOUT_S) -> OnlineTrack:
        """Fill track.stream_url (and size/ext) for playback."""
        raise NotImplementedError

    def artwork(self, track: OnlineTrack,
                timeout: float = DEFAULT_TIMEOUT_S) -> bytes | None:
        """Best-effort artwork bytes (PNG/JPEG) or None."""
        return None


# ---------------------------------------------------------------------------
# Internet Archive provider (public domain / freely licensed audio)
# ---------------------------------------------------------------------------

class InternetArchiveProvider(OnlineProvider):
    """archive.org audio search + streaming + downloads.

    * search: advancedsearch.php JSON API (mediatype:audio)
    * stream: metadata API -> first playable audio file -> download URL
    * downloads: permitted (the Internet Archive exists to serve the files)
    """

    name = "internetarchive"
    label = "Internet Archive"
    supports_download = True

    SEARCH_URL = "https://archive.org/advancedsearch.php"
    METADATA_URL = "https://archive.org/metadata/{identifier}"
    THUMB_URL = "https://archive.org/services/img/{identifier}"
    UA = "GoldenMusic/3 (Windows music player; contact: user)"

    PLAYABLE_EXTS = (".mp3", ".ogg", ".oga", ".flac", ".m4a", ".wav", ".opus")

    # ---------------- pure parsing (unit-tested without network) -------
    @staticmethod
    def parse_search_response(text: str) -> list:
        """advancedsearch.php JSON -> [OnlineTrack]. Raises ValueError on
        malformed input (the caller maps that to a UI error state)."""
        data = json.loads(text)
        if not isinstance(data, dict) or "response" not in data:
            raise ValueError("unexpected search payload shape")
        response = data.get("response")
        if not isinstance(response, dict):
            raise ValueError("unexpected search payload shape")
        docs = response.get("docs", [])
        if not isinstance(docs, list):
            raise ValueError("unexpected search payload shape")
        tracks = []
        for d in docs[:MAX_RESULTS]:
            if not isinstance(d, dict):
                continue
            ident = d.get("identifier")
            if not ident:
                continue
            title = d.get("title") or ident
            if isinstance(title, list):
                title = title[0] if title else ident
            creator = d.get("creator") or ""
            if isinstance(creator, list):
                creator = creator[0] if creator else ""
            year = d.get("year") or ""
            album = f"Internet Archive{(' ' + str(year)) if year else ''}"
            tracks.append(OnlineTrack(
                provider=InternetArchiveProvider.name,
                item_id=str(ident),
                title=str(title),
                artist=str(creator) or "Internet Archive",
                album=album,
                artwork_url=InternetArchiveProvider.THUMB_URL.format(
                    identifier=ident),
                source_url=f"https://archive.org/details/{ident}",
                downloads_allowed=None,   # unknown until resolve-time check
                extra={"downloads": d.get("downloads", 0)},
            ))
        return tracks

    @staticmethod
    def pick_playable_file(metadata_json_text: str) -> dict | None:
        """metadata API JSON -> best playable file dict (or None).

        Prefers .mp3, then .ogg/.oga, then flac/wav/m4a by size order —
        streaming-friendly formats first.
        """
        data = json.loads(metadata_json_text)
        files = (data or {}).get("files", [])
        if not isinstance(files, list):
            return None
        preference = {".mp3": 0, ".ogg": 1, ".oga": 1, ".opus": 2,
                      ".m4a": 3, ".flac": 4, ".wav": 5}
        best = None
        best_score = 99
        for f in files:
            if not isinstance(f, dict):
                continue
            name = str(f.get("name", ""))
            fmt = str(f.get("format", "")).lower()
            ext = name[name.rfind("."):] if "." in name else ""
            if fmt == "h.264" or ext not in preference:
                continue
            score = preference[ext]
            if score < best_score:
                best_score = score
                best = f
        return best

    @staticmethod
    def assess_download_eligibility(metadata_json_text: str) -> bool | None:
        """Item-specific download eligibility from the source's actual
        metadata (the same metadata API response resolve_stream fetches —
        no extra network call).

        Returns False when the item carries an access restriction the
        Internet Archive itself reports ("access-restricted-item",
        darked items, or a private chosen file). Returns True for items
        the source presents as publicly accessible. None = the payload was
        unusable, so eligibility could NOT be established (callers must
        not claim downloadability).

        This replaces v3.0.0's constant ``downloads_allowed=True``.
        """
        try:
            data = json.loads(metadata_json_text)
        except (TypeError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        if data.get("is_dark") is True:
            return False
        meta = data.get("metadata")
        if not isinstance(meta, dict):
            return None
        flag = meta.get("access-restricted-item")
        if flag is True or str(flag).lower() in ("true", "1"):
            return False
        return True

    # ---------------- I/O ----------------------------------------------
    def _http_get(self, url: str, timeout: float) -> bytes:
        """HTTPS GET with timeout + bounded retry. Never follows http://
        (urllib enforces the https scheme by construction here). Routes
        through the shared proxy-aware opener."""
        if not url.lower().startswith("https://"):
            raise ValueError("non-HTTPS URL refused")
        last_err = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                req = urllib.request.Request(   # noqa: S310 (https enforced)
                    url, headers={"User-Agent": self.UA})
                with urlopen(req, timeout) as r:  # nosec B310 (https enforced)
                    if r.status != HTTP_OK:
                        raise urllib.error.HTTPError(
                            url, r.status, "non-200", None, None)
                    return r.read(2 * 1024 * 1024)      # bounded read
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_err = e
                if attempt < MAX_RETRIES:
                    time.sleep(0.4 * (attempt + 1))     # small backoff
        raise RuntimeError(f"request failed after retries: {last_err}")

    def search(self, query: str,
               timeout: float = DEFAULT_TIMEOUT_S) -> list:
        q = urllib.parse.quote(f"({query}) AND mediatype:audio")
        url = (f"{self.SEARCH_URL}?q={q}"
               f"&fl[]=identifier,title,creator,year,downloads"
               f"&rows={MAX_RESULTS}&page=1&output=json")
        text = self._http_get(url, timeout).decode("utf-8", "replace")
        return self.parse_search_response(text)

    def resolve_stream(self, track: OnlineTrack,
                       timeout: float = DEFAULT_TIMEOUT_S) -> OnlineTrack:
        meta_url = self.METADATA_URL.format(
            identifier=urllib.parse.quote(track.item_id, safe=""))
        text = self._http_get(meta_url, timeout).decode("utf-8", "replace")
        f = self.pick_playable_file(text)
        if not f:
            raise RuntimeError("no playable audio file in item")
        # Item-specific eligibility, established from the SAME metadata the
        # resolver already fetched: the item's own access flags + the
        # chosen file's private flag. Never assumed.
        eligible = self.assess_download_eligibility(text)
        file_private = str(f.get("private", "")).lower() in ("true", "1")
        if eligible is False or file_private:
            track.downloads_allowed = False
        else:
            track.downloads_allowed = eligible   # True, or None (unusable)
        name = urllib.parse.quote(str(f.get("name", "")), safe="")
        track.stream_url = (
            f"https://archive.org/download/"
            f"{urllib.parse.quote(track.item_id, safe='')}/{name}")
        try:
            track.size_bytes = int(f.get("size") or 0)
        except (TypeError, ValueError):
            track.size_bytes = 0
        fname = str(f.get("name", ""))
        track.ext = fname[fname.rfind("."):] if "." in fname else ""
        return track

    def artwork(self, track: OnlineTrack,
                timeout: float = DEFAULT_TIMEOUT_S) -> bytes | None:
        if not track.artwork_url:
            return None
        try:
            return self._http_get(track.artwork_url, timeout)
        except Exception:
            return None


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_PROVIDERS: dict[str, OnlineProvider] = {}


def register_provider(p: OnlineProvider):
    _PROVIDERS[p.name] = p
    return p


def default_providers() -> list:
    global _PROVIDERS
    if not _PROVIDERS:
        register_provider(InternetArchiveProvider())
    return list(_PROVIDERS.values())


def provider_by_name(name: str) -> OnlineProvider | None:
    default_providers()
    return _PROVIDERS.get(name)


# ---------------------------------------------------------------------------
# Search result cache (bounded, TTL)
# ---------------------------------------------------------------------------

class SearchCache:
    """Small TTL cache so paging back through searches doesn't re-hit the
    network (and offline moments show cached results instead of errors)."""

    TTL_S = 600

    def __init__(self, max_entries: int = 40):
        self.max_entries = max_entries
        self._data: dict[str, tuple[float, list]] = {}

    def key(self, query: str, provider: str) -> str:
        return f"{provider}|{query.strip().lower()}"

    def get(self, query: str, provider: str) -> list | None:
        k = self.key(query, provider)
        hit = self._data.get(k)
        if not hit:
            return None
        ts, results = hit
        if time.time() - ts > self.TTL_S:
            self._data.pop(k, None)
            return None
        return results

    def put(self, query: str, provider: str, results: list):
        if len(self._data) >= self.max_entries:
            oldest = min(self._data, key=lambda k: self._data[k][0])
            self._data.pop(oldest, None)
        self._data[self.key(query, provider)] = (time.time(), list(results))


# ---------------------------------------------------------------------------
# Search history (persisted by the caller; validated here)
# ---------------------------------------------------------------------------

HISTORY_MAX = 12


def sanitize_history(raw) -> list:
    """Validate + bound a persisted search-history list."""
    if not isinstance(raw, list):
        return []
    out, seen = [], set()
    for item in raw:
        if isinstance(item, str):
            q = item.strip()[:80]
            if q and q.lower() not in seen:
                seen.add(q.lower())
                out.append(q)
        if len(out) >= HISTORY_MAX:
            break
    return out


def suggest_completions(query: str, history: list) -> list:
    """Local history-based suggestions while typing (offline, instant)."""
    q = query.strip().lower()
    if len(q) < 2:
        return []
    return [h for h in history if q in h.lower()][:5]


# ---------------------------------------------------------------------------
# URL validation for playback (mixed local/online queues)
# ---------------------------------------------------------------------------

_STREAM_URL_RE = re.compile(r"^https://[\w.\-/@%?=&+~#:]+$")


def valid_stream_url(url: str) -> bool:
    """Only https, no credentials or ports in the authority, no control
    characters, and a well-formed host."""
    if not isinstance(url, str) or not url.startswith("https://"):
        return False
    if not _STREAM_URL_RE.match(url):
        return False
    authority = url[len("https://"):].split("/", 1)[0]
    if "@" in authority or ":" in authority or not authority.strip("."):
        return False
    if "@" in url:
        return False
    return True
