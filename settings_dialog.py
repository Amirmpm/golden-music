"""
Golden Music — Settings dialog.
Theme picker, default folder, sleep timer, clear data, about.
"""
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QCheckBox, QSpinBox, QLineEdit, QFileDialog,
    QGroupBox, QFormLayout, QMessageBox, QTabWidget, QWidget,
    QFrame, QSizePolicy, QSlider, QColorDialog
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
        tabs.addTab(self._build_downloads_tab(), "Downloads")
        tabs.addTab(self._build_online_tab(), "Online")
        tabs.addTab(self._build_windows_tab(), "Windows")
        tabs.addTab(self._build_library_tab(), "Library")
        tabs.addTab(self._build_shortcuts_tab(), "Shortcuts")
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
        # Live-apply wiring — installed AFTER _load_current_settings so the
        # initial sync doesn't fire redundant theme applies at dialog open.
        # (The accent swatches / reset button already applied live; the mode
        # combo, sliders, density and background combos now do too, as
        # documented in NUVX_SETTINGS_PARITY and the _on_ok comment.)
        self.lg_mode_combo.currentIndexChanged.connect(self._lg_live_apply)
        self.lg_transparency.valueChanged.connect(self._lg_live_apply)
        self.lg_blur.valueChanged.connect(self._lg_live_apply)
        self.lg_corner.valueChanged.connect(self._lg_live_apply)
        self.lg_anim.valueChanged.connect(self._lg_live_apply)
        self.lg_density.currentIndexChanged.connect(self._lg_live_apply)
        self.lg_background.currentIndexChanged.connect(self._lg_live_apply)
        self.lg_art_cb.toggled.connect(self._lg_live_apply)
        self.lg_reduced_cb.toggled.connect(self._lg_live_apply)

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

    _ACCENT_SWATCHES = ["#e9b957", "#4591ea", "#b57edc", "#ff2e63",
                        "#2ecc71", "#f97316", "#e8590c", "#e2e8f0"]

    def _build_appearance_tab(self) -> QWidget:
        """v3: the unified Liquid Glass design system (the 12-theme picker
        is retired as the primary appearance mechanism; the legacy theme key
        keeps being persisted so a rollback build still finds a look)."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        glass = QGroupBox("Liquid Glass")
        g = QFormLayout(glass)

        self.lg_mode_combo = QComboBox()
        self.lg_mode_combo.addItem("Follow Windows", "system")
        self.lg_mode_combo.addItem("Light", "light")
        self.lg_mode_combo.addItem("Dark", "dark")
        g.addRow("Mode:", self.lg_mode_combo)

        # accent swatches + custom
        self._swatch_buttons = []
        swatch_holder = QWidget()
        sw = QHBoxLayout(swatch_holder)
        sw.setContentsMargins(0, 0, 0, 0)
        sw.setSpacing(6)
        for hexv in self._ACCENT_SWATCHES:
            b = QPushButton()
            b.setFixedSize(26, 26)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(hexv)
            b.setStyleSheet(f"background: {hexv}; border-radius: 13px;"
                            f"border: 1px solid rgba(255,255,255,0.35);")
            b.clicked.connect(lambda _c, h=hexv: self._set_accent(h))
            sw.addWidget(b)
            self._swatch_buttons.append(b)
        custom_btn = QPushButton("Custom…")
        custom_btn.clicked.connect(self._pick_custom_accent)
        sw.addWidget(custom_btn)
        g.addRow("Accent color:", swatch_holder)

        self.lg_art_cb = QCheckBox("Derive accent from album artwork")
        g.addRow(self.lg_art_cb)

        self.lg_transparency = QSlider(Qt.Orientation.Horizontal)
        self.lg_transparency.setRange(0, 100)
        g.addRow("Panel transparency:", self.lg_transparency)

        self.lg_blur = QSlider(Qt.Orientation.Horizontal)
        self.lg_blur.setRange(0, 200)
        g.addRow("Blur / native backdrop:", self.lg_blur)

        self.lg_corner = QSlider(Qt.Orientation.Horizontal)
        self.lg_corner.setRange(50, 150)
        g.addRow("Corner radius:", self.lg_corner)

        self.lg_anim = QSlider(Qt.Orientation.Horizontal)
        self.lg_anim.setRange(0, 200)
        g.addRow("Animation intensity:", self.lg_anim)

        self.lg_density = QComboBox()
        for label in ("Compact", "Comfortable", "Spacious"):
            self.lg_density.addItem(label, label.lower())
        g.addRow("Interface density:", self.lg_density)

        self.lg_background = QComboBox()
        self.lg_background.addItem("Accent gradient", "gradient")
        self.lg_background.addItem("Accent tint", "tint")
        self.lg_background.addItem("Solid", "solid")
        g.addRow("Background:", self.lg_background)

        self.lg_reduced_cb = QCheckBox("Reduce motion (accessibility)")
        g.addRow(self.lg_reduced_cb)

        reset_row = QHBoxLayout()
        self.lg_reset_btn = QPushButton("Reset to defaults")
        self.lg_reset_btn.clicked.connect(self._lg_reset)
        reset_row.addWidget(self.lg_reset_btn)
        reset_row.addStretch(1)
        g.addRow(reset_row)

        layout.addWidget(glass)
        layout.addStretch(1)
        return tab

    def _set_accent(self, hexv: str):
        self.main_window.glass.accent = hexv
        self._lg_live_apply()

    def _pick_custom_accent(self):
        from PyQt6.QtGui import QColor
        c = QColorDialog.getColor(QColor(self.main_window.glass.accent), self,
                                  "Custom accent color")
        if c.isValid():
            self.main_window.glass.accent = c.name()
            self._lg_live_apply()

    def _lg_live_apply(self, *_a):
        self._sync_glass_from_controls()
        mw = self.main_window
        mw._apply_theme()
        # The dialog was constructed under the previous mode — re-render its
        # own chrome with the freshly applied theme so the dialog follows a
        # live Light/Dark switch (previously it kept dark-baked inline styles
        # over the light app).
        if self.theme is not mw.theme:
            self.theme = mw.theme
            try:
                self._apply_styles()
            except Exception:
                pass
        mw._save_config_debounced()

    def _sync_glass_from_controls(self):
        mw = self.main_window
        g = mw.glass
        g.mode = self.lg_mode_combo.currentData() or "system"
        g.accent_from_art = self.lg_art_cb.isChecked()
        g.transparency = self.lg_transparency.value()
        g.blur = self.lg_blur.value()
        g.corner = self.lg_corner.value()
        g.anim_intensity = self.lg_anim.value()
        g.density = self.lg_density.currentData() or "comfortable"
        g.background = self.lg_background.currentData() or "gradient"
        g.reduced_motion = self.lg_reduced_cb.isChecked()
        g.validate()

    def _lg_reset(self):
        import liquidglass
        mw = self.main_window
        mw.glass = liquidglass.GlassSettings()
        mw._apply_theme()
        self._load_current_settings()
        mw._save_config_debounced()

    # ------------------------------------------------------------------
    # Downloads tab
    # ------------------------------------------------------------------
    def _build_downloads_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        folder_group = QGroupBox("Download Folder (chosen once, reused)")
        fl = QVBoxLayout(folder_group)
        self.dl_folder_lbl = QLabel("…")
        self.dl_folder_lbl.setWordWrap(True)
        fl.addWidget(self.dl_folder_lbl)
        row = QHBoxLayout()
        self.dl_choose_btn = QPushButton("Choose folder…")
        self.dl_choose_btn.clicked.connect(self._dl_choose)
        row.addWidget(self.dl_choose_btn)
        self.dl_open_btn = QPushButton("Open folder")
        self.dl_open_btn.clicked.connect(
            lambda: self.main_window._open_download_folder())
        row.addWidget(self.dl_open_btn)
        row.addStretch(1)
        fl.addLayout(row)
        layout.addWidget(folder_group)

        info_group = QGroupBox("Manager")
        il = QFormLayout(info_group)
        self.dl_floor_lbl = QLabel(
            "Downloads pause below 500 MB free disk space. "
            "Completed files join your local library automatically "
            "(duplicates are skipped).")
        self.dl_floor_lbl.setWordWrap(True)
        il.addRow(self.dl_floor_lbl)
        self.dl_history_lbl = QLabel("0 completed downloads")
        il.addRow(self.dl_history_lbl)
        layout.addWidget(info_group)

        clear_row = QHBoxLayout()
        self.dl_clear_btn = QPushButton("Clear download history")
        self.dl_clear_btn.clicked.connect(self._dl_clear_history)
        clear_row.addWidget(self.dl_clear_btn)
        clear_row.addStretch(1)
        layout.addLayout(clear_row)

        layout.addStretch(1)
        return tab

    def _dl_choose(self):
        from pathlib import Path
        mw = self.main_window
        start = mw.download_folder or str(mw._default_download_dir())
        folder = QFileDialog.getExistingDirectory(
            self, "Download folder — used automatically for every future "
                  "download", start)
        if folder:
            mw.download_folder = folder
            if getattr(mw, "download_manager", None):
                mw.download_manager.set_folder(Path(folder))
            mw.downloads_page.set_folder(Path(folder))
            mw._save_config_debounced()
            self._load_current_settings()

    def _dl_clear_history(self):
        mw = self.main_window
        mw.downloads_history = []
        if getattr(mw, "download_manager", None):
            mw.download_manager.load_history([])
            mw.downloads_page.set_completed([])
        mw._save_config_debounced()
        self._load_current_settings()

    # ------------------------------------------------------------------
    # Online tab
    # ------------------------------------------------------------------
    def _build_online_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        src_group = QGroupBox("Online Music")
        sl = QVBoxLayout(src_group)
        self.online_cb = QCheckBox("Enable online search and streaming")
        self.online_cb.stateChanged.connect(self._online_apply)
        sl.addWidget(self.online_cb)
        info = QLabel(
            "Provider: Internet Archive — public-domain and freely licensed "
            "audio. No account required; downloads are permitted by the "
            "source. Additional licensed providers can be added by "
            "implementing the OnlineProvider interface.")
        info.setWordWrap(True)
        sl.addWidget(info)
        layout.addWidget(src_group)

        hist_group = QGroupBox("Search History")
        hl = QVBoxLayout(hist_group)
        self.search_hist_lbl = QLabel("No recent searches")
        self.search_hist_lbl.setWordWrap(True)
        hl.addWidget(self.search_hist_lbl)
        row = QHBoxLayout()
        clear = QPushButton("Clear search history")
        clear.clicked.connect(self._clear_search_history)
        row.addWidget(clear)
        row.addStretch(1)
        hl.addLayout(row)
        layout.addWidget(hist_group)

        # v3.1 (NUVX_SETTINGS_PARITY item 34): custom proxy — REAL
        # implementation (applies to search/stream/artwork/downloads)
        proxy_group = QGroupBox("Network Proxy")
        pl = QVBoxLayout(proxy_group)
        self.proxy_edit = QLineEdit()
        self.proxy_edit.setPlaceholderText("http://proxy.example.com:8080")
        pl.addWidget(self.proxy_edit)
        proxy_hint = QLabel(
            "Empty = system proxy. Applies to online search, streaming, "
            "artwork and downloads. Applied on OK (an invalid URL is "
            "refused with a message).")
        proxy_hint.setWordWrap(True)
        pl.addWidget(proxy_hint)
        layout.addWidget(proxy_group)

        layout.addStretch(1)
        return tab

    def _online_apply(self, *_a):
        mw = self.main_window
        mw.online_enabled = self.online_cb.isChecked()
        mw._save_config_debounced()

    def _clear_search_history(self):
        mw = self.main_window
        mw.search_history = []
        mw.search_page.set_history([])
        mw._save_config_debounced()
        self._load_current_settings()

    # ------------------------------------------------------------------
    # Windows integration tab
    # ------------------------------------------------------------------
    def _build_shortcuts_tab(self) -> QWidget:
        """Dedicated keyboard-shortcuts page (NUVX_SETTINGS_PARITY item
        53): documents the REAL, live shortcuts — the ones QShortcut and
        MediaKeyFilter actually install."""
        from PyQt6.QtWidgets import QTableWidget, QTableWidgetItem, \
            QHeaderView
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)
        intro = QLabel(
            "These shortcuts are installed by the application itself — "
            "this page documents the live keymap, it does not (yet) allow "
            "rebinding.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        table = QTableWidget(0, 2)
        table.setHorizontalHeaderLabels(["Shortcut", "Action"])
        table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        shortcuts = [
            ("Space / Enter", "Play / Pause (when no list or input has focus)"),
            ("Ctrl + ←", "Previous track"),
            ("Ctrl + →", "Next track"),
            ("Ctrl + ↑ / Ctrl + ↓", "Volume up / down"),
            ("← / → (neutral focus)", "Previous / Next track"),
            ("↑ / ↓ (neutral focus)", "Volume up / down"),
            ("Ctrl + F", "Focus the search bar"),
            ("Ctrl + M", "Toggle Mini Player"),
            ("Ctrl + J", "Jump to the playing track"),
            ("F11 / F", "Fullscreen player (Esc leaves)"),
            ("Esc", "Leave the fullscreen player"),
            ("Media keys", "Play/Pause/Stop/Next/Prev — global (keyboard or "
                          "headset)"),
        ]
        for key, action in shortcuts:
            r = table.rowCount()
            table.insertRow(r)
            k_item = QTableWidgetItem(key)
            k_item.setFont(self._mono_font())
            table.setItem(r, 0, k_item)
            table.setItem(r, 1, QTableWidgetItem(action))
        layout.addWidget(table, 1)
        return tab

    def _mono_font(self):
        from PyQt6.QtGui import QFont
        f = QFont("Consolas")
        f.setStyleHint(QFont.StyleHint.Monospace)
        return f

    def _build_windows_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        reg_group = QGroupBox("Default apps & Open with")
        rl = QVBoxLayout(reg_group)
        self.winreg_status_lbl = QLabel("…")
        self.winreg_status_lbl.setWordWrap(True)
        rl.addWidget(self.winreg_status_lbl)
        policy = QLabel(
            "Golden Music registers itself so Windows lists it under "
            "Default apps → Audio, and in every audio file's Open with menu. "
            "It never changes your current default player — the choice is "
            "always yours, made in Windows Settings.")
        policy.setWordWrap(True)
        rl.addWidget(policy)
        row = QHBoxLayout()
        self.winreg_btn = QPushButton("Register / refresh")
        self.winreg_btn.clicked.connect(self._winreg_register)
        row.addWidget(self.winreg_btn)
        self.winreg_remove_btn = QPushButton("Remove registration")
        self.winreg_remove_btn.clicked.connect(self._winreg_unregister)
        row.addWidget(self.winreg_remove_btn)
        self.winreg_open_btn = QPushButton("Open Windows Default apps")
        self.winreg_open_btn.clicked.connect(self._winreg_open_defaults)
        row.addWidget(self.winreg_open_btn)
        row.addStretch(1)
        rl.addLayout(row)
        layout.addWidget(reg_group)

        media_group = QGroupBox("Windows media controls")
        ml = QVBoxLayout(media_group)
        self.smtc_status_lbl = QLabel("…")
        self.smtc_status_lbl.setWordWrap(True)
        ml.addWidget(self.smtc_status_lbl)
        smtc_info = QLabel(
            "System Media Transport Controls show Golden Music in the "
            "Windows media overlay, lock screen and taskbar flyout "
            "(Play/Pause/Next/Previous + track info). Global keyboard media "
            "keys work independently of this.")
        smtc_info.setWordWrap(True)
        ml.addWidget(smtc_info)
        layout.addWidget(media_group)

        fmt_group = QGroupBox("Audio formats")
        fl = QVBoxLayout(fmt_group)
        self.formats_lbl = QLabel("…")
        self.formats_lbl.setWordWrap(True)
        fl.addWidget(self.formats_lbl)
        layout.addWidget(fmt_group)

        layout.addStretch(1)
        return tab

    def _winreg_register(self):
        import winreg_apps
        try:
            report = winreg_apps.register_app()
            n = len(report.get("registered", []))
            QMessageBox.information(
                self, "Windows integration",
                f"Registered for {n} verified audio format(s).\n\n"
                f"Skipped (not verified on this machine): "
                f"{', '.join(report.get('skipped', [])) or 'none'}\n\n"
                f"Your default player is unchanged — to make Golden Music "
                f"the default, open Windows Settings → Default apps and set "
                f"it for audio.")
        except Exception as e:
            QMessageBox.warning(self, "Windows integration",
                                 f"Registration failed:\n{e}")
        self._load_current_settings()

    def _winreg_unregister(self):
        import winreg_apps
        try:
            winreg_apps.unregister_app()
            QMessageBox.information(self, "Windows integration",
                                     "Golden Music's registration was removed.")
        except Exception as e:
            QMessageBox.warning(self, "Windows integration",
                                 f"Removal failed:\n{e}")
        self._load_current_settings()

    def _winreg_open_defaults(self):
        import winreg_apps
        if not winreg_apps.open_default_apps_settings():
            QMessageBox.information(
                self, "Windows integration",
                "Open Windows Settings → Apps → Default apps, then choose "
                "Golden Music for audio files.")

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
        # v3.1 (NUVX_SETTINGS_PARITY items 30/31): lyrics text size + timing
        # offset — REAL implementations, persisted and applied live
        lyr_row1 = QHBoxLayout()
        lyr_row1.addWidget(QLabel("Lyrics text size:"))
        self.lyr_scale_combo = QComboBox()
        self.lyr_scale_combo.addItem("85%", 0.85)
        self.lyr_scale_combo.addItem("100% (default)", 1.0)
        self.lyr_scale_combo.addItem("115%", 1.15)
        self.lyr_scale_combo.addItem("130%", 1.3)
        lyr_row1.addWidget(self.lyr_scale_combo)
        lyr_row1.addStretch(1)
        lyr_layout.addLayout(lyr_row1)
        lyr_row2 = QHBoxLayout()
        lyr_row2.addWidget(QLabel("Timing offset:"))
        self.lyr_offset_spin = QSpinBox()
        self.lyr_offset_spin.setRange(-5000, 5000)
        self.lyr_offset_spin.setSingleStep(100)
        self.lyr_offset_spin.setSuffix(" ms")
        lyr_row2.addWidget(self.lyr_offset_spin)
        lyr_row2.addWidget(QLabel("(positive = lines earlier)"))
        lyr_row2.addStretch(1)
        lyr_layout.addLayout(lyr_row2)
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

        layout.addSpacing(10)

        # --- Updates -----------------------------------------------------
        self.updates_cb = QCheckBox("Automatically check for updates (once a day)")
        self.updates_cb.setChecked(bool(getattr(
            self.main_window, "updates_enabled", True)))
        layout.addWidget(self.updates_cb, 0, Qt.AlignmentFlag.AlignHCenter)
        btn_check = QPushButton("Check for updates now")
        btn_check.setObjectName("GoldBtn")
        btn_check.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_check.clicked.connect(
            lambda: self.main_window._run_update_check(manual=True))
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(btn_check)
        row.addStretch(1)
        layout.addLayout(row)

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
        # Appearance — Liquid Glass (v3)
        g = getattr(mw, "glass", None)
        if g is not None:
            self.lg_mode_combo.setCurrentIndex(
                max(0, self.lg_mode_combo.findData(g.mode)))
            self.lg_art_cb.setChecked(g.accent_from_art)
            self.lg_transparency.setValue(int(g.transparency))
            self.lg_blur.setValue(int(g.blur))
            self.lg_corner.setValue(int(g.corner))
            self.lg_anim.setValue(int(g.anim_intensity))
            self.lg_density.setCurrentIndex(
                max(0, self.lg_density.findData(g.density)))
            self.lg_background.setCurrentIndex(
                max(0, self.lg_background.findData(g.background)))
            self.lg_reduced_cb.setChecked(bool(g.reduced_motion))
        self.auto_theme_cb.setChecked(getattr(self.main_window, 'auto_theme_enabled', False))
        self.toast_cb.setChecked(getattr(self.main_window, '_toast_enabled', True))
        self.tray_min_cb.setChecked(getattr(self.main_window, 'taskbar_close_to_tray', False))
        self.updates_cb.setChecked(getattr(self.main_window, 'updates_enabled', True))

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
        # v3.1: lyrics font scale / timing offset / proxy
        scale = float(getattr(self.main_window, 'lyrics_font_scale', 1.0) or 1.0)
        si = self.lyr_scale_combo.findData(round(scale, 2))
        if si < 0:
            si = self.lyr_scale_combo.findData(1.0)
        self.lyr_scale_combo.setCurrentIndex(max(0, si))
        self.lyr_offset_spin.setValue(
            int(getattr(self.main_window, 'lyrics_offset_ms', 0) or 0))
        self.proxy_edit.setText(
            str(getattr(self.main_window, 'proxy_url', '') or ''))

        # Audio output
        saved_out = getattr(self.main_window, 'audio_output_id', "")
        i = self.output_combo.findData(saved_out or "")
        if i >= 0:
            self.output_combo.setCurrentIndex(i)

        # Library
        self.folder_edit.setText(getattr(self.main_window, 'default_folder', ''))
        self.auto_rescan_cb.setChecked(getattr(self.main_window, 'auto_rescan', True))
        self.remember_track_cb.setChecked(getattr(self.main_window, 'remember_track', True))

        # Downloads / Online / Windows (v3)
        dl_folder = getattr(mw, 'download_folder', '') or str(
            mw._default_download_dir() if hasattr(mw, '_default_download_dir')
            else '')
        self.dl_folder_lbl.setText(
            f"Current folder: {dl_folder}\nAll downloads land here "
            f"automatically — change it only when you really want to.")
        n_hist = len(getattr(mw, 'downloads_history', []))
        self.dl_history_lbl.setText(
            f"{n_hist} completed download" + ("s" if n_hist != 1 else ""))
        self.online_cb.setChecked(getattr(mw, 'online_enabled', True))
        sh = getattr(mw, 'search_history', [])
        self.search_hist_lbl.setText(
            "\n".join(sh) if sh else "No recent searches")
        try:
            import winreg_apps
            st = winreg_apps.registration_status()
            if not st.get("windows"):
                self.winreg_status_lbl.setText(
                    "Not running on Windows — registration applies on the "
                    "Windows build.")
            elif st.get("registered"):
                self.winreg_status_lbl.setText(
                    f"Registered. Appears in Default apps for: "
                    f"{', '.join(st.get('extensions', []))}")
            else:
                self.winreg_status_lbl.setText(
                    "Not registered yet (portable build?). Use "
                    "'Register / refresh' to make Golden Music appear in "
                    "Default apps and Open with.")
        except Exception as e:
            self.winreg_status_lbl.setText(f"Status unavailable: {e}")
        try:
            import smtc as _smtc_mod
            sm = getattr(mw, '_smtc', None)
            if _smtc_mod.SystemMediaControls.available() and sm and sm.is_active():
                self.smtc_status_lbl.setText(
                    "Active — Golden Music appears in the Windows media "
                    "overlay, lock screen and taskbar flyout.")
            elif _smtc_mod.SystemMediaControls.available():
                self.smtc_status_lbl.setText(
                    "Available but not active — restart the app.")
            else:
                self.smtc_status_lbl.setText(
                    "WinRT bindings not installed on this machine "
                    "(pip install winrt-runtime winrt-Windows.Media "
                    "winrt-Windows.Storage.Streams). Global media keys and "
                    "tray controls still work.")
        except Exception as e:
            self.smtc_status_lbl.setText(f"Status unavailable: {e}")
        try:
            import formatcaps
            exts = formatcaps.probe_supported_exts()
            self.formats_lbl.setText(
                f"Verified playable on this machine: "
                f"{', '.join(exts)}\nOnly these are registered with Windows.")
        except Exception as e:
            self.formats_lbl.setText(f"Format probe unavailable: {e}")

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
        # Apply appearance — Liquid Glass values were applied live as the
        # controls changed; make sure the final state is synced + applied.
        self._sync_glass_from_controls()
        self.main_window._apply_theme()
        self.main_window.auto_theme_enabled = self.auto_theme_cb.isChecked()
        self.main_window._apply_auto_theme_setting()
        self.main_window._toast_enabled = self.toast_cb.isChecked()
        self.main_window.taskbar_close_to_tray = self.tray_min_cb.isChecked()
        self.main_window.updates_enabled = self.updates_cb.isChecked()

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
        # v3.1: lyrics text size + timing offset (applied live to both the
        # side panel and the fullscreen lyrics)
        mw.lyrics_font_scale = float(self.lyr_scale_combo.currentData() or 1.0)
        mw.lyrics_offset_ms = self.lyr_offset_spin.value()
        try:
            lp = getattr(mw, "_lyrics_panel", None)
            if lp is not None:
                lp._restyle_lines()
            fs = getattr(mw, "_fullscreen_player", None)
            if fs is not None:
                fs._restyle_lines()
        except Exception:
            pass
        # v3.1: custom proxy (validated — invalid URLs are refused here,
        # keeping the previously-working setting)
        proxy = self.proxy_edit.text().strip()
        if proxy:
            try:
                import online as _online_mod
                _online_mod.set_proxy(proxy)
                mw.proxy_url = proxy
            except ValueError as e:
                QMessageBox.warning(
                    self, APP_NAME, f"Proxy not applied:\n{e}\n\n"
                    "Keeping the previous proxy setting.")
        else:
            try:
                import online as _online_mod
                _online_mod.set_proxy("")
            except Exception:
                pass
            mw.proxy_url = ""
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
