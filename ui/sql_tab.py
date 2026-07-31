import re as _re
import pandas as pd
import sqlparse

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTextEdit,
    QPushButton,
    QLabel,
    QFileDialog,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QSplitter,
    QComboBox,
    QLineEdit,
    QPlainTextEdit,
    QAbstractItemView,
    QSizePolicy,
)
from PySide6.QtGui import QTextCursor, QColor, QTextCharFormat

from ui.code_editor import CodeEditor
from PySide6.QtGui import QTextCursor, QKeyEvent, QShortcut, QKeySequence

from ui.sql_highlighter import SqlHighlighter
from ui.sql_completer import SqlCompleter
from ui.editable_table import EditableTableWidget
from utils.df_export import export_dataframe
from ui.column_filter_dialog import ColumnFilterDialog
from ui.theme_manager import ThemeManager
from ui.snippet_manager import SnippetManager


# ─── SQL error hint engine ────────────────────────────────────────────────────

_ERROR_HINTS = [
    # Syntax errors
    (r"you have an error in your sql syntax",
     "Check for missing commas, unmatched parentheses, or typos near the marked position."),
    (r"syntax error at or near",
     "Check for missing commas, unmatched parentheses, or incorrect keyword usage."),
    # Unknown column / table
    (r"unknown column '(.+?)'",
     lambda m: f"Column '{m.group(1)}' doesn't exist — check the table schema or your alias."),
    (r"table '(.+?)' doesn't exist",
     lambda m: f"Table '{m.group(1)}' not found — verify the table name and active database."),
    (r"relation \"(.+?)\" does not exist",
     lambda m: f"Table '{m.group(1)}' not found — check spelling and current schema."),
    # Access denied
    (r"access denied",
     "Permission denied — your user lacks privileges for this operation."),
    (r"permission denied",
     "Permission denied — your user lacks privileges for this operation."),
    # Duplicate / constraint
    (r"duplicate entry '(.+?)' for key '(.+?)'",
     lambda m: f"Duplicate value '{m.group(1)}' on key '{m.group(2)}' — value must be unique."),
    (r"unique constraint",
     "A unique constraint was violated — the value already exists in that column."),
    (r"foreign key constraint",
     "Foreign key violation — the referenced row doesn't exist or a dependent row blocks deletion."),
    (r"cannot be null|null value in column",
     "A required (NOT NULL) column has no value — provide a value for all required fields."),
    # Connection
    (r"lost connection|server has gone away|broken pipe",
     "The database connection dropped — try running the query again."),
    (r"connection refused|could not connect",
     "Cannot reach the database server — check host, port, and firewall settings."),
    # Timeout / cancel
    (r"query was cancelled|canceling statement",
     "The query was cancelled by the user."),
    (r"lock wait timeout|deadlock",
     "A lock timeout or deadlock occurred — another process may be holding a lock on this table."),
    # Disk / space
    (r"disk full|no space left",
     "The server disk is full — contact your DBA."),
    # Data too long
    (r"data too long for column '(.+?)'",
     lambda m: f"The value for '{m.group(1)}' exceeds the column's maximum length."),
]

def _sql_error_hint(message: str, query: str = "") -> str:
    """Return a short actionable hint for a SQL error message, or empty string."""
    ml = message.lower()
    for pattern, hint in _ERROR_HINTS:
        m = _re.search(pattern, ml)
        if m:
            return hint(m) if callable(hint) else hint
    return ""


class SqlTab(QWidget):

    # Emitted when user confirms inline edits — parent executes the SQL
    commit_sql = Signal(list)  # list of SQL strings

    def __init__(self):
        super().__init__()

        self.current_df = None
        self.current_table_name = None
        self.filter_visible = False
        self.filter_conditions = []  # List of (column, operator, value) tuples
        self.original_df = None  # Store original unfiltered data

        # Pagination state for result table
        self._result_page_size = 500
        self._result_page      = 0
        self._result_view_df   = None   # current sorted/filtered full dataset
        self._result_sort_col  = -1
        self._result_sort_asc  = True

        # Extra features
        self._format_on_run = False   # auto-beautify SQL before executing
        self._prev_df       = None    # previous result set for diff
        self._diff_active   = False   # diff mode toggle
        self.pinned         = False   # favourite / pinned tab

        self.init_ui()
        
        # Theme will be set by update_theme() call in init_ui

    def init_ui(self):

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Create a splitter for resizable editor and results
        self.splitter = QSplitter(Qt.Vertical)
        self.splitter.setHandleWidth(3)

        # ==================================
        # SQL EDITOR
        # ==================================

        self.editor = CodeEditor()
        self.editor.setPlaceholderText("Write SQL here…")
        self.editor.setMinimumHeight(120)

        # Apply syntax highlighting to the document
        self.highlighter = SqlHighlighter(self.editor.document())
        
        # Apply autocomplete
        self.completer = SqlCompleter(self.editor)

        # Snippets
        self.snippet_manager = SnippetManager()
        self.completer.set_snippets(self.snippet_manager.get_all())

        # Install event filter for better control
        self.editor.installEventFilter(self)
        
        # ==================================
        # BUTTONS TOOLBAR (after editor)
        # ==================================
        
        # Toolbar: { } Snippets | Cancel | ▶ Run
        run_layout = QHBoxLayout()
        run_layout.setContentsMargins(6, 4, 6, 4)
        run_layout.setSpacing(6)

        snip_btn = QPushButton("{ } Snippets")
        snip_btn.setFixedHeight(28)
        snip_btn.setToolTip("Manage SQL snippets")
        snip_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #8e8e93;
                border: 1px solid #3a3a3c;
                border-radius: 5px;
                padding: 0 12px;
                font-size: 12px;
            }
            QPushButton:hover { color: #89d185; border-color: #89d185; }
        """)
        snip_btn.clicked.connect(self._open_snippet_editor)
        run_layout.addWidget(snip_btn)

        # ── Format on Run toggle ──
        self.fmt_run_btn = QPushButton("⌨ Auto-Format")
        self.fmt_run_btn.setFixedHeight(28)
        self.fmt_run_btn.setCheckable(True)
        self.fmt_run_btn.setToolTip("Auto-beautify SQL before every run (Ctrl+Shift+F)")
        self.fmt_run_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #8e8e93;
                border: 1px solid #3a3a3c;
                border-radius: 5px;
                padding: 0 10px;
                font-size: 12px;
            }
            QPushButton:checked { color: #30d158; border-color: #30d158; }
            QPushButton:hover   { color: #e5e5ea; border-color: #636366; }
        """)
        self.fmt_run_btn.toggled.connect(lambda v: setattr(self, '_format_on_run', v))
        run_layout.addWidget(self.fmt_run_btn)

        # ── Diff toggle ──
        self.diff_btn = QPushButton("≠ Diff")
        self.diff_btn.setFixedHeight(28)
        self.diff_btn.setCheckable(True)
        self.diff_btn.setToolTip("Highlight changes between last two query results")
        self.diff_btn.setEnabled(False)
        self.diff_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #8e8e93;
                border: 1px solid #3a3a3c;
                border-radius: 5px;
                padding: 0 10px;
                font-size: 12px;
            }
            QPushButton:checked { color: #ff9f0a; border-color: #ff9f0a; }
            QPushButton:hover   { color: #e5e5ea; border-color: #636366; }
            QPushButton:disabled { color: #48484a; border-color: #2c2c2e; }
        """)
        self.diff_btn.toggled.connect(self._on_diff_toggled)
        run_layout.addWidget(self.diff_btn)

        # ── Verify Query button ──
        self.verify_btn = QPushButton("⚖ Verify")
        self.verify_btn.setFixedHeight(28)
        self.verify_btn.setToolTip(
            "Compare this query against another query to verify result correctness"
        )
        self.verify_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #8e8e93;
                border: 1px solid #3a3a3c;
                border-radius: 5px;
                padding: 0 10px;
                font-size: 12px;
            }
            QPushButton:hover { color: #0A84FF; border-color: #0A84FF; }
        """)
        run_layout.addWidget(self.verify_btn)

        # ── Pin / favourite toggle ──
        self.pin_btn = QPushButton("★")
        self.pin_btn.setFixedSize(28, 28)
        self.pin_btn.setCheckable(True)
        self.pin_btn.setToolTip("Pin this tab — it will reopen on next launch")
        self.pin_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #636366;
                border: 1px solid #3a3a3c;
                border-radius: 5px;
                font-size: 14px;
            }
            QPushButton:checked { color: #ffd60a; border-color: #ffd60a; }
            QPushButton:hover   { color: #e5e5ea; border-color: #636366; }
        """)
        self.pin_btn.toggled.connect(self._on_pin_toggled)
        run_layout.addWidget(self.pin_btn)

        run_layout.addStretch()

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setFixedHeight(28)
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #8e8e93;
                border: 1px solid #3a3a3c;
                border-radius: 5px;
                padding: 0 14px;
                font-size: 12px;
            }
            QPushButton:hover { color: #e5e5ea; }
            QPushButton:disabled { color: #48484a; border-color: #2c2c2e; }
        """)
        run_layout.addWidget(self.cancel_btn)

        self.run_btn = QPushButton("▶  Run")
        self.run_btn.setFixedHeight(28)
        self.run_btn.setStyleSheet("""
            QPushButton {
                background: #0A84FF;
                color: #fff;
                border: none;
                border-radius: 5px;
                padding: 0 18px;
                font-weight: 600;
                font-size: 13px;
            }
            QPushButton:hover  { background: #228BFF; }
            QPushButton:pressed { background: #0066CC; }
        """)
        run_layout.addWidget(self.run_btn)

        # ── Find / Replace bar (Cmd+F = find, Cmd+H = find+replace) ─────────
        self._find_bar = QWidget()
        self._find_bar.hide()
        self._find_matches: list = []     # list of QTextCursor positions
        self._find_match_idx: int = -1

        fb_layout = QHBoxLayout(self._find_bar)
        fb_layout.setContentsMargins(6, 4, 6, 4)
        fb_layout.setSpacing(6)

        _inp_style = (
            "QLineEdit{background:#2c2c2e;color:#e5e5ea;border:1px solid #3a3a3c;"
            "border-radius:4px;padding:0 6px;font-size:12px;height:26px;}"
            "QLineEdit:focus{border-color:#0A84FF;}"
        )
        _toggle_style = (
            "QPushButton{padding:0 6px;height:22px;border:1px solid #3a3a3c;"
            "border-radius:3px;background:transparent;color:#8e8e93;font-size:11px;}"
            "QPushButton:hover{background:#3a3a3c;color:#e5e5ea;}"
            "QPushButton:checked{background:#0A84FF;color:#fff;border-color:#0A84FF;}"
        )
        _action_style = (
            "QPushButton{padding:0 10px;height:26px;border:1px solid #3a3a3c;"
            "border-radius:4px;background:transparent;color:#e5e5ea;font-size:12px;}"
            "QPushButton:hover{background:#3a3a3c;}"
        )

        # Find row
        self._find_input = QLineEdit()
        self._find_input.setPlaceholderText("Find…")
        self._find_input.setFixedHeight(26)
        self._find_input.setStyleSheet(_inp_style)
        self._find_input.setMinimumWidth(180)

        self._find_match_lbl = QLabel("")
        self._find_match_lbl.setStyleSheet("color:#8e8e93;font-size:11px;min-width:60px;")

        # Toggle buttons: Aa (case), \b (whole word), .* (regex)
        self._fb_case_btn = QPushButton("Aa")
        self._fb_case_btn.setCheckable(True)
        self._fb_case_btn.setToolTip("Match Case")
        self._fb_case_btn.setFixedSize(26, 22)
        self._fb_case_btn.setStyleSheet(_toggle_style)

        self._fb_word_btn = QPushButton("\\b")
        self._fb_word_btn.setCheckable(True)
        self._fb_word_btn.setToolTip("Whole Word")
        self._fb_word_btn.setFixedSize(26, 22)
        self._fb_word_btn.setStyleSheet(_toggle_style)

        self._fb_regex_btn = QPushButton(".*")
        self._fb_regex_btn.setCheckable(True)
        self._fb_regex_btn.setToolTip("Regular Expression")
        self._fb_regex_btn.setFixedSize(26, 22)
        self._fb_regex_btn.setStyleSheet(_toggle_style)

        _prev_btn = QPushButton("▲")
        _prev_btn.setFixedSize(22, 26)
        _prev_btn.setStyleSheet(_action_style)
        _prev_btn.setToolTip("Previous match (Shift+Enter)")

        _next_btn = QPushButton("▼")
        _next_btn.setFixedSize(22, 26)
        _next_btn.setStyleSheet(_action_style)
        _next_btn.setToolTip("Next match (Enter)")

        # Replace row (hidden unless Cmd+H)
        self._replace_row = QWidget()
        self._replace_row.hide()
        rr_layout = QHBoxLayout(self._replace_row)
        rr_layout.setContentsMargins(0, 0, 0, 0)
        rr_layout.setSpacing(6)
        self._replace_input = QLineEdit()
        self._replace_input.setPlaceholderText("Replace with…")
        self._replace_input.setFixedHeight(26)
        self._replace_input.setStyleSheet(_inp_style)
        self._replace_input.setMinimumWidth(180)
        _replace_btn     = QPushButton("Replace")
        _replace_btn.setStyleSheet(_action_style)
        _replace_all_btn = QPushButton("Replace All")
        _replace_all_btn.setStyleSheet(_action_style)
        rr_layout.addWidget(QLabel("Replace:"))
        rr_layout.addWidget(self._replace_input, 2)
        rr_layout.addWidget(_replace_btn)
        rr_layout.addWidget(_replace_all_btn)
        rr_layout.addStretch()

        _close_find_btn = QPushButton("✕")
        _close_find_btn.setFixedSize(22, 22)
        _close_find_btn.setStyleSheet(
            "QPushButton{border:none;background:transparent;color:#8e8e93;font-size:14px;}"
        )

        # Assemble find row
        fb_rows = QVBoxLayout()
        fb_rows.setContentsMargins(0, 0, 0, 0)
        fb_rows.setSpacing(3)
        find_row_w = QWidget()
        find_row_l = QHBoxLayout(find_row_w)
        find_row_l.setContentsMargins(0, 0, 0, 0)
        find_row_l.setSpacing(6)
        find_row_l.addWidget(QLabel("Find:"))
        find_row_l.addWidget(self._find_input, 2)
        find_row_l.addWidget(self._fb_case_btn)
        find_row_l.addWidget(self._fb_word_btn)
        find_row_l.addWidget(self._fb_regex_btn)
        find_row_l.addWidget(self._find_match_lbl)
        find_row_l.addWidget(_prev_btn)
        find_row_l.addWidget(_next_btn)
        fb_rows.addWidget(find_row_w)
        fb_rows.addWidget(self._replace_row)

        fb_layout.addLayout(fb_rows, 1)
        fb_layout.addWidget(_close_find_btn)

        # Wire signals
        self._find_input.textChanged.connect(self._find_live_update)
        self._find_input.returnPressed.connect(self._find_next)
        self._find_input.installEventFilter(self)   # catch Shift+Enter for prev
        self._fb_case_btn.toggled.connect(self._find_live_update)
        self._fb_word_btn.toggled.connect(self._find_live_update)
        self._fb_regex_btn.toggled.connect(self._find_live_update)
        _next_btn.clicked.connect(self._find_next)
        _prev_btn.clicked.connect(self._find_prev)
        _close_find_btn.clicked.connect(self._hide_find_bar)
        _replace_btn.clicked.connect(self._replace_current)
        _replace_all_btn.clicked.connect(self._replace_all)

        # ── Top widget assembly ───────────────────────────────────────────────
        
        self.filter_container = QWidget()
        self.filter_container.hide()
        filter_main_layout = QVBoxLayout(self.filter_container)
        filter_main_layout.setContentsMargins(5, 5, 5, 5)
        filter_main_layout.setSpacing(4)
        
        # Filter rows container
        self.filter_rows_layout = QVBoxLayout()
        self.filter_rows_layout.setSpacing(3)
        filter_main_layout.addLayout(self.filter_rows_layout)
        
        # Action buttons
        action_layout = QHBoxLayout()
        action_layout.setSpacing(6)
        
        add_filter_btn = QPushButton("+ Add Filter")
        add_filter_btn.clicked.connect(self.add_filter_row)
        add_filter_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #0078d4;
                border: 1px solid #0078d4;
                padding: 4px 10px;
                border-radius: 3px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #0078d4;
                color: #ffffff;
            }
        """)
        action_layout.addWidget(add_filter_btn)
        
        action_layout.addStretch()
        
        apply_all_btn = QPushButton("Apply All ⌘⏎")
        apply_all_btn.clicked.connect(self.apply_all_filters)
        apply_all_btn.setStyleSheet("""
            QPushButton {
                background-color: #0078d4;
                color: #ffffff;
                padding: 4px 16px;
                border-radius: 3px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #1890ff;
            }
        """)
        action_layout.addWidget(apply_all_btn)
        
        clear_all_btn = QPushButton("Clear All")
        clear_all_btn.clicked.connect(self.clear_all_filters)
        clear_all_btn.setStyleSheet("""
            QPushButton {
                background-color: #d13438;
                color: #ffffff;
                padding: 4px 16px;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #e04348;
            }
        """)
        action_layout.addWidget(clear_all_btn)
        
        filter_main_layout.addLayout(action_layout)
        
        # Style will be applied via update_theme method
        self.update_theme(is_dark=True)  # Apply default theme immediately
        
        # Add initial filter row
        self.add_filter_row()

        # ==================================
        # STATUS (Hidden by default)
        # ==================================

        # Status / error area — QPlainTextEdit so errors are selectable & copyable
        self.status_label = QPlainTextEdit()
        self.status_label.setReadOnly(True)
        self.status_label.setFixedHeight(0)   # hidden until needed
        self.status_label.setStyleSheet("""
            QPlainTextEdit {
                color: #888888;
                padding: 3px 5px;
                font-size: 12px;
                background: transparent;
                border: none;
            }
        """)
        self.status_label.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.status_label.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # ==================================
        # RESULT GRID
        # ==================================

        self.result_table = EditableTableWidget()

        # remove serial number column
        self.result_table.verticalHeader().setVisible(False)
        self.result_table.setAlternatingRowColors(True)

        # Client-side sort is managed by SqlTab (pagination-aware);
        # disable EditableTableWidget's own handler to prevent double-fire.
        self.result_table.setSortingEnabled(False)
        try:
            self.result_table.horizontalHeader().sectionClicked.disconnect(
                self.result_table.on_header_clicked
            )
        except Exception:
            pass
        self.result_table.horizontalHeader().sectionClicked.connect(
            self._on_result_sort
        )

        # Connect filter signal
        self.result_table.filter_changed.connect(self.on_filter_changed)

        # Wire filter-chip: clicking a cell value pre-fills the filter row
        self.result_table.filter_by_value.connect(self._on_result_filter_chip)
        # Wire structure viewer signal (routed up to parent ConnectionPanel)
        self.result_table.show_structure.connect(self._on_result_show_structure)

        # Hide results table by default - show empty Excel grid instead
        self._show_excel_grid()

        # ── Pagination bar ─────────────────────────────────────────────
        self._pagination_bar = QWidget()
        self._pagination_bar.hide()
        _pag_layout = QHBoxLayout(self._pagination_bar)
        _pag_layout.setContentsMargins(6, 2, 6, 2)
        _pag_layout.setSpacing(6)

        self._prev_page_btn = QPushButton('◀ Prev')
        self._prev_page_btn.setFixedWidth(70)
        self._prev_page_btn.clicked.connect(self._prev_result_page)
        _pag_layout.addWidget(self._prev_page_btn)

        self._page_label = QLabel('')
        self._page_label.setAlignment(Qt.AlignCenter)
        _pag_layout.addWidget(self._page_label, 1)

        self._next_page_btn = QPushButton('Next ▶')
        self._next_page_btn.setFixedWidth(70)
        self._next_page_btn.clicked.connect(self._next_result_page)
        _pag_layout.addWidget(self._next_page_btn)

        # ==================================
        # ADD TO LAYOUT WITH SPLITTER
        # ==================================
        
        # Top widget: editor + run button + find/replace bar
        top_widget = QWidget()
        top_layout = QVBoxLayout()
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(2)
        top_layout.addWidget(self.editor)
        top_layout.addLayout(run_layout)
        top_layout.addWidget(self._find_bar)
        top_widget.setLayout(top_layout)
        
        # Bottom widget: filter + status + results
        bottom_widget = QWidget()
        bottom_layout = QVBoxLayout()
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(2)
        bottom_layout.addWidget(self.filter_container)
        bottom_layout.addWidget(self.status_label)
        bottom_layout.addWidget(self.result_table)
        bottom_layout.addWidget(self._pagination_bar)
        bottom_widget.setLayout(bottom_layout)
        
        # Add widgets to splitter
        self.splitter.addWidget(top_widget)
        self.splitter.addWidget(bottom_widget)
        self.splitter.setSizes([300, 500])  # Initial sizes
        
        layout.addWidget(self.splitter)

        self.setLayout(layout)

        # ==================================
        # KEYBOARD SHORTCUTS
        # ==================================
        
        # Add keyboard shortcut for run
        from PySide6.QtGui import QShortcut, QKeySequence
        self.run_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        self.run_shortcut.activated.connect(self.run_btn.click)
        
        # Add keyboard shortcuts for SQL formatting
        self.beautify_shortcut = QShortcut(QKeySequence("Ctrl+I"), self)
        self.beautify_shortcut.activated.connect(self.format_sql)
        
        self.minify_shortcut = QShortcut(QKeySequence("Ctrl+Shift+I"), self)
        self.minify_shortcut.activated.connect(self.minify_sql)

        # Ctrl+H — find & replace (shows replace row)
        self.find_shortcut = QShortcut(QKeySequence("Ctrl+H"), self)
        self.find_shortcut.activated.connect(self._toggle_find_replace)

        # Ctrl+F — find only (hides replace row)
        self.find_only_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        self.find_only_shortcut.activated.connect(self._toggle_find_bar)

        # Ctrl+Shift+F — toggle format-on-run
        self.fmt_shortcut = QShortcut(QKeySequence("Ctrl+Shift+F"), self)
        self.fmt_shortcut.activated.connect(lambda: self.fmt_run_btn.toggle())

        # Add keyboard shortcut for save (Cmd+S)
        self.save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        self.save_shortcut.activated.connect(self.save_changes)

        # Add Esc shortcut to hide filter
        self.esc_shortcut = QShortcut(QKeySequence("Esc"), self)
        self.esc_shortcut.activated.connect(self.hide_filter)

        # ==================================
        # EVENTS
        # ==================================
        
        # Dummy buttons for compatibility (hidden)
        self.commit_btn = QPushButton()
        self.commit_btn.hide()
        self.revert_btn = QPushButton()
        self.revert_btn.hide()
        
        # Apply theme after all widgets are created
        # Note: Will be updated when theme changes via apply_theme in main window
    
    def get_main_window(self):
        """Get the main window by traversing up the parent hierarchy"""
        widget = self
        while widget is not None:
            if hasattr(widget, 'execute_query'):
                return widget
            widget = widget.parent()
        return None
    
    def save_changes(self):
        """Save changes with Cmd+S shortcut - commits edits to database"""
        if hasattr(self.result_table, 'has_changes') and self.result_table.has_changes():
            self.commit_changes()
        else:
            # Silent when no changes
            pass
    
    # ======================================
    # AUTOCOMPLETE
    # ======================================
    
    def set_schema(self, tables, columns_dict):
        """Update autocomplete with schema information"""
        self.completer.set_schema(tables, columns_dict)

    def _open_snippet_editor(self):
        """Open the snippet management dialog."""
        from ui.snippet_editor_dialog import SnippetEditorDialog
        dlg = SnippetEditorDialog(self.snippet_manager, parent=self.window())
        dlg.snippets_changed.connect(self._reload_snippets)
        dlg.exec()

    def _reload_snippets(self):
        """Called when snippets are changed in the editor dialog."""
        self.completer.set_snippets(self.snippet_manager.get_all())

    def eventFilter(self, obj, event):
        """Route key events: popup navigation first, then auto-trigger."""
        # Handle key events in the find input
        if obj is self._find_input and event.type() == event.Type.KeyPress:
            key = event.key()
            mod = event.modifiers()
            if key == Qt.Key_Return and (mod & Qt.ShiftModifier):
                self._find_prev()
                return True
            if key == Qt.Key_Escape:
                self._hide_find_bar()
                return True
            return False   # let QLineEdit handle other keys

        if obj != self.editor or event.type() != event.Type.KeyPress:
            return super().eventFilter(obj, event)

        key = event.key()
        mod = event.modifiers()

        # 1. Esc: hide popup first; if popup was not visible, fall through to
        #    the QShortcut (hide_filter). Returning True stops the Esc reaching
        #    the shortcut, so only do that when the popup actually was visible.
        if key == Qt.Key_Escape:
            if self.completer.popup_visible:
                self.completer.hide_popup()
                self._completer_suppressed = True   # don't re-show on next backspace
                return True          # consumed — don't also close the filter bar
            # popup not visible → let the existing Esc shortcut hide the filter
            return super().eventFilter(obj, event)

        # 2. Let popup consume navigation / accept keys
        if self.completer.handle_key(event):
            return True

        # 3. Ctrl+Space: force-show suggestions without inserting a space
        if key == Qt.Key_Space and mod == Qt.ControlModifier:
            self._completer_suppressed = False
            self.completer.update(force=True)
            return True

        # Ctrl+/ — toggle line comment (-- ) on selected lines or current line
        if key == Qt.Key_Slash and mod == Qt.ControlModifier:
            self._toggle_line_comment()
            return True

        # Ctrl+D — select next occurrence of current word / selection
        if key == Qt.Key_D and mod == Qt.ControlModifier:
            self._select_next_occurrence()
            return True

        # 3. Cursor-movement keys: hide popup, pass to editor normally
        if key in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Home, Qt.Key_End,
                   Qt.Key_PageUp, Qt.Key_PageDown):
            self.completer.hide_popup()
            return super().eventFilter(obj, event)

        # 4. Cmd+Backspace (⌘⌫ on Mac) — delete entire current line
        if key == Qt.Key_Backspace and mod == Qt.ControlModifier:
            self._delete_current_line()
            return True

        # Auto-close quotes: ' and "
        if key in (Qt.Key_Apostrophe, Qt.Key_QuoteDbl):
            self._auto_close_quote(key)
            return True

        # 5. Backspace / Delete: process first, then update
        if key in (Qt.Key_Backspace, Qt.Key_Delete):
            result = super().eventFilter(obj, event)
            if not getattr(self, '_completer_suppressed', False):
                self.completer.update()
            return result

        # 5. Printable word characters: process first, then update
        text = event.text()
        if text and (text.isalnum() or text in ('_', '.')):
            self._completer_suppressed = False   # typing a letter re-enables suggestions
            result = super().eventFilter(obj, event)
            # Don't show suggestions when cursor is inside a string literal
            if not self._cursor_inside_string():
                self.completer.update()
            else:
                self.completer.hide_popup()
            return result

        # 6. Any other key (Enter for new line, space, punctuation…): hide popup
        if key not in (Qt.Key_Shift, Qt.Key_Control, Qt.Key_Alt, Qt.Key_Meta,
                       Qt.Key_CapsLock, Qt.Key_NumLock):
            self.completer.hide_popup()

        return super().eventFilter(obj, event)

    def _auto_close_quote(self, key):
        """Insert a matching closing quote and place the cursor between them.
        If the cursor is already just before the closing quote, skip over it."""
        char = "'" if key == Qt.Key_Apostrophe else '"'
        cursor = self.editor.textCursor()
        doc = self.editor.toPlainText()
        pos = cursor.position()

        # If selection exists, wrap the selection with quotes
        if cursor.hasSelection():
            sel = cursor.selectedText()
            cursor.insertText(char + sel + char)
            return

        # If next character is already the same quote, just move past it
        if pos < len(doc) and doc[pos] == char:
            cursor.movePosition(QTextCursor.NextCharacter)
            self.editor.setTextCursor(cursor)
            return

        # Otherwise insert both and position cursor between them
        cursor.insertText(char + char)
        cursor.movePosition(QTextCursor.PreviousCharacter)
        self.editor.setTextCursor(cursor)
        # A quote opens a string — suppress suggestions immediately
        self._completer_suppressed = True
        self.completer.hide_popup()

    def _cursor_inside_string(self) -> bool:
        """Return True if the editor cursor is currently inside a quoted string literal."""
        text = self.editor.toPlainText()
        pos = self.editor.textCursor().position()
        in_single = False
        in_double = False
        i = 0
        while i < pos:
            ch = text[i]
            if ch == "'" and not in_double:
                in_single = not in_single
            elif ch == '"' and not in_single:
                in_double = not in_double
            elif ch == '\\' and (in_single or in_double):
                i += 1  # skip escaped character
            i += 1
        return in_single or in_double

    def _toggle_line_comment(self):
        """Toggle -- comment on each selected line (or current line)."""
        cursor = self.editor.textCursor()
        cursor.beginEditBlock()
        if cursor.hasSelection():
            start = cursor.selectionStart()
            end   = cursor.selectionEnd()
        else:
            start = end = cursor.position()

        # Work on a copy to get block positions
        c = QTextCursor(self.editor.document())
        c.setPosition(start)
        c.movePosition(QTextCursor.StartOfBlock)
        first_block = c.blockNumber()

        c.setPosition(end)
        if c.atBlockStart() and end > start:
            c.movePosition(QTextCursor.PreviousBlock)
        last_block = c.blockNumber()

        # Determine if ALL lines are commented (to decide add vs remove)
        doc = self.editor.document()
        all_commented = all(
            doc.findBlockByNumber(b).text().lstrip().startswith('--')
            for b in range(first_block, last_block + 1)
        )

        for b in range(first_block, last_block + 1):
            block = doc.findBlockByNumber(b)
            bc = QTextCursor(block)
            line = block.text()
            if all_commented:
                # Remove first occurrence of -- (with optional space)
                stripped = line.lstrip()
                indent = len(line) - len(stripped)
                if stripped.startswith('-- '):
                    bc.movePosition(QTextCursor.StartOfBlock)
                    bc.movePosition(QTextCursor.Right, QTextCursor.MoveAnchor, indent)
                    bc.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor, 3)
                    bc.removeSelectedText()
                elif stripped.startswith('--'):
                    bc.movePosition(QTextCursor.StartOfBlock)
                    bc.movePosition(QTextCursor.Right, QTextCursor.MoveAnchor, indent)
                    bc.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor, 2)
                    bc.removeSelectedText()
            else:
                # Add -- at the start of the line (after indent)
                indent = len(line) - len(line.lstrip())
                bc.movePosition(QTextCursor.StartOfBlock)
                bc.movePosition(QTextCursor.Right, QTextCursor.MoveAnchor, indent)
                bc.insertText('-- ')

        cursor.endEditBlock()

    def _select_next_occurrence(self):
        """Cmd+D: expand selection to current word, then find and select next match."""
        cursor = self.editor.textCursor()
        doc    = self.editor.document()

        # If no selection, select the word under the cursor first
        if not cursor.hasSelection():
            cursor.select(QTextCursor.WordUnderCursor)
            self.editor.setTextCursor(cursor)
            return

        word = cursor.selectedText()
        if not word:
            return

        # Search forward from current selection end
        search_start = cursor.selectionEnd()
        full_text = self.editor.toPlainText()

        idx = full_text.find(word, search_start)
        if idx == -1:
            # Wrap around from the beginning
            idx = full_text.find(word, 0)
        if idx == -1 or idx == cursor.selectionStart():
            return  # only one occurrence

        new_cursor = QTextCursor(doc)
        new_cursor.setPosition(idx)
        new_cursor.setPosition(idx + len(word), QTextCursor.KeepAnchor)
        self.editor.setTextCursor(new_cursor)
        self.editor.ensureCursorVisible()
    
    def _delete_current_line(self):
        """Delete the entire line the cursor is on (Cmd+Backspace / ⌘⌫)."""
        cursor = self.editor.textCursor()
        cursor.beginEditBlock()
        cursor.movePosition(QTextCursor.StartOfBlock)
        cursor.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
        cursor.removeSelectedText()
        # Also remove the newline unless we're on the last line
        if not cursor.atEnd():
            cursor.deleteChar()
        cursor.endEditBlock()
        self.editor.setTextCursor(cursor)
        self.completer.hide_popup()

    # ======================================
    # DATA EDITING
    # ======================================
    
    def commit_changes(self):
        """Commit changes to database"""
        if not self.result_table.has_changes():
            QMessageBox.information(self, "Info", "No changes to commit")
            return
        
        changes = self.result_table.get_changes()
        
        if not changes or not self.result_table.table_name:
            QMessageBox.warning(self, "Warning", "Cannot generate SQL for changes")
            return
        
        # Show SQL preview
        all_sql = []
        all_sql.extend(changes['updates'])
        all_sql.extend(changes['inserts'])
        all_sql.extend(changes['deletes'])
        
        if not all_sql:
            QMessageBox.information(self, "Info", "No changes to commit")
            return
        
        sql_preview = "\n".join(all_sql)
        
        reply = QMessageBox.question(
            self,
            "Commit Changes",
            f"Execute the following SQL?\n\n{sql_preview[:500]}...",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            self.commit_sql.emit(all_sql)
            self.commit_btn.setEnabled(False)
    
    def revert_changes(self):
        """Revert all changes"""
        self.result_table.revert_changes()
        self.commit_btn.setEnabled(False)
        self.revert_btn.setEnabled(False)
    
    def enable_commit_buttons(self):
        """Enable commit/revert buttons when changes are made"""
        if self.result_table.has_changes():
            self.commit_btn.setEnabled(True)
            self.revert_btn.setEnabled(True)

    # ======================================
    # FORMAT SQL
    # ======================================

    def format_sql(self):
        query = self.editor.toPlainText()
        if not query.strip():
            return
        try:
            formatted = sqlparse.format(query, reindent=True, keyword_case="upper")
            self.editor.setPlainText(formatted)
        except Exception:
            # Query too large for sqlparse (>10 000 tokens) — skip silently
            pass

    def minify_sql(self):
        """Minify/compress SQL query."""
        query = self.editor.toPlainText()
        if not query.strip():
            return
        import re
        try:
            minified = sqlparse.format(
                query, reindent=False, keyword_case="upper", strip_comments=True)
            minified = re.sub(r'\s+', ' ', minified).strip()
            self.editor.setPlainText(minified)
        except Exception:
            pass    
    def show_filter_dialog(self):
        """Show filter dialog for current results"""
        if self.current_df is None or self.current_df.empty:
            QMessageBox.information(self, "No Data", "Run a query first to filter results")
            return
        
        # Get column names
        columns = [str(col) for col in self.current_df.columns]
        
        # Show filter dialog
        dialog = ColumnFilterDialog(columns, self)
        
        if dialog.exec():
            filters = dialog.get_filters()
            
            # Apply filters to the table
            for column, value in filters.items():
                col_index = columns.index(column) if column in columns else -1
                if col_index >= 0:
                    self.result_table.apply_column_filter(col_index, value)
    # ======================================
    # LOAD DATAFRAME
    # ======================================

    def load_dataframe(
        self,
        dataframe: pd.DataFrame,
        table_name=None
    ):
        # ── Teardown any active multi-result tab bar ──────────────────────────
        if hasattr(self, '_multi_result_bar') and self._multi_result_bar is not None:
            self._multi_result_bar.hide()

        # Keep previous result for diff before overwriting
        if self.current_df is not None:
            self._prev_df = self.current_df.copy()
            self.diff_btn.setEnabled(True)
        self.current_df = dataframe
        self.original_df = dataframe.copy()  # Store original for filtering
        self.current_table_name = table_name

        # Reset pagination + sort state for new query results
        self._result_view_df  = dataframe.copy()
        self._result_page     = 0
        self._result_sort_col = -1
        self._result_sort_asc = True
        self.result_table.horizontalHeader().setSortIndicator(-1, Qt.AscendingOrder)

        # Update filter column options
        if len(dataframe.columns) > 0:
            self._update_filter_columns(list(dataframe.columns))

        # Show results + pagination bar
        self.result_table.show()
        self._pagination_bar.show()

        self._refresh_result_view()

        # Update button states
        self.commit_btn.setEnabled(False)
        self.revert_btn.setEnabled(False)

    def load_multi_results(self, results: list, elapsed: float):
        """Show multiple SELECT results as a horizontal tab bar above the grid."""
        if not results:
            return

        # Build or rebuild the multi-result bar
        if not hasattr(self, '_multi_result_bar') or self._multi_result_bar is None:
            from PySide6.QtWidgets import QTabBar
            bar = QTabBar()
            bar.setExpanding(False)
            bar.setStyleSheet(
                "QTabBar::tab { padding: 3px 12px; font-size: 11px; }"
                "QTabBar::tab:selected { font-weight: bold; }"
            )
            # Insert above result_table in bottom layout
            parent_layout = self.result_table.parent().layout()
            if parent_layout:
                idx = parent_layout.indexOf(self.result_table)
                parent_layout.insertWidget(idx, bar)
            self._multi_result_bar = bar
            self._multi_results: list = []
            bar.currentChanged.connect(self._on_multi_result_tab)
        else:
            bar = self._multi_result_bar
            bar.blockSignals(True)
            while bar.count():
                bar.removeTab(0)
            bar.blockSignals(False)

        self._multi_results = results
        bar.blockSignals(True)
        for i, (lbl, df) in enumerate(results):
            short = lbl[:30] + ("…" if len(lbl) > 30 else "")
            bar.addTab(f"Result {i+1}: {short}")
        bar.blockSignals(False)
        bar.show()

        # Show first result
        bar.setCurrentIndex(0)
        self._on_multi_result_tab(0)
        self.update_status(sum(len(df) for _, df in results), elapsed)

    def _on_multi_result_tab(self, index: int):
        if not hasattr(self, '_multi_results') or index >= len(self._multi_results):
            return
        _, df = self._multi_results[index]
        if isinstance(df, Exception):
            self.show_error(str(df))
            return
        self.current_df = df
        self.original_df = df.copy()
        self._result_view_df = df.copy()
        self._result_page = 0
        if len(df.columns) > 0:
            self._update_filter_columns(list(df.columns))
        self.result_table.show()
        self._refresh_result_view()

    
    def add_filter_headers(self):
        """Add filter input boxes to column headers"""
        from ui.filter_header import FilterHeaderWidget
        
        for col in range(self.result_table.columnCount()):
            col_name = self.result_table.horizontalHeaderItem(col).text()
            filter_widget = FilterHeaderWidget(col, col_name)
            filter_widget.filter_changed.connect(self.result_table.apply_column_filter)
            # Note: QTableWidget doesn't support setCellWidget for headers directly
            # Instead, we'll use the inline filtering in the table itself
    

    # ── Pagination helpers for SQL result table ───────────────────────────

    def _refresh_result_view(self):
        """Display the current page of _result_view_df in result_table.

        Only 500 rows are rendered at a time so the main thread is never
        blocked building a 14k-row QTableWidget.
        """
        df = self._result_view_df
        if df is None or df.empty:
            self.result_table.load_data(df, self.current_table_name)
            self._pagination_bar.hide()
            return

        page_size  = self._result_page_size
        total_rows = len(df)
        total_pages = max(1, (total_rows + page_size - 1) // page_size)
        self._result_page = max(0, min(self._result_page, total_pages - 1))

        start = self._result_page * page_size
        end   = min(start + page_size, total_rows)
        page_df = df.iloc[start:end].reset_index(drop=True)

        self.result_table.load_data(page_df, self.current_table_name)
        # Restore interactive resize mode (overrides the Stretch set by the placeholder grid)
        self.result_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.result_table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)

        # Re-apply sort indicator so it survives load_data reset
        if self._result_sort_col >= 0:
            order = Qt.AscendingOrder if self._result_sort_asc else Qt.DescendingOrder
            self.result_table.horizontalHeader().setSortIndicator(
                self._result_sort_col, order
            )

        # Update pagination controls
        self._page_label.setText(
            f"Page {self._result_page + 1} of {total_pages}  "
            f"({start + 1}–{end} of {total_rows} rows)"
        )
        self._prev_page_btn.setEnabled(self._result_page > 0)
        self._next_page_btn.setEnabled(self._result_page < total_pages - 1)
        self._pagination_bar.setVisible(total_pages > 1)

    def _on_result_sort(self, col: int):
        """Sort the full result dataset by column *col* then refresh page 0."""
        if self._result_view_df is None or self._result_view_df.empty:
            return
        # Block sort when there are unsaved cell edits
        if self.result_table.has_changes():
            return

        if self._result_sort_col == col:
            self._result_sort_asc = not self._result_sort_asc
        else:
            self._result_sort_col = col
            self._result_sort_asc = True

        col_name = self._result_view_df.columns[col]
        self._result_view_df = self._result_view_df.sort_values(
            col_name, ascending=self._result_sort_asc, na_position='last'
        ).reset_index(drop=True)

        self._result_page = 0
        self._refresh_result_view()

    def _prev_result_page(self):
        if self._result_page > 0:
            self._result_page -= 1
            self._refresh_result_view()

    def _next_result_page(self):
        if self._result_view_df is None:
            return
        total_pages = max(1, (len(self._result_view_df) + self._result_page_size - 1) // self._result_page_size)
        if self._result_page < total_pages - 1:
            self._result_page += 1
            self._refresh_result_view()

    def on_filter_changed(self):
        """Update status when filters change"""
        filter_status = self.result_table.get_filter_status()
        if filter_status:
            row_count = self.result_table.rowCount()
            self.status_label.setPlainText(f"Filtered: {row_count} rows | {filter_status}")
            self.status_label.setFixedHeight(28)
        else:
            row_count = self.result_table.rowCount()
            self.status_label.setPlainText(f"Rows: {row_count}")
            self.status_label.setFixedHeight(28)

    def _on_result_filter_chip(self, col_name: str, operator: str, value: str):
        """Pre-populate the filter bar with the clicked cell value and show it."""
        if not self.filter_visible:
            self.toggle_filter()
        if self.filter_rows_layout.count() > 0:
            row_widget = self.filter_rows_layout.itemAt(0).widget()
            if row_widget:
                col_combo = row_widget.findChild(QComboBox, "column_combo")
                val_input = row_widget.findChild(QLineEdit, "value_input")
                op_combo  = row_widget.findChild(QComboBox, "operator_combo")
                if col_combo and col_name in [col_combo.itemText(i)
                                              for i in range(col_combo.count())]:
                    col_combo.setCurrentText(col_name)
                if op_combo:
                    op_combo.setCurrentText(operator)
                if val_input:
                    val_input.setText(value)
                    val_input.setFocus()
        self.apply_all_filters()

    def _on_result_show_structure(self, table_name: str):
        """Route show-structure request to ConnectionPanel parent."""
        panel = self.parent()
        while panel is not None:
            if hasattr(panel, '_show_table_structure'):
                panel._show_table_structure(table_name)
                return
            panel = panel.parent()



    def _show_excel_grid(self, cols: int = 8, rows: int = 20):
        """Show an empty Excel-like placeholder grid."""
        self.result_table.setColumnCount(cols)
        self.result_table.setRowCount(rows)
        self.result_table.setHorizontalHeaderLabels(
            [chr(65 + i) for i in range(cols)]
        )
        self.result_table.clearContents()
        # Stretch all columns equally to fill the full available width
        self.result_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        # Make sure the grid is visible and not clickable to cause confusion
        self.result_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.result_table.show()
        # Clear status
        self.status_label.setFixedHeight(0)
        self.status_label.setPlainText("")

    def _set_status(self, text: str, style: str = "", height: int = 28):
        """Helper: show text in the status_label (QPlainTextEdit)."""
        lines = text.count('\n') + 1
        h = min(max(height, lines * 20 + 16), 150)
        self.status_label.setFixedHeight(h)
        self.status_label.setPlainText(text)
        if style:
            self.status_label.setStyleSheet(style)
        self.status_label.show()

    def update_status(self, rows, execution_time):
        self._set_status(
            f"{rows} rows | {execution_time:.3f}s",
            """
            QPlainTextEdit {
                color: #0078d4;
                padding: 5px;
                font-size: 12px;
                font-weight: 500;
                background: transparent;
                border: none;
            }
            """,
        )

    def show_error(self, message: str, query: str = "", elapsed: float = 0.0):
        """Display a SQL error inline with actionable hints — always selectable."""
        self.result_table.clearContents()
        self.result_table.setRowCount(0)
        self.result_table.setColumnCount(0)
        self._show_excel_grid()   # keep the empty grid visible

        hint = _sql_error_hint(message, query)
        time_str = f"  ({elapsed:.2f}s)" if elapsed > 0 else ""
        display = f"❌  {message}{time_str}"
        if hint:
            display += f"\n💡  {hint}"

        self._set_status(
            display,
            """
            QPlainTextEdit {
                color: #f48771;
                padding: 8px 10px;
                font-size: 12px;
                font-weight: 500;
                background-color: #3a1a1a;
                border-radius: 4px;
                border-left: 3px solid #f48771;
                selection-background-color: #6a3a3a;
                border: none;
            }
            """,
        )

    def show_cancelled(self):
        """Show a neutral 'query cancelled' status."""
        self.result_table.clearContents()
        self.result_table.setRowCount(0)
        self.result_table.setColumnCount(0)
        self._show_excel_grid()
        self._set_status(
            "⊘  Query cancelled",
            """
            QPlainTextEdit {
                color: #8e8e93;
                padding: 6px 10px;
                font-size: 12px;
                background-color: #2c2c2e;
                border-radius: 4px;
                border: none;
            }
            """,
        )

    # ======================================
    # EXPORT DATA
    # ======================================

    def export_data(self):
        """Export data in multiple formats: CSV, JSON, Excel, SQL"""
        export_dataframe(
            self, self.current_df, "results.csv",
            getattr(self, 'current_table_name', 'table'),
        )

    # ======================================
    # IMPORT DATA
    # ======================================

    def import_data(self):
        """Import data from CSV, JSON, or Excel files"""
        # Show file dialog for import
        file_name, _ = QFileDialog.getOpenFileName(
            self,
            "Import Data",
            "",
            "All Supported (*.csv *.json *.xlsx);;CSV Files (*.csv);;JSON Files (*.json);;Excel Files (*.xlsx)"
        )

        if not file_name:
            return

        try:
            # Determine format from file extension
            if file_name.endswith('.csv'):
                df = pd.read_csv(file_name)
                format_name = "CSV"
            elif file_name.endswith('.json'):
                df = pd.read_json(file_name)
                format_name = "JSON"
            elif file_name.endswith('.xlsx'):
                df = pd.read_excel(file_name, engine='openpyxl')
                format_name = "Excel"
            else:
                QMessageBox.warning(
                    self,
                    "Unsupported Format",
                    "Please select a CSV, JSON, or Excel file"
                )
                return

            # Load the imported data into the table
            self.load_dataframe(df)
            self.status_label.setPlainText(f"Imported {len(df)} rows from {format_name} file")
            self.status_label.setFixedHeight(28)

            QMessageBox.information(
                self,
                "Success",
                f"Successfully imported {len(df)} rows from {format_name} file"
            )
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to import: {str(e)}"
            )

    # ======================================
    # HELPERS
    # ======================================

    def get_query(self):
        """Return selected text, query at cursor, or all text."""
        cursor = self.editor.textCursor()
        if cursor.hasSelection():
            # QPlainTextEdit uses \u2029 for paragraph separators
            return cursor.selectedText().replace('\u2029', '\n')
        query_at_cursor = self.get_query_at_cursor()
        if query_at_cursor:
            return query_at_cursor
        return self.editor.toPlainText()

    def set_query(self, text: str):
        """Set the editor content."""
        self.editor.setPlainText(text)

    def update_theme(self, is_dark=True):
        """Update editor palette and filter container theme."""
        if is_dark:
            self.editor.apply_dark_palette()
            style = ThemeManager.get_filter_container_style_dark()
        else:
            self.editor.apply_light_palette()
            style = ThemeManager.get_filter_container_style_light()
        self.filter_container.setStyleSheet(style)
        if hasattr(self, 'result_table'):
            self.result_table.update_theme(is_dark)
    
    # ======================================
    # FILTER METHODS
    # ======================================
    
    def toggle_filter(self):
        """Toggle filter visibility"""
        self.filter_visible = not self.filter_visible
        if self.filter_visible:
            self.filter_container.show()
            # Focus on first value input
            if self.filter_rows_layout.count() > 0:
                row_widget = self.filter_rows_layout.itemAt(0).widget()
                if row_widget:
                    value_input = row_widget.findChild(QLineEdit, "value_input")
                    if value_input:
                        value_input.setFocus()
        else:
            self.filter_container.hide()
    
    def hide_filter(self):
        """Hide filter on Esc"""
        if self.filter_visible:
            self.filter_visible = False
            self.filter_container.hide()
    
    def add_filter_row(self):
        """Add a new filter row"""
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(4)
        
        # Column selector
        column_combo = QComboBox()
        column_combo.setObjectName("column_combo")
        column_combo.setMinimumWidth(120)
        if self.current_df is not None and len(self.current_df.columns) > 0:
            column_combo.addItems(list(self.current_df.columns))
        row_layout.addWidget(column_combo)
        
        # Operator selector
        operator_combo = QComboBox()
        operator_combo.setObjectName("operator_combo")
        operator_combo.addItems([
            "=", "<>", "<", ">", "<=", ">=",
            "CONTAINS", "NOT CONTAINS",
            "STARTS WITH", "ENDS WITH",
            "IN", "NOT IN",
            "IS NULL", "IS NOT NULL",
        ])
        operator_combo.setMinimumWidth(120)
        row_layout.addWidget(operator_combo)
        
        # Value input
        value_input = QLineEdit()
        value_input.setObjectName("value_input")
        value_input.setPlaceholderText("Enter value...")
        value_input.setMinimumWidth(180)
        # Connect Return key to apply filters
        value_input.returnPressed.connect(self.apply_all_filters)
        row_layout.addWidget(value_input)
        
        row_layout.addStretch()
        
        # Remove button
        remove_btn = QPushButton("−")
        remove_btn.setObjectName("remove_btn")
        remove_btn.setFixedSize(24, 24)
        remove_btn.setStyleSheet("""
            QPushButton {
                background-color: #d13438;
                color: #ffffff;
                border-radius: 2px;
                font-size: 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #e04348;
            }
        """)
        remove_btn.clicked.connect(lambda: self.remove_filter_row(row_widget))
        row_layout.addWidget(remove_btn)
        
        self.filter_rows_layout.addWidget(row_widget)
    
    def remove_filter_row(self, row_widget):
        """Remove a filter row"""
        if self.filter_rows_layout.count() > 1:
            self.filter_rows_layout.removeWidget(row_widget)
            row_widget.deleteLater()
    
    def apply_all_filters(self):
        """Apply all filter conditions to the dataframe"""
        if self.original_df is None or len(self.original_df) == 0:
            return
        
        import pandas as pd
        filtered_df = self.original_df.copy()
        
        # Collect all filter conditions
        for i in range(self.filter_rows_layout.count()):
            row_widget = self.filter_rows_layout.itemAt(i).widget()
            if not row_widget:
                continue
            
            column_combo = row_widget.findChild(QComboBox, "column_combo")
            operator_combo = row_widget.findChild(QComboBox, "operator_combo")
            value_input = row_widget.findChild(QLineEdit, "value_input")
            
            if not all([column_combo, operator_combo, value_input]):
                continue
            
            column = column_combo.currentText()
            operator = operator_combo.currentText()
            value = value_input.text().strip()
            
            if not column or not value:
                continue
            
            # Apply filter based on operator
            try:
                col_s = filtered_df[column].astype(str)
                if operator in ("=", "=="):
                    filtered_df = filtered_df[col_s == value]
                elif operator in ("<>", "!="):
                    filtered_df = filtered_df[col_s != value]
                elif operator == ">":
                    filtered_df = filtered_df[pd.to_numeric(filtered_df[column], errors='coerce') > float(value)]
                elif operator == ">=":
                    filtered_df = filtered_df[pd.to_numeric(filtered_df[column], errors='coerce') >= float(value)]
                elif operator == "<":
                    filtered_df = filtered_df[pd.to_numeric(filtered_df[column], errors='coerce') < float(value)]
                elif operator == "<=":
                    filtered_df = filtered_df[pd.to_numeric(filtered_df[column], errors='coerce') <= float(value)]
                elif operator == "CONTAINS":
                    filtered_df = filtered_df[col_s.str.contains(value, case=False, na=False)]
                elif operator == "NOT CONTAINS":
                    filtered_df = filtered_df[~col_s.str.contains(value, case=False, na=False)]
                elif operator == "STARTS WITH":
                    filtered_df = filtered_df[col_s.str.startswith(value, na=False)]
                elif operator == "ENDS WITH":
                    filtered_df = filtered_df[col_s.str.endswith(value, na=False)]
                elif operator == "IN":
                    vals = [v.strip() for v in value.split(",")]
                    filtered_df = filtered_df[col_s.isin(vals)]
                elif operator == "NOT IN":
                    vals = [v.strip() for v in value.split(",")]
                    filtered_df = filtered_df[~col_s.isin(vals)]
                elif operator == "IS NULL":
                    filtered_df = filtered_df[
                        filtered_df[column].isna() | (col_s.str.strip() == "")]
                elif operator == "IS NOT NULL":
                    filtered_df = filtered_df[
                        ~(filtered_df[column].isna() | (col_s.str.strip() == ""))]
            except Exception as e:
                from utils.logger import get_logger
                logger = get_logger()
                logger.error(f"Filter error: {str(e)}")
                continue
        
        # Update view and reload with pagination
        self._result_view_df = filtered_df
        self._result_page = 0
        self._refresh_result_view()
        self.result_table.show()

        # Update status
        total = len(self.original_df) if self.original_df is not None else 0
        self.status_label.setPlainText(f"{len(filtered_df)} rows (filtered from {total})")
        self.status_label.setFixedHeight(28)
    
    def clear_all_filters(self):
        """Clear all filters and show original data"""
        if self.original_df is not None:
            self._result_view_df = self.original_df.copy()
            self._result_page = 0
            self._refresh_result_view()
            self.result_table.show()
            self.status_label.setPlainText(f"{len(self.original_df)} rows")
            self.status_label.setFixedHeight(28)
        
        # Clear all filter rows except first one
        while self.filter_rows_layout.count() > 1:
            row_widget = self.filter_rows_layout.itemAt(self.filter_rows_layout.count() - 1).widget()
            if row_widget:
                self.filter_rows_layout.removeWidget(row_widget)
                row_widget.deleteLater()
        
        # Reset first row
        if self.filter_rows_layout.count() > 0:
            row_widget = self.filter_rows_layout.itemAt(0).widget()
            if row_widget:
                value_input = row_widget.findChild(QLineEdit, "value_input")
                if value_input:
                    value_input.clear()
    
    def _update_filter_columns(self, columns):
        """Update column options in all filter rows"""
        for i in range(self.filter_rows_layout.count()):
            row_widget = self.filter_rows_layout.itemAt(i).widget()
            if row_widget:
                column_combo = row_widget.findChild(QComboBox, "column_combo")
                if column_combo:
                    current = column_combo.currentText()
                    column_combo.clear()
                    column_combo.addItems(columns)
                    if current in columns:
                        column_combo.setCurrentText(current)
    
    def get_query_at_cursor(self):
        """Get the SQL query where the cursor is positioned"""
        full_text = self.editor.toPlainText()
        cursor = self.editor.textCursor()
        cursor_pos = cursor.position()
        
        if not full_text.strip():
            return None
        
        # Split by semicolon to find individual queries
        queries = []
        current_query = ""
        current_pos = 0
        
        for line in full_text.split('\n'):
            line_len = len(line) + 1  # +1 for newline
            
            # Check if line contains semicolon
            if ';' in line:
                parts = line.split(';')
                for i, part in enumerate(parts):
                    current_query += part
                    current_pos += len(part)
                    
                    if i < len(parts) - 1:  # Not the last part
                        current_query += ';'
                        current_pos += 1
                        
                        # Store this query with its position range
                        if current_query.strip():
                            queries.append((
                                current_pos - len(current_query),
                                current_pos,
                                current_query.strip()
                            ))
                        current_query = ""
            else:
                current_query += line + '\n'
                current_pos += line_len
        
        # Add any remaining query
        if current_query.strip():
            queries.append((
                current_pos - len(current_query),
                current_pos,
                current_query.strip()
            ))
        
        # Find which query contains the cursor
        for start_pos, end_pos, query in queries:
            if start_pos <= cursor_pos <= end_pos:
                return query
        
        # If no query found, return None
        return None

    # ── Find / Replace ────────────────────────────────────────────────────────

    # ─── Find / Replace ───────────────────────────────────────────────────────

    def _toggle_find_bar(self):
        """Cmd+F: show find bar (replace row hidden)."""
        if self._find_bar.isHidden():
            self._replace_row.hide()
            self._find_bar.show()
            self._find_input.setFocus()
            self._find_input.selectAll()
            self._find_live_update()
        else:
            self._hide_find_bar()

    def _toggle_find_replace(self):
        """Cmd+H: show find+replace bar."""
        self._replace_row.show()
        self._find_bar.show()
        self._find_input.setFocus()
        self._find_input.selectAll()
        self._find_live_update()

    def _hide_find_bar(self):
        self._find_bar.hide()
        self._find_matches.clear()
        self._find_match_idx = -1
        self._find_match_lbl.setText("")
        self._clear_find_highlights()
        self.editor.setFocus()

    # ── Smart search helpers ──────────────────────────────────────────────────

    def _find_build_pattern(self, text: str):
        """Build a regex pattern from the current search text + toggles.
        Smart-case: if text has no uppercase → case-insensitive.
        Returns compiled re.Pattern or None."""
        if not text:
            return None
        use_case  = self._fb_case_btn.isChecked()
        use_word  = self._fb_word_btn.isChecked()
        use_regex = self._fb_regex_btn.isChecked()

        # Smart-case: override case toggle when text has no uppercase
        if not use_case and not any(c.isupper() for c in text):
            flags = _re.IGNORECASE
        else:
            flags = 0 if use_case else _re.IGNORECASE

        try:
            pattern = text if use_regex else _re.escape(text)
            if use_word:
                pattern = r"\b" + pattern + r"\b"
            return _re.compile(pattern, flags)
        except _re.error:
            return None

    def _clear_find_highlights(self):
        """Remove all orange match highlights from the editor."""
        fmt_clr = QTextCharFormat()
        cur = QTextCursor(self.editor.document())
        cur.select(QTextCursor.Document)
        cur.setCharFormat(fmt_clr)
        cur.clearSelection()
        self.editor.setExtraSelections(self.editor.extraSelections()[:1])  # keep line hl

    def _find_live_update(self, *_):
        """Re-run search on every keystroke or toggle change."""
        text = self._find_input.text()
        self._find_matches.clear()
        self._find_match_idx = -1
        self._clear_find_highlights()

        pat = self._find_build_pattern(text)
        if pat is None:
            self._find_match_lbl.setText("")
            self._find_input.setStyleSheet(
                self._find_input.styleSheet().replace("border-color:#ff453a;", "")
            )
            return

        content = self.editor.toPlainText()
        cursors = []
        fmt_hi = QTextCharFormat()
        fmt_hi.setBackground(QColor("#ff9f0a"))
        fmt_hi.setForeground(QColor("#000000"))

        for m in pat.finditer(content):
            c = QTextCursor(self.editor.document())
            c.setPosition(m.start())
            c.setPosition(m.end(), QTextCursor.KeepAnchor)
            self._find_matches.append(c)
            sel = self.editor.ExtraSelection() if hasattr(self.editor, "ExtraSelection") else None
            # Use QTextEdit.ExtraSelection approach via setExtraSelections
            from PySide6.QtWidgets import QTextEdit
            es = QTextEdit.ExtraSelection()
            es.cursor = c
            es.format = fmt_hi
            cursors.append(es)

        # Keep current-line highlight (index 0) and add match highlights
        base = self.editor.extraSelections()[:1]
        self.editor.setExtraSelections(base + cursors)

        count = len(self._find_matches)
        if count == 0:
            self._find_match_lbl.setText("not found")
            self._find_match_lbl.setStyleSheet("color:#ff453a;font-size:11px;min-width:60px;")
        else:
            self._find_match_lbl.setText(f"1 of {count}")
            self._find_match_lbl.setStyleSheet("color:#8e8e93;font-size:11px;min-width:60px;")
            # Jump to first match
            self._find_match_idx = 0
            self._jump_to_match(0)

    def _jump_to_match(self, idx: int):
        if not self._find_matches:
            return
        idx = idx % len(self._find_matches)
        self._find_match_idx = idx
        c = QTextCursor(self._find_matches[idx])
        self.editor.setTextCursor(c)
        self.editor.ensureCursorVisible()
        self._find_match_lbl.setText(
            f"{idx + 1} of {len(self._find_matches)}")
        self._find_match_lbl.setStyleSheet("color:#8e8e93;font-size:11px;min-width:60px;")

    def _find_next(self):
        if not self._find_matches:
            self._find_live_update()
            return
        self._jump_to_match(self._find_match_idx + 1)

    def _find_prev(self):
        if not self._find_matches:
            self._find_live_update()
            return
        self._jump_to_match(self._find_match_idx - 1)

    def _replace_current(self):
        text = self._find_input.text()
        repl = self._replace_input.text()
        if not text or not self._find_matches:
            return
        idx  = self._find_match_idx
        if 0 <= idx < len(self._find_matches):
            c = QTextCursor(self._find_matches[idx])
            c.beginEditBlock()
            c.removeSelectedText()
            c.insertText(repl)
            c.endEditBlock()
        self._find_live_update()

    def _replace_all(self):
        text = self._find_input.text()
        repl = self._replace_input.text()
        if not text:
            return
        pat = self._find_build_pattern(text)
        if pat is None:
            return
        new_content = pat.sub(repl, self.editor.toPlainText())
        if new_content != self.editor.toPlainText():
            self.editor.setPlainText(new_content)
        self._find_live_update()

    # ── Diff ──────────────────────────────────────────────────────────────────

    def _on_diff_toggled(self, active: bool):
        self._diff_active = active
        if active and self._prev_df is not None:
            self._apply_diff_highlights()
        else:
            self._clear_diff_highlights()

    def _apply_diff_highlights(self):
        """Highlight cells that differ between _prev_df and current_df."""
        from PySide6.QtGui import QColor, QBrush
        if self._prev_df is None or self.current_df is None:
            return
        # Compare the full dataframes (not the rendered page)
        prev = self._prev_df.reset_index(drop=True)
        curr = self.current_df.reset_index(drop=True)
        added_bg   = QBrush(QColor("#1a3a1a"))   # green — new row
        changed_bg = QBrush(QColor("#3a2800"))   # amber — changed cell
        # Walk the visible result table rows
        for row in range(self.result_table.rowCount()):
            # Map visible row back to the full dataframe index
            # (result_table shows the current page slice)
            page_start = self._result_page * self._result_page_size
            df_row = page_start + row
            for col in range(self.result_table.columnCount()):
                item = self.result_table.item(row, col)
                if item is None:
                    continue
                if df_row >= len(prev):
                    item.setBackground(added_bg)
                else:
                    try:
                        prev_val = str(prev.iloc[df_row, col]) if col < prev.shape[1] else ""
                        curr_val = str(curr.iloc[df_row, col]) if col < curr.shape[1] else ""
                        if prev_val != curr_val:
                            item.setBackground(changed_bg)
                    except Exception:
                        pass

    def _clear_diff_highlights(self):
        """Remove diff highlighting (restore normal theme colours)."""
        from PySide6.QtGui import QBrush, QColor
        clear = QBrush(QColor(0, 0, 0, 0))
        for row in range(self.result_table.rowCount()):
            for col in range(self.result_table.columnCount()):
                item = self.result_table.item(row, col)
                if item:
                    item.setBackground(clear)

    # ── Pin / Favourite ───────────────────────────────────────────────────────

    def _on_pin_toggled(self, pinned: bool):
        self.pinned = pinned
        # Signal to parent to persist; connection_panel listens via tab widget
        panel = self.parent()
        while panel is not None:
            if hasattr(panel, '_save_pinned_tabs'):
                panel._save_pinned_tabs()
                break
            panel = panel.parent()