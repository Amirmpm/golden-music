"""
Golden Music v3.0 — split out of the former main.pyw monolith.
"""
import sys
import os

if sys.platform == 'win32':
    os.environ.setdefault('QT_MEDIA_BACKEND', 'windowsmediafoundation')

import json
import logging
import math
import random
import subprocess
import time
from pathlib import Path
from enum import Enum

from PyQt6.QtCore import (
    Qt, QSize, QRect, QRectF, QPointF, QTimer, QEvent, pyqtSignal, QUrl,
    QObject, QThread, QPropertyAnimation, QVariantAnimation, QPoint,
    QEasingCurve
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton,
    QVBoxLayout, QHBoxLayout, QGridLayout, QFrame, QSizePolicy,
    QSlider, QListWidget, QListWidgetItem, QStackedWidget,
    QSystemTrayIcon, QMenu, QFileDialog, QMessageBox,
    QStyle, QStyleFactory, QAbstractItemView,
    QToolButton, QButtonGroup, QProgressBar, QLineEdit, QComboBox,
    QSplitter, QTreeWidget, QTreeWidgetItem, QTextEdit, QPlainTextEdit,
    QGraphicsOpacityEffect
)
from PyQt6.QtGui import (
    QIcon, QPixmap, QPainter, QColor, QFont, QPalette, QAction,
    QLinearGradient, QBrush, QRadialGradient, QFontDatabase,
    QMouseEvent, QKeyEvent, QPaintEvent, QPen, QPainterPath,
    QShortcut, QKeySequence, QFontMetrics
)
from PyQt6.QtWidgets import QStyledItemDelegate

from config import (
    APP_NAME, APP_VERSION, ORG_NAME, AUDIO_EXTS, config_path,
    Theme, build_qss, fmt_time, scan_folder, extract_title_artist,
    HAVE_QT_AUDIO, load_json_file, load_tag_cache_file,
    restrict_file_permissions, path_within
)
from icons import Icon, render_icon, make_icon, get_dpr

from audio import AudioBackend
from scanner import FolderScanner
from settings_dialog import SettingsDialog
from mini_player import MiniPlayer
from widgets import ClickableSlider
from tray_menu import TrayMenuWidget
from theme_picker import ThemePickerPopup
import coverart
import ipc
import updater
from update_dialog import UpdateCheckWorker, UpdateBanner, UpdateDialog
from mediakeys import MediaKeyFilter

try:
    from autottheme import windows_prefers_dark as _os_prefers_dark
except ImportError:
    def _os_prefers_dark():
        return True

from gm.core import (
    RepeatMode, View, SortMode, _is_scratch_path, _track_item_text,
    _list_subdirs, _dir_has_subdirs, _rgba, log,
)
from gm.ui.widgets import (
    IconButton, CoverLabel, MarqueeLabel, LoadingOverlay, _SpinnerCanvas,
)
from gm.ui.delegates import TrackRowDelegate
from gm.ui.player_bar import PlayerBar
from gm.ui.rail import SideRail  # v2 rail kept for fallback
from gm.ui.sidebar import Sidebar
from gm import i18n
from gm.prefs import DEFAULT_PREFS, merge_prefs
from gm.ui.stats_widgets import StatsChartBar, StatTrackRow
from gm.workers import (
    CoverLoaderThread, TagLoaderThread, AlbumsWorkerThread, StoreInitWorker,
)
from gm.ui.pages3 import (
    AlbumsPage, ArtistsPage, GenresPage, PlaylistsPage3, LibraryTable,
)
from gm.ui.stats3 import StatsPage3
from gm.ui.command_palette import CommandPalette


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1000, 700)
        self.setMinimumSize(860, 620)
        # Accept folder/file drops from the OS file manager
        self.setAcceptDrops(True)
        # Per-user single-instance IPC server ("Open with" forwards + show).
        # Started here so it exists before the window is even visible.
        self._ipc_server = ipc.start_server(
            self.open_external_files, self._reveal_window)

        # State
        self.theme_name = Theme.DEFAULT_NAME
        self.theme = Theme.get(self.theme_name)
        self.library = []
        self.favorites = set()
        self.added_folders = []
        self.playlists = {}   # {name: [track paths]} — persisted in config
        self.current_view = View.LIBRARY
        # v3.0: SQLite store (created off-thread in _load_config_async)
        self.store = None
        self._store_worker = None
        self._pending_cfg = None
        # v3.0: user preferences dict (validated overlay in config.json)
        self.prefs = dict(DEFAULT_PREFS)
        self.current_track = None
        self.current_index = -1
        self.current_playlist = []
        # Playback history for shuffle-correct Previous/Next navigation.
        # history holds tracks in the order they STARTED playing; redo holds
        # tracks "taken back" with Previous so Next can replay them forward.
        # Both store paths (not indexes) so library edits can't corrupt them.
        self.play_history = []    # [..., previous_track]
        self.redo_stack = []      # [next_track_after_redo, ...]
        self._nav_via_history = False  # True while stepping back through history
        # Smart shuffle: paths in the current playlist that have NOT played
        # yet this session. Shuffle picks from here first so every track
        # gets a turn before repeats; refilled when the playlist changes.
        self.shuffle_unplayed = set()
        self.shuffle = False
        self.repeat_mode = RepeatMode.OFF
        self.user_is_seeking = False
        self.sort_mode = SortMode.TITLE
        self.search_filter = ""
        # Folder filter from the folder tree — persisted while the app runs
        # so search/sort changes don't silently reset it (FUNC-6).
        self._folder_filter = None
        # Search typing debounce timer (created lazily in _on_search_changed)
        self._search_debounce = None

        # Settings
        self.auto_rescan = True
        self.remember_track = True
        # Taskbar click on the app icon minimizes by default; when this is
        # enabled the window hides to the tray instead of minimizing.
        self.taskbar_close_to_tray = False
        self.default_folder = ""
        self.sleep_timer_active = False
        self.sleep_timer_minutes = 30
        self._sleep_timer = None
        # Fullscreen visualizer (animated background) — persisted, toggled
        # in Settings → Interface and on the fullscreen top bar itself.
        self.visualizer_enabled = True

        # Audio
        self.audio = AudioBackend(self)
        self.audio.position_changed.connect(self._on_position_changed)
        self.audio.duration_changed.connect(self._on_duration_changed)
        self.audio.state_changed.connect(self._on_state_changed)
        self.audio.error_occurred.connect(self._on_audio_error)
        self.audio.track_finished.connect(self._on_track_finished)

        # v3: engine-backed player adapter (mpv preferred, Qt fallback;
        # degrades to the legacy AudioBackend when neither engine loads).
        try:
            from gm.player_adapter import PlayerAdapter
            order = ("mpv", "qt")
            if self.prefs.get("audio_engine") == "qt":
                order = ("qt",)
            self.audio = PlayerAdapter(self, engine_order=order)
            self.audio_v3 = self.audio
        except Exception:
            log.exception("PlayerAdapter failed; legacy AudioBackend")
            self.audio = AudioBackend(self)
            self.audio_v3 = None
        self.audio.set_next_provider(self._peek_next_path)
        self.audio.swapped_to.connect(self._on_gapless_swap)

        # Playback effects: speed / A-B repeat / fade
        from playback_fx import PlaybackFX
        self.fx = PlaybackFX(self.audio)

        # Play statistics (user data — survives updates)
        try:
            from playstats import PlayStats, set_search_roots
            self.stats = PlayStats(config_path().parent / "stats.json")
            # Moved-file resolution needs to know where music lives; the
            # full folder set arrives with the async config load, so seed
            # with what's known now and refresh after config + every scan.
            set_search_roots(list(getattr(self, "added_folders", []) or []))
            self._stats_timer = QTimer(self)
            self._stats_timer.setInterval(30_000)   # flush every 30 s
            self._stats_timer.timeout.connect(self._flush_stats)
            self._stats_timer.start()
            # Refresh the visible stats page once a minute while it's shown
            self._stats_view_timer = QTimer(self)
            self._stats_view_timer.setInterval(60_000)
            self._stats_view_timer.timeout.connect(
                lambda: (self.stack.currentIndex() == 3) and self._refresh_stats_view())
            self._stats_view_timer.start()
        except Exception as e:
            log.warning(f"stats init failed: {e}")
            self.stats = None

        # Track-change toast (window hidden only)
        self._toast_enabled = True
        # Per-track listening meter (70% credit rule for stats)
        self._listened_ms = 0
        self._last_toast_key = None

        # Auto-update state (persisted; see _save_config / _load_config_async)
        self.updates_enabled = True
        self.update_last_check = 0.0
        self.update_etag = ""
        self.update_skip_version = ""
        self.update_snooze_until = 0.0
        self._update_worker = None
        self._last_update_result = None
        self._pending_install_path = None
        self._updated_flag = False

        # Threads
        self.scanner = None
        self._scan_results_pending = None
        self._last_scan_folders = None
        self._cover_loader = None
        self._albums_worker = None

        # Mini player
        self.mini_player = None

        # Read the saved theme EARLY — BEFORE building anything visible.
        # (Tiny local JSON read.)  Order matters: the tray popup is built
        # in _build_tray() with self.theme, so it must come after this or
        # the tray menu would bake DEFAULT (royal_gold) colors on every
        # boot into a different theme.
        self._early_load_theme()

        # Tray
        self.tray = None
        self._build_tray()

        # UI
        self._build_ui()
        self._apply_theme()
        # Defer config loading to after window is shown (instant launch)
        self._force_quit = False
        self._last_volume = 80

        self.ui_timer = QTimer(self)
        self.ui_timer.setInterval(500)
        self.ui_timer.timeout.connect(self._ui_tick)
        self.ui_timer.start()
        # Faster tick for A-B repeat precision (100 ms)
        self._fx_timer = QTimer(self)
        self._fx_timer.setInterval(100)
        self._fx_timer.timeout.connect(self._on_fx_tick)
        self._fx_timer.start()

        # Keyboard shortcuts
        self._setup_shortcuts()

        # Global media keys (keyboard/headset) — Windows only, best effort
        self._media_key_filter = None
        try:
            app_inst = QApplication.instance()
            if app_inst is not None and sys.platform == "win32":
                mk = MediaKeyFilter()
                if mk.install():
                    app_inst.installNativeEventFilter(mk)
                    mk.play_pause.connect(self._on_play_pause)
                    mk.stop.connect(lambda: (self.audio.stop(),
                                             self.player_bar.update_play_button(False)))
                    mk.next.connect(self._on_next)
                    mk.prev.connect(self._on_prev)
                    self._media_key_filter = mk
        except Exception as e:
            log.warning(f"Media key setup failed: {e}")

        # Load config AFTER window is shown (async, non-blocking)
        # User-data safety net: snapshot + auto-restore BEFORE touching config
        try:
            import userbackup
            data_dir = config_path().parent
            userbackup.restore_missing(data_dir)
            userbackup.create_startup_backup(data_dir)
        except Exception as e:
            log.warning(f"backup bootstrap failed: {e}")

        QTimer.singleShot(50, self._load_config_async)

        # Auto theme: follow the OS dark/light preference when enabled
        self.auto_theme_enabled = False
        try:
            from autottheme import AutoThemeWatcher
            self._auto_theme_watcher = AutoThemeWatcher(self)
            self._auto_theme_watcher.changed.connect(self._on_os_theme_changed)
        except Exception as e:
            log.warning(f"auto-theme watcher unavailable: {e}")
            self._auto_theme_watcher = None

    def _on_fx_tick(self):
        """100 ms tick: A-B repeat precision + crossfade ramp advancement."""
        try:
            if getattr(self, "fx", None):
                self.fx.tick()
        except Exception:
            pass
        try:
            if getattr(self, "audio_v3", None) is not None:
                self.audio_v3.tick()
        except Exception:
            pass

    # Dark and light theme names used by "follow Windows" mode.
    AUTO_DARK_THEME = Theme.AUTO_DARK_DEFAULT
    AUTO_LIGHT_THEME = Theme.AUTO_LIGHT_DEFAULT

    def _apply_auto_theme_setting(self):
        if getattr(self, "_auto_theme_watcher", None) is None:
            return
        if self.auto_theme_enabled:
            self._auto_theme_watcher.start()
            self._on_os_theme_changed(_os_prefers_dark())
        else:
            self._auto_theme_watcher.stop()

    def _on_os_theme_changed(self, prefer_dark: bool):
        if not self.auto_theme_enabled:
            return
        target = self.AUTO_DARK_THEME if prefer_dark else self.AUTO_LIGHT_THEME
        if target != self.theme_name:
            self.theme_name = target
            self._apply_theme()
            self._save_config()

    def open_external_files(self, paths, reveal: bool = True) -> bool:
        """Play files handed over by the OS ("Open with", double-click,
        second-instance IPC) as an ad-hoc queue.

        The queue lives only in current_playlist — this method never
        mutates self.library / added_folders / favorites / playlists and
        never writes the config (never-wipe guards): a one-off external
        file must not leak into the persisted library.
        Returns True when at least one valid file was queued.
        """
        files = ipc.sanitize_paths(paths if isinstance(paths, list) else [])
        if not files:
            if reveal:
                # Nothing playable — still surface the running instance.
                self._reveal_window()
            return False
        log.info(f"External open: queuing {len(files)} file(s)")
        self._play_from_list(files, 0)
        if reveal:
            self._reveal_window()   # user just asked for this file by name
        return True

    def _build_ui(self):
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)

        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Scan progress bar (at very top)
        self.scan_progress = QProgressBar()
        self.scan_progress.setFixedHeight(4)
        self.scan_progress.setTextVisible(False)
        self.scan_progress.setRange(0, 100)
        self.scan_progress.setValue(0)
        self.scan_progress.setVisible(False)
        outer.addWidget(self.scan_progress)

        # Top area
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(0)

        self.rail = Sidebar(self.theme)
        self.rail.nav_requested.connect(self._on_rail_clicked)
        self.sidebar = self.rail            # v3 name
        top.addWidget(self.rail)

        # Loading overlay (covers the whole window during slow operations)
        self.loading_overlay = LoadingOverlay(self)

        self.main_area = QWidget()
        self.main_area.setObjectName("MainArea")
        self.main_layout = QVBoxLayout(self.main_area)
        self.main_layout.setContentsMargins(28, 20, 28, 14)
        self.main_layout.setSpacing(10)

        # Top row: title + search + sort + buttons
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(8)

        self.page_title = QLabel("Library")
        self.page_title.setObjectName("PageTitle")
        top_row.addWidget(self.page_title)
        top_row.addSpacing(12)

        # Search bar
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search tracks...")
        self.search_edit.setFixedWidth(200)
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._on_search_changed)
        top_row.addWidget(self.search_edit)

        # Sort dropdown
        self.sort_combo = QComboBox()
        self.sort_combo.addItem("Sort: Title", SortMode.TITLE.value)
        self.sort_combo.addItem("Sort: Artist", SortMode.ARTIST.value)
        self.sort_combo.addItem("Sort: Date Added", SortMode.DATE_ADDED.value)
        self.sort_combo.addItem("Sort: Filename", SortMode.FILENAME.value)
        self.sort_combo.setFixedWidth(140)
        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        top_row.addWidget(self.sort_combo)

        top_row.addStretch(1)

        self.scan_status_label = QLabel("")
        self.scan_status_label.setObjectName("CountLabel")
        self.scan_status_label.setStyleSheet(f"color: {self.theme['gold']};")
        self.scan_status_label.setVisible(False)
        top_row.addWidget(self.scan_status_label)
        top_row.addSpacing(8)

        self.count_label = QLabel("")
        self.count_label.setObjectName("CountLabel")
        top_row.addWidget(self.count_label)

        top_row.addStretch(1)

        # Action group (kept together at the right edge)
        self.shuffle_all_btn = QPushButton("Shuffle All")
        self.shuffle_all_btn.setObjectName("GhostBtn")
        self.shuffle_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.shuffle_all_btn.clicked.connect(self._on_shuffle_all)
        top_row.addWidget(self.shuffle_all_btn)

        self.play_all_btn = QPushButton("Play All")
        self.play_all_btn.setObjectName("GoldBtn")
        self.play_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_all_btn.clicked.connect(self._on_play_all)
        top_row.addWidget(self.play_all_btn)

        top_row_w = QWidget()
        top_row_w.setLayout(top_row)
        self.main_layout.addWidget(top_row_w)

        # Stack
        self.stack = QStackedWidget()
        self.stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.stack.addWidget(self._build_library_page())      # 0 library
        self.stack.addWidget(self._build_favorites_page())    # 1 favorites
        self.albums_page = AlbumsPage(self)                   # 2 albums
        self.stack.addWidget(self.albums_page)
        self.stats_page = StatsPage3(self)                    # 3 stats
        self.stack.addWidget(self.stats_page)
        try:
            self.stats_page.play_requested.connect(
                lambda pth: self._play_path_standalone(pth))
            self.stats_page.favorite_toggled.connect(
                lambda pth, on: self._stats_toggle_favorite(pth, on))
            _ac = getattr(self.stats_page, "artist_chart", None)
            if _ac is not None:
                _ac.row_clicked.connect(
                    lambda row: self._play_artist_from_stats(row))
        except Exception:
            log.exception("stats page signal wiring failed")
        # pages ship their own headers; the window-level page_title already
        # names the destination — hide the duplicates
        for _pg, _attr in ((self.albums_page, "title"),
                           (self.stats_page, "title_lbl")):
            try:
                getattr(_pg, _attr).hide()
            except Exception:
                pass
        self.artists_page = ArtistsPage(self)                 # 4 artists
        self.stack.addWidget(self.artists_page)
        self.genres_page = GenresPage(self)                   # 5 genres
        self.stack.addWidget(self.genres_page)
        self.playlists_page = PlaylistsPage3(self)            # 6 playlists
        self.stack.addWidget(self.playlists_page)
        for _pg, _attr in ((self.artists_page, "title_lbl"),
                           (self.genres_page, "title_lbl"),
                           (self.playlists_page, "title_lbl")):
            try:
                getattr(_pg, _attr).hide()
            except Exception:
                pass
        self.radio_page = self._build_radio_page()            # 7 radio
        self.stack.addWidget(self.radio_page)
        self.main_layout.addWidget(self.stack, 1)

        top.addWidget(self.main_area, 1)
        outer.addLayout(top, 1)

        # Player bar
        self.player_bar = PlayerBar(self.theme)
        # Non-modal update badge — pinned above the player bar, bottom-right
        self._update_banner = UpdateBanner(self)
        self._update_banner.reposition_if_visible()
        outer.addWidget(self.player_bar)
        # Cover click → fullscreen player; title/artist click →
        # track info dialog (separate handlers so the cover opens the
        # immersive view while the text still shows track details).
        self.player_bar.cover_click_handler = self._toggle_fullscreen_player
        self.player_bar.info_click_handler = self._on_bar_info_clicked

        # Signals
        self.player_bar.like_btn.clicked.connect(self._on_like_toggled)
        self.player_bar.prev_btn.clicked.connect(self._on_prev)
        self.player_bar.play_btn.clicked.connect(self._on_play_pause)
        self.player_bar.next_btn.clicked.connect(self._on_next)
        self.player_bar.shuffle_btn.clicked.connect(self._on_shuffle_toggled)
        self.player_bar.lyrics_btn.clicked.connect(self._toggle_lyrics_panel)
        self.player_bar.more_btn.clicked.connect(self._show_more_menu)
        self.player_bar.ab_btn.clicked.connect(self._on_ab_button)
        self.player_bar.rate_btn.clicked.connect(self._cycle_playback_rate)
        self.player_bar.repeat_btn.clicked.connect(self._on_repeat_toggled)
        self.player_bar.seek_slider.sliderPressed.connect(self._on_seek_pressed)
        self.player_bar.seek_slider.sliderReleased.connect(self._on_seek_released)
        self.player_bar.seek_slider.sliderMoved.connect(self._on_seek_moved)
        self.player_bar.seek_slider.valueChanged.connect(self._on_seek_value_changed)
        self.player_bar.vol_slider.valueChanged.connect(self._on_volume_changed)
        self.player_bar.vol_slider.sliderMoved.connect(self._on_volume_changed)
        self.player_bar.vol_btn.clicked.connect(self._on_mute_toggle)

        self.library_list.itemDoubleClicked.connect(self._on_track_double_clicked)
        self.favorites_list.itemDoubleClicked.connect(self._on_track_double_clicked)

        # Context menus
        self.library_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.library_list.customContextMenuRequested.connect(self._on_context_menu)
        self.favorites_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.favorites_list.customContextMenuRequested.connect(self._on_context_menu)

        icon_path = Path(__file__).parent / "assets" / "icon.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

    def _build_library_page(self) -> QWidget:
        """Two-column layout: track list LEFT, cover art RIGHT."""
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        # LEFT: folder tree + track list
        left_split = QSplitter(Qt.Orientation.Horizontal)
        left_split.setHandleWidth(8)
        left_split.setStyleSheet(f"QSplitter::handle {{ background: transparent; }}")

        # Folder tree
        self.folder_tree = QTreeWidget()
        self.folder_tree.setHeaderHidden(True)
        self.folder_tree.setObjectName("TrackList")
        self.folder_tree.setMinimumWidth(140)
        self.folder_tree.setMaximumWidth(220)
        self.folder_tree.setStyleSheet(
            f"QTreeWidget {{ background: {self.theme['panel_bg']}; border: 1px solid {self.theme['border']}; border-radius: 10px; padding: 6px; outline: 0; }}"
            f" QTreeWidget::item {{ padding: 8px 6px; border-radius: 8px; margin: 1px 2px; outline: 0; background: transparent; }}"
            f" QTreeWidget::item:hover {{ background: {self.theme['panel_bg_2']}; outline: 0; }}"
            f" QTreeWidget::item:selected {{ background: transparent; color: {self.theme['gold_light']}; outline: 0; border: none; }}"
            f" QTreeWidget::item:focus {{ background: transparent; outline: 0; border: none; }}"
        )
        # No selection rectangle at all behind folder names — clicks are
        # handled in code and the folder stays visually clean.
        self.folder_tree.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection)
        self.folder_tree.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.folder_tree.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.folder_tree.itemClicked.connect(self._on_folder_tree_click)
        self.folder_tree.itemExpanded.connect(self._on_folder_tree_expanded)
        self.folder_tree.customContextMenuRequested.connect(
            self._on_folder_tree_context_menu)
        left_split.addWidget(self.folder_tree)

        # Track list (v3: rows OR virtual table, switchable in settings)
        self.library_list = QListWidget()
        self.library_list.setObjectName("TrackList")
        self.library_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.library_list.setUniformItemSizes(True)
        self.library_list.setMinimumHeight(200)
        self._track_delegate = TrackRowDelegate(self.theme, self.library_list)
        self.library_list.setItemDelegate(self._track_delegate)
        try:
            self.library_table = LibraryTable(self)
            self.library_table.set_columns(
                list(self.prefs.get("table_columns",
                                    ["index", "title", "artist", "album",
                                     "duration"])))
        except Exception:
            self.library_table = None
        left_split.addWidget(self.library_list)

        left_split.setSizes([160, 400])
        left_split.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(left_split, 3)

        # RIGHT: cover art + title + artist
        right_col = QVBoxLayout()
        right_col.setSpacing(10)
        right_col.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_col.setContentsMargins(10, 10, 10, 10)

        self.cover_label = CoverLabel()
        right_col.addWidget(self.cover_label, alignment=Qt.AlignmentFlag.AlignCenter)

        self.now_title = MarqueeLabel("No track selected")
        self.now_title.setObjectName("NowTitle")
        self.now_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_col.addWidget(self.now_title)

        self.now_artist = MarqueeLabel("Add a folder to get started")
        self.now_artist.setObjectName("NowArtist")
        self.now_artist.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_col.addWidget(self.now_artist)

        right_w = QWidget()
        right_w.setLayout(right_col)
        right_w.setFixedWidth(300)
        right_w.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        layout.addWidget(right_w, 0)

        return page

    def _build_favorites_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        center = QHBoxLayout()
        center.addStretch(1)
        cover_col = QVBoxLayout()
        cover_col.setSpacing(10)
        cover_col.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.fav_cover = CoverLabel()
        cover_col.addWidget(self.fav_cover, alignment=Qt.AlignmentFlag.AlignCenter)
        self.fav_title = QLabel("Favorites")
        self.fav_title.setObjectName("NowTitle")
        self.fav_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cover_col.addWidget(self.fav_title)
        self.fav_subtitle = QLabel("Double-click a track to play")
        self.fav_subtitle.setObjectName("NowArtist")
        self.fav_subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cover_col.addWidget(self.fav_subtitle)
        center.addLayout(cover_col)
        center.addStretch(1)
        center_w = QWidget()
        center_w.setLayout(center)
        center_w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(center_w, 3)
        self.favorites_list = QListWidget()
        self.favorites_list.setObjectName("TrackList")
        self.favorites_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.favorites_list.setUniformItemSizes(True)
        self.favorites_list.setMinimumHeight(140)
        self._fav_delegate = TrackRowDelegate(self.theme, self.favorites_list)
        self.favorites_list.setItemDelegate(self._fav_delegate)
        layout.addWidget(self.favorites_list, 2)
        return page

    # ------------------------------------------------------------------
    # Albums grid page
    # ------------------------------------------------------------------
    def _build_albums_page(self) -> QWidget:
        from PyQt6.QtWidgets import QListWidget, QListWidgetItem
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self.albums_grid = QListWidget()
        self.albums_grid.setObjectName("TrackList")
        self.albums_grid.setViewMode(QListWidget.ViewMode.IconMode)
        self.albums_grid.setIconSize(QSize(120, 120))
        self.albums_grid.setGridSize(QSize(150, 190))
        self.albums_grid.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)   # one album at a time
        self.albums_grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.albums_grid.setWordWrap(True)
        self.albums_grid.itemDoubleClicked.connect(self._on_album_clicked)
        layout.addWidget(self.albums_grid)
        return page

    def _rebuild_albums_grid(self):
        """Group library tracks by (album tag | artist | folder) and show
        each group's cover + name.

        LAG FIX: the old version ran mutagen over every untagged track and
        decoded covers synchronously on the UI thread (≈14 s freeze on a
        3k-track library). The grouping + cover decoding now runs in a
        QThread; results stream back and the grid fills incrementally.
        """
        import coverart as _ca

        # Fast pass — group by cached tags (no I/O); only truly untagged
        # tracks need the background worker.
        groups = {}
        untagged = []
        for p in self.library:
            title, artist = self._tag_cache.get(p, ("", ""))
            if not title and not artist:
                untagged.append(p)
                key = "Unknown Album"
            else:
                key = f"{artist} — Singles" if artist else "Unknown Album"
            groups.setdefault(key, []).append(p)

        dpr = get_dpr()
        self.albums_grid.clear()
        from PyQt6.QtGui import QIcon as _QI
        placeholder = render_icon(Icon.MUSIC_NOTE, 120, self.theme["gold"], dpr)
        for name in sorted(groups.keys(), key=str.lower):
            paths = groups[name]
            if name == "Unknown Album" and untagged:
                continue   # this bucket gets filled by the worker instead
            item = QListWidgetItem(f"{name}\n({len(paths)} tracks)")
            # LAG FIX: covers decode in the background worker for cache-misses;
            # only reuse an already-cached pixmap here (zero I/O on the UI
            # thread — the old code decoded up to 4 covers per album here).
            cover_pm = _ca.get_cached_pixmap(paths[0])
            if cover_pm is None:
                cover_pm = placeholder
            item.setIcon(_QI(cover_pm.scaled(
                130, 130, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)))
            item.setData(Qt.ItemDataRole.UserRole, paths[0])
            item.setToolTip("\n".join(os.path.basename(x) for x in paths[:8]))
            self.albums_grid.addItem(item)

        if not untagged:
            return

        # Background pass — resolve album tags + decode one cover per album
        # off the UI thread; chunks stream into the grid as they complete.
        # A startup preload may still be running — reuse it instead of
        # doubling the work; its results land in the same grid.
        worker = getattr(self, "_albums_worker", None)
        if worker is not None and worker.isRunning():
            return
        self._albums_worker = AlbumsWorkerThread(untagged, self)
        self._albums_worker.album_group_ready.connect(
            self._on_album_group_ready)
        self._albums_worker.finished.connect(
            lambda: self.loading_overlay.hide_loading())
        self.loading_overlay.show_loading("Loading albums")
        self._albums_worker.start()

    def _preload_albums_background(self):
        """Warm the album groups + cover cache at STARTUP (user request:
        «آلبوم‌ها در اول برنامه در پس‌زمینه لود بشن و بمونن»). Revisiting
        the Albums page afterwards is instant — everything comes from cache."""
        # A worker may already be filling the grid (user clicked Albums early)
        if getattr(self, "_albums_worker", None) is not None and \
                self._albums_worker.isRunning():
            return
        self._albums_worker = AlbumsWorkerThread(list(self.library), self)
        self._albums_worker.album_group_ready.connect(
            self._on_album_group_ready)   # grid updates live if visible
        self._albums_worker.start()

    def _on_album_group_ready(self, name: str, paths: list, cover_data):
        """Worker result: one album group — append it to the grid (or warm
        the caches during a background preload)."""
        if not self.albums_grid.isVisible():
            return
        dpr = get_dpr()
        item = QListWidgetItem(f"{name}\n({len(paths)} tracks)")
        if cover_data:
            pm = coverart.bytes_to_pixmap(cover_data)
            if pm.isNull():
                pm = None
        else:
            pm = None
        if pm is None:
            pm = render_icon(Icon.MUSIC_NOTE, 120, self.theme["gold"], dpr)
        from PyQt6.QtGui import QIcon as _QI
        item.setIcon(_QI(pm.scaled(
            130, 130, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)))
        item.setData(Qt.ItemDataRole.UserRole, paths[0])
        item.setToolTip("\n".join(os.path.basename(x) for x in paths[:8]))
        self.albums_grid.addItem(item)

    def _on_album_clicked(self, item):
        """Double-click an album: filter the library list to its tracks."""
        first = item.data(Qt.ItemDataRole.UserRole)
        if not first:
            return
        # Re-derive the album group of this representative track — the album
        # page groups by folder, so every track of the same folder belongs.
        target_dir = os.path.dirname(first)
        tracks = [p for p in self.library if path_within(p, target_dir)]
        if first not in tracks:
            tracks.append(first)
        self._play_from_list(tracks, tracks.index(first),
                             view=View.LIBRARY)

    # ------------------------------------------------------------------
    # Stats page
    # ------------------------------------------------------------------
    def _build_stats_page(self) -> QWidget:
        """Listening stats — redesigned: stat cards on top, a "Most Played"
        list with real album covers, and a compact top-artists strip."""
        from PyQt6.QtWidgets import QScrollArea
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(8, 4, 8, 8)
        outer.setSpacing(14)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent;")
        body = QWidget()
        body.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(16, 8, 16, 16)
        lay.setSpacing(16)

        # ---- Stat cards row (total / plays / artists / sessions) --------
        cards_row = QHBoxLayout()
        cards_row.setSpacing(12)
        self._stat_cards = {}
        for key, icon_name in (("total", Icon.TIMER), ("plays", Icon.PLAY),
                               ("artists", Icon.ALBUMS), ("sessions", Icon.STATS)):
            card = QFrame()
            card.setObjectName("TrackList")   # glass panel look
            card.setFixedHeight(92)
            cl = QHBoxLayout(card)
            cl.setContentsMargins(16, 12, 16, 12)
            cl.setSpacing(12)
            dpr = get_dpr()
            ic = QLabel()
            ic.setPixmap(render_icon(icon_name, 30, self.theme["gold"], dpr))
            ic.setFixedWidth(34)
            ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cl.addWidget(ic)
            col = QVBoxLayout()
            col.setSpacing(2)
            val = QLabel("—")
            val.setObjectName("NowTitle")
            val.setStyleSheet("font-size: 19px;")
            val.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            sub = QLabel("")
            sub.setStyleSheet(f"color: {self.theme['muted']}; font-size: 11px;")
            col.addWidget(val)
            col.addWidget(sub)
            cl.addLayout(col, 1)
            cards_row.addWidget(card, 1)
            self._stat_cards[key] = (val, sub, ic)
        lay.addLayout(cards_row)

        # ---- Most played: real rows with per-track play buttons ----------
        mp_card = QFrame()
        mp_card.setObjectName("TrackList")
        mp_lay = QVBoxLayout(mp_card)
        mp_lay.setContentsMargins(16, 14, 16, 14)
        mp_lay.setSpacing(8)
        mp_head = QLabel("Most Played Tracks")
        mp_head.setObjectName("PageTitle")
        mp_head.setStyleSheet("font-size: 15px;")
        mp_lay.addWidget(mp_head)
        # A vertical list of StatTrackRow widgets — each row: medal, cover,
        # title/artist, play count, and a ▶ button that plays that track
        # (double-click on the row plays it too).
        self._stat_tracks_area = QWidget()
        self._stat_tracks_area.setStyleSheet("background: transparent;")
        self._stat_tracks_lay = QVBoxLayout(self._stat_tracks_area)
        self._stat_tracks_lay.setContentsMargins(0, 0, 0, 0)
        self._stat_tracks_lay.setSpacing(4)
        mp_lay.addWidget(self._stat_tracks_area)
        self.stat_top_tracks = QLabel("")   # empty-state message lives here
        self.stat_top_tracks.setWordWrap(True)
        mp_lay.addWidget(self.stat_top_tracks)
        lay.addWidget(mp_card)

        # ---- Listening activity chart card --------------------------------
        ch_card = QFrame()
        ch_card.setObjectName("TrackList")
        ch_lay = QVBoxLayout(ch_card)
        ch_lay.setContentsMargins(16, 14, 16, 14)
        ch_lay.setSpacing(8)
        ch_head = QLabel("Top Tracks Chart")
        ch_head.setObjectName("PageTitle")
        ch_head.setStyleSheet("font-size: 15px;")
        ch_lay.addWidget(ch_head)
        self._stats_chart = StatsChartBar(t := self.theme)
        ch_lay.addWidget(self._stats_chart)
        lay.addWidget(ch_card)

        # ---- Top artists strip -------------------------------------------
        ar_card = QFrame()
        ar_card.setObjectName("TrackList")
        ar_lay = QVBoxLayout(ar_card)
        ar_lay.setContentsMargins(16, 14, 16, 14)
        ar_lay.setSpacing(8)
        ar_head = QLabel("Top Artists")
        ar_head.setObjectName("PageTitle")
        ar_head.setStyleSheet("font-size: 15px;")
        ar_lay.addWidget(ar_head)
        self.stat_artists_label = QLabel("")
        self.stat_artists_label.setWordWrap(True)
        ar_lay.addWidget(self.stat_artists_label)
        self.stat_misc = QLabel("")
        self.stat_misc.setStyleSheet(f"color: {self.theme['muted']}; font-size: 11px;")
        ar_lay.addWidget(self.stat_misc)
        lay.addWidget(ar_card)

        row = QHBoxLayout()
        row.addStretch(1)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.setObjectName("GhostBtn")
        refresh_btn.clicked.connect(self._refresh_stats_view)
        row.addWidget(refresh_btn)
        lay.addLayout(row)
        lay.addStretch(1)

        scroll.setWidget(body)
        outer.addWidget(scroll)
        self._stats_built = True
        return page

    def _refresh_stats_view(self):
        """v3: delegates to the StatsPage3 widget (kept for timer compat)."""
        page = getattr(self, "stats_page", None)
        if page is not None:
            try:
                page.refresh()
                return
            except Exception:
                log.exception("stats page refresh failed")

    def _refresh_stats_view_legacy(self):
        if not getattr(self, "stats", None) or not getattr(self, "_stats_built", False):
            return
        from playstats import PlayStats
        t = self.theme
        dpr = get_dpr()

        def _set_card(key, value, subtitle):
            val, sub, ic = self._stat_cards[key]
            val.setText(value)
            sub.setText(subtitle)
            icon_name = {"total": Icon.TIMER, "plays": Icon.PLAY,
                         "artists": Icon.ALBUMS, "sessions": Icon.STATS}[key]
            ic.setPixmap(render_icon(icon_name, 30, t["gold"], dpr))

        total = self.stats.total_seconds
        plays = sum(self.stats.play_counts.values())
        artists_n = len(self.stats.artist_seconds)
        _set_card("total", PlayStats.fmt_total(total), "total listening time")
        _set_card("plays", f"{plays:,}", "tracks played")
        _set_card("artists", f"{artists_n:,}", "artists heard")
        _set_card("sessions", f"{self.stats.sessions:,}", "sessions opened")

        # Most played — real interactive rows: cover + play button per track.
        # Paths ride along from top_tracks() so covers resolve DIRECTLY
        # (the old title-based lookup missed files with duplicate names).
        # Rebuilt only when the ranking changed (button mashing on Refresh
        # used to rebuild identical rows every click).
        tops = self.stats.top_tracks(self._tag_cache, 10)
        sig = tuple(tops)
        if sig == getattr(self, "_stats_rows_sig", None):
            return
        self._stats_rows_sig = sig
        while self._stat_tracks_lay.count():
            item = self._stat_tracks_lay.takeAt(0)
            wd = item.widget()
            if wd is not None:
                wd.deleteLater()
        if tops:
            self.stat_top_tracks.setVisible(False)
            for i, (title, artist, cnt, path) in enumerate(tops, 1):
                # Cover: warm cache first (instant); a cold cache reads the
                # file once — stats refresh is user-triggered and bounded
                # to 10 rows, so the small synchronous cost is acceptable
                # (and results land in the shared cover cache).
                cover_pm = coverart.get_cached_pixmap(path)
                if cover_pm is None:
                    data = coverart.get_cover_bytes(path)
                    if data:
                        cover_pm = coverart.bytes_to_pixmap(data)
                row = StatTrackRow(i, path, title, artist, cnt, cover_pm,
                                   self.theme, self._play_from_stats)
                self._stat_tracks_lay.addWidget(row)
            self._stat_tracks_area.setVisible(True)
            # Animated top-tracks bar chart
            self._stats_chart.set_theme(self.theme)
            self._stats_chart.set_data(
                [(title, cnt) for title, _a, cnt, _p in tops[:7]])
        else:
            self._stat_tracks_area.setVisible(False)
            self.stat_top_tracks.setVisible(True)
            self.stat_top_tracks.setText(
                f"<span style='color:{t['muted']};'>Nothing yet — play something!</span>")
            self._stats_chart.set_data([])

        artists = self.stats.top_artists(5)
        if artists:
            rows = []
            for rank, (a, dur) in enumerate(artists, 1):
                bar_pct = max(8, int(100 - rank * 16))
                rows.append(
                    f"<table width='100%' cellspacing='0'><tr>"
                    f"<td width='18' style='color:{t['muted']};'>{rank}</td>"
                    f"<td style='color:{t['text']};'>{self._esc_html(a)}</td>"
                    f"<td width='90'>"
                    f"<table width='{bar_pct}%'><tr><td style='background-color:"
                    f"{t['gold']};'>&nbsp;</td></tr></table></td>"
                    f"<td align='right' width='80' style='color:{t['muted']};'>"
                    f"{dur}</td></tr></table>")
            self.stat_artists_label.setText(
                "<div style='line-height:150%;'>" + "".join(rows) + "</div>")
        else:
            self.stat_artists_label.setText("")
        self.stat_misc.setText(
            ("Listening since " + self.stats.first_played)
            if self.stats.first_played else "")

    @staticmethod
    def _esc_html(s: str) -> str:
        return (s.replace("&", "&amp;").replace("<", "&lt;")
                 .replace(">", "&gt;"))

    def _play_from_stats(self, path: str):
        """▶ on a stats row: play this track with the library as the queue
        (proper next/prev context), like clicking it in the library list."""
        if not path:
            # Stale stats entry (file removed from library, cache not yet
            # rebuilt) — silently ignore instead of nagging the user.
            log.info("stats row has no resolvable track path; ignoring play")
            return
        if not os.path.exists(path):
            QMessageBox.information(self, APP_NAME,
                "This track is no longer in the library.")
            return
        if path not in self.library:
            self._play_path_from_playlist(path)
            return
        self._on_rail_clicked("library")
        self._play_from_list(list(self.library),
                             max(0, self.library.index(path)),
                             view=View.LIBRARY)

    def _build_tray(self):
        icon_path = Path(__file__).parent / "assets" / "icon.png"
        tray_icon = QIcon(str(icon_path)) if icon_path.exists() else QIcon()
        self.tray = QSystemTrayIcon(tray_icon, self)
        self.tray.setToolTip(APP_NAME)
        # Custom popup tray menu (two rows of icon buttons)
        self.tray_menu_widget = TrayMenuWidget(self.theme, self)
        self.tray_menu_widget.prev_clicked.connect(self._on_prev)
        self.tray_menu_widget.play_clicked.connect(self._on_play_pause)
        self.tray_menu_widget.next_clicked.connect(self._on_next)
        self.tray_menu_widget.show_clicked.connect(self._toggle_visible)
        self.tray_menu_widget.close_clicked.connect(self.tray_menu_widget.close)
        self.tray_menu_widget.quit_clicked.connect(self._quit)
        # Right-click opens the CUSTOM styled popup (not the gray native
        # QMenu — Windows would render that with the old system theme).
        # Left/middle clicks keep their behaviors in _on_tray_activated.
        self.tray.setContextMenu(None)
        self.tray.activated.connect(self._on_tray_activated)

    def _setup_shortcuts(self):
        # README-documented bindings (always active, no conflicts):
        #   Ctrl+F search · Ctrl+M mini player · Ctrl+J jump to playing
        #   F11/F fullscreen · Esc leave fullscreen
        #   Ctrl+arrows: prev/next track · volume up/down
        QShortcut(QKeySequence("Ctrl+F"), self, activated=lambda: self.search_edit.setFocus())
        QShortcut(QKeySequence("Ctrl+M"), self, activated=self._toggle_mini_player)
        QShortcut(QKeySequence("Ctrl+J"), self, activated=self._jump_to_playing)
        QShortcut(QKeySequence("Ctrl+Left"), self, activated=self._on_prev)
        QShortcut(QKeySequence("Ctrl+Right"), self, activated=self._on_next)
        QShortcut(QKeySequence("Ctrl+Up"), self, activated=lambda: self._change_volume(5))
        QShortcut(QKeySequence("Ctrl+Down"), self, activated=lambda: self._change_volume(-5))
        QShortcut(QKeySequence(Qt.Key.Key_F11), self, activated=self._toggle_fullscreen_player)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self._exit_fullscreen_player)
        # Bare convenience keys (Space, Enter, plain arrows, M, F) are
        # dispatched in keyPressEvent() ONLY when no widget consumed them —
        # a window-wide QShortcut for bare keys used to hijack typing in the
        # search box and type-ahead/navigation in lists (pressing F with a
        # list focused opened the fullscreen player).

    def keyPressEvent(self, e):
        """Focus-aware fallback for the bare convenience keys.

        Runs only when no child widget accepted the key event. If focus is
        on a widget that meaningfully owns keys (item views with their
        type-ahead/navigation, sliders, text inputs, combos) the leftover
        key is dropped so nothing is stolen; on neutral focus the player
        shortcuts apply.
        """
        fw = QApplication.focusWidget()
        if isinstance(fw, (QAbstractItemView, QSlider, QLineEdit,
                           QTextEdit, QPlainTextEdit, QComboBox)):
            super().keyPressEvent(e)   # the widget already had its chance
            return
        k = e.key()
        if k in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._on_play_pause()
        elif k == Qt.Key.Key_Left:
            self._on_prev()
        elif k == Qt.Key.Key_Right:
            self._on_next()
        elif k == Qt.Key.Key_Up:
            self._change_volume(5)
        elif k == Qt.Key.Key_Down:
            self._change_volume(-5)
        elif k == Qt.Key.Key_M:
            self._on_mute_toggle()
        elif k == Qt.Key.Key_F:
            self._toggle_fullscreen_player()
        else:
            super().keyPressEvent(e)

    # ------------------------------------------------------------------
    # Fullscreen player (F11 / F to toggle, Esc to exit)
    # ------------------------------------------------------------------
    def _toggle_fullscreen_player(self):
        if getattr(self, "_fullscreen_player", None) is not None and \
                self._fullscreen_player.isVisible():
            self._exit_fullscreen_player()
            return
        self._fullscreen_player = getattr(self, "_fullscreen_player", None)
        if self._fullscreen_player is None:
            from fullscreen_player import FullscreenPlayer
            self._fullscreen_player = FullscreenPlayer(self)
        self._fullscreen_player._apply_theme()
        self._fullscreen_player.refresh_state()
        self._fullscreen_player.show()
        self._fullscreen_player.raise_()
        self._fullscreen_player.activateWindow()

    def _exit_fullscreen_player(self):
        fs = getattr(self, "_fullscreen_player", None)
        if fs is not None and fs.isVisible():
            fs.close()

    def _change_volume(self, delta):
        v = self.player_bar.vol_slider.value() + delta
        v = max(0, min(100, v))
        self.player_bar.vol_slider.setValue(v)

    def _early_load_theme(self):
        """Synchronously read the theme + prefs keys from the config file.
        Tiny local-JSON read (~µs) that lets _build_ui() paint with the
        user's saved theme instead of the DEFAULT dark one — the root fix
        for light themes starting life with stale dark-theme inline colors.
        v3: prefs ride along so the audio engine choice + UI scale are known
        before the player/bar are constructed."""
        try:
            cfg, ok = load_json_file(config_path(), "config")
            if ok and isinstance(cfg, dict):
                if cfg.get("theme"):
                    self.theme_name = Theme.normalize(str(cfg["theme"]))
                    self.theme = Theme.get(self.theme_name)
                if isinstance(cfg.get("prefs"), dict):
                    from gm.prefs import merge_prefs
                    self.prefs = merge_prefs(cfg["prefs"])
        except Exception as e:
            log.debug(f"early theme load skipped: {e}")

    _THEME_COLOR_KEYS = ("window_bg", "panel_bg", "panel_bg_2", "border",
                         "text", "muted", "gold", "gold_light", "gold_deep",
                         "active")

    def _sweep_inline_theme_styles(self):
        """Rewrite inline stylesheets that still carry the PREVIOUS theme's
        color tokens (stats labels, hints, custom panels...).  _apply_theme
        can only explicitly re-style a handful of widgets; everything styled
        with an f-string at construction time is caught by this sweep."""
        old = getattr(self, "_last_theme", None)
        if not old or old.get("name") == self.theme.get("name"):
            return
        new = self.theme
        mapping = [(old[k], new[k]) for k in self._THEME_COLOR_KEYS
                   if old.get(k) and old.get(k) != new.get(k)]
        if not mapping:
            return
        for w in self.findChildren(QWidget):
            ss = w.styleSheet()
            if not ss:
                continue
            updated = ss
            for old_c, new_c in mapping:
                if old_c in updated:
                    updated = updated.replace(old_c, new_c)
            if updated != ss:
                w.setStyleSheet(updated)

    def _sync_app_palette(self):
        """Mirror the active theme into the QApplication palette and force
        the matching Qt color scheme.  Without this, Fusion auto-follows the
        OS dark/light setting: on a Windows machine in dark mode, every
        unstyled area (tab strips, scroll viewports, tooltips, message boxes)
        renders in dark palette colors on top of a light theme."""
        app = QApplication.instance()
        if app is None:
            return
        t = self.theme
        is_dark = t.get("is_dark", True)
        pal = app.palette()
        roles = {
            QPalette.ColorRole.Window: t["window_bg"],
            QPalette.ColorRole.WindowText: t["text"],
            QPalette.ColorRole.Base: t["panel_bg"],
            QPalette.ColorRole.AlternateBase: t["panel_bg_2"],
            QPalette.ColorRole.Text: t["text"],
            QPalette.ColorRole.Button: t["panel_bg_2"],
            QPalette.ColorRole.ButtonText: t["text"],
            QPalette.ColorRole.ToolTipBase: t["panel_bg_2"],
            QPalette.ColorRole.ToolTipText: t["text"],
            QPalette.ColorRole.Highlight: t["gold"],
            QPalette.ColorRole.HighlightedText: ("#ffffff" if not is_dark
                                                 else t["window_bg"]),
            QPalette.ColorRole.Link: t["gold"],
            QPalette.ColorRole.PlaceholderText: t["muted"],
        }
        for role, color in roles.items():
            pal.setColor(role, QColor(color))
        # Disabled controls: muted text so they stay legible but dimmed.
        for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                     QPalette.ColorRole.ButtonText):
            pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(t["muted"]))
        app.setPalette(pal)
        # Stop Fusion from auto-following the OS color scheme (Qt 6.5+).
        try:
            app.styleHints().setColorScheme(
                Qt.ColorScheme.Dark if is_dark else Qt.ColorScheme.Light)
        except Exception:
            pass

    def _apply_theme_dict(self, t: dict):
        """Apply a theme dict in place (used by the dynamic album theme)."""
        if not t:
            return
        self.theme = dict(t)
        try:
            from gm.theme_qss import build_qss_v3
            qss = build_qss_v3(self.theme,
                               scale=float(self.prefs.get("ui_scale", 1.0)))
        except Exception:
            qss = build_qss(self.theme)
        app_inst = QApplication.instance()
        if app_inst is not None:
            app_inst.setStyleSheet(qss)
        self.setStyleSheet(qss)
        self._sync_app_palette()
        if hasattr(self, "rail"):
            self.rail.set_theme(self.theme)
        if hasattr(self, "player_bar"):
            self.player_bar.set_theme(self.theme)

    def _apply_theme(self):
        self.theme = Theme.get(self.theme_name)
        # v3: dynamic album-cover theme overrides the base palette while
        # enabled (transient — never persisted as the user's theme choice)
        if self.prefs.get("dynamic_album_theme", False) and \
                getattr(self, "_dynamic_theme", None):
            self.theme = dict(self._dynamic_theme)
        theme_changed = (getattr(self, "_last_theme", None) or {}).get("name") \
            != self.theme.get("name")
        # v3 design system QSS (typography scale + modern components);
        # falls back to the v2 builder if the module is unavailable.
        try:
            from gm.theme_qss import build_qss_v3
            qss = build_qss_v3(self.theme,
                               scale=float(self.prefs.get("ui_scale", 1.0)),
                               reduce_animations=bool(
                                   self.prefs.get("reduce_animations", False)))
        except Exception:
            qss = build_qss(self.theme)
        # Apply at BOTH levels: on the window for its children (as before)
        # and on the application so top-level popups that are not QObject
        # children of the window — QTipLabel tooltips above all — inherit
        # the themed QToolTip/QMenu rules instead of the native palette.
        app_inst = QApplication.instance()
        if app_inst is not None:
            app_inst.setStyleSheet(qss)
        self.setStyleSheet(qss)
        self._sync_app_palette()
        if hasattr(self, "rail"):
            self.rail.set_theme(self.theme)
        for page in ("albums_page", "artists_page", "genres_page",
                     "playlists_page", "stats_page"):
            w = getattr(self, page, None)
            if w is not None:
                try:
                    w.set_theme(self.theme)
                except Exception:
                    pass
        if getattr(self, "_palette", None) is not None:
            try:
                self._palette.set_theme(self.theme)
            except Exception:
                pass
        if hasattr(self, "_track_delegate"):
            self._track_delegate.set_theme(self.theme)
        if hasattr(self, "_fav_delegate"):
            self._fav_delegate.set_theme(self.theme)
        if hasattr(self, "player_bar"):
            self.player_bar.set_theme(self.theme)
        # Tray popup follows the theme too (dividers, accent buttons,
        # header, glass background) — previously it kept its boot colors.
        tmw = getattr(self, "tray_menu_widget", None)
        if tmw is not None:
            tmw.set_theme(self.theme)
        for cover in ("cover_label", "fav_cover"):
            if hasattr(self, cover):
                getattr(self, cover).set_theme(self.theme)
        if hasattr(self, "scan_status_label"):
            self.scan_status_label.setStyleSheet(f"color: {self.theme['gold']};")
        if hasattr(self, "folder_tree"):
            self.folder_tree.setStyleSheet(
                f"QTreeWidget {{ background: {self.theme['panel_bg']}; border: 1px solid {self.theme['border']}; border-radius: 10px; padding: 6px; outline: 0; }}"
                f" QTreeWidget::item {{ padding: 8px 6px; border-radius: 8px; margin: 1px 2px; outline: 0; background: transparent; }}"
                f" QTreeWidget::item:hover {{ background: {self.theme['panel_bg_2']}; outline: 0; }}"
                f" QTreeWidget::item:selected {{ background: transparent; color: {self.theme['gold_light']}; outline: 0; border: none; }}"
                f" QTreeWidget::item:focus {{ background: transparent; outline: 0; border: none; }}"
            )
            if theme_changed:
                self._rebuild_folder_tree()
        # Rebuild tray menu with new theme colors
        if hasattr(self, "tray") and self.tray is not None:
            self._rebuild_tray_menu()
        if hasattr(self, "library_list") and theme_changed:
            self._highlight_playing_in_lists()
        if theme_changed:
            # Catch every remaining widget whose inline stylesheet was baked
            # with the previous theme's colors. The sweep walks EVERY widget
            # (~500 on a loaded window) — skip it entirely for same-theme
            # re-applies (startup called it twice, wasting ~200 ms each).
            self._sweep_inline_theme_styles()
            # Stats rows carry per-instance styles the sweep can't guess —
            # re-theme them explicitly.
            rows_lay = getattr(self, "_stat_tracks_lay", None)
            if rows_lay is not None:
                for i in range(rows_lay.count()):
                    row = rows_lay.itemAt(i).widget()
                    if isinstance(row, StatTrackRow):
                        row.set_theme(self.theme)
            chart = getattr(self, "_stats_chart", None)
            if chart is not None:
                chart.set_theme(self.theme)
        # Snapshot for the next sweep.
        self._last_theme = dict(self.theme)

    def _rebuild_tray_menu(self):
        """The custom tray popup replaces the old native QMenu; just
        re-theme it when the palette changes."""
        if getattr(self, "tray_menu_widget", None) is not None:
            self.tray_menu_widget._apply_theme(self.theme)

    def _jump_to_playing(self):
        """Scroll to and select the currently playing track in its list."""
        if self.current_track is None:
            QMessageBox.information(self, APP_NAME, "Nothing is playing right now.")
            return
        # Switch to the view that contains the track
        target = self.favorites_list if self.current_track in self.favorites else self.library_list
        if self.current_view == View.FAVORITES and self.current_track not in self.favorites:
            # Playing from library while viewing favorites -> jump to library
            pass
        lst = None
        for candidate in (self.library_list, self.favorites_list):
            for i in range(candidate.count()):
                if candidate.item(i).data(Qt.ItemDataRole.UserRole) == self.current_track:
                    lst = candidate
                    break
            if lst:
                break
        if lst is None:
            QMessageBox.information(self, APP_NAME,
                "The playing track is not in the visible lists (it may have been removed).")
            return
        if lst is self.library_list and self.current_view != View.LIBRARY:
            self._on_rail_clicked("library")
        elif lst is self.favorites_list and self.current_view != View.FAVORITES:
            self._on_rail_clicked("favorites")
        for i in range(lst.count()):
            it = lst.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == self.current_track:
                lst.setCurrentRow(i)
                lst.scrollToItem(it, QListWidget.ScrollHint.PositionAtCenter)
                break

    #: stack index per nav key (kept explicit for tests/timers)
    _PAGE_INDEX = {"library": 0, "favorites": 1, "albums": 2, "stats": 3,
                   "artists": 4, "genres": 5, "playlists": 6, "radio": 7}

    def _on_rail_clicked(self, key: str):
        """v3 navigation router: sidebar keys -> stack pages/actions."""
        from gm.i18n import tr
        # sidebar exclusivity (v3 Sidebar.set_active handles QButtonGroup)
        try:
            self.rail.set_active(key if key in self._PAGE_INDEX else key)
        except Exception:
            pass
        if key in self._PAGE_INDEX:
            idx = self._PAGE_INDEX[key]
            self.stack.setCurrentIndex(idx)
            titles = {"library": "Library", "favorites": "Favorites",
                      "albums": "Albums", "stats": "Stats",
                      "artists": "Artists", "genres": "Genres",
                      "playlists": "Playlists", "radio": "Radio"}
            self.page_title.setText(titles.get(key, ""))
            if key in ("library", "favorites"):
                self.current_view = (View.LIBRARY if key == "library"
                                     else View.FAVORITES)
                self._update_count_label()
            else:
                # browsing pages act within the library context
                self.current_view = View.LIBRARY
            if key == "albums":
                self.albums_page.refresh()
            elif key == "stats":
                self.stats_page.refresh()
            elif key == "artists":
                self.artists_page.refresh()
            elif key == "genres":
                self.genres_page.refresh()
            elif key == "playlists":
                self.playlists_page.refresh()
            elif key == "radio":
                self._refresh_radio_page()
        elif key == "refresh":
            self._on_refresh()
        elif key == "addfolder":
            self._on_add_folder()
        elif key == "theme":
            self._show_theme_picker()
        elif key == "settings":
            self._show_settings()
        elif key == "command":
            self._show_command_palette()
        elif key == "mini":
            self._toggle_mini_player()
        elif key == "more_duplicates":
            self._open_duplicates_dialog()
        elif key == "more_backup":
            self._open_backup_restore()
        elif key == "more_export":
            self._export_all_playlists_m3u()

    @property
    def albums_grid(self):
        """v2 compat: the albums grid widget inside the v3 AlbumsPage."""
        page = getattr(self, "albums_page", None)
        return getattr(page, "_grid", None) if page is not None else None

    def _show_playlists(self):
        try:
            from playlist_dialog import PlaylistDialog
            dlg = PlaylistDialog(self, self)
            dlg.exec()
        except Exception as e:
            log.error(f"Playlist dialog failed: {e}")

    def sort_playlists_names(self):
        """Playlist names sorted case-insensitively (used by the dialog)."""
        return sorted(self.playlists.keys(), key=str.lower)

    def _show_theme_picker(self):
        """Show the theme picker popup, fully clamped inside the screen."""
        self.theme_popup = ThemePickerPopup(self.theme_name, self)
        self.theme_popup.theme_selected.connect(self._on_theme_selected)
        # Anchor near the theme button but clamp inside the available
        # screen area so the popup never spills off-monitor.
        btn = self.rail.btn_theme
        btn_pos = btn.mapToGlobal(QPoint(0, 0))
        screen_geo = QApplication.screenAt(
            QPoint(btn_pos.x() + btn.width() // 2,
                   btn_pos.y() + btn.height() // 2))
        if screen_geo is None:
            screen_geo = QApplication.primaryScreen().availableGeometry()
        else:
            screen_geo = screen_geo.availableGeometry()
        pw, ph = self.theme_popup.width(), self.theme_popup.height()
        # Prefer opening to the right of the sidebar, vertically centered
        x = max(screen_geo.left() + 8,
                min(btn_pos.x() + btn.width() + 12,
                    screen_geo.right() - pw - 8))
        y = max(screen_geo.top() + 8,
                min(btn_pos.y() - ph // 2,
                    screen_geo.bottom() - ph - 8))
        self.theme_popup.move(x, y)
        self.theme_popup.show()
        self.theme_popup.raise_()
        self.theme_popup.activateWindow()

    def _on_theme_selected(self, name: str):
        self.theme_name = name
        self._apply_theme()
        self._save_config()

    def _show_settings(self):
        try:
            from gm.ui.settings3 import SettingsDialog3
            dlg = SettingsDialog3(self, self)
        except Exception:
            log.exception("v3 settings failed; using legacy dialog")
            dlg = SettingsDialog(self, self)
        dlg.exec()

    def _toggle_mini_player(self):
        if self.mini_player is None or not self.mini_player.isVisible():
            if self.mini_player is None:
                self.mini_player = MiniPlayer(self)
            self.mini_player._apply_theme()
            if self.current_track:
                title, artist = extract_title_artist(self.current_track)
                # Get current cover
                pm = self.player_bar._cover_thumb_pm
                self.mini_player.update_track(title, artist, pm)
            state = self.audio.state()
            self.mini_player.update_play_state(state == AudioBackend.STATE_PLAYING)
            self.mini_player.show()
            self.mini_player.move(self.geometry().center().x() - 160, self.geometry().bottom() - 120)
            self.hide()
        else:
            self.mini_player.hide()
            self.show()
            self.raise_()
            self.activateWindow()

    def _update_count_label(self):
        if self.current_view == View.LIBRARY:
            n = len(self._get_filtered_sorted_library())
            ff = getattr(self, "_folder_filter", None)
            total = len(self._folder_tracks(ff)) if ff else len(self.library)
            if n != total:
                suffix = " in folder" if ff else ""
                self.count_label.setText(f"{n} of {total} track{'' if total == 1 else 's'}{suffix}")
            else:
                suffix = " in folder" if ff else ""
                self.count_label.setText(f"{n} track{'' if n != 1 else 's'}{suffix}")
        else:
            n = len(self._get_filtered_sorted_favorites())
            total = len(self.favorites)
            if n != total:
                self.count_label.setText(f"{n} of {total} favorites")
            else:
                self.count_label.setText(f"{n} favorite{'s' if n != 1 else ''}")

    def _on_search_changed(self, text):
        # Debounced: rebuilding a 3k-track list on every keystroke stuttered
        # typing; 180 ms after the last keypress is instant-feeling but
        # coalesces bursts of keys.
        self.search_filter = text.lower().strip()
        if self._search_debounce is None:
            self._search_debounce = QTimer(self)
            self._search_debounce.setSingleShot(True)
            self._search_debounce.setInterval(180)
            self._search_debounce.timeout.connect(self._apply_search_filter)
        self._search_debounce.start()

    def _apply_search_filter(self):
        self._rebuild_library_list()
        self._rebuild_favorites_list()
        self._update_count_label()

    def _on_sort_changed(self, idx):
        mode_val = self.sort_combo.itemData(idx)
        self.sort_mode = SortMode(mode_val)
        self._rebuild_library_list()
        self._rebuild_favorites_list()

    def _get_filtered_sorted_library(self):
        tracks = list(self.library)
        # Folder filter (from the folder tree) narrows the pool first; it is
        # kept as state so a search/sort change no longer drops it.
        if getattr(self, "_folder_filter", None):
            ff = self._folder_filter
            tracks = [p for p in tracks if path_within(p, ff)]
        # Filter using cache (fast, no I/O). v3.0: with the SQLite store
        # live, FTS5 answers the query (album/genre too, diacritics-folded,
        # 100k-track fast) and the result set intersects the folder filter.
        if self.search_filter:
            store = getattr(self, "store", None)
            fts_paths = None
            if store is not None and getattr(store, "db", None) is not None:
                try:
                    fts_paths = set(store.search(self.search_filter, 4000))
                except Exception:
                    fts_paths = None
            if fts_paths is not None:
                pool = set(tracks)
                tracks = [p for p in tracks if p in fts_paths]
                # include FTS hits outside the current folder filter only
                # when no folder filter is active (matches v2 semantics)
            else:
                filtered = []
                for path in tracks:
                    if path in self._tag_cache:
                        title, artist = self._tag_cache[path]
                    else:
                        title = os.path.splitext(os.path.basename(path))[0]
                        artist = ""
                    if self.search_filter in title.lower() or self.search_filter in artist.lower():
                        filtered.append(path)
                tracks = filtered
        # Sort using cache (fast)
        if self.sort_mode == SortMode.TITLE:
            tracks.sort(key=lambda x: (self._tag_cache.get(x, (os.path.splitext(os.path.basename(x))[0], ""))[0].lower()))
        elif self.sort_mode == SortMode.ARTIST:
            tracks.sort(key=lambda x: (self._tag_cache.get(x, ("", "Unknown"))[1].lower(),
                                        self._tag_cache.get(x, (os.path.splitext(os.path.basename(x))[0], ""))[0].lower()))
        elif self.sort_mode == SortMode.FILENAME:
            tracks.sort(key=lambda x: os.path.basename(x).lower())
        elif self.sort_mode == SortMode.DATE_ADDED:
            # getmtime is a syscall per file — cache it per library snapshot
            # instead of hitting the disk on every search keystroke.
            tracks.sort(key=lambda x: self._mtime_cache(x), reverse=True)
        return tracks

    def _mtime_cache(self, path: str) -> float:
        cache = getattr(self, "_mtime_map", None)
        if cache is None or path not in cache:
            if cache is None:
                cache = self._mtime_map = {}
                self._mtime_lib_len = 0
            # Rebuild wholesale when the library changes (cheap: one walk)
            if getattr(self, "_mtime_lib_len", 0) != len(self.library):
                cache.clear()
                for p in self.library:
                    try:
                        cache[p] = os.path.getmtime(p)
                    except OSError:
                        cache[p] = 0.0
                self._mtime_lib_len = len(self.library)
            else:
                try:
                    cache[path] = os.path.getmtime(path)
                except OSError:
                    cache[path] = 0.0
        return cache.get(path, 0.0)

    def _get_filtered_sorted_favorites(self):
        tracks = list(self.favorites)
        if self.search_filter:
            filtered = []
            for path in tracks:
                if path in self._tag_cache:
                    title, artist = self._tag_cache[path]
                else:
                    title = os.path.splitext(os.path.basename(path))[0]
                    artist = ""
                if self.search_filter in title.lower() or self.search_filter in artist.lower():
                    filtered.append(path)
            tracks = filtered
        if self.sort_mode == SortMode.TITLE:
            tracks.sort(key=lambda x: (self._tag_cache.get(x, (os.path.splitext(os.path.basename(x))[0], ""))[0].lower()))
        elif self.sort_mode == SortMode.ARTIST:
            tracks.sort(key=lambda x: (self._tag_cache.get(x, ("", "Unknown"))[1].lower(),
                                        self._tag_cache.get(x, (os.path.splitext(os.path.basename(x))[0], ""))[0].lower()))
        elif self.sort_mode == SortMode.FILENAME:
            tracks.sort(key=lambda x: os.path.basename(x).lower())
        elif self.sort_mode == SortMode.DATE_ADDED:
            tracks.sort(key=lambda x: self._mtime_cache(x), reverse=True)
        return tracks

    @staticmethod
    def _default_music_dir() -> str:
        """The OS's user music folder (Windows: <Profile>\\Music)."""
        try:
            from PyQt6.QtCore import QStandardPaths
            locs = QStandardPaths.standardLocations(
                QStandardPaths.StandardLocation.MusicLocation)
            if locs and os.path.isdir(locs[0]):
                return locs[0]
        except Exception:
            pass
        return str(Path.home() / "Music")

    def _on_add_folder(self):
        # Default to the OS music folder unless the user set one explicitly
        if self.default_folder and os.path.isdir(self.default_folder):
            start_dir = self.default_folder
        elif not self.added_folders:
            start_dir = self._default_music_dir()
        else:
            start_dir = ""
        folder = QFileDialog.getExistingDirectory(self, "Select Music Folder", start_dir)
        if not folder:
            return
        if folder in self.added_folders:
            QMessageBox.information(self, APP_NAME, "This folder is already in your library.")
            return
        self.added_folders.append(folder)
        self._start_scan([folder], label="Scanning new folder...")

    def _on_refresh(self):
        """Rescan all added folders: add new tracks, remove deleted ones."""
        if not self.added_folders:
            QMessageBox.information(self, APP_NAME, "No folders to refresh. Add a folder first.")
            return
        existing = [f for f in self.added_folders if os.path.isdir(f)]
        if not existing:
            QMessageBox.information(self, APP_NAME, "None of your added folders exist anymore.")
            return
        self._start_scan(existing, label="Refreshing library...")

    def _startup_rescan(self):
        if not self.added_folders:
            return
        existing = [f for f in self.added_folders if os.path.isdir(f)]
        # Never forget registered folders just because a drive is
        # temporarily offline — scan what's reachable, keep the rest.
        if not existing:
            return
        self._start_scan(existing, label="Checking for new tracks...")

    def _start_scan(self, folders: list, label: str = "Scanning..."):
        if self.scanner is not None and self.scanner.isRunning():
            self._scan_results_pending = folders
            return
        self.scan_status_label.setText(label)
        self.scan_status_label.setVisible(True)
        self.scan_progress.setValue(0)
        self.scan_progress.setVisible(True)
        self.rail.btn_add.setEnabled(False)
        self.rail.btn_refresh.setEnabled(False)  # no double-queue scans
        self._last_scan_folders = list(folders)
        self.scanner = FolderScanner(folders, self)
        self.scanner.progress.connect(self._on_scan_progress)
        self.scanner.finished_scan.connect(self._on_scan_finished)
        self.scanner.start()

    def _on_scan_progress(self, folder: str, found: int, total: int):
        short = os.path.basename(folder) or folder
        if len(short) > 25:
            short = short[:22] + "..."
        self.scan_status_label.setText(f"Scanning: {short} — {found} found")
        if total > 0:
            pct = int(found * 100 / total)
            self.scan_progress.setValue(pct)

    def _on_scan_finished(self, found: list):
        existing = set(self.library)
        added = 0
        for p in found:
            if p not in existing:
                self.library.append(p)
                existing.add(p)
                added += 1
        # Prune ONLY entries under the folders this scan actually covered
        # (+ always drop scratch/test paths). Tracks elsewhere — e.g. on a
        # temporarily disconnected drive — are kept, never wiped.
        roots = [os.path.normcase(os.path.abspath(f))
                 for f in (getattr(self, "_last_scan_folders", None) or [])
                 if f]

        def _keep(p):
            if _is_scratch_path(p):
                return False
            if os.path.exists(p):
                return True
            return not any(path_within(p, r) for r in roots)

        before = len(self.library)
        self.library = [p for p in self.library if _keep(p)]
        removed = before - len(self.library)
        self.library.sort(key=lambda x: os.path.basename(x).lower())
        self.favorites = {p for p in self.favorites if _keep(p)}
        self.scan_progress.setVisible(False)
        self.scan_status_label.setVisible(False)
        self.rail.btn_add.setEnabled(True)
        self.rail.btn_refresh.setEnabled(True)
        self._rebuild_library_list(rebuild_tree=True)
        self._rebuild_favorites_list()
        self._update_count_label()
        try:
            from playstats import set_search_roots
            if getattr(self, "stats", None) is not None:
                _roots = list(self.added_folders or [])
                _roots += list({os.path.dirname(os.path.abspath(p))
                                for p in self.library if p})
                set_search_roots(_roots)
                self.stats.prune_missing(set(self.library))
        except Exception:
            pass
        self._save_config()
        if added > 0 or removed > 0:
            msg = []
            if added:
                msg.append(f"Added {added} new track(s)")
            if removed:
                msg.append(f"Removed {removed} missing track(s)")
            self._show_toast(" · ".join(msg) + f" (total: {len(self.library)})")
        if self._scan_results_pending is not None:
            nxt = self._scan_results_pending
            self._scan_results_pending = None
            self._start_scan(nxt)

    def _rebuild_library_list(self, rebuild_tree: bool = False):
        """v3: routes to the virtual table when the table view is active,
        otherwise the classic two-line row list."""
        try:
            tbl = getattr(self, "library_table", None)
            stack = getattr(self, "_list_stack", None)
            if tbl is not None and stack is not None and stack.currentIndex() == 1:
                tbl.set_paths(self._get_filtered_sorted_library())
                return
        except Exception:
            log.exception("table refresh failed")
        self._rebuild_library_list_orig(rebuild_tree=rebuild_tree)

    def _rebuild_library_list_orig(self, rebuild_tree: bool = False):
        """Rebuild the library list FAST — just filenames, no tag reading.
        Tags are loaded in background by _start_tag_loader.

        `rebuild_tree`: the folder tree mirrors added_folders, not the
        library list — rebuilding it here (a recursive walk of the whole
        music collection) on every search keystroke was a major lag source.
        Callers that actually change folders pass True.
        """
        self.library_list.clear()
        tracks = self._get_filtered_sorted_library()
        for i, path in enumerate(tracks):
            # Use cached tags when available; fall back to filename (no I/O)
            if path in self._tag_cache:
                title, artist = self._tag_cache[path]
            else:
                title = os.path.splitext(os.path.basename(path))[0]
                artist = ""
            item = QListWidgetItem(_track_item_text(title, artist))
            item.setData(TrackRowDelegate.TITLE_ROLE, i + 1)
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(f"{title}\n{artist}\n{path}" if artist else path)
            self.library_list.addItem(item)
        self._highlight_playing_in_lists()
        # Reflect the active folder filter in the count label (search count
        # stays as-is; a folder filter deserves the same clarity).
        self._update_count_label()
        if rebuild_tree:
            self._rebuild_folder_tree()
        # Start background tag loading
        self._start_tag_loader(tracks)

    _tag_loader = None
    _tag_cache = {}
    _tag_loader_generation = 0   # bumped per loader; stale results are dropped

    def _start_tag_loader(self, tracks: list):
        """Load title/artist tags in background thread."""
        # Cancel previous loader
        if self._tag_loader is not None:
            self._tag_loader.cancel()
            self._tag_loader.wait(500)

        # Filter out cached tracks
        uncached = [p for p in tracks if p not in self._tag_cache]
        if not uncached:
            # All cached — update items immediately
            self._update_list_items_with_tags()
            return

        self._tag_loader_generation += 1
        self._tag_loader = TagLoaderThread(uncached, self)
        self._tag_loader.generation = self._tag_loader_generation
        self._tag_loader.tag_loaded.connect(self._on_tag_loaded)
        self._tag_loader.start()

    def _on_tag_loaded(self, path: str, title: str, artist: str):
        """Called when a tag is loaded in background."""
        # Drop results from a cancelled loader: a slow mutagen read on an old
        # thread must never overwrite a fresher value written after an edit.
        loader = self._tag_loader
        if loader is not None and getattr(loader, "generation", None) != self._tag_loader_generation:
            return
        self._tag_cache[path] = (title, artist)
        # Update the corresponding item in the list
        for lst in (self.library_list, self.favorites_list):
            for i in range(lst.count()):
                item = lst.item(i)
                if item.data(Qt.ItemDataRole.UserRole) == path:
                    item.setText(_track_item_text(title, artist))
                    item.setToolTip(f"{title}\n{artist}\n{path}")
                    lst.viewport().update()
                    break

    def _update_list_items_with_tags(self):
        """Update all list items with cached tags (fast, no I/O)."""
        for i in range(self.library_list.count()):
            item = self.library_list.item(i)
            path = item.data(Qt.ItemDataRole.UserRole)
            if path in self._tag_cache:
                title, artist = self._tag_cache[path]
                item.setText(_track_item_text(title, artist))
                item.setToolTip(f"{title}\n{artist}\n{path}")
        self.library_list.viewport().update()

    def _save_tag_cache(self):
        """Save tag cache for instant loading on next startup.

        v3.0: written to SQLite (store) when available; the legacy JSON
        copy is still refreshed so a downgrade to 2.x keeps working."""
        try:
            if getattr(self, "store", None) is not None and \
                    getattr(self.store, "db", None) is not None:
                self.store.persist_tags(self._tag_cache)
            p = config_path().parent / "tag_cache.json"
            cache_data = {path: list(tags) for path, tags in self._tag_cache.items()}
            p.write_text(json.dumps(cache_data, ensure_ascii=False), encoding="utf-8")
            restrict_file_permissions(p)
        except Exception as e:
            log.error(f"Tag cache save error: {e}")

    def _load_tag_cache(self):
        """Load tag cache (instant — no I/O for tag reading).

        v3.0: from SQLite when the store is live, otherwise the legacy
        validated JSON cache. Corrupted data must never crash the app.
        """
        if getattr(self, "store", None) is not None and \
                getattr(self.store, "db", None) is not None:
            try:
                self._tag_cache = {
                    k: (v[0] if isinstance(v, (tuple, list)) and v else "",
                        v[1] if isinstance(v, (tuple, list)) and len(v) > 1 else "")
                    for k, v in self.store.load_tag_cache().items()}
                return bool(self._tag_cache)
            except Exception:
                log.exception("tag cache load from store failed; JSON fallback")
        self._tag_cache = load_tag_cache_file(config_path().parent / "tag_cache.json")
        return bool(self._tag_cache)

    def _rebuild_folder_tree(self):
        """Build a tree of added folders and their subfolders.

        LAG FIX: subfolders load LAZILY — each branch gets a single
        placeholder child and real children are built on first expand
        (itemExpanded). Walking 1400+ directories and creating 1400+
        QTreeWidgetItems at once froze every search keystroke and scan.
        """
        if not hasattr(self, 'folder_tree'):
            return
        self.folder_tree.clear()
        dpr = get_dpr()
        folder_icon = QIcon(render_icon(Icon.FOLDER, 18, self.theme["gold"], dpr))
        add_icon = QIcon(render_icon(Icon.FOLDER_ADD, 18, self.theme["gold_light"], dpr))
        all_icon = QIcon(render_icon(Icon.LIBRARY, 18, self.theme["gold"], dpr))

        # "All Tracks" root item
        all_item = QTreeWidgetItem(["All Tracks"])
        all_item.setIcon(0, all_icon)
        all_item.setData(0, Qt.ItemDataRole.UserRole, "__all__")
        all_item.setFont(0, self._bold_font())
        self.folder_tree.addTopLevelItem(all_item)

        # "Add Folder..." action item
        add_item = QTreeWidgetItem(["Add Folder..."])
        add_item.setIcon(0, add_icon)
        add_item.setData(0, Qt.ItemDataRole.UserRole, "__add__")
        add_item.setForeground(0, QColor(self.theme["gold_light"]))
        self.folder_tree.addTopLevelItem(add_item)

        # User-added folders — children attach lazily on expand.
        for folder_path in self.added_folders:
            if not os.path.isdir(folder_path):
                continue
            folder_name = os.path.basename(folder_path) or folder_path
            root_item = QTreeWidgetItem([folder_name])
            root_item.setIcon(0, folder_icon)
            root_item.setData(0, Qt.ItemDataRole.UserRole, folder_path)
            root_item.setToolTip(0, folder_path)
            if _dir_has_subdirs(folder_path):
                root_item.addChild(QTreeWidgetItem(["…"]))   # lazy marker
            self.folder_tree.addTopLevelItem(root_item)
        # first-level folders stay collapsed (expandAll would force the
        # full lazy build — exactly what we avoid)

    def _on_folder_tree_expanded(self, item):
        """Replace the lazy marker with the branch's real subfolders."""
        if item.childCount() != 1 or item.child(0).text(0) != "…":
            return   # already materialized (or childless)
        item.takeChildren()
        dpr = get_dpr()
        folder_icon = QIcon(render_icon(Icon.FOLDER, 18, self.theme["gold"], dpr))
        dir_path = item.data(0, Qt.ItemDataRole.UserRole)
        for sub in _list_subdirs(dir_path):
            sub_path = os.path.join(dir_path, sub)
            sub_item = QTreeWidgetItem([sub])
            sub_item.setIcon(0, folder_icon)
            sub_item.setData(0, Qt.ItemDataRole.UserRole, sub_path)
            sub_item.setToolTip(0, sub_path)
            if _dir_has_subdirs(sub_path):
                sub_item.addChild(QTreeWidgetItem(["…"]))   # next lazy level
            item.addChild(sub_item)

    def _bold_font(self):
        f = self.font()
        f.setBold(True)
        return f

    def _on_folder_tree_click(self, item, column):
        """Handle folder tree click: filter tracks or add folder."""
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data == "__add__":
            self._on_add_folder()
            return
        if data == "__all__":
            self._folder_filter = None
            self._mark_selected_folder(None)
            self.search_filter = ""
            self.search_edit.setText("")
            self._rebuild_library_list()
            return
        # Filter tracks to those in this folder (path-boundary safe —
        # plain startswith would match sibling folders sharing a name prefix).
        # The filter is remembered so a later search/sort change keeps it
        # (it used to silently vanish on the first keystroke in the search box).
        folder = data
        self._folder_filter = folder
        self._mark_selected_folder(item)
        self.search_filter = ""
        self.search_edit.setText("")
        self._rebuild_library_list()

    def _mark_selected_folder(self, selected_item):
        """Visual selection without any rectangle: the picked folder swaps
        its closed-folder icon for an OPEN folder drawn in the theme accent
        (gold) and its label turns gold — Explorer-style, symmetric, and
        obvious at a glance."""
        dpr = get_dpr()
        t = self.theme
        closed = QIcon(render_icon(Icon.FOLDER, 18, t["gold"], dpr))
        opened = QIcon(render_icon(Icon.FOLDER_OPEN, 18, t["gold_light"], dpr))
        tree = self.folder_tree
        for i in range(tree.topLevelItemCount()):
            self._walk_folder_rows(tree.topLevelItem(i), selected_item,
                                   closed, opened)

    def _walk_folder_rows(self, item, selected_item, closed, opened):
        if item.data(0, Qt.ItemDataRole.UserRole) not in ("__all__", "__add__"):
            is_sel = (item is selected_item)
            item.setIcon(0, opened if is_sel else closed)
            item.setForeground(0, QColor(self.theme["gold_light"] if is_sel
                                         else self.theme["text"]))
        for i in range(item.childCount()):
            self._walk_folder_rows(item.child(i), selected_item,
                                   closed, opened)

    def _on_folder_tree_context_menu(self, pos):
        """Right-click menu on a folder tree entry: folder-wide actions."""
        item = self.folder_tree.itemAt(pos)
        if item is None:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data in ("__add__", "__all__", None):
            return
        folder = data
        menu = QMenu(self)
        dpr = get_dpr()
        t = self.theme
        act_play = QAction("Play Folder", self)
        act_play.setIcon(QIcon(render_icon(Icon.PLAY, 16, t["gold"], dpr)))
        menu.addAction(act_play)
        act_shuffle = QAction("Shuffle Folder", self)
        act_shuffle.setIcon(QIcon(render_icon(Icon.SHUFFLE, 16, t["gold"], dpr)))
        menu.addAction(act_shuffle)
        menu.addSeparator()
        act_queue = QAction("Add Folder to Queue", self)
        act_queue.setIcon(QIcon(render_icon(Icon.LIST_LINES, 16, t["gold"], dpr)))
        menu.addAction(act_queue)
        act_pl = QAction("Add Folder to Playlist...", self)
        act_pl.setIcon(QIcon(render_icon(Icon.PLAYLISTS, 16, t["gold"], dpr)))
        menu.addAction(act_pl)
        menu.addSeparator()
        act_explore = QAction("Open in Explorer", self)
        act_explore.setIcon(QIcon(render_icon(Icon.FOLDER, 16, t["gold"], dpr)))
        menu.addAction(act_explore)
        act_rescan = QAction("Rescan Folder", self)
        act_rescan.setIcon(QIcon(render_icon(Icon.REFRESH, 16, t["gold"], dpr)))
        menu.addAction(act_rescan)
        menu.addSeparator()
        act_remove = QAction("Remove Folder from Library", self)
        act_remove.setIcon(QIcon(render_icon(Icon.TRASH, 16, "#e05050", dpr)))
        menu.addAction(act_remove)
        act_delete = QAction("Delete Folder from Disk...", self)
        act_delete.setIcon(QIcon(render_icon(Icon.TRASH, 16, "#e05050", dpr)))
        menu.addAction(act_delete)
        chosen = menu.exec(self.folder_tree.mapToGlobal(pos))
        if chosen is None:
            return
        if chosen == act_play:
            self._play_folder(folder, shuffle=False)
        elif chosen == act_shuffle:
            self._play_folder(folder, shuffle=True)
        elif chosen == act_queue:
            self._queue_folder(folder)
        elif chosen == act_pl:
            self._folder_to_playlist(folder)
        elif chosen == act_explore:
            self._open_file_location(folder if os.path.isdir(folder)
                                     else os.path.dirname(folder))
        elif chosen == act_rescan:
            if os.path.isdir(folder):
                self._start_scan([folder], label="Rescanning folder...")
        elif chosen == act_remove:
            self._remove_folder_from_library(folder)
        elif chosen == act_delete:
            self._delete_folder_from_disk(folder)

    # ------------------------------------------------------------------
    # Folder-wide actions (folder tree right-click menu)
    # ------------------------------------------------------------------
    def _folder_tracks(self, folder: str) -> list:
        """All library tracks under `folder` (path-boundary safe)."""
        return [p for p in self.library if path_within(p, folder)]

    def _play_folder(self, folder: str, shuffle: bool):
        """Play / shuffle-play only the tracks inside `folder`."""
        if folder == "__all__":
            tracks = list(self.library)
        else:
            tracks = self._folder_tracks(folder)
        if not tracks:
            QMessageBox.information(self, APP_NAME,
                "This folder has no audio tracks in the library.")
            return
        if shuffle:
            random.shuffle(tracks)
        self._on_rail_clicked("library")
        self._play_from_list(tracks, 0, view=View.LIBRARY, shuffled=shuffle)
        name = os.path.basename(folder) or folder
        self.page_title.setText(f"{'Shuffle: ' if shuffle else ''}{name}")

    def _queue_folder(self, folder: str):
        """Append every folder track to the end of the current playlist."""
        tracks = self._folder_tracks(folder)
        if not tracks:
            QMessageBox.information(self, APP_NAME,
                "This folder has no audio tracks in the library.")
            return
        if not self.current_playlist:
            self._play_from_list(tracks, 0, view=self.current_view)
            return
        existing = set(self.current_playlist)
        added = [p for p in tracks if p not in existing]
        self.current_playlist.extend(added)
        self.shuffle_unplayed.update(added)
        self._show_toast(
            f"Queued {len(added)} track(s) from {os.path.basename(folder)}")

    def _folder_to_playlist(self, folder: str):
        """Create/extend a personal playlist with every track of the folder."""
        from playlist_dialog import PlaylistStore
        tracks = self._folder_tracks(folder)
        if not tracks:
            QMessageBox.information(self, APP_NAME,
                "This folder has no audio tracks in the library.")
            return
        default = os.path.basename(folder) or "New Playlist"
        name, ok = QInputDialog.getText(self, APP_NAME,
            "Playlist name:", text=PlaylistStore.sanitize_name(default))
        if not ok:
            return
        clean = PlaylistStore.valid_new_name(name, self.playlists)
        if not clean:
            existing = PlaylistStore.sanitize_name(name)
            if existing and existing.casefold() in \
                    {k.casefold() for k in self.playlists}:
                # Extend the existing playlist with the folder tracks
                pl = self.playlists[existing]
                added = [p for p in tracks if p not in pl]
                pl.extend(added)
                self._save_config()
                QMessageBox.information(self, APP_NAME,
                    f"Added {len(added)} track(s) to '{existing}'.")
            return
        self.playlists[clean] = list(tracks)
        self._save_config()
        QMessageBox.information(self, APP_NAME,
            f"Created '{clean}' with {len(tracks)} track(s).")

    def _remove_folder_from_library(self, folder: str):
        """Remove a folder and its tracks from the library (files untouched)."""
        name = os.path.basename(folder) or folder
        tracks = self._folder_tracks(folder)
        reply = QMessageBox.question(
            self, APP_NAME,
            f"Remove '{name}' from the library?\n\n"
            f"{len(tracks)} track(s) will leave the library. "
            f"The files on disk are NOT deleted.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.added_folders = [f for f in self.added_folders
                              if os.path.normcase(os.path.abspath(f)) !=
                              os.path.normcase(os.path.abspath(folder))]
        self._store_remove(tracks)
        for p in tracks:
            self.library.remove(p) if p in self.library else None
            self.favorites.discard(p)
            try:
                self.play_history.remove(p)
            except ValueError:
                pass
            try:
                self.redo_stack.remove(p)
            except ValueError:
                pass
            for name_pl in list(self.playlists):
                if p in self.playlists[name_pl]:
                    self.playlists[name_pl].remove(p)
        if self.current_track in tracks:
            self.audio.stop()
            self.current_track = None
            self.player_bar.update_play_button(False)
            self.player_bar.title_label.setMarqueeText("No track selected")
            self.player_bar.artist_label.setMarqueeText("—")
            self.now_title.setMarqueeText("No track selected")
            self.now_artist.setMarqueeText("Select a track to play")
        self.current_playlist = [p for p in self.current_playlist
                                 if p not in set(tracks)]
        self.current_index = 0 if self.current_playlist else -1
        if self._folder_filter and (path_within(self._folder_filter, folder)
                                    or self._folder_filter == folder):
            self._folder_filter = None
        self._rebuild_library_list()
        self._rebuild_favorites_list()
        self._rebuild_folder_tree()
        self._update_count_label()
        self._save_config()
        self._show_toast(f"Removed '{name}' from library")

    def _delete_folder_from_disk(self, folder: str):
        """Permanently delete a folder's audio files from disk (2-step confirm).
        The folder is removed from the library first, then the files."""
        name = os.path.basename(folder) or folder
        tracks = self._folder_tracks(folder)
        reply = QMessageBox.warning(
            self, APP_NAME,
            f"PERMANENTLY DELETE '{name}' from your computer?\n\n"
            f"{len(tracks)} audio file(s) will be deleted.\n"
            f"This does NOT go to the Recycle Bin and cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        reply2 = QMessageBox.warning(
            self, APP_NAME,
            f"Are you REALLY sure about deleting {len(tracks)} file(s)\n"
            f"inside:\n{folder}\n\nLast chance — this cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply2 != QMessageBox.StandardButton.Yes:
            return
        # Stop playback first if a doomed track is playing
        if self.current_track in tracks:
            self.audio.stop()
            self.current_track = None
            self.player_bar.update_play_button(False)
            self.player_bar.title_label.setMarqueeText("No track selected")
            self.player_bar.artist_label.setMarqueeText("—")
        self._remove_folder_from_library_quiet(folder)
        deleted = 0
        errors = []
        for p in tracks:
            try:
                if os.path.exists(p):
                    os.remove(p)
                    deleted += 1
            except OSError as ex:
                errors.append(f"{os.path.basename(p)}: {ex}")
        # Try to prune now-empty subdirectories up to the folder root itself
        self._prune_empty_dirs(folder)
        log.info(f"Folder delete: {deleted} file(s) removed from {folder}")
        if errors:
            QMessageBox.warning(self, APP_NAME,
                f"Deleted {deleted} file(s); some could not be deleted:\n\n" +
                "\n".join(errors[:8]))
        elif self.isVisible():
            self._show_toast(
                f"Deleted '{name}' ({deleted} files) from disk")

    def _remove_folder_from_library_quiet(self, folder: str):
        """Library bookkeeping for _delete_folder_from_disk (no prompts)."""
        tracks = set(self._folder_tracks(folder))
        self.added_folders = [f for f in self.added_folders
                              if os.path.normcase(os.path.abspath(f)) !=
                              os.path.normcase(os.path.abspath(folder))]
        self._store_remove(tracks)
        self.library = [p for p in self.library if p not in tracks]
        self.favorites -= tracks
        self.play_history = [p for p in self.play_history if p not in tracks]
        self.redo_stack = [p for p in self.redo_stack if p not in tracks]
        self.current_playlist = [p for p in self.current_playlist
                                 if p not in tracks]
        self.current_index = 0 if self.current_playlist else -1
        for name_pl in list(self.playlists):
            self.playlists[name_pl] = [p for p in self.playlists[name_pl]
                                       if p not in tracks]
        if self._folder_filter and self._folder_filter in tracks:
            self._folder_filter = None
        self._rebuild_library_list()
        self._rebuild_favorites_list()
        self._rebuild_folder_tree()
        self._update_count_label()
        self._save_config()

    @staticmethod
    def _prune_empty_dirs(folder: str):
        """Delete the folder and its now-empty ancestors' empty children."""
        try:
            for root, dirs, files in os.walk(folder, topdown=False):
                if not dirs and not files:
                    os.rmdir(root)
            if os.path.isdir(folder) and not os.listdir(folder):
                os.rmdir(folder)
        except OSError:
            pass  # non-empty or locked — leave it alone

    def _rebuild_favorites_list(self):
        """Rebuild favorites list — two-line rows via TrackRowDelegate."""
        self.favorites_list.clear()
        tracks = self._get_filtered_sorted_favorites()
        for i, path in enumerate(tracks):
            if path in self._tag_cache:
                title, artist = self._tag_cache[path]
                item = QListWidgetItem(_track_item_text(title, artist))
                item.setToolTip(f"{title}\n{artist}\n{path}")
            else:
                fname = os.path.splitext(os.path.basename(path))[0]
                item = QListWidgetItem(_track_item_text(fname, ""))
                item.setToolTip(path)
            item.setData(TrackRowDelegate.TITLE_ROLE, i + 1)
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.favorites_list.addItem(item)
        self._highlight_playing_in_lists()
        self._update_count_label()

    def _highlight_playing_in_lists(self):
        # TrackRowDelegate renders the "playing" glow from this role —
        # font/background tweaks on the item itself are ignored by the delegate.
        for lst in (self.library_list, self.favorites_list):
            for i in range(lst.count()):
                item = lst.item(i)
                is_playing = (self.current_track is not None and
                              item.data(Qt.ItemDataRole.UserRole) == self.current_track)
                item.setData(Qt.ItemDataRole.UserRole + 2, bool(is_playing))
            lst.viewport().update()

    def _on_context_menu(self, pos):
        lst = self.library_list if self.current_view == View.LIBRARY else self.favorites_list
        item = lst.itemAt(pos)
        if not item:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        menu = QMenu(self)
        dpr = get_dpr()
        t = self.theme
        act_play = QAction("Play", self)
        act_play.setIcon(QIcon(render_icon(Icon.PLAY, 16, t["gold"], dpr)))
        act_play.triggered.connect(lambda: self._play_track_from_list(path))
        menu.addAction(act_play)
        menu.addSeparator()
        if path in self.favorites:
            act_unfav = QAction("Remove from Favorites", self)
            act_unfav.setIcon(QIcon(render_icon(Icon.HEART_FILLED, 16, t["gold"], dpr)))
            act_unfav.triggered.connect(lambda: self._toggle_favorite(path))
            menu.addAction(act_unfav)
        else:
            act_fav = QAction("Add to Favorites", self)
            act_fav.setIcon(QIcon(render_icon(Icon.HEART, 16, t["gold"], dpr)))
            act_fav.triggered.connect(lambda: self._toggle_favorite(path))
            menu.addAction(act_fav)
        menu.addSeparator()
        act_remove = QAction("Remove from Library", self)
        act_remove.setIcon(QIcon(render_icon(Icon.TRASH, 16, "#e05050", dpr)))
        act_remove.triggered.connect(lambda: self._remove_from_library(path))
        menu.addAction(act_remove)
        menu.addSeparator()
        act_add_pl = QAction("Add to Playlist...", self)
        act_add_pl.setIcon(QIcon(render_icon(Icon.LIBRARY, 16, t["gold"], dpr)))
        act_add_pl.triggered.connect(
            lambda: self._add_to_playlist(path))
        menu.addAction(act_add_pl)
        menu.addSeparator()
        act_open_loc = QAction("Open File Location", self)
        act_open_loc.setIcon(QIcon(render_icon(Icon.FOLDER, 16, t["gold"], dpr)))
        act_open_loc.triggered.connect(lambda: self._open_file_location(path))
        menu.addAction(act_open_loc)
        act_props = QAction("Properties", self)
        act_props.setIcon(QIcon(render_icon(Icon.SETTINGS, 16, t["muted"], dpr)))
        act_props.triggered.connect(lambda: self._show_track_properties(path))
        menu.addAction(act_props)
        # ---- v3 additions: Play Next / queue / rating ------------------
        menu.addSeparator()
        act_next = QAction("Play Next", self)
        act_next.setIcon(QIcon(render_icon(Icon.NEXT, 16, t["gold"], dpr)))
        act_next.triggered.connect(lambda: self._insert_next([path]))
        menu.addAction(act_next)
        act_queue = QAction("Add to Queue", self)
        act_queue.setIcon(QIcon(render_icon(Icon.QUEUE, 16, t["gold"], dpr)))
        act_queue.triggered.connect(lambda: self._append_queue([path]))
        menu.addAction(act_queue)
        menu.addSeparator()
        rate_menu = QMenu("Rate", self)
        rate_menu.setIcon(QIcon(render_icon(Icon.STAR, 16, t["gold"], dpr)))
        current_rating = 0
        try:
            if getattr(self, "store", None) is not None:
                tr = self.store.db.get_track(path)
                current_rating = (tr or {}).get("rating", 0) or 0
        except Exception:
            current_rating = 0
        for stars in range(1, 6):
            ra = QAction("★" * stars, self)
            ra.setIcon(QIcon(render_icon(
                Icon.STAR_FILLED if stars <= current_rating else Icon.STAR,
                15, t["gold"], dpr)))
            ra.triggered.connect(lambda _c=False, st=stars:
                                 self._set_track_rating(path, st))
            rate_menu.addAction(ra)
        if current_rating:
            clear_ra = QAction("Clear rating", self)
            clear_ra.triggered.connect(lambda: self._set_track_rating(path, 0))
            rate_menu.addAction(clear_ra)
        menu.addMenu(rate_menu)
        # Use cached tags for display if available
        if path in self._tag_cache:
            title, artist = self._tag_cache[path]
            menu.setWindowTitle(f"{title} — {artist}")
        menu.exec(lst.mapToGlobal(pos))

    def _add_to_playlist(self, path):
        """Right-click helper — delegate to the playlist picker dialog."""
        try:
            from playlist_dialog import add_track_to_playlist_dialog
            add_track_to_playlist_dialog(self, path)
        except Exception as e:
            log.error(f"Add-to-playlist failed: {e}")

    def _open_file_location(self, path):
        """Reveal the track's file in the OS file manager."""
        from gm.platforms import open_file_location
        open_file_location(path)

    def _show_track_properties(self, path):
        """Show the technical properties dialog for a track."""
        try:
            from trackinfo import get_track_info
            from track_info_dialog import TrackInfoDialog
            info = get_track_info(path)
            dlg = TrackInfoDialog(info, self.theme, self)
            dlg.exec()
        except Exception as e:
            log.error(f"Track properties failed: {e}")
            QMessageBox.warning(self, APP_NAME, f"Could not read file info:\n{e}")

    def _on_bar_info_clicked(self):
        """Clicking the player-bar cover / title / artist opens the playing
        track's info dialog."""
        if self.current_track:
            self._show_track_properties(self.current_track)

    # ------------------------------------------------------------------
    # Track menu (three-line button) for the CURRENTLY PLAYING track
    # ------------------------------------------------------------------
    def _show_more_menu(self):
        """⋯ overflow menu on the player bar: Audio Output, A-B Repeat,
        Playback Speed, Show in Folder and Track Options."""
        menu = QMenu(self)
        dpr = get_dpr()
        t = self.theme
        # ---- Audio output submenu (System Default / HDMI / S/PDIF / ...) --
        out_menu = QMenu("Audio Output", menu)
        out_menu.setIcon(QIcon(render_icon(Icon.VOLUME_HIGH, 16, t["gold"], dpr)))
        current_id = str(getattr(self, "audio_output_id", "") or "")
        grp_devices = []
        act_def = QAction("System Default", out_menu)
        act_def.setCheckable(True)
        act_def.setChecked(current_id == "")
        act_def.triggered.connect(lambda: self._switch_audio_output(""))
        out_menu.addAction(act_def)
        out_menu.addSeparator()
        try:
            from audio_output import list_output_devices
            for name, dev_id in list_output_devices():
                act = QAction(name, out_menu)
                act.setCheckable(True)
                act.setChecked(dev_id == current_id)
                act.triggered.connect(
                    lambda _=False, did=dev_id: self._switch_audio_output(did))
                out_menu.addAction(act)
                grp_devices.append(act)
        except Exception as e:
            log.warning(f"output enumeration failed: {e}")
        if not grp_devices:
            act_none = QAction("No other devices found", out_menu)
            act_none.setEnabled(False)
            out_menu.addAction(act_none)
        menu.addMenu(out_menu)
        menu.addSeparator()
        act_ab = QAction("A-B Repeat", self)
        act_ab.setIcon(QIcon(render_icon(Icon.REPEAT_ONE, 16, t["gold"], dpr)))
        act_ab.triggered.connect(self._on_ab_button)
        menu.addAction(act_ab)
        act_rate = QAction("Playback Speed...", self)
        act_rate.setIcon(QIcon(render_icon(Icon.SPEED, 16, t["gold"], dpr)))
        act_rate.triggered.connect(
            lambda: self._cycle_playback_rate(self.player_bar.more_btn))
        menu.addAction(act_rate)
        act_loc = QAction("Show in Folder", self)
        act_loc.setIcon(QIcon(render_icon(Icon.FOLDER, 16, t["gold"], dpr)))
        act_loc.triggered.connect(self._share_current_track)
        menu.addAction(act_loc)
        act_opts = QAction("Track Options", self)
        act_opts.setIcon(QIcon(render_icon(Icon.LIST_LINES, 16, t["gold"], dpr)))
        act_opts.triggered.connect(self._show_track_menu)
        menu.addAction(act_opts)
        btn = self.player_bar.more_btn
        menu.exec(btn.mapToGlobal(QPoint(btn.width() // 2 - 60, -menu.sizeHint().height() - 8)))

    def _switch_audio_output(self, device_id: str):
        """Route playback to a named output device (menu pick) and persist."""
        try:
            from audio_output import apply_output_device
            ok = apply_output_device(self.audio, device_id)
            self.audio_output_id = device_id if ok else ""
            if ok and self.tray and self.tray.isVisible():
                label = device_id or "System Default"
                if device_id:
                    try:
                        from audio_output import list_output_devices
                        for name, did in list_output_devices():
                            if did == device_id:
                                label = name
                                break
                    except Exception:
                        pass
                self._show_toast(f"Audio output: {label}")
        except Exception as e:
            log.warning(f"audio output switch failed: {e}")
        self._save_config_debounced()

    def _share_current_track(self):
        """Reveal the playing track in Explorer (file pre-selected)."""
        if not self.current_track:
            QMessageBox.information(self, APP_NAME, "No track is playing.")
            return
        self._open_file_location(self.current_track)

    def _show_track_menu(self):
        """Popup menu for the current track: Delete / Properties / Open in
        Folder (+ Edit Tags). Mirrors the track context menu."""
        if not self.current_track:
            QMessageBox.information(self, APP_NAME, "No track is playing.")
            return
        path = self.current_track
        menu = QMenu(self)
        dpr = get_dpr()
        t = self.theme
        act_delete = QAction("Delete", self)
        act_delete.setIcon(QIcon(render_icon(Icon.TRASH, 16, "#e05050", dpr)))
        act_delete.triggered.connect(lambda: self._delete_track_from_disk(path))
        menu.addAction(act_delete)
        menu.addSeparator()
        act_props = QAction("Properties", self)
        act_props.setIcon(QIcon(render_icon(Icon.SETTINGS, 16, t["muted"], dpr)))
        act_props.triggered.connect(lambda: self._show_track_properties(path))
        menu.addAction(act_props)
        act_folder = QAction("Open in Folder", self)
        act_folder.setIcon(QIcon(render_icon(Icon.FOLDER, 16, t["gold"], dpr)))
        act_folder.triggered.connect(lambda: self._open_file_location(path))
        menu.addAction(act_folder)
        act_edit = QAction("Edit Tags...", self)
        act_edit.setIcon(QIcon(render_icon(Icon.LYRICS, 16, t["gold_light"], dpr)))
        act_edit.triggered.connect(lambda: self._edit_tags(path))
        menu.addAction(act_edit)
        if hasattr(self, "player_bar") and hasattr(self.player_bar, "track_menu_btn"):
            btn = self.player_bar.track_menu_btn
            menu.exec(btn.mapToGlobal(QPoint(btn.width() // 2 - 60, btn.height() + 6)))
        else:
            from PyQt6.QtGui import QCursor
            menu.exec(QCursor.pos())

    def _delete_track_from_disk(self, path):
        """Permanently delete the audio file from disk (with confirmation).
        The track is also removed from library/history/playlists."""
        title = os.path.basename(path)
        reply = QMessageBox.warning(
            self, APP_NAME,
            f"Permanently delete this file from your computer?\n\n"
            f"{title}\n\nThis cannot be undone (does NOT go to Recycle Bin).",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            was_playing = path == self.current_track
            if was_playing:
                self.audio.stop()
                self.current_track = None
                self.player_bar.update_play_button(False)
                self.player_bar.title_label.setMarqueeText("No track selected")
                self.player_bar.artist_label.setMarqueeText("—")
                if getattr(self, "tray_menu_widget", None) is not None:
                    self.tray_menu_widget.update_now_playing()
            # Library bookkeeping (also purges history entries)
            self._remove_from_library(path)
            # Playlists cleanup
            changed = False
            for name in list(self.playlists):
                if path in self.playlists[name]:
                    self.playlists[name].remove(path)
                    changed = True
            if changed:
                self._save_config()
            # Finally remove the physical file
            os.remove(path)
            log.info(f"Deleted file from disk: {path}")
            self._show_toast(f"Deleted {title}")
        except FileNotFoundError:
            QMessageBox.warning(self, APP_NAME, "File was already gone.")
        except OSError as e:
            log.error(f"Disk delete failed: {e}")
            QMessageBox.critical(self, APP_NAME,
                f"Could not delete file:\n{e}\n\n(Is it locked by another program?)")

    def _edit_tags(self, path):
        """Open the real tag editor for a file."""
        try:
            from tag_editor import TagEditorDialog
            dlg = TagEditorDialog(path, self.theme, self)
            if dlg.exec():
                # File changed on disk → refresh caches so the UI reflects it
                self._tag_cache.pop(path, None)
                coverart.clear_cache()
                self._start_tag_loader([path])
                if path == self.current_track:
                    title, artist = extract_title_artist(path)
                    self.now_title.setMarqueeText(title)
                    self.now_artist.setMarqueeText(artist)
                    self.player_bar.title_label.setMarqueeText(title)
                    self.player_bar.artist_label.setMarqueeText(artist)
                self._load_cover_async(path)
                self._rebuild_library_list()
        except Exception as e:
            log.error(f"Tag editor failed: {e}")
            QMessageBox.warning(self, APP_NAME, f"Tag editor failed:\n{e}")

    # ------------------------------------------------------------------
    # Lyrics panel (local .lrc / embedded / online via lrclib.net)
    # ------------------------------------------------------------------
    def _toggle_lyrics_panel(self):
        """v3 lyrics panel: bilingual view, quality detection, editor,
        remembered per-track offset, confirmation-gated online replacement."""
        try:
            prefer_v3 = True
            try:
                from gm.lyrics_flow import LyricsPanel3   # noqa: F401
            except Exception:
                prefer_v3 = False
            if prefer_v3:
                if getattr(self, "_lyrics_panel3", None) is None:
                    from gm.lyrics_flow import LyricsPanel3
                    self._lyrics_panel3 = LyricsPanel3(self)
                    self._lyrics_panel3.seek_requested.connect(
                        lambda ms: self.audio.set_position(int(ms)))
                panel = self._lyrics_panel3
                panel.set_theme(self.theme)
                if panel.isVisible():
                    panel.hide()
                    return
                panel.load_for_track(
                    self.current_track,
                    allow_online=bool(self.prefs.get("lyrics_online", True)))
                gp = self.mapToGlobal(self.rect().bottomLeft())
                panel.move(gp.x() + 80, max(80, gp.y() - 460))
                panel.show()
                panel.raise_()
                return
            from lyrics_panel import LyricsPanel
            if getattr(self, "_lyrics_panel", None) is None:
                self._lyrics_panel = LyricsPanel(self)
            self._lyrics_panel._apply_style()
            if self._lyrics_panel.isVisible():
                self._lyrics_panel.hide()
                return
            self._lyrics_panel.load_for_track(
                self.current_track,
                allow_online=bool(getattr(self, "lyrics_online_enabled", True)))
            gp = self.mapToGlobal(self.rect().bottomLeft())
            self._lyrics_panel.move(gp.x() + 80, gp.y() - 420)
            self._lyrics_panel.show()
            self._lyrics_panel.raise_()
        except Exception as e:
            log.error(f"Lyrics panel failed: {e}")
            QMessageBox.warning(self, APP_NAME, f"Lyrics failed:\n{e}")

    def _on_lyrics_position_tick(self, pos_ms: int):
        """Called from _ui_tick to sync the active lyric line."""
        panel = getattr(self, "_lyrics_panel", None)
        if panel is not None and panel.isVisible() and self.current_track:
            panel.sync_position(pos_ms)
        p3 = getattr(self, "_lyrics_panel3", None)
        if p3 is not None and p3.isVisible() and self.current_track:
            p3.sync_position(pos_ms)
        fw = getattr(self, "_floating_lyrics_w", None)
        if fw is not None and fw.isVisible() and self.current_track:
            try:
                fw.sync_position(pos_ms)
            except Exception:
                pass
        fs = getattr(self, "_fullscreen_player", None)
        if fs is not None and fs.isVisible() and self.current_track:
            try:
                fs.sync_lyrics(pos_ms)
            except Exception:
                pass

    def _play_track_from_list(self, path):
        lst = self.library_list if self.current_view == View.LIBRARY else self.favorites_list
        tracks = []
        target_idx = 0
        for i in range(lst.count()):
            it = lst.item(i)
            p = it.data(Qt.ItemDataRole.UserRole)
            tracks.append(p)
            if p == path:
                target_idx = i
        self._play_from_list(tracks, target_idx, view=self.current_view)

    def _toggle_favorite(self, path):
        if path in self.favorites:
            self.favorites.discard(path)
        else:
            self.favorites.add(path)
        if path == self.current_track:
            self.player_bar.update_like_button(path in self.favorites)
        self._rebuild_favorites_list()
        self._save_config()

    def _remove_from_library(self, path):
        if path in self.library:
            self.library.remove(path)
        self._store_remove([path])
        self.favorites.discard(path)
        # Keep history consistent — a removed track must not come back via
        # Previous/Next history navigation
        try:
            self.play_history.remove(path)
        except ValueError:
            pass
        try:
            self.redo_stack.remove(path)
        except ValueError:
            pass
        if path != self.current_track:
            # Removing a non-playing track: drop it from the playlist and keep
            # current_index pointing at the SAME playing track.
            try:
                pl_pos = self.current_playlist.index(path)
                self.current_playlist.pop(pl_pos)
                if pl_pos < self.current_index:
                    self.current_index -= 1
                if not self.current_playlist:
                    self.current_index = -1
            except ValueError:
                pass  # not in the active playlist — nothing to fix
        if path == self.current_track:
            self.audio.stop()
            self.current_track = None
            # Remove the dead entry from the playlist too, keeping the next
            # track at the same index (or clamped to the new last position)
            if path in self.current_playlist:
                pl_pos = self.current_playlist.index(path)
                self.current_playlist.pop(pl_pos)
                self.current_index = min(max(0, pl_pos), max(0, len(self.current_playlist) - 1))
                if not self.current_playlist:
                    self.current_index = -1
            else:
                self.current_index = -1
            self.player_bar.title_label.setMarqueeText("No track selected")
            self.player_bar.artist_label.setMarqueeText("—")
            self.now_title.setMarqueeText("No track selected")
            self.now_artist.setMarqueeText("Select a track to play")
            if getattr(self, "tray_menu_widget", None) is not None:
                self.tray_menu_widget.update_now_playing()
        self._rebuild_library_list()
        self._rebuild_favorites_list()
        self._save_config()

    def _on_play_all(self):
        tracks = self._get_filtered_sorted_library()
        if not tracks:
            QMessageBox.information(self, APP_NAME, "Library is empty. Add a folder first.")
            return
        self._on_rail_clicked("library")
        self._play_from_list(tracks, 0, view=View.LIBRARY)

    def _on_shuffle_all(self):
        tracks = self._get_filtered_sorted_library() if self.current_view == View.LIBRARY else self._get_filtered_sorted_favorites()
        if not tracks:
            QMessageBox.information(self, APP_NAME, "No tracks to play.")
            return
        random.shuffle(tracks)
        self._play_from_list(tracks, 0, view=self.current_view, shuffled=True)
        self.shuffle = True
        self.player_bar.update_shuffle_button(True)

    def _on_track_double_clicked(self, item: QListWidgetItem):
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path or not os.path.exists(path):
            return
        lst = self.library_list if self.current_view == View.LIBRARY else self.favorites_list
        tracks = []
        target_idx = 0
        for i in range(lst.count()):
            it = lst.item(i)
            p = it.data(Qt.ItemDataRole.UserRole)
            tracks.append(p)
            if p == path:
                target_idx = i
        self._play_from_list(tracks, target_idx, view=self.current_view)

    def _play_from_list(self, tracks, index, view=View.LIBRARY, shuffled=False):
        if not tracks:
            return
        self.current_playlist = list(tracks)
        self.current_index = index
        self.shuffle = shuffled
        self.player_bar.update_shuffle_button(shuffled)
        # New listening session — old history belongs to the previous queue
        # unless we're already inside it (double-click during history nav).
        if not self._nav_via_history:
            self.play_history.clear()
            self.redo_stack.clear()
            # Smart shuffle: fresh queue = everything is unplayed again
            # except whatever is about to start.
            self.shuffle_unplayed = set(tracks)
        self._load_and_play_current()

    _HISTORY_MAX = 200  # bound memory; deep enough for any listening session

    def _push_history(self, path):
        """Record a track start in history, clearing the redo branch —
        same semantics as browser back/forward.

        Skipped entirely while navigating via history (_nav_via_history):
        _on_prev/_on_next already maintain history/redo themselves."""
        if not path or self._nav_via_history:
            return
        # Avoid consecutive duplicates (repeat-one restarts, seeks)
        if self.play_history and self.play_history[-1] == path:
            return
        self.play_history.append(path)
        del self.play_history[:-self._HISTORY_MAX]
        self.redo_stack.clear()

    def _load_and_play_current(self):
        if not self.current_playlist or self.current_index < 0:
            return
        path = self.current_playlist[self.current_index]
        self._push_history(path)
        self.shuffle_unplayed.discard(path)   # smart shuffle: this one had its turn
        if getattr(self, "fx", None):
            self.fx.clear_ab()                # A-B loop is per-track
        # Listen metering restarts per track (70% credit rule — see stats)
        self._finalize_listen_credit()
        self._listened_ms = 0
        self.current_track = path
        # Use cached tags if available, otherwise extract (just for current track)
        if path in self._tag_cache:
            title, artist = self._tag_cache[path]
        else:
            try:
                title, artist = extract_title_artist(path)
                self._tag_cache[path] = (title, artist)
            except Exception:
                title = os.path.splitext(os.path.basename(path))[0]
                artist = "Unknown Artist"
                self._tag_cache[path] = (title, artist)
        self.now_title.setMarqueeText(title)
        self.now_artist.setMarqueeText(artist)
        self.player_bar.title_label.setMarqueeText(title)
        self.player_bar.artist_label.setMarqueeText(artist)
        self.player_bar.time_total.setText("0:00")
        self.player_bar.time_current.setText("0:00")
        self.player_bar.seek_slider.setValue(0)
        self.player_bar.update_like_button(path in self.favorites)
        self._load_cover_async(path)
        self._highlight_playing_in_lists()
        # Load and play immediately (simplified audio backend)
        self.audio.set_volume(self.player_bar.vol_slider.value())
        self._start_track_analytics(path)
        self.audio.load_and_play(path)
        if getattr(self, "fx", None):
            self.fx.fade_in_from_silence()
            rate = self.fx.current_rate()
            if abs(rate - 1.0) > 1e-6:
                self.fx.set_rate(rate)   # re-apply speed to the new media item
        # Update mini player
        if self.mini_player and self.mini_player.isVisible():
            pm = self.player_bar._cover_thumb_pm
            self.mini_player.update_track(title, artist, pm)
        # Keep the tray popup header in sync (it stays alive while hidden)
        if getattr(self, "tray_menu_widget", None) is not None:
            self.tray_menu_widget.update_now_playing(
                title, artist, self.player_bar._cover_thumb_pm)

    def _on_track_finished(self):
        """Called when a track finishes naturally."""
        self._finalize_listen_credit()
        self._on_next()

    def _finalize_listen_credit(self):
        """Apply the 70% rule for the track that just ended (natural finish
        or manual skip): credit ONE play when >70% was actually heard."""
        if not getattr(self, "stats", None) or not self.current_track:
            return
        dur_ms = self.audio.duration()
        listened = float(getattr(self, "_listened_ms", 0)) / 1000.0
        title, artist = self._tag_cache.get(self.current_track, ("", ""))
        self.stats.credit_track(
            self.current_track,
            listened,
            artist or "",
            duration_seconds=dur_ms / 1000.0 if dur_ms else 0.0,
            listened_seconds=listened)
        self._flush_stats()

    def _flush_stats(self):
        if getattr(self, "stats", None) and self.stats._dirty:
            self.stats.save()

    # ------------------------------------------------------------------
    # In-app toast notifications (replace tray balloons — no spam, no
    # focus stealing, theme-aware)
    # ------------------------------------------------------------------
    _TOAST_MS = 3200

    def _ensure_toast_widget(self):
        if getattr(self, "_toast_label", None) is None:
            lbl = QLabel(self)
            lbl.setObjectName("AppToast")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setWordWrap(True)
            lbl.setMaximumWidth(560)
            lbl.setVisible(False)
            self._toast_label = lbl
            self._toast_effect = QGraphicsOpacityEffect(lbl)
            self._toast_effect.setOpacity(0.0)
            lbl.setGraphicsEffect(self._toast_effect)
            self._toast_anim = QVariantAnimation(self)
            self._toast_anim.setDuration(180)
            self._toast_anim.valueChanged.connect(
                self._toast_effect.setOpacity)
            self._toast_timer = QTimer(self)
            self._toast_timer.setSingleShot(True)
            self._toast_timer.timeout.connect(self._fade_out_toast)
        return self._toast_label

    def _show_toast(self, text: str, error: bool = False,
                    ms: int = _TOAST_MS):
        """Bottom-center in-app toast pill.

        Replaces tray balloons for all operational notifications. No-op
        (log only) while the window is hidden — an in-app toast would be
        invisible anyway and balloons were dropped to stop the spam.
        Honors the toast_enabled setting.
        """
        if not getattr(self, "_toast_enabled", True):
            return
        if not self.isVisible():
            log.info(f"toast suppressed (window hidden): {text}")
            return
        try:
            lbl = self._ensure_toast_widget()
            t = self.theme
            border = t.get("danger", "#a33") if error else t.get("border", "#555")
            lbl.setStyleSheet(
                f"QLabel#AppToast {{ background: {t.get('panel_bg', '#222')};"
                f" color: {t.get('text', '#eee')}; border: 1px solid {border};"
                f" border-radius: 10px; padding: 8px 14px; font-size: 12px; }}")
            lbl.setText(text)
            lbl.adjustSize()
            self._position_toast()
            self._toast_anim.stop()
            self._toast_anim.setStartValue(0.0)
            self._toast_anim.setEndValue(1.0)
            self._toast_effect.setOpacity(0.0)
            lbl.show()
            lbl.raise_()
            self._toast_anim.start()
            self._toast_timer.start(ms)
        except Exception as e:
            log.debug(f"toast error: {e}")

    def _fade_out_toast(self):
        try:
            anim = self._toast_anim
            anim.stop()
            try:
                anim.finished.disconnect()
            except TypeError:
                pass   # no handlers connected
            anim.setStartValue(1.0)
            anim.setEndValue(0.0)
            anim.finished.connect(self._finish_toast_hide)
            anim.start()
        except Exception:
            self._finish_toast_hide()

    def _finish_toast_hide(self):
        try:
            self._toast_label.hide()
        except Exception:
            pass

    def _position_toast(self):
        """Center the toast above the player bar."""
        lbl = getattr(self, "_toast_label", None)
        if lbl is None:
            return
        bottom = self.height() - 96          # fallback: above the bottom edge
        pb = getattr(self, "player_bar", None)
        if pb is not None:
            bottom = pb.geometry().top() - 12
        x = max(8, (self.width() - lbl.width()) // 2)
        y = max(8, bottom - lbl.height())
        lbl.move(x, y)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if getattr(self, "_toast_label", None) is not None and \
                self._toast_label.isVisible():
            self._position_toast()
        b = getattr(self, "_update_banner", None)
        if b is not None:
            b.reposition_if_visible()

    # ------------------------------------------------------------------
    # In-app auto-update (engine in updater.py; UI in update_dialog.py)
    # ------------------------------------------------------------------
    _UPDATE_CHECK_DELAY_MS = 25_000        # first check ~25 s after boot
    _UPDATE_CHECK_INTERVAL_S = 24 * 3600   # at most once per 24 h

    def updates_dir(self):
        return config_path().parent / "updates"

    def _schedule_update_check(self):
        if getattr(self, "_smoke", False):
            return
        QTimer.singleShot(self._UPDATE_CHECK_DELAY_MS,
                          self._maybe_auto_update_check)

    def _maybe_auto_update_check(self):
        if not bool(getattr(self, "updates_enabled", True)):
            return
        now = time.time()
        if now - float(getattr(self, "update_last_check", 0) or 0) \
                < self._UPDATE_CHECK_INTERVAL_S:
            return
        self._run_update_check(manual=False)

    def _run_update_check(self, manual: bool):
        """Spawn a background probe. Manual checks always give explicit
        feedback; automatic ones stay silent unless an update shows up."""
        if getattr(self, "_update_worker", None) is not None:
            return
        self.update_last_check = time.time()
        self._save_config_debounced()
        self._update_worker = UpdateCheckWorker(
            APP_VERSION, etag=getattr(self, "update_etag", "") or None,
            parent=self)
        self._update_worker.finished_with.connect(
            lambda r, m=manual: self._on_update_check_done(r, m))
        self._update_worker.start()

    def _on_update_check_done(self, result: dict, manual: bool):
        self._update_worker = None
        status = result.get("status", "error")
        if status == "update_available":
            self._last_update_result = result
            self.update_etag = result.get("etag") or getattr(
                self, "update_etag", "")
            self._save_config_debounced()
            release = result["release"]
            if manual:
                self._hide_update_banner()
                UpdateDialog(self, release, result["asset"]).exec()
                return
            if (release["version"] != getattr(self, "update_skip_version", "")
                    and time.time() >= float(
                        getattr(self, "update_snooze_until", 0) or 0)):
                self._show_update_banner(release, result["asset"])
            return
        if manual:
            # Explicit feedback for the Settings → About button.
            if status in ("up_to_date", "not_modified"):
                QMessageBox.information(
                    self, APP_NAME,
                    f"You're up to date — Golden Music v{APP_VERSION} is "
                    "the latest version.")
            elif status == "rate_limited":
                QMessageBox.warning(
                    self, APP_NAME,
                    "GitHub is rate-limiting update checks right now. "
                    "Please try again later.")
            else:
                QMessageBox.warning(
                    self, APP_NAME,
                    "Could not check for updates (offline or GitHub "
                    "unreachable). Please try again later.")

    def _show_update_banner(self, release, asset):
        self._update_banner.show_for(release)

    def _hide_update_banner(self):
        self._update_banner.setVisible(False)

    def _open_update_dialog(self):
        r = getattr(self, "_last_update_result", None)
        if not r or r.get("status") != "update_available":
            return
        self._hide_update_banner()
        UpdateDialog(self, r["release"], r["asset"]).exec()

    def snooze_update(self):
        self.update_snooze_until = time.time() + self._UPDATE_CHECK_INTERVAL_S
        self._hide_update_banner()
        self._save_config_debounced()

    def skip_update_version(self, version: str):
        self.update_skip_version = str(version or "")
        self._hide_update_banner()
        self._save_config_debounced()

    def set_pending_install(self, installer_path: str):
        """'Install when I quit' — the verified installer runs at exit."""
        self._pending_install_path = str(installer_path)

    def launch_update_and_quit(self, installer_path: str) -> bool:
        """Full self-update: detached installer + graceful exit."""
        if not updater.launch_installer(installer_path):
            return False
        self._quit()
        return True

    def _handle_update_startup(self):
        """Post-boot update bookkeeping: clean stale downloads, evaluate
        the pending marker, celebrate a finished update, schedule the
        first automatic check."""
        try:
            updater.clean_stale_downloads(self.updates_dir())
            outcome = updater.evaluate_pending_marker(
                self.updates_dir(), APP_VERSION, self._updated_flag)
            if self._updated_flag:
                self._show_toast(f"Updated to v{APP_VERSION} ✓")
            if outcome and outcome.get("kind") == "incomplete":
                QMessageBox.warning(
                    self, APP_NAME,
                    f"The last update to v{outcome['target']} didn't "
                    "complete. Your data is untouched.\n\n"
                    f"Log: {config_path().parent / 'goldenmusic.log'}\n"
                    f"Download again from: {updater.RELEASES_PAGE_URL}")
        except Exception as e:
            log.debug(f"update startup handling failed: {e}")
        self._schedule_update_check()

    # Track-change toast (in-app only)
    # ------------------------------------------------------------------
    _TOAST_COOLDOWN_S = 4

    def _maybe_toast_track_change(self):
        """Gentle 'now playing' feedback while the window is visible.

        The old tray balloon variant targeted the hidden window; balloons
        are gone now, and an in-app toast would be invisible while hidden,
        so hidden changes are logged only.
        """
        if not self.current_track or not self.isVisible():
            self._last_toast_key = None
            return
        key = (self.current_track,
               self.audio.state() == AudioBackend.STATE_PLAYING)
        if key == self._last_toast_key:
            return
        now = time.time()
        if now - getattr(self, "_last_toast_ts", 0) < self._TOAST_COOLDOWN_S:
            return
        self._last_toast_key = key
        self._last_toast_ts = now
        title, artist = self._tag_cache.get(
            self.current_track, ("", ""))
        if not title:
            # Never run mutagen on the UI thread for a toast — fall back
            # to the filename stem instead.
            title = os.path.splitext(os.path.basename(self.current_track))[0]
        body = f"{title} — {artist}" if artist else title
        self._show_toast(f"♪ {body}")

    def _on_media_loaded(self):
        """Deprecated — kept for compatibility."""
        pass

    def _load_cover_async(self, path):
        # Remember which request is current so a stale loader finishing late
        # can't overwrite the new track's cover.
        self._cover_request = path
        if self._cover_loader is not None:
            try:
                self._cover_loader.cover_ready.disconnect(self._on_cover_ready)
            except TypeError:
                pass  # already disconnected
            if self._cover_loader.isRunning():
                # Don't deleteLater() a still-running QThread — that can hard-
                # crash ("QThread: Destroyed while thread is still running").
                # Park it under the QApplication so Qt owns its remaining
                # lifetime: the disconnect above blocks stale results and
                # deleteLater fires from its own finished signal.
                self._cover_loader.finished.connect(
                    self._cover_loader.deleteLater)
                self._cover_loader.setParent(QApplication.instance())
            else:
                self._cover_loader.deleteLater()
        self._cover_loader = CoverLoaderThread(path)
        self._cover_loader.cover_ready.connect(self._on_cover_ready)
        self._cover_loader.start()

    def _on_cover_ready(self, path, data):
        # Ignore results from an outdated load (fast track switching).
        if path != getattr(self, "_cover_request", None) or path != self.current_track:
            return
        self._last_cover_pm = None
        if data is None:
            self.cover_label.set_cover(None)
            self.fav_cover.set_cover(None)
            self.player_bar.set_cover_thumb(None)
            pm = None
        else:
            pm = coverart.bytes_to_pixmap(data)
            if pm.isNull():
                self.cover_label.set_cover(None)
                self.fav_cover.set_cover(None)
                self.player_bar.set_cover_thumb(None)
                pm = None
            else:
                self.cover_label.set_cover(pm)
                self.fav_cover.set_cover(pm)
                self.player_bar.set_cover_thumb(pm)
        # Keep the tray popup's cover in sync with the loaded art too.
        if getattr(self, "tray_menu_widget", None) is not None:
            title, artist = self._current_track_tags()
            self.tray_menu_widget.update_now_playing(title, artist, pm)
        if self.mini_player and self.mini_player.isVisible() and self.current_track:
            self.mini_player.update_track(*self._current_track_tags(), pm)
        # v3: remember for the web-remote bridge / dynamic theme
        self._last_cover_pm = pm
        try:
            if self.prefs.get("dynamic_album_theme", False) and pm is not None:
                self._maybe_apply_dynamic_theme(pm)
        except Exception:
            pass

    def _maybe_apply_dynamic_theme(self, pm):
        """Material You-style accent extraction from the current cover.
        Applied as a transient theme overlay (never persisted)."""
        try:
            from gm.dynamic_theme import theme_from_cover
            t = theme_from_cover(pm, self.theme,
                                 dark=bool(self.theme.get("is_dark", True)))
            if t:
                self._dynamic_theme = t
                self._apply_theme_dict(t)
        except Exception:
            log.exception("dynamic theme failed")

    def _on_play_pause(self):
        if self.current_track is None:
            if self.current_playlist:
                self._load_and_play_current()
            else:
                self._on_play_all()
            self._tray_sync_now_playing()
            return
        state = self.audio.state()
        if state == AudioBackend.STATE_PLAYING:
            if getattr(self, "fx", None):
                self.fx.fade_out_then(self.audio.pause)
            else:
                self.audio.pause()
        elif state == AudioBackend.STATE_PAUSED:
            self.audio.play()
            if getattr(self, "fx", None):
                self.fx.fade_in_from_silence()
        else:
            # Stopped — reload and play
            self.audio.set_volume(self.player_bar.vol_slider.value())
            self.audio.load_and_play(self.current_track)

    def _cycle_playback_rate(self, anchor_btn=None):
        """Open a small popup with a speed slider (0.5x – 2.0x).

        Anchors to the button that ASKED for it (rate button from the bar,
        menu item from the ⋯ menu) and is clamped fully inside the monitor —
        it used to always anchor to more_btn and could open off-screen.
        Non-modal (Popup): the player keeps playing and the old modal dialog
        no longer grabs the playlist context menu's focus away."""
        if not getattr(self, "fx", None):
            return
        # Close a previous popup instead of stacking dialogs on fast clicks.
        old = getattr(self, "_rate_popup", None)
        if old is not None:
            try:
                old.close()
            except Exception:
                pass
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QSlider, QHBoxLayout
        dlg = QDialog(self, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        dlg.setWindowTitle("Playback Speed")
        dlg.setWindowIcon(QIcon(render_icon(Icon.SPEED, 32, self.theme["gold"],
                                            get_dpr())))
        dlg.setModal(False)
        dlg.setFixedWidth(380)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._rate_popup = dlg
        lay = QVBoxLayout(dlg)
        row = QHBoxLayout()
        icon_lbl = QLabel()
        icon_lbl.setPixmap(render_icon(Icon.SPEED, 22, self.theme["gold"],
                                       get_dpr()))
        row.addStretch(1)
        row.addWidget(icon_lbl)
        lbl = QLabel(f"{self.fx.current_rate():.2f}x")
        lbl.setObjectName("NowTitle")
        row.addWidget(lbl)
        row.addStretch(1)
        lay.addLayout(row)
        slider = QSlider(Qt.Orientation.Horizontal)
        # 50..200 mapped /100 -> 0.5x..2x
        slider.setRange(50, 200)
        slider.setValue(int(round(self.fx.current_rate() * 100)))
        slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        slider.setTickInterval(25)
        def _on_move(v):
            lbl.setText(f"{v/100:.2f}x")
            self.fx.set_rate(v / 100.0)
        slider.valueChanged.connect(_on_move)
        lay.addWidget(slider)
        # Preset buttons: 2 rows x 3 cols so every button has room for a
        # 22px icon AND full text. The old single-row layout squeezed 6
        # icon+text buttons into ~50px each and Qt elided the labels ("0.").
        presets = QGridLayout()
        presets.setSpacing(8)
        presets.setContentsMargins(4, 2, 4, 2)
        current_rate = self.fx.current_rate()
        preset_icons = {
            0.5: "timer",           # very slow
            0.75: "chevron-left",   # slow
            1.0: "play",            # normal speed
            1.25: "music-note",     # slightly faster
            1.5: "chevron-right",   # fast
            2.0: "gauge",           # fastest (speed gauge)
        }
        cur = self.theme.get("gold", "#d4a85a")
        txt = self.theme.get("text", "#ffffff")
        gold_light = self.theme.get("gold_light", cur)
        for i, p in enumerate((0.5, 0.75, 1.0, 1.25, 1.5, 2.0)):
            b = QPushButton()
            is_current = abs(p - current_rate) < 0.01
            icon_color = cur if is_current else txt
            b.setIcon(make_icon(preset_icons[p], 22, icon_color, get_dpr()))
            b.setIconSize(QSize(22, 22))
            b.setText(f"{p:g}x")
            b.setToolTip(f"Set speed to {p:g}x")
            b.setMinimumSize(112, 52)
            # Inline style (not QSS objectName) so it works even if the
            # app-wide stylesheet was already applied before the popup opens.
            border = cur if is_current else "rgba(255,255,255,0.16)"
            b.setStyleSheet(
                "QPushButton {"
                f" background: {'rgba(212,168,90,0.16)' if is_current else 'transparent'};"
                f" color: {gold_light if is_current else txt};"
                f" border: 1px solid {border}; border-radius: 12px;"
                " font-size: 13px; font-weight: 600; padding: 6px 8px; }"
                "QPushButton:hover {"
                f" background: rgba(212,168,90,0.22); border-color: {cur};"
                f" color: {gold_light}; }}")
            b.clicked.connect(lambda _, pv=p: (slider.setValue(int(pv*100))))
            presets.addWidget(b, i // 3, i % 3)
        lay.addLayout(presets)
        # ---- Anchor to the requesting button, clamp inside its monitor ----
        if anchor_btn is None:
            anchor_btn = self.player_bar.rate_btn \
                if self.player_bar.rate_btn.isVisible() \
                else self.player_bar.more_btn
        btn_pos = anchor_btn.mapToGlobal(QPoint(0, anchor_btn.height()))
        # Which monitor is the anchor on? (fallback: primary)
        screen = QApplication.screenAt(
            QPoint(btn_pos.x() + anchor_btn.width() // 2, btn_pos.y()))
        geo = (screen if screen is not None
               else QApplication.primaryScreen()).availableGeometry()
        dlg.adjustSize()
        dw, dh = dlg.width(), dlg.height()
        x = max(geo.left() + 4,
                min(btn_pos.x() + anchor_btn.width() // 2 - dw // 2,
                    geo.right() - dw - 4))
        y = btn_pos.y() + 6
        if y + dh > geo.bottom() - 4:          # below would clip → open above
            y = max(geo.top() + 4,
                    anchor_btn.mapToGlobal(QPoint(0, 0)).y() - dh - 6)
        dlg.move(x, y)
        dlg.finished.connect(lambda _r: self.player_bar.rate_btn.setToolTip(
            f"Playback speed: {self.fx.current_rate():g}x"))
        dlg.show()

    def _on_ab_button(self):
        """First click sets A, second sets B (loop), third clears."""
        if not getattr(self, "fx", None):
            return
        if not self.fx.ab_active():
            if self.fx.a_ms is None:
                a = self.fx.mark_a()
                self.player_bar.ab_btn.setChecked(True)
                self.player_bar.ab_btn.setToolTip(
                    f"A-B Repeat: A set at {fmt_time(a or 0)} — click again to set B")
            else:
                b = self.fx.mark_b()
                self.player_bar.ab_btn.setToolTip(
                    f"A-B loop active {fmt_time(self.fx.a_ms)} → {fmt_time(b or 0)} — click to clear")
        else:
            self.fx.clear_ab()
            self.player_bar.ab_btn.setChecked(False)
            self.player_bar.ab_btn.setToolTip("A-B Repeat")

    def _play_path_from_playlist(self, path):
        """Play `path` via the playlist when present, or directly (history
        entry may have been removed from the current list)."""
        try:
            idx = self.current_playlist.index(path)
        except ValueError:
            # Track no longer in the active playlist — play it standalone so
            # history navigation never dies after library edits.
            # Mirror _load_and_play_current's per-track resets so the
            # standalone path doesn't leak stale A-B loops, stale 70%
            # meters, or a stale current_index into the next track.
            self._err_skip_count = 0
            if getattr(self, "fx", None):
                self.fx.clear_ab()
            self._finalize_listen_credit()
            self._listened_ms = 0
            self.current_track = path
            self.current_index = -1   # no valid position in current_playlist
            if path in self._tag_cache:
                title, artist = self._tag_cache[path]
            else:
                try:
                    title, artist = extract_title_artist(path)
                    self._tag_cache[path] = (title, artist)
                except Exception:
                    title = os.path.splitext(os.path.basename(path))[0]
                    artist = "Unknown Artist"
                    self._tag_cache[path] = (title, artist)
            self.now_title.setMarqueeText(title)
            self.now_artist.setMarqueeText(artist)
            self.player_bar.title_label.setMarqueeText(title)
            self.player_bar.artist_label.setMarqueeText(artist)
            self.player_bar.time_total.setText("0:00")
            self.player_bar.time_current.setText("0:00")
            self.player_bar.seek_slider.setValue(0)
            self.player_bar.update_like_button(path in self.favorites)
            self._load_cover_async(path)
            self._highlight_playing_in_lists()
            self._push_history(path)
            if path in getattr(self, "shuffle_unplayed", set()):
                self.shuffle_unplayed.discard(path)
            self.audio.set_volume(self.player_bar.vol_slider.value())
            self.audio.load_and_play(path)
            if getattr(self, "fx", None):
                self.fx.fade_in_from_silence()
                rate = self.fx.current_rate()
                if abs(rate - 1.0) > 1e-6:
                    self.fx.set_rate(rate)
            if self.mini_player and self.mini_player.isVisible():
                self.mini_player.update_track(
                    title, artist, self.player_bar._cover_thumb_pm)
            if getattr(self, "tray_menu_widget", None) is not None:
                self.tray_menu_widget.update_now_playing(
                    title, artist, self.player_bar._cover_thumb_pm)
            return
        self.current_index = idx
        self._load_and_play_current()

    def _on_next(self):
        if not self.current_playlist:
            return
        # A successful manual/auto advance means the previous error is over —
        # reset the consecutive-error counter (used by _on_audio_error).
        self._err_skip_count = 0
        if self.repeat_mode == RepeatMode.ONE and self.current_track:
            # Repeat-one restart is a fresh listen of the same track: credit
            # the finished pass under the 70% rule, then reset the meter.
            self._finalize_listen_credit()
            self._listened_ms = 0
            self.audio.set_position(0)
            self.audio.play()
            return
        # Forward through history first (redo branch) — applies to shuffle too
        if self.redo_stack:
            nxt = self.redo_stack.pop()
            self.play_history.append(nxt)
            del self.play_history[:-self._HISTORY_MAX]
            self._nav_via_history = True
            try:
                self._play_path_from_playlist(nxt)
            finally:
                self._nav_via_history = False
            return
        if self.shuffle:
            # Fresh random jump — only when there is nothing to redo.
            # Smart shuffle: prefer tracks that haven't played yet this
            # session so everything gets a turn before anything repeats.
            candidates = [p for p in self.shuffle_unplayed
                          if p in set(self.current_playlist)]
            if len(self.current_playlist) > 1:
                if candidates:
                    chosen = random.choice(candidates)
                    self.shuffle_unplayed.discard(chosen)
                else:
                    # Everything has played — plain shuffle over the list
                    idx = self.current_index
                    while idx == self.current_index:
                        idx = random.randrange(len(self.current_playlist))
                    chosen = self.current_playlist[idx]
                self.current_index = max(0, self.current_playlist.index(chosen)) \
                    if chosen in self.current_playlist else 0
            else:
                self.current_index = 0
        else:
            self.current_index += 1
            if self.current_index >= len(self.current_playlist):
                if self.repeat_mode == RepeatMode.ALL:
                    self.current_index = 0
                else:
                    self.current_index = len(self.current_playlist) - 1
                    self.audio.stop()
                    self.player_bar.update_play_button(False)
                    return
        self._load_and_play_current()

    def _on_prev(self):
        if not self.current_playlist:
            return
        pos = self.audio.position()
        if pos > 3000:
            self.audio.set_position(0)
            return
        # History-aware Previous: in shuffle mode this must go back to the
        # track that ACTUALLY played before, not pick a new random one.
        if len(self.play_history) >= 2:
            cur = self.play_history[-1]
            prev_path = self.play_history[-2]
            if prev_path == cur:
                # Defensive: deduped history should not contain adjacent dupes
                return
            self.play_history.pop()
            self.redo_stack.append(cur)
            self._nav_via_history = True
            try:
                self._play_path_from_playlist(prev_path)
            finally:
                self._nav_via_history = False
            return
        # No usable history — fall back to sequential behavior
        if self.shuffle:
            if len(self.current_playlist) > 1:
                idx = self.current_index
                while idx == self.current_index:
                    idx = random.randrange(len(self.current_playlist))
                self.current_index = idx
            else:
                self.current_index = 0
        else:
            self.current_index -= 1
            if self.current_index < 0:
                if self.repeat_mode == RepeatMode.ALL:
                    self.current_index = len(self.current_playlist) - 1
                else:
                    self.current_index = 0
        self._load_and_play_current()

    def _on_like_toggled(self):
        if self.current_track is None:
            return
        self._toggle_favorite(self.current_track)

    def _on_shuffle_toggled(self):
        self.shuffle = self.player_bar.shuffle_btn.isChecked()
        self.player_bar.update_shuffle_button(self.shuffle)
        # Smart shuffle: when turning shuffle ON mid-queue, seed the unplayed
        # set with everything except the current track so unplayed tracks
        # get priority from this point on.
        if self.shuffle and self.current_playlist:
            self.shuffle_unplayed = set(self.current_playlist)
            self.shuffle_unplayed.discard(self.current_track)

    def _on_repeat_toggled(self):
        if self.repeat_mode == RepeatMode.OFF:
            self.repeat_mode = RepeatMode.ALL
        elif self.repeat_mode == RepeatMode.ALL:
            self.repeat_mode = RepeatMode.ONE
        else:
            self.repeat_mode = RepeatMode.OFF
        self.player_bar.update_repeat_button(self.repeat_mode)
        self._save_config()

    def _on_seek_pressed(self):
        self.user_is_seeking = True

    def _on_seek_moved(self, val):
        """Called while dragging or clicking on seek bar."""
        dur = self.audio.duration()
        if dur > 0:
            new_pos = int(dur * val / 1000.0)
            self.player_bar.time_current.setText(fmt_time(new_pos))

    def _on_seek_released(self):
        # Seek to the final drag position FIRST, then clear the flag so the
        # UI tick resumes following the (new) playback position.
        val = self.player_bar.seek_slider.value()
        dur = self.audio.duration()
        if dur > 0:
            new_pos = int(dur * val / 1000.0)
            self.audio.set_position(new_pos)
            self.player_bar.time_current.setText(fmt_time(new_pos))
        self.user_is_seeking = False

    def _on_seek_value_changed(self, val):
        """Only called when the USER changes the slider (click or drag).
        Programmatic updates use blockSignals(True) so this doesn't fire.
        """
        if not self.user_is_seeking:
            # This is a click-to-seek (user clicked on the groove)
            dur = self.audio.duration()
            if dur > 0:
                new_pos = int(dur * val / 1000.0)
                self.audio.set_position(new_pos)
                self.player_bar.time_current.setText(fmt_time(new_pos))
        else:
            # During drag, just update the time label
            dur = self.audio.duration()
            if dur > 0:
                self.player_bar.time_current.setText(fmt_time(int(dur * val / 1000.0)))

    def _on_volume_changed(self, val):
        self.audio.set_volume(val)
        self.player_bar.update_volume_button(val)
        if val > 0:
            self._last_volume = val
        self._save_config_debounced()

    def _on_mute_toggle(self):
        if self.player_bar.vol_slider.value() == 0:
            self.player_bar.vol_slider.setValue(self._last_volume or 80)
        else:
            self._last_volume = self.player_bar.vol_slider.value()
            self.player_bar.vol_slider.setValue(0)

    def _on_position_changed(self, pos):
        if not self.user_is_seeking:
            dur = self.audio.duration()
            if dur > 0:
                # Block signals to prevent feedback loop:
                # setValue → valueChanged → _on_seek_value_changed → set_position → position reset
                self.player_bar.seek_slider.blockSignals(True)
                self.player_bar.seek_slider.setValue(int(pos * 1000 / dur))
                self.player_bar.seek_slider.blockSignals(False)
            self.player_bar.time_current.setText(fmt_time(pos))

    def _on_duration_changed(self, dur):
        self.player_bar.time_total.setText(fmt_time(dur))
        if not self.user_is_seeking:
            pos = self.audio.position()
            if dur > 0:
                # Block signals here too
                self.player_bar.seek_slider.blockSignals(True)
                self.player_bar.seek_slider.setValue(int(pos * 1000 / dur))
                self.player_bar.seek_slider.blockSignals(False)

    def _on_state_changed(self, state):
        if state == AudioBackend.STATE_PLAYING:
            self.player_bar.update_play_button(True)
            if self.mini_player and self.mini_player.isVisible():
                self.mini_player.update_play_state(True)
        elif state == AudioBackend.STATE_PAUSED:
            self.player_bar.update_play_button(False)
            if self.mini_player and self.mini_player.isVisible():
                self.mini_player.update_play_state(False)
        elif state == AudioBackend.STATE_STOPPED:
            self.player_bar.update_play_button(False)
            if self.mini_player and self.mini_player.isVisible():
                self.mini_player.update_play_state(False)
        # Keep the tray popup's play/pause icon in sync too (it stays alive
        # while hidden, so it must follow every state flip).
        if getattr(self, "tray_menu_widget", None) is not None:
            self.tray_menu_widget.update_play_state(
                state == AudioBackend.STATE_PLAYING)

    def _on_audio_error(self, msg):
        # A corrupt / unsupported file must never stall the player on a dead
        # track: finalize its (uncounted) listen credit, then auto-skip to
        # the next track (guarding against a playlist full of bad files by
        # capping consecutive skips).
        self._show_toast(f"Audio: {msg}", error=True, ms=4000)
        log.warning(f"audio error: {msg}")
        try:
            bad = self.current_track
            if bad:
                self._finalize_listen_credit()
                self.current_track = None
                n = getattr(self, "_err_skip_count", 0) + 1
                self._err_skip_count = n
                if bad in (self.current_playlist or []):
                    if n <= max(3, len(self.current_playlist)):
                        log.warning(f"Auto-skipping unreadable file ({n}): {bad}")
                        QTimer.singleShot(400, self._on_next)
                        return
                self._err_skip_count = 0
        except Exception as ex:
            log.warning(f"error auto-skip failed: {ex}")

    def _ui_tick(self):
        if self.audio.state() == AudioBackend.STATE_PLAYING and not self.user_is_seeking:
            pos = self.audio.position()
            dur = self.audio.duration()
            if dur > 0:
                # Block signals to prevent feedback loop
                self.player_bar.seek_slider.blockSignals(True)
                self.player_bar.seek_slider.setValue(int(pos * 1000 / dur))
                self.player_bar.seek_slider.blockSignals(False)
            self.player_bar.time_current.setText(fmt_time(pos))
            self._on_lyrics_position_tick(pos)
        # Stats: accumulate listening time while playing (0.5 s per 500 ms
        # tick) + per-track listen metering for the 70% credit rule.
        if self.audio.state() == AudioBackend.STATE_PLAYING:
            if getattr(self, "stats", None):
                self.stats.add_seconds(0.5)
            if self.current_track is not None:
                self._listened_ms = getattr(self, "_listened_ms", 0) + 500
            # v3: bookmark autosave for long files (every ~5 s of ticks)
            self._bm_tick = getattr(self, "_bm_tick", 0) + 1
            if self._bm_tick >= 10:
                self._bm_tick = 0
                self._bookmark_autosave()
            # v3: start the spectrum tap once playback truly advances
            self._maybe_begin_tap(pos)
        # Toast notification when the window is hidden
        if getattr(self, "_toast_enabled", True):
            self._maybe_toast_track_change()

    # Sleep timer
    def start_sleep_timer(self, minutes):
        self.sleep_timer_active = True
        self.sleep_timer_minutes = minutes
        if self._sleep_timer is None:
            self._sleep_timer = QTimer(self)
            self._sleep_timer.setSingleShot(True)
            self._sleep_timer.timeout.connect(self._on_sleep_timer_done)
        self._sleep_timer.start(minutes * 60 * 1000)
        self._show_toast(f"Sleep timer: will stop in {minutes} min")

    def stop_sleep_timer(self):
        self.sleep_timer_active = False
        if self._sleep_timer is not None:
            self._sleep_timer.stop()

    def _on_sleep_timer_done(self):
        self.sleep_timer_active = False
        self.audio.stop()
        self.player_bar.update_play_button(False)
        self._show_toast("Sleep timer: playback stopped")

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            # Left-click: toggle show/hide
            self._toggle_visible()
        elif reason == QSystemTrayIcon.ActivationReason.MiddleClick:
            self._on_play_pause()
        elif reason == QSystemTrayIcon.ActivationReason.Context:
            # Right-click: our own styled popup instead of the gray native menu
            self._show_tray_popup()

    def _current_track_tags(self) -> tuple:
        """(title, artist) for the playing track from cache only — never runs
        mutagen on the UI thread; falls back to the filename stem."""
        if not self.current_track:
            return "", ""
        title, artist = self._tag_cache.get(self.current_track, ("", ""))
        if not title:
            title = os.path.splitext(os.path.basename(self.current_track))[0]
        return title, artist

    def _tray_sync_now_playing(self):
        """Push the current track's info (title / artist / cover) into the
        tray popup header from live app state."""
        if getattr(self, "tray_menu_widget", None) is None:
            return
        if self.current_track:
            title, artist = self._current_track_tags()
            self.tray_menu_widget.update_now_playing(
                title, artist, self.player_bar._cover_thumb_pm)
        else:
            self.tray_menu_widget.update_now_playing()

    def _show_tray_popup(self):
        """Show the custom tray popup above the taskbar, near the cursor."""
        from PyQt6.QtGui import QCursor
        pos = QCursor.pos()
        # Update play state, theme and now-playing header
        self.tray_menu_widget._apply_theme(self.theme)
        playing = self.audio.state() == AudioBackend.STATE_PLAYING
        self.tray_menu_widget.update_play_state(playing)
        self._tray_sync_now_playing()
        menu_size = self.tray_menu_widget.size()
        # X: centered on the cursor, clamped to the taskbar's screen
        for scr in QApplication.screens():
            if scr.geometry().contains(pos):
                screen_geo = scr.availableGeometry()
                break
        else:
            screen_geo = QApplication.primaryScreen().availableGeometry()
        x = pos.x() - menu_size.width() // 2
        x = max(screen_geo.left() + 4,
                min(x, screen_geo.right() - menu_size.width() - 4))
        # Y: bottom edge sits exactly on top of the taskbar line
        y = screen_geo.bottom() - menu_size.height() + 1
        self.tray_menu_widget.move(x, y)
        self.tray_menu_widget.show()
        self.tray_menu_widget.raise_()
        self.tray_menu_widget.activateWindow()

    def _reveal_window(self):
        """Un-minimize, show, raise and focus the main window.

        show() alone remembers a previous minimized state and raise_() cannot
        take the Windows foreground by itself, so the full restore dance lives
        here. Shared by the tray left-click and the tray popup's Show button.
        """
        self.setWindowState(
            (self.windowState() & ~Qt.WindowState.WindowMinimized)
            | Qt.WindowState.WindowActive)
        self.showNormal()
        self.raise_()
        self.activateWindow()
        if sys.platform == "win32":
            # Windows foreground-lock: a background process may not steal
            # focus; nudge the shell so the restored window really lands on
            # top (harmless no-op when it already has focus).
            try:
                import ctypes
                ctypes.windll.user32.SetForegroundWindow(int(self.winId()))
            except Exception as e:
                log.debug(f"SetForegroundWindow unavailable: {e}")

    def _toggle_visible(self):
        """Tray Show/Hide toggle.

        A minimized (or hidden) window must always come back restored and on
        top — never left minimized behind other windows, never hidden by a
        "show" click. isVisible() is true for minimized windows, so the
        minimized state is part of the predicate.
        """
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self._reveal_window()

    # ------------------------------------------------------------------
    # Drag & drop from the OS file manager
    # ------------------------------------------------------------------
    _DROP_MAX_FILES = 5000  # sanity cap per drop event

    def dragEnterEvent(self, e):
        """Accept drops that contain at least one local dir/audio file."""
        md = e.mimeData()
        if not md.hasUrls():
            return
        for url in md.urls()[:self._DROP_MAX_FILES]:
            if not url.isLocalFile():
                continue
            p = Path(url.toLocalFile())
            if p.is_dir() or p.suffix.lower() in AUDIO_EXTS:
                e.acceptProposedAction()
                return

    def dropEvent(self, e):
        md = e.mimeData()
        folders, files = [], []
        for url in md.urls()[:self._DROP_MAX_FILES]:
            if not url.isLocalFile():
                continue
            p = Path(url.toLocalFile())
            if p.is_dir():
                folders.append(str(p))
            elif p.suffix.lower() in AUDIO_EXTS and p.exists():
                files.append(str(p))
        if not folders and not files:
            return
        # Add folders to scan list (dedup against existing)
        new_folders = [f for f in folders if f not in self.added_folders]
        self.added_folders.extend(new_folders)
        # Direct file drops join the library immediately
        existing = set(self.library)
        added_files = [f for f in files if f not in existing]
        self.library.extend(added_files)
        if new_folders:
            self._start_scan(new_folders, label="Scanning dropped folder...")
        else:
            # No folder scan pending — refresh lists directly. Dropped files
            # may live outside every known folder → tree must refresh once.
            self.library.sort(key=lambda x: os.path.basename(x).lower())
            self._rebuild_library_list(rebuild_tree=True)
            self._update_count_label()
            self._save_config()
        if added_files or new_folders:
            n = len(added_files) + len(new_folders)
            log.info(f"Drop: +{len(added_files)} files, +{len(new_folders)} folders")

    def changeEvent(self, e):
        """When enabled in Settings, clicking the taskbar icon (minimize)
        sends the window to the tray instead."""
        from PyQt6.QtCore import QEvent
        if (e.type() == QEvent.Type.WindowStateChange
                and self.windowState() & Qt.WindowState.WindowMinimized
                and getattr(self, "taskbar_close_to_tray", False)):
            # Defer so the window state settles before hiding
            QTimer.singleShot(0, self._minimize_to_tray)
        super().changeEvent(e)

    def _minimize_to_tray(self):
        if self.windowState() & Qt.WindowState.WindowMinimized:
            self.hide()
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
            # No balloon here — the once-ever persisted close-to-tray notice
            # below is the single educational popup; repeated balloons were
            # notification spam (and invisible anyway once hidden).

    def closeEvent(self, e):
        # v3.0: stop background remotes + analysis threads on ANY close path
        try:
            from gm.remote_bridges import stop_all as _stop_remotes
            _stop_remotes()
        except Exception:
            pass
        try:
            if getattr(self, "_mpris", None) is not None:
                self._mpris.stop()
        except Exception:
            pass
        for _attr in ("_content_tap", "_waveform_worker"):
            _t = getattr(self, _attr, None)
            if _t is not None:
                try:
                    _t.stop_tap()
                except AttributeError:
                    pass
                try:
                    _t.cancel()
                except AttributeError:
                    pass
                if _t.isRunning():
                    _t.wait(1500)
        _fw = getattr(self, "_floating_lyrics_w", None)
        if _fw is not None:
            try:
                _fw.close()
            except Exception:
                pass
        if self._force_quit:
            self._save_config()
            if self.scanner is not None and self.scanner.isRunning():
                self.scanner.cancel()
                self.scanner.wait(2000)
            # v3.0: the SQLite store opener may still be running (slow
            # first-start migration) — give it a moment to land.
            sw = getattr(self, "_store_worker", None)
            if sw is not None and sw.isRunning():
                sw.wait(4000)
            # Cancel remaining worker threads so nothing is mid-emit when the
            # interpreter tears down (avoids QThread destroyed warnings/crashes)
            for attr in ("_tag_loader", "_cover_loader", "_albums_worker"):
                t = getattr(self, attr, None)
                if t is not None:
                    try:
                        t.cancel()
                    except AttributeError:
                        pass
                    if t.isRunning():
                        t.wait(1000)
            lp = getattr(self, "_lyrics_panel", None)
            if lp is not None and getattr(lp, "_loader", None) is not None:
                lp._loader.cancel()
                if lp._loader.isRunning():
                    lp._loader.wait(1000)
            if self.mini_player is not None:
                self.mini_player.close()
            e.accept()
        else:
            e.ignore()
            self.hide()
            # Educational notice — shown ONCE EVER (persisted in config):
            # users must learn that closing keeps the app running. Every
            # subsequent close-to-tray is silent.
            if self.tray and not bool(getattr(self, "tray_notice_shown", False)):
                self.tray_notice_shown = True
                self._save_config()   # persist immediately — a quit may follow
                self.tray.showMessage(APP_NAME,
                    f"{APP_NAME} keeps running in the tray. "
                    "Right-click the tray icon to quit.",
                    QSystemTrayIcon.MessageIcon.Information, 4000)

    def _quit(self):
        self._force_quit = True
        if self.tray:
            self.tray.hide()
        # 'Install when I quit': the verified installer launches detached,
        # /CLOSEAPPLICATIONS handles the still-open process via AppMutex.
        pending = getattr(self, "_pending_install_path", None)
        if pending and os.path.isfile(pending):
            try:
                updater.launch_installer(pending)
            except Exception as e:
                log.warning(f"pending update install failed: {e}")
        # Tear down worker threads BEFORE quitting the event loop — the old
        # code called QApplication.quit() directly, leaving CoverLoader /
        # TagLoader / AlbumsWorker / LyricsLoader mid-emit while the
        # interpreter tore down ("QThread: Destroyed while thread is still
        # running" hard-crash on exit under load).
        # close() runs MainWindow.closeEvent, which waits on every worker;
        # tray.hide() above means closeEvent takes the _force_quit branch.
        self.close()
        QApplication.quit()

    _save_timer = None

    def _save_config_debounced(self):
        if self._save_timer is None:
            self._save_timer = QTimer(self)
            self._save_timer.setSingleShot(True)
            self._save_timer.setInterval(800)
            self._save_timer.timeout.connect(self._save_config)
        self._save_timer.start()


    # ==================================================================
    # v3.0 — preferences, command palette, radio, M3U, shortcuts, watch
    # ==================================================================
    def _load_prefs(self, cfg: dict):
        from gm.prefs import merge_prefs
        self.prefs = merge_prefs((cfg or {}).get("prefs"))
        try:
            from gm.ui import animations
            animations.set_reduce_animations(
                bool(self.prefs.get("reduce_animations", False)))
        except Exception:
            pass
        try:
            from gm import i18n
            i18n.set_language(self.prefs.get("language", "en"))
            app_inst = QApplication.instance()
            if app_inst is not None:
                from PyQt6.QtCore import Qt as _Qt
                app_inst.setLayoutDirection(
                    _Qt.LayoutDirection.RightToLeft if i18n.is_rtl()
                    else _Qt.LayoutDirection.LeftToRight)
        except Exception:
            pass

    def apply_prefs(self, new_prefs: dict):
        """SettingsDialog3 OK handler: validate, persist, live-apply."""
        from gm.prefs import merge_prefs
        if not isinstance(new_prefs, dict):
            return
        if new_prefs.get("___reset___"):
            new_prefs = {}
        self.prefs = merge_prefs({**self.prefs, **new_prefs})
        try:
            from gm.ui import animations
            animations.set_reduce_animations(
                bool(self.prefs.get("reduce_animations", False)))
        except Exception:
            pass
        # language + RTL
        try:
            from gm import i18n
            i18n.set_language(self.prefs.get("language", "en"))
            app_inst = QApplication.instance()
            if app_inst is not None:
                from PyQt6.QtCore import Qt as _Qt
                app_inst.setLayoutDirection(
                    _Qt.LayoutDirection.RightToLeft if i18n.is_rtl()
                    else _Qt.LayoutDirection.LeftToRight)
        except Exception:
            pass
        # live theme refresh (scale/animation styling)
        self._apply_theme()
        # audio engine / EQ / crossfade / normalization
        self._apply_audio_prefs()
        # smart playlists opt-in set
        self._sync_smart_playlists()
        # folder watcher
        self._apply_folder_watch()
        # remotes
        self._apply_remote_prefs()
        # floating lyrics
        self._apply_floating_lyrics()
        # shortcuts rebuilt
        self._setup_shortcuts()
        self._save_config()

    # ---------------- settings-dialog request hooks (guarded API) ------
    def request_theme_picker(self):
        self._show_theme_picker()

    def request_theme_refresh(self):
        self._apply_theme()

    def request_ui_scale(self, scale):
        try:
            self.prefs["ui_scale"] = max(0.75, min(1.5, float(scale)))
        except (TypeError, ValueError):
            return
        self._apply_theme()

    def request_output_picker(self):
        try:
            from audio_output import list_output_devices
            devs = list_output_devices()
            if not devs:
                return
            from PyQt6.QtWidgets import QInputDialog
            names = [d[0] for d in devs]
            cur = getattr(self, "audio_output_id", "") or ""
            cur_idx = 0
            for i, d in enumerate(devs):
                if d[1] == cur:
                    cur_idx = i
                    break
            name, ok = QInputDialog.getItem(
                self, "Audio Output", "Output device:", names, cur_idx, False)
            if ok:
                for i, d in enumerate(devs):
                    if d[0] == name:
                        self._switch_audio_output(i)
                        break
        except Exception:
            log.exception("output picker failed")

    def request_update_check(self):
        try:
            self._run_update_check(manual=True)
        except Exception:
            log.exception("manual update check failed")

    def request_open_log(self):
        try:
            from applog import log_file_path
            from gm.platforms import open_file_location
            open_file_location(str(log_file_path()))
        except Exception:
            pass

    def request_open_repo(self):
        from gm.platforms import open_url
        open_url("https://github.com/Amirmpm/golden-music")

    def request_clear_lyrics_cache(self):
        try:
            import shutil
            cache_dir = config_path().parent / "lyrics_cache"
            if cache_dir.is_dir():
                shutil.rmtree(str(cache_dir), ignore_errors=True)
            self._show_toast("Lyrics cache cleared")
        except Exception:
            log.exception("lyrics cache clear failed")

    def request_clear_waveform_cache(self):
        try:
            if getattr(self, "store", None) is not None:
                with self.store.db._tx() as conn:
                    conn.execute("DELETE FROM waveform_cache")
            self._show_toast("Waveform cache cleared")
        except Exception:
            log.exception("waveform cache clear failed")

    def request_telegram_test(self):
        ok = False
        try:
            from gm.telegrambot import validate_token_format
            token = self.prefs.get("telegram_bot_token", "")
            ok = validate_token_format(token)
        except Exception:
            ok = False
        self._show_toast("Telegram token looks valid" if ok else
                         "Telegram token invalid")

    # ---------------- command palette -----------------------------------
    def _command_actions(self):
        acts = []
        add = lambda icon, title, group, run, hint=None: acts.append(
            {"icon": icon, "title": title, "group": group, "run": run,
             **({"hint": hint} if hint else {})})
        add(Icon.PLAY, "Play / Pause", "Playback",
            lambda: self._on_play_pause_requested()
            if hasattr(self, "_on_play_pause_requested") else self.audio.play(),
            "Space")
        add(Icon.NEXT, "Next track", "Playback", self._on_next, "Ctrl+→")
        add(Icon.PREV, "Previous track", "Playback", self._on_prev, "Ctrl+←")
        add(Icon.SHUFFLE, "Toggle shuffle", "Playback", self._on_shuffle_toggled)
        add(Icon.REPEAT, "Cycle repeat mode", "Playback", self._on_repeat_toggled)
        add(Icon.HEART, "Like current track", "Playback",
            lambda: self._toggle_favorite_for(self.current_track)
            if self.current_track else None, "Ctrl+B")
        add(Icon.LYRICS, "Toggle lyrics panel", "View",
            self._toggle_lyrics_panel, "Ctrl+L")
        add(Icon.QUEUE, "Toggle queue panel", "View",
            self._toggle_queue_panel if hasattr(self, "_toggle_queue_panel")
            else (lambda: None))
        add(Icon.MINIMIZE, "Mini player", "View", self._toggle_mini_player, "Ctrl+M")
        add(Icon.MAXIMIZE, "Fullscreen player", "View",
            self._toggle_fullscreen_player, "F11")
        for key, title in (("library", "Go to Library"), ("favorites", "Go to Favorites"),
                           ("albums", "Go to Albums"), ("artists", "Go to Artists"),
                           ("genres", "Go to Genres"), ("playlists", "Go to Playlists"),
                           ("radio", "Go to Radio"), ("stats", "Go to Stats")):
            add(Icon.LIBRARY, title, "Navigation",
                lambda k=key: self._on_rail_clicked(k))
        add(Icon.BRUSH, "Switch theme", "Appearance", self._show_theme_picker)
        add(Icon.SETTINGS, "Open settings", "Appearance", self._show_settings)
        add(Icon.FOLDER_ADD, "Add music folder", "Library", self._on_add_folder)
        add(Icon.REFRESH, "Rescan library", "Library", self._on_refresh)
        add(Icon.DOWNLOAD, "Export playlists (M3U)", "Library",
            self._export_all_playlists_m3u)
        add(Icon.QUEUE, "Play next: current album", "Queue",
            lambda: self._play_next_album())
        return acts

    def _command_search(self, query):
        """Quick search provider: tracks + albums + artists + playlists."""
        results = []
        q = (query or "").strip()
        if not q or len(q) < 2:
            return results
        try:
            paths = self._get_filtered_sorted_library() if not q else None
        except Exception:
            paths = None
        try:
            if getattr(self, "store", None) is not None:
                hits = self.store.search(q, 14)
            else:
                hits = [p for p in (paths or self.library)][:14]
                hits = [p for p in hits if q.lower() in os.path.basename(p).lower()][:10]
            for p in hits[:10]:
                title, artist = self._tag_cache.get(p, (os.path.splitext(
                    os.path.basename(p))[0], ""))
                results.append({
                    "icon": Icon.MUSIC_NOTE, "title": title or os.path.basename(p),
                    "subtitle": artist or "—",
                    "run": (lambda path=p: self._play_path_standalone(path)),
                })
        except Exception:
            log.exception("palette search failed")
        # albums / artists quick entries
        try:
            if getattr(self, "store", None) is not None:
                for alb in self.store.db.album_groups()[:400]:
                    if q.lower() in (alb["album"] or "").lower():
                        results.append({
                            "icon": Icon.ALBUMS, "title": alb["album"],
                            "subtitle": f"Album · {alb['artist']}",
                            "run": (lambda a=alb["album"], ar=alb["artist"]:
                                    self._open_album_from_palette(a, ar)),
                        })
                        if len(results) >= 16:
                            break
        except Exception:
            pass
        return results

    def _open_album_from_palette(self, album, artist):
        try:
            self._on_rail_clicked("albums")
            page = self.albums_page
            page._open_album((album, artist))
        except Exception:
            log.exception("palette album open failed")

    def _play_path_standalone(self, path):
        if path and path in self.library:
            idx = self.library.index(path)
            self._play_from_list(list(self.library), idx, self.current_view)
        elif path:
            self._play_from_list([path], 0, self.current_view)

    def _show_command_palette(self):
        try:
            if getattr(self, "_palette", None) is None:
                self._palette = CommandPalette(
                    self, self._command_actions, self._command_search)
                self._palette.set_theme(self.theme)
            self._palette.popup()
        except Exception:
            log.exception("command palette failed")

    def _play_next_album(self):
        try:
            cur = self.current_track
            if not cur:
                return
            info = (self.store.db.get_track(cur) if getattr(self, "store", None)
                    else None)
            if info and info.get("album"):
                album_tracks = self.store.db.tracks_where(
                    "album=?", (info["album"],), order="track_no")
                rest = [t["path"] for t in album_tracks if t["path"] != cur]
                if rest:
                    self._insert_next(rest)
                    self._show_toast(f"Queued {len(rest)} tracks from album")
                    return
            folder = os.path.dirname(cur)
            self._insert_next([p for p in self.current_playlist
                               if os.path.dirname(p) == folder and p != cur])
        except Exception:
            log.exception("play next album failed")

    # ---------------- shortcuts (v3 rebindable) -------------------------
    _SHORTCUT_DEFAULTS = [
        ("play_pause", "Play / Pause", "Space"),
        ("prev", "Previous track", "Ctrl+Left"),
        ("next", "Next track", "Ctrl+Right"),
        ("volume_up", "Volume up", "Ctrl+Up"),
        ("volume_down", "Volume down", "Ctrl+Down"),
        ("search_focus", "Focus search", "Ctrl+F"),
        ("command_palette", "Command palette", "Ctrl+K"),
        ("mini_player", "Mini player", "Ctrl+M"),
        ("jump_playing", "Jump to playing track", "Ctrl+J"),
        ("fullscreen", "Fullscreen player", "F11"),
        ("lyrics_panel", "Lyrics panel", "Ctrl+L"),
        ("queue_panel", "Queue panel", "Ctrl+Q"),
        ("toggle_favorite", "Like current track", "Ctrl+B"),
        ("sleep_timer", "Sleep timer", "Ctrl+T"),
        ("theme_picker", "Theme picker", "Ctrl+P"),
        ("rate_current", "Rate current track", "Ctrl+R"),
    ]

    def shortcut_defs(self):
        return [dict(id=i, title=t, default=d)
                for i, t, d in self._SHORTCUT_DEFAULTS]

    def _shortcut_key(self, action_id: str) -> str:
        custom = self.prefs.get("custom_shortcuts", {})
        if isinstance(custom, dict) and custom.get(action_id):
            seq = str(custom[action_id])
            if seq.strip():
                return seq
        for i, _t, d in self._SHORTCUT_DEFAULTS:
            if i == action_id:
                return d
        return ""

    def _setup_shortcuts(self):
        """(Re)bind global window shortcuts; v3 honors custom bindings."""
        try:
            self._setup_shortcuts_v3()
        except Exception:
            log.exception("v3 shortcut setup failed")

    def _setup_shortcuts_v3(self):
        # remove previous
        for sc in getattr(self, "_v3_shortcuts", []):
            try:
                sc.setEnabled(False)
                sc.deleteLater()
            except Exception:
                pass
        self._v3_shortcuts = []
        from PyQt6.QtGui import QShortcut, QKeySequence
        actions = {
            "search_focus": lambda: (self.search_edit.setFocus(),
                                     self.search_edit.selectAll()),
            "command_palette": self._show_command_palette,
            "mini_player": self._toggle_mini_player,
            "jump_playing": self._jump_to_playing,
            "prev": self._on_prev,
            "next": self._on_next,
            "fullscreen": self._toggle_fullscreen_player,
            "lyrics_panel": self._toggle_lyrics_panel,
            "queue_panel": (self._toggle_queue_panel
                            if hasattr(self, "_toggle_queue_panel")
                            else (lambda: None)),
            "toggle_favorite": lambda: self._toggle_favorite_for(
                self.current_track) if self.current_track else None,
            "sleep_timer": (self._show_sleep_dialog
                            if hasattr(self, "_show_sleep_dialog")
                            else (lambda: None)),
            "theme_picker": self._show_theme_picker,
            "rate_current": self._rate_current_dialog,
        }
        volume_up = lambda: self.player_bar.vol_slider.setValue(
            min(100, self.player_bar.vol_slider.value() + 5))
        volume_down = lambda: self.player_bar.vol_slider.setValue(
            max(0, self.player_bar.vol_slider.value() - 5))
        for aid in ("volume_up", "volume_down"):
            actions[aid] = (volume_up if aid == "volume_up" else volume_down)
        for aid, handler in actions.items():
            key = self._shortcut_key(aid)
            if not key:
                continue
            try:
                sc = QShortcut(QKeySequence(key), self)
                sc.activated.connect(lambda h=handler: h())
                self._v3_shortcuts.append(sc)
            except Exception:
                log.warning("bad shortcut binding %s=%s", aid, key)

    def _rate_current_dialog(self):
        """Quick star-rating popup for the current track (Ctrl+R)."""
        if not self.current_track:
            return
        try:
            current = 0
            if getattr(self, "store", None) is not None:
                tr = self.store.db.get_track(self.current_track)
                current = (tr or {}).get("rating", 0) or 0
            from PyQt6.QtWidgets import QDialog, QHBoxLayout, QPushButton
            from icons import Icon as _I, render_icon as _ri, get_dpr as _gd
            dlg = QDialog(self)
            dlg.setWindowTitle("Rate track")
            lay = QHBoxLayout(dlg)
            lay.setContentsMargins(14, 12, 14, 12)
            dpr = _gd()
            for stars in range(1, 6):
                b = QPushButton()
                b.setFlat(True)
                b.setFixedSize(40, 40)
                b.setIcon(_ri(_I.STAR if stars > current else _I.STAR_FILLED,
                              22, self.theme["gold"], dpr))
                b.clicked.connect(lambda _c=False, st=stars: (
                    self._set_track_rating(self.current_track, st), dlg.accept()))
                lay.addWidget(b)
            dlg.exec()
        except Exception:
            log.exception("rate dialog failed")

    def _set_track_rating(self, path, stars: int):
        """Persist a 0-5 star rating (DB + file tags when supported)."""
        if not path:
            return
        stars = max(0, min(5, int(stars)))
        try:
            if getattr(self, "store", None) is not None:
                self.store.db.set_rating(path, stars)
        except Exception:
            log.exception("rating store failed")
        try:
            from gm.rating_tags import write_rating
            write_rating(path, stars)
        except Exception:
            log.exception("rating tag write failed (non-fatal)")
        self._show_toast(f"Rated {'★' * stars or '—'}")
        try:
            self._highlight_playing_in_lists()
        except Exception:
            pass

    def _toggle_favorite_for(self, path):
        if not path:
            return
        if path in self.favorites:
            self.favorites.discard(path)
        else:
            self.favorites.add(path)
        self.player_bar.update_like_button(path in self.favorites)
        self._rebuild_favorites_list()
        self._save_config()

    # ---------------- smart playlists sync ------------------------------
    def _sync_smart_playlists(self):
        try:
            store = getattr(self, "store", None)
            if store is None or getattr(store, "db", None) is None:
                return
            wanted = []
            if self.prefs.get("smart_most_played"):
                wanted.append("most_played")
            if self.prefs.get("smart_recently_added"):
                wanted.append("recently_added")
            if self.prefs.get("smart_never_played"):
                wanted.append("never_played")
            if self.prefs.get("smart_not_heard_recently"):
                wanted.append("not_heard_recently")
            if self.prefs.get("smart_top_rated"):
                wanted.append("top_rated")
            existing = {p["name"] for p in store.smart_playlists()}
            from gm.smart import BUILTINS
            for key in wanted:
                label = BUILTINS[key]["label"]
                if label not in existing:
                    store.ensure_smart_playlist(key)
            # remove smart playlists whose toggle went off
            for pl in store.smart_playlists():
                if pl["name"] not in {BUILTINS[k]["label"] for k in wanted}:
                    store.db.delete_playlist(pl["id"])
        except Exception:
            log.exception("smart playlist sync failed")

    def smart_playlist_rows(self):
        rows = []
        try:
            store = getattr(self, "store", None)
            if store is None or getattr(store, "db", None) is None:
                return rows
            for pl in store.smart_playlists():
                n = len(store.smart_tracks(pl["id"]))
                rows.append({"name": pl["name"], "id": pl["id"], "count": n})
        except Exception:
            log.exception("smart rows failed")
        return rows

    def request_play_smart(self, key_or_label: str):
        try:
            store = getattr(self, "store", None)
            if store is None:
                return
            from gm.smart import BUILTINS
            label = key_or_label
            for k, b in BUILTINS.items():
                if k == key_or_label or b["label"] == key_or_label:
                    label = b["label"]
                    break
            for pl in store.smart_playlists():
                if pl["name"] == label:
                    tracks = [t["path"] for t in store.smart_tracks(pl["id"])
                              if os.path.exists(t["path"])]
                    if tracks:
                        self._play_from_list(tracks, 0, self.current_view)
                    else:
                        self._show_toast("No matching tracks yet")
                    return
        except Exception:
            log.exception("play smart failed")

    # ---------------- M3U import/export ----------------------------------
    def request_m3u_import(self):
        try:
            from PyQt6.QtWidgets import QFileDialog
            files, _f = QFileDialog.getOpenFileNames(
                self, "Import playlists (M3U/M3U8)", "",
                "Playlists (*.m3u *.m3u8 *.M3U *.M3U8)")
            for f in files or []:
                self._import_m3u_file(f)
        except Exception:
            log.exception("m3u import failed")

    def _import_m3u_file(self, file_path):
        try:
            from gm.m3u import parse_m3u
            name, entries = parse_m3u(file_path)
            if not name:
                name = os.path.splitext(os.path.basename(file_path))[0]
            resolved = []
            base = os.path.dirname(os.path.abspath(file_path))
            for e in entries:
                p = e if os.path.isabs(e) else os.path.normpath(
                    os.path.join(base, e))
                if os.path.exists(p):
                    resolved.append(p)
            if not resolved:
                self._show_toast("No playable files found in playlist")
                return
            self.playlists[name] = resolved
            # also register unknown files into the library
            new = [p for p in resolved if p not in self.library]
            if new:
                self.library.extend(new)
            self._save_config()
            self._rebuild_library_list()
            self._show_toast(f"Imported '{name}' ({len(resolved)} tracks)")
        except Exception:
            log.exception("m3u import file failed")

    def _export_all_playlists_m3u(self):
        self.request_m3u_export()

    def request_m3u_export(self):
        try:
            from PyQt6.QtWidgets import QFileDialog
            import sys as _sys
            d = QFileDialog.getExistingDirectory(self, "Export playlists to…")
            if not d:
                return
            from gm.m3u import export_m3u
            count = 0
            _bad = set('\\/:*?"<>|')
            for name, paths in self.playlists.items():
                safe = "".join(c for c in name if c not in _bad).strip()
                if not safe:
                    continue
                out = os.path.join(d, safe + ".m3u8")
                export_m3u(name, paths, out,
                           relative=bool(self.prefs.get("m3u_relative_paths",
                                                        True)))
                count += 1
            self._show_toast(f"Exported {count} playlist(s) to {d}")
        except Exception:
            log.exception("m3u export failed")

    # ---------------- radio page ----------------------------------------
    def _build_radio_page(self):
        from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QLabel,
                                     QListWidget, QListWidgetItem,
                                     QHBoxLayout, QPushButton, QAbstractItemView)
        page = QWidget()
        page.setObjectName("RadioPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)
        top = QHBoxLayout()
        self.radio_title = QLabel("Radio")
        self.radio_title.setObjectName("PageTitle")
        top.addWidget(self.radio_title, 1)
        self.radio_add_btn = QPushButton("Add Station")
        self.radio_add_btn.setObjectName("GoldBtn")
        self.radio_add_btn.clicked.connect(self._add_radio_station)
        top.addWidget(self.radio_add_btn)
        layout.addLayout(top)
        self.radio_list = QListWidget()
        self.radio_list.setObjectName("TrackList")
        self.radio_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.radio_list.itemDoubleClicked.connect(
            lambda item: self._play_radio_station(item.data(Qt.ItemDataRole.UserRole)))
        layout.addWidget(self.radio_list, 1)
        hint = QLabel("Stream URLs (http(s)://…, incl. M3U/HLS). "
                      "Double-click to play.")
        hint.setStyleSheet(f"color: {self.theme.get('muted', '#888')};")
        layout.addWidget(hint)
        return page

    def _refresh_radio_page(self):
        try:
            self.radio_list.clear()
            stations = []
            if getattr(self, "store", None) is not None:
                stations = self.store.db.radio_stations()
            for st in stations:
                host = st["url"].split("//")[-1].split("/")[0]
                item = QListWidgetItem(f"{st['name']}   ·   {host}")
                item.setData(Qt.ItemDataRole.UserRole, st)
                self.radio_list.addItem(item)
            if not stations:
                from PyQt6.QtWidgets import QListWidgetItem as _I
                it = _I("No stations yet — add one to start listening.")
                it.setFlags(Qt.ItemFlag.NoItemFlags)
                self.radio_list.addItem(it)
        except Exception:
            log.exception("radio refresh failed")

    def radio_rows(self):
        rows = []
        try:
            if getattr(self, "store", None) is not None:
                for st in self.store.db.radio_stations():
                    rows.append({"id": st["id"], "name": st["name"],
                                 "url": st["url"]})
        except Exception:
            pass
        return rows

    def _add_radio_station(self):
        try:
            from PyQt6.QtWidgets import QInputDialog
            name, ok = QInputDialog.getText(self, "Add station", "Station name:")
            if not ok or not name.strip():
                return
            url, ok2 = QInputDialog.getText(self, "Add station", "Stream URL:")
            if not ok2 or not url.strip():
                return
            url = url.strip()
            if not url.startswith(("http://", "https://")):
                self._show_toast("Only http(s) stream URLs are allowed")
                return
            if getattr(self, "store", None) is not None:
                self.store.db.add_radio(name.strip(), url)
                self._refresh_radio_page()
                self._show_toast(f"Added '{name.strip()}'")
        except Exception:
            log.exception("add radio failed")

    def _play_radio_station(self, station):
        try:
            if not station:
                return
            self._play_url_stream(station["name"], station["url"])
        except Exception:
            log.exception("radio play failed")

    def request_play_radio(self, station_id):
        try:
            for st in self.radio_rows():
                if st["id"] == station_id:
                    self._play_url_stream(st["name"], st["url"])
                    return
        except Exception:
            log.exception("request radio failed")

    def request_remove_radio(self, station_id):
        try:
            if getattr(self, "store", None) is not None:
                self.store.db.remove_radio(station_id)
                self._refresh_radio_page()
        except Exception:
            log.exception("remove radio failed")

    def request_add_radio(self):
        self._add_radio_station()

    def _play_url_stream(self, name: str, url: str):
        """Play an internet stream (radio). Marks now-playing accordingly."""
        try:
            self.current_track = url
            self.current_index = -1
            self.current_playlist = []
            self._is_stream = True
            self.now_title.setText(name)
            self.now_artist.setText(url.split("//")[-1].split("/")[0])
            self.player_bar.title_label.setMarqueeText(name)
            self.player_bar.artist_label.setMarqueeText("Internet radio")
            self.player_bar.update_like_button(False)
            self.player_bar.update_play_button(1)
            self.audio.load_and_play(url)
            if hasattr(self, "_load_cover_async"):
                try:
                    self.cover_label.set_cover(None)
                    self.player_bar.set_cover_thumb(None)
                except Exception:
                    pass
            self._show_toast(f"Streaming {name}")
        except Exception:
            log.exception("stream play failed")

    # ---------------- folder watching ------------------------------------
    def _apply_folder_watch(self):
        try:
            enabled = bool(self.prefs.get("folder_watch", True))
            if enabled:
                self._start_folder_watcher()
            else:
                self._stop_folder_watcher()
        except Exception:
            log.exception("folder watch apply failed")

    def _start_folder_watcher(self):
        try:
            from PyQt6.QtCore import QFileSystemWatcher
            if getattr(self, "_folder_watcher", None) is None:
                self._folder_watcher = QFileSystemWatcher(self)
                self._folder_watcher.directoryChanged.connect(
                    self._on_folder_changed)
                self._folder_watcher.fileChanged.connect(
                    self._on_watched_file_changed)
            # watch added folders (non-recursive: each registered folder +
            # its direct subfolders are watched; deep trees rely on rescan)
            roots = list(self.added_folders or [])
            want = set()
            for root in roots:
                try:
                    if os.path.isdir(root):
                        want.add(root)
                        for sub in self._list_subdirs(root)[:60]:
                            want.add(sub)
                except OSError:
                    continue
            current = set(self._folder_watcher.directories())
            add = list(want - current)
            rem = list(current - want)
            if add:
                self._folder_watcher.addPaths(add)
            if rem:
                self._folder_watcher.removePaths(rem)
        except Exception:
            log.exception("folder watcher start failed")

    def _stop_folder_watcher(self):
        try:
            w = getattr(self, "_folder_watcher", None)
            if w is not None:
                dirs = list(w.directories())
                if dirs:
                    w.removePaths(dirs)
        except Exception:
            pass

    def _on_watched_file_changed(self, path: str):
        # single file changed: refresh tags for it soon (debounced)
        self._schedule_watch_debounce()

    def _on_folder_changed(self, folder: str):
        self._schedule_watch_debounce()

    def _schedule_watch_debounce(self):
        try:
            from PyQt6.QtCore import QTimer
            ms = int(self.prefs.get("folder_watch_debounce_ms", 2000))
            if getattr(self, "_watch_debounce", None) is None:
                self._watch_debounce = QTimer(self)
                self._watch_debounce.setSingleShot(True)
                self._watch_debounce.timeout.connect(self._on_watch_debounced)
            self._watch_debounce.start(max(250, ms))
        except Exception:
            pass

    def _on_watch_debounced(self):
        """After quiet period: incremental scan of changed folders only."""
        try:
            if not self.added_folders:
                return
            self._on_refresh()
        except Exception:
            log.exception("watch rescan failed")

    # ---------------- favorites collections ------------------------------
    def favorite_collections(self):
        try:
            if getattr(self, "store", None) is not None:
                return self.store.db.favorite_categories()
        except Exception:
            pass
        return []


    # ---------------- Up Next queue panel (v3) ---------------------------
    def _toggle_queue_panel(self):
        try:
            if getattr(self, "_queue_panel_w", None) is None:
                from queue_panel import QueuePanel
                self._queue_panel_w = QueuePanel(self, self)
                self.main_layout.insertWidget(
                    self.main_layout.indexOf(self.stack) + 1,
                    self._queue_panel_w)
            self._queue_panel_w.toggle()
            if self._queue_panel_w.isVisible():
                self._queue_panel_w.refresh()
        except Exception:
            log.exception("queue panel toggle failed")

    def _insert_next(self, paths):
        """'Play Next': insert paths right after the current index."""
        if not paths:
            return
        try:
            paths = [p for p in paths if isinstance(p, str)]
            if not self.current_playlist:
                self.current_playlist = list(paths)
                self.current_index = 0
                self._load_and_play_current()
                return
            at = self.current_index + 1 if self.current_index >= 0 else 0
            self.current_playlist[at:at] = paths
            if getattr(self, "_queue_panel_w", None) is not None and \
                    self._queue_panel_w.isVisible():
                self._queue_panel_w.refresh()
            self._show_toast(f"Playing next: {len(paths)} track(s)")
        except Exception:
            log.exception("insert next failed")

    def _append_queue(self, paths):
        if not paths:
            return
        try:
            self.current_playlist.extend(p for p in paths
                                         if isinstance(p, str))
            if getattr(self, "_queue_panel_w", None) is not None and \
                    self._queue_panel_w.isVisible():
                self._queue_panel_w.refresh()
        except Exception:
            log.exception("append queue failed")

    # ---------------- sleep timer dialog ---------------------------------
    def _show_sleep_dialog(self):
        try:
            from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                                         QPushButton, QLabel, QSpinBox)
            from PyQt6.QtCore import Qt as _Qt
            dlg = QDialog(self)
            dlg.setWindowTitle("Sleep Timer")
            lay = QVBoxLayout(dlg)
            lay.setContentsMargins(18, 14, 18, 14)
            info = QLabel("Stop playback after this many minutes:")
            lay.addWidget(info)
            row = QHBoxLayout()
            sb = QSpinBox()
            sb.setRange(1, 480)
            sb.setValue(int(self.sleep_timer_minutes or 30))
            row.addWidget(sb)
            lay.addLayout(row)
            btns = QHBoxLayout()
            ok = QPushButton("Start")
            ok.setObjectName("GoldBtn")
            ok.clicked.connect(lambda: (self.start_sleep_timer(sb.value()),
                                        dlg.accept()))
            btns.addWidget(ok)
            stop = QPushButton("Stop timer")
            stop.clicked.connect(lambda: (self._stop_sleep_timer(), dlg.accept()))
            btns.addWidget(stop)
            cancel = QPushButton("Cancel")
            cancel.clicked.connect(dlg.reject)
            btns.addWidget(cancel)
            lay.addLayout(btns)
            dlg.exec()
        except Exception:
            log.exception("sleep dialog failed")

    def _stop_sleep_timer(self):
        try:
            if getattr(self, "_sleep_timer", None) is not None:
                self._sleep_timer.stop()
            self.sleep_timer_active = False
            self._save_config()
            self._show_toast("Sleep timer off")
        except Exception:
            pass

    # ---------------- duplicates + backup helpers ------------------------
    def _open_duplicates_dialog(self):
        try:
            from duplicates import group_duplicates
            from duplicates_dialog import DuplicatesDialog
            groups = group_duplicates(list(self.library),
                                      self._tag_cache.get)
            if not groups:
                self._show_toast("No duplicates found")
                return
            dlg = DuplicatesDialog(self, groups, self)
            dlg.exec()
            self._rebuild_library_list()
            self._save_config()
        except Exception:
            log.exception("duplicates dialog failed")

    def _open_backup_restore(self):
        try:
            from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                                         QPushButton, QLabel, QMessageBox)
            import userbackup
            data_dir = config_path().parent
            dlg = QDialog(self)
            dlg.setWindowTitle("Backup / Restore")
            dlg.resize(420, 160)
            lay = QVBoxLayout(dlg)
            lay.setContentsMargins(18, 14, 18, 14)
            info = QLabel("Backups live in:\n" + str(data_dir / "backups"))
            info.setWordWrap(True)
            lay.addWidget(info)
            row = QHBoxLayout()
            b1 = QPushButton("Back up now")
            b1.setObjectName("GoldBtn")
            def _do_backup():
                userbackup.create_startup_backup(data_dir)
                self._show_toast("Backup created")
                dlg.accept()
            b1.clicked.connect(_do_backup)
            row.addWidget(b1)
            b2 = QPushButton("Restore missing data files")
            b2.clicked.connect(lambda: (
                userbackup.restore_missing(data_dir),
                self._show_toast("Restore attempted"), dlg.accept()))
            row.addWidget(b2)
            close = QPushButton("Close")
            close.clicked.connect(dlg.reject)
            row.addWidget(close)
            lay.addLayout(row)
            dlg.exec()
        except Exception:
            log.exception("backup dialog failed")

    # ---------------- audio/remote/floating pref appliers ----------------
    def _apply_audio_prefs(self):
        """Apply engine/EQ/crossfade/normalization prefs (safe no-ops before
        the v3 audio layer is constructed)."""
        try:
            adapter = getattr(self, "audio_v3", None)
            if adapter is None:
                return
            adapter.apply_prefs(self.prefs)
        except Exception:
            log.exception("audio prefs apply failed")

    def _apply_remote_prefs(self):
        """Start/stop the web remote + telegram bot per prefs."""
        try:
            from gm.remote_bridges import sync_remotes
            sync_remotes(self)
        except Exception:
            log.exception("remote prefs apply failed")

    def _apply_floating_lyrics(self):
        try:
            want = bool(self.prefs.get("floating_lyrics", False))
            fw = getattr(self, "_floating_lyrics_w", None)
            if want and fw is None:
                from gm.ui.lyrics_ui import FloatingLyricsWidget
                self._floating_lyrics_w = FloatingLyricsWidget()
                self._floating_lyrics_w.set_theme(self.theme)
                self._floating_lyrics_w.seek_requested.connect(
                    lambda ms: self.audio.set_position(int(ms)))
                self._floating_lyrics_w.show()
            elif not want and fw is not None:
                fw.close()
                fw.deleteLater()
                self._floating_lyrics_w = None
            elif want and fw is not None:
                fw.set_theme(self.theme)
        except Exception:
            log.exception("floating lyrics apply failed")


    # ------------------------------------------------------------------
    # v3.0 — gapless handoff, waveform seekbar, spectrum tap, bookmarks
    # ------------------------------------------------------------------
    def _peek_next_path(self):
        """What the queue logic WILL play next (no side effects) — used to
        arm gapless/crossfade preload."""
        try:
            if not self.current_playlist:
                return None
            if self.repeat_mode == RepeatMode.ONE:
                return self.current_track
            if self.shuffle:
                pool = [p for p in self.shuffle_unplayed
                        if p in self.current_playlist and p != self.current_track]
                if pool:
                    return pool[0]
                others = [p for p in self.current_playlist
                          if p != self.current_track]
                return others[0] if others else None
            nxt = self.current_index + 1
            if 0 <= nxt < len(self.current_playlist):
                return self.current_playlist[nxt]
            if self.repeat_mode == RepeatMode.ALL and self.current_playlist:
                return self.current_playlist[0]
        except Exception:
            pass
        return None

    def _on_gapless_swap(self, path):
        """The adapter finished the dual-engine handoff: adopt the new track
        WITHOUT reloading audio (that is the whole point of gapless)."""
        try:
            if not path:
                return
            self._finalize_listen_credit()
            self._listened_ms = 0
            if getattr(self, "fx", None):
                self.fx.clear_ab()
            self.current_track = path
            try:
                self.current_index = self.current_playlist.index(path)
            except ValueError:
                self.current_index = -1
            self.shuffle_unplayed.discard(path)
            self._push_history(path)
            title, artist = self._tag_cache.get(
                path, (os.path.splitext(os.path.basename(path))[0], ""))
            self.now_title.setMarqueeText(title)
            self.now_artist.setMarqueeText(artist)
            self.player_bar.title_label.setMarqueeText(title)
            self.player_bar.artist_label.setMarqueeText(artist)
            self.player_bar.update_like_button(path in self.favorites)
            self._load_cover_async(path)
            self._highlight_playing_in_lists()
            self._maybe_toast_track_change()
            self._start_track_analytics(path)
        except Exception:
            log.exception("gapless swap UI adoption failed")

    def _start_track_analytics(self, path):
        """Per-track setup: normalization gain, waveform, spectrum tap,
        bookmark resume. Called for every track start (incl. gapless swaps)."""
        try:
            if not path or str(path).startswith(("http://", "https://")):
                return
            # --- loudness normalization (ReplayGain tags) ---
            try:
                if self.prefs.get("normalization", "off") == "tags" and \
                        getattr(self, "audio_v3", None) is not None:
                    from gm.normalize import Normalizer, read_replaygain_tags
                    tags = read_replaygain_tags(path)
                    gain = None
                    if tags.get("track_gain_db") is not None:
                        gain = tags["track_gain_db"]
                    elif tags.get("album_gain_db") is not None:
                        gain = tags["album_gain_db"]
                    if gain is not None:
                        self.audio_v3.set_track_gain_db(float(gain))
                    else:
                        self.audio_v3.set_track_gain_db(0.0)
            except Exception:
                pass
            # --- waveform seekbar ---
            try:
                self._start_waveform_for(path)
            except Exception:
                pass
            # --- spectrum tap ---
            try:
                self._start_spectrum_tap(path)
            except Exception:
                pass
            # --- bookmark resume ---
            try:
                self._maybe_resume_bookmark(path)
            except Exception:
                pass
            # --- floating lyrics / mini lyrics bar feed ---
            try:
                self._feed_lyrics_widgets(path)
            except Exception:
                pass
        except Exception:
            log.exception("track analytics failed")

    def _feed_lyrics_widgets(self, path):
        """Background-load lyrics for the floating widget + panel refresh."""
        if not path or str(path).startswith(("http://", "https://")):
            return
        fw = getattr(self, "_floating_lyrics_w", None)
        p3 = getattr(self, "_lyrics_panel3", None)
        if fw is None and (p3 is None or not p3.isVisible()):
            return
        title, artist = self._tag_cache.get(
            path, (os.path.splitext(os.path.basename(path))[0], ""))
        from gm.lyrics_flow import LyricsSearchWorker
        if getattr(self, "_lyrics_feed_worker", None) is not None and \
                self._lyrics_feed_worker.isRunning():
            self._lyrics_feed_worker.wait(400)
        self._lyrics_feed_worker = LyricsSearchWorker(
            path, title, artist, 0,
            online=bool(self.prefs.get("lyrics_online", True)), parent=self)
        self._lyrics_feed_worker.ready.connect(self._on_lyrics_feed)
        self._lyrics_feed_worker.start()

    def _on_lyrics_feed(self, path, ly, _cands):
        if path != self.current_track or ly is None or ly.is_empty():
            return
        try:
            offset = 0
            if getattr(self, "store", None) is not None:
                offset = self.store.db.lyrics_offset(path)
            from gm.lyrics_io import apply_offset
            lines = apply_offset(list(ly.lines), offset)
            fw = getattr(self, "_floating_lyrics_w", None)
            if fw is not None and fw.isVisible():
                fw.set_lines(lines)
        except Exception:
            pass

    def _start_waveform_for(self, path):
        if not self.prefs.get("waveform_seekbar", True):
            self.player_bar.seek_slider.clear_peaks()
            return
        worker = getattr(self, "_waveform_worker", None)
        if worker is not None and worker.isRunning():
            worker.cancel()
            worker.wait(500)
        from gm.audio_tap import WaveformWorker, HAVE_MINIAUDIO
        if not HAVE_MINIAUDIO:
            self.player_bar.seek_slider.clear_peaks()
            self._waveform_unavailable = True
            return
        db = self.store.db if getattr(self, "store", None) is not None else None
        self._waveform_worker = WaveformWorker(path, db, parent=self)
        self._waveform_worker.peaks_ready.connect(self._on_peaks_ready)
        self._waveform_worker.start()

    def _on_peaks_ready(self, path, peaks):
        try:
            if self.current_track == path and peaks:
                self.player_bar.seek_slider.set_peaks(peaks)
            elif self.current_track == path:
                self.player_bar.seek_slider.clear_peaks()
        except Exception:
            pass

    def _start_spectrum_tap(self, path):
        """Mark the tap as WANTED for this path; actual thread start waits
        for genuinely advancing playback (see _ui_tick) — a stubbed or
        non-playing backend never spawns the thread."""
        if not self.prefs.get("visualizer_enabled", True):
            return
        self._tap_wanted = path
        self._tap_progress = 0
        self._tap_last_pos = -1

    def _maybe_begin_tap(self, pos):
        try:
            wanted = getattr(self, "_tap_wanted", None)
            if not wanted or getattr(self, "_content_tap", None) is not None:
                return
            last = getattr(self, "_tap_last_pos", -1)
            prog = getattr(self, "_tap_progress", 0)
            if last >= 0 and pos > last:
                prog += 1
            else:
                prog = 0
            self._tap_last_pos = pos
            self._tap_progress = prog
            if prog >= 3:
                from gm.audio_tap import ContentTap
                tap = ContentTap(self)
                tap.spectrum_ready.connect(self._on_spectrum)
                self._content_tap = tap
                tap.start_tap(wanted,
                              lambda: self.audio.position(),
                              lambda: self.audio.state())
        except Exception:
            pass

    def _on_spectrum(self, mags):
        try:
            viz = getattr(self.player_bar, "spectrum", None)
            if viz is not None:
                if mags is None:
                    viz.hide()
                    return
                viz.set_magnitudes(mags)
        except Exception:
            pass

    def _maybe_resume_bookmark(self, path):
        if not self.prefs.get("bookmarks_enabled", True):
            return
        if getattr(self, "store", None) is None:
            return
        dur = self.audio.duration()
        if not dur or dur < 10 * 60 * 1000:      # only long files
            return
        pos = self.store.db.get_bookmark(path)
        if pos and pos > 5000:
            self.audio.set_position(int(pos))
            self._show_toast(f"Resumed at {fmt_time(pos)}")

    def _bookmark_autosave(self):
        try:
            if not self.prefs.get("bookmarks_enabled", True):
                return
            if getattr(self, "store", None) is None or not self.current_track:
                return
            if str(self.current_track).startswith(("http://", "https://")):
                return
            dur = self.audio.duration()
            if dur and dur >= 10 * 60 * 1000 and self.audio.state() == 1:
                pos = self.audio.position()
                if 5000 < pos < dur * 0.97:
                    self.store.db.set_bookmark(self.current_track, int(pos))
        except Exception:
            pass

    def _clear_bookmark(self, path):
        try:
            if getattr(self, "store", None) is not None and path:
                self.store.db.delete_bookmark(path)
        except Exception:
            pass

    def _stats_toggle_favorite(self, path, on):
        try:
            if on:
                self.favorites.add(path)
            else:
                self.favorites.discard(path)
            if self.current_track == path:
                self.player_bar.update_like_button(path in self.favorites)
            self._rebuild_favorites_list()
            self._save_config()
        except Exception:
            log.exception("stats favorite toggle failed")

    def _play_artist_from_stats(self, row):
        try:
            rows = self.stats_page.artist_chart.rows()
            artist = rows[row][0] if row < len(rows) else None
            if not artist or getattr(self, "store", None) is None:
                return
            tracks = self.store.db.tracks_where("artist=?", (artist,))
            paths = [t["path"] for t in tracks if os.path.exists(t["path"])]
            if paths:
                self._play_from_list(paths, 0, self.current_view)
        except Exception:
            log.exception("stats artist play failed")

    def _store_remove(self, paths):
        """Explicit removal from the SQLite store (v3.0). Called by every
        UI action that removes tracks/folders; _save_config's reconcile is
        conservative (only drops files that vanished from disk)."""
        store = getattr(self, "store", None)
        if store is None or getattr(store, "db", None) is None:
            return
        try:
            store.sync_remove(list(paths))
        except Exception:
            log.exception("store removal failed")

    def _save_config(self):
        # NEVER-WIPE GUARDS (data-loss fix):
        # 1. Scratch/Temp paths (test sandboxes) are never persisted.
        #    (When the app's own data dir lives in a sandbox — testenv —
        #    the filter is disabled: there is no real user config to
        #    protect, and the sandbox must be able to persist its own
        #    fixture paths. Same rule as gm.store._sandboxed.)
        _sandboxed = _is_scratch_path(str(config_path().parent))
        _keep = (lambda p: True) if _sandboxed else (
            lambda p: not _is_scratch_path(p))
        lib = [p for p in self.library if _keep(p)]
        favs = sorted(p for p in self.favorites if _keep(p))
        folders = [f for f in self.added_folders if _keep(f)]
        playlists = {name: [p for p in paths if _keep(p)]
                     for name, paths in self.playlists.items()
                     if isinstance(name, str) and isinstance(paths, list)}
        last = (self.current_track if self.remember_track else None)
        if last is not None and not _keep(last):
            last = None
        # 2. Never replace a non-empty saved library with an empty one
        #    (failed load / foreign in-memory state) — keep the disk data
        #    and persist settings around it.  v3.0: the authoritative copy
        #    is the SQLite store when available (JSON kept for downgrade
        #    safety); the guard applies to whichever source is live.
        store = getattr(self, "store", None)
        try:
            if store is not None and getattr(store, "db", None) is not None:
                db_count = store.db.track_count()
                if not lib and db_count:
                    lib = store.load_library()
                    favs = sorted(store.load_favorites())
                    folders = [f for f in self.added_folders
                               if not _is_scratch_path(f)]
                    playlists = store.load_playlists()
                    log.warning("save guard: kept DB library "
                                "(in-memory library was empty)")
            else:
                raw = config_path().read_text(encoding="utf-8")
                old = json.loads(raw) if raw.strip() else {}
                if not lib and isinstance(old, dict) and old.get("library"):
                    lib = [q for q in old["library"] if isinstance(q, str)]
                    favs = sorted(q for q in old.get("favorites", [])
                                  if isinstance(q, str))
                    folders = [q for q in old.get("added_folders", [])
                               if isinstance(q, str)]
                    old_pl = old.get("playlists", {})
                    if isinstance(old_pl, dict):
                        playlists = {str(k): [q for q in v if isinstance(q, str)]
                                     for k, v in old_pl.items()
                                     if isinstance(k, str) and isinstance(v, list)}
                    log.warning("save guard: kept on-disk library "
                                "(in-memory library was empty)")
        except Exception:
            pass
        cfg = {
            "version": APP_VERSION,
            "theme": self.theme_name,
            "prefs": self.prefs,
            "library": lib,
            "favorites": favs,
            "added_folders": folders,
            "playlists": playlists,
            "volume": self.player_bar.vol_slider.value() if hasattr(self, "player_bar") else 80,
            "last_track": last,
            "last_position": self.audio.position() if self.remember_track and self.audio.available else 0,
            "shuffle": self.shuffle,
            "repeat_mode": self.repeat_mode.value,
            # Persist geometry even while hidden-to-tray: Qt remembers the
            # pre-hide normal geometry, so quitting from the tray no longer
            # resets window position/size on next launch.
            "geometry": self.saveGeometry().data().hex(),
            "auto_rescan": self.auto_rescan,
            "remember_track": self.remember_track,
            "taskbar_close_to_tray": self.taskbar_close_to_tray,
            "default_folder": self.default_folder,
            "sleep_timer_active": self.sleep_timer_active,
            "sleep_timer_minutes": self.sleep_timer_minutes,
            "sort_mode": self.sort_mode.value,
            # Settings-dialog fields that previously reset on every restart
            "lyrics_online": bool(getattr(self, "lyrics_online_enabled", True)),
            "audio_output_id": str(getattr(self, "audio_output_id", "") or ""),
            "auto_theme": bool(getattr(self, "auto_theme_enabled", False)),
            "toast_enabled": bool(getattr(self, "_toast_enabled", True)),
            "tray_notice_shown": bool(getattr(self, "tray_notice_shown", False)),
            "updates_enabled": bool(getattr(self, "updates_enabled", True)),
            "update_last_check": float(getattr(self, "update_last_check", 0) or 0),
            "update_etag": str(getattr(self, "update_etag", "") or ""),
            "update_skip_version": str(getattr(self, "update_skip_version", "") or ""),
            "update_snooze_until": float(getattr(self, "update_snooze_until", 0) or 0),
            "visualizer_enabled": bool(getattr(self, "visualizer_enabled", True)),
            "default_volume": self._validated_int(getattr(self, "_last_volume", 80), 80, 0, 100),
            "fade_enabled": bool(getattr(self.fx, "enabled_fade", True)) if getattr(self, "fx", None) else True,
            "fade_ms": self._validated_int(getattr(self.fx, "fade_ms", 300), 300, 100, 2000) if getattr(self, "fx", None) else 300,
            "show_ab_button": (self.player_bar.ab_btn.isVisible() if hasattr(self, "player_bar") else False),
            "show_rate_button": (self.player_bar.rate_btn.isVisible() if hasattr(self, "player_bar") else False),
            "show_lyrics_button": (self.player_bar.lyrics_btn.isVisible() if hasattr(self, "player_bar") else True),
        }
        try:
            p = config_path()
            p.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
            restrict_file_permissions(p)
        except Exception as e:
            log.error(f"Config save error: {e}")
        # v3.0: write-through to SQLite (source of truth). The JSON copy
        # above is kept for 2.x downgrade safety — see docs/DECISIONS.md.
        if store is not None and getattr(store, "db", None) is not None:
            try:
                store.sync_library(lib)
                store.persist_favorites(set(favs))
                store.persist_playlists(playlists)
            except Exception as e:
                log.error(f"Store save error: {e}")
        # Also save tag cache
        self._save_tag_cache()

    def _load_config(self):
        cfg, ok = load_json_file(config_path(), "config")
        if not ok:
            return

        self.theme_name = cfg.get("theme", "royal_gold")
        # v3.0: library data prefers the SQLite store; the JSON keys are
        # the fallback (legacy mode when the migration could not verify).
        if getattr(self, "store", None) is not None and \
                getattr(self.store, "db", None) is not None:
            try:
                self.library = self.store.load_library()
                self.favorites = self.store.load_favorites()
                self.playlists = self.store.load_playlists()
                self.added_folders = cfg.get("added_folders", [])
            except Exception:
                log.exception("store load failed; falling back to JSON")
                self.library = cfg.get("library", [])
                self.favorites = set(cfg.get("favorites", []))
                self.added_folders = cfg.get("added_folders", [])
                raw_pl = cfg.get("playlists", {})
                if isinstance(raw_pl, dict):
                    self.playlists = {str(k): [p for p in v if isinstance(p, str)]
                                      for k, v in raw_pl.items()
                                      if isinstance(k, str) and isinstance(v, list)}
                else:
                    self.playlists = {}
        else:
            self.library = cfg.get("library", [])
            self.favorites = set(cfg.get("favorites", []))
            self.added_folders = cfg.get("added_folders", [])
        if getattr(self, "store", None) is None:
            raw_pl = cfg.get("playlists", {})
            if isinstance(raw_pl, dict):
                self.playlists = {str(k): [p for p in v if isinstance(p, str)]
                                  for k, v in raw_pl.items()
                                  if isinstance(k, str) and isinstance(v, list)}
            else:
                self.playlists = {}
        vol = cfg.get("volume", 80)
        self._last_volume = vol
        if hasattr(self, "player_bar"):
            self.player_bar.vol_slider.setValue(vol)
        self.shuffle = cfg.get("shuffle", False)
        self.player_bar.update_shuffle_button(self.shuffle)
        rm = cfg.get("repeat_mode", 0)
        self.repeat_mode = RepeatMode(rm)
        self.player_bar.update_repeat_button(self.repeat_mode)
        self.auto_rescan = cfg.get("auto_rescan", True)
        self.remember_track = cfg.get("remember_track", True)
        self.taskbar_close_to_tray = bool(cfg.get("taskbar_close_to_tray", False))
        self.tray_notice_shown = bool(cfg.get("tray_notice_shown", False))
        self.updates_enabled = bool(cfg.get("updates_enabled", True))
        self.update_last_check = float(cfg.get("update_last_check", 0) or 0)
        self.update_etag = str(cfg.get("update_etag", "") or "")
        self.update_skip_version = str(cfg.get("update_skip_version", "") or "")
        self.update_snooze_until = float(cfg.get("update_snooze_until", 0) or 0)
        self.default_folder = cfg.get("default_folder", "")
        self.sleep_timer_active = cfg.get("sleep_timer_active", False)
        self.sleep_timer_minutes = cfg.get("sleep_timer_minutes", 30)
        sm = cfg.get("sort_mode", "title")
        self.sort_mode = SortMode(sm)
        if hasattr(self, "sort_combo"):
            idx = self.sort_combo.findData(sm)
            if idx >= 0:
                self.sort_combo.setCurrentIndex(idx)
        geom_hex = cfg.get("geometry")
        if geom_hex:
            try:
                self.restoreGeometry(bytes.fromhex(geom_hex))
            except Exception:
                pass
        self._apply_theme()
        self.library = [p for p in self.library if os.path.exists(p)]
        self.favorites = {p for p in self.favorites if os.path.exists(p)}
        self._rebuild_library_list()
        self._rebuild_favorites_list()
        self._update_count_label()
        if self.sleep_timer_active:
            self.start_sleep_timer(self.sleep_timer_minutes)
        last = cfg.get("last_track")
        last_pos = cfg.get("last_position", 0)
        if last and last in self.library and self.remember_track:
            idx = self.library.index(last)
            self.current_playlist = list(self.library)
            self.current_index = idx
            self.current_track = last
            title, artist = extract_title_artist(last)
            self.now_title.setMarqueeText(title)
            self.now_artist.setMarqueeText(artist)
            self.player_bar.title_label.setMarqueeText(title)
            self.player_bar.artist_label.setMarqueeText(artist)
            self.player_bar.update_like_button(last in self.favorites)
            self._highlight_playing_in_lists()
            self._load_cover_async(last)
            self.audio.load(last)
            QTimer.singleShot(500, lambda: self._restore_position(last, last_pos))

    def _load_config_async(self):
        """Load config in background — non-blocking.

        v3.0: settings come from config.json; the library (tracks, tags,
        favorites, playlists, stats) is opened from SQLite by
        StoreInitWorker — the first start also runs the verified JSON ->
        SQLite migration off the UI thread. _finish_config_load runs once
        the store is ready (or failed -> legacy JSON fallback).
        """
        # Show loading state
        self.scan_status_label.setText("Loading library...")
        self.scan_status_label.setVisible(True)
        self.scan_progress.setVisible(True)
        self.scan_progress.setRange(0, 0)  # indeterminate

        # Read config file (fast — just JSON, validated)
        cfg, ok = load_json_file(config_path(), "config")
        self._pending_cfg = cfg if (ok and isinstance(cfg, dict)) else {}

        if self._store_worker is not None:      # already loading
            return
        self._store_worker = StoreInitWorker(config_path(), self)
        self._store_worker.ready.connect(self._on_store_ready)
        self._store_worker.start()

    def _on_store_ready(self, store):
        """Store worker finished: swap in DB-backed stats + apply config."""
        try:
            if store is not None and getattr(store, "db", None) is not None:
                self.store = store
                if store.migration.ok:
                    from gm.store import StatsStore
                    try:
                        # seed sessions counter from legacy stats on first run
                        if store.db.meta_get("sessions") is None:
                            import json as _json
                            blob = store.db.meta_get("stats_legacy") or "{}"
                            try:
                                legacy = _json.loads(blob)
                            except ValueError:
                                legacy = {}
                            base = legacy.get("sessions", 0)
                            try:
                                base = int(base)
                            except (TypeError, ValueError):
                                base = 0
                            store.db.meta_set("sessions", str(base))
                        self.stats = StatsStore(store.db)
                        self.stats.bump_session()
                        if getattr(self, "_stats_view_timer", None):
                            pass  # timers keep running; page reads new store
                    except Exception:
                        log.exception("DB stats init failed; keeping legacy")
                else:
                    log.warning("JSON->SQLite migration not verified — "
                                "running in legacy JSON mode this session")
        except Exception:
            log.exception("store ready handler failed")
        finally:
            self._finish_config_load(self._pending_cfg or {})
            self._pending_cfg = None

    @staticmethod
    def _validated_int(value, default: int, lo: int, hi: int) -> int:
        """Coerce a config value to an int in [lo, hi]; default on any junk."""
        try:
            v = int(value)
        except (TypeError, ValueError):
            return default
        return max(lo, min(hi, v))

    @staticmethod
    def _validated_enum(enum_cls, value, default):
        """Coerce a config value to an enum member; default on any junk."""
        try:
            return enum_cls(value)
        except ValueError:
            return default

    def _finish_config_load(self, cfg):
        """Apply loaded config — fast operations only, no tag reading."""
        if cfg is None:
            cfg = {}

        self.scan_status_label.setVisible(False)
        self.scan_progress.setVisible(False)

        # Default to Royal Gold for new installs (v2.0.0 psychology
        # suite); migrate pre-2.0 theme ids so users keep their vibe.
        self.theme_name = Theme.normalize(cfg.get("theme", Theme.DEFAULT_NAME))
        if self.theme_name not in Theme.names():
            self.theme_name = Theme.DEFAULT_NAME
        self.theme = Theme.get(self.theme_name)
        self._load_prefs(cfg)
        # v3.0: library data prefers the SQLite store; the JSON keys are
        # the fallback (legacy mode when the migration could not verify).
        if getattr(self, "store", None) is not None and \
                getattr(self.store, "db", None) is not None:
            try:
                self.library = self.store.load_library()
                self.favorites = self.store.load_favorites()
                self.playlists = self.store.load_playlists()
                self.added_folders = cfg.get("added_folders", [])
            except Exception:
                log.exception("store load failed; falling back to JSON")
                self.library = cfg.get("library", [])
                self.favorites = set(cfg.get("favorites", []))
                self.added_folders = cfg.get("added_folders", [])
                raw_pl = cfg.get("playlists", {})
                if isinstance(raw_pl, dict):
                    self.playlists = {str(k): [p for p in v if isinstance(p, str)]
                                      for k, v in raw_pl.items()
                                      if isinstance(k, str) and isinstance(v, list)}
                else:
                    self.playlists = {}
        else:
            self.library = cfg.get("library", [])
            self.favorites = set(cfg.get("favorites", []))
            self.added_folders = cfg.get("added_folders", [])
        if getattr(self, "store", None) is None:
            raw_pl = cfg.get("playlists", {})
            if isinstance(raw_pl, dict):
                self.playlists = {str(k): [p for p in v if isinstance(p, str)]
                                  for k, v in raw_pl.items()
                                  if isinstance(k, str) and isinstance(v, list)}
            else:
                self.playlists = {}
        # Corrupt/tampered values must never abort the whole load — fall
        # back to defaults per field (ValueError/TypeError safe).
        vol = self._validated_int(cfg.get("volume", 80), 80, 0, 100)
        self._last_volume = vol
        self.player_bar.vol_slider.setValue(vol)
        self.shuffle = bool(cfg.get("shuffle", False))
        self.player_bar.update_shuffle_button(self.shuffle)
        self.repeat_mode = self._validated_enum(
            RepeatMode, cfg.get("repeat_mode", 0), RepeatMode.OFF)
        self.player_bar.update_repeat_button(self.repeat_mode)
        self.auto_rescan = cfg.get("auto_rescan", True)
        self.remember_track = cfg.get("remember_track", True)
        self.taskbar_close_to_tray = bool(cfg.get("taskbar_close_to_tray", False))
        self.tray_notice_shown = bool(cfg.get("tray_notice_shown", False))
        self.updates_enabled = bool(cfg.get("updates_enabled", True))
        self.update_last_check = float(cfg.get("update_last_check", 0) or 0)
        self.update_etag = str(cfg.get("update_etag", "") or "")
        self.update_skip_version = str(cfg.get("update_skip_version", "") or "")
        self.update_snooze_until = float(cfg.get("update_snooze_until", 0) or 0)
        self.default_folder = cfg.get("default_folder", "")
        sm = cfg.get("sort_mode", "title")
        try:
            self.sort_mode = SortMode(sm)
        except ValueError:
            self.sort_mode = SortMode.TITLE
        # Settings-dialog fields — restore what _save_config persisted
        self.lyrics_online_enabled = bool(cfg.get("lyrics_online", True))
        self.audio_output_id = str(cfg.get("audio_output_id", "") or "")
        self.auto_theme_enabled = bool(cfg.get("auto_theme", False))
        self._toast_enabled = bool(cfg.get("toast_enabled", True))
        self.visualizer_enabled = bool(cfg.get("visualizer_enabled", True))
        self._last_volume = self._validated_int(cfg.get("default_volume", vol), vol, 0, 100)
        if getattr(self, "fx", None):
            self.fx.enabled_fade = bool(cfg.get("fade_enabled", True))
            self.fx.fade_ms = self._validated_int(cfg.get("fade_ms", 300), 300, 100, 2000)
        # Sleep timer is persisted so it survives restarts too
        self.sleep_timer_minutes = self._validated_int(
            cfg.get("sleep_timer_minutes", 30), 30, 1, 480)
        if bool(cfg.get("sleep_timer_active", False)):
            self.start_sleep_timer(self.sleep_timer_minutes)
        if hasattr(self, "sort_combo"):
            idx = self.sort_combo.findData(sm)
            if idx >= 0:
                self.sort_combo.setCurrentIndex(idx)
        geom_hex = cfg.get("geometry")
        if geom_hex:
            try:
                self.restoreGeometry(bytes.fromhex(geom_hex))
            except Exception:
                pass
        self._apply_theme()

        # Filter missing files (fast — os.path.exists)
        self.library = [p for p in self.library if os.path.exists(p)]
        self.favorites = {p for p in self.favorites if os.path.exists(p)}
        # The stats seed at __init__ ran before added_folders was known —
        # refresh moved-file search roots now that folders are populated.
        try:
            from playstats import set_search_roots
            _roots = list(self.added_folders or [])
            _roots += list({os.path.dirname(os.path.abspath(p))
                            for p in self.library if p})
            set_search_roots(_roots)
        except Exception:
            pass

        # Rebuild list FAST (filenames only, no tag reading)
        # Load tag cache first (instant — no I/O)
        self._load_tag_cache()
        self._rebuild_library_list()
        self._rebuild_favorites_list()
        self._update_count_label()

        # Restore last track (if any)
        last = cfg.get("last_track")
        last_pos = cfg.get("last_position", 0)
        if last and last in self.library and self.remember_track:
            idx = self.library.index(last)
            self.current_playlist = list(self.library)
            self.current_index = idx
            self.current_track = last
            # Use cached tags if available, otherwise filename
            if last in self._tag_cache:
                title, artist = self._tag_cache[last]
            else:
                title = os.path.splitext(os.path.basename(last))[0]
                artist = "Unknown Artist"
            self.now_title.setMarqueeText(title)
            self.now_artist.setMarqueeText(artist)
            self.player_bar.title_label.setMarqueeText(title)
            self.player_bar.artist_label.setMarqueeText(artist)
            self.player_bar.update_like_button(last in self.favorites)
            self._highlight_playing_in_lists()
            self._load_cover_async(last)
            # Load media without playing (user can press play)
            self.audio.load(last)
            QTimer.singleShot(500, lambda: self._restore_position(last, last_pos))

        # Start rescan in background
        if self.auto_rescan:
            QTimer.singleShot(100, self._startup_rescan)

        # Apply restored UI toggles + audio output + auto-theme (all were
        # persisted by _save_config; see FUNC-4 fix — these used to reset
        # to defaults on every restart).
        if hasattr(self, "player_bar"):
            self.player_bar.ab_btn.setVisible(bool(cfg.get("show_ab_button", False)))
            self.player_bar.rate_btn.setVisible(bool(cfg.get("show_rate_button", False)))
            self.player_bar.lyrics_btn.setVisible(bool(cfg.get("show_lyrics_button", True)))
        if self.audio_output_id:
            try:
                from audio_output import apply_output_device
                apply_output_device(self.audio, self.audio_output_id)
            except Exception as e:
                log.warning(f"restored audio output unavailable: {e}")
        if self.auto_theme_enabled:
            self._apply_auto_theme_setting()

        # Warm album groups + covers in the background so the Albums page is
        # instant on first visit (user request: preload, keep, revisit fast).
        # Smoke mode quits at 2500 ms — don't start a heavy worker at
        # 1500 ms that would race the shutdown.
        if not getattr(self, "_smoke", False):
            QTimer.singleShot(1500, self._preload_albums_background)

        # v3 feature wiring once prefs + store are live
        try:
            self._apply_audio_prefs()
            try:
                if str(sys.platform).startswith("linux"):
                    from gm.mpris import start_mpris
                    self._mpris = start_mpris(self)
            except Exception:
                log.exception("mpris wiring failed")
            self._sync_smart_playlists()
            self._apply_folder_watch()
            if self.prefs.get("webremote_enabled", False) or \
                    self.prefs.get("telegram_enabled", False):
                self._apply_remote_prefs()
            if self.prefs.get("floating_lyrics", False):
                self._apply_floating_lyrics()
        except Exception:
            log.exception("v3 post-load wiring failed")

    def _restore_position(self, path, pos):
        if self.current_track == path:
            self.audio.set_position(pos)
            self.player_bar.time_current.setText(fmt_time(pos))
            dur = self.audio.duration()
            if dur > 0:
                # Block signals to prevent feedback loop
                self.player_bar.seek_slider.blockSignals(True)
                self.player_bar.seek_slider.setValue(int(pos * 1000 / dur))
                self.player_bar.seek_slider.blockSignals(False)

