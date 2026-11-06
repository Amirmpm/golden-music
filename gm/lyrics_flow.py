"""Golden Music 3.0 — the upgraded lyrics panel (orchestrator).

Combines: the bilingual LyricsView, quality/spam detection with the
user-confirmation flow, the lyrics editor, per-track remembered offset,
translation sidecars, and saving back to tags/sidecars — on top of the v2
loader (local/embedded/cache/lrclib) extended with v3 fuzzy search
(gm.lyrics_search).

Policy (user-visible):
  * suspicious existing lyrics (too short / spam) -> warning bar, never
    silently replaced;
  * lyrics found online are staged as a candidate and only saved after
    explicit user confirmation (Replace);
  * manual offset is per-track and remembered (DB + prefs fallback);
  * translations: sidecar <base>.tr.lrc / inline '|||' / [translation:] —
    shown under each line when enabled.
"""
from __future__ import annotations

import logging
import os

from PyQt6.QtCore import QObject, QThread, pyqtSignal, QTimer
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                             QVBoxLayout, QWidget)

from icons import Icon, get_dpr, render_icon
from gm.ui.lyrics_ui import (LyricsView, LyricsQualityBar,
                             LyricsEditorDialog)

log = logging.getLogger("app.lyrics3")


class LyricsSearchWorker(QThread):
    """Runs the v3 aggregator off the UI thread."""
    ready = pyqtSignal(str, object, object)      # (path, Lyrics, candidates)

    def __init__(self, path, title, artist, duration_s, online=True,
                 parent=None):
        super().__init__(parent)
        self._args = (path, title, artist, duration_s, online)

    def run(self):
        path, title, artist, duration_s, online = self._args
        candidates = None
        ly = None
        try:
            from lyrics import get_lyrics, Lyrics
            ly = get_lyrics(path, title, artist, allow_online=online)
            if (ly is None or ly.is_empty()) and online:
                candidates = self._search_candidates(title, artist, duration_s)
        except Exception:
            log.exception("lyrics load failed")
        self.ready.emit(path, ly, candidates)

    @staticmethod
    def _search_candidates(title, artist, duration_s):
        try:
            from gm.lyrics_search import Aggregator, LrclibProvider
            from gm.lyrics_query import clean_title, clean_artist
            agg = Aggregator([LrclibProvider()])
            t = clean_title(title or "")
            a = clean_artist(artist or "")
            if not t:
                return None
            ranked = agg.search_all(t, a, duration_s)
            if ranked:
                return [c for c, score in ranked[:5]]
        except Exception:
            log.exception("candidate search failed")
        return None


def _candidates_to_lyrics(cand: dict):
    from lyrics import Lyrics
    ly = Lyrics()
    ly.lines = list(cand.get("lines") or [])
    ly.synced = bool(cand.get("synced"))
    ly.source = str(cand.get("source") or "online")
    return ly


class LyricsPanel3(QFrame):
    """Floating lyrics side panel (v3)."""

    seek_requested = pyqtSignal(int)            # ms
    closed = pyqtSignal()

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.mw = main_window
        self.setObjectName("LyricsPanel3")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.resize(460, 520)
        self._path = None
        self._ly = None
        self._candidates = None
        self._offset_ms = 0
        self._worker = None
        self._build()
        self.set_theme(self.mw.theme)

    # ------------------------------------------------------------------ UI
    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)
        head = QHBoxLayout()
        self.source_label = QLabel("")
        self.source_label.setObjectName("CountLabel")
        head.addWidget(self.source_label, 1)
        self.offset_btn = QPushButton("Offset")
        self.offset_btn.setObjectName("GhostBtn")
        self.offset_btn.setToolTip("Manual timing offset for this track")
        self.offset_btn.clicked.connect(self._adjust_offset)
        head.addWidget(self.offset_btn)
        self.edit_btn = QPushButton("Edit")
        self.edit_btn.setObjectName("GhostBtn")
        self.edit_btn.clicked.connect(self._edit_lyrics)
        head.addWidget(self.edit_btn)
        self.search_btn = QPushButton("Find")
        self.search_btn.setObjectName("GhostBtn")
        self.search_btn.setToolTip("Search for better lyrics online")
        self.search_btn.clicked.connect(self._search_better)
        head.addWidget(self.search_btn)
        self.close_btn = QPushButton()
        self.close_btn.setFlat(True)
        self.close_btn.setFixedSize(26, 26)
        self.close_btn.clicked.connect(self._close)
        head.addWidget(self.close_btn)
        lay.addLayout(head)

        self.quality_bar = LyricsQualityBar()
        self.quality_bar.search_requested.connect(self._search_better)
        self.quality_bar.keep_requested.connect(self._keep_current)
        self.quality_bar.replace_requested.connect(self._replace_with_candidate)
        lay.addWidget(self.quality_bar)

        self.view = LyricsView()
        self.view.line_activated.connect(self._on_line_activated)
        lay.addWidget(self.view, 1)

    def set_theme(self, t):
        try:
            self.setStyleSheet(
                f"QFrame#LyricsPanel3 {{ background: {t['panel_bg']};"
                f" border: 1px solid {t['border']}; border-radius: 12px; }}")
            self.view.set_theme(t)
            self.quality_bar.set_theme(t)
            self.close_btn.setIcon(render_icon(Icon.CLOSE, 14, t["muted"],
                                               get_dpr()))
            self.view.set_font_scale(float(
                self.mw.prefs.get("lyrics_font_scale", 1.0)))
        except Exception:
            pass

    # ------------------------------------------------------------- loading
    def load_for_track(self, path, allow_online=True):
        self._path = path
        self._ly = None
        self._candidates = None
        self.quality_bar.hide()
        self.view.clear()
        self.source_label.setText("Loading lyrics…")
        if not path or str(path).startswith(("http://", "https://")):
            self.source_label.setText("Radio stream — no lyrics")
            return
        title, artist = self.mw._tag_cache.get(
            path, (os.path.splitext(os.path.basename(path))[0], ""))
        dur_s = 0
        try:
            d = self.mw.audio.duration()
            dur_s = int(d / 1000) if d else 0
        except Exception:
            dur_s = 0
        # remembered offset
        self._offset_ms = 0
        try:
            if getattr(self.mw, "store", None) is not None:
                self._offset_ms = self.mw.store.db.lyrics_offset(path)
            else:
                off = self.mw.prefs.get("lyrics_offset", {})
                self._offset_ms = int(off.get(path, 0) or 0)
        except Exception:
            self._offset_ms = 0
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(500)
        online = allow_online and bool(self.mw.prefs.get("lyrics_online", True))
        self._worker = LyricsSearchWorker(path, title, artist, dur_s,
                                          online, self)
        self._worker.ready.connect(self._on_ready)
        self._worker.start()

    def _on_ready(self, path, ly, candidates):
        if path != self._path:
            return
        self._ly = ly
        self._candidates = candidates
        show_quality = bool(self.mw.prefs.get("lyrics_quality_check", True))
        if ly is not None and not ly.is_empty():
            from gm.lyrics_io import apply_offset
            lines = apply_offset(list(ly.lines), self._offset_ms)
            trans = self._load_translations(self._path)
            self.view.set_lines(lines, trans, ly.source)
            self.source_label.setText(
                f"{ly.source} · {'synced' if ly.synced else 'plain'}"
                + (f" · offset {self._offset_ms:+d} ms" if self._offset_ms
                   else ""))
            if show_quality:
                text = "\n".join(t or "" for _ms, t in ly.lines)
                from gm.lyrics_quality import verdict
                v = verdict(text)
                if not v["valid"]:
                    self.quality_bar.show_warning(text, None)
        elif candidates:
            self.source_label.setText("No stored lyrics — candidates found")
            self.quality_bar.show_warning(None,
                                          f"{len(candidates)} online match(es)"
                                          f" — best: "
                                          f"{candidates[0].get('title', '')}")
        else:
            self.source_label.setText("No lyrics found")

    def _load_translations(self, path):
        try:
            from gm.lyrics_translate import (parse_translation_sidecar,
                                             merge_translations,
                                             extract_inline_translation)
            base = os.path.splitext(path)[0]
            for suffix in (".tr.lrc", ".trans.lrc", ".lrc.tr"):
                side = base + suffix if not base.endswith(".lrc") else None
                if side and os.path.isfile(side):
                    trans = parse_translation_sidecar(side)
                    if trans:
                        orig = list((self._ly.lines if self._ly else []))
                        return merge_translations(orig, trans)
            # inline '|||' style inside the lyrics themselves
            if self._ly is not None:
                text = "\n".join(t or "" for _ms, t in self._ly.lines)
                inline = extract_inline_translation(text)
                if inline:
                    return inline
        except Exception:
            log.exception("translation load failed")
        return None

    # ------------------------------------------------------------ controls
    def sync_position(self, ms):
        try:
            pos = int(ms) + self._offset_ms
            self.view.sync_position(pos)
        except Exception:
            pass

    def _on_line_activated(self, ms):
        self.seek_requested.emit(max(0, int(ms) - self._offset_ms))

    def _adjust_offset(self):
        from PyQt6.QtWidgets import QInputDialog
        cur = self._offset_ms
        val, ok = QInputDialog.getInt(self, "Lyrics offset",
                                      "Shift lyrics timing by (ms):\n"
                                      "positive = later, negative = earlier",
                                      cur, -10000, 10000, 100)
        if not ok:
            return
        self._offset_ms = int(val)
        try:
            if getattr(self.mw, "store", None) is not None:
                self.mw.store.db.set_lyrics_offset(self._path, val)
            else:
                off = self.mw.prefs.setdefault("lyrics_offset", {})
                off[self._path] = val
        except Exception:
            pass
        if self._ly is not None:
            from gm.lyrics_io import apply_offset
            trans = self._load_translations(self._path)
            self.view.set_lines(apply_offset(list(self._ly.lines),
                                             self._offset_ms),
                                trans, self._ly.source)
            self.source_label.setText(
                f"{self._ly.source} · "
                f"{'synced' if self._ly.synced else 'plain'}"
                f" · offset {self._offset_ms:+d} ms")

    def _edit_lyrics(self):
        if self._ly is None:
            self._ly = _candidates_to_lyrics(
                {"lines": [], "synced": False, "source": "new"})
        try:
            dlg = LyricsEditorDialog(list(self._ly.lines), self._ly.synced,
                                     self.mw.theme, parent=self)
            if dlg.exec():
                self._ly.lines = dlg.result_lines()
                self._ly.synced = any(t is not None for t, _x
                                      in [(l[0], l[1]) for l in self._ly.lines])
                self._save_lyrics(dlg.save_mode())
                self.view.set_lines(list(self._ly.lines),
                                    self._load_translations(self._path),
                                    "edited")
                self.source_label.setText("edited · "
                                          + ("synced" if self._ly.synced
                                             else "plain"))
        except Exception:
            log.exception("lyrics editor failed")

    def _search_better(self):
        if self._path:
            self.load_for_track(self._path, allow_online=True)

    def _keep_current(self):
        self.quality_bar.hide()

    def _replace_with_candidate(self):
        if not self._candidates:
            return
        cand = self._candidates[0]
        self._ly = _candidates_to_lyrics(cand)
        mode = str(self.mw.prefs.get("lyrics_save_mode", "ask") or "ask")
        if mode == "ask":
            from PyQt6.QtWidgets import QMessageBox
            ret = QMessageBox.question(
                self, "Save lyrics",
                "Save these lyrics to the song?\n"
                "Yes = embed in file tag · No = save .lrc sidecar · "
                "Cancel = keep in app only",
                QMessageBox.StandardButton.Yes |
                QMessageBox.StandardButton.No |
                QMessageBox.StandardButton.Cancel)
            if ret == QMessageBox.StandardButton.Yes:
                mode = "embed"
            elif ret == QMessageBox.StandardButton.No:
                mode = "sidecar"
            else:
                mode = "none"
        self._save_lyrics(mode)
        trans = self._load_translations(self._path)
        self.view.set_lines(list(self._ly.lines), trans, self._ly.source)
        self.source_label.setText(f"{self._ly.source} · replaced")
        self.quality_bar.hide()

    def _save_lyrics(self, mode: str):
        if self._ly is None or not self._path:
            return
        try:
            if mode == "embed":
                from gm.lyrics_io import save_embedded
                save_embedded(self._path, list(self._ly.lines),
                              self._ly.synced)
            elif mode == "sidecar":
                from gm.lyrics_io import save_to_sidecar
                save_to_sidecar(self._path, list(self._ly.lines),
                                self._ly.synced, overwrite=False)
        except Exception as e:
            log.warning("lyrics save failed: %s", e)
            try:
                self.mw._show_toast("Could not write lyrics to file "
                                    "(read-only?)")
            except Exception:
                pass

    def _close(self):
        self.hide()
        self.closed.emit()
