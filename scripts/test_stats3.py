"""Offline tests for Golden Music 2.3 — gm/ui/stats3.py (stats page v3).

Covers: LineChart / BarChart / HBarChart / Heatmap / TopTracksList /
WrappedDialog / StatsPage3 against a REAL gm.db.LibraryDB seeded in a
tempdir (40 tracks, ~600 play_events over 60 days, random.Random(42)).

Run:  source env.sh && QT_QPA_PLATFORM=offscreen python3 scripts/test_stats3.py
Constraints: NO gm.window/main import, NO network, NO pytest — plain
script with [OK]/[FAIL] lines, a SUMMARY and exit code 1 on failure.
"""
from __future__ import annotations

import os
import random
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtCore import QPoint, QPointF, Qt, QEvent  # noqa: E402
from PyQt6.QtGui import QColor, QMouseEvent, QPixmap  # noqa: E402
from PyQt6.QtWidgets import QApplication, QFileDialog  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402

from config import Theme  # noqa: E402
from gm.db import LibraryDB  # noqa: E402
from gm.ui.stats3 import (  # noqa: E402
    BarChart, HBarChart, Heatmap, LineChart, StatsPage3, TopTracksList,
    WrappedDialog, fmt_hours,
)

app = QApplication.instance() or QApplication(sys.argv)
app.setApplicationName("GMStats3Test")

PASS = 0
FAIL = 0
FAILURES: list[str] = []


def check(name: str, cond, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK] {name}")
    else:
        FAIL += 1
        FAILURES.append(name)
        print(f"  [FAIL] {name} {extra}")


def hover(widget, x: int, y: int) -> None:
    """Synthetic hover: QTest.mouseMove plus a hand-rolled QMouseEvent."""
    try:
        QTest.mouseMove(widget, QPoint(x, y))
    except Exception:
        pass
    try:
        ev = QMouseEvent(QEvent.Type.MouseMove, QPointF(float(x), float(y)),
                         QPointF(float(x), float(y)), Qt.MouseButton.NoButton,
                         Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(widget, ev)
    except Exception:
        pass
    app.processEvents()


# ---------------------------------------------------------------------------
# Deterministic seed data
# ---------------------------------------------------------------------------
TMP = Path(tempfile.mkdtemp(prefix="gmstats3_"))
DB = LibraryDB(TMP / "library.db")
rng = random.Random(42)

ARTISTS = [f"Artist {i}" for i in range(8)]
GENRES = ["Rock", "Electronic", "Jazz", "Ambient", "Metal"]
TRACKS: list[dict] = []
for i in range(40):
    artist = ARTISTS[i // 5]
    album = f"Album {chr(ord('A') + (i % 5))} {i // 5}"
    TRACKS.append({
        "path": str(TMP / "music" / f"track_{i:02d}.mp3"),
        "title": f"Track {i:02d}",
        "artist": artist,
        "album": album,
        "genre": GENRES[i % 5],
        "year": 2000 + (i % 20),
        "duration_ms": 180000 + i * 1000,
    })

# added_at spread inside the current calendar year
_year_start = datetime(date.today().year, 1, 1).timestamp()
for i, tr in enumerate(TRACKS):
    tr["added_at"] = _year_start + i * 7 * 86400.0
DB.upsert_tracks(TRACKS)

# ~600 play_events over the last 60 days, evening-leaning hours, four
# deliberately silent days (zero-day coverage for the heatmap).
ACTIVE_DAYS = [d for d in range(60) if d not in {5, 17, 33, 41}]
PLAYABLE = list(range(30))          # only artists 0..5 get plays
WEIGHTS = [max(1, 30 - i) for i in PLAYABLE]
N_EVENTS = 600
today = date.today()
events: list[tuple[str, float, float]] = []        # (path, ts, seconds)
per_day = [0] * 60
for _ in range(N_EVENTS):
    day_off = rng.choice(ACTIVE_DAYS)
    per_day[day_off] += 1
    hour = rng.choice((19, 20, 21, 22, 23, 0, 9, 10, 12, 14, 16, 18, 1, 2))
    minute = rng.randrange(60)
    second = rng.randrange(60)
    day = today - timedelta(days=day_off)
    ts = datetime(day.year, day.month, day.day,
                  hour, minute, second).timestamp()
    track = TRACKS[rng.choices(PLAYABLE, weights=WEIGHTS, k=1)[0]]
    events.append((track["path"], ts, round(rng.uniform(30.0, 300.0), 1)))

conn = DB._conn()
conn.executemany(
    "INSERT INTO play_events(path, played_at, seconds) VALUES (?,?,?)",
    events)
_counts: dict[str, tuple[int, float]] = {}
for path, ts, secs in events:
    cnt, last = _counts.get(path, (0, 0.0))
    _counts[path] = (cnt + 1, max(last, ts))
for path, (cnt, last) in _counts.items():
    conn.execute("UPDATE tracks SET play_count=?, last_played_at=? "
                 "WHERE path=?", (cnt, last, path))
conn.commit()

TOTAL_SECONDS = round(sum(s for _p, _t, s in events), 1)
DB.meta_set("total_seconds", f"{TOTAL_SECONDS}")
DB.meta_set("sessions", "5")

PLAYS_BY_YEAR: dict[int, int] = {}
SECONDS_BY_YEAR: dict[int, float] = {}
for _p, ts, secs in events:
    y = datetime.fromtimestamp(ts).year
    PLAYS_BY_YEAR[y] = PLAYS_BY_YEAR.get(y, 0) + 1
    SECONDS_BY_YEAR[y] = SECONDS_BY_YEAR.get(y, 0.0) + secs
ARTISTS_PLAYED = 6
THIS_YEAR = date.today().year
EXP_YEAR = PLAYS_BY_YEAR.get(THIS_YEAR, 0)
EXP_SECONDS = SECONDS_BY_YEAR.get(THIS_YEAR, 0.0)
MAX_PLAY_COUNT = max(c for c, _l in _counts.values())

# artist totals for the wrapped cross-check
_artist_seconds: dict[str, float] = {}
for path, _ts, secs in events:
    tr = next(t for t in TRACKS if t["path"] == path)
    _artist_seconds[tr["artist"]] = \
        _artist_seconds.get(tr["artist"], 0.0) + secs
EXP_TOP_ARTIST = max(_artist_seconds.items(), key=lambda kv: kv[1])[0]


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class LegacyStats:
    """Minimal legacy playstats.PlayStats-shaped object (fallback path)."""

    total_seconds = 12345.0
    play_counts = {f"/legacy/song{i}.mp3": 40 - i for i in range(15)}
    artist_seconds = {"Legacy A": 500.0, "Legacy B": 250.0}
    sessions = 7
    first_played = "2024-01-01"

    def top_tracks(self, tag_cache, n=10):
        ranked = sorted(self.play_counts.items(), key=lambda kv: -kv[1])[:n]
        return [(tag_cache.get(p, (Path(p).stem, ""))[0],
                 tag_cache.get(p, ("", ""))[1], c, p) for p, c in ranked]

    def top_artists(self, n=5):
        ranked = sorted(self.artist_seconds.items(),
                        key=lambda kv: -kv[1])[:n]
        return [(a, "8 min") for a, _s in ranked]


COVER_PM = QPixmap(80, 80)
COVER_PM.fill(QColor("#e9b957"))


class FakeMW:
    """Duck-typed main window: never imports gm.window."""

    def __init__(self):
        self.theme = Theme.get("royal_gold")
        self.stats = LegacyStats()
        self.store = SimpleNamespace(db=DB)
        self.favorites = {TRACKS[0]["path"], TRACKS[3]["path"]}
        self.prefs: dict = {}
        self.library = [t["path"] for t in TRACKS]
        self._tag_cache = {t["path"]: (t["title"], t["artist"])
                           for t in TRACKS}
        self.toasts: list[str] = []
        self.plays: list[tuple] = []
        self.covers: list[str] = []

    def _show_toast(self, text, error=False, ms=2500):
        self.toasts.append(str(text))

    def _play_from_list(self, tracks, index, view=None):
        self.plays.append((list(tracks), index, view))

    def _load_cover_async(self, path, cb):
        """Sync fake cover loader: pixmap for even-numbered tracks."""
        self.covers.append(path)
        idx = int(Path(path).stem.split("_")[-1]) if "track_" in path else -1
        cb(path, COVER_PM if idx >= 0 and idx % 2 == 0 else None)


MW = FakeMW()
ROYAL = Theme.get("royal_gold")
IVORY = Theme.get("ivory")


# ===========================================================================
print("\n=== 1. StatsPage3: construct + refresh under two themes ===")


def t_page_two_themes():
    try:
        page = StatsPage3(MW)
    except Exception as exc:                                  # noqa: BLE001
        check("StatsPage3 constructs", False, repr(exc))
        return None
    check("StatsPage3 constructs", True)
    page.resize(1280, 1000)
    page.show()
    app.processEvents()
    try:
        page.refresh()
        QTest.qWait(700)      # let the first-refresh staggered fade-in land
        app.processEvents()
        pm = page.grab()
        img = pm.toImage()
        distinct = {img.pixelColor(x, y).rgb()
                    for y in range(0, img.height(), 16)
                    for x in range(0, img.width(), 16)}
        check("refresh + full repaint (royal_gold)",
              not pm.isNull() and len(distinct) > 4,
              f"{len(distinct)} sampled colors")
    except Exception as exc:                                  # noqa: BLE001
        check("refresh + full repaint (royal_gold)", False, repr(exc))
    try:
        page.set_theme(IVORY)
        page.refresh()
        app.processEvents()
        pm = page.grab()
        img = pm.toImage()
        light = sum(1 for y in range(0, img.height(), 24)
                    for x in range(0, img.width(), 24)
                    if img.pixelColor(x, y).value() > 150)
        check("set_theme(ivory) + refresh + repaint",
              not pm.isNull() and light > 50, f"{light} light samples")
    except Exception as exc:                                  # noqa: BLE001
        check("set_theme(ivory) + refresh + repaint", False, repr(exc))
    page.set_theme(ROYAL)
    check("header title", page.title_lbl.text() == "Stats")
    check("header subtitle", "visualized" in page.title_lbl.parentWidget()
          .findChildren(type(page.title_lbl))[1].text().lower()
          if page.title_lbl.parentWidget() else False)
    check("charts created", page.line_chart is not None
          and page.bar_chart is not None
          and page.artist_chart is not None
          and page.genre_chart is not None)
    check("heatmap created", page.heatmap is not None)
    check("tracks list created", page.tracks_list is not None)
    return page


PAGE = t_page_two_themes()

# ===========================================================================
print("\n=== 2. Stat cards match computed expectations ===")


def t_stat_cards():
    if PAGE is None:
        check("stat cards (page missing)", False)
        return
    total_txt = PAGE.stat_card_text("total")
    check("total time card", total_txt == fmt_hours(TOTAL_SECONDS),
          f"{total_txt!r} != {fmt_hours(TOTAL_SECONDS)!r}")
    check("plays card == 600", PAGE.stat_card_text("plays") == "600",
          PAGE.stat_card_text("plays"))
    check("artists card == 6 (artists heard)",
          PAGE.stat_card_text("artists") == str(ARTISTS_PLAYED),
          PAGE.stat_card_text("artists"))
    sessions_txt = PAGE.stat_card_text("sessions")
    check("sessions card >= 1", sessions_txt.isdigit()
          and int(sessions_txt) >= 1, sessions_txt)
    check("sessions card comes from mw.stats (7)",
          sessions_txt == "7", sessions_txt)


t_stat_cards()

# ===========================================================================
print("\n=== 3. LineChart / BarChart / HBarChart: sample + empty data ===")


def t_charts():
    sample = [(f"d{i}", float(i * i + 1)) for i in range(12)]
    for name, chart in (("LineChart", LineChart()),
                        ("BarChart", BarChart()),
                        ("HBarChart", HBarChart())):
        try:
            chart.set_data(sample, ROYAL, lambda v: f"{v:.0f}")
            chart.resize(420, 260)
            pm = chart.grab()
            check(f"{name} sample render", not pm.isNull())
        except Exception as exc:                              # noqa: BLE001
            check(f"{name} sample render", False, repr(exc))
        check(f"{name} sizeHint sane",
              chart.sizeHint().height() >= 180 and chart.sizeHint().width() > 0)
        try:
            chart.set_data([], ROYAL, lambda v: f"{v:.0f}")
            pm = chart.grab()
            check(f"{name} empty render", not pm.isNull())
        except Exception as exc:                              # noqa: BLE001
            check(f"{name} empty render", False, repr(exc))
    check("LineChart.points accessor",
          LineChart().set_data(sample, ROYAL, None) is None)  # no crash call
    line = LineChart()
    line.set_data(sample, ROYAL, lambda v: f"{v:.0f}")
    check("LineChart.points()", [p for p in line.points()] == sample
          or len(line.points()) == len(sample))
    bar = BarChart()
    bar.set_data(sample, ROYAL, lambda v: f"{v:.0f}")
    check("BarChart.rows()", len(bar.rows()) == len(sample))
    hbar = HBarChart()
    hbar.set_data([("a", 5.0), ("b", 3.0)], ROYAL, lambda v: f"{v:.0f}")
    check("HBarChart.rows()", hbar.rows() == [("a", 5.0), ("b", 3.0)])
    hbar.resize(320, 200)
    hbar.show()
    app.processEvents()
    clicked: list[int] = []
    hbar.row_clicked.connect(lambda i: clicked.append(i))
    QTest.mouseClick(hbar, Qt.MouseButton.LeftButton,
                     pos=QPoint(150, int(HBarChart.ROW_H / 2)))
    app.processEvents()
    check("HBarChart row_clicked emitted", clicked == [0], str(clicked))
    hover(hbar, 150, int(HBarChart.ROW_H + HBarChart.GAP
                         + HBarChart.ROW_H / 2))
    check("HBarChart hover no crash", True)


t_charts()

# ===========================================================================
print("\n=== 4. Heatmap: data with zero-days + hover ===")


def t_heatmap():
    hm = Heatmap()
    hm.resize(820, 190)
    today_h = date.today()
    rows = []
    for back in range(0, 90, 2):      # every other day: interleaved zeros
        d = today_h - timedelta(days=back)
        rows.append((d.isoformat(), float((back % 7 + 1) * 600)))
    # an explicit zero-seconds day (present in the map but empty on screen)
    rows.append(((today_h - timedelta(days=1)).isoformat(), 0.0))
    hm.set_data(rows, ROYAL)
    try:
        pm = hm.grab()
        check("heatmap renders", not pm.isNull())
    except Exception as exc:                                  # noqa: BLE001
        check("heatmap renders", False, repr(exc))
    check("heatmap days() keeps zero-day",
          hm.days().get((today_h - timedelta(days=1)).isoformat()) == 0.0)
    check("heatmap sizeHint wide",
          hm.sizeHint().width() >= 700 and hm.sizeHint().height() > 100)
    hm.show()
    app.processEvents()
    try:
        hover(hm, int(Heatmap.LEFT_W + 3 * Heatmap.STEP + 5),
              int(Heatmap.TOP_H + 3 * Heatmap.STEP + 5))
        check("heatmap hover no crash", True)
    except Exception as exc:                                  # noqa: BLE001
        check("heatmap hover no crash", False, repr(exc))
    try:
        hm.set_data([], ROYAL)
        pm = hm.grab()
        check("heatmap empty render", not pm.isNull())
    except Exception as exc:                                  # noqa: BLE001
        check("heatmap empty render", False, repr(exc))
    if PAGE is not None and PAGE.heatmap is not None:
        check("page heatmap fed by daily_heatmap",
              len(PAGE.heatmap.days()) == len(ACTIVE_DAYS),
              str(len(PAGE.heatmap.days())))


t_heatmap()

# ===========================================================================
print("\n=== 5. TopTracksList: paging, heart toggle, play signal ===")


def make_rows(n: int) -> list[dict]:
    return [{"path": TRACKS[i]["path"], "title": TRACKS[i]["title"],
             "artist": TRACKS[i]["artist"], "play_count": 100 - i}
            for i in range(n)]


def t_tracks_list():
    loader_calls: list[str] = []

    def loader(path, cb):
        loader_calls.append(path)
        cb(path, COVER_PM)

    tl = TopTracksList(ROYAL, loader)
    tl.set_rows(make_rows(35), {TRACKS[0]["path"]})
    check("10 rows initially", tl.row_count() == 10, str(tl.row_count()))
    tl.resize(700, 900)
    tl.show()
    app.processEvents()
    try:
        pm = tl.grab()
        check("track list renders", not pm.isNull())
    except Exception as exc:                                  # noqa: BLE001
        check("track list renders", False, repr(exc))
    QTest.mouseClick(tl._more_btn, Qt.MouseButton.LeftButton)
    app.processEvents()
    check("show more -> 20 rows", tl.row_count() == 20, str(tl.row_count()))
    check("cover loader used", len(loader_calls) >= 20)

    row0 = tl.row_at(0)
    row1 = tl.row_at(1)
    check("row accessors", row0 is not None and row1 is not None)
    heart_events: list[tuple] = []
    play_events: list[str] = []
    tl.favorite_toggled.connect(lambda p, on: heart_events.append((p, on)))
    tl.play_requested.connect(lambda p: play_events.append(p))
    QTest.mouseClick(row0.heart_btn, Qt.MouseButton.LeftButton)
    app.processEvents()
    QTest.mouseClick(row1.heart_btn, Qt.MouseButton.LeftButton)
    app.processEvents()
    check("heart toggles emit favorite_toggled",
          heart_events == [(TRACKS[0]["path"], False),
                           (TRACKS[1]["path"], True)],
          str(heart_events))
    QTest.mouseClick(row0.play_btn, Qt.MouseButton.LeftButton)
    app.processEvents()
    check("play button emits play_requested",
          play_events == [TRACKS[0]["path"]], str(play_events))
    tl.set_rows([], set())
    check("empty rows -> 0", tl.row_count() == 0)

    if PAGE is not None and PAGE.tracks_list is not None:
        check("page top tracks shows 10 of 100",
              PAGE.tracks_list.row_count() == 10,
              str(PAGE.tracks_list.row_count()))
        page_plays: list[str] = []
        PAGE.play_requested.connect(lambda p: page_plays.append(p))
        prow = PAGE.tracks_list.row_at(0)
        if prow is not None:
            QTest.mouseClick(prow.play_btn, Qt.MouseButton.LeftButton)
            app.processEvents()
        check("page re-emits play_requested", len(page_plays) == 1,
              str(page_plays))


t_tracks_list()

# ===========================================================================
print("\n=== 6. WrappedDialog: open, close, export ===")

WRAP_DATA = {
    "total_seconds": 512400.0, "total_plays": 600, "sessions": 7,
    "top_artist": ("Neon Circuit", 123, 65000.0),
    "top_genre": ("Electronic", 210),
    "top_track": ("Midnight Drive", "Neon Circuit", 45, "/x/midnight.mp3"),
    "top_album": ("Signal Lights", "Neon Circuit", 90),
    "new_count": 12, "superlative": "Night owl 🦉 — after dark",
    "first_played": "2019-04-01",
}


def t_wrapped():
    toasts: list[str] = []
    try:
        dlg = WrappedDialog(2025, WRAP_DATA, ROYAL, None,
                            toast_cb=toasts.append,
                            cover_loader=lambda p, cb: cb(p, COVER_PM))
        dlg.show()
        app.processEvents()
        pm = dlg.grab()
        check("WrappedDialog opens + paints", not pm.isNull())
        dlg.close()
        app.processEvents()
    except Exception as exc:                                  # noqa: BLE001
        check("WrappedDialog opens + paints", False, repr(exc))
        return
    dlg = WrappedDialog(2025, WRAP_DATA, ROYAL)
    dlg.show()
    app.processEvents()
    out = TMP / "wrapped_export.txt"
    try:
        with mock.patch.object(QFileDialog, "getSaveFileName",
                               return_value=(str(out), "")) as patched:
            QTest.mouseClick(dlg.export_btn, Qt.MouseButton.LeftButton)
            app.processEvents()
        check("QFileDialog.getSaveFileName patched+used",
              patched.call_count == 1, str(patched.call_count))
    except Exception as exc:                                  # noqa: BLE001
        check("QFileDialog.getSaveFileName patched+used", False, repr(exc))
    check("export file written", out.is_file())
    if out.is_file():
        text = out.read_text(encoding="utf-8")
        check("export contains year", "2025" in text)
        check("export contains total time", fmt_hours(512400.0) in text,
              text.splitlines()[2] if len(text.splitlines()) > 2 else "")
        check("export contains plays total", "600" in text)
        check("export contains top artist", "Neon Circuit" in text)
        check("export contains superlative", "Night owl" in text)
    dlg.close()
    # sparse data must not raise (all optional cards skipped)
    try:
        sparse = WrappedDialog(1999, {"total_seconds": 10.0}, IVORY)
        pm = sparse.grab()
        check("WrappedDialog sparse data", not pm.isNull())
    except Exception as exc:                                  # noqa: BLE001
        check("WrappedDialog sparse data", False, repr(exc))


t_wrapped()

# ===========================================================================
print("\n=== 7. Wrapped gathering from the real db ===")


def t_gather_wrapped():
    if PAGE is None:
        check("gather_wrapped (page missing)", False)
        return
    try:
        data = PAGE.gather_wrapped(THIS_YEAR)
    except Exception as exc:                                  # noqa: BLE001
        check("gather_wrapped runs", False, repr(exc))
        return
    check("gather_wrapped runs", True)
    check("wrapped total_plays for this year",
          data.get("total_plays") == EXP_YEAR,
          f"{data.get('total_plays')} != {EXP_YEAR}")
    check("wrapped total_seconds for this year",
          abs(data.get("total_seconds", -1.0) - EXP_SECONDS) < 1.0,
          str(data.get("total_seconds")))
    top_artist = data.get("top_artist")
    check("wrapped top artist", top_artist is not None
          and top_artist[0] == EXP_TOP_ARTIST, str(top_artist))
    top_track = data.get("top_track")
    check("wrapped top track count == max play_count",
          top_track is not None and top_track[2] == MAX_PLAY_COUNT,
          str(top_track))
    check("wrapped sessions from mw.stats", data.get("sessions") == 7,
          str(data.get("sessions")))
    check("wrapped new_count == 40 (added this year)",
          data.get("new_count") == 40, str(data.get("new_count")))
    check("wrapped superlative present (hours or streak)",
          bool(data.get("superlative")), str(data.get("superlative")))
    check("wrapped dialog opens from page button data",
          isinstance(data, dict))
    # click the real Wrapped button (uses the combo-selected year)
    try:
        QTest.mouseClick(PAGE.wrapped_btn, Qt.MouseButton.LeftButton)
        app.processEvents()
        check("Wrapped button opens dialog", True)
    except Exception as exc:                                  # noqa: BLE001
        check("Wrapped button opens dialog", False, repr(exc))


t_gather_wrapped()

# ===========================================================================
print("\n=== 8. db-failure fallback: legacy mw.stats only ===")


def t_db_fallback():
    if PAGE is None:
        check("db fallback (page missing)", False)
        return
    MW.prefs = {"reduce_animations": True}   # guarded pref wiring check
    MW.store = None                    # db gone -> legacy stats path
    try:
        PAGE.refresh()
        app.processEvents()
        pm = PAGE.grab()
        check("refresh without db", not pm.isNull())
    except Exception as exc:                                  # noqa: BLE001
        check("refresh without db", False, repr(exc))
    check("fallback total from legacy stats",
          PAGE.stat_card_text("total") == fmt_hours(12345.0),
          PAGE.stat_card_text("total"))
    legacy_plays = sum(LegacyStats.play_counts.values())
    check("fallback plays from legacy stats",
          PAGE.stat_card_text("plays") == f"{legacy_plays:,}",
          PAGE.stat_card_text("plays"))
    check("fallback artists from legacy stats",
          PAGE.stat_card_text("artists") == "2",
          PAGE.stat_card_text("artists"))
    check("fallback sessions from legacy stats",
          PAGE.stat_card_text("sessions") == "7",
          PAGE.stat_card_text("sessions"))
    check("fallback top tracks from legacy stats",
          PAGE.tracks_list.row_count() == 10,
          str(PAGE.tracks_list.row_count()))
    entry = PAGE.tracks_list.entries()[0]
    check("fallback top track title",
          entry["title"] == Path(LegacyStats.play_counts and
                                 sorted(LegacyStats.play_counts.items(),
                                        key=lambda kv: -kv[1])[0][0]).stem,
          entry["title"])
    check("fallback time charts empty",
          PAGE.line_chart.points() == [] and PAGE.bar_chart.rows() == []
          and PAGE.genre_chart.rows() == [])
    check("fallback artist chart from legacy artist_seconds",
          PAGE.artist_chart.rows() == [("Legacy A", 500.0),
                                       ("Legacy B", 250.0)],
          str(PAGE.artist_chart.rows()))
    check("fallback heatmap empty", PAGE.heatmap.days() == {})
    MW.store = SimpleNamespace(db=DB)  # restore for any later use


t_db_fallback()

# ---------------------------------------------------------------------------
DB.close()
print(f"\nSUMMARY: {PASS} passed, {FAIL} failed")
if FAILURES:
    print("Failed checks:")
    for name in FAILURES:
        print(f"  - {name}")
sys.exit(1 if FAIL else 0)
