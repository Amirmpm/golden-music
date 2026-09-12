"""Capture ALL 12 themes as PNGs — fully offscreen, project-local only.

- Runs the real MainWindow with QT_QPA_PLATFORM=offscreen (no GPU/window capture
  involved, so shots can never come out black).
- Uses a *portable* config (goldenmusic_config.json in the repo root) so all
  config reads/writes stay inside the project folder — the user's real
  ~/.goldenmusic data is never touched.
- Injects fake track metadata + a generated cover into the UI state so the
  screenshots show a realistic "now playing" scene (track info, list rows).
- Saves one PNG per theme into screenshots/ (README-ready).
"""
import json
import os
import sys
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# --- Portable config: keep every read/write inside the project folder --------
PORTABLE_CFG = ROOT / "goldenmusic_config.json"   # already in .gitignore
DEMO_TRACKS = [
    ("Midnight Whispers", "Luna Ray"),
    ("Golden Hour", "The Amber Collective"),
    ("Velvet Sky", "Nova&Co"),
    ("Paper Hearts", "Elias Moon"),
    ("Ocean Drive", "Cassette Dreams"),
    ("Starlight Parade", "Aurora Fields"),
    ("Silver Lining", "The Writers"),
    ("Echoes of You", "Marlowe"),
]
DEMO_LIBRARY = [f"C:/DemoMusic/{t.replace(' ', '_').lower()}.mp3"
                for t, _ in DEMO_TRACKS]
seed = {
    "version": "2.1.1",
    "theme": "royal_gold",
    "library": DEMO_LIBRARY,
    "favorites": DEMO_LIBRARY[:3],
    "added_folders": ["C:/DemoMusic"],
    "volume": 70,
    "auto_rescan": False,          # demo files don't exist — never rescan
    "remember_track": False,
    "playlists": {},
}
PORTABLE_CFG.write_text(json.dumps(seed, indent=2), encoding="utf-8")

from PyQt6.QtWidgets import QApplication  # noqa: E402
from PyQt6.QtCore import Qt, QTimer  # noqa: E402
from PyQt6.QtGui import QPixmap, QPainter, QColor, QLinearGradient, QBrush  # noqa: E402

app = QApplication(sys.argv)

from main import MainWindow  # noqa: E402
from config import Theme  # noqa: E402
from icons import render_icon  # noqa: E402

SHOTS = ROOT / "screenshots"
SHOTS.mkdir(exist_ok=True)

w = MainWindow()
w.resize(1280, 800)
w.show()

# Fake the audio surface so no real playback is attempted offscreen
w.audio.load = lambda p: None
w.audio.load_and_play = lambda p: None

# --- Inject fake track metadata so lists and the player bar look real --------
w.current_playlist = list(DEMO_LIBRARY)
w.current_index = 0
w.current_track = DEMO_LIBRARY[0]
w._tag_cache.update({
    path: (title, artist)
    for path, (title, artist) in zip(DEMO_LIBRARY, DEMO_TRACKS)
})

title0, artist0 = DEMO_TRACKS[0]
w.now_title.setText(title0)
w.now_artist.setText(artist0)
w.player_bar.title_label.setText(title0)
w.player_bar.artist_label.setText(artist0)
w.player_bar.time_total.setText("3:42")
w.player_bar.time_current.setText("1:18")
w.player_bar.seek_slider.blockSignals(True)
w.player_bar.seek_slider.setValue(355)   # ~35% in
w.player_bar.seek_slider.blockSignals(False)

# Generated cover art: warm gradient + music note (same style as CoverLabel)
cover = QPixmap(512, 512)
cover.fill(Qt.GlobalColor.transparent)
p = QPainter(cover)
p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
grad = QLinearGradient(0, 0, 512, 512)
grad.setColorAt(0.0, QColor(0x8b, 0x6a, 0x2a))
grad.setColorAt(0.5, QColor(0xd4, 0xa8, 0x5a))
grad.setColorAt(1.0, QColor(0xf0, 0xd8, 0x96))
p.setBrush(QBrush(grad))
p.setPen(Qt.PenStyle.NoPen)
p.drawRoundedRect(0, 0, 512, 512, 48, 48)
note = render_icon("music-note", 200, "rgba(26,20,16,110)", 1.0)
p.drawPixmap(156, 156, note)
p.end()
w.cover_label.set_cover(cover)
w.fav_cover.set_cover(cover)
w.player_bar.set_cover_thumb(cover)

# Rebuild the list from the seeded tag cache so rows show title | artist
w._rebuild_library_list()
w._highlight_playing_in_lists()
w.search_edit.setPlaceholderText("Search tracks...")

THEMES = Theme.names()  # all 12, in config order
_state = {"idx": 0, "first": THEMES[0]}


def capture_next():
    i = _state["idx"]
    if i >= len(THEMES):
        w._on_theme_selected(_state["first"])
        QTimer.singleShot(300, finish)
        return
    name = THEMES[i]
    w._on_theme_selected(name)
    w.current_view = __import__("main").View.LIBRARY
    w.stack.setCurrentIndex(0)
    w.page_title.setText("Library")

    def snap():
        w.repaint()
        app.processEvents()
        pix = w.grab()          # grab from Qt itself — never black
        out = SHOTS / f"{name}.png"
        pix.save(str(out), "PNG")
        print(f"saved {out.name} ({pix.width()}x{pix.height()})")
        _state["idx"] = i + 1
        QTimer.singleShot(250, capture_next)

    QTimer.singleShot(700, snap)


def finish():
    w.close()
    app.quit()
    print("done — all themes captured")


QTimer.singleShot(1500, capture_next)   # let async config load finish
app.exec()
