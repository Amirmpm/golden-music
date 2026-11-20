"""Golden Music v3 — unified Liquid Glass design system.

Replaces the old 12-theme picker as the PRIMARY appearance system with one
coherent translucent material, following the Nuvx Desktop visual language:

  * dark base ``#0C0A0E``-family background with an accent-tinted gradient
  * white-alpha (dark) / black-alpha (light) glass panels whose opacity is
    the ``transparency`` setting
  * accent colour, optionally derived from the current album artwork
  * an 8/5/14/18 px corner-radius ladder (Nuvx preset defaults) scaled by
    the ``corner`` setting
  * native Mica / Acrylic window backdrops on Windows 11 22H2+ (DWM), with
    a graceful opaque fallback everywhere else
  * density, animation-intensity and reduced-motion controls

The module deliberately produces a theme dict with the SAME keys the whole
code base already consumes (``window_bg``, ``panel_bg``, ``panel_bg_2``,
``border``, ``text``, ``muted``, ``gold``, ``gold_light``, ``gold_deep``,
``active``, ``is_dark``, ``name``) so every existing widget keeps working.
Colours are QSS-safe ``rgba(r,g,b,a)`` strings when translucency is on.

Migration: :func:`migrate_legacy_config` maps an old 12-theme config to a
GlassSettings (accent = the old theme's accent colour) and PRESERVES the old
``theme`` key so a downgrade can still boot with the legacy system.
"""
from __future__ import annotations

import colorsys
import sys
from dataclasses import dataclass, asdict

# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def hex_to_rgb(h: str):
    h = (h or "").strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return None
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


def rgb_to_hex(r: int, g: int, b: int) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(_clamp(int(c), 0, 255) for c in (r, g, b)))


def rgba(r, g, b, a: float) -> str:
    """QSS/CSS-safe rgba() string. a in 0..1."""
    return f"rgba({int(_clamp(r,0,255))},{int(_clamp(g,0,255))},{int(_clamp(b,0,255))},{round(_clamp(a,0.0,1.0),3)})"


def glass_alpha(base, alpha: float) -> str:
    """Same RGB as ``base`` (an ``rgba()``/``rgb()`` theme glass colour)
    re-authored at a fixed alpha 0..1.

    Lets widgets use the exact Nuvx glass opacities (hover 0.10, selected
    0.16, control-hover 0.14 …) while keeping the theme's own glass colour,
    so dark and light themes both stay correct. Non-rgba input is returned
    unchanged.
    """
    s = str(base or "").strip()
    if not (s.startswith("rgba(") or s.startswith("rgb(")):
        return base
    inner = s[s.find("(") + 1:s.rfind(")")]
    parts = [p.strip() for p in inner.split(",")]
    if len(parts) < 3:
        return base
    try:
        r, g, b = (int(float(parts[0])), int(float(parts[1])),
                   int(float(parts[2])))
    except ValueError:
        return base
    return rgba(r, g, b, alpha)


def _lighten(hex_color: str, pct: float) -> str:
    rgb = hex_to_rgb(hex_color)
    if rgb is None:
        return hex_color
    r, g, b = rgb
    f = _clamp(pct / 100.0, 0, 1)
    return rgb_to_hex(r + (255 - r) * f, g + (255 - g) * f, b + (255 - b) * f)


def _darken(hex_color: str, pct: float) -> str:
    rgb = hex_to_rgb(hex_color)
    if rgb is None:
        return hex_color
    r, g, b = rgb
    f = 1.0 - _clamp(pct / 100.0, 0, 1)
    return rgb_to_hex(r * f, g * f, b * f)


def _hsl(hex_color: str):
    rgb = hex_to_rgb(hex_color)
    if rgb is None:
        return (0.0, 0.0, 0.0)
    r, g, b = (c / 255.0 for c in rgb)
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    return (h, s, l)


def accent_readable(hex_color: str, is_dark: bool, max_boost: float = 0.45) -> str:
    """Keep an accent usable as TEXT on its background (Nuvx darkens an
    over-bright accent in light mode and brightens a too-dark accent in
    dark mode)."""
    h, s, l = _hsl(hex_color)
    if is_dark:
        if l < 0.45:
            l = 0.45 + (0.45 - l) * 0.7
    else:
        if l > 0.62:                      # too bright on a light page
            l = max(0.32, l - max_boost)
        elif l < 0.18:
            l = 0.32
    r, g, b = (int(round(c * 255)) for c in colorsys.hls_to_rgb(h, l, s))
    return rgb_to_hex(r, g, b)


# ---------------------------------------------------------------------------
# Settings model
# ---------------------------------------------------------------------------

MODES = ("system", "light", "dark")
DENSITIES = ("compact", "comfortable", "spacious")
BACKGROUNDS = ("solid", "tint", "gradient")
DEFAULT_ACCENT = "#e9b957"        # Golden Music signature champagne gold


@dataclass
class GlassSettings:
    """User-tunable Liquid Glass appearance — every value validated and
    clamped so a corrupt config can never break rendering."""

    mode: str = "system"                 # system | light | dark
    accent: str = DEFAULT_ACCENT
    accent_from_art: bool = True         # derive accent from album artwork
    transparency: int = 72                # 0..100 (panel opacity %)
    blur: int = 100                      # 0..200 — DWM backdrop strength
    corner: int = 100                    # 50..150 — radius-ladder scale %
    density: str = "comfortable"         # compact | comfortable | spacious
    anim_intensity: int = 100             # 0..200 — animation speed/distance %
    reduced_motion: bool = False
    background: str = "gradient"         # solid | tint | gradient

    # ---------------- validation ----------------
    def validate(self) -> "GlassSettings":
        if self.mode not in MODES:
            self.mode = "system"
        if self.density not in DENSITIES:
            self.density = "comfortable"
        if self.background not in BACKGROUNDS:
            self.background = "gradient"
        rgbv = hex_to_rgb(self.accent)
        self.accent = self.accent if rgbv else DEFAULT_ACCENT
        if not isinstance(self.transparency, int):
            self.transparency = 72
        self.transparency = _clamp(self.transparency, 0, 100)
        self.blur = _clamp(int(self.blur or 100), 0, 200)
        self.corner = _clamp(int(self.corner or 100), 50, 150)
        self.anim_intensity = _clamp(int(self.anim_intensity or 100), 0, 200)
        self.accent_from_art = bool(self.accent_from_art)
        self.reduced_motion = bool(self.reduced_motion)
        return self

    # ---------------- persistence ----------------
    def to_dict(self) -> dict:
        d = asdict(self)
        d["transparency"] = int(self.transparency)
        return d

    @classmethod
    def from_dict(cls, d: dict | None) -> "GlassSettings":
        if not isinstance(d, dict):
            return cls().validate()
        allowed = {f for f in cls.__dataclass_fields__}
        kwargs = {k: v for k, v in d.items() if k in allowed}
        try:
            return cls(**kwargs).validate()
        except TypeError:
            return cls().validate()

    # ---------------- derived values ----------------
    def is_dark(self, os_prefers_dark=None) -> bool:
        if self.mode == "dark":
            return True
        if self.mode == "light":
            return False
        if os_prefers_dark is None:
            os_prefers_dark = _os_prefers_dark_default()
        return bool(os_prefers_dark)

    def panel_alpha(self) -> float:
        """Base glass panel opacity 0..1 — Nuvx default feel ≈ 0.28 dark /
        0.42 light at transparency=72."""
        t = self.transparency / 100.0
        return _clamp(0.06 + 0.40 * t, 0.05, 0.92)

    def anim_ms(self, base_ms: int) -> int:
        """Scale an animation duration by intensity; 0 when reduced motion."""
        if self.reduced_motion:
            return 0
        return max(1, int(base_ms * 100.0 / max(1, self.anim_intensity)))

    def spacing(self, base: int) -> int:
        return {"compact": max(2, base - 4), "comfortable": base,
                "spacious": base + 4}[self.density]

    def radius(self, kind: str) -> int:
        """Nuvx radius ladder (card 8, thumb 5, panel 14, chrome 18),
        scaled by the corner setting (50..150 %)."""
        base = {"card": 8, "thumb": 5, "panel": 14, "chrome": 18,
                "chip": 10, "pill": 19, "toggle": 12, "toast": 12}.get(kind, 10)
        return max(2, min(28, round(base * self.corner / 100.0)))


def _os_prefers_dark_default() -> bool:
    try:
        from autottheme import windows_prefers_dark  # noqa: WPS433
        return windows_prefers_dark()
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Legacy theme migration (12-theme picker -> Liquid Glass)
# ---------------------------------------------------------------------------

def migrate_legacy_config(cfg: dict) -> tuple[GlassSettings, dict]:
    """Derive a GlassSettings from an old-style config.

    The old ``theme`` key is PRESERVED (never removed) so a rollback build
    can still read it. Accent = the legacy theme's accent colour; dark/light
    follows the legacy theme's family. Returns (settings, summary dict for
    the migration log/report).
    """
    from config import Theme                                   # late import (cycle-safe)
    legacy = Theme.normalize(str(cfg.get("theme") or Theme.DEFAULT_NAME))
    old = Theme.get(legacy)
    auto = bool(cfg.get("auto_theme", False))
    settings = GlassSettings(
        mode=("system" if auto else ("dark" if old.get("is_dark", True) else "light")),
        accent=old.get("gold", DEFAULT_ACCENT),
        accent_from_art=True,
    ).validate()
    summary = {
        "legacy_theme": legacy,
        "legacy_is_dark": bool(old.get("is_dark", True)),
        "legacy_auto_theme": auto,
        "migrated_accent": settings.accent,
        "migrated_mode": settings.mode,
    }
    return settings, summary


# ---------------------------------------------------------------------------
# Theme-dict generation (drop-in replacement for Theme.get)
# ---------------------------------------------------------------------------

# Nuvx-inspired base neutrals
_DARK_BASE = "#0c0a0e"
_LIGHT_BASE = "#f6f4f8"


def build_glass_theme(settings: GlassSettings, is_dark: bool | None = None,
                      os_prefers_dark=None) -> dict:
    """Produce the application theme dict in the legacy key shape.

    IMPORTANT compatibility rule: every legacy key (``window_bg``,
    ``panel_bg``, ``panel_bg_2``, ``border``, ``text``, ``muted``,
    ``gold*``, ``active``) is a plain HEX string so ``QColor(theme[k])``
    painters and the QPalette mirror keep working. The TRANSLUCENT
    material is exposed as rgba() only through the dedicated
    ``glass_*`` keys (consumed by build_qss via :func:`glassify_for_qss`
    and by the new v3 widgets) — never through the legacy keys.
    """
    s = settings.validate()
    if is_dark is None:
        is_dark = s.is_dark(os_prefers_dark)

    accent = accent_readable(s.accent, is_dark)
    alpha = s.panel_alpha()

    if is_dark:
        base = _DARK_BASE
        text, muted = "#f2f0f5", "#b2acba"
        panel_rgb, panel2_rgb = (28, 24, 32), (44, 38, 50)
        glass_rgb = (255, 255, 255)          # white-alpha glass on dark
        scrim = rgba(0, 0, 0, 0.55)
    else:
        base = _LIGHT_BASE
        text, muted = "#16141a", "#56525e"
        panel_rgb, panel2_rgb = (252, 251, 253), (238, 236, 242)
        glass_rgb = (255, 255, 255)
        scrim = rgba(255, 255, 255, 0.62)

    # Accent-tinted window background (tint/gradient backgrounds only).
    t = hex_to_rgb(accent) or hex_to_rgb(DEFAULT_ACCENT)
    bb = hex_to_rgb(base)
    tinted = rgb_to_hex(*(int(bb[i] + (t[i] - bb[i]) * 0.07) for i in range(3)))
    bg_top = base if s.background == "solid" else tinted
    bg_bottom = _lighten(base, 5) if s.background != "gradient" else _lighten(tinted, 6)

    # translucent glass materials (rgba — QSS + v3 widgets only)
    glass_panel = rgba(panel_rgb[0], panel_rgb[1], panel_rgb[2], alpha)
    glass_panel2 = rgba(panel2_rgb[0], panel2_rgb[1], panel2_rgb[2],
                        _clamp(alpha + 0.10, 0.1, 0.97))
    border_a = _clamp(0.10 + 0.14 * (s.transparency / 100.0), 0.08, 0.35)
    glass_border = rgba(glass_rgb[0], glass_rgb[1], glass_rgb[2], border_a)
    glass_fill = rgba(glass_rgb[0], glass_rgb[1], glass_rgb[2],
                      _clamp(alpha * 0.5, 0.04, 0.5))
    hover_fill = rgba(glass_rgb[0], glass_rgb[1], glass_rgb[2],
                      _clamp(alpha + 0.14, 0.10, 0.98))
    accent_rgb = hex_to_rgb(accent) or (233, 185, 87)
    glass_active = rgba(accent_rgb[0], accent_rgb[1], accent_rgb[2], 0.20)

    theme = {
        "name": f"liquid_{'dark' if is_dark else 'light'}",
        "is_dark": is_dark,
        # legacy keys — HEX ONLY (QColor-safe)
        "window_bg": bg_top,
        "window_bg_2": bg_bottom,
        "panel_bg": rgb_to_hex(*panel_rgb),
        "panel_bg_2": rgb_to_hex(*panel2_rgb),
        "border": _mix(base, "#ffffff" if is_dark else "#888888", 0.16),
        "text": text,
        "muted": muted,
        "gold": accent,
        "gold_light": _lighten(accent, 18),
        "gold_deep": _darken(accent, 22),
        "active": rgb_to_hex(*accent_rgb),
        # Liquid Glass extras (rgba — the actual material system)
        "glass_panel": glass_panel,
        "glass_panel_2": glass_panel2,
        "glass_fill": glass_fill,
        "glass_hover": hover_fill,
        "glass_border": glass_border,
        "glass_active": glass_active,
        "scrim": scrim,
        "accent": accent,
        "lg": {
            "transparency": s.transparency,
            "blur": s.blur,
            "corner": s.corner,
            "density": s.density,
            "anim_intensity": s.anim_intensity,
            "reduced_motion": s.reduced_motion,
            "background": s.background,
            "radius_card": s.radius("card"),
            "radius_thumb": s.radius("thumb"),
            "radius_panel": s.radius("panel"),
            "radius_chrome": s.radius("chrome"),
            "radius_pill": s.radius("pill"),
            "anim_ms_fast": s.anim_ms(160),
            "anim_ms_medium": s.anim_ms(280),
            "anim_ms_slow": s.anim_ms(420),
        },
    }
    return theme


def _mix(hex_a: str, hex_b: str, f: float) -> str:
    a, b = hex_to_rgb(hex_a), hex_to_rgb(hex_b)
    if not a or not b:
        return hex_a
    return rgb_to_hex(*(int(a[i] + (b[i] - a[i]) * _clamp(f, 0, 1))
                       for i in range(3)))


def glassify_for_qss(theme: dict) -> dict:
    """Shallow copy of a theme dict where the LEGACY surface keys are
    replaced by their translucent glass counterparts for QSS rendering.

    Only build_qss() should consume the result — QColor painters must keep
    the hex dict. This is the single place the material system becomes
    visible.
    """
    out = dict(theme)
    out["panel_bg"] = theme.get("glass_panel", theme["panel_bg"])
    out["panel_bg_2"] = theme.get("glass_panel_2", theme["panel_bg_2"])
    out["border"] = theme.get("glass_border", theme["border"])
    out["active"] = theme.get("glass_active", theme["active"])
    return out


def is_liquid_theme_name(name: str) -> bool:
    return isinstance(name, str) and name.startswith("liquid")


# ---------------------------------------------------------------------------
# Artwork-derived accent
# ---------------------------------------------------------------------------

def accent_from_pixmap(pixmap) -> str | None:
    """Dominant saturated colour of an artwork pixmap -> accent hex.

    Downscale -> skip near-black/near-white pixels -> HSL sort by
    (saturation, coverage) -> most colourful bucket wins; nothing is
    invented: returns None for empty/gray art so the caller keeps the
    previous accent.
    """
    try:
        from PyQt6.QtCore import Qt
        if pixmap is None or pixmap.isNull():
            return None
        pm = pixmap.scaled(32, 32, Qt.AspectRatioMode.IgnoreAspectRatio)
        img = pm.toImage()
        buckets: dict[tuple, list] = {}
        total = 0
        for y in range(img.height()):
            for x in range(img.width()):
                c = img.pixelColor(x, y)
                hue = c.hueF()
                sat = c.saturationF()
                lig = c.lightnessF()
                if lig < 0.08 or lig > 0.96 or sat < 0.18:
                    continue
                key = (int(hue * 24), int(sat * 4))
                buckets.setdefault(key, []).append(c)
                total += 1
        if not total:
            return None
        # score: saturation * sqrt(coverage)
        best_key = max(buckets, key=lambda k: (
            sum(c.saturationF() for c in buckets[k]) / len(buckets[k])
            * (len(buckets[k]) ** 0.5)))
        cs = buckets[best_key]
        r = sum(c.red() for c in cs) // len(cs)
        g = sum(c.green() for c in cs) // len(cs)
        b = sum(c.blue() for c in cs) // len(cs)
        return rgb_to_hex(r, g, b)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Native Windows backdrop (Mica / Acrylic / opaque fallback)
# ---------------------------------------------------------------------------

DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMSBT_NONE, DWMSBT_MAINWINDOW, DWMSBT_TRANSIENTWINDOW = 1, 2, 3


def apply_native_backdrop(widget, settings: GlassSettings, is_dark: bool) -> bool:
    """Enable a native DWM backdrop on Windows 11 22H2+.

    ``blur`` >= 60 -> Mica (DWMSBT_MAINWINDOW), otherwise Acrylic
    (transient). Falls back to immersive dark-mode flag only; returns True
    when the backdrop attribute was accepted (best-effort — older Windows
    silently keeps an opaque surface and we still render readable QSS).
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        hwnd = int(widget.winId())
        dwm = ctypes.windll.dwmapi
        backdrop = (DWMSBT_MAINWINDOW if settings.blur >= 60
                    else DWMSBT_TRANSIENTWINDOW)
        if settings.blur <= 0:
            backdrop = DWMSBT_NONE
        ok = dwm.DwmSetWindowAttribute(
            hwnd, DWMWA_SYSTEMBACKDROP_TYPE,
            ctypes.byref(ctypes.c_int(backdrop)), 4) == 0
        dwm.DwmSetWindowAttribute(
            hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(ctypes.c_int(1 if is_dark else 0)), 4)
        return ok
    except Exception:
        return False
