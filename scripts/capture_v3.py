"""Capture v3.0 screenshots — fully offscreen, project-local only.

- Real MainWindow via QT_QPA_PLATFORM=offscreen; portable config keeps
  every read/write inside the project folder (the user's real
  ~/.goldenmusic is never touched).
- Waits for the real SQLite store (the migration path runs on the portable
  JSON), then seeds ratings/play-events/radio so the new pages look alive.
- Captures: all 12 themes on the Library page (README-ready, same
  filenames as before) + v3 feature pages (albums / artists / genres /
  stats / settings / command palette / queue / lyrics).
"""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from version import APP_VERSION  # noqa: E402

os.environ["QT_QPA_PLATFORM"] = "offscreen"

PORTABLE_CFG = ROOT / "goldenmusic_config.json"   # gitignored, repo-local

DEMO_TRACKS = [
    ("Midnight Whispers", "Luna Ray"),
    ("Golden Hour", "The Amber Collective"),
    ("Velvet Sky", "Nova&Co"),
    ("Paper Hearts", "Elias Moon"),
    ("Ocean Drive", "Cassette Dreams"),
    ("Starlight Parade", "Aurora Fields"),
    ("Silver Lining", "The Writers"),
    ("Echoes of You", "Marlowe"),
    ("Amber Dust", "Luna Ray"),
    ("Northern Lights", "Aurora Fields"),
    ("Neon Rain", "Nova&Co"),
    ("Paper Boats", "Cassette Dreams"),
]
DEMO_LIBRARY = [f"C:/DemoMusic/{t[0].replace(' ', '_').lower()}.mp3"
                for t in DEMO_TRACKS]
seed = {
    "version": APP_VERSION,
    "theme": "royal_gold",
    "library": DEMO_LIBRARY,
    "favorites": DEMO_LIBRARY[:3],
    "added_folders": ["C:/DemoMusic"],
    "volume": 70,
    "auto_rescan": False,
    "remember_track": False,
    "playlists": {"Roadtrip": DEMO_LIBRARY[3:7]},
    "prefs": {
        "smart_most_played": True,
        "smart_recently_added": True,
        "visualizer_enabled": False,   # no decoder for fake files
        "waveform_seekbar": True,
    },
}
PORTABLE_CFG.write_text(json.dumps(seed, indent=2), encoding="utf-8")

from PyQt6.QtWidgets import QApplication  # noqa: E402
from PyQt6.QtCore import Qt, QTimer, QEventLoop  # noqa: E402
from PyQt6.QtGui import (QPixmap, QPainter, QColor, QLinearGradient,  # noqa: E402
                         QBrush)

app = QApplication(sys.argv)

from main import MainWindow  # noqa: E402
from config import Theme  # noqa: E402
from icons import render_icon  # noqa: E402

SHOTS = ROOT / "screenshots"
SHOTS.mkdir(exist_ok=True)
OUT = Path("/home/z/my-project/download/goldenmusic_screenshots")
OUT.mkdir(parents=True, exist_ok=True)

w = MainWindow()
w.resize(1360, 850)
w.show()

# Fake the audio surface so no real playback is attempted offscreen
w.audio.load = lambda p: None
w.audio.load_and_play = lambda p: None
w.audio.position = lambda: 78000
w.audio.duration = lambda: 222000
w.audio.state = lambda: 1

_state = {"phase": "wait_store", "shots": 0}


def grab(name, also_download=True):
    pm = w.grab()
    ok = pm.save(str(SHOTS / f"{name}.png"))
    if also_download and ok:
        pm.save(str(OUT / f"{name}.png"))
    _state["shots"] += 1
    print(f"shot: {name} ({'ok' if ok else 'FAILED'})", flush=True)


def cover_art(c1, c2, c3, icon="music-note"):
    cover = QPixmap(512, 512)
    cover.fill(Qt.GlobalColor.transparent)
    p = QPainter(cover)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    grad = QLinearGradient(0, 0, 512, 512)
    grad.setColorAt(0.0, QColor(*c1))
    grad.setColorAt(0.5, QColor(*c2))
    grad.setColorAt(1.0, QColor(*c3))
    p.setBrush(QBrush(grad))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(0, 0, 512, 512, 48, 48)
    note = render_icon(icon, 200, "rgba(26,20,16,110)", 1.0)
    p.drawPixmap(156, 156, note)
    p.end()
    return cover


def seed_ui():
    w.library = list(DEMO_LIBRARY)
    w.current_playlist = list(DEMO_LIBRARY)
    w.current_index = 0
    w.current_track = DEMO_LIBRARY[0]
    w._tag_cache.update({
        path: (title, artist)
        for path, (title, artist) in zip(DEMO_LIBRARY, DEMO_TRACKS)
    })
    t0, a0 = DEMO_TRACKS[0]
    w.now_title.setMarqueeText(t0)
    w.now_artist.setMarqueeText(a0)
    w.player_bar.title_label.setMarqueeText(t0)
    w.player_bar.artist_label.setMarqueeText(a0)
    w.player_bar.time_total.setText("3:42")
    w.player_bar.time_current.setText("1:18")
    w.player_bar.seek_slider.blockSignals(True)
    w.player_bar.seek_slider.setValue(355)
    w.player_bar.seek_slider.blockSignals(False)
    cover = cover_art((0x8b, 0x6a, 0x2a), (0xd4, 0xa8, 0x5a),
                      (0xf0, 0xd8, 0x96))
    w.cover_label.set_cover(cover)
    w.fav_cover.set_cover(cover)
    w.player_bar.set_cover_thumb(cover)
    w._last_cover_pm = cover
    # waveform preview on the seekbar
    try:
        import math
        peaks = [abs(math.sin(i / 19.0)) * (0.35 + 0.65 * abs(
            math.sin(i / 61.0))) for i in range(320)]
        w.player_bar.seek_slider.set_peaks(peaks)
    except Exception:
        pass
    w._toast_enabled = False
    w.scan_status_label.hide()
    w.scan_progress.hide()
    w._rebuild_library_list()
    w._highlight_playing_in_lists()
    w._update_count_label()
    w._on_rail_clicked("library")
    w.rail.set_library_counts(len(DEMO_LIBRARY), 3)


def seed_store():
    """Rich demo data directly in the SQLite store (portable dir)."""
    st = w.store
    db = st.db
    # tags + ratings + play counts
    rows = []
    for i, path in enumerate(DEMO_LIBRARY):
        title, artist = DEMO_TRACKS[i]
        rows.append({"path": path, "title": title, "artist": artist,
                     "album": ["Nocturne", "Solar Drift", "Nocturne",
                               "Paperwork", "Coastline", "Nocturne",
                               "Daybreak", "Echo Chamber", "Solar Drift",
                               "Aurora", "Neon Nights", "Coastline"][i % 12],
                     "genre": ["Dream Pop", "Indie", "Electronic", "Jazz",
                               "Synthwave", "Indie"][i % 6],
                     "duration_ms": 190000 + i * 8000,
                     "bitrate_kbps": 256, "year": 2020 + (i % 5),
                     "rating": [5, 4, 3, 0, 4, 5, 0, 3, 4, 5, 2, 0][i],
                     "play_count": [42, 31, 12, 0, 27, 38, 3, 9, 21, 33, 5, 1][i]})
    db.upsert_tracks(rows)
    # play stats are owned by record_play — seed them directly like the
    # app would have recorded them
    with db._tx() as conn:
        for r in rows:
            conn.execute("UPDATE tracks SET play_count=?, rating=?, "
                         "last_played_at=? WHERE path=?",
                         (r["play_count"], r["rating"],
                          now - r["play_count"] * 3600, r["path"]))
    # play events across the last 45 days for charts/heatmap
    import random
    rng = random.Random(7)
    now = time.time()
    ev = []
    for i, path in enumerate(DEMO_LIBRARY):
        for k in range((rows[i]["play_count"] or 0)):
            ev.append((path, now - rng.uniform(0, 45 * 86400),
                       rng.uniform(90, 220)))
    with db._tx() as conn:
        conn.executemany(
            "INSERT INTO play_events(path,played_at,seconds) VALUES(?,?,?)",
            ev)
    db.meta_set("total_seconds", str(int(sum(e[2] for e in ev))))
    db.meta_set("sessions", "48")
    db.meta_set("stats_legacy", json.dumps(
        {"sessions": 48, "first_played": "2023-02-14",
         "artist_seconds": {"Luna Ray": 9100.0, "Aurora Fields": 6400.0}}))
    db.add_radio("SomaFM Groove Salad",
                 "https://ice1.somafm.com/groovesalad-128-mp3",
                 "https://somafm.com", "Ambient")
    db.add_radio("Radio Javan", "https://api.rjtv.tv/live/stream",
                 "https://radiojavan.com", "Persian Pop")
    db.add_radio("FIP", "https://icecast.radiofrance.fr/fip-midfi.mp3",
                 "https://fip.fr", "Eclectic")
    db.rebuild_fts()
    # re-seed UI now that the store is rich
    seed_ui()
    _state["phase"] = "capture_pages"
    QTimer.singleShot(400, next_step)


def wait_store(deadline=20000):
    if getattr(w, "store", None) is not None:
        try:
            seed_store()
        except Exception as e:
            print("store seed failed:", e)
            seed_ui()
            _state["phase"] = "capture_pages"
            QTimer.singleShot(300, next_step)
        return
    if deadline <= 0:
        print("store timeout — capturing without store data")
        seed_ui()
        _state["phase"] = "capture_pages"
        QTimer.singleShot(300, next_step)
        return
    QTimer.singleShot(100, lambda: wait_store(deadline - 100))


THEMES = Theme.names()
PAGES = [
    ("library", "library"),
    ("albums", "albums"),
    ("artists", "artists"),
    ("genres", "genres"),
    ("playlists", "playlists"),
    ("radio", "radio"),
    ("stats", "stats"),
]
_theme_state = {"idx": 0}


def next_step():
    ph = _state["phase"]
    if ph == "capture_pages":
        for key, _title in PAGES:
            try:
                w._on_rail_clicked(key)
                # pages animate content in (stagger fades ~350-900 ms) —
                # wait on a REAL event loop so the timers fire before grab
                loop = QEventLoop()
                QTimer.singleShot(1000 if key == "stats" else 600, loop.quit)
                loop.exec()
                grab(f"v3_{key}")
            except Exception as e:
                print(f"page {key} failed: {e}")
        # command palette
        try:
            w._show_command_palette()
            if getattr(w, "_palette", None) is not None:
                w._palette._input.setText("") if hasattr(
                    w._palette, "_input") else None
                app.processEvents()
                pm = w._palette.grab()
                pm.save(str(SHOTS / "v3_command_palette.png"))
                pm.save(str(OUT / "v3_command_palette.png"))
                w._palette.hide()
        except Exception as e:
            print("palette failed:", e)
        # settings dialog
        try:
            from gm.ui.settings3 import SettingsDialog3
            dlg = SettingsDialog3(w, w)
            dlg.resize(940, 640)
            dlg.show()
            app.processEvents()
            pm = dlg.grab()
            pm.save(str(SHOTS / "v3_settings.png"))
            pm.save(str(OUT / "v3_settings.png"))
            dlg.close()
        except Exception as e:
            print("settings grab failed:", e)
        # queue panel
        try:
            w._toggle_queue_panel()
            app.processEvents()
            grab("v3_queue", also_download=True)
            w._toggle_queue_panel()
        except Exception as e:
            print("queue grab failed:", e)
        _state["phase"] = "themes"
        QTimer.singleShot(300, next_step)
        return
    if ph == "themes":
        i = _theme_state["idx"]
        if i >= len(THEMES):
            print(f"DONE — {_state['shots'] + 7} shots in {SHOTS}")
            w._force_quit = True
            w.close()
            app.quit()
            return
        name = THEMES[i]
        _theme_state["idx"] = i + 1
        w._on_theme_selected(name)
        w._on_rail_clicked("library")
        app.processEvents()
        QTimer.singleShot(350, lambda: (grab(name), next_step()))
        return


QTimer.singleShot(1200, lambda: wait_store(20000))
sys.exit(app.exec())
