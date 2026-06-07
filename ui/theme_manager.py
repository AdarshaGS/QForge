"""
ThemeManager — polished native-macOS dark/light themes for QForge.
"""


class ThemeManager:

    # ── Accent colour ─────────────────────────────────────────────────────────
    ACCENT        = "#0A84FF"   # macOS system blue (dark-mode)
    ACCENT_HOVER  = "#228BFF"
    ACCENT_PRESS  = "#0066CC"

    # ── Dark palette ──────────────────────────────────────────────────────────
    @staticmethod
    def get_dark_theme() -> str:
        A  = ThemeManager.ACCENT
        AH = ThemeManager.ACCENT_HOVER
        AP = ThemeManager.ACCENT_PRESS
        return f"""
/* ── Base ──────────────────────────────────────────────────────── */
QMainWindow, QDialog {{
    background: #1c1c1e;
}}
QWidget {{
    background: #1c1c1e;
    color: #e5e5ea;
    font-size: 13px;
}}

/* ── Menu bar ───────────────────────────────────────────────────── */
QMenuBar {{
    background: #2c2c2e;
    color: #e5e5ea;
    border-bottom: 1px solid #3a3a3c;
    padding: 2px 0;
}}
QMenuBar::item {{ padding: 4px 10px; border-radius: 4px; }}
QMenuBar::item:selected {{ background: #3a3a3c; }}

QMenu {{
    background: #2c2c2e;
    color: #e5e5ea;
    border: 1px solid #3a3a3c;
    border-radius: 8px;
    padding: 4px;
}}
QMenu::item {{ padding: 6px 24px 6px 14px; border-radius: 4px; }}
QMenu::item:selected {{ background: {A}; color: #fff; }}
QMenu::separator {{ height: 1px; background: #3a3a3c; margin: 3px 8px; }}

/* ── Sidebar tree ───────────────────────────────────────────────── */
QTreeWidget {{
    background: #1c1c1e;
    color: #c7c7cc;
    border: none;
    outline: none;
    font-size: 12.5px;
}}
QTreeWidget::item {{
    height: 24px;
    padding-left: 2px;
    border-radius: 4px;
}}
QTreeWidget::item:hover  {{ background: #2c2c2e; }}
QTreeWidget::item:selected {{ background: {A}22; color: {A}; }}
QTreeWidget QHeaderView::section {{
    background: #1c1c1e;
    color: #6e6e73;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    padding: 4px 8px;
    border: none;
    border-bottom: 1px solid #3a3a3c;
}}

/* ── Tab bar (content tabs) ─────────────────────────────────────── */
QTabWidget::pane {{
    border: none;
    background: #1c1c1e;
}}
QTabBar {{
    background: #2c2c2e;
}}
QTabBar::tab {{
    background: transparent;
    color: #8e8e93;
    padding: 8px 18px;
    border: none;
    border-bottom: 2px solid transparent;
    margin-right: 1px;
    font-size: 13px;
}}
QTabBar::tab:selected {{
    color: #e5e5ea;
    border-bottom: 2px solid {A};
    background: #1c1c1e;
}}
QTabBar::tab:hover:!selected {{ color: #c7c7cc; background: #3a3a3c22; }}
QTabBar::close-button {{
    subcontrol-position: right;
    padding: 2px;
    border-radius: 3px;
}}
QTabBar::close-button:hover {{ background: #ff453a33; }}

/* ── Connection tab bar (top-level) ─────────────────────────────── */
QTabBar#conn_tab_bar::tab {{
    background: transparent;
    color: #8e8e93;
    padding: 6px 14px;
    border: none;
    border-bottom: 2px solid transparent;
    font-size: 12px;
}}
QTabBar#conn_tab_bar::tab:selected {{
    color: #e5e5ea;
    border-bottom: 2px solid {A};
}}

/* ── Text / SQL editor (QPlainTextEdit + QTextEdit) ─────────────── */
QPlainTextEdit, QTextEdit {{
    background: #1c1c1e;
    color: #d4d4d4;
    border: none;
    selection-background-color: #264f78;
    selection-color: #ffffff;
    font-family: 'Menlo', 'Monaco', 'Courier New', monospace;
    font-size: 13px;
    line-height: 1.5;
}}

/* ── Search / input ─────────────────────────────────────────────── */
QLineEdit {{
    background: #2c2c2e;
    color: #e5e5ea;
    border: 1px solid #3a3a3c;
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: {A};
}}
QLineEdit:focus {{ border-color: {A}; }}
QLineEdit::placeholder {{ color: #636366; }}

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
QPushButton:disabled {{ background: #3a3a3c; color: #6e6e73; }}
/* ghost buttons (no background) */
QPushButton[flat="true"] {{
    background: transparent;
    color: {A};
    border: 1px solid {A}66;
}}
QPushButton[flat="true"]:hover {{ background: {A}22; }}

/* ── Combo box ──────────────────────────────────────────────────── */
QComboBox {{
    background: #2c2c2e;
    color: #e5e5ea;
    border: 1px solid #3a3a3c;
    border-radius: 6px;
    padding: 5px 8px;
}}
QComboBox:hover {{ border-color: {A}; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{
    background: #2c2c2e;
    color: #e5e5ea;
    border: 1px solid #3a3a3c;
    selection-background-color: {A};
    outline: none;
}}

/* ── Table / results grid ───────────────────────────────────────── */
QTableWidget, QTableView {{
    background: #1c1c1e;
    color: #d4d4d4;
    gridline-color: #2c2c2e;
    border: none;
    selection-background-color: {A}33;
    selection-color: #e5e5ea;
    alternate-background-color: #202022;
    outline: none;
}}
QTableWidget::item, QTableView::item {{
    padding: 2px 6px;
    border: none;
}}
QTableWidget::item:selected, QTableView::item:selected {{
    background: {A}33;
    color: #e5e5ea;
}}
QHeaderView::section {{
    background: #2c2c2e;
    color: #8e8e93;
    border: none;
    border-right: 1px solid #3a3a3c;
    border-bottom: 1px solid #3a3a3c;
    padding: 4px 8px;
    font-size: 12px;
    font-weight: 600;
}}
QHeaderView::section:hover {{ background: #3a3a3c; color: #e5e5ea; }}

/* ── Splitter ───────────────────────────────────────────────────── */
QSplitter::handle {{ background: #3a3a3c; }}
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
    background: #48484a;
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{ background: #636366; }}
QScrollBar:horizontal {{
    background: transparent;
    height: 8px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: #48484a;
    border-radius: 4px;
    min-width: 24px;
}}
QScrollBar::handle:horizontal:hover {{ background: #636366; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ── Status bar ─────────────────────────────────────────────────── */
QStatusBar {{
    background: #2c2c2e;
    color: #8e8e93;
    border-top: 1px solid #3a3a3c;
    font-size: 12px;
}}

/* ── Progress dialog ────────────────────────────────────────────── */
QProgressDialog {{
    background: #2c2c2e;
    color: #e5e5ea;
    border: 1px solid #3a3a3c;
    border-radius: 8px;
}}

/* ── Tool tips ──────────────────────────────────────────────────── */
QToolTip {{
    background: #2c2c2e;
    color: #e5e5ea;
    border: 1px solid #3a3a3c;
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 12px;
}}

/* ── Labels ─────────────────────────────────────────────────────── */
QLabel {{ color: #e5e5ea; background: transparent; }}

/* ── Message box ────────────────────────────────────────────────── */
QMessageBox {{ background: #2c2c2e; }}
QMessageBox QLabel {{ color: #e5e5ea; }}
"""

    # ── Light palette ─────────────────────────────────────────────────────────
    @staticmethod
    def get_light_theme() -> str:
        A  = "#007AFF"
        AH = "#228BFF"
        AP = "#0066CC"
        return f"""
QMainWindow, QDialog {{ background: #f2f2f7; }}
QWidget {{ background: #f2f2f7; color: #1c1c1e; font-size: 13px; }}

QMenuBar {{
    background: #f2f2f7; color: #1c1c1e;
    border-bottom: 1px solid #d1d1d6; padding: 2px 0;
}}
QMenuBar::item {{ padding: 4px 10px; border-radius: 4px; }}
QMenuBar::item:selected {{ background: #e5e5ea; }}
QMenu {{
    background: #ffffff; color: #1c1c1e;
    border: 1px solid #d1d1d6; border-radius: 8px; padding: 4px;
}}
QMenu::item {{ padding: 6px 24px 6px 14px; border-radius: 4px; }}
QMenu::item:selected {{ background: {A}; color: #fff; }}

QTreeWidget {{
    background: #f2f2f7; color: #1c1c1e;
    border: none; outline: none; font-size: 12.5px;
}}
QTreeWidget::item {{ height: 24px; border-radius: 4px; }}
QTreeWidget::item:hover {{ background: #e5e5ea; }}
QTreeWidget::item:selected {{ background: {A}22; color: {A}; }}

QTabWidget::pane {{ border: none; background: #ffffff; }}
QTabBar {{ background: #f2f2f7; }}
QTabBar::tab {{
    background: transparent; color: #6e6e73;
    padding: 8px 18px; border: none;
    border-bottom: 2px solid transparent; margin-right: 1px;
}}
QTabBar::tab:selected {{ color: #1c1c1e; border-bottom: 2px solid {A}; background: #ffffff; }}
QTabBar::tab:hover:!selected {{ color: #1c1c1e; }}

QPlainTextEdit, QTextEdit {{
    background: #ffffff; color: #1c1c1e; border: none;
    selection-background-color: #cce8ff; selection-color: #1c1c1e;
    font-family: 'Menlo', 'Monaco', 'Courier New', monospace; font-size: 13px;
}}
QLineEdit {{
    background: #ffffff; color: #1c1c1e;
    border: 1px solid #d1d1d6; border-radius: 6px; padding: 5px 8px;
}}
QLineEdit:focus {{ border-color: {A}; }}

QPushButton {{
    background: {A}; color: #fff; border: none;
    border-radius: 6px; padding: 5px 16px; font-weight: 600;
}}
QPushButton:hover {{ background: {AH}; }}
QPushButton:pressed {{ background: {AP}; }}
QPushButton:disabled {{ background: #e5e5ea; color: #aeaeb2; }}

QTableWidget, QTableView {{
    background: #ffffff; color: #1c1c1e;
    gridline-color: #e5e5ea; border: none;
    selection-background-color: {A}22; selection-color: #1c1c1e;
    alternate-background-color: #f9f9fb; outline: none;
}}
QHeaderView::section {{
    background: #f2f2f7; color: #6e6e73; border: none;
    border-right: 1px solid #d1d1d6; border-bottom: 1px solid #d1d1d6;
    padding: 4px 8px; font-size: 12px; font-weight: 600;
}}

QSplitter::handle {{ background: #d1d1d6; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}
QSplitter::handle:hover {{ background: {A}; }}

QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #c7c7cc; border-radius: 4px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: #aeaeb2; }}
QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: #c7c7cc; border-radius: 4px; min-width: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QLabel {{ color: #1c1c1e; background: transparent; }}
QToolTip {{
    background: #ffffff; color: #1c1c1e;
    border: 1px solid #d1d1d6; border-radius: 4px; padding: 4px 8px;
}}
"""

    # ── Filter containers ─────────────────────────────────────────────────────
    @staticmethod
    def get_filter_container_style_dark() -> str:
        return """
            QWidget { background: #2c2c2e; border: 1px solid #3a3a3c; border-radius: 6px; }
            QComboBox {
                padding: 3px 6px; border: 1px solid #3a3a3c; border-radius: 5px;
                background: #1c1c1e; color: #e5e5ea; font-size: 12px;
            }
            QComboBox:hover { border-color: #0A84FF; }
            QComboBox::drop-down { border: none; width: 16px; }
            QComboBox QAbstractItemView {
                background: #2c2c2e; color: #e5e5ea;
                selection-background-color: #0A84FF; border: 1px solid #3a3a3c;
            }
            QLineEdit {
                padding: 3px 6px; border: 1px solid #3a3a3c; border-radius: 5px;
                background: #1c1c1e; color: #e5e5ea; font-size: 12px;
            }
            QLineEdit:focus { border-color: #0A84FF; }
        """

    @staticmethod
    def get_filter_container_style_light() -> str:
        return """
            QWidget { background: #f2f2f7; border: 1px solid #d1d1d6; border-radius: 6px; }
            QComboBox {
                padding: 3px 6px; border: 1px solid #d1d1d6; border-radius: 5px;
                background: #ffffff; color: #1c1c1e; font-size: 12px;
            }
            QComboBox:hover { border-color: #007AFF; }
            QComboBox::drop-down { border: none; width: 16px; }
            QComboBox QAbstractItemView {
                background: #ffffff; color: #1c1c1e;
                selection-background-color: #cce8ff; border: 1px solid #d1d1d6;
            }
            QLineEdit {
                padding: 3px 6px; border: 1px solid #d1d1d6; border-radius: 5px;
                background: #ffffff; color: #1c1c1e; font-size: 12px;
            }
            QLineEdit:focus { border-color: #007AFF; }
        """
