"""
Golden Music — Settings dialog.
Theme picker, default folder, sleep timer, clear data, about.
"""
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QCheckBox, QSpinBox, QLineEdit, QFileDialog,
    QGroupBox, QFormLayout, QMessageBox, QTabWidget, QWidget,
    QFrame, QSizePolicy
)
from PyQt6.QtGui import QPixmap

from config import Theme, APP_NAME, APP_VERSION
from icons import render_icon, get_dpr
import icons


class SettingsDialog(QDialog):
    """Tabbed settings dialog with theme picker, playback, library, and about."""

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.setWindowTitle(f"{APP_NAME} Settings")
        self.setModal(True)
        self.setMinimumWidth(480)
        self.theme = main_window.theme

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Tab widget
        tabs = QTabWidget()
        tabs.addTab(self._build_appearance_tab(), "Appearance")
        tabs.addTab(self._build_playback_tab(), "Playback")
        tabs.addTab(self._build_library_tab(), "Library")
        tabs.addTab(self._build_about_tab(), "About")
        layout.addWidget(tabs)

        # Bottom buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        ok_btn = QPushButton("OK")
        ok_btn.setObjectName("GoldBtn")
        ok_btn.clicked.connect(self._on_ok)
        btn_layout.addWidget(ok_btn)
        btn_w = QWidget()
        btn_w.setLayout(btn_layout)
        layout.addWidget(btn_w)

        self._apply_styles()
        self._load_current_settings()

    def _apply_styles(self):
        t = self.theme
        is_dark = t.get('is_dark')
        # Readable accent for group-box titles on BOTH color families:
        # light themes use a dark "ink" accent (readable on white), dark
        # themes use the bright accent (readable on near-black).
        # (gold_light is tuned for dark surfaces only.)
        title_col = t['gold']
        self.setStyleSheet(f"""
            QDialog {{ background: {t['window_bg']}; color: {t['text']}; }}
            QWidget {{ color: {t['text']}; font-size: 13px; }}
            QTabWidget {{ background: {t['window_bg']}; }}
            QTabWidget::pane {{ border: 1px solid {t['border']}; border-radius: 8px; background: {t['panel_bg']}; }}
            QTabBar {{ background: {t['window_bg']}; }}
            QTabBar::tab {{ background: {t['panel_bg_2']}; color: {t['muted']}; padding: 8px 16px; border-radius: 6px; margin: 2px; font-size: 13px; }}
            QTabBar::tab:selected {{ background: {t['gold']}; color: {'#ffffff' if is_dark else t['window_bg']}; font-weight: 600; }}
            QTabBar::tab:hover {{ color: {t['gold_light']}; }}
            QScrollArea {{ background: transparent; }}
            QScrollArea > QWidget > QWidget {{ background: transparent; }}
            QGroupBox {{
                color: {title_col}; font-weight: 600; font-size: 13px;
                border: 1px solid {t['border']}; border-radius: 8px;
                margin-top: 12px; padding-top: 12px;
                background: {t['panel_bg']};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin; left: 10px; padding: 0 4px;
                color: {title_col};
                background: {t['panel_bg']};
            }}
            QLabel {{ color: {t['text']}; background: transparent; }}
            QPushButton {{
                background: {t['panel_bg_2']}; color: {t['text']};
                border: 1px solid {t['border']}; border-radius: 6px;
                padding: 8px 16px; font-size: 13px;
            }}
            QPushButton:hover {{ background: {t['active']}; border-color: {t['gold']}; color: {t['gold_light']}; }}
            QPushButton:disabled {{ color: {t['muted']}; }}
            QPushButton#GoldBtn {{ background: {t['gold']}; color: {'#ffffff' if not is_dark else t['window_bg']}; font-weight: 600; border: none; }}
            QPushButton#GoldBtn:hover {{ background: {t['gold_light']}; color: {'#ffffff' if not is_dark else t['window_bg']}; }}
            QPushButton#DangerBtn {{ background: transparent; color: #e05050; border: 1px solid #603030; }}
            QPushButton#DangerBtn:hover {{ background: rgba(224, 80, 80, 30); color: #ff7070; }}
            QComboBox {{
                background: {t['panel_bg_2']}; color: {t['text']};
                border: 1px solid {t['border']}; border-radius: 6px;
                padding: 6px 10px; min-width: 120px;
            }}
            QComboBox:hover {{ border-color: {t['gold']}; }}
            QComboBox:disabled {{ color: {t['muted']}; background: {t['panel_bg']}; }}
            QComboBox QAbstractItemView {{
                background: {t['panel_bg']}; color: {t['text']};
                selection-background-color: {t['active']}; selection-color: {t['gold_light']};
                border: 1px solid {t['border']}; outline: 0;
            }}
            QCheckBox {{ color: {t['text']}; spacing: 8px; background: transparent; }}
            QCheckBox:hover {{ color: {t['gold_light']}; }}
            QCheckBox:disabled {{ color: {t['muted']}; }}
            QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 4px; border: 1px solid {t['border']}; background: {t['panel_bg_2']}; }}
            QCheckBox::indicator:hover {{ border-color: {t['gold']}; }}
            QCheckBox::indicator:checked {{ background: {t['gold']}; border-color: {t['gold']}; }}
            QSpinBox {{
                background: {t['panel_bg_2']}; color: {t['text']};
                border: 1px solid {t['border']}; border-radius: 6px;
                padding: 4px 8px; min-width: 80px;
            }}
            QSpinBox:disabled {{ color: {t['muted']}; background: {t['panel_bg']}; }}
            QLineEdit {{
                background: {t['panel_bg_2']}; color: {t['text']};
                border: 1px solid {t['border']}; border-radius: 6px;
                padding: 6px 10px;
            }}
            QScrollBar:vertical {{ background: {t['panel_bg']}; width: 10px; }}
            QScrollBar::handle:vertical {{ background: {t['border']}; border-radius: 5px; min-height: 30px; }}
            QScrollBar::handle:vertical:hover {{ background: {t['gold_deep']}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)

    def _build_appearance_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        # Theme group
        theme_group = QGroupBox("Theme")
        theme_layout = QVBoxLayout(theme_group)
        label = QLabel("Choose a color theme:")
        theme_layout.addWidget(label)
        self.theme_combo = QComboBox()
        for name in Theme.names():
            self.theme_combo.addItem(Theme.display_name(name), name)
        theme_layout.addWidget(self.theme_combo)

        # Theme preview
        self.theme_preview = QLabel()
        self.theme_preview.setFixedHeight(40)
        self.theme_preview.setStyleSheet(f"border-radius: 8px; border: 1px solid {self.theme['border']};")
        theme_layout.addWidget(self.theme_preview)
        self.theme_combo.currentIndexChanged.connect(self._update_theme_preview)

        layout.addWidget(theme_group)

        layout.addStretch(1)
        return tab

    def _build_playback_tab(self) -> QWidget:
        from PyQt6.QtWidgets import QScrollArea
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        tab_inner = QWidget()
        layout = QVBoxLayout(tab_inner)
        layout.setSpacing(12)

        # Volume group
        vol_group = QGroupBox("Volume")
        vol_layout = QFormLayout(vol_group)
        self.default_vol_spin = QSpinBox()
        self.default_vol_spin.setRange(0, 100)
        self.default_vol_spin.setSuffix(" %")
        vol_layout.addRow("Default volume:", self.default_vol_spin)
        layout.addWidget(vol_group)

        # Playback effects group (all toggleable per user request)
        fx_group = QGroupBox("Playback Effects")
        fx_layout = QVBoxLayout(fx_group)
        self.fade_cb = QCheckBox("Smooth volume fade on pause / track change")
        fx_layout.addWidget(self.fade_cb)
        self.ab_cb = QCheckBox("Show A-B Repeat button")
        fx_layout.addWidget(self.ab_cb)
        self.rate_cb = QCheckBox("Show playback speed button (0.5x – 2x)")
        fx_layout.addWidget(self.rate_cb)
        fade_row = QHBoxLayout()
        fade_row.addWidget(QLabel("Fade duration:"))
        self.fade_spin = QSpinBox()
        self.fade_spin.setRange(100, 2000)
        self.fade_spin.setSingleStep(100)
        self.fade_spin.setSuffix(" ms")
        fade_row.addWidget(self.fade_spin)
        fade_row.addStretch(1)
        fx_layout.addLayout(fade_row)
        layout.addWidget(fx_group)

        # Lyrics group
        lyr_group = QGroupBox("Lyrics")
        lyr_layout = QVBoxLayout(lyr_group)
        self.lyrics_cb = QCheckBox("Show lyrics button (sidecar .lrc / embedded / online)")
        lyr_layout.addWidget(self.lyrics_cb)
        self.lyrics_online_cb = QCheckBox("Allow fetching lyrics online when missing")
        lyr_layout.addWidget(self.lyrics_online_cb)
        layout.addWidget(lyr_group)

        # Appearance extras
        ui_group = QGroupBox("Interface")
        ui_layout = QVBoxLayout(ui_group)
        self.auto_theme_cb = QCheckBox("Auto theme — follow Windows dark/light mode")
        ui_layout.addWidget(self.auto_theme_cb)
        self.toast_cb = QCheckBox("Notify on track change (when window is hidden)")
        ui_layout.addWidget(self.toast_cb)
        self.tray_min_cb = QCheckBox(
            "Clicking the taskbar icon sends the app to the tray instead of minimizing")
        ui_layout.addWidget(self.tray_min_cb)
        self.viz_cb = QCheckBox("Visualizer — animated background in the fullscreen player")
        ui_layout.addWidget(self.viz_cb)
        layout.addWidget(ui_group)

        # Audio output selection (HDMI / S/PDIF / headphones / speakers)
        out_group = QGroupBox("Audio Output")
        out_layout = QVBoxLayout(out_group)
        self.output_combo = QComboBox()
        self.output_combo.addItem("System default", "")
        try:
            from audio_output import list_output_devices
            for name, dev_id in list_output_devices():
                self.output_combo.addItem(name, dev_id)
        except Exception:
            pass
        self.output_combo.currentIndexChanged.connect(self._on_output_changed)
        out_layout.addWidget(self.output_combo)
        self.output_status = QLabel("")
        self.output_status.setStyleSheet(
            f"color: {self.main_window.theme['muted']}; font-size: 11px;")
        out_layout.addWidget(self.output_status)
        hint = QLabel("Switches immediately — including the track playing now. "
                      "Only devices enabled in Windows are listed "
                      "(enable HDMI/S-PDIF in Windows Sound settings first).")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {self.main_window.theme['muted']}; font-size: 11px;")
        out_layout.addWidget(hint)
        layout.addWidget(out_group)

        # Sleep timer group
        sleep_group = QGroupBox("Sleep Timer")
        sleep_layout = QVBoxLayout(sleep_group)
        self.sleep_cb = QCheckBox("Enable sleep timer (stop playback after N minutes)")
        sleep_layout.addWidget(self.sleep_cb)
        sleep_row = QHBoxLayout()
        sleep_row.addWidget(QLabel("Minutes:"))
        self.sleep_spin = QSpinBox()
        self.sleep_spin.setRange(1, 480)
        self.sleep_spin.setSuffix(" min")
        sleep_row.addWidget(self.sleep_spin)
        sleep_row.addStretch(1)
        sleep_w = QWidget()
        sleep_w.setLayout(sleep_row)
        sleep_layout.addWidget(sleep_w)
        self.sleep_cb.toggled.connect(self.sleep_spin.setEnabled)
        layout.addWidget(sleep_group)

        # Crossfade removed — replaced by real Fade above.

        layout.addStretch(1)
        scroll.setWidget(tab_inner)

        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        host = QWidget()
        host.setLayout(outer)
        return host

    def _build_library_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        # Default folder group
        folder_group = QGroupBox("Default Music Folder")
        folder_layout = QHBoxLayout(folder_group)
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("No default folder set")
        folder_layout.addWidget(self.folder_edit, 1)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_folder)
        folder_layout.addWidget(browse_btn)
        layout.addWidget(folder_group)

        # Auto-rescan group
        rescan_group = QGroupBox("Startup")
        rescan_layout = QVBoxLayout(rescan_group)
        self.auto_rescan_cb = QCheckBox("Auto-rescan folders on startup (check for new tracks)")
        self.auto_rescan_cb.setChecked(True)
        rescan_layout.addWidget(self.auto_rescan_cb)
        self.remember_track_cb = QCheckBox("Remember last played track and position")
        self.remember_track_cb.setChecked(True)
        rescan_layout.addWidget(self.remember_track_cb)
        layout.addWidget(rescan_group)

        # Library tools
        tools_group = QGroupBox("Library Tools")
        tools_layout = QVBoxLayout(tools_group)
        dup_btn = QPushButton("Find Duplicate Tracks...")
        dup_btn.clicked.connect(self._find_duplicates)
        tools_layout.addWidget(dup_btn)
        layout.addWidget(tools_group)

        # Data management
        data_group = QGroupBox("Data Management")
        data_layout = QVBoxLayout(data_group)
        clear_fav_btn = QPushButton("Clear All Favorites")
        clear_fav_btn.setObjectName("DangerBtn")
        clear_fav_btn.clicked.connect(self._clear_favorites)
        data_layout.addWidget(clear_fav_btn)
        clear_lib_btn = QPushButton("Clear Entire Library")
        clear_lib_btn.setObjectName("DangerBtn")
        clear_lib_btn.clicked.connect(self._clear_library)
        data_layout.addWidget(clear_lib_btn)
        clear_cache_btn = QPushButton("Clear Cover Art Cache")
        clear_cache_btn.setObjectName("DangerBtn")
        clear_cache_btn.clicked.connect(self._clear_cache)
        data_layout.addWidget(clear_cache_btn)
        layout.addWidget(data_group)

        layout.addStretch(1)
        return tab

    def _build_about_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(8)

        # App icon
        dpr = get_dpr()
        icon_pm = render_icon(icons.Icon.MUSIC_NOTE, 64, self.theme["gold"], dpr)
        icon_label = QLabel()
        icon_label.setPixmap(icon_pm)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon_label)

        # App name
        name_label = QLabel(f"<h2>{APP_NAME}</h2>")
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(name_label)

        version_label = QLabel(f"Version {APP_VERSION}")
        version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        version_label.setStyleSheet(f"color: {self.theme['muted']};")
        layout.addWidget(version_label)

        layout.addSpacing(16)

        # Stats
        stats = QLabel(
            f"<b>Library:</b> {len(self.main_window.library)} tracks<br>"
            f"<b>Favorites:</b> {len(self.main_window.favorites)} tracks<br>"
            f"<b>Folders:</b> {len(self.main_window.added_folders)}"
        )
        stats.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(stats)

        layout.addStretch(1)

        copyright_label = QLabel("Built with PyQt6 · Python 3.14")
        copyright_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        copyright_label.setStyleSheet(f"color: {self.theme['muted']}; font-size: 11px;")
        layout.addWidget(copyright_label)

        return tab

    def _load_current_settings(self):
        mw = self.main_window
        # Appearance
        idx = self.theme_combo.findData(self.main_window.theme_name)
        if idx >= 0:
            self.theme_combo.setCurrentIndex(idx)
        self._update_theme_preview()
        self.auto_theme_cb.setChecked(getattr(self.main_window, 'auto_theme_enabled', False))
        self.toast_cb.setChecked(getattr(self.main_window, '_toast_enabled', True))
        self.tray_min_cb.setChecked(getattr(self.main_window, 'taskbar_close_to_tray', False))

        # Playback
        self.default_vol_spin.setValue(getattr(self.main_window, '_last_volume', 80))
        self.sleep_cb.setChecked(getattr(self.main_window, 'sleep_timer_active', False))
        self.sleep_spin.setValue(getattr(self.main_window, 'sleep_timer_minutes', 30))
        self.sleep_spin.setEnabled(self.sleep_cb.isChecked())
        fx = getattr(self.main_window, 'fx', None)
        self.fade_cb.setChecked(bool(fx and fx.enabled_fade))
        if fx:
            self.fade_spin.setValue(int(getattr(fx, 'fade_ms', 300)))
        self.ab_cb.setChecked(mw.player_bar.ab_btn.isVisible())
        self.rate_cb.setChecked(mw.player_bar.rate_btn.isVisible())
        self.lyrics_cb.setChecked(mw.player_bar.lyrics_btn.isVisible())
        self.viz_cb.setChecked(
            bool(getattr(mw, 'visualizer_enabled', True)))
        self.lyrics_online_cb.setChecked(
            getattr(self.main_window, 'lyrics_online_enabled', True))

        # Audio output
        saved_out = getattr(self.main_window, 'audio_output_id', "")
        i = self.output_combo.findData(saved_out or "")
        if i >= 0:
            self.output_combo.setCurrentIndex(i)

        # Library
        self.folder_edit.setText(getattr(self.main_window, 'default_folder', ''))
        self.auto_rescan_cb.setChecked(getattr(self.main_window, 'auto_rescan', True))
        self.remember_track_cb.setChecked(getattr(self.main_window, 'remember_track', True))

    def _update_theme_preview(self):
        name = self.theme_combo.currentData()
        t = Theme.get(name)
        self.theme_preview.setStyleSheet(
            f"border-radius: 8px; border: 1px solid {t['border']};"
            f"background: linear-gradient(135deg, {t['window_bg']}, {t['panel_bg']}, {t['gold']});"
        )

    def _on_output_changed(self, idx):
        """Switch the audio output device immediately (no OK needed)."""
        dev_id = self.output_combo.itemData(idx)
        if dev_id is None:
            return
        mw = self.main_window
        mw.audio_output_id = dev_id or ""
        try:
            from audio_output import apply_output_device
            ok = apply_output_device(mw.audio, dev_id)
            name = self.output_combo.currentText()
            self.output_status.setText(
                f"✔ Now playing through: {name}" if ok
                else "Could not switch to this device.")
        except Exception as e:
            self.output_status.setText(f"Switch failed: {e}")
        mw._save_config_debounced()

    def _browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Default Music Folder", "")
        if folder:
            self.folder_edit.setText(folder)

    def _clear_favorites(self):
        reply = QMessageBox.question(self, APP_NAME,
            "Clear all favorites? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.main_window.favorites.clear()
            self.main_window._rebuild_favorites_list()
            self.main_window._save_config()
            QMessageBox.information(self, APP_NAME, "All favorites cleared.")

    def _clear_library(self):
        reply = QMessageBox.question(self, APP_NAME,
            "Clear entire library? This will remove ALL tracks and folders. This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.main_window.library.clear()
            self.main_window.favorites.clear()
            self.main_window.added_folders.clear()
            self.main_window._rebuild_library_list()
            self.main_window._rebuild_favorites_list()
            self.main_window._save_config()
            QMessageBox.information(self, APP_NAME, "Library cleared.")

    def _clear_cache(self):
        import coverart
        coverart.clear_cache()
        QMessageBox.information(self, APP_NAME, "Cover art cache cleared.")

    def _find_duplicates(self):
        try:
            from duplicates_dialog import DuplicatesDialog
            self.accept()  # close settings so the browser is front and center
            dlg = DuplicatesDialog(self.main_window, self.main_window)
            dlg.exec()
        except Exception as e:
            QMessageBox.warning(self, APP_NAME, f"Duplicate finder failed:\n{e}")

    def _on_ok(self):
        # Apply appearance
        new_theme = self.theme_combo.currentData()
        if new_theme != self.main_window.theme_name:
            self.main_window.theme_name = new_theme
            self.main_window._apply_theme()
        self.main_window.auto_theme_enabled = self.auto_theme_cb.isChecked()
        self.main_window._apply_auto_theme_setting()
        self.main_window._toast_enabled = self.toast_cb.isChecked()
        self.main_window.taskbar_close_to_tray = self.tray_min_cb.isChecked()

        # Apply playback effects
        self.main_window._last_volume = self.default_vol_spin.value()
        fx = getattr(self.main_window, 'fx', None)
        if fx:
            fx.enabled_fade = self.fade_cb.isChecked()
            fx.fade_ms = self.fade_spin.value()
        mw = self.main_window
        mw.player_bar.ab_btn.setVisible(self.ab_cb.isChecked())
        mw.player_bar.rate_btn.setVisible(self.rate_cb.isChecked())
        mw.player_bar.lyrics_btn.setVisible(self.lyrics_cb.isChecked())
        mw.lyrics_online_enabled = self.lyrics_online_cb.isChecked()
        mw.visualizer_enabled = self.viz_cb.isChecked()
        try:
            fs = getattr(mw, "_fullscreen_player", None)
            if fs is not None:
                fs.visualizer_enabled = mw.visualizer_enabled
                fs.viz_btn.setChecked(mw.visualizer_enabled)
        except Exception:
            pass

        # Audio output device — already applied live on combo change
        # (_on_output_changed); keep the id in sync and persist it.
        mw.audio_output_id = self.output_combo.currentData() or ""

        if self.sleep_cb.isChecked():
            self.main_window.start_sleep_timer(self.sleep_spin.value())
        else:
            self.main_window.stop_sleep_timer()

        # Apply library
        self.main_window.default_folder = self.folder_edit.text()
        self.main_window.auto_rescan = self.auto_rescan_cb.isChecked()
        self.main_window.remember_track = self.remember_track_cb.isChecked()

        self.main_window._save_config()
        self.accept()
