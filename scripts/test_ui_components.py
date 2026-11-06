"""Tests for the v3.0 UI components: sidebar, animations, command palette,
empty states / skeletons / inline spinner.

Boots ONLY the components (never main.py / gm.window) under the offscreen
platform, using royal_gold + ivory themes and fake parent widgets.
"""
import os
import sys
import traceback
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
# --- Test isolation: sandbox HOME + portable config -------------------------
# MUST run before any project import (config_path() reads HOME at call time).
import testenv as _testenv
_testenv.install()
del _testenv
# ------------------------------------------------------------------------------
from PyQt6.QtCore import QEventLoop, Qt, QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication, QFrame, QGraphicsOpacityEffect, QLabel, QMenu,
    QPushButton, QWidget,
)

from config import Theme
from icons import Icon
from gm.ui import animations as anim_mod
from gm.ui.animations import (
    ValueAnimator, fade_in, fade_out, pop_in, set_reduce_animations,
    slide_in, stagger_fade,
)
from gm.ui.sidebar import Sidebar
from gm.ui.command_palette import CommandPalette
from gm.ui.empty_states import EmptyState, InlineSpinner, SkeletonRows

app = QApplication.instance() or QApplication(sys.argv)
app.setApplicationName("GM-Test-UIComponents")

ROYAL = Theme.get("royal_gold")
IVORY = Theme.get("ivory")

passed = 0
failed = 0
errors = []


def check(name, fn):
    global passed, failed
    try:
        fn()
        passed += 1
        print(f"  [OK] {name}")
    except Exception as e:
        failed += 1
        errors.append((name, traceback.format_exc()))
        print(f"  [FAIL] {name}: {e}")


def wait(ms):
    """Pump the event loop for *ms* milliseconds (timers/animations run)."""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


# ============================================================================
print("\n=== Sidebar ===")
# ============================================================================
host = QWidget()
host.resize(1000, 800)

nav_log = []
sb = Sidebar(ROYAL, parent=host)
sb.nav_requested.connect(nav_log.append)

SIDEBAR_BUTTON_KEYS = {
    "library", "favorites", "albums", "artists", "genres", "playlists",
    "radio", "stats", "addfolder", "refresh", "theme", "settings", "mini",
    "command", "more",
}
NAV_KEYS = ("library", "favorites", "albums", "artists", "genres",
            "playlists", "radio", "stats")
ACTION_KEYS = ("addfolder", "refresh", "theme", "settings", "mini",
               "command", "more")


def sidebar_keys():
    out = {}
    for b in sb.findChildren(QPushButton):
        k = b.property("key")
        if isinstance(k, str) and k:
            out[k] = b
    return out


def sidebar_construct():
    assert sb.objectName() == "Sidebar", "objectName"
    assert sb.width() == 232, f"width {sb.width()}"
    keys = sidebar_keys()
    assert set(keys) == SIDEBAR_BUTTON_KEYS, f"keys {sorted(set(keys))}"
    for k in NAV_KEYS:
        assert keys[k].isCheckable(), f"{k} should be checkable"
    for k in ACTION_KEYS:
        assert not keys[k].isCheckable(), f"{k} should NOT be checkable"
check("sidebar_construct_rows", sidebar_construct)


def sidebar_emissions():
    nav_log.clear()
    keys = sidebar_keys()
    for k in NAV_KEYS + ACTION_KEYS[:-1]:  # every button except "more"
        keys[k].click()
    expected = list(NAV_KEYS + ACTION_KEYS[:-1])
    assert nav_log == expected, f"button emissions {nav_log}"
    # overflow menu -> more_* keys
    nav_log.clear()
    menus = sb.findChildren(QMenu)
    assert len(menus) == 1, f"expected 1 overflow menu, got {len(menus)}"
    mkeys = {}
    for act in menus[0].actions():
        k = act.property("key")
        if isinstance(k, str):
            mkeys[k] = act
    assert set(mkeys) == {"more_duplicates", "more_backup", "more_export"}
    for k in ("more_duplicates", "more_backup", "more_export"):
        mkeys[k].trigger()
    assert nav_log == ["more_duplicates", "more_backup", "more_export"], nav_log
check("sidebar_nav_requested_all_keys", sidebar_emissions)


def sidebar_active_exclusive():
    keys = sidebar_keys()
    sb.set_active("albums")
    assert keys["albums"].isChecked(), "albums should be checked"
    for k in ("library", "favorites", "genres", "stats"):
        assert not keys[k].isChecked(), f"{k} leaked checked state"
    sb.set_active("stats")
    assert keys["stats"].isChecked() and not keys["albums"].isChecked()
    sb.set_active("bogus_key")
    assert not any(keys[k].isChecked() for k in NAV_KEYS), "unknown key clears"
    sb.set_active("library")
    assert keys["library"].isChecked()
check("sidebar_set_active_exclusive", sidebar_active_exclusive)


def sidebar_collapsed():
    sb.set_collapsed(True)
    assert sb.width() == 68, f"collapsed width {sb.width()}"
    for b in sidebar_keys().values():
        assert b.text() == "", "labels hidden in collapsed mode"
        assert b.toolTip(), "tooltips required in collapsed mode"
    sb.set_collapsed(False)
    assert sb.width() == 232
    keys = sidebar_keys()
    assert keys["library"].text() == "Library"
    assert keys["command"].text() == "Command Palette"
    assert not keys["library"].toolTip(), "tooltips cleared when expanded"
check("sidebar_set_collapsed", sidebar_collapsed)


def sidebar_badges():
    sb.set_library_counts(123, 5)
    badges = sb._badges
    assert badges["library"].text() == "123"
    assert badges["favorites"].text() == "5"
    assert badges["library"].isVisibleTo(sb), "library badge visible"
    assert badges["favorites"].isVisibleTo(sb), "favorites badge visible"
    sb.set_collapsed(True)
    assert not badges["library"].isVisibleTo(sb), "badge hidden when collapsed"
    sb.set_collapsed(False)
    assert badges["library"].isVisibleTo(sb)
    sb.set_library_counts(0, 0)
    assert not badges["library"].isVisibleTo(sb), "badge hidden at 0"
    assert not badges["favorites"].isVisibleTo(sb)
check("sidebar_badges", sidebar_badges)


def sidebar_theming():
    sb.set_theme(IVORY)
    sb.set_theme(ROYAL)
    assert sb._theme_label is not None and sb._theme_label.text() == "Royal Gold"
    assert sb._more_menu is not None
check("sidebar_set_theme_twice", sidebar_theming)


# ============================================================================
print("\n=== Animations ===")
# ============================================================================
host.show()
app.processEvents()


def anim_fade_in():
    lbl = QLabel("fade", host)
    lbl.show()
    app.processEvents()
    done = []
    fade_in(lbl, 100, lambda: done.append(1))
    assert lbl.graphicsEffect() is not None, "effect attached during fade"
    wait(300)
    assert done == [1], f"callback {done}"
    assert lbl.graphicsEffect() is None, "effect removed after fade"
check("anim_fade_in", anim_fade_in)


def anim_fade_out():
    lbl = QLabel("out", host)
    lbl.show()
    app.processEvents()
    assert not lbl.isHidden(), "precondition: label visible"
    done = []
    fade_out(lbl, 90, lambda: done.append(1))
    wait(250)
    assert done == [1]
    assert lbl.isHidden(), "widget hidden after fade_out"
    assert lbl.graphicsEffect() is None
check("anim_fade_out", anim_fade_out)


def anim_slide_in():
    w = QWidget()
    w.resize(120, 60)
    w.move(400, 300)
    w.show()
    app.processEvents()
    orig = w.pos()
    slide_in(w, 100, "left")
    assert w.pos().x() < orig.x(), "starts offset to the left"
    wait(250)
    assert w.pos() == orig, f"ends at original pos {w.pos()} vs {orig}"
    w.hide()
check("anim_slide_in", anim_slide_in)


def anim_pop_in():
    w = QWidget()
    w.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
    w.resize(200, 120)
    w.show()
    app.processEvents()
    final = w.geometry()
    pop_in(w, 100)
    wait(300)
    assert w.geometry() == final, f"geometry {w.geometry()} vs {final}"
    assert w.graphicsEffect() is None, "fade part cleaned up"
    w.hide()
check("anim_pop_in", anim_pop_in)


def anim_stagger():
    labels = [QLabel(str(i), host) for i in range(30)]
    stagger_fade(labels, per_item_ms=20, start_ms=40)
    for i, lbl in enumerate(labels):
        if i < 24:
            assert lbl.graphicsEffect() is not None, f"item {i} parked at 0"
        else:
            assert lbl.graphicsEffect() is None, f"item {i} beyond cap"
    wait(40 + 24 * 20 + 400)
    for lbl in labels:
        assert lbl.graphicsEffect() is None, "effects cleaned after stagger"
check("anim_stagger_fade_capped_24", anim_stagger)


def anim_value_animator():
    vals = []
    done = []
    va = ValueAnimator(0.0, 10.0, 100, "out_cubic", vals.append,
                       lambda: done.append(1))
    assert not va.is_running()
    va.start()
    assert va.is_running()
    wait(300)
    assert vals and vals[0] <= vals[-1], "monotonic-ish delivery"
    assert abs(vals[-1] - 10.0) < 1e-9, f"final value {vals[-1]}"
    assert done == [1]
    assert not va.is_running()
check("anim_value_animator", anim_value_animator)


def anim_pulse():
    lbl = QLabel("pulse", host)
    ValueAnimator.pulse(lbl, ms=120)
    assert lbl.graphicsEffect() is not None, "pulse installs an effect"
    wait(300)
    assert lbl.graphicsEffect() is None, "pulse cleans up"
check("anim_valueanimator_pulse", anim_pulse)


def anim_guard_foreign_effect():
    lbl = QLabel("foreign", host)
    eff = QGraphicsOpacityEffect(lbl)
    eff.setOpacity(0.5)
    lbl.setGraphicsEffect(eff)
    done = []
    fade_in(lbl, 100, lambda: done.append(1))
    assert done == [1], "skipped synchronously"
    assert lbl.graphicsEffect() is eff, "foreign effect untouched"
check("anim_guard_foreign_effect", anim_guard_foreign_effect)


def anim_reduce_mode():
    set_reduce_animations(True)
    try:
        assert anim_mod.REDUCE_ANIMATIONS is True, "module flag set"
        lbl = QLabel("reduce", host)
        lbl.show()
        done = []
        fade_in(lbl, 100, lambda: done.append(1))
        assert done == [1] and lbl.graphicsEffect() is None
        lbl2 = QLabel("reduce2", host)
        lbl2.show()
        done2 = []
        fade_out(lbl2, 100, lambda: done2.append(1))
        assert done2 == [1] and lbl2.isHidden()
        labels = [QLabel(f"r{i}", host) for i in range(5)]
        for l in labels:
            l.show()
        stagger_fade(labels)
        assert all(l.graphicsEffect() is None for l in labels)
        ValueAnimator.pulse(lbl)
        assert lbl.graphicsEffect() is None, "pulse no-ops in reduce mode"
    finally:
        set_reduce_animations(False)
    assert anim_mod.REDUCE_ANIMATIONS is False, "module flag restored"
check("anim_reduce_mode_noops", anim_reduce_mode)


# ============================================================================
print("\n=== Command Palette ===")
# ============================================================================
pwin = QWidget()
pwin.resize(1200, 800)
pwin.show()
app.processEvents()

run_log = []
search_calls = []


def make_actions():
    return [
        {"icon": Icon.SETTINGS, "title": "Open Settings", "hint": "Ctrl+,",
         "group": "Command", "run": lambda: run_log.append("settings")},
        {"icon": Icon.BRUSH, "title": "Switch Theme", "group": "Command",
         "run": lambda: run_log.append("theme")},
        {"icon": Icon.LIBRARY, "title": "Go to Library", "group": "Navigation",
         "run": lambda: run_log.append("library")},
        {"icon": Icon.ALBUMS, "title": "Go to Albums", "group": "Navigation",
         "run": lambda: run_log.append("albums")},
        {"icon": Icon.PLAYLISTS, "title": "Go to Playlists",
         "group": "Navigation", "run": lambda: run_log.append("playlists")},
        {"icon": Icon.STATS, "title": "Go to Stats", "group": "Go to",
         "run": lambda: run_log.append("stats")},
        {"icon": Icon.SEARCH, "title": "Search Tracks", "group": "Command",
         "run": lambda: run_log.append("search")},
    ]


def search_provider(query):
    search_calls.append(query)
    return [
        {"icon": Icon.MUSIC_NOTE, "title": f"Track: {query} one",
         "subtitle": "Artist A", "run": lambda: run_log.append("track1")},
        {"icon": Icon.MUSIC_NOTE, "title": f"Track: {query} two",
         "subtitle": "Artist B", "run": lambda: run_log.append("track2")},
    ]


palette = CommandPalette(pwin, make_actions, search_provider)
palette.set_theme(ROYAL)


def palette_titles():
    out = []
    for i in range(palette.list.count()):
        item = palette.list.item(i)
        act = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(act, dict):
            out.append(str(act.get("title")))
    return out


def palette_header_count():
    return sum(
        1 for i in range(palette.list.count())
        if not (palette.list.item(i).flags() & Qt.ItemFlag.ItemIsSelectable)
    )


def palette_construct():
    assert palette.objectName() == "CommandPalette"
    assert palette.input.objectName() == "CommandInput"
    assert palette.list.objectName() == "CommandList"
    assert palette.input.placeholderText() == "Type a command or search\u2026"
    assert palette.footer.text() == "\u2191\u2193 navigate \u00b7 Enter run \u00b7 Esc close"
    assert palette.styleSheet(), "themed via build_qss_v3"
check("palette_construct_styled", palette_construct)


def palette_popup_default():
    palette.popup()
    app.processEvents()
    assert palette.isVisible(), "popup shows"
    titles = palette_titles()
    assert len(titles) == 7, f"all 7 commands shown, got {titles}"
    assert palette_header_count() >= 3, "group headers present"
    cur = palette.list.currentItem()
    assert cur is not None and cur.data(Qt.ItemDataRole.UserRole), \
        "first selectable row selected"
check("palette_popup_default_rows", palette_popup_default)


def palette_filter_substring():
    search_calls.clear()
    palette.input.setText("albums")
    wait(250)  # > 120ms debounce
    titles = palette_titles()
    assert "Go to Albums" in titles, titles
    assert "Go to Library" not in titles, titles
    assert search_calls == ["albums"], search_calls
    assert "Track: albums one" in titles, "search results appended"
check("palette_filter_substring_and_search", palette_filter_substring)


def palette_filter_fuzzy():
    search_calls.clear()
    palette.input.setText("albmus")  # transposed — difflib ratio ~0.67
    wait(250)
    assert "Go to Albums" in palette_titles(), "fuzzy match kept"
    assert search_calls == ["albmus"]
check("palette_filter_fuzzy_difflib", palette_filter_fuzzy)


def palette_no_match():
    palette.input.setText("zzzqqq")
    wait(250)
    titles = palette_titles()
    assert "Go to Albums" not in titles
    assert titles == ["Track: zzzqqq one", "Track: zzzqqq two"], titles
check("palette_no_match_falls_back_to_search", palette_no_match)


def palette_command_mode():
    search_calls.clear()
    palette.input.setText(">set")
    wait(250)
    titles = palette_titles()
    assert "Open Settings" in titles, titles
    assert "Go to Library" not in titles, titles
    assert search_calls == [], f"search must not run in > mode: {search_calls}"
check("palette_command_mode_prefix", palette_command_mode)


def palette_keyboard_and_deferred_run():
    palette.popup()
    app.processEvents()
    assert palette.isVisible()
    run_log.clear()
    QTest.keyClick(palette.input, Qt.Key.Key_Return)
    assert not palette.isVisible(), "palette closes on Enter"
    assert run_log == [], "run must be deferred, not re-entrant"
    app.processEvents()
    assert run_log == ["settings"], f"deferred run executed: {run_log}"
    # hide/popup cycle again to prove re-open works
    palette.popup()
    app.processEvents()
    assert palette.isVisible()
    QTest.keyClick(palette.input, Qt.Key.Key_Escape)
    assert not palette.isVisible(), "Esc closes"
check("palette_enter_deferred_run_and_esc", palette_keyboard_and_deferred_run)


def palette_arrows_skip_headers():
    palette.popup()
    app.processEvents()
    QTest.keyClick(palette.input, Qt.Key.Key_Down)
    app.processEvents()
    item = palette.list.currentItem()
    assert item is not None and item.data(Qt.ItemDataRole.UserRole), \
        "selection never lands on a group header"
    QTest.keyClick(palette.input, Qt.Key.Key_Up)
    app.processEvents()
    item = palette.list.currentItem()
    assert item is not None and item.data(Qt.ItemDataRole.UserRole)
    palette.hide()
check("palette_arrows_skip_headers", palette_arrows_skip_headers)


def palette_theming():
    palette.set_theme(IVORY)
    palette.set_theme(ROYAL)
    assert "CommandPalette" in palette.styleSheet()
check("palette_set_theme_twice", palette_theming)


# ============================================================================
print("\n=== Empty States / Skeleton / Spinner ===")
# ============================================================================


def empty_state_basic():
    acted = []
    es = EmptyState(Icon.MUSIC_NOTE, "Nothing here",
                    "Add a folder to start listening",
                    "Add Folder", lambda: acted.append(1), ROYAL)
    es.setParent(host)
    es.resize(400, 300)
    es.show()
    assert es.title_label.objectName() == "EmptyTitle"
    assert es.hint_label.objectName() == "EmptyHint"
    assert es.action_button is not None
    assert es.action_button.objectName() == "GoldBtn"
    wait(300)  # fade-in on show completes
    assert es.graphicsEffect() is None, "fade-in cleaned up"
    es.action_button.click()
    assert acted == [1], "on_action wired"
    es.set_theme(IVORY)
    es.set_theme(ROYAL)
    es.hide()
check("empty_state_construct_fade_action", empty_state_basic)


def skeleton_rows_basic():
    sk = SkeletonRows(6, host)
    assert sk.rows() == 6
    bars = sk.findChildren(QFrame, "SkeletonLine")
    assert len(bars) == 6, f"skeleton bars {len(bars)}"
    sk.show()
    wait(100)
    sk.start()
    wait(2000)  # at least one shimmer pass
    sk.stop()
    wait(400)   # let in-flight pulses finish
    for bar in sk._rows:
        assert bar.graphicsEffect() is None, "stop() clears effects"
    sk.set_theme(IVORY)
    sk.set_theme(ROYAL)
    sk.hide()
    sk.show()
    wait(120)
    sk.hide()
check("skeleton_rows_start_stop_shimmer", skeleton_rows_basic)


def spinner_basic():
    sp = InlineSpinner(ROYAL, host)
    sp.show()
    wait(120)
    assert sp.is_spinning(), "spins while visible"
    sp.hide()
    app.processEvents()
    assert not sp.is_spinning(), "stops when hidden (hideEvent)"
    sp.set_color("#ff3355")
    sp.set_theme(IVORY)
    sp.show()
    wait(60)
    assert sp.is_spinning()
    sp.hide()
check("inline_spinner_show_hide", spinner_basic)


# ============================================================================
print("\n" + "=" * 50)
print(f"UI-COMPONENTS SUMMARY: {passed} passed, {failed} failed")
print("=" * 50)
if errors:
    for n, tb in errors:
        print(f"\n--- {n} ---\n{tb}")
    sys.exit(1)
print("ALL UI COMPONENT TESTS PASSED!")
