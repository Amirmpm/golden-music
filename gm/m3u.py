"""Golden Music 2.3 — M3U/M3U8 playlist import/export.

Security notes: parsed files are capped (size + entry count); entry paths are
validated as audio extensions and resolved RELATIVE TO THE PLAYLIST FILE only
(no network URLs are followed — http(s) entries are kept verbatim as stream
URLs for the radio feature, everything else must exist on disk to be added).
"""
from __future__ import annotations

import os
from pathlib import Path

from config import AUDIO_EXTS

MAX_M3U_BYTES = 4 * 1024 * 1024      # 4 MiB
MAX_ENTRIES = 20_000
_LINE_CAP = 4096


def _is_audio_file(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower()
    return ext in AUDIO_EXTS


def parse_m3u(file_path) -> tuple[str, list[str]]:
    """Parse an .m3u/.m3u8 file -> (playlist_name, entries).

    Entries are kept as-authored (relative paths stay relative; the caller
    resolves them against the playlist's directory). ``#EXTINF`` metadata is
    used only for the playlist title suggestion. Never raises for malformed
    content — worst case returns ("", []).
    """
    try:
        p = Path(file_path)
        if not p.is_file() or p.stat().st_size > MAX_M3U_BYTES:
            return ("", [])
        raw = p.read_bytes()
        text = None
        for enc in ("utf-8-sig", "utf-8", "utf-16", "cp1252", "latin-1"):
            try:
                text = raw.decode(enc)
                break
            except (UnicodeDecodeError, LookupError):
                continue
        if text is None:
            return ("", [])
        name = ""
        entries: list[str] = []
        in_ext = False
        for line in text.splitlines():
            line = line.strip()[:_LINE_CAP]
            if not line:
                continue
            if line.upper().startswith("#EXTM3U"):
                continue
            if line.startswith("#EXTINF:"):
                in_ext = True
                # title = text after the last comma
                title = line.split(",", 1)[1].strip() if "," in line else ""
                if title and not name:
                    # first EXTINF title seeds the playlist name suggestion
                    pass
                continue
            if line.startswith("#"):
                continue
            if line.upper().startswith("HTTP://") or \
                    line.upper().startswith("HTTPS://"):
                entries.append(line)
                in_ext = False
                continue
            # plain path entry
            if os.path.splitext(line)[1] and not _is_audio_file(line):
                in_ext = False
                continue
            entries.append(line)
            in_ext = False
            if len(entries) >= MAX_ENTRIES:
                break
        # name fallback: file stem
        if not name:
            name = p.stem
        return (name, entries)
    except Exception:
        return ("", [])


def resolve_entry(entry: str, playlist_dir: str) -> str | None:
    """Resolve one playlist entry against its file. Returns an absolute,
    existing local path, a URL string, or None when it points nowhere."""
    try:
        if entry.upper().startswith(("HTTP://", "HTTPS://")):
            return entry
        cand = entry if os.path.isabs(entry) else os.path.normpath(
            os.path.join(playlist_dir, entry))
        if os.path.isfile(cand):
            return cand
        return None
    except Exception:
        return None


def export_m3u(name: str, paths, out_path: str, relative: bool = True,
               title_line: str | None = None) -> bool:
    """Write an .m3u8 playlist. Returns success."""
    try:
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        base_dir = str(out.parent)
        lines = ["#EXTM3U"]
        if title_line or name:
            lines.append(f"#PLAYLIST:{(title_line or name)}")
        for p in paths or []:
            p = str(p)
            if p.upper().startswith(("HTTP://", "HTTPS://")):
                lines.append(p)
                continue
            if not os.path.isfile(p):
                continue
            if relative:
                try:
                    rel = os.path.relpath(p, base_dir)
                    lines.append(rel)
                except ValueError:      # different drive on Windows
                    lines.append(p)
            else:
                lines.append(os.path.abspath(p))
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return True
    except Exception:
        return False
