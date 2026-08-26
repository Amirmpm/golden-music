"""Download modern icon sets from Iconify API for GoldenMusic icon preview.

Fetches the app's 37 icons in 4 different styles and saves them to
C:/Users/Meg/Desktop/New folder (2)/<set>/ so the user can pick a set.
"""
import json
import time
import urllib.request
import urllib.parse
from pathlib import Path

OUT_DIR = Path(r"C:\Users\Meg\Desktop\New folder (2)")
GOLD = "#d4a85a"

# App icons (from icons.py usage across the codebase)
APP_ICONS = [
    "play", "pause", "stop", "next", "prev", "shuffle",
    "repeat", "repeat-one", "heart", "heart-filled",
    "volume-high", "volume-low", "volume-mute",
    "library", "favorites-nav", "folder-add", "folder",
    "sun", "moon", "settings", "music-note", "search",
    "sort-asc", "sort-desc", "queue", "equalizer", "timer",
    "minimize", "chevron-left", "chevron-right", "menu",
    "list-lines", "lyrics", "more", "trash", "close", "refresh",
]

# Each set: (prefix, style-suffix, name-mapping, colorize?)
SETS = {
    # 1) Lucide — stroke-based, clean & consistent (what the app uses now, but official)
    "lucide": {
        "prefix": "lucide",
        "suffix": "",
        "map": {
            "next": "skip-forward", "prev": "skip-back",
            "repeat-one": "repeat-1", "heart-filled": "heart",
            "volume-high": "volume-2", "volume-low": "volume-1",
            "volume-mute": "volume-x", "library": "disc-3",
            "favorites-nav": "heart", "folder-add": "circle-plus",
            "music-note": "music",
            "sort-asc": "arrow-up-narrow-wide", "sort-desc": "arrow-down-wide-narrow",
            "timer": "alarm-clock", "minimize": "picture-in-picture-2",
            "list-lines": "align-left", "lyrics": "message-square-quote",
            "more": "ellipsis-vertical", "close": "x",
            "refresh": "refresh-cw", "stop": "square-stop",
            "queue": "list-music", "equalizer": "sliders-horizontal",
        },
    },
    # 2) Phosphor — duotone, elegant two-layer look
    "phosphor": {
        "prefix": "ph",
        "suffix": "-duotone",
        "map": {
            "next": "skip-forward", "prev": "skip-back",
            "repeat-one": "repeat-once", "heart-filled": "heart-fill",
            "volume-high": "speaker-high", "volume-low": "speaker-low",
            "volume-mute": "speaker-slash", "library": "vinyl-record",
            "favorites-nav": "heart-straight", "folder-add": "plus-circle",
            "music-note": "music-notes", "sort-asc": "sort-ascending",
            "sort-desc": "sort-descending", "queue": "playlist",
            "equalizer": "sliders-horizontal", "timer": "clock-countdown",
            "minimize": "picture-in-picture", "list-lines": "list-dashes",
            "lyrics": "chats-circle", "more": "dots-three-outline-vertical",
            "settings": "gear-six", "search": "magnifying-glass",
            "close": "x", "refresh": "arrows-clockwise",
            "chevron-left": "caret-left", "chevron-right": "caret-right",
            "menu": "list", "stop": "stop-circle",
        },
    },
    # 3) Solar — bold geometric, very modern
    "solar": {
        "prefix": "solar",
        "suffix": "-bold",
        "map": {
            "next": "forward", "prev": "rewind-back", "shuffle": "shuffle",
            "repeat-one": "repeat-one", "heart-filled": "heart-bold",
            "volume-high": "volume-bold", "volume-low": "volume-bold",
            "volume-mute": "volume-cross", "library": "vinyl-record",
            "favorites-nav": "heart", "folder-add": "add-circle",
            "music-note": "music-note-2", "sort-asc": "sort-from-top-to-bottom",
            "sort-desc": "sort-from-bottom-to-top", "queue": "playlist",
            "timer": "stopwatch", "minimize": "to-pip",
            "chevron-left": "alt-arrow-left", "chevron-right": "alt-arrow-right",
            "menu": "menu-dots", "list-lines": "text",
            "lyrics": "chat-square-code", "more": "menu-dots-circle",
            "settings": "settings-minimalistic", "search": "magnifier",
            "trash": "trash-bin-trash", "close": "close-circle",
            "sun": "sun-2", "moon": "moon-stars", "stop": "stop-circle",
            "play": "play-circle", "pause": "pause-circle",
            "folder": "folder", "equalizer": "slider-horizontal",
            "refresh": "refresh", "stop": "stop-circle",
        },
    },
    # 4) MynaUI — solid rounded, soft minimal
    "mynaui": {
        "prefix": "mynaui",
        "suffix": "-solid",
        "map": {
            "next": "skip-forward-solid", "prev": "rewind-solid",
            "shuffle": "shuffle-solid", "repeat": "repeat-solid",
            "repeat-one": "repeat-solid", "heart-filled": "heart-solid",
            "volume-high": "volume-high-solid", "volume-low": "volume-low-solid",
            "volume-mute": "volume-off-solid", "library": "album-solid",
            "favorites-nav": "heart-solid", "folder-add": "plus-circle-solid",
            "music-note": "music-solid", "sort-asc": "arrow-up-solid",
            "sort-desc": "arrow-down-solid", "queue": "list-solid",
            "equalizer": "cog-four-solid", "timer": "alarm-clock-solid",
            "minimize": "minimize-solid", "chevron-left": "chevron-left-solid",
            "chevron-right": "chevron-right-solid", "menu": "menu-solid",
            "list-lines": "text-align-left-solid", "lyrics": "chat-messages-solid",
            "more": "dots-solid", "settings": "cog-solid",
            "search": "search-solid", "trash": "trash-solid", "close": "x-solid",
            "sun": "sun-medium-solid", "moon": "moon-star-solid",
            "stop": "stop-octagon-solid", "play": "play-solid",
            "pause": "pause-solid", "folder": "folder-solid",
            "refresh": "refresh-solid", "stop": "stop-octagon-solid",
        },
    },
}


def fetch_svg(prefix: str, name: str, color: str | None = None) -> str:
    url = f"https://api.iconify.design/{prefix}.json?icons={urllib.parse.quote(name)}"
    if color:
        url += f"&color={urllib.parse.quote(color)}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    icon = (data.get("icons") or {}).get(name)
    if not icon:
        return ""
    body = icon["body"]
    w = icon.get("width", data.get("width", 24))
    h = icon.get("height", data.get("height", 24))
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}">{body}</svg>'
    )
    return svg


def main():
    total_ok = total_fail = 0
    for set_name, cfg in SETS.items():
        out_dir = OUT_DIR / set_name
        out_dir.mkdir(parents=True, exist_ok=True)
        ok, fail = [], []
        for icon in APP_ICONS:
            mapped = cfg["map"].get(icon, icon)
            ph = mapped if set_name != "phosphor" else mapped
            name = mapped + cfg["suffix"] if not mapped.endswith(cfg["suffix"]) else mapped
            try:
                svg = fetch_svg(cfg["prefix"], name, color=GOLD if set_name in ("solar", "mynaui") else None)
                if not svg:
                    fail.append(f"{icon}->{name}")
                    continue
                (out_dir / f"{icon}.svg").write_text(svg, encoding="utf-8")
                ok.append(icon)
                time.sleep(0.05)
            except Exception as e:  # noqa: BLE001
                fail.append(f"{icon}->{name}: {e}")
        print(f"[{set_name}] OK={len(ok)} FAIL={len(fail)}")
        for f in fail:
            print(f"   !! {f}")
        total_ok += len(ok)
        total_fail += len(fail)
    print(f"\nTOTAL: ok={total_ok} fail={total_fail}")


if __name__ == "__main__":
    main()
