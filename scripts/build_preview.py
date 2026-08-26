"""Build a self-contained HTML preview of all downloaded icon sets.

Inlines every SVG with the app's gold color so the user can compare sets
side by side in a browser. Output: C:/Users/Meg/Desktop/New folder (2)/preview.html
"""
import html
from pathlib import Path

OUT_DIR = Path(r"C:\Users\Meg\Desktop\New folder (2)")
GOLD = "#d4a85a"
BG_DARK = "#1a1410"

SETS = [
    ("phosphor", "Phosphor Duotone", "⭐ پیشنهادی",
     "دو رنگ، لایه‌دار و شیک — عمق داره و خیلی متمایزه"),
    ("lucide", "Lucide", "کلاسیک",
     "خطی، تمیز و یکدست — شبیه چیزی که الان توی اپه ولی رسمی‌تر"),
    ("solar", "Solar Bold", "توپر",
     "ضخیم، هندسی و پرقدرت — برای دکمه‌های پخش عالیه"),
    ("mynaui", "MynaUI Solid", "مینیمال",
     "گرد، نرم و ساده — حس دوستانه و امروزی"),
]

ICONS = [
    "play", "pause", "stop", "next", "prev", "shuffle",
    "repeat", "repeat-one", "heart", "heart-filled",
    "volume-high", "volume-low", "volume-mute",
    "library", "favorites-nav", "folder-add", "folder",
    "sun", "moon", "settings", "music-note", "search",
    "sort-asc", "sort-desc", "queue", "equalizer", "timer",
    "minimize", "chevron-left", "chevron-right", "menu",
    "list-lines", "lyrics", "more", "trash", "close", "refresh",
]


def load_svg(set_dir: str, icon: str) -> str:
    p = OUT_DIR / set_dir / f"{icon}.svg"
    if not p.exists():
        return ""
    svg = p.read_text(encoding="utf-8")
    # For preview: force gold fill/stroke. Solar/MynaUI already have baked gold;
    # Lucide/Phosphor use currentColor.
    if set_dir in ("phosphor", "lucide"):
        svg = svg.replace("currentColor", GOLD)
        svg = svg.replace("<svg ", f'<svg style="color:{GOLD}" ')
    return svg


def main():
    cells_html = []
    for set_dir, name, badge, desc in SETS:
        cells = []
        for icon in ICONS:
            svg = load_svg(set_dir, icon)
            body = svg if svg else '<span style="color:#e06c6c;font-size:18px">✕</span>'
            label = html.escape(icon)
            cells.append(
                f'<div class="cell">{body}<span>{label}</span></div>'
            )
        cells_html.append(f"""
  <div class="set">
    <div class="set-title">{html.escape(name)} <span class="badge">{html.escape(badge)}</span></div>
    <div class="desc">{html.escape(desc)}</div>
    <div class="grid">{''.join(cells)}</div>
  </div>""")

    page = f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8">
<title>GoldenMusic — انتخاب ست آیکون</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: "Segoe UI", Tahoma, sans-serif;
    background: {BG_DARK};
    color: #f0e6d2;
    padding: 24px;
  }}
  h1 {{ font-size: 20px; color: {GOLD}; margin-bottom: 4px; }}
  .sub {{ color: #9a8a6f; font-size: 13px; margin-bottom: 28px; }}
  .set {{ background: #241d17; border: 1px solid #3a2f24; border-radius: 14px;
          padding: 18px 20px; margin-bottom: 26px; }}
  .set-title {{ font-size: 16px; font-weight: 700; color: #e8c47a;
                display: flex; align-items: center; gap: 10px; }}
  .badge {{ font-size: 11px; font-weight: 400; color: {BG_DARK};
            background: {GOLD}; padding: 2px 10px; border-radius: 99px; }}
  .desc {{ color: #9a8a6f; font-size: 12.5px; margin: 6px 0 16px; }}
  .grid {{ display: flex; flex-wrap: wrap; gap: 8px; }}
  .cell {{
    width: 74px; height: 74px; border-radius: 12px;
    background: #2d251c; border: 1px solid #3a2f24;
    display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 5px;
    transition: all .15s ease;
  }}
  .cell:hover {{ background: #3a2f24; transform: translateY(-2px); }}
  .cell svg {{ width: 26px; height: 26px; }}
  .cell span {{ font-size: 9.5px; color: #9a8a6f; direction: ltr; }}
</style>
</head>
<body>
<h1>🎵 GoldenMusic — انتخاب ست آیکون مدرن</h1>
<p class="sub">۴ ست آیکون از اینترنت دانلود شد — همه با رنگ طلایی خودِ برنامه روی پس‌زمینه واقعی اپ رندر شدن. هر ست رو ببین و بگو کدوم.</p>
{''.join(cells_html)}
</body>
</html>"""
    out = OUT_DIR / "preview.html"
    out.write_text(page, encoding="utf-8")
    print(f"Wrote {out} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
