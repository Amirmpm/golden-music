"""Golden Music 3.0 — lyrics persistence (Qt-free, mutagen lazily imported).

Writing lyrics back out — sidecar files and embedded tags — plus the
offset shift used by the sync controls. All failures raise
``LyricsIOError`` (with a machine-readable ``reason``) instead of
crashing: the caller decides whether to surface the error.

Embedded formats:

* MP3  -> USLT (plain text; the serialized LRC when the lyrics are
  synced, so other players can resync) + SYLT (synced, UTF-8,
  format=2 i.e. milliseconds, type=1 lyrics) — written together for
  maximum compatibility;
* FLAC/OGG and friends -> Vorbis ``LYRICS`` comment (serialized LRC when
  synced);
* MP4/M4A -> ``©lyr`` atom.

An empty ``lines`` list never modifies anything on disk.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("app.lyrics.io")

__all__ = [
    "LyricsIOError", "to_lrc_text", "save_to_sidecar",
    "build_uslt", "build_sylt", "save_embedded", "apply_offset",
]

MAX_LRC_BYTES = 2 * 1024 * 1024   # 2 MiB — payload ceiling


class LyricsIOError(Exception):
    """Lyrics write failure. ``reason`` is a stable machine-readable code
    ("empty", "not_found", "exists", "unsupported_format", "write_failed")."""

    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(message or reason)
        self.reason = reason


def _clean_lines(lines) -> list[tuple[int | None, str]]:
    """Coerce arbitrary input into [(ms|None, text)] with no empty texts."""
    out: list[tuple[int | None, str]] = []
    for item in lines or []:
        try:
            ms, text = item[0], str(item[1])
        except (TypeError, IndexError, KeyError, ValueError):
            continue
        if not text.strip():
            continue
        if ms is None:
            out.append((None, text.rstrip()))
            continue
        try:
            out.append((max(0, int(ms)), text.rstrip()))
        except (TypeError, ValueError):
            out.append((None, text.rstrip()))
    return out


def to_lrc_text(lines, synced: bool) -> str:
    """Serialize ``[(ms|None, text)]`` to LRC text (or plain text).

    Timestamps are ``[mm:ss.mmm]``; untimed lines and blanks are skipped
    when synced. Local implementation — deliberately independent of the
    config-importing legacy lyrics.py / lrc_cache.py.
    """
    out: list[str] = []
    if synced:
        for ms, text in (lines or []):
            if ms is None or not (text or "").strip():
                continue
            total = max(0, int(ms))
            mm, rest = divmod(total, 60_000)
            ss, mmm = divmod(rest, 1000)
            out.append(f"[{mm:02d}:{ss:02d}.{mmm:03d}]{text}")
    else:
        for _ms, text in (lines or []):
            if (text or "").strip():
                out.append(text)
    return "\n".join(out) + ("\n" if out else "")


def save_to_sidecar(filepath: str, lines, synced: bool,
                    overwrite: bool = False) -> bool:
    """Write ``<audio basename>.lrc`` next to the audio file.

    Raises ``LyricsIOError("exists")`` when a sidecar is already present
    and ``overwrite`` is False (user content always wins). Returns True
    on success.
    """
    clean = _clean_lines(lines)
    if not clean:
        raise LyricsIOError("empty", "no lyric lines to write")
    if not filepath:
        raise LyricsIOError("not_found", "no audio path given")
    text = to_lrc_text(clean, synced)
    if not text.strip():
        raise LyricsIOError("empty", "nothing serializable to write")
    if len(text.encode("utf-8")) > MAX_LRC_BYTES:
        raise LyricsIOError("too_large", "sidecar exceeds 2 MiB")
    target = os.path.splitext(filepath)[0] + ".lrc"
    if os.path.exists(target) and not overwrite:
        raise LyricsIOError("exists", f"sidecar already present: {target}")
    tmp = target + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, target)
    except OSError as e:
        log.warning("sidecar write failed %s: %s", target, e)
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass
        raise LyricsIOError("write_failed", str(e)) from e
    return True


# ---------------------------------------------------------------------------
# embedded tags (mutagen, lazily imported)
# ---------------------------------------------------------------------------

def build_uslt(lines, synced: bool):
    """Build a mutagen ``USLT`` frame (plain text; LRC text when synced)."""
    from mutagen.id3 import USLT, Encoding   # lazy

    text = to_lrc_text(_clean_lines(lines), synced)
    if not text:
        raise LyricsIOError("empty", "no lyric lines for USLT")
    return USLT(encoding=Encoding.UTF8, lang="eng", desc="", text=text)


def build_sylt(lines):
    """Build a mutagen ``SYLT`` frame: UTF-8, format=2 (milliseconds),
    type=1 (lyrics), text=[(text, ms), ...]."""
    from mutagen.id3 import SYLT, Encoding   # lazy

    entries = [(text, int(ms)) for ms, text in _clean_lines(lines)
               if ms is not None]
    if not entries:
        raise LyricsIOError("empty", "no timed lines for SYLT")
    return SYLT(encoding=Encoding.UTF8, lang="eng", format=2, type=1,
                desc="", text=entries)


def save_embedded(filepath: str, lines, synced: bool) -> bool:
    """Embed lyrics into the audio file's tags. Returns True on success.

    Failures raise ``LyricsIOError``: "empty" (nothing to write — the
    file is NOT modified), "not_found", "unsupported_format" or
    "write_failed". Never crashes the caller on its own — the caller
    decides how to surface the error.
    """
    clean = _clean_lines(lines)
    if not clean:
        raise LyricsIOError("empty", "no lyric lines to embed")
    if not filepath or not os.path.isfile(filepath):
        raise LyricsIOError("not_found", f"audio file missing: {filepath!r}")
    ext = os.path.splitext(filepath)[1].lower()
    try:
        if ext == ".mp3":
            return _save_mp3(filepath, clean, synced)
        if ext in (".m4a", ".mp4"):
            return _save_mp4(filepath, clean, synced)
        return _save_generic(filepath, clean, synced)
    except LyricsIOError:
        raise
    except Exception as e:
        log.warning("embedded lyrics write failed %s: %s", filepath, e)
        raise LyricsIOError("write_failed", str(e)) from e


def _plain_text(clean: list[tuple[int | None, str]], synced: bool) -> str:
    return to_lrc_text(clean, synced)


def _save_mp3(filepath: str, clean, synced: bool) -> bool:
    from mutagen.id3 import ID3, ID3NoHeaderError   # lazy

    try:
        tags = ID3(filepath)
    except ID3NoHeaderError:
        # No ID3 yet: only stamp a fresh tag onto a REAL MPEG file,
        # never onto random bytes that merely carry a .mp3 name.
        try:
            from mutagen.mp3 import MP3   # lazy

            MP3(filepath)
        except Exception:
            raise LyricsIOError(
                "unsupported_format", f"not a valid MP3: {filepath}") from None
        tags = ID3()
    except Exception as e:
        raise LyricsIOError("unsupported_format", str(e)) from e
    tags.delall("USLT")
    tags.delall("SYLT")
    if synced:
        # SYLT carries the millisecond timings; USLT keeps the LRC text
        # so players without SYLT support can still resync.
        tags.add(build_sylt(clean))
        tags.add(build_uslt(clean, True))
    else:
        tags.add(build_uslt(clean, False))
    tags.save(filepath)
    return True


def _save_mp4(filepath: str, clean, synced: bool) -> bool:
    from mutagen.mp4 import MP4   # lazy

    try:
        m = MP4(filepath)
    except Exception as e:
        raise LyricsIOError("unsupported_format", str(e)) from e
    if m.tags is None:
        m.add_tags()
    m.tags["\xa9lyr"] = [_plain_text(clean, synced)]
    m.save()
    return True


def _save_generic(filepath: str, clean, synced: bool) -> bool:
    """FLAC / OGG / Vorbis-comment-style tags via mutagen.File."""
    from mutagen import File as MutagenFile   # lazy

    try:
        m = MutagenFile(filepath)
    except Exception as e:
        raise LyricsIOError("unsupported_format", str(e)) from e
    if m is None or not callable(getattr(m, "save", None)):
        raise LyricsIOError("unsupported_format",
                            f"unrecognized audio format: {filepath}")
    if m.tags is None:
        add_tags = getattr(m, "add_tags", None)
        if not callable(add_tags):
            raise LyricsIOError("unsupported_format",
                                f"format has no tag layer: {filepath}")
        add_tags()
    m.tags["LYRICS"] = [_plain_text(clean, synced)]
    m.save()
    return True


# ---------------------------------------------------------------------------
# offset shifting
# ---------------------------------------------------------------------------

def apply_offset(lines, offset_ms: int) -> list[tuple[int | None, str]]:
    """Shift all synced line timestamps by ``offset_ms``.

    Negative results clamp at 0; the list is re-sorted (stable) so the
    clamped result stays monotonic. Untimed lines keep their text and
    sort after the timed ones.
    """
    try:
        off = int(offset_ms)
    except (TypeError, ValueError):
        off = 0
    out: list[tuple[int | None, str]] = []
    for ms, text in (lines or []):
        if ms is None:
            out.append((None, str(text)))
            continue
        try:
            out.append((max(0, int(ms) + off), str(text)))
        except (TypeError, ValueError):
            out.append((None, str(text)))
    out.sort(key=lambda e: (e[0] is None, e[0] if e[0] is not None else 0))
    return out
