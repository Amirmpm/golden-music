"""Offline UI tests for the Golden Music 2.3 lyrics components
(gm/ui/lyrics_ui.py): LyricsView, LyricsQualityBar, LyricsEditorDialog,
FloatingLyricsWidget, MiniLyricsBar + the local LRC parser.

Run:
    source /home/z/env.sh
    QT_QPA_PLATFORM=offscreen python3 scripts/test_lyrics_ui.py

Constraints: NO network, NO pytest, NO gm.window/main import — plain
script with [OK]/[FAIL] lines, a SUMMARY and exit code 1 on failure.
Persian sample lines prove the per-label RTL handling.
"""
import os
import sys
import traceback
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# --- Test isolation: sandbox HOME + portable config -------------------------
# MUST run before any project import (config_path() reads HOME at call time).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import testenv as _testenv  # noqa: E402
_testenv.install()
del _testenv
# ------------------------------------------------------------------------------

from PyQt6.QtCore import Qt, QEvent, QPointF, QAbstractAnimation  # noqa: E402
from PyQt6.QtGui import QMouseEvent  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication, QLabel, QPushButton, QPlainTextEdit, QSpinBox,
)

QAPP = QApplication.instance() or QApplication(sys.argv)
QAPP.setApplicationName("GM-Test-LyricsUI")

from config import Theme  # noqa: E402
import gm.ui.lyrics_ui as lui  # noqa: E402
from gm.ui.lyrics_ui import (  # noqa: E402
    LyricsView, LyricsQualityBar, LyricsEditorDialog,
    FloatingLyricsWidget, MiniLyricsBar, parse_lrc_local, _is_rtl,
)

PASS = 0
FAIL = 0
FAILURES = []


def check(name, fn):
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  [OK] {name}")
    except Exception as e:  # noqa: BLE001
        FAIL += 1
        FAILURES.append(name)
        print(f"  [FAIL] {name}: {e}")
        print(traceback.format_exc())


def line_labels(view):
    """LyricLine labels in line order (sorted by the line_index property)."""
    lbls = [l for l in view.findChildren(QLabel)
            if l.objectName() == "LyricLine"]
    return sorted(lbls, key=lambda l: int(l.property("line_index") or 0))


def translation_labels(view):
    lbls = [l for l in view.findChildren(QLabel)
            if l.objectName() == "LyricTranslation"]
    return sorted(lbls, key=lambda l: int(l.property("line_index") or 0))


def find_btn(widget, text):
    for b in widget.findChildren(QPushButton):
        if b.text() == text:
            return b
    return None


def mouse_press(target):
    ev = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(3, 3),
                     QPointF(3, 3), Qt.MouseButton.LeftButton,
                     Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    return QApplication.sendEvent(target, ev)


def mouse_dblclick(target):
    ev = QMouseEvent(QEvent.Type.MouseButtonDblClick, QPointF(4, 4),
                     QPointF(4, 4), Qt.MouseButton.LeftButton,
                     Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    return QApplication.sendEvent(target, ev)


# --- sample data (Persian lines prove the RTL path) --------------------------
SYNCED_LINES = [
    (1000, "سلام ای دنیا"),            # RTL original
    (4000, "Golden road ahead"),        # LTR original
    (8000, "طلایی راه"),                # RTL original
    (12000, "موسیقی هنر است"),          # RTL original
    (16000, "The end"),
]
TRANSLATIONS = [
    (1000, "سلام ای دنیا", "Hello world"),            # LTR translation
    (4000, "Golden road ahead", "جاده طلایی پیش رو"),  # RTL translation
    (8000, "طلایی راه", "Golden road"),
]
PLAIN_LINES = [(None, "just words"), (None, "no timing here")]

BAD_LYRICS = "short"          # verdict() -> {"valid": False, "reason": "too_short"}


# =============================================================================
print("\n=== module helpers ===")


def t_parse_multi_stamp():
    out = parse_lrc_local("[00:01.000][00:02.500]chorus\nplain line\n")
    assert out == [(1000, "chorus"), (2500, "chorus"), (None, "plain line")], out


check("parse_lrc_local multi-timestamp + plain", t_parse_multi_stamp)


def t_parse_invalid_token():
    try:
        parse_lrc_local("[00:01.000]ok\n[05:XX]bad\n")
    except ValueError:
        return
    raise AssertionError("[05:XX] should raise ValueError")


check("parse_lrc_local rejects malformed stamp", t_parse_invalid_token)


def t_parse_seconds_range():
    try:
        parse_lrc_local("[00:99.000]bad seconds\n")
    except ValueError:
        return
    raise AssertionError("seconds >= 60 should raise ValueError")


check("parse_lrc_local rejects seconds >= 60", t_parse_seconds_range)


def t_rtl_detection():
    assert _is_rtl("سلام ای دنیا") is True
    assert _is_rtl("جاده طلایی پیش رو") is True
    assert _is_rtl("Golden road ahead") is False
    assert _is_rtl("") is False


check("_is_rtl Persian vs English", t_rtl_detection)


# =============================================================================
print("\n=== LyricsView (both themes) ===")


def exercise_view(theme_name):
    theme = dict(Theme.get(theme_name))

    view = LyricsView(theme)
    assert view.theme["name"] == theme_name

    # -- synced set_lines: label counts -----------------------------------
    view.set_lines(SYNCED_LINES, TRANSLATIONS, source="LRCLIB")
    lbls = line_labels(view)
    trans = translation_labels(view)
    assert len(lbls) == 5, f"expected 5 line labels, got {len(lbls)}"
    assert len(trans) == 3, f"expected 3 translation labels, got {len(trans)}"
    assert lbls[2].text() == "طلایی راه"

    # -- sync_position: active line is bold + gold ------------------------
    view.sync_position(4100)            # line at 4000 becomes active
    active_flags = [bool(l.property("active")) for l in lbls]
    assert active_flags == [False, True, False, False, False], active_flags
    ss = lbls[1].styleSheet()
    assert theme["gold"] in ss, f"active line not gold: {ss}"
    assert "700" in ss, f"active line not bold: {ss}"
    assert lbls[1].property("role") == "active"
    assert lbls[2].property("role") == "near"   # next 2 lines stay normal
    assert lbls[3].property("role") == "near"
    assert lbls[0].property("role") == "muted"  # past lines muted

    # -- click a synced line -> line_activated(ms) ------------------------
    got = []
    view.line_activated.connect(got.append)
    mouse_press(lbls[2])                # line index 2 -> 8000 ms
    assert got == [8000], f"line_activated got {got}"
    view.line_activated.disconnect(got.append)

    # -- RTL: Persian line label has RightToLeft direction ----------------
    assert lbls[2].layoutDirection() == Qt.LayoutDirection.RightToLeft, \
        "Persian original line should be RTL"
    assert lbls[1].layoutDirection() == Qt.LayoutDirection.LeftToRight, \
        "English original line should be LTR"
    assert trans[1].layoutDirection() == Qt.LayoutDirection.RightToLeft, \
        "Persian translation label should be RTL"

    # -- translation toggle ------------------------------------------------
    toggled = []
    view.translation_toggled.connect(toggled.append)
    view._toggle_btn.click()            # hide translations
    assert toggled == [False], toggled
    assert all(not l.isVisibleTo(view) for l in trans), \
        "translation labels should be hidden after toggle"
    view._toggle_btn.click()            # show them again
    assert toggled == [False, True], toggled

    # -- plain lyrics: reading view, no highlight, no crash ----------------
    view.set_lines(PLAIN_LINES)
    lbls = line_labels(view)
    assert len(lbls) == 2
    view.sync_position(12345)           # must be a harmless no-op
    assert not any(bool(l.property("active")) for l in lbls), \
        "plain lyrics must not highlight anything"

    # -- clear --------------------------------------------------------------
    view.clear()
    assert len(line_labels(view)) == 0
    assert len(translation_labels(view)) == 0

    # -- theme reactivity ----------------------------------------------------
    view.set_lines(SYNCED_LINES, TRANSLATIONS)
    view.sync_position(4100)
    other = dict(Theme.get("ivory" if theme_name != "ivory" else "royal_gold"))
    view.set_theme(other)
    ss = line_labels(view)[1].styleSheet()
    assert other["gold"] in ss, f"theme switch did not restyle: {ss}"
    assert bool(line_labels(view)[1].property("active")) is True


for name in ("royal_gold", "ivory"):
    check(f"LyricsView synced/bilingual/click/RTL/theme [{name}]",
          lambda n=name: exercise_view(n))


# =============================================================================
print("\n=== LyricsQualityBar ===")


def t_quality_warning_mode():
    bar = LyricsQualityBar(dict(Theme.get("royal_gold")))
    bar.show_warning(BAD_LYRICS, None)
    assert bar.isVisible(), "bar should be visible after show_warning"
    reason = bar._reason_label.text()
    assert "too_short" in reason, f"reason text missing verdict reason: {reason!r}"
    assert bar._primary_btn.text() == "Find better"
    assert bar._secondary_btn.text() == "Keep anyway"


check("quality bar warning mode shows reason", t_quality_warning_mode)


def t_quality_find_better():
    bar = LyricsQualityBar(dict(Theme.get("royal_gold")))
    bar.show_warning(BAD_LYRICS, None)
    got = []
    bar.search_requested.connect(lambda: got.append("search"))
    find_btn(bar, "Find better").click()
    assert got == ["search"], f"search_requested not emitted: {got}"
    assert not bar.isVisible(), "bar should hide after the choice"


check("quality bar 'Find better' emits + hides", t_quality_find_better)


def t_quality_candidate_mode():
    bar = LyricsQualityBar(dict(Theme.get("ivory")))
    bar.show_warning(BAD_LYRICS, "Musixmatch — full lyrics, 42 lines")
    assert "Better lyrics found" in bar._title_label.text()
    assert "42 lines" in bar._reason_label.text()
    got = []
    bar.replace_requested.connect(lambda: got.append("replace"))
    find_btn(bar, "Replace").click()
    assert got == ["replace"], f"replace_requested not emitted: {got}"
    assert not bar.isVisible(), "bar should hide after the choice"


check("quality bar candidate mode Replace", t_quality_candidate_mode)


def t_quality_keep_anyway():
    bar = LyricsQualityBar(dict(Theme.get("royal_gold")))
    bar.show_warning(BAD_LYRICS, None)
    got = []
    bar.keep_requested.connect(lambda: got.append("keep"))
    find_btn(bar, "Keep anyway").click()
    assert got == ["keep"], f"keep_requested not emitted: {got}"
    assert not bar.isVisible(), "bar should hide after the choice"


check("quality bar 'Keep anyway' emits + hides", t_quality_keep_anyway)


# =============================================================================
print("\n=== LyricsEditorDialog ===")


def t_editor_valid_lrc_ok():
    dlg = LyricsEditorDialog(SYNCED_LINES, True, dict(Theme.get("royal_gold")))
    dlg.show()
    editor = dlg.findChild(QPlainTextEdit)
    assert editor is not None
    assert editor.toPlainText().startswith("[00:01.000]"), \
        "synced dialog should serialize to LRC"
    editor.setPlainText("[00:01.000]first\n[00:04.000]second\n[00:08.000]third\n")
    ok_btn = find_btn(dlg, "OK")
    assert ok_btn is not None and ok_btn.objectName() == "GoldBtn"
    ok_btn.click()
    assert not dlg.isVisible(), "dialog should close after OK"
    res = dlg.result_lines()
    assert res[0] == (1000, "first"), f"first line mismatch: {res[0]}"
    assert res[1] == (4000, "second") and res[2] == (8000, "third")
    assert dlg.offset_ms() == 0
    assert dlg.save_mode() == "embed", "default save mode should be embed"
    dlg._save_combo.setCurrentIndex(1)
    assert dlg.save_mode() == "sidecar"
    dlg._save_combo.setCurrentIndex(2)
    assert dlg.save_mode() == "none"


check("editor valid LRC -> result_lines/save_mode", t_editor_valid_lrc_ok)


def t_editor_invalid_timestamp():
    dlg = LyricsEditorDialog(SYNCED_LINES, True, dict(Theme.get("royal_gold")))
    dlg.show()
    dlg.findChild(QPlainTextEdit).setPlainText(
        "[00:01.000]ok\n[05:XX]broken\n")

    calls = []

    class _FakeMessageBox:
        @staticmethod
        def warning(*args, **kwargs):
            calls.append(args)
            return "ok"

    original = lui.QMessageBox
    lui.QMessageBox = _FakeMessageBox
    try:
        valid = dlg._validate()
    finally:
        lui.QMessageBox = original
    assert valid is False, "invalid timestamps must fail validation"
    assert len(calls) == 1, f"expected exactly one warning, got {len(calls)}"


check("editor invalid timestamp -> warning, no accept", t_editor_invalid_timestamp)


def t_editor_offset_shift():
    dlg = LyricsEditorDialog(SYNCED_LINES, True, dict(Theme.get("royal_gold")))
    dlg.show()
    spin = dlg.findChild(QSpinBox)
    assert spin is not None
    assert spin.minimum() == -10000 and spin.maximum() == 10000
    spin.setValue(500)
    find_btn(dlg, "Shift timings").click()
    parsed = parse_lrc_local(dlg.findChild(QPlainTextEdit).toPlainText())
    assert len(parsed) == 5, parsed
    for (ms, _txt), (orig_ms, _o) in zip(parsed, SYNCED_LINES):
        assert ms == orig_ms + 500, f"expected {orig_ms}+500, got {ms}"
    assert dlg.offset_ms() == 500, dlg.offset_ms()
    # negative shift back must work too
    spin.setValue(-500)
    find_btn(dlg, "Shift timings").click()
    parsed = parse_lrc_local(dlg.findChild(QPlainTextEdit).toPlainText())
    assert [ms for ms, _t in parsed] == [1000, 4000, 8000, 12000, 16000], parsed
    assert dlg.offset_ms() == 0


check("editor Shift timings applies offset", t_editor_offset_shift)


def t_editor_insert_timestamp():
    dlg = LyricsEditorDialog(SYNCED_LINES, True, dict(Theme.get("royal_gold")))
    dlg.show()
    editor = dlg.findChild(QPlainTextEdit)
    editor.setPlainText("[00:10.000]already stamped\nneeds a stamp\n")
    before = len(parse_lrc_local(editor.toPlainText()))
    dlg._insert_timestamp()
    after_text = editor.toPlainText()
    after = parse_lrc_local(after_text)
    assert len(after) == before + 1, \
        f"insert should add exactly one stamped line: {after_text!r}"
    assert "[" in after_text


check("editor Insert timestamp adds a stamp", t_editor_insert_timestamp)


def t_editor_plain_mode():
    dlg = LyricsEditorDialog(PLAIN_LINES, False, dict(Theme.get("ivory")))
    dlg.show()
    editor = dlg.findChild(QPlainTextEdit)
    assert editor.toPlainText() == "just words\nno timing here\n", \
        repr(editor.toPlainText())
    editor.setPlainText("alpha\nbeta\n")
    find_btn(dlg, "OK").click()
    res = dlg.result_lines()
    assert res == [(None, "alpha"), (None, "beta")], res


check("editor plain mode round-trip", t_editor_plain_mode)


# =============================================================================
print("\n=== FloatingLyricsWidget ===")


def t_floating_sync_and_lock():
    fl = FloatingLyricsWidget(dict(Theme.get("royal_gold")))
    fl.set_lines(SYNCED_LINES, TRANSLATIONS)
    cur = fl.findChild(QLabel, "FloatCurrent")
    nxt = fl.findChild(QLabel, "FloatNext")
    assert cur is not None and nxt is not None

    fl.sync_position(1000)                 # line 0 active
    assert cur.text() == "سلام ای دنیا", cur.text()
    assert nxt.text() == "Hello world", nxt.text()   # translation of line 0
    fl.sync_position(8500)                 # line at 8000 active
    assert cur.text() == "طلایی راه", cur.text()
    assert nxt.text() == "Golden road", nxt.text()
    assert cur.layoutDirection() == Qt.LayoutDirection.RightToLeft, \
        "Persian floating line should be RTL"

    # click-through toggle drives the mouse-transparent attribute
    fl.set_click_through(True)
    assert fl.testAttribute(
        Qt.WidgetAttribute.WA_TransparentForMouseEvents) is True
    fl.set_click_through(False)
    assert fl.testAttribute(
        Qt.WidgetAttribute.WA_TransparentForMouseEvents) is False

    # lock -> transparent for mouse + chip
    fl.lock(True)
    assert fl.testAttribute(
        Qt.WidgetAttribute.WA_TransparentForMouseEvents) is True
    assert fl._lock_chip.isVisibleTo(fl), "lock chip should be visible"
    fl.lock(False)
    assert fl.testAttribute(
        Qt.WidgetAttribute.WA_TransparentForMouseEvents) is False

    # double-click (while interactive/unlocked) -> unlock_requested
    got = []
    fl.unlock_requested.connect(lambda: got.append("unlock"))
    mouse_dblclick(fl)
    assert got == ["unlock"], f"unlock_requested not emitted: {got}"

    # no-crash extras
    fl.set_font_scale(1.3)
    fl.set_theme(dict(Theme.get("ivory")))
    fl.sync_position(20000)
    assert cur.text() == "The end", cur.text()


check("floating lyrics sync/RTL/lock/unlock", t_floating_sync_and_lock)


# =============================================================================
print("\n=== MiniLyricsBar ===")


def t_mini_bar():
    mb = MiniLyricsBar(dict(Theme.get("royal_gold")))
    mb.set_lines(SYNCED_LINES)
    lbl = mb.findChild(QLabel, "MiniLyricsLabel")
    assert lbl is not None
    mb.sync_position(4100)
    assert "Golden road ahead" in lbl.text(), lbl.text()
    got = []
    mb.seek_requested.connect(got.append)
    mouse_dblclick(mb)
    assert got == [4000], f"seek_requested got {got}"
    mb.sync_position(13000)
    assert lbl.text().strip() == "موسیقی هنر است", lbl.text()
    mouse_dblclick(mb)
    assert got == [4000, 12000], f"second seek got {got}"
    mb.set_theme(dict(Theme.get("ivory")))      # no crash
    mb.set_accent_color("#ff5500")              # album color accent
    mb.sync_position(100)                       # before first line
    assert lbl.text().strip() == "سلام ای دنیا", lbl.text()


check("mini bar sync/seek/theme", t_mini_bar)


# =============================================================================
print("\n=== reduce-motion switch ===")


def t_reduce_animations():
    # the guarded import must always expose a usable flag
    assert isinstance(lui.REDUCE_ANIMATIONS, bool)
    if lui._animations is not None:
        # live read-through: set_reduce_animations() must be honored
        anim_mod = lui._animations
        anim_mod.set_reduce_animations(True)
        fl = None
        try:
            assert lui._reduce_animations() is True
            fl = FloatingLyricsWidget(dict(Theme.get("royal_gold")))
            fl.show()
            fl.set_lines(SYNCED_LINES, TRANSLATIONS)
            fl.sync_position(1000)
            # snap path: full opacity immediately, no animation running
            assert fl._cur_effect.opacity() == 1.0
            assert fl._fade_anim.state() != QAbstractAnimation.State.Running
        finally:
            anim_mod.set_reduce_animations(False)
        assert lui._reduce_animations() is False
        if fl is not None:
            # animate path: cross-fade starts from 0 opacity
            fl.sync_position(8500)
            assert fl._cur_effect.opacity() == 0.0
            assert fl._fade_anim.state() == QAbstractAnimation.State.Running
            fl.hide()
    else:
        # fallback: the GM_REDUCE_ANIMATIONS env var drives the flag
        os.environ["GM_REDUCE_ANIMATIONS"] = "1"
        try:
            assert lui._reduce_animations() is True
        finally:
            os.environ.pop("GM_REDUCE_ANIMATIONS", None)
        assert lui._reduce_animations() is False


check("reduce-animations flag respected", t_reduce_animations)


# =============================================================================
QAPP.processEvents()
print("\n" + "=" * 50)
print(f"LYRICS-UI SUMMARY: {PASS} passed, {FAIL} failed")
print("=" * 50)
if FAILURES:
    print("Failed checks: " + ", ".join(FAILURES))
    sys.exit(1)
print("ALL LYRICS-UI TESTS PASSED!")
