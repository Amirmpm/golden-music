"""Offline tests for the Golden Music 3.0 settings dialog (gm/ui/settings3.py).

Covers the dialog contract with a FAKE main window that only provides
``prefs``, ``apply_prefs`` and ``theme`` — everything else the dialog
touches must be getattr-guarded (no AttributeError may ever escape):

  1.  construction for two themes (dark + light), size/modality;
  2.  every category button switches the stacked pages;
  3.  search "crossfade" lists the matching rows (live widgets borrowed
      into the results page); "zzznope" shows the no-results row;
      clearing the search restores rows to their home pages;
  4.  toggling a checkbox updates the working prefs copy;
  5.  Cancel -> apply_prefs NOT called;
  6.  OK (QPushButton#GoldBtn) -> apply_prefs called exactly once with
      the edited dict (EQ round-trip + non-default shortcuts included);
  7.  shortcut conflict detection via _set_shortcut/_shortcut_conflicts;
  8.  Reset Defaults clears the prefs copy back to defaults;
  9.  EQ sliders round-trip into prefs eq_gains;
  10. guarded mw.* hooks never crash on a minimal fake window.

Run:  QT_QPA_PLATFORM=offscreen python3 scripts/test_settings3.py
Constraints: NO main/gm.window import, NO network, NO pytest — plain
script with [OK]/[FAIL] lines, a SUMMARY and exit code 1 on failure.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtWidgets import QApplication, QPushButton, QDialog, QCheckBox  # noqa: E402

import gm.ui.settings3 as s3mod  # noqa: E402
from gm.ui.settings3 import (  # noqa: E402
    DEFAULT_SHORTCUT_DEFS, RESET_FLAG_KEY, SettingsDialog3, SettingRow,
)
from config import Theme  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
app.setApplicationName("GM-Settings3-Test")

PASS = 0
FAIL = 0
FAILURES: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    """Record one [OK]/[FAIL] line."""
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK] {name}")
    else:
        FAIL += 1
        FAILURES.append(f"{name} {extra}".strip())
        print(f"  [FAIL] {name} {extra}")


def pe() -> None:
    """Process pending Qt events."""
    app.processEvents()


def make_prefs() -> dict:
    """A representative prefs payload, including the EQ gains list."""
    return {
        # appearance
        "ui_scale": 1.25, "reduce_animations": True, "density": "compact",
        "show_covers_in_list": True, "list_view": "table",
        "dynamic_album_theme": True, "language": "fa",
        # playback
        "crossfade_ms": 3000, "gapless": False,
        "pitch_preserve_speed": True, "silence_skip": True,
        "bookmarks_enabled": True, "fade_enabled": True, "fade_ms": 600,
        # audio & EQ
        "audio_engine": "mpv", "normalization": "tags",
        "eq_enabled": True, "eq_preset": "Rock",
        "eq_gains": [4.0, 3.0, -1.0, -2.0, 0.0, 1.0, 3.0, 4.0, 4.0, 4.0],
        # library
        "auto_rescan": True, "folder_watch": True,
        "folder_watch_debounce_ms": 750,
        "smart_most_played": True, "smart_recently_added": False,
        "smart_never_played": True, "smart_not_heard_recently": False,
        "smart_top_rated": True, "m3u_relative_paths": True,
        # lyrics
        "lyrics_online": True, "lyrics_show_translation": True,
        "lyrics_save_mode": "embed", "lyrics_quality_check": True,
        "floating_lyrics": False, "lyrics_font_scale": 1.2,
        # stats
        "stats_charts_enabled": True, "stats_heatmap_enabled": True,
        "stats_wrapped_enabled": True,
        # integrations
        "webremote_enabled": False, "webremote_host": "0.0.0.0",
        "webremote_port": 8642, "webremote_token": "abc123",
        "telegram_enabled": True, "telegram_bot_token": "123:ABC",
        "telegram_allowed_ids": "111, 222", "updates_enabled": True,
        # shortcuts
        "custom_shortcuts": {"theme_picker": "Ctrl+Shift+P"},
        # advanced
        "default_folder": "/music", "toast_enabled": True,
        "taskbar_close_to_tray": True,
    }


class FakeWindow:
    """Duck-typed main window: ONLY prefs/apply_prefs/theme exist."""

    def __init__(self, prefs: dict, theme: dict) -> None:
        self.prefs = prefs
        self.theme = theme
        self.applied: list[dict] = []

    def apply_prefs(self, new_prefs: dict) -> None:
        self.applied.append(new_prefs)


def find_button(dlg: QDialog, text: str) -> QPushButton:
    """First QPushButton with exact *text* inside the dialog."""
    for btn in dlg.findChildren(QPushButton):
        if btn.text() == text:
            return btn
    raise AssertionError(f"button not found: {text!r}")


def find_row(dlg: SettingsDialog3, key: str) -> SettingRow:
    """The SettingRow editing prefs *key*."""
    for row in dlg._rows:
        if row.key == key:
            return row
    raise AssertionError(f"no row edits key {key!r}")


def fresh(theme_name: str = "royal_gold") -> tuple[SettingsDialog3, FakeWindow]:
    """A new dialog + fake window pair."""
    mw = FakeWindow(make_prefs(), Theme.get(theme_name))
    dlg = SettingsDialog3(mw)
    pe()
    return dlg, mw


# ---------------------------------------------------------------- 1. build
print("== construction ==")
for theme_name in ("royal_gold", "ivory"):
    try:
        dlg, mw = fresh(theme_name)
        ok = dlg is not None
    except Exception as exc:  # noqa: BLE001 - the test must capture crashes
        ok = False
        print(f"    construction raised: {exc!r}")
    check(f"constructs with theme {theme_name!r}", ok)

dlg, mw = fresh()
check("minimum size is 860x600",
      dlg.minimumWidth() == 860 and dlg.minimumHeight() == 600,
      f"got {dlg.minimumWidth()}x{dlg.minimumHeight()}")
check("dialog is modal", dlg.isModal())
check("dialog is resizable (no fixed size)",
      dlg.minimumSize() != dlg.maximumSize())
check("working prefs is a COPY of mw.prefs",
      dlg._prefs is not mw.prefs and dlg._prefs == mw.prefs)
check("rows were built for every category",
      len(dlg._rows) > 40 and len(dlg._pages) == len(SettingsDialog3.CATEGORIES))
check("every row has a title and a keywords list",
      all(r.title and isinstance(r.keywords, list) for r in dlg._rows))

# ---------------------------------------------------- 2. category switching
print("== category navigation ==")
for cat, label in SettingsDialog3.CATEGORIES:
    dlg._cat_btns[cat].click()
    pe()
    check(f"button {label!r} switches to its page",
          dlg._stack.currentWidget() is dlg._pages[cat])

# ------------------------------------------------------------- 3. search
print("== search ==")
crossfade_row = find_row(dlg, "crossfade_ms")
gapless_row = find_row(dlg, "gapless")
check("SettingRow.matches is case-insensitive",
      crossfade_row.matches("CROSSFADE") and crossfade_row.matches("fade"))
check("non-matching row does not match",
      not gapless_row.matches("crossfade"))

dlg._search_edit.setText("crossfade")
pe()
check("search switches to the results page",
      dlg._stack.currentWidget() is dlg._search_scroll)
results = [w for w in dlg._search_section.widgets
           if w is not dlg._no_results_row]
check("search 'crossfade' lists matching rows",
      any("crossfade" in r.title.casefold() for r in results),
      f"got {[r.title for r in results]}")
check("matched row is the LIVE row (re-parented, not cloned)",
      crossfade_row in results
      and crossfade_row.parent() is dlg._search_scroll.widget())
check("non-matching rows stay on their home pages",
      gapless_row not in results
      and gapless_row.parent() is dlg._pages["playback"].widget())

dlg._search_edit.setText("zzznope")
pe()
results = [w for w in dlg._search_section.widgets]
check("search 'zzznope' shows the no-results row",
      dlg._no_results_row in results and len(results) == 1)

dlg._search_edit.clear()
pe()
check("clearing the search returns to the last category page",
      dlg._stack.currentWidget() is dlg._pages[dlg._current_category])
check("cleared search restores rows to their home pages",
      crossfade_row.parent() is dlg._pages["playback"].widget()
      and gapless_row.parent() is dlg._pages["playback"].widget())

dlg._search_edit.setText("eq")
pe()
dlg._cat_btns["library"].click()
pe()
check("clicking a category while searching clears the search",
      dlg._search_edit.text() == ""
      and dlg._stack.currentWidget() is dlg._pages["library"])

# ---------------------------------------------------- 4. live prefs editing
print("== live editing ==")
gapless_cb = gapless_row.control
assert isinstance(gapless_cb, QCheckBox)
check("checkbox initial state from prefs", gapless_cb.isChecked() is False)
gapless_cb.setChecked(True)
pe()
check("toggling a checkbox updates the prefs copy",
      dlg._prefs.get("gapless") is True)
check("mw.prefs stays untouched until OK",
      mw.prefs.get("gapless") is False)

regen = find_button(dlg, "Regenerate")
regen.click()
pe()
check("Regenerate mints a fresh token into prefs",
      isinstance(dlg._prefs.get("webremote_token"), str)
      and dlg._prefs["webremote_token"] != "abc123"
      and len(dlg._prefs["webremote_token"]) >= 16)

# ------------------------------------------------------------- 5. cancel
print("== cancel ==")
dlg_c, mw_c = fresh()
find_button(dlg_c, "Cancel").click()
pe()
check("Cancel does not call apply_prefs", mw_c.applied == [])
check("Cancel rejects the dialog",
      dlg_c.result() == QDialog.DialogCode.Rejected)

# ------------------------------------------------------------------ 6. OK
print("== ok / apply ==")
dlg_o, mw_o = fresh()
ok_btn = dlg_o.findChild(QPushButton, "GoldBtn")
check("OK button exists and is #GoldBtn", ok_btn is not None
      and ok_btn.objectName() == "GoldBtn")
find_row(dlg_o, "gapless").control.setChecked(True)
pe()
ok_btn.click()
pe()
check("OK calls apply_prefs exactly once", len(mw_o.applied) == 1)
payload = mw_o.applied[0] if mw_o.applied else {}
check("OK payload carries the edited value", payload.get("gapless") is True)
check("OK payload round-trips unchanged seeded values",
      payload.get("webremote_port") == 8642
      and payload.get("density") == "compact")
check("OK payload has 10 EQ gains", len(payload.get("eq_gains", [])) == 10)
check("OK payload keeps only non-default custom shortcuts",
      payload.get("custom_shortcuts") == {"theme_picker": "Ctrl+Shift+P"})
check("OK payload has no reset flag", RESET_FLAG_KEY not in payload)
check("OK accepts the dialog",
      dlg_o.result() == QDialog.DialogCode.Accepted)

# -------------------------------------------------- 7. shortcut conflicts
print("== shortcut conflicts ==")
dlg_s, _mw_s = fresh()
check("fallback shortcut defs are used when mw lacks shortcut_defs()",
      dlg_s._shortcut_defs == DEFAULT_SHORTCUT_DEFS)
check("seeded custom shortcut is loaded",
      dlg_s._shortcuts.get("theme_picker") == "Ctrl+Shift+P")
check("no conflicts initially", dlg_s._shortcut_conflicts() == {})
check("_set_shortcut rejects unknown action ids",
      dlg_s._set_shortcut("nope", "Ctrl+F9") is False)
dlg_s._set_shortcut("play_pause", "Ctrl+Alt+F9")
dlg_s._set_shortcut("prev", "Ctrl+Alt+F9")
conflicts = dlg_s._shortcut_conflicts()
check("two identical sequences are detected",
      set(conflicts) == {"play_pause", "prev"}
      and conflicts.get("play_pause") == ["prev"]
      and conflicts.get("prev") == ["play_pause"])
item = dlg_s._sc_table.item(0, 1)
check("conflicting cell is highlighted with a tooltip",
      item.text() == "Ctrl+Alt+F9"
      and item.toolTip().startswith("Conflicts with"))
dlg_s._reset_shortcut("prev")
check("resetting one action clears the conflict",
      dlg_s._shortcut_conflicts() == {})
dlg_s._reset_all_shortcuts()
check("reset-all restores every default",
      all(dlg_s._shortcuts[a] == d for a, _t, d in DEFAULT_SHORTCUT_DEFS))

# ------------------------------------------------------ 8. reset defaults
print("== reset defaults ==")
dlg_r, _mw_r = fresh()
find_row(dlg_r, "gapless").control.setChecked(True)
pe()
find_button(dlg_r, "Reset Defaults").click()
pe()
check("reset defaults drops seeded values",
      dlg_r._prefs.get("gapless") in (False, None)
      and dlg_r._prefs.get("webremote_port") in (None, 1024))
check("reset defaults restores slider/combo defaults",
      dlg_r._prefs.get("ui_scale", 1.0) == 1.0
      and dlg_r._prefs.get("density", "normal") == "normal")
check("reset defaults clears custom shortcuts from the prefs copy",
      not dlg_r._prefs.get("custom_shortcuts"))
check("reset defaults is NOT the dangerous factory reset",
      RESET_FLAG_KEY not in dlg_r._prefs)
check("reset defaults re-syncs the UI",
      find_row(dlg_r, "gapless").control.isChecked() is False)

# factory reset (dangerous) with an auto-confirmed QMessageBox
orig_question = s3mod.QMessageBox.question
s3mod.QMessageBox.question = staticmethod(  # type: ignore[assignment]
    lambda *a, **k: s3mod.QMessageBox.StandardButton.Yes)
try:
    find_button(dlg_r, "Reset all settings").click()
    pe()
finally:
    s3mod.QMessageBox.question = orig_question  # type: ignore[assignment]
check("factory reset sets the reset flag payload",
      dlg_r._prefs == {RESET_FLAG_KEY: True})

# ------------------------------------------------------------- 9. EQ
print("== equalizer ==")
dlg_e, mw_e = fresh()
check("EQ sliders initialise from prefs eq_gains",
      dlg_e._eq_sliders[0].value() == 8          # 4.0 dB / 0.5 dB steps
      and dlg_e._eq_sliders[2].value() == -2)    # -1.0 dB
dlg_e._eq_sliders[1].setValue(20)                # +10.0 dB
pe()
check("moving a band switches the preset to Custom",
      dlg_e._eq.preset == "Custom")
dlg_e._eq_sliders[9].setValue(-6)                # -3.0 dB
pe()
dlg_e.findChild(QPushButton, "GoldBtn").click()
pe()
gains = mw_e.applied[0].get("eq_gains", []) if mw_e.applied else []
check("EQ slider values round-trip into prefs eq_gains",
      len(gains) == 10 and gains[1] == 10.0 and gains[9] == -3.0
      and gains[0] == 4.0)
check("EQ preset/enabled round-trip",
      mw_e.applied[0].get("eq_preset") == "Custom"
      and mw_e.applied[0].get("eq_enabled") is True)

dlg_e2, _mw_e2 = fresh()
dlg_e2._eq_reset()
pe()
check("EQ Reset flattens the curve",
      dlg_e2._eq.preset == "Flat"
      and all(g == 0.0 for g in dlg_e2._eq.gains))
dlg_e2._eq_sliders[0].setValue(6)
pe()
dlg_e2._eq_save_custom()
pe()
check("Save-as-Custom keeps the edited curve",
      dlg_e2._eq.preset == "Custom" and dlg_e2._eq.gains[0] == 3.0)

# ------------------------------------------- 10. guarded mw.* interactions
print("== guarded hooks (minimal fake) ==")
dlg_g, mw_g = fresh()
guarded_labels = [
    "Change Theme…", "Choose Output…", "Import M3U…", "Export all playlists",
    "Send test message", "Check now", "Show QR", "GitHub repository",
    "Open Log", "Clear lyrics cache", "Clear waveform cache",
]
crashed = None
try:
    for label in guarded_labels:
        find_button(dlg_g, label).click()
        pe()
    dlg_g.set_theme(Theme.get("obsidian"))
    pe()
    dlg_g._search_edit.setText("remote")
    pe()
    dlg_g._search_edit.clear()
    pe()
except Exception as exc:  # noqa: BLE001 - capture any crash for the report
    crashed = repr(exc)
check(f"clicking all {len(guarded_labels)} guarded buttons never raises",
      crashed is None, f"crashed: {crashed}")
check("guarded calls never touched the fake window's attributes",
      set(vars(mw_g)) == {"prefs", "theme", "applied"}
      and mw_g.applied == [])
check("set_theme re-applies QSS with the prefs ui_scale",
      "QFrame#SettingRow" in dlg_g.styleSheet()
      and dlg_g._theme["name"] == "obsidian")

# ---------------------------------------------------------------- summary
print()
print(f"SETTINGS3 SUMMARY: {PASS} passed, {FAIL} failed")
if FAILURES:
    print("failed checks:")
    for name in FAILURES:
        print(f"  - {name}")
sys.exit(1 if FAIL else 0)
