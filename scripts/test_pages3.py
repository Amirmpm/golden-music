"""Offline tests for gm/ui/pages3.py — library browsing pages (v3.0).

Covers: AlbumsPage / ArtistsPage / GenresPage / PlaylistsPage3,
TrackTableModel, LibraryTable, FlowLayout, progressive rendering,
search filtering, provider-contract guards (bare fake mw + real LibraryDB).

Run (LD_LIBRARY_PATH fix + offscreen Qt first):
    source /home/z/env.sh
    export QT_QPA_PLATFORM=offscreen
    python scripts/test_pages3.py

No gm.window / main / settings_dialog import — a bare fake mw drives the
pages through the documented provider contract.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtGui import QColor, QPixmap  # noqa: E402
from PyQt6.QtWidgets import QApplication, QPushButton, QWidget  # noqa: E402

from config import Theme  # noqa: E402
from gm.db import LibraryDB  # noqa: E402
from gm.ui.pages3 import (  # noqa: E402
    ALL_COLUMNS,
    AlbumsPage,
    ArtistsPage,
    FlowLayout,
    GenresPage,
    LibraryTable,
    PlaylistsPage3,
    TrackTableModel,
    format_duration_ms,
    star_text,
)

PASS = 0
FAIL = 0

APP = QApplication.instance() or QApplication(sys.argv)


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


def pump(app: QApplication = APP, rounds: int = 12) -> None:
    """Drain the event loop so chunked builders / debounces fire."""
    for _ in range(rounds):
        app.processEvents()


def pump_until(app: QApplication, cond, timeout_s: float = 3.0) -> bool:
    """Process events until cond() is truthy or the timeout elapses."""
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return cond()


# ---------------------------------------------------------------------------
# Fixture data: 25 tracks / 4 artists / 3 albums / 3 genres
# ---------------------------------------------------------------------------
ARTIST_SPEC = [
    # artist,        album,             n,   genre
    ("Aurora Nova", "Aurora Skies",    10, "Rock"),
    ("Bass Harbor", "Harbor Lights",    8, "Jazz"),
    ("Cedar Grove", "Grove Sessions",   5, "Electronic"),
    ("Delta Waves", "",                 2, "Rock"),
]


def build_db(tmp: Path) -> tuple[LibraryDB, list[str], dict]:
    """Create a real LibraryDB with 25 tagged tracks; returns (db, paths, tags)."""
    db = LibraryDB(tmp / "t.db")
    rows: list[dict] = []
    paths: list[str] = []
    k = 0
    for artist, album, n, genre in ARTIST_SPEC:
        folder = album if album else "Singles"
        for j in range(1, n + 1):
            k += 1
            path = str(tmp / "music" / folder / f"track_{k:02d}.mp3")
            paths.append(path)
            rows.append({
                "path": path,
                "title": f"Song {k:02d}",
                "artist": artist,
                "album": album,
                "genre": genre,
                "year": 2020 + (k % 5),
                "track_no": j,
                "duration_ms": 180_000 + k * 5_000,
                "bitrate_kbps": 192 + (k % 4) * 32,
                "rating": k % 6,
                "play_count": k % 10,
            })
    db.bulk_ingest(rows)
    tags = {p: (r["title"], r["artist"]) for p, r in zip(paths, rows)}
    return db, paths, tags


class FakeStore:
    """Duck-typed mw.store: .db or None."""

    def __init__(self, db):
        self.db = db


class FakeMW:
    """Fake main window implementing ONLY the pages3 provider contract."""

    def __init__(self, db=None, theme_name: str = "royal_gold",
                 prefs: dict | None = None, library=None, tag_cache=None):
        self.theme = Theme.get(theme_name)
        self.library = list(library) if library else []
        self._tag_cache = dict(tag_cache) if tag_cache else {}
        self.prefs = dict(prefs) if prefs else {}
        self.store = FakeStore(db) if db is not None else None
        self.playlists = {}
        self.played: list[tuple[list[str], int]] = []
        self.toasts: list[str] = []
        self.requests: list[tuple] = []
        self.cover_requests: list[str] = []

    # -- provider contract hooks ------------------------------------------
    def _play_from_list(self, tracks, index, view=None):
        self.played.append((list(tracks), int(index)))

    def _show_toast(self, msg):
        self.toasts.append(str(msg))

    def _load_cover_async(self, path, cb):
        self.cover_requests.append(str(path))
        # deterministic: even hash -> 64x64 pixmap, odd -> None
        pm = None
        if hash(str(path)) % 2 == 0:
            pm = QPixmap(64, 64)
            pm.fill(QColor("#d4a85a"))
        cb(str(path), pm)

    # -- playlists page hooks (guarded on the real window) ------------------
    def request_new_playlist(self):
        self.requests.append(("new_playlist",))

    def request_m3u_import(self):
        self.requests.append(("m3u_import",))

    def request_m3u_export(self):
        self.requests.append(("m3u_export",))

    def request_add_radio(self):
        self.requests.append(("add_radio",))

    def request_play_smart(self, key):
        self.requests.append(("play_smart", key))

    def request_play_radio(self, station_id):
        self.requests.append(("play_radio", station_id))

    def request_remove_radio(self, station_id):
        self.requests.append(("remove_radio", station_id))


class RadioMW(FakeMW):
    """FakeMW plus a radio_rows() provider."""

    def radio_rows(self):
        return [
            {"id": 3, "name": "Gold FM", "url": "http://stream.gold.fm:8000/x"},
            {"id": 7, "name": "Night Jazz",
             "url": "https://jazz.example.org/live"},
        ]


class BareMW:
    """A mw with NOTHING — every attr access must fall back safely."""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_construct_two_themes():
    print("=== construction & theme switching (2 themes) ===")
    for theme_name in ("royal_gold", "ivory"):
        mw = FakeMW(theme_name=theme_name)
        made = [
            AlbumsPage(mw), ArtistsPage(mw), GenresPage(mw),
            PlaylistsPage3(mw), LibraryTable(mw),
        ]
        ok = all(w is not None for w in made)
        check(f"construct all pages ({theme_name})", ok)
        for widget in made:
            widget.set_theme(Theme.get("ivory" if theme_name == "royal_gold"
                                       else "royal_gold"))
        check(f"set_theme both directions ({theme_name})", True)
    bare = BareMW()
    pages = [AlbumsPage(bare), ArtistsPage(bare), GenresPage(bare),
             PlaylistsPage3(bare)]
    for page in pages:
        page.set_theme(Theme.get("royal_gold"))
        page.refresh()
    pump(APP, 4)
    check("bare fake mw (no attrs at all) constructs + refreshes", True)


def test_refresh_populates(db, paths, tags):
    print("=== refresh() populates from the real db ===")
    mw = FakeMW(db=db, library=paths, tag_cache=tags)
    albums = AlbumsPage(mw)
    albums.refresh()
    pump_until(APP, lambda: albums._grid_item_count() >= 3)
    check("albums count == 3", len(albums.albums()) == 3,
          str(len(albums.albums())))
    check("album grid filled", albums._grid_item_count() == 3,
          str(albums._grid_item_count()))
    names = {a["album"] for a in albums.albums()}
    check("album names match", names == {"Aurora Skies", "Harbor Lights",
                                         "Grove Sessions"}, str(names))

    artists = ArtistsPage(mw)
    artists.refresh()
    pump_until(APP, lambda: artists._card_count() >= 4)
    check("artists count == 4", len(artists.artists()) == 4,
          str(len(artists.artists())))
    artist_names = {a for a, _ in artists.artists()}
    check("artist names match", artist_names ==
          {"Aurora Nova", "Bass Harbor", "Cedar Grove", "Delta Waves"},
          str(artist_names))

    genres = GenresPage(mw)
    genres.refresh()
    pump(APP, 2)
    check("genres count == 3", len(genres.genres()) == 3,
          str(len(genres.genres())))
    gd = dict(genres.genres())
    check("genre counts match", gd == {"Rock": 12, "Jazz": 8,
                                       "Electronic": 5}, str(gd))


def test_grid_list_toggle(db, paths, tags):
    print("=== grid/list view toggle ===")
    mw = FakeMW(db=db, library=paths, tag_cache=tags)
    page = AlbumsPage(mw)
    page.refresh()
    pump_until(APP, lambda: page._grid_item_count() == 3)
    check("default mode is grid", page.mode() == "grid", page.mode())

    page._header._btn_list.click()
    check("toggle switches mode to list", page.mode() == "list", page.mode())
    pump_until(APP, lambda: page._album_list.count() == 3)
    check("list mode rows == 3", page._album_list.count() == 3,
          str(page._album_list.count()))

    page._header._btn_grid.click()
    check("toggle back to grid", page.mode() == "grid", page.mode())
    pump_until(APP, lambda: page._grid_item_count() == 3)
    check("grid items == 3 again", page._grid_item_count() == 3,
          str(page._grid_item_count()))

    artists = ArtistsPage(mw)
    artists.refresh()
    pump_until(APP, lambda: artists._card_count() >= 4)
    artists._header._btn_list.click()
    check("artists toggle switches to list", artists.mode() == "list",
          artists.mode())
    pump_until(APP, lambda: artists._artist_list.count() == 4)
    check("artists list rows == 4", artists._artist_list.count() == 4,
          str(artists._artist_list.count()))


def test_album_detail(db, paths, tags):
    print("=== album detail: open + play + shuffle + back ===")
    mw = FakeMW(db=db, library=paths, tag_cache=tags)
    page = AlbumsPage(mw)
    page.refresh()
    pump_until(APP, lambda: page._grid_item_count() == 3)

    key = next(a["key"] for a in page.albums() if a["album"] == "Aurora Skies")
    page._open_album(key)
    pump_until(APP, lambda: page._detail_list.count() == 10)
    check("detail opens with 10 tracks", page._detail_list.count() == 10,
          str(page._detail_list.count()))
    check("stack shows detail",
          page._stack.currentWidget() is page._detail)

    page._play_detail_row(0)
    check("track double-click handler plays album list",
          len(mw.played) == 1 and len(mw.played[0][0]) == 10
          and mw.played[0][1] == 0, str(mw.played[:1]))
    played_paths = set(mw.played[0][0])
    expected = {r["path"] for r in db.tracks_where(
        "album=? AND artist=?", ("Aurora Skies", "Aurora Nova"))}
    check("played tracks are the album's tracks", played_paths == expected,
          f"{len(played_paths)} vs {len(expected)}")

    page._play_album()
    check("Play All plays from index 0",
          mw.played[-1][1] == 0 and len(mw.played[-1][0]) == 10)
    page._shuffle_album()
    check("Shuffle still plays all 10 tracks",
          len(mw.played[-1][0]) == 10 and mw.played[-1][1] == 0)

    page._back_from_detail()
    check("back returns to grid",
          page._stack.currentWidget() is page._grid)

    pump(APP, 6)
    check("album covers requested (visible/capped)",
          len(mw.cover_requests) >= 1, str(len(mw.cover_requests)))


def test_artist_detail(db, paths, tags):
    print("=== artist detail: open + play ===")
    mw = FakeMW(db=db, library=paths, tag_cache=tags)
    page = ArtistsPage(mw)
    page.refresh()
    pump_until(APP, lambda: page._card_count() == 4)

    page._open_artist("Aurora Nova")
    pump_until(APP, lambda: page._detail_list.count() == 10)
    check("artist detail opens with 10 tracks",
          page._detail_list.count() == 10, str(page._detail_list.count()))
    page._play_artist_row(2)
    expected = {r["path"] for r in db.tracks_where("artist=?",
                                                   ("Aurora Nova",))}
    check("artist row play passes artist tracks",
          len(mw.played) == 1 and mw.played[0][1] == 2
          and set(mw.played[0][0]) == expected,
          f"{mw.played[:1]}")
    page._play_artist()
    check("artist Play All", len(mw.played[-1][0]) == 10
          and mw.played[-1][1] == 0)
    page._back_from_detail()
    check("artist back works", page._stack.currentIndex() == 0)


def test_genre_chips_and_detail(db, paths, tags):
    print("=== genre chips + genre detail ===")
    mw = FakeMW(db=db, library=paths, tag_cache=tags)
    page = GenresPage(mw)
    page.refresh()
    pump(APP, 2)
    labels = page._genre_labels()
    check("chips match genres", set(labels) ==
          {"Rock · 12", "Jazz · 8", "Electronic · 5"}, str(labels))
    chips = [page._flow.itemAt(i).widget()
             for i in range(page._flow.count())]
    check("chips are GhostBtn QPushButtons",
          all(isinstance(c, QPushButton) for c in chips))
    check("chips carry TAG icon", all(not c.icon().isNull() for c in chips))

    page._open_genre("Rock")
    pump_until(APP, lambda: page._detail_list.count() == 12)
    check("genre detail lists 12 Rock tracks",
          page._detail_list.count() == 12, str(page._detail_list.count()))
    page._play_genre_row(0)
    check("genre row play", len(mw.played) == 1 and len(mw.played[0][0]) == 12
          and mw.played[0][1] == 0, str(mw.played[:1]))
    page._shuffle_genre()
    check("genre shuffle play", len(mw.played[-1][0]) == 12)
    page._back_from_detail()
    check("genre back returns to chips", page._stack.currentIndex() == 0)


def test_track_table_model(db, paths, tags):
    print("=== TrackTableModel: columns, data, headerData ===")
    aurora = [r for r in db.tracks_where("album=?", ("Aurora Skies",))
              if r["artist"] == "Aurora Nova"]
    model = TrackTableModel(
        paths=[r["path"] for r in aurora],
        tag_cache=tags,
        meta_for=lambda p: db.get_track(p) or {})
    check("rowCount == 10", model.rowCount() == 10, str(model.rowCount()))
    check("default columnCount == 6", model.columnCount() == 6,
          str(model.columnCount()))
    check("ALL_COLUMNS has 10 defs", len(ALL_COLUMNS) == 10,
          str(len(ALL_COLUMNS)))

    by_col = {r["path"]: r for r in aurora}
    model.set_columns([c for c, _ in ALL_COLUMNS])
    check("columnCount with all columns", model.columnCount() == 10,
          str(model.columnCount()))
    for col, cid in enumerate(c for c, _ in ALL_COLUMNS):
        idx = model.index(0, col)
        val = model.data(idx, Qt.ItemDataRole.DisplayRole)
        row = by_col[model.paths()[0]]
        if cid == "index":
            want = "1"
        elif cid == "title":
            want = row["title"]
        elif cid == "artist":
            want = row["artist"]
        elif cid == "album":
            want = row["album"]
        elif cid == "genre":
            want = row["genre"]
        elif cid == "duration":
            want = format_duration_ms(row["duration_ms"])
        elif cid == "rating":
            want = star_text(row["rating"])
        elif cid == "bitrate":
            want = f"{row['bitrate_kbps']} kbps"
        elif cid == "play_count":
            want = str(row["play_count"])
        else:
            want = row["path"]
        check(f"data[{cid}] == {want!r}", val == want, repr(val))

    hdr = {model.headerData(c, Qt.Orientation.Horizontal):
               cid for c, (cid, _) in enumerate(ALL_COLUMNS)}
    check("headerData labels unique + correct",
          len(hdr) == 10 and hdr.get("Title") == "title"
          and hdr.get("#") == "index" and hdr.get("File") == "path", str(hdr))

    check("duration helper 275000 -> 4:35",
          format_duration_ms(275_000) == "4:35",
          format_duration_ms(275_000))
    check("duration helper None -> ''", format_duration_ms(None) == "")
    check("rating helper 3 -> ★★★☆☆", star_text(3) == "★★★☆☆",
          star_text(3))

    # sorting via the view API path
    model.set_columns(["index", "title"])
    title_col = [model.column_id(c) for c in range(
        model.columnCount())].index("title")
    model.sort(title_col, Qt.SortOrder.AscendingOrder)
    first_title = tags[model.paths()[0]][0]
    last_title = tags[model.paths()[-1]][0]
    titles = [tags[p][0] for p in model.paths()]
    check("sort by title re-orders paths",
          titles == sorted(titles, key=str.casefold)
          and first_title == "Song 01" and last_title == "Song 10",
          f"{first_title}..{last_title}")
    model.sort(title_col, Qt.SortOrder.DescendingOrder)
    titles_desc = [tags[p][0] for p in model.paths()]
    check("descending sort reverses",
          titles_desc[0] == "Song 10", titles_desc[0])


def test_library_table(db, paths, tags):
    print("=== LibraryTable: columns, widths, sort, double-click ===")
    mw = FakeMW(db=db, library=paths, tag_cache=tags,
                prefs={"density": "compact"})
    table = LibraryTable(mw)
    aurora_paths = [r["path"] for r in db.tracks_where(
        "album=? AND artist=?", ("Aurora Skies", "Aurora Nova"))]
    table.set_paths(aurora_paths)
    check("table row count", table.model().rowCount() == 10,
          str(table.model().rowCount()))
    check("compact density -> 28px rows",
          table.verticalHeader().defaultSectionSize() == 28,
          str(table.verticalHeader().defaultSectionSize()))

    table.set_columns(["index", "title", "artist", "duration"])
    check("set_columns -> 4 columns", table.model().columnCount() == 4,
          str(table.model().columnCount()))
    check("header count matches", table.horizontalHeader().count() == 4,
          str(table.horizontalHeader().count()))
    check("current_paths round-trip",
          table.current_paths() == aurora_paths)

    title_col = 1
    table.sortByColumn(title_col, Qt.SortOrder.AscendingOrder)
    first = tags[table.current_paths()[0]][0]
    check("view sortByColumn re-orders model",
          first == "Song 01", first)

    table._play_row(0)
    check("double-click handler -> _play_from_list(paths, row)",
          len(mw.played) == 1 and mw.played[0][1] == 0
          and set(mw.played[0][0]) == set(aurora_paths), str(mw.played[:1]))


def test_progressive_rendering(tmp):
    print("=== progressive rendering: 500 in-memory albums, 60/tick ===")
    lib_paths = []
    tag_cache = {}
    for i in range(500):
        p = str(tmp / "big" / f"Album {i:03d}" / f"track {i:03d}.mp3")
        lib_paths.append(p)
        tag_cache[p] = (f"Title {i:03d}", f"Artist {i % 7}")
    mw = FakeMW(db=None, library=lib_paths, tag_cache=tag_cache)
    page = AlbumsPage(mw)
    page.refresh()

    t0 = time.perf_counter()
    APP.processEvents()
    counts = [page._grid_item_count()]
    max_iter_s = time.perf_counter() - t0
    for _ in range(300):
        t0 = time.perf_counter()
        APP.processEvents()
        max_iter_s = max(max_iter_s, time.perf_counter() - t0)
        counts.append(page._grid_item_count())
        if counts[-1] >= 500:
            break
    check("first event-loop pass builds exactly one 60-item chunk",
          counts[0] == 60, str(counts[:3]))
    check("build completed to 500 items", counts[-1] == 500, str(counts[-3:]))
    check("multiple chunks used (progressive, not one blocking loop)",
          len(counts) >= 8, str(len(counts)))
    check("no single chunk blocked > 300ms",
          max_iter_s < 0.300, f"{max_iter_s:.3f}s")
    check("cover requests capped at 200",
          len(mw.cover_requests) <= 200, str(len(mw.cover_requests)))


def test_playlists_page(db, paths, tags):
    print("=== PlaylistsPage3: user + smart rows + guarded hooks ===")
    prefs = {
        "smart_most_played": True,
        "smart_top_rated": True,
        "smart_never_played": False,      # explicitly off
        # recently_added / not_heard_recently absent -> off
    }
    mw = FakeMW(db=db, library=paths, tag_cache=tags, prefs=prefs)
    mw.playlists = {
        "Road Trip": paths[:3],
        "Chill Mix": paths[3:4],
    }
    page = PlaylistsPage3(mw)
    page.refresh()
    pump(APP, 2)

    smart_keys = {r["key"] for r in page._smart_rows}
    check("2 enabled smart rows", len(page._smart_rows) == 2,
          str(page._smart_rows))
    check("enabled keys are most_played + top_rated",
          smart_keys == {"most_played", "top_rated"}, str(smart_keys))
    labels = {r["label"] for r in page._smart_rows}
    check("smart labels correct", labels == {"Most Played", "Top Rated"},
          str(labels))
    by_key = {r["key"]: r for r in page._smart_rows}
    want_most = len(db.tracks_where("play_count>0"))
    want_top = len(db.tracks_where("rating>=4"))
    check("most_played count from db", by_key["most_played"]["count"] ==
          want_most, f"{by_key['most_played']['count']} vs {want_most}")
    check("top_rated count from db",
          by_key["top_rated"]["count"] == want_top,
          f"{by_key['top_rated']['count']} vs {want_top}")

    user_names = {r["name"] for r in page._user_rows}
    check("user playlists from mw.playlists dict",
          user_names == {"Road Trip", "Chill Mix"}, str(user_names))
    road_idx = next(i for i, r in enumerate(page._user_rows)
                    if r["name"] == "Road Trip")
    page._play_user_row(road_idx)
    check("user row play passes its paths",
          len(mw.played) == 1 and mw.played[0][0] == paths[:3]
          and mw.played[0][1] == 0, str(mw.played[:1]))

    page._activate_smart("most_played")
    check("smart row click -> request_play_smart(key)",
          ("play_smart", "most_played") in mw.requests, str(mw.requests))

    page._new_btn.click()
    check("New Playlist -> request_new_playlist",
          ("new_playlist",) in mw.requests)
    page._import_btn.click()
    page._export_btn.click()
    check("import/export hooks fire",
          ("m3u_import",) in mw.requests and ("m3u_export",) in mw.requests,
          str(mw.requests))
    check("no radio rows without provider", page._radio_rows == [])

    # disabled builtin must not appear even via _activate (guard only)
    check("disabled builtins excluded",
          all(k not in ("never_played", "recently_added",
                        "not_heard_recently") for k in smart_keys))


def test_radio_rows(db, paths, tags):
    print("=== PlaylistsPage3: radio rows (guarded provider) ===")
    mw = RadioMW(db=db, library=paths, tag_cache=tags)
    page = PlaylistsPage3(mw)
    page.refresh()
    pump(APP, 2)
    check("radio rows loaded", len(page._radio_rows) == 2,
          str(page._radio_rows))
    names = {r["name"] for r in page._radio_rows}
    check("radio names", names == {"Gold FM", "Night Jazz"}, str(names))
    page._play_radio_row(0)
    check("radio play -> request_play_radio(id)",
          ("play_radio", 3) in mw.requests, str(mw.requests))
    page._remove_radio_row(1)
    check("radio trash -> request_remove_radio(id)",
          ("remove_radio", 7) in mw.requests, str(mw.requests))
    page._add_radio_btn.click()
    check("add station -> request_add_radio",
          ("add_radio",) in mw.requests)


def test_search_filter(db, paths, tags):
    print("=== album search filtering (live + debounce) ===")
    mw = FakeMW(db=db, library=paths, tag_cache=tags)
    page = AlbumsPage(mw)
    page.refresh()
    pump_until(APP, lambda: page._grid_item_count() == 3)

    page._apply_search("Aurora")
    pump_until(APP, lambda: page._grid_item_count() == 1)
    check("search narrows to 1 album", page._grid_item_count() == 1,
          str(page._grid_item_count()))
    check("filtered album is Aurora Skies",
          len(page._filtered) == 1 and page._filtered[0]["album"] ==
          "Aurora Skies", str(page._filtered))

    page._apply_search("bass")     # artist match, case-insensitive
    pump_until(APP, lambda: page._album_list.count() >= 0)
    check("artist search matches album group",
          len(page._filtered) == 1 and
          page._filtered[0]["artist"] == "Bass Harbor", str(page._filtered))

    page._apply_search("zzz-no-match")
    check("no match -> empty state visible",
          page._grid_item_count() == 0 and
          page._stack.currentWidget() is page._empty,
          page._stack.currentWidget().objectName()
          if page._stack.currentWidget() else "?")

    page._apply_search("")
    pump_until(APP, lambda: page._grid_item_count() == 3)
    check("clearing search restores 3", page._grid_item_count() == 3)

    # live QLineEdit debounce (150ms)
    page._header.search.setText("Harbor")
    fired = pump_until(APP,
                       lambda: not page._header._debounce.isActive()
                       and page._grid_item_count() == 1, timeout_s=2.0)
    check("150ms debounce narrows via the QLineEdit too", fired,
          str(page._grid_item_count()))


def test_flow_layout():
    print("=== FlowLayout wraps ===")
    host = QWidget()
    flow = FlowLayout(host, margin=0, spacing=8)
    for i in range(6):
        btn = QPushButton(f"Chip {i}")
        btn.adjustSize()
        flow.addWidget(btn)
    check("flow counts 6 items", flow.count() == 6, str(flow.count()))
    wide = flow.heightForWidth(1200)
    narrow = flow.heightForWidth(40)
    check("narrow width wraps taller than wide",
          wide > 0 and narrow > wide, f"{wide} vs {narrow}")
    flow.clear()
    check("clear empties the flow", flow.count() == 0, str(flow.count()))


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="gm_pages3_") as td:
        tmp = Path(td)
        db, paths, tags = build_db(tmp)
        test_construct_two_themes()
        test_refresh_populates(db, paths, tags)
        test_grid_list_toggle(db, paths, tags)
        test_album_detail(db, paths, tags)
        test_artist_detail(db, paths, tags)
        test_genre_chips_and_detail(db, paths, tags)
        test_track_table_model(db, paths, tags)
        test_library_table(db, paths, tags)
        test_progressive_rendering(tmp)
        test_playlists_page(db, paths, tags)
        test_radio_rows(db, paths, tags)
        test_search_filter(db, paths, tags)
        test_flow_layout()
        db.close()

    print()
    print(f"SUMMARY: {PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
