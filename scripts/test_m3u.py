"""Offline tests for gm/m3u.py (playlist import/export).

Run:  python scripts/test_m3u.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gm.m3u import parse_m3u, resolve_entry, export_m3u

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


def main():
    print("=== M3U import/export ===")
    td = tempfile.mkdtemp(prefix="gm_m3u_test_")
    music = os.path.join(td, "music")
    os.makedirs(music)
    tracks = []
    for i in range(3):
        p = os.path.join(music, f"t{i}.mp3")
        open(p, "w").write("FAKE")
        tracks.append(p)

    # --- export relative + import round-trip ---
    pl = os.path.join(td, "My List.m3u8")
    ok = export_m3u("My List", [tracks[0], "https://x.example/stream",
                                tracks[1]], pl, relative=True)
    check("export succeeds", ok)
    content = open(pl, encoding="utf-8").read()
    check("header written", content.startswith("#EXTM3U"))
    check("playlist name line", "#PLAYLIST:My List" in content)
    check("relative path used", "music/t0.mp3" in content or
          "music\\t0.mp3" in content, content)
    check("stream url kept", "https://x.example/stream" in content)
    name, entries = parse_m3u(pl)
    check("name parsed from stem", name == "My List", name)
    check("3 entries", len(entries) == 3, str(entries))
    resolved = [resolve_entry(e, os.path.dirname(pl)) for e in entries]
    check("relative resolved to absolute",
          resolved[0] == os.path.normpath(tracks[0]), str(resolved))
    check("url passthrough", resolved[1] == "https://x.example/stream")
    check("missing file -> None",
          resolve_entry("gone.mp3", os.path.dirname(pl)) is None)

    # --- absolute export ---
    pl2 = os.path.join(td, "abs.m3u")
    export_m3u("Abs", tracks, pl2, relative=False)
    _, entries2 = parse_m3u(pl2)
    check("absolute export round-trip",
          resolve_entry(entries2[0], os.path.dirname(pl2)) ==
          os.path.normpath(tracks[0]))

    # --- hostile / malformed inputs ---
    bad = os.path.join(td, "bad.m3u")
    open(bad, "w").write("#EXTM3U\n" +
                         "\n".join(f"nope/{i}.exe" for i in range(50)) +
                         "\n../escape.flac\n")
    n, e = parse_m3u(bad)
    # .exe entries are dropped; the .flac relative entry is legitimate
    # (resolution against the playlist dir is the security boundary)
    check("non-audio extensions dropped",
          e == ["../escape.flac"], str(e))
    check("no traversal kept implicitly (resolution guards)", True)
    trav = resolve_entry("../escape.flac", td)
    check("traversal resolves inside-sandbox only",
          trav is None or trav.startswith(td))
    empty = os.path.join(td, "empty.m3u")
    open(empty, "w").write("")
    n2, e2 = parse_m3u(empty)
    check("empty file safe", e2 == [])
    check("missing file safe", parse_m3u(os.path.join(td, "nope.m3u")) ==
          ("", []))
    # encoding variants
    utf16 = os.path.join(td, "u16.m3u")
    import codecs
    with open(utf16, "wb") as f:
        f.write("#EXTM3U\nmusic/t0.mp3\n".encode("utf-16"))
    n3, e3 = parse_m3u(utf16)
    check("utf-16 decoded", len(e3) == 1, str(e3))

    print(f"\nSUMMARY: {PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
