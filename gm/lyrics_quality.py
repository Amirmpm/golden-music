"""Golden Music 3.0 — lyrics text quality gate (Qt-free, network-free).

Decides whether a lyrics payload (from cache, embedded tags or a provider)
is worth showing, and whether a freshly found candidate should be shown to
the user for confirmation before replacing what is already stored.

The verdict dict is ``{"valid": bool, "reason": str, "action": str}``:

* ``too_short``       (< 120 chars or < 4 non-empty lines) -> search again
* ``spam_*``          (URLs, ringtone/download words, @handles/phones,
                      shouting ALL-CAPS walls, repeated line walls) ->
                      search again
* ``instrumental``    (only "instrumental"/"[inst]" markers) -> keep,
                      nothing to search for
* ``ok``              real lyrics -> keep
"""
from __future__ import annotations

import logging
import re
from collections import Counter

from gm.lyrics_query import normalize_for_match

log = logging.getLogger("app.lyrics.quality")

__all__ = ["SPAM_WORDS", "verdict", "needs_confirmation"]

# Spam / advertising words found in scraped or machine-generated "lyrics".
SPAM_WORDS: tuple[str, ...] = (
    "ringtone", "download the app", "full lyrics at", "lyricsfreak",
    "musixmatch.com", "azlyrics", "telegram.me", "t.me", "instagram.com",
)

_URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)
_HANDLE_RE = re.compile(r"(?<![\w@])@[A-Za-z0-9_]{4,}\b")
_PHONE_RE = re.compile(r"\+?\d[\d\s\-().]{6,}\d")

_MIN_CHARS = 120
_MIN_LINES = 4
_MIN_SHOUT_CHARS = 200
_MAX_UPPER_RATIO = 0.4
_MAX_REPEAT_RATIO = 0.6
_INSTRUMENTAL_TOKENS = frozenset({"instrumental", "inst"})


def _has_phone_number(content: str) -> bool:
    """True when a digit run long enough to be a phone number is present."""
    for m in _PHONE_RE.finditer(content):
        if sum(ch.isdigit() for ch in m.group(0)) >= 8:
            return True
    return False


def verdict(text: str) -> dict:
    """Classify a lyrics payload. Never raises; None/"" is simply invalid."""
    try:
        t = str(text or "")
        lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
        content = " ".join(lines).strip()
        if not content:
            return {"valid": False, "reason": "empty", "action": "search"}
        # Instrumental markers: "(instrumental)", "[inst]", plain word.
        tokens = re.sub(r"[\[\](){}]", " ", content).lower().split()
        if tokens and all(tok in _INSTRUMENTAL_TOKENS for tok in tokens):
            return {"valid": True, "reason": "instrumental", "action": "keep"}
        if len(content) < _MIN_CHARS or len(lines) < _MIN_LINES:
            return {"valid": False, "reason": "too_short", "action": "search"}
        if _URL_RE.search(content):
            return {"valid": False, "reason": "spam_url", "action": "search"}
        lowered = content.lower()
        for word in SPAM_WORDS:
            if word in lowered:
                return {"valid": False, "reason": "spam_words", "action": "search"}
        if _HANDLE_RE.search(content) or _has_phone_number(content):
            return {"valid": False, "reason": "spam_contact", "action": "search"}
        alpha = [c for c in content if c.isalpha()]
        if len(content) > _MIN_SHOUT_CHARS and alpha and \
                sum(1 for c in alpha if c.isupper()) / len(alpha) > _MAX_UPPER_RATIO:
            return {"valid": False, "reason": "spam_shouting", "action": "search"}
        normed = [n for n in (normalize_for_match(ln) for ln in lines) if n]
        if normed:
            top = max(Counter(normed).values())
            if top / len(normed) > _MAX_REPEAT_RATIO:
                return {"valid": False, "reason": "spam_repeated", "action": "search"}
        return {"valid": True, "reason": "ok", "action": "keep"}
    except Exception as e:  # never-crash policy: verdict must always answer
        log.warning("verdict() failed: %s", e)
        return {"valid": False, "reason": "error", "action": "search"}


def needs_confirmation(existing_text: str, candidate_text: str) -> bool:
    """Should the user confirm replacing existing lyrics with a candidate?

    True when the existing text is invalid (short/spam), or when the
    candidate is twice as long (a full transcription replacing a snippet).
    Both valid and similar in size -> False (safe silent upgrade).
    """
    existing = str(existing_text or "")
    candidate = str(candidate_text or "")
    if not verdict(existing)["valid"]:
        return True
    if existing and len(candidate) >= 2 * len(existing):
        return True
    return False
