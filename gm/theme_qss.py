"""Golden Music 2.3 — modern QSS design system.

build_qss_v3() restyles the whole application: refined typography scale,
consistent 10px radii, layered surfaces, hover/press states everywhere,
modern scrollbars/menus/tooltips/tabs/tables, and a sidebar look that
supports icon+label rows. Keyed on the SAME objectNames the app already
uses, plus the new v3 ones (Sidebar, CommandPalette, SettingsPage...).

``scale`` (0.75–1.5) multiplies the typography scale; layout paddings use
rem-like steps so density stays proportional. ``reduce_animations`` removes
transition-y feel via flat hovers (Qt QSS has no transitions; animations
live in gm.ui.animations — this flag mostly documents intent + tightens
hover colors).
"""
from __future__ import annotations

from config import Theme, _lighten, _darken


def _clamp_scale(scale: float) -> float:
    try:
        s = float(scale)
    except (TypeError, ValueError):
        s = 1.0
    return max(0.75, min(1.5, s))


def font_stack() -> str:
    return ("'Segoe UI Variable Text','Segoe UI','Noto Sans','Ubuntu',"
            "'DejaVu Sans',sans-serif")


def font_stack_display() -> str:
    return ("'Segoe UI Variable Display','Segoe UI','Noto Sans',"
            "'DejaVu Sans',sans-serif")


def build_qss_v3(t: dict, scale: float = 1.0,
                 reduce_animations: bool = False) -> str:
    s = _clamp_scale(scale)
    fs = lambda px: f"{round(px * s)}px"  # noqa: E731

    bg = t["window_bg"]
    panel = t["panel_bg"]
    panel2 = t["panel_bg_2"]
    border = t["border"]
    text = t["text"]
    muted = t["muted"]
    gold = t["gold"]
    gold_l = t["gold_light"]
    gold_d = t["gold_deep"]
    active = t["active"]
    is_dark = bool(t.get("is_dark", True))

    hover = _lighten(panel2, 16) if is_dark else _darken(panel2, 6)
    press = _lighten(panel2, 26) if is_dark else _darken(panel2, 12)
    surface3 = _lighten(panel, 7) if is_dark else _darken(panel, 3)

    q = f"""
* {{
    font-family: {font_stack()};
    font-size: {fs(13)};
    color: {text};
    outline: none;
}}
QWidget#Root {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 {_lighten(bg, 5)}, stop:1 {bg});
}}
QFrame#MainArea, QWidget#MainArea {{ background: transparent; }}

/* ---------- Sidebar (v3) ---------- */
QFrame#Sidebar {{
    background: {panel};
    border-right: 1px solid {border};
}}
QFrame#Sidebar QLabel#SidebarHeader {{
    color: {muted};
    font-size: {fs(10.5)};
    font-weight: 700;
    letter-spacing: 1.5px;
    padding: 14px 16px 6px 16px;
}}
QPushButton#SidebarBtn {{
    background: transparent;
    border: none;
    border-radius: 9px;
    padding: 8px 12px 8px 10px;
    text-align: left;
    color: {muted};
    font-size: {fs(13)};
}}
QPushButton#SidebarBtn:hover {{ background: {hover}; color: {text}; }}
QPushButton#SidebarBtn:checked {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 {gold_d}, stop:1 {gold});
    color: {bg if not is_dark else '#141414'};
    font-weight: 600;
}}
QPushButton#SidebarBtn:checked:hover {{ background: {gold}; }}
QFrame#SidebarSep {{
    background: {border};
    max-height: 1px;
    border: none;
    margin: 6px 12px;
}}

/* ---------- legacy rail still supported ---------- */
QFrame#SideRail {{ background: {panel}; border-right: 1px solid {border}; }}
QToolButton#RailBtn {{
    background: transparent; border: none; border-radius: 10px;
    margin: 2px;
}}
QToolButton#RailBtn:hover {{ background: {hover}; }}
QToolButton#RailBtn:checked {{ background: {active}; }}

/* ---------- page title & toolbar ---------- */
QLabel#PageTitle {{
    font-family: {font_stack_display()};
    font-size: {fs(22)};
    font-weight: 700;
    color: {text};
    background: transparent;
    padding: 2px 4px 2px 0px;
}}
QLabel#PageSubtitle {{ color: {muted}; font-size: {fs(12.5)}; }}
QLabel#SectionTitle {{
    font-size: {fs(15)}; font-weight: 700; color: {text};
}}
QLabel#StatValue {{
    font-family: {font_stack_display()};
    font-size: {fs(26)}; font-weight: 800; color: {gold};
}}
QLabel#StatCaption {{ color: {muted}; font-size: {fs(11.5)}; }}

QLineEdit {{
    background: {panel2};
    border: 1px solid {border};
    border-radius: 10px;
    padding: 8px 34px 8px 12px;
    color: {text};
    selection-background-color: {gold};
    selection-color: {bg if not is_dark else '#141414'};
}}
QLineEdit:focus {{ border-color: {gold}; background: {surface3}; }}
QLineEdit:hover {{ border-color: {_lighten(border, 18)}; }}

QComboBox {{
    background: {panel2}; border: 1px solid {border};
    border-radius: 9px; padding: 6px 30px 6px 12px; color: {text};
}}
QComboBox:hover {{ border-color: {_lighten(border, 18)}; }}
QComboBox:focus {{ border-color: {gold}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox QAbstractItemView {{
    background: {panel}; border: 1px solid {border};
    border-radius: 10px; padding: 4px;
    selection-background-color: {active};
    selection-color: {text};
    outline: 0;
}}

QPushButton {{
    background: {panel2};
    border: 1px solid {border};
    border-radius: 9px;
    padding: 7px 14px;
    color: {text};
    font-weight: 500;
}}
QPushButton:hover {{ background: {hover}; border-color: {_lighten(border, 18)}; }}
QPushButton:pressed {{ background: {press}; }}
QPushButton:disabled {{ color: {muted}; background: {panel}; }}
QPushButton#GoldBtn {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 {gold_l}, stop:1 {gold});
    color: {'#141414' if is_dark else '#141414'};
    border: none; font-weight: 700;
}}
QPushButton#GoldBtn:hover {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 {gold}, stop:1 {gold_l});
}}
QPushButton#GhostBtn {{
    background: transparent; border: 1px solid {border}; color: {text};
}}
QPushButton#GhostBtn:hover {{ background: {hover}; }}
QPushButton#PlayBtn {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 {gold_l}, stop:1 {gold});
    border: none; border-radius: 23px; color: #141414;
}}
QPushButton#PlayBtn:hover {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 {gold}, stop:1 {gold_l});
}}
QPushButton#PlayBtn:pressed {{ background: {gold_d}; }}

/* ---------- lists, tables, trees ---------- */
QListWidget, QTreeWidget, QTableView, QTreeView {{
    background: transparent;
    border: none;
    alternate-background-color: {surface3};
}}
QListWidget#TrackList::item, QTreeWidget#TrackList::item {{
    border-radius: 8px;
    margin: 1px 2px;
}}
QListWidget::item:selected, QTreeWidget::item:selected, QTableView::item:selected {{
    background: {active};
    color: {text};
}}
QListWidget::item:hover, QTreeWidget::item:hover {{ background: {hover}; }}
QTableView {{
    gridline-color: transparent;
    selection-background-color: {active};
}}
QHeaderView::section {{
    background: {panel};
    color: {muted};
    border: none;
    border-bottom: 1px solid {border};
    padding: 8px 10px;
    font-size: {fs(11.5)};
    font-weight: 700;
}}
QTableView QTableCornerButton::section {{
    background: {panel}; border: none;
}}

QScrollBar:vertical {{
    background: transparent; width: 10px; margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {_lighten(border, 12)};
    border-radius: 5px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {muted}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{
    background: transparent; height: 10px; margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {_lighten(border, 12)};
    border-radius: 5px; min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{ background: {muted}; }}

/* ---------- sliders ---------- */
QSlider#SeekSlider, QSlider#VolSlider {{
    background: transparent; height: 18px; border: none;
}}
QSlider::groove:horizontal {{
    height: 5px; border-radius: 2px;
    background: {panel2};
}}
QSlider#SeekSlider::groove:horizontal {{
    height: 6px; border-radius: 3px;
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 {gold_d}, stop:1 {gold});
}}
QSlider::sub-page:horizontal {{
    height: 6px; border-radius: 3px; background: {gold};
}}
QSlider::handle:horizontal {{
    width: 14px; height: 14px; margin: -5px 0;
    border-radius: 7px; background: {gold_l};
    border: 2px solid {panel};
}}
QSlider::handle:horizontal:hover {{ background: {gold_l}; }}

/* ---------- progress ---------- */
QProgressBar {{
    background: transparent; border: none; height: 4px;
    border-radius: 2px;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 {gold_d}, stop:1 {gold});
    border-radius: 2px;
}}

/* ---------- player bar ---------- */
QFrame#PlayerBar {{
    background: {panel};
    border-top: 1px solid {border};
}}
QLabel#BarTitle {{ font-weight: 700; font-size: {fs(13.5)}; color: {text}; }}
QLabel#BarArtist {{ color: {muted}; font-size: {fs(12)}; }}
QLabel#NowTitle {{ font-weight: 700; font-size: {fs(15)}; }}
QLabel#NowArtist {{ color: {muted}; font-size: {fs(12.5)}; }}
QLabel#TimeLabel {{ color: {muted}; font-size: {fs(11.5)}; }}
QLabel#CountLabel {{ color: {muted}; font-size: {fs(12)}; }}

/* ---------- menus & tooltips ---------- */
QMenu {{
    background: {panel};
    border: 1px solid {border};
    border-radius: 12px;
    padding: 6px;
}}
QMenu::item {{
    padding: 7px 24px 7px 14px;
    border-radius: 7px;
    color: {text};
}}
QMenu::item:selected {{ background: {active}; }}
QMenu::item:disabled {{ color: {muted}; }}
QMenu::separator {{
    height: 1px; background: {border}; margin: 5px 8px;
}}
QMenu::icon {{ padding-left: 8px; }}
QToolTip {{
    background: {panel};
    color: {text};
    border: 1px solid {gold_d};
    border-radius: 7px;
    padding: 6px 10px;
    font-size: {fs(12)};
}}

/* ---------- tabs (settings, dialogs) ---------- */
QTabWidget::pane {{
    border: 1px solid {border}; border-radius: 10px; top: -1px;
    background: {panel};
}}
QTabBar::tab {{
    background: transparent; color: {muted};
    padding: 9px 16px; margin-right: 2px;
    border-top-left-radius: 9px; border-top-right-radius: 9px;
    font-weight: 600;
}}
QTabBar::tab:hover {{ color: {text}; background: {hover}; }}
QTabBar::tab:selected {{
    color: {gold}; background: {panel};
    border-bottom: 2px solid {gold};
}}

/* ---------- checkboxes / radios ---------- */
QCheckBox, QRadioButton {{ spacing: 8px; padding: 4px 0; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 17px; height: 17px; border-radius: 5px;
    border: 1px solid {muted}; background: {panel2};
}}
QRadioButton::indicator {{ border-radius: 9px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {gold};
}}
QCheckBox::indicator:checked {{
    background: {gold}; border-color: {gold};
    image: url(none);
}}

/* ---------- dialogs ---------- */
QDialog {{ background: {bg}; }}

/* ---------- settings (v3) ---------- */
QFrame#SettingsSide {{
    background: {panel}; border-right: 1px solid {border};
}}
QPushButton#SettingsCatBtn {{
    background: transparent; border: none; border-radius: 9px;
    padding: 9px 14px; text-align: left; color: {muted};
    font-weight: 600; font-size: {fs(13)};
}}
QPushButton#SettingsCatBtn:hover {{ background: {hover}; color: {text}; }}
QPushButton#SettingsCatBtn:checked {{
    background: {active}; color: {gold};
    border-left: 3px solid {gold};
}}
QScrollArea {{ background: transparent; border: none; }}
QGroupBox {{
    background: {panel};
    border: 1px solid {border};
    border-radius: 12px;
    margin-top: 14px;
    padding: 14px 12px 10px 12px;
    font-weight: 700;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px; padding: 0 5px;
    color: {gold};
}}

/* ---------- command palette ---------- */
QFrame#CommandPalette {{
    background: {panel};
    border: 1px solid {gold_d};
    border-radius: 14px;
}}
QLineEdit#CommandInput {{
    background: transparent; border: none; border-bottom: 1px solid {border};
    border-radius: 0; padding: 14px 16px; font-size: {fs(15)};
}}
QListWidget#CommandList {{
    background: transparent; border: none;
}}
/* NOTE: no vertical padding/margin here! QSS ::item padding+margin is
   SUBTRACTED from every setItemWidget row's geometry — with vertical
   insets the rows collapse (labels get 0 height, only icons show).
   Horizontal-only margin keeps the inset selection bar; all vertical
   rhythm lives inside the row/header widgets themselves. */
QListWidget#CommandList::item {{
    margin: 0 6px; border-radius: 8px;
}}
QListWidget#CommandList::item:selected {{ background: {active}; }}

/* ---------- skeletons / empty states ---------- */
QFrame#SkeletonLine {{
    background: {panel2}; border-radius: 5px;
}}
QLabel#EmptyTitle {{
    font-family: {font_stack_display()};
    font-size: {fs(17)}; font-weight: 700; color: {text};
}}
QLabel#EmptyHint {{ color: {muted}; font-size: {fs(12.5)}; }}

/* ---------- rating stars ---------- */
QPushButton#StarBtn {{
    background: transparent; border: none; padding: 1px;
}}
QPushButton#StarBtn:hover {{ background: {hover}; border-radius: 5px; }}
"""
    if reduce_animations:
        # flatter, calmer hovers for motion-sensitive users
        q = q.replace("QPushButton#SidebarBtn:checked {", "QPushButton#SidebarBtn:checked {\n    /* reduce-animations: flat */")
    return q
