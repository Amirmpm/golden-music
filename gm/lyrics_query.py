"""Golden Music 2.3 — lyrics query building (Qt-free, network-free).

Turns messy real-world filenames and tags into clean, searchable queries
and ranks the candidate results that come back:

* strip leading track numbers ("01", "1.", "01 -", "01_"), underscores,
  quality tags (320kbps / flac / hq ...), site pollution and parenthetical
  junk ("(Official Video)", "(Remastered)" ...);
* repair the classic Persian-library mojibake where Windows-1256 bytes
  were decoded as cp1252/Latin-1 (and vice versa);
* fold Arabic/Persian script variants (yeh/kehef forms, diacritics, ZWNJ)
  so fuzzy matching works across transliterations and RTL keyboards;
* score candidates by title/artist similarity, duration proximity and
  synced-ness, with the GOOD/OK thresholds used by the search aggregator.

Designed around the "01 vaveyla" failure case: an untagged Persian track
whose filename carries a leading track number must still resolve to the
query "vaveyla", and near-miss results must be caught by fuzzy scoring.
"""
from __future__ import annotations

import difflib
import logging
import re

log = logging.getLogger("app.lyrics.query")

__all__ = [
    "GOOD", "OK",
    "SITE_WORDS", "PAREN_JUNK", "UNKNOWN_ARTISTS",
    "clean_title", "clean_artist", "fix_mojibake", "normalize_for_match",
    "title_from_filename", "title_variants", "fuzzy_score",
    "score_candidate", "pick_best",
]

# Match-quality thresholds (consumed by gm.lyrics_search.Aggregator).
GOOD = 0.75
OK = 0.55

# Site / watermark pollution words, removed from titles and artists.
# Dotted entries match as substrings; word entries match token-bounded.
# NOTE: bare "genius" is intentionally absent — it is also a song title.
SITE_WORDS: tuple[str, ...] = (
    "archive.org", "keepyourmusic", "musixmatch", "genius.com", "azlyrics",
    "ulsan", "www.", ".com", ".ir",
)

# Parenthetical/bracket junk prefixes: a "(...)" or "[...]" group whose
# content STARTS with one of these (case-insensitive) is dropped.
PAREN_JUNK: tuple[str, ...] = (
    "official", "video oficial", "music video", "lyric video", "visualizer",
    "videoclip", "clip oficial", "audio", "lyrics", "lyric", "hd", "4k",
    "remastered", "remaster", "mv",
)

# Artist placeholders that carry zero information for a lyrics query.
UNKNOWN_ARTISTS: frozenset[str] = frozenset({
    "", "unknown", "unknown artist", "various", "various artists",
    "artist", "soundtrack", "ost",
})

_MULTISPACE_RE = re.compile(r"\s+")
_PAREN_GROUP_RE = re.compile(r"\s*[\(\[].*?[\)\]]\s*")
_EMPTY_GROUP_RE = re.compile(r"\s*[\(\[]\s*[)\]]\s*")
_QUALITY_RE = re.compile(
    r"\b(?:320|256|192|128)\s?kbps\b"
    r"|\b(?:24bit|16bit|hqlossless|flac|mp3|hq)\b",
    re.IGNORECASE,
)
_FEAT_SPLIT_RE = re.compile(r"\s+(?:feat\.?|ft\.?|feats\.?|featuring)\s+",
                            re.IGNORECASE)
_ARTIST_TAIL_RE = re.compile(r"\s+(?:ft|feat|feats|featuring)\.?\s+.*$",
                             re.IGNORECASE)
_TRACK_NO_RE = re.compile(r"^\s*(\d{1,3})(?=[\s._\-–—:)\]]|$)")
_EDGE_PUNCT = " \t-–—_~|·•."

# Arabic-script ranges (incl. presentation forms) for mojibake heuristics.
_ARABIC_RE = re.compile(
    r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]")
_ARABIC_DIACRITICS_RE = re.compile(r"[\u064B-\u065F\u0670\u0640]")
_ZERO_WIDTH_RE = re.compile(r"[\u200B-\u200F\u202A-\u202E\u2060\uFEFF]")
_NON_WORD_RE = re.compile(r"[^\w\s]", re.UNICODE)

_MOJIBAKE_MIN_HIGH_RATIO = 0.25   # U+00C0-U+00FF garbage gate
_MOJIBAKE_MIN_ARABIC_RATIO = 0.6  # acceptance after the round trip


# ---------------------------------------------------------------------------
# mojibake repair
# ---------------------------------------------------------------------------

def fix_mojibake(text: str) -> str:
    """Repair Windows-1256-decoded-as-cp1252/Latin-1 mojibake.

    Persian libraries are full of tags whose cp1256 bytes were mis-decoded
    as cp1252 or Latin-1 ("ÚØ§Ù„Ø³ØªØ§Ù†"-style garbage). Heuristic:

    1. skip texts that already contain Arabic script (nothing to repair);
    2. require a high ratio of U+00C0-U+00FF chars (or U+FFFD replacement
       chars) so normal Latin text never enters the repair path;
    3. re-encode as cp1252 (strict) or Latin-1 (with U+FFFD -> "?"),
       decode as cp1256, and accept the result only when more than 60% of
       its non-space characters are Arabic script.

    Mixed ASCII+mojibake strings ("Googoosh ì„ …") stay unchanged — the
    60% acceptance rule is deliberately conservative to protect French,
    Spanish and German titles. Returns the original text on any doubt.
    """
    if not text:
        return text
    if _ARABIC_RE.search(text):
        return text  # already Arabic script — nothing to repair
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return text
    high = sum(1 for c in chars if "\u00c0" <= c <= "\u00ff")
    has_replacement = "\ufffd" in text
    if high / len(chars) < _MOJIBAKE_MIN_HIGH_RATIO and not has_replacement:
        return text
    for encoding, errors in (("cp1252", "strict"), ("latin-1", "replace")):
        try:
            raw = text.encode(encoding, errors=errors)
        except (UnicodeEncodeError, LookupError):
            continue
        try:
            fixed = raw.decode("cp1256", errors="replace")
        except (UnicodeDecodeError, LookupError):
            continue
        fchars = [c for c in fixed if not c.isspace()]
        if not fchars:
            continue
        arabic = sum(1 for c in fchars if _ARABIC_RE.match(c))
        if arabic / len(fchars) > _MOJIBAKE_MIN_ARABIC_RATIO:
            log.debug("mojibake repaired (%s pass): %r -> %r", encoding,
                      text, fixed)
            return fixed
    return text


# ---------------------------------------------------------------------------
# normalization for matching
# ---------------------------------------------------------------------------

def normalize_for_match(text: str) -> str:
    """Fold a string for fuzzy comparison.

    casefold + strip Arabic diacritics (U+064B-065F, U+0670, tatweel),
    unify Arabic Yeh U+0649 / Farsi Yeh U+06CC -> U+064A and Persian Kehef
    U+06A9 -> Arabic Kaf U+0643, drop ZWNJ and other zero-width marks,
    then collapse punctuation and whitespace to single spaces.
    """
    s = str(text or "").casefold()
    s = s.replace("\u0649", "\u064a")   # alef maqsura -> yeh
    s = s.replace("\u06cc", "\u064a")   # farsi yeh    -> yeh
    s = s.replace("\u06a9", "\u0643")   # kehef        -> kaf
    s = _ARABIC_DIACRITICS_RE.sub("", s)
    s = _ZERO_WIDTH_RE.sub("", s)
    s = _NON_WORD_RE.sub(" ", s)
    return _MULTISPACE_RE.sub(" ", s).strip()


# ---------------------------------------------------------------------------
# cleaning
# ---------------------------------------------------------------------------

def _strip_track_number(s: str) -> str:
    """Drop a leading track number ("01", "1.", "01 -", "01_", "3) ...").

    Zero-padded numbers always count ("01 vaveyla" -> "vaveyla");
    non-padded ones only when a hard separator follows ("1. vaveyla"),
    so real titles like "7 Rings" or "24K Magic" survive intact.
    """
    m = _TRACK_NO_RE.match(s)
    if not m:
        return s
    num = m.group(1)
    rest = s[m.end(1):]
    zero_padded = len(num) >= 2 and num[0] == "0"
    if not zero_padded:
        if not rest or rest[0] not in "._-–—:)]":
            return s  # "7 rings" / "99 luftballons" keep their number
    return rest.lstrip(" \t._-–—:)]").strip()


def _strip_paren_junk(s: str) -> str:
    """Remove (parenthetical)/[bracket] junk groups by PAREN_JUNK prefix."""
    for word in PAREN_JUNK:
        s = re.sub(r"\s*[\(\[]\s*" + re.escape(word) + r"[^)\]]*[)\]]\s*",
                   " ", s, flags=re.IGNORECASE)
    return s


def _strip_site_words(s: str) -> str:
    """Remove SITE_WORDS pollution, but never blank out the whole string."""
    prev = s
    for word in SITE_WORDS:
        if not word:
            continue
        escaped = re.escape(word)
        pattern = escaped if word.startswith(".") or word.endswith(".") \
            else r"\b" + escaped + r"\b"
        try:
            candidate = re.sub(pattern, " ", prev, flags=re.IGNORECASE)
        except re.error:  # pragma: no cover — regex is built from literals
            continue
        if candidate.strip(" \t-–—_~|·•.()[],"):
            prev = candidate  # "Genius" the song name is not genius.com
    return prev


def _common_clean(raw: str) -> str:
    """Shared normalization for clean_title/clean_artist."""
    s = fix_mojibake(str(raw or ""))
    s = s.replace("_", " ")
    s = _MULTISPACE_RE.sub(" ", s).strip()
    s = _QUALITY_RE.sub(" ", s)
    s = _strip_site_words(s)
    s = _strip_paren_junk(s)
    s = _EMPTY_GROUP_RE.sub(" ", s)
    s = _MULTISPACE_RE.sub(" ", s).strip().strip(_EDGE_PUNCT).strip()
    return _MULTISPACE_RE.sub(" ", s).strip()


def clean_title(raw: str) -> str:
    """Normalize a track title into a search-friendly form.

    Strips: mojibake, underscores, leading track numbers, quality tags,
    site pollution, parenthetical junk and stray edge punctuation. The
    "feat ..." tail is KEPT here (dropped later by title_variants) so the
    exact-variant query can still use it.
    """
    s = fix_mojibake(str(raw or ""))
    s = s.replace("_", " ")
    s = _MULTISPACE_RE.sub(" ", s).strip()
    s = _strip_track_number(s)
    return _common_clean(s) if s else ""


def clean_artist(raw: str) -> str:
    """Normalize an artist string: same as clean_title minus the track
    number stripping, plus "ft/feat ..." tail removal and placeholder
    ("unknown artist", pure digits) collapsing to "".
    """
    s = fix_mojibake(str(raw or ""))
    s = s.replace("_", " ")
    s = _MULTISPACE_RE.sub(" ", s).strip()
    s = _ARTIST_TAIL_RE.sub("", s)
    s = _common_clean(s) if s else ""
    if s.casefold() in UNKNOWN_ARTISTS or (s.isdigit() and len(s) <= 3):
        return ""  # folder names like "2" / "01" poison artist queries
    return s


# ---------------------------------------------------------------------------
# filename parsing
# ---------------------------------------------------------------------------

def title_from_filename(stem: str) -> tuple[str, str]:
    """Split a filename stem into ``(artist, title)``.

    Supported shapes (most specific first):
      "Artist - 07 - Title"  -> ("Artist", "Title")
      "Artist - Title"       -> ("Artist", "Title")
      "07 - Title"           -> ("", "Title")
      "07 Title" / "07. Title" / plain -> ("", cleaned stem)
    """
    s = fix_mojibake(str(stem or "")).replace("_", " ")
    s = _MULTISPACE_RE.sub(" ", s).strip()
    if not s:
        return ("", "")
    parts = re.split(r"\s+-\s+", s)
    if len(parts) >= 3:
        middle = parts[1].strip()
        if middle.isdigit() and len(middle) <= 3:
            artist = clean_artist(parts[0])
            title = clean_title(" ".join(parts[2:]))
            if artist or title:
                return (artist, title)
    if len(parts) == 2:
        first, second = parts[0].strip(), parts[1].strip()
        if first.isdigit() and len(first) <= 3:
            return ("", clean_title(second))
        artist = clean_artist(parts[0])
        title = clean_title(parts[1])
        if artist and title:
            return (artist, title)
        if title:
            return ("", title)
    return ("", clean_title(s))


# ---------------------------------------------------------------------------
# query variants
# ---------------------------------------------------------------------------

def title_variants(title: str, artist: str) -> list[tuple[str, str]]:
    """Ordered (query_title, query_artist) attempts for multi-pass search.

    Order (most specific first, deduped):
      1. exact cleaned title + artist
      2. title without parentheticals
      3. title without the "feat ..." tail
      4. combined bare string "artist - title" (artist slot empty)
      5. title only (artist tags are often folder-name junk)
      6. first words when the title is very long (> 40 chars)
    """
    base_title = clean_title(title or "")
    base_artist = clean_artist(artist or "")
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def _emit(query_title: str, query_artist: str) -> None:
        qt = _MULTISPACE_RE.sub(" ", str(query_title or "")) \
                .strip().strip(_EDGE_PUNCT).strip()
        qa = _MULTISPACE_RE.sub(" ", str(query_artist or "")).strip()
        if not qt:
            return
        key = (normalize_for_match(qt), normalize_for_match(qa))
        if key in seen:
            return
        seen.add(key)
        out.append((qt, qa))

    _emit(base_title, base_artist)                       # 1 exact cleaned
    _emit(_PAREN_GROUP_RE.sub(" ", base_title), base_artist)  # 2 no parens
    _emit(_FEAT_SPLIT_RE.split(base_title)[0], base_artist)  # 3 no feat
    if base_artist:                                       # 4 combined bare
        _emit(f"{base_artist} - {base_title}", "")
    _emit(base_title, "")                                 # 5 title only
    if len(base_title) > 40:                              # 6 first words
        _emit(" ".join(base_title.split()[:5]), base_artist)
    return out


# ---------------------------------------------------------------------------
# fuzzy matching + candidate ranking
# ---------------------------------------------------------------------------

def fuzzy_score(a: str, b: str) -> float:
    """Similarity of two strings in [0, 1].

    Weighted mix of difflib.SequenceMatcher ratio on normalized strings,
    Jaccard token overlap, and a shared-prefix bonus. Identical strings
    after normalization score exactly 1.0.
    """
    na = normalize_for_match(a or "")
    nb = normalize_for_match(b or "")
    if na == nb:
        return 1.0
    if not na or not nb:
        return 0.0
    seq = difflib.SequenceMatcher(None, na, nb).ratio()
    ta, tb = set(na.split()), set(nb.split())
    union = ta | tb
    jaccard = (len(ta & tb) / len(union)) if union else 1.0
    prefix = 0
    for ca, cb in zip(na, nb):
        if ca != cb:
            break
        prefix += 1
    prefix_bonus = prefix / max(len(na), len(nb))
    score = 0.5 * seq + 0.35 * jaccard + 0.15 * prefix_bonus
    return min(1.0, max(0.0, score))


def _duration_score(delta_s: float) -> float:
    """±8 s full score, linear decay to 0 at ±20 s."""
    d = abs(float(delta_s))
    if d <= 8:
        return 1.0
    if d >= 20:
        return 0.0
    return 1.0 - (d - 8.0) / 12.0


def score_candidate(candidate: dict, title: str, artist: str,
                    duration_s: float | None = None) -> float:
    """Score one candidate dict against a cleaned (title, artist) query.

    0.50 * title similarity + 0.25 * artist similarity + 0.15 * duration
    proximity + 0.10 synced bonus; missing artist/duration count neutral
    (0.5) instead of punishing.
    """
    if not isinstance(candidate, dict):
        return 0.0
    title_sim = fuzzy_score(str(candidate.get("title") or ""), title)
    cand_artist = str(candidate.get("artist") or "")
    if not normalize_for_match(artist) or not normalize_for_match(cand_artist):
        artist_sim = 0.5
    else:
        artist_sim = fuzzy_score(cand_artist, artist)
    duration_sim = 0.5
    cand_duration = candidate.get("duration")
    if duration_s and cand_duration is not None:
        try:
            duration_sim = _duration_score(int(cand_duration) - int(duration_s))
        except (TypeError, ValueError):
            duration_sim = 0.5
    score = 0.5 * title_sim + 0.25 * artist_sim + 0.15 * duration_sim
    if candidate.get("synced"):
        score += 0.1
    return min(1.0, max(0.0, score))


def pick_best(candidates: list[dict] | None, title: str, artist: str,
              duration_s: float | None = None) -> tuple[dict | None, float]:
    """Pick the best candidate from a list.

    Returns ``(best, score)`` — ``(None, 0.0)`` when nothing qualifies.
    Duration-matching synced candidates beat same-titled spam; compare
    the score against GOOD / OK for accept/retry decisions.
    """
    best: dict | None = None
    best_score = 0.0
    for cand in candidates or []:
        if not isinstance(cand, dict):
            continue
        try:
            score = score_candidate(cand, title, artist, duration_s)
        except Exception as e:  # never crash on malformed candidates
            log.debug("candidate scoring failed: %s", e)
            continue
        if score > best_score:
            best, best_score = cand, score
    return (best, best_score)
