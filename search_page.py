"""Golden Music v3 — Search page: ONLINE music search (Nuvx Desktop layout).

The Nuvx search experience adapted to Golden Music's provider architecture:

  * 42 px pill search field, search glyph, clear button, "Search songs,
    albums, artists" hint
  * search history with suggestions (persisted; validated by online.py)
  * results as rows: artwork, title (semibold 15), artist (regular 13),
    source, duration; loading / error / empty states
  * every network call runs on a QThread (OnlineSearchWorker) with
    cancellation, timeout and a TTL result cache
  * actions: Play (stream), Play next, Add to queue, Download (only when
    the provider permits downloads)

Home = local library; Search = online (PART 4.3 of the spec).
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                              QPushButton, QLineEdit, QListWidget,
                              QListWidgetItem, QAbstractItemView, QMenu,
                              QSizePolicy, QProgressBar)
from PyQt6.QtGui import QPixmap

import online as online_mod
from icons import Icon, render_icon

RESULT_ROLE = Qt.ItemDataRole.UserRole + 1


# ---------------------------------------------------------------------------
# Search worker (network off the GUI thread)
# ---------------------------------------------------------------------------

class OnlineSearchWorker(QThread):
    """Runs a provider search off-thread. Carries its generation so a
    stale worker's results can be rejected at delivery time; cancelled
    workers emit nothing."""
    results_ready = pyqtSignal(list)
    error_occurred = pyqtSignal(str)

    def __init__(self, provider, query: str, generation: int = 0,
                 parent=None):
        super().__init__(parent)
        self.provider = provider
        self.query = query
        self.generation = int(generation)
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            tracks = self.provider.search(self.query, timeout=10.0)
            if self._cancelled:
                return
            self.results_ready.emit(tracks)
        except Exception as e:
            if not self._cancelled:
                self.error_occurred.emit(str(e))


class StreamResolveWorker(QThread):
    """Resolves an online track's stream URL off-thread."""
    resolved = pyqtSignal(object)
    failed = pyqtSignal(object, str)

    def __init__(self, provider, track, parent=None):
        super().__init__(parent)
        self.provider = provider
        self.track = track
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            t = self.provider.resolve_stream(self.track)
            if self._cancelled:
                return
            self.resolved.emit(t)
        except Exception as e:
            if not self._cancelled:
                self.failed.emit(self.track, str(e))


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

class SearchPage(QWidget):
    """Online search UI. Emits intents; the MainWindow decides actions."""

    search_triggered = pyqtSignal(str)                    # query
    play_track_requested = pyqtSignal(object)            # OnlineTrack
    play_next_requested = pyqtSignal(object)
    add_queue_requested = pyqtSignal(object)
    download_track_requested = pyqtSignal(object)         # OnlineTrack

    def __init__(self, theme: dict, provider=None, parent=None):
        super().__init__(parent)
        self.theme = dict(theme or {})
        self.provider = provider or online_mod.provider_by_name(
            "internetarchive")
        self.history: list = []
        self._worker = None
        self._zombies: list = []        # cancelled workers, kept alive until finished
        self._search_generation = 0     # request-id guard against stale results
        self._resolve_worker = None
        self._cache = online_mod.SearchCache()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.setSpacing(12)

        self.title = QLabel("Search")
        lay.addWidget(self.title)

        # search pill
        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.field = QLineEdit()
        self.field.setPlaceholderText("Search songs, albums, artists…")
        self.field.setFixedHeight(42)
        self.field.setClearButtonEnabled(True)
        self.field.returnPressed.connect(self._submit)
        bar.addWidget(self.field, 2)
        self.go_btn = QPushButton("Search")
        self.go_btn.setFixedHeight(38)
        self.go_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.go_btn.clicked.connect(self._submit)
        bar.addWidget(self.go_btn, 0)
        lay.addLayout(bar)

        # suggestions row (history-driven, instant, offline)
        self.suggestion_label = QLabel("")
        self.suggestion_label.setWordWrap(True)
        lay.addWidget(self.suggestion_label)
        self.field.textChanged.connect(self._on_text)

        # status line (loading / error / empty / count)
        self.status = QLabel("")
        lay.addWidget(self.status)

        # results
        self.results = QListWidget()
        self.results.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.results.setAlternatingRowColors(False)
        self.results.itemDoubleClicked.connect(self._on_activate_row)
        self.results.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.results.customContextMenuRequested.connect(self._on_row_menu)
        lay.addWidget(self.results, 1)

        self.set_theme(self.theme)

    # ------------------------------------------------------------------
    def set_theme(self, theme: dict):
        self.theme = dict(theme or {})
        t = self.theme
        accent = t.get("gold", "#e9b957")
        text = t.get("text", "#f2f0f5")
        muted = t.get("muted", "#b2acba")
        border = t.get("border", "rgba(255,255,255,0.14)")
        fill = t.get("glass_fill", "rgba(255,255,255,0.08)")
        radius = t.get("lg", {}).get("radius_pill", 19)
        self.title.setStyleSheet(
            f"color: {text}; font-size: 34px; font-weight: 700; "
            f"background: transparent;")
        self.field.setStyleSheet(f"""
            QLineEdit {{
                background: {fill};
                color: {text};
                border: 1px solid {border};
                border-radius: {radius}px;
                padding: 0 18px;
                font-size: 15px;
            }}
            QLineEdit:focus {{ border: 1px solid {accent}; }}
        """)
        self.go_btn.setStyleSheet(f"""
            QPushButton {{
                background: {accent};
                color: {'#111' if t.get('is_dark', True) else '#fff'};
                border: none; border-radius: 19px; padding: 0 22px;
                font-weight: 600; font-size: 14px;
            }}
        """)
        self.status.setStyleSheet(
            f"color: {muted}; font-size: 13px; background: transparent;")
        self.suggestion_label.setStyleSheet(
            f"color: {muted}; font-size: 12px; background: transparent;")
        self.results.setStyleSheet(f"""
            QListWidget {{
                background: transparent; border: none; outline: 0;
            }}
            QListWidget::item {{
                background: {fill};
                border: 1px solid {border};
                border-radius: 8px;
                margin: 3px 0;
                padding: 8px 12px;
            }}
            QListWidget::item:selected {{
                border: 1px solid {accent};
            }}
        """)

    def set_history(self, history: list):
        self.history = online_mod.sanitize_history(history)
        self._render_suggestions(self.field.text())

    def cancel_search(self):
        """Shutdown hook: cancel the in-flight search (stale emissions are
        suppressed by the worker's own flag)."""
        if self._worker is not None:
            self._worker.cancel()
        for w in list(self._zombies):
            try:
                w.cancel()
            except Exception:
                pass

    # ------------------------------------------------------------------
    def _on_text(self, text: str):
        self._render_suggestions(text)

    def _render_suggestions(self, text: str):
        sug = online_mod.suggest_completions(text, self.history)
        if sug:
            self.suggestion_label.setText("Try:  " + "   ·   ".join(sug))
        else:
            self.suggestion_label.setText("")

    def _submit(self):
        q = self.field.text().strip()
        if not q:
            return
        self._run_search(q)

    def run_search(self, query: str):
        self.field.setText(query)
        self._run_search(query)

    def _retire_worker(self, w):
        """Cancel a superseded worker and keep a Python reference until its
        thread finishes (a running QThread must not be garbage-collected)."""
        if w is None:
            return
        try:
            w.cancel()
        except Exception:
            pass
        self._zombies.append(w)
        w.finished.connect(self._on_zombie_finished)

    def _on_zombie_finished(self):
        w = self.sender()
        if w is not None and w in self._zombies:
            self._zombies.remove(w)
        if w is not None:
            w.deleteLater()

    def _run_search(self, query: str):
        cached = self._cache.get(query, self.provider.name)
        if cached is not None:
            self._show_results(cached)
            self.search_triggered.emit(query)
            return
        # Supersede any in-flight search WITHOUT blocking the GUI thread
        # (v3.0.0 waited up to 1.5 s here). The cancelled worker checks its
        # flag before emitting, and _on_results rejects anything stale.
        self._retire_worker(self._worker)
        self._search_generation += 1
        gen = self._search_generation
        self.status.setText(f"Searching “{query}” …")
        self.results.clear()
        w = OnlineSearchWorker(self.provider, query, generation=gen)
        self._worker = w
        w.results_ready.connect(
            lambda tracks, w=w: self._on_results(tracks, w))
        w.error_occurred.connect(
            lambda msg, w=w: self._on_error(msg, w))
        w.start()
        self.search_triggered.emit(query)

    def _on_results(self, tracks: list, worker):
        # Stale-result rejection: only the CURRENT generation's worker may
        # render — and cache under the WORKER's query (v3.0.0 cached under
        # the live field text, polluting the cache with wrong-key results).
        if worker is not self._worker or \
                worker.generation != self._search_generation:
            return
        self._cache.put(worker.query, self.provider.name, tracks)
        self._show_results(tracks)

    def _show_results(self, tracks: list):
        self.status.setText(
            f"{len(tracks)} result"
            + ("s" if len(tracks) != 1 else "")
            + "  ·  source: " + self.provider.label)
        self.results.clear()
        for t in tracks:
            text = (f"{t.title}  —  {t.artist}\n"
                    f"{t.album}"
                    + (f"  ·  {t.duration_ms // 60000}:"
                       f"{(t.duration_ms % 60000) // 1000:02d}"
                       if t.duration_ms else ""))
            it = QListWidgetItem(text)
            it.setData(RESULT_ROLE, t)
            self.results.addItem(it)

    def _on_error(self, msg: str, worker=None):
        if worker is not None and \
                (worker is not self._worker or
                 worker.generation != self._search_generation):
            return          # stale error from a superseded search
        self.status.setText(f"Search failed: {msg}")
        self.results.clear()

    # ------------------------------------------------------------------
    def _row_track(self, item: QListWidgetItem):
        return item.data(RESULT_ROLE)

    def _on_activate_row(self, item):
        t = self._row_track(item)
        if t is not None:
            self.play_track_requested.emit(t)

    def _on_row_menu(self, pos):
        item = self.results.itemAt(pos)
        if item is None:
            return
        t = self._row_track(item)
        if t is None:
            return
        m = QMenu(self)
        m.addAction("Play (stream)", lambda: self.play_track_requested.emit(t))
        m.addAction("Play next", lambda: self.play_next_requested.emit(t))
        m.addAction("Add to queue", lambda: self.add_queue_requested.emit(t))
        # Download eligibility is item-specific and established at resolve
        # time (online.py assesses the source's own access metadata); the
        # action stays available and the manager refuses restricted items
        # with a clear message (v3.0.0 hard-marked everything downloadable).
        if getattr(self.provider, "supports_download", False):
            if getattr(t, "downloads_allowed", None) is False:
                m.addAction("Download (restricted on source)",
                            lambda: self.download_track_requested.emit(t))
            else:
                m.addAction("Download", lambda: self.download_track_requested.emit(t))
        m.exec(self.results.mapToGlobal(pos))
