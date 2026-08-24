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
        self.setStyleSheet(f"""
            QDialog {{ background: {t['window_bg']}; color: {t['text']}; }}
            QTabWidget::pane {{ border: 1px solid {t['border']}; border-radius: 8px; background: {t['panel_bg']}; }}
            QTabBar::tab {{ background: {t['panel_bg_2']}; color: {t['muted']}; padding: 8px 16px; border-radius: 6px; margin: 2px; }}
            QTabBar::tab:selected {{ background: {t['gold']}; color: {t['window_bg']}; }}
            QGroupBox {{
                color: {t['gold_light']}; font-weight: 600; font-size: 13px;
                border: 1px solid {t['border']}; border-radius: 8px;
                margin-top: 12px; padding-top: 12px;
            }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
            QLabel {{ color: {t['text']}; }}
            QPushButton {{
                background: {t['panel_bg_2']}; color: {t['text']};
                border: 1px solid {t['border']}; border-radius: 6px;
                padding: 8px 16px; font-size: 12px;
            }}
            QPushButton:hover {{ background: {t['border']}; }}
            QPushButton#GoldBtn {{ background: {t['gold']}; color: {t['window_bg']}; font-weight: 600; border: none; }}
            QPushButton#GoldBtn:hover {{ background: {t['gold_light']}; }}
            QPushButton#DangerBtn {{ background: transparent; color: #e05050; border: 1px solid #603030; }}
            QPushButton#DangerBtn:hover {{ background: #302020; }}
            QComboBox {{
                background: {t['panel_bg_2']}; color: {t['text']};
                border: 1px solid {t['border']}; border-radius: 6px;
                padding: 6px 10px; min-width: 120px;
            }}
            QComboBox::drop-down {{ border: none; width: 24px; }}
            QComboBox QAbstractItemView {{ background: {t['panel_bg']}; color: {t['text']}; selection-background-color: {t['active']}; }}
            QCheckBox {{ color: {t['text']}; spacing: 8px; }}
            QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 4px; border: 1px solid {t['border']}; background: {t['panel_bg_2']}; }}
            QCheckBox::indicator:checked {{ background: {t['gold']}; border-color: {t['gold']}; }}
            QSpinBox {{
                background: {t['panel_bg_2']}; color: {t['text']};
                border: 1px solid {t['border']}; border-radius: 6px;
                padding: 4px 8px; min-width: 80px;
            }}
            QLineEdit {{
                background: {t['panel_bg_2']}; color: {t['text']};
                border: 1px solid {t['border']}; border-radius: 6px;
                padding: 6px 10px;
            }}
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

        # Sidebar group
        sidebar_group = QGroupBox("Sidebar")
        sidebar_layout = QVBoxLayout(sidebar_group)
        self.hover_expand_cb = QCheckBox("Hover to expand sidebar (collapse when not hovering)")
        sidebar_layout.addWidget(self.hover_expand_cb)
        layout.addWidget(sidebar_group)

        layout.addStretch(1)
        return tab

    def _build_playback_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        # Volume group
        vol_group = QGroupBox("Volume")
        vol_layout = QFormLayout(vol_group)
        self.default_vol_spin = QSpinBox()
        self.default_vol_spin.setRange(0, 100)
        self.default_vol_spin.setSuffix(" %")
        vol_layout.addRow("Default volume:", self.default_vol_spin)
        layout.addWidget(vol_group)

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

        layout.addStretch(1)
        return tab

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
        # Appearance
        idx = self.theme_combo.findData(self.main_window.theme_name)
        if idx >= 0:
            self.theme_combo.setCurrentIndex(idx)
        self._update_theme_preview()
        self.hover_expand_cb.setChecked(getattr(self.main_window, 'hover_expand', True))

        # Playback
        self.default_vol_spin.setValue(getattr(self.main_window, '_last_volume', 80))
        self.sleep_cb.setChecked(getattr(self.main_window, 'sleep_timer_active', False))
        self.sleep_spin.setValue(getattr(self.main_window, 'sleep_timer_minutes', 30))
        self.sleep_spin.setEnabled(self.sleep_cb.isChecked())

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

    def _on_ok(self):
        # Apply appearance
        new_theme = self.theme_combo.currentData()
        if new_theme != self.main_window.theme_name:
            self.main_window.theme_name = new_theme
            self.main_window._apply_theme()
        self.main_window.hover_expand = self.hover_expand_cb.isChecked()

        # Apply playback
        self.main_window._last_volume = self.default_vol_spin.value()
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
