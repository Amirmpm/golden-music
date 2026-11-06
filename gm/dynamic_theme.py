"""Golden Music 3.0 — Material You-style dynamic theme from album art.

Extracts a vibrant accent color from the current cover (pure-Qt quantizer:
scale to 24x24, bucket by hue with saturation/value weighting, prefer
vibrant-but-not-neon buckets) and derives a full 12-key theme dict that the
rest of the app can consume unchanged.
"""
from __future__ import annotations

import colorsys


def _hex_to_rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _rgb_to_hex(rgb) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, int(c))) for c in rgb))


def dominant_color(pixmap, prefer_vibrant: bool = True):
    """Return (r, g, b) for the most representative accent of a QPixmap."""
    try:
        if pixmap is None or pixmap.isNull():
            return None
        small = pixmap.scaled(24, 24, mode=1)  # Qt.TransformationMode.SmoothTransformation
        img = small.toImage()
        buckets: dict[int, list] = {}
        for y in range(img.height()):
            for x in range(img.width()):
                c = img.pixelColor(x, y)
                r, g, b = c.red(), c.green(), c.blue()
                h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
                if v < 0.12 or s < 0.10:      # near-black / near-white noise
                    continue
                deg = int(h * 360) // 30 * 30
                buckets.setdefault(deg, []).append((r, g, b, s, v))
        if not buckets:
            return None
        def bucket_score(items):
            s = sum(i[3] for i in items) / len(items)
            v = sum(i[4] for i in items) / len(items)
            count = len(items)
            vibrance = s * (1.0 - abs(v - 0.62))     # prefer mid-bright
            return vibrance * (0.55 + min(1.0, count / 24.0))
        ranked = sorted(buckets.items(),
                        key=lambda kv: bucket_score(kv[1]),
                        reverse=not prefer_vibrant)
        items = ranked[0][1]
        r = sum(i[0] for i in items) // len(items)
        g = sum(i[1] for i in items) // len(items)
        b = sum(i[2] for i in items) // len(items)
        return (r, g, b)
    except Exception:
        return None


def _mix(rgb1, rgb2, t: float):
    return tuple(a + (b - a) * t for a, b in zip(rgb1, rgb2))


def _shift(rgb, dh: float, ds: float, dv: float):
    h, s, v = colorsys.rgb_to_hsv(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255)
    h = (h + dh) % 1.0
    s = max(0.0, min(1.0, s + ds))
    v = max(0.0, min(1.0, v + dv))
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return (int(r * 255), int(g * 255), int(b * 255))


def theme_from_cover(pixmap, base_theme: dict, dark: bool = True) -> dict | None:
    """Build a full theme dict from the cover's dominant color.

    Dark: near-black panels with the accent as 'gold'; Light: warm-white
    panels with a deepened accent for contrast (WCAG-ish safety via V clamp).
    """
    dom = dominant_color(pixmap)
    if dom is None:
        return None
    r, g, b = dom
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    # keep accent usable: clamp saturation into [0.45, 0.85], value [0.55, 0.8]
    s = max(0.45, min(0.85, s))
    v = max(0.55, min(0.80, v))
    accent = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(h, s, v))
    accent_deep = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(h, s, v * 0.72))
    accent_light = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(h, min(0.9, s * 0.9), min(1.0, v * 1.18)))

    if dark:
        window = _mix((16, 17, 20), accent, 0.05)
        panel = _mix((22, 23, 27), accent, 0.05)
        panel2 = _mix((30, 32, 36), accent, 0.08)
        border_c = _mix((44, 46, 52), accent, 0.18)
        text = (238, 238, 240)
        muted = _mix((140, 140, 148), accent, 0.15)
        active = _mix((40, 42, 48), accent, 0.25)
        ink = (20, 20, 22)
    else:
        window = _mix((248, 247, 245), accent, 0.04)
        panel = (252, 251, 250)
        panel2 = _mix((240, 238, 236), accent, 0.06)
        border_c = _mix((216, 214, 212), accent, 0.12)
        text = (28, 28, 30)
        muted = _mix((110, 110, 116), accent, 0.10)
        active = _mix((232, 230, 228), accent, 0.18)
        ink = (250, 250, 250)

    t = {
        "name": "album_dynamic",
        "window_bg": _rgb_to_hex(window),
        "panel_bg": _rgb_to_hex(panel),
        "panel_bg_2": _rgb_to_hex(panel2),
        "border": _rgb_to_hex(border_c),
        "text": _rgb_to_hex(text),
        "muted": _rgb_to_hex(muted),
        "gold": _rgb_to_hex(accent),
        "gold_light": _rgb_to_hex(accent_light),
        "gold_deep": _rgb_to_hex(accent_deep),
        "active": _rgb_to_hex(active),
        "is_dark": bool(dark),
    }
    return t
