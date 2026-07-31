"""
ThemeManager — QForge design system (see ai/ui-design.md for the source of
truth: palette tokens, component rules, and accessibility checks).
"""


class ThemeManager:

    # ── Dark tokens — "graphite blue" ───────────────────────────────────────
    D_BG           = "#17191E"   # app background
    D_SIDEBAR      = "#1E2128"   # navigation / object browser
    D_WORKSPACE    = "#20232A"   # SQL editor / main canvas
    D_RAISED       = "#292D36"   # tabs, toolbars, menus, dialogs
    D_INPUT        = "#2D323C"   # text inputs and combos
    D_HOVER        = "#323845"   # hovered controls / headers
    D_BORDER       = "#3A404C"   # separators, gridlines, quiet outlines
    D_BORDER_STRONG = "#56606F"  # hovered outlines only
    D_TEXT         = "#E7EAF0"
    D_TEXT2        = "#A9B0BD"
    D_TEXT3        = "#737B89"
    D_BLUE         = "#4F8CFF"
    D_BLUE_HOVER   = "#70A4FF"
    D_BLUE_PRESS   = "#326FD5"
    D_SELECTION    = "#31578F"
    D_DANGER       = "#FF6B6B"
    D_DANGER_HOVER = "#FF8888"

    # ── Dark environment-safety tokens (background / text / border) ─────────
    D_ENV_UNCLASSIFIED_BG     = "#2A2E36"
    D_ENV_UNCLASSIFIED_TEXT   = "#A9B0BD"
    D_ENV_UNCLASSIFIED_BORDER = "#56606F"
    D_ENV_LOCAL_BG            = "#163D2A"
    D_ENV_LOCAL_TEXT          = "#71D69A"
    D_ENV_LOCAL_BORDER        = "#2D8054"
    D_ENV_DEVELOPMENT_BG      = "#1E2E3D"
    D_ENV_DEVELOPMENT_TEXT    = "#8FC4E8"
    D_ENV_DEVELOPMENT_BORDER  = "#3E7CA6"
    D_ENV_STAGING_BG          = "#4A3716"
    D_ENV_STAGING_TEXT        = "#F2C56B"
    D_ENV_STAGING_BORDER      = "#A96F10"
    D_ENV_PRODUCTION_BG       = "#542228"
    D_ENV_PRODUCTION_TEXT     = "#FFAAA8"
    D_ENV_PRODUCTION_BORDER   = "#D96565"

    # ── Light tokens — "soft paper" ──────────────────────────────────────────
    L_BG        = "#F3F4F6"
    L_SIDEBAR   = "#E9ECF1"
    L_WORKSPACE = "#FAFAFB"
    L_RAISED    = "#FFFFFF"
    L_HOVER     = "#DDE1E8"
    L_BORDER    = "#D8DCE3"
    L_TEXT      = "#20242C"
    L_TEXT2     = "#687080"
    L_TEXT3     = "#8B93A1"
    L_BLUE       = "#2563EB"
    L_BLUE_HOVER = "#1D4ED8"
    L_BLUE_PRESS = "#1E40AF"
    L_SELECTION  = "#DCEAFE"
    L_DANGER       = "#D92D20"
    L_DANGER_HOVER = "#B42318"

    # ── Light environment-safety tokens (background / text / border) ────────
    L_ENV_UNCLASSIFIED_BG     = "#E9ECF1"
    L_ENV_UNCLASSIFIED_TEXT   = "#687080"
    L_ENV_UNCLASSIFIED_BORDER = "#B7BEC9"
    L_ENV_LOCAL_BG            = "#E3F5EA"
    L_ENV_LOCAL_TEXT          = "#1D6B41"
    L_ENV_LOCAL_BORDER        = "#4CA97A"
    L_ENV_DEVELOPMENT_BG      = "#E4EEF7"
    L_ENV_DEVELOPMENT_TEXT    = "#1F5C85"
    L_ENV_DEVELOPMENT_BORDER  = "#5B93B8"
    L_ENV_STAGING_BG          = "#FBF0DA"
    L_ENV_STAGING_TEXT        = "#8A5A0A"
    L_ENV_STAGING_BORDER      = "#D3A03E"
    L_ENV_PRODUCTION_BG       = "#FBE4E4"
    L_ENV_PRODUCTION_TEXT     = "#B3261E"
    L_ENV_PRODUCTION_BORDER   = "#E08585"

    # Back-compat aliases used by call sites that reference the accent
    # directly rather than through a generated stylesheet.
    ACCENT       = D_BLUE
    ACCENT_HOVER = D_BLUE_HOVER
    ACCENT_PRESS = D_BLUE_PRESS

    @classmethod
    def env_colors(cls, env: str, is_dark: bool) -> tuple:
        """(background, text, border) hex colors for a normalized environment
        key (see utils/environment.py). `env` must already be normalized."""
        prefix = "D_ENV_" if is_dark else "L_ENV_"
        key = env.upper()
        return (
            getattr(cls, f"{prefix}{key}_BG"),
            getattr(cls, f"{prefix}{key}_TEXT"),
            getattr(cls, f"{prefix}{key}_BORDER"),
        )

    # ── Dark palette ──────────────────────────────────────────────────────────
    @staticmethod
    def get_dark_theme() -> str:
        BG, SIDEBAR, WORKSPACE = ThemeManager.D_BG, ThemeManager.D_SIDEBAR, ThemeManager.D_WORKSPACE
        RAISED, INPUT, HOVER = ThemeManager.D_RAISED, ThemeManager.D_INPUT, ThemeManager.D_HOVER
        BORDER, BORDER_S = ThemeManager.D_BORDER, ThemeManager.D_BORDER_STRONG
        TEXT, TEXT2, TEXT3 = ThemeManager.D_TEXT, ThemeManager.D_TEXT2, ThemeManager.D_TEXT3
        A, AH, AP = ThemeManager.D_BLUE, ThemeManager.D_BLUE_HOVER, ThemeManager.D_BLUE_PRESS
        SEL = ThemeManager.D_SELECTION
        DANGER, DANGER_H = ThemeManager.D_DANGER, ThemeManager.D_DANGER_HOVER
        return f"""
/* ── Base ──────────────────────────────────────────────────────── */
QMainWindow {{
    background: {BG};
}}
QDialog {{
    background: {RAISED};
}}
QWidget {{
    background: {BG};
    color: {TEXT};
    font-size: 13px;
}}

/* ── Menu bar ───────────────────────────────────────────────────── */
QMenuBar {{
    background: {RAISED};
    color: {TEXT};
    border-bottom: 1px solid {BORDER};
    padding: 2px 0;
}}
QMenuBar::item {{ padding: 4px 10px; border-radius: 4px; }}
QMenuBar::item:selected {{ background: {HOVER}; }}

QMenu {{
    background: {RAISED};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 4px;
}}
QMenu::item {{ padding: 6px 24px 6px 14px; border-radius: 4px; }}
QMenu::item:selected {{ background: {A}; color: #fff; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 3px 8px; }}
QMenu::right-arrow {{ width: 6px; height: 6px; }}

/* ── Sidebar tree ───────────────────────────────────────────────── */
QTreeWidget {{
    background: {SIDEBAR};
    color: {TEXT2};
    border: none;
    outline: none;
    font-size: 12.5px;
}}
QTreeWidget::item {{
    height: 24px;
    padding-left: 2px;
    border-radius: 4px;
}}
QTreeWidget::item:hover  {{ background: {HOVER}; }}
QTreeWidget::item:selected {{ background: {A}22; color: {A}; }}
QTreeWidget QHeaderView::section {{
    background: {SIDEBAR};
    color: {TEXT3};
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    padding: 4px 8px;
    border: none;
    border-bottom: 1px solid {BORDER};
}}

/* ── Tab bar (content tabs) ─────────────────────────────────────── */
QTabWidget::pane {{
    border: none;
    background: {WORKSPACE};
}}
QTabBar {{
    background: {RAISED};
}}
QTabBar::tab {{
    background: transparent;
    color: {TEXT2};
    padding: 8px 18px;
    border: none;
    border-bottom: 2px solid transparent;
    margin-right: 1px;
    font-size: 13px;
}}
QTabBar::tab:selected {{
    color: {TEXT};
    border-bottom: 2px solid {A};
    background: {WORKSPACE};
}}
QTabBar::tab:hover:!selected {{ color: {TEXT}; background: {HOVER}; }}
QTabBar::close-button {{
    subcontrol-position: right;
    subcontrol-origin: padding;
    width: 16px;
    height: 16px;
    border-radius: 3px;
    image: url("data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 16 16'><line x1='4' y1='4' x2='12' y2='12' stroke='%23{TEXT2[1:]}' stroke-width='2' stroke-linecap='round'/><line x1='12' y1='4' x2='4' y2='12' stroke='%23{TEXT2[1:]}' stroke-width='2' stroke-linecap='round'/></svg>");
}}
QTabBar::close-button:hover {{
    background: {DANGER}33;
    border-radius: 3px;
    image: url("data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 16 16'><line x1='4' y1='4' x2='12' y2='12' stroke='%23{DANGER[1:]}' stroke-width='2.5' stroke-linecap='round'/><line x1='12' y1='4' x2='4' y2='12' stroke='%23{DANGER[1:]}' stroke-width='2.5' stroke-linecap='round'/></svg>");
}}
QTabBar#conn_tab_bar::tab {{
    background: transparent;
    color: {TEXT2};
    padding: 6px 14px;
    border: none;
    border-bottom: 2px solid transparent;
    border-right: 1px solid {BORDER};
    font-size: 12px;
    margin-right: 2px;
}}
QTabBar#conn_tab_bar::tab:selected {{
    color: {TEXT};
    border-bottom: 2px solid {A};
}}

/* ── Text / SQL editor (QPlainTextEdit + QTextEdit) ─────────────── */
QPlainTextEdit, QTextEdit {{
    background: {WORKSPACE};
    color: {TEXT};
    border: none;
    selection-background-color: {SEL};
    selection-color: {TEXT};
    font-family: 'Menlo', 'Monaco', 'Courier New', monospace;
    font-size: 13px;
    line-height: 1.5;
}}

/* ── Search / input ─────────────────────────────────────────────── */
QLineEdit {{
    background: {INPUT};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: {A};
}}
QLineEdit:hover  {{ border-color: {BORDER_S}; }}
QLineEdit:focus  {{ border-color: {A}; }}
QLineEdit::placeholder {{ color: {TEXT3}; }}

/* ── Buttons ────────────────────────────────────────────────────── */
QPushButton {{
    background: {A};
    color: #fff;
    border: none;
    border-radius: 6px;
    padding: 5px 16px;
    font-weight: 600;
    font-size: 13px;
}}
QPushButton:hover  {{ background: {AH}; }}
QPushButton:pressed {{ background: {AP}; }}
QPushButton:disabled {{ background: {RAISED}; color: {TEXT3}; }}
/* secondary — neutral, bordered */
QPushButton[flat="true"] {{
    background: transparent;
    color: {A};
    border: 1px solid {A}66;
}}
QPushButton[flat="true"]:hover {{ background: {A}22; }}
/* destructive — explicit danger treatment, never primary blue */
QPushButton[danger="true"] {{
    background: transparent;
    color: {DANGER};
    border: 1px solid {DANGER}88;
}}
QPushButton[danger="true"]:hover {{ background: {DANGER}22; border-color: {DANGER}; }}
QPushButton[danger="true"]:pressed {{ background: {DANGER}33; }}

/* ── Combo box ──────────────────────────────────────────────────── */
QComboBox {{
    background: {INPUT};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 5px 8px;
}}
QComboBox:hover {{ border-color: {BORDER_S}; }}
QComboBox:focus {{ border-color: {A}; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{
    background: {RAISED};
    color: {TEXT};
    border: 1px solid {BORDER};
    selection-background-color: {A};
    outline: none;
}}

/* ── Table / results grid ───────────────────────────────────────── */
QTableWidget, QTableView {{
    background: {WORKSPACE};
    color: {TEXT};
    gridline-color: {BORDER};
    border: none;
    selection-background-color: {SEL};
    selection-color: {TEXT};
    alternate-background-color: {WORKSPACE};
    outline: none;
}}
QTableWidget::item, QTableView::item {{
    padding: 2px 6px;
    border: none;
}}
QTableWidget::item:selected, QTableView::item:selected {{
    background: {SEL};
    color: {TEXT};
}}
QHeaderView::section {{
    background: {RAISED};
    color: {TEXT2};
    border: none;
    border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
    padding: 4px 8px;
    font-size: 12px;
    font-weight: 600;
}}
QHeaderView::section:hover {{ background: {HOVER}; color: {TEXT}; }}

/* ── Splitter ───────────────────────────────────────────────────── */
QSplitter::handle {{ background: {BORDER}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical   {{ height: 1px; }}
QSplitter::handle:hover {{ background: {A}; }}

/* ── Scroll bars (thin, macOS style) ────────────────────────────── */
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {BORDER_S};
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{ background: {TEXT3}; }}
QScrollBar:horizontal {{
    background: transparent;
    height: 8px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {BORDER_S};
    border-radius: 4px;
    min-width: 24px;
}}
QScrollBar::handle:horizontal:hover {{ background: {TEXT3}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ── Status bar ─────────────────────────────────────────────────── */
QStatusBar {{
    background: {RAISED};
    color: {TEXT2};
    border-top: 1px solid {BORDER};
    font-size: 12px;
}}

/* ── Progress dialog ────────────────────────────────────────────── */
QProgressDialog {{
    background: {RAISED};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}

/* ── Tool tips ──────────────────────────────────────────────────── */
QToolTip {{
    background: {RAISED};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 12px;
}}

/* ── Labels ─────────────────────────────────────────────────────── */
QLabel {{ color: {TEXT}; background: transparent; }}

/* ── Message box ────────────────────────────────────────────────── */
QMessageBox {{ background: {RAISED}; }}
QMessageBox QLabel {{ color: {TEXT}; }}
"""

    # ── Light palette ─────────────────────────────────────────────────────────
    @staticmethod
    def get_light_theme() -> str:
        BG, SIDEBAR, WORKSPACE = ThemeManager.L_BG, ThemeManager.L_SIDEBAR, ThemeManager.L_WORKSPACE
        RAISED, HOVER, BORDER = ThemeManager.L_RAISED, ThemeManager.L_HOVER, ThemeManager.L_BORDER
        TEXT, TEXT2, TEXT3 = ThemeManager.L_TEXT, ThemeManager.L_TEXT2, ThemeManager.L_TEXT3
        A, AH, AP = ThemeManager.L_BLUE, ThemeManager.L_BLUE_HOVER, ThemeManager.L_BLUE_PRESS
        SEL = ThemeManager.L_SELECTION
        DANGER, DANGER_H = ThemeManager.L_DANGER, ThemeManager.L_DANGER_HOVER
        return f"""
QMainWindow {{ background: {BG}; }}
QDialog {{ background: {RAISED}; }}
QWidget {{ background: {BG}; color: {TEXT}; font-size: 13px; }}

QMenuBar {{
    background: {RAISED}; color: {TEXT};
    border-bottom: 1px solid {BORDER}; padding: 2px 0;
}}
QMenuBar::item {{ padding: 4px 10px; border-radius: 4px; }}
QMenuBar::item:selected {{ background: {HOVER}; }}
QMenu {{
    background: {RAISED}; color: {TEXT};
    border: 1px solid {BORDER}; border-radius: 8px; padding: 4px;
}}
QMenu::item {{ padding: 6px 24px 6px 14px; border-radius: 4px; }}
QMenu::item:selected {{ background: {A}; color: #fff; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 3px 8px; }}
QMenu::right-arrow {{ width: 6px; height: 6px; }}

QTreeWidget {{
    background: {SIDEBAR}; color: {TEXT2};
    border: none; outline: none; font-size: 12.5px;
}}
QTreeWidget::item {{ height: 24px; padding-left: 2px; border-radius: 4px; }}
QTreeWidget::item:hover {{ background: {HOVER}; }}
QTreeWidget::item:selected {{ background: {A}22; color: {A}; }}
QTreeWidget QHeaderView::section {{
    background: {SIDEBAR}; color: {TEXT3};
    font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px;
    padding: 4px 8px; border: none; border-bottom: 1px solid {BORDER};
}}

QTabWidget::pane {{ border: none; background: {WORKSPACE}; }}
QTabBar {{ background: {RAISED}; }}
QTabBar::tab {{
    background: transparent; color: {TEXT2};
    padding: 8px 18px; border: none;
    border-bottom: 2px solid transparent; margin-right: 1px;
    font-size: 13px;
}}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {A}; background: {WORKSPACE}; }}
QTabBar::tab:hover:!selected {{ color: {TEXT}; background: {HOVER}; }}
QTabBar::close-button {{
    subcontrol-position: right;
    subcontrol-origin: padding;
    width: 16px;
    height: 16px;
    border-radius: 3px;
    image: url("data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 16 16'><line x1='4' y1='4' x2='12' y2='12' stroke='%23{TEXT2[1:]}' stroke-width='2' stroke-linecap='round'/><line x1='12' y1='4' x2='4' y2='12' stroke='%23{TEXT2[1:]}' stroke-width='2' stroke-linecap='round'/></svg>");
}}
QTabBar::close-button:hover {{
    background: {DANGER}22;
    border-radius: 3px;
    image: url("data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 16 16'><line x1='4' y1='4' x2='12' y2='12' stroke='%23{DANGER[1:]}' stroke-width='2.5' stroke-linecap='round'/><line x1='12' y1='4' x2='4' y2='12' stroke='%23{DANGER[1:]}' stroke-width='2.5' stroke-linecap='round'/></svg>");
}}
QTabBar#conn_tab_bar::tab {{
    background: transparent; color: {TEXT2};
    padding: 6px 14px; border: none;
    border-bottom: 2px solid transparent; border-right: 1px solid {BORDER};
    font-size: 12px; margin-right: 2px;
}}
QTabBar#conn_tab_bar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {A}; }}

QPlainTextEdit, QTextEdit {{
    background: {WORKSPACE}; color: {TEXT}; border: none;
    selection-background-color: {SEL}; selection-color: {TEXT};
    font-family: 'Menlo', 'Monaco', 'Courier New', monospace; font-size: 13px;
    line-height: 1.5;
}}
QLineEdit {{
    background: {RAISED}; color: {TEXT};
    border: 1px solid {BORDER}; border-radius: 6px; padding: 5px 8px;
    selection-background-color: {A};
}}
QLineEdit:hover {{ border-color: {TEXT3}; }}
QLineEdit:focus {{ border-color: {A}; }}
QLineEdit::placeholder {{ color: {TEXT3}; }}

QPushButton {{
    background: {A}; color: #fff; border: none;
    border-radius: 6px; padding: 5px 16px; font-weight: 600; font-size: 13px;
}}
QPushButton:hover {{ background: {AH}; }}
QPushButton:pressed {{ background: {AP}; }}
QPushButton:disabled {{ background: {HOVER}; color: {TEXT3}; }}
QPushButton[flat="true"] {{
    background: transparent; color: {A}; border: 1px solid {A}66;
}}
QPushButton[flat="true"]:hover {{ background: {A}1a; }}
QPushButton[danger="true"] {{
    background: transparent; color: {DANGER}; border: 1px solid {DANGER}88;
}}
QPushButton[danger="true"]:hover {{ background: {DANGER}1a; border-color: {DANGER}; }}
QPushButton[danger="true"]:pressed {{ background: {DANGER}33; }}

QComboBox {{
    background: {RAISED}; color: {TEXT};
    border: 1px solid {BORDER}; border-radius: 6px; padding: 5px 8px;
}}
QComboBox:hover {{ border-color: {TEXT3}; }}
QComboBox:focus {{ border-color: {A}; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{
    background: {RAISED}; color: {TEXT};
    border: 1px solid {BORDER}; selection-background-color: {A}; outline: none;
}}

QTableWidget, QTableView {{
    background: {WORKSPACE}; color: {TEXT};
    gridline-color: {BORDER}; border: none;
    selection-background-color: {SEL}; selection-color: {TEXT};
    alternate-background-color: {WORKSPACE}; outline: none;
}}
QTableWidget::item, QTableView::item {{ padding: 2px 6px; border: none; }}
QTableWidget::item:selected, QTableView::item:selected {{ background: {SEL}; color: {TEXT}; }}
QHeaderView::section {{
    background: {RAISED}; color: {TEXT2}; border: none;
    border-right: 1px solid {BORDER}; border-bottom: 1px solid {BORDER};
    padding: 4px 8px; font-size: 12px; font-weight: 600;
}}
QHeaderView::section:hover {{ background: {HOVER}; color: {TEXT}; }}

QSplitter::handle {{ background: {BORDER}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}
QSplitter::handle:hover {{ background: {A}; }}

QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {TEXT3}; border-radius: 4px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {TEXT2}; }}
QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {TEXT3}; border-radius: 4px; min-width: 24px; }}
QScrollBar::handle:horizontal:hover {{ background: {TEXT2}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QStatusBar {{
    background: {RAISED}; color: {TEXT2};
    border-top: 1px solid {BORDER}; font-size: 12px;
}}
QProgressDialog {{
    background: {RAISED}; color: {TEXT};
    border: 1px solid {BORDER}; border-radius: 8px;
}}

QLabel {{ color: {TEXT}; background: transparent; }}
QToolTip {{
    background: {RAISED}; color: {TEXT};
    border: 1px solid {BORDER}; border-radius: 4px; padding: 4px 8px;
    font-size: 12px;
}}
QMessageBox {{ background: {RAISED}; }}
QMessageBox QLabel {{ color: {TEXT}; }}
"""

    # ── Filter containers ─────────────────────────────────────────────────────
    @staticmethod
    def get_filter_container_style_dark() -> str:
        RAISED, INPUT = ThemeManager.D_RAISED, ThemeManager.D_INPUT
        BORDER, BORDER_S = ThemeManager.D_BORDER, ThemeManager.D_BORDER_STRONG
        TEXT, TEXT3, A, SEL = ThemeManager.D_TEXT, ThemeManager.D_TEXT3, ThemeManager.D_BLUE, ThemeManager.D_SELECTION
        return f"""
            QWidget {{ background: {RAISED}; border: 1px solid {BORDER}; border-radius: 6px; }}
            QComboBox {{
                padding: 3px 6px; border: 1px solid {BORDER}; border-radius: 5px;
                background: {INPUT}; color: {TEXT}; font-size: 12px;
            }}
            QComboBox:hover {{ border-color: {BORDER_S}; }}
            QComboBox:focus {{ border-color: {A}; }}
            QComboBox::drop-down {{ border: none; width: 16px; }}
            QComboBox QAbstractItemView {{
                background: {RAISED}; color: {TEXT};
                selection-background-color: {A}; border: 1px solid {BORDER};
            }}
            QLineEdit {{
                padding: 3px 6px; border: 1px solid {BORDER}; border-radius: 5px;
                background: {INPUT}; color: {TEXT}; font-size: 12px;
            }}
            QLineEdit:hover {{ border-color: {BORDER_S}; }}
            QLineEdit:focus {{ border-color: {A}; }}
        """

    @staticmethod
    def get_filter_container_style_light() -> str:
        RAISED, BORDER = ThemeManager.L_RAISED, ThemeManager.L_BORDER
        TEXT, TEXT3, A = ThemeManager.L_TEXT, ThemeManager.L_TEXT3, ThemeManager.L_BLUE
        return f"""
            QWidget {{ background: {RAISED}; border: 1px solid {BORDER}; border-radius: 6px; }}
            QComboBox {{
                padding: 3px 6px; border: 1px solid {BORDER}; border-radius: 5px;
                background: {RAISED}; color: {TEXT}; font-size: 12px;
            }}
            QComboBox:hover {{ border-color: {TEXT3}; }}
            QComboBox:focus {{ border-color: {A}; }}
            QComboBox::drop-down {{ border: none; width: 16px; }}
            QComboBox QAbstractItemView {{
                background: {RAISED}; color: {TEXT};
                selection-background-color: {A}; border: 1px solid {BORDER};
            }}
            QLineEdit {{
                padding: 3px 6px; border: 1px solid {BORDER}; border-radius: 5px;
                background: {RAISED}; color: {TEXT}; font-size: 12px;
            }}
            QLineEdit:hover {{ border-color: {TEXT3}; }}
            QLineEdit:focus {{ border-color: {A}; }}
        """
