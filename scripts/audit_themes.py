"""Audit all theme palettes: WCAG contrast + psychological harmony check."""
import sys
sys.path.insert(0, '.')
from config import Theme


def _rgb(hex_color):
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def _luminance(hex_color):
    r, g, b = (v / 255.0 for v in _rgb(hex_color))
    def lin(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(a, b):
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def audit(t):
    issues = []
    # Critical pairs: text on backgrounds
    pairs = [
        ("text/window_bg", t["text"], t["window_bg"], 7.0),
        ("text/panel_bg", t["text"], t["panel_bg"], 6.0),
        ("muted/panel_bg", t["muted"], t["panel_bg"], 3.5),
        ("gold/gold_light", t["gold"], t["panel_bg_2"], 1.8),
        ("gold_light/window_bg", t["gold_light"], t["window_bg"], 5.0),
        ("accent-vs-window", t["gold"], t["window_bg"], 3.0),
    ]
    for label, fg, bg, minimum in pairs:
        c = contrast(fg, bg)
        if c < minimum:
            issues.append(f"LOW CONTRAST {label}: {c:.2f} < {minimum} "
                          f"({fg} on {bg})")
    # Accent must not be garish neon or muddy gray.
    # v2.0 exemptions: Obsidian is intentionally monochrome; Neon Rose is
    # deliberately a glowing hot-pink "pop" theme by design.
    r, g, b = _rgb(t["gold"])
    sat = (max(r, g, b) - min(r, g, b)) / max(1, max(r, g, b))
    NEON_BY_DESIGN = ("neon_rose",)
    if max(r, g, b) > 240 and sat > 0.55 and t["name"] not in NEON_BY_DESIGN:
        issues.append(f"NEON accent {t['gold']} — too electric")
    if sat < 0.08 and t["name"] not in ("obsidian",):
        issues.append(f"WASHED accent {t['gold']} — nearly gray in a color theme")
    return issues


print(f"{'THEME':<14} {'issues'}")
print('-' * 70)
total_issues = 0
for t in Theme.ALL:
    issues = audit(t)
    total_issues += len(issues)
    status = 'OK' if not issues else f'{len(issues)} issue(s)'
    print(f"{t['name']:<14} {status}")
    for i in issues:
        print(f"   - {i}")
print('-' * 70)
print('TOTAL ISSUES:', total_issues)
