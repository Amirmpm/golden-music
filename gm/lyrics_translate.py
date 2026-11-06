"""Golden Music 2.3 — bilingual lyrics handling (Qt-free, network-free).

Persian libraries are listened to bilingually: the UI shows the original
line and, when available, a translation. Three sources of translations
are supported here:

* sidecar ``.lrc`` files whose translation lines carry the SAME
  timestamps as the original lyrics (``parse_translation_sidecar``);
* inline ``[translation: ...]`` tags on individual LRC lines;
* bilingual lines separated by ``|||`` or ``///``
  (``extract_inline_translation``).

``merge_translations`` aligns original and translated lines by timestamp
(exact match, else nearest within 800 ms, else no translation).
"""
from __future__ import annotations

import logging
import os
import re

log = logging.getLogger("app.lyrics.translate")

__all__ = [
    "SEPARATORS", "NEAREST_WINDOW_MS",
    "parse_translation_sidecar", "merge_translations",
    "extract_inline_translation",
]

MAX_SIDECAR_BYTES = 2 * 1024 * 1024   # 2 MiB — same ceiling as lyrics.py
NEAREST_WINDOW_MS = 800
SEPARATORS: tuple[str, ...] = ("|||", "///")

_LRC_TS = re.compile(r"\[(\d{1,2}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
_TRANSLATION_TAG = re.compile(r"\[translation:\s*(.*?)\]", re.IGNORECASE)


def _stamp_ms(mm: str, ss: str, frac: str | None) -> int:
    return int(mm) * 60_000 + int(ss) * 1000 + int((frac or "0").ljust(3, "0")[:3])


def parse_translation_sidecar(path: str) -> list[tuple[int | None, str]]:
    """Read a translation sidecar (.lrc style, same timestamps as the
    original). Lines may also use the inline ``[translation: ...]`` tag.

    Returns ``[(time_ms_or_None, translated_text), ...]`` in file order;
    I/O errors return [] (never crash).
    """
    try:
        if not path or not os.path.isfile(path) \
                or os.path.getsize(path) > MAX_SIDECAR_BYTES:
            return []
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError as e:
        log.debug("translation sidecar read failed %s: %s", path, e)
        return []
    entries: list[tuple[int | None, str]] = []
    for raw in text.splitlines():
        stamps = _LRC_TS.findall(raw)
        content = _LRC_TS.sub("", raw).strip()
        m = _TRANSLATION_TAG.search(content)
        if m:
            content = m.group(1).strip()
        if not content:
            continue
        if stamps:
            for match in _LRC_TS.finditer(raw):
                entries.append((_stamp_ms(*match.groups()), content))
        else:
            entries.append((None, content))
    return entries


def merge_translations(
    original: list[tuple[int | None, str]] | None,
    translation: list[tuple[int | None, str]] | None,
) -> list[tuple[int | None, str, str | None]]:
    """Align original and translation lines by timestamp.

    Exact ms match first; else the nearest translation within
    ``NEAREST_WINDOW_MS`` (each translation is used at most once); else
    ``None``. Untimed original lines keep their text with ``None``.
    Returns ``[(time_ms_or_None, original, translation_or_None), ...]``.
    """
    pool: dict[int, str] = {}
    for ms, txt in (translation or []):
        if ms is None:
            continue
        try:
            t = int(ms)
        except (TypeError, ValueError):
            continue
        pool.setdefault(t, str(txt))
    used: set[int] = set()
    merged: list[tuple[int | None, str, str | None]] = []
    for ms, txt in (original or []):
        text = str(txt)
        try:
            t = int(ms) if ms is not None else None
        except (TypeError, ValueError):
            t = None
        if t is None:
            merged.append((None, text, None))
            continue
        chosen: int | None = None
        if t in pool and t not in used:
            chosen = t
        else:
            for cand_t in pool:
                if cand_t in used:
                    continue
                dt = abs(cand_t - t)
                if dt <= NEAREST_WINDOW_MS and \
                        (chosen is None or dt < abs(chosen - t)):
                    chosen = cand_t
        if chosen is None:
            merged.append((t, text, None))
        else:
            used.add(chosen)
            merged.append((t, text, pool[chosen]))
    return merged


def extract_inline_translation(
    lrc_text: str,
) -> list[tuple[int | None, str, str | None]]:
    """Extract bilingual pairs from LRC text.

    Supported per line: ``original ||| translation``, ``original /// translation``
    and ``original [translation: X]``. Returns
    ``[(time_ms_or_None, original, translation_or_None), ...]``; lines
    without any translation marker come back with ``None`` so the caller
    can rebuild a full bilingual list from a single file.
    """
    out: list[tuple[int | None, str, str | None]] = []
    for raw in (lrc_text or "").splitlines():
        stamps = _LRC_TS.findall(raw)
        content = _LRC_TS.sub("", raw).strip()
        if not content:
            continue
        translation: str | None = None
        m = _TRANSLATION_TAG.search(content)
        if m:
            translation = m.group(1).strip() or None
            content = _TRANSLATION_TAG.sub("", content).strip()
        else:
            for sep in SEPARATORS:
                if sep in content:
                    left, _sep, right = content.partition(sep)
                    content = left.strip()
                    translation = right.strip() or None
                    break
        if not content and not translation:
            continue
        if stamps:
            for match in _LRC_TS.finditer(raw):
                out.append((_stamp_ms(*match.groups()), content, translation))
        else:
            out.append((None, content, translation))
    return out
