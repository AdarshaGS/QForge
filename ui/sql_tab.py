import difflib
import os
import re as _re
import time
import pandas as pd
import sqlparse

from PySide6.QtCore import Qt, Signal, QRect
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
    QScrollArea,
    QCheckBox,
)
from PySide6.QtGui import QTextCursor, QColor, QTextCharFormat, QFontMetrics

from ui.code_editor import CodeEditor
from PySide6.QtGui import QTextCursor, QKeyEvent, QShortcut, QKeySequence

from ui.sql_highlighter import SqlHighlighter
from ui.sql_completer import SqlCompleter
from ui.editable_table import EditableTableWidget
from utils.df_export import export_dataframe
from utils import perf_metrics
from services import preferences
from ui.column_filter_dialog import ColumnFilterDialog
from ui.theme_manager import ThemeManager
from ui.snippet_manager import SnippetManager
from utils.sql_errors import sql_error_hint as _sql_error_hint, sql_error_title as _sql_error_title


# ─── Error card: location lookup (title/hint now in utils/sql_errors.py,
# shared with ui/edit_error_dialog.py's grid-save error dialog, issue #143) ──

_ERROR_LINE_RE = _re.compile(r"at line (\d+)", _re.IGNORECASE)
_ERROR_NEAR_RE = _re.compile(r"near ['\"](.+?)['\"]", _re.IGNORECASE)

# Issue #115: guardrails on CSV/JSON/Excel import into a query tab's result
# grid. A file-size cap rejects an oversized file (including a maliciously
# crafted small .xlsx that would decompress into an enormous sheet) before
# it's even opened; the row cap catches a large-but-not-huge JSON/Excel file
# that a byte-size check alone wouldn't.
_IMPORT_MAX_FILE_SIZE_BYTES = 500 * 1024 * 1024  # 500 MiB
_IMPORT_MAX_ROWS = 2_000_000
_RAW_SQL_COLUMN = "Raw SQL"


def _sql_error_location(message: str, query: str) -> tuple[int, int] | None:
    """Best-effort (line, column) — both 1-indexed — of the error within
    *query*, or None if nothing in the message anchors a position. MySQL/
    Postgres error text commonly includes "at line N" and/or "near '...'";
    column is found by locating that quoted token in the query text. Not
    guaranteed exact for every driver/error shape — best-effort, matching
    what a human would eyeball from the message, not a real parser."""
    line_m = _ERROR_LINE_RE.search(message)
    near_m = _ERROR_NEAR_RE.search(message)

    if not near_m or not query:
        return (int(line_m.group(1)), 1) if line_m else None

    token = near_m.group(1)
    line = int(line_m.group(1)) if line_m else 1
    lines = query.split("\n")

    # Try the reported line first — cheaper and usually right when present.
    if line_m:
        line_idx = min(max(line - 1, 0), len(lines) - 1)
        col = lines[line_idx].find(token)
        if col != -1:
            return (line, col + 1)

    # Fall back to a whole-query search, recomputing line/col from the
    # absolute offset — covers drivers that give "near" without "at line".
    pos = query.find(token)
    if pos == -1:
        return (line, 1) if line_m else None
    before = query[:pos]
    line = before.count("\n") + 1
    col = pos - (before.rfind("\n") + 1) + 1
    return (line, col)


class SqlTab(QWidget):

    # Emitted when user confirms inline edits — parent executes the SQL
    commit_sql = Signal(list)  # list of SQL strings
    # Emitted when the status-bar cost badge is clicked — parent opens the
    # Analyze Query dialog's Cost & Profile tab for this tab's query
    open_analyzer = Signal()
    # Emitted by Ctrl+Shift+Return — parent runs every statement in the
    # editor regardless of selection/cursor position, distinct from plain
    # Run (Ctrl+Return / the Run button), which scopes to the selection or
    # the statement at the cursor (see ConnectionPanel._run_query_in_tab).
    run_all_requested = Signal()

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

        # Schema validation (unknown table/column squiggly underline) —
        # debounced off the same keystrokes that drive autocomplete, and
        # reapplied whenever the cursor moves since CodeEditor's own
        # cursorPositionChanged handler (_highlight_current_line) resets
        # extraSelections to just the line highlight on every move.
        self._validation_timer = None
        self._validation_selections: list = []
        self._validation_issues: list = []
        self._find_selections: list = []
        self.editor.cursorPositionChanged.connect(self._apply_extra_selections)

        # Snippets
        self.snippet_manager = SnippetManager()
        self.completer.set_snippets(self.snippet_manager.get_all())

        # Install event filter for better control
        self.editor.installEventFilter(self)

        # Ctrl/Cmd-click go-to-definition (issue #206)
        self.editor.identifier_clicked.connect(self._go_to_definition)
        
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
                padding: 0;
                font-size: 14px;
            }
            QPushButton:checked { color: #ffd60a; border-color: #ffd60a; }
            QPushButton:hover   { color: #e5e5ea; border-color: #636366; }
        """)
        self.pin_btn.toggled.connect(self._on_pin_toggled)
        run_layout.addWidget(self.pin_btn)

        # ── Transaction controls (Slice 4, ai/load-context.md) ──────────────
        # Tab-scoped: this tab gets its own persistent connection once a
        # transaction is open (see ConnectionPanel._run_query_in_tab), so the
        # status/buttons here reflect only this tab, not the whole connection.
        self.tx_status_lbl = QLabel("")
        self.tx_status_lbl.setStyleSheet(
            "color: #ff9f0a; font-size: 12px; font-weight: 600;"
        )
        self.tx_status_lbl.hide()
        run_layout.addWidget(self.tx_status_lbl)

        _tx_btn_style = """
            QPushButton {
                background: transparent;
                color: #8e8e93;
                border: 1px solid #3a3a3c;
                border-radius: 5px;
                padding: 0 10px;
                font-size: 12px;
            }
            QPushButton:hover:!disabled { color: #e5e5ea; border-color: #636366; }
            QPushButton:disabled { color: #48484a; border-color: #2c2c2e; }
        """

        self.begin_tx_btn = QPushButton("Begin Tx")
        self.begin_tx_btn.setFixedHeight(28)
        self.begin_tx_btn.setToolTip("Start a manual transaction on a dedicated connection for this tab")
        self.begin_tx_btn.setStyleSheet(_tx_btn_style)
        run_layout.addWidget(self.begin_tx_btn)

        self.commit_tx_btn = QPushButton("Commit")
        self.commit_tx_btn.setFixedHeight(28)
        self.commit_tx_btn.setEnabled(False)
        self.commit_tx_btn.setToolTip("Commit the open transaction")
        self.commit_tx_btn.setStyleSheet(_tx_btn_style)
        run_layout.addWidget(self.commit_tx_btn)

        self.rollback_tx_btn = QPushButton("Rollback")
        self.rollback_tx_btn.setFixedHeight(28)
        self.rollback_tx_btn.setEnabled(False)
        self.rollback_tx_btn.setToolTip("Roll back the open transaction, discarding its changes")
        self.rollback_tx_btn.setStyleSheet(_tx_btn_style)
        run_layout.addWidget(self.rollback_tx_btn)

        run_layout.addStretch()

        self.auto_profile_chk = QCheckBox("Auto-profile")
        self.auto_profile_chk.setToolTip(
            "After each query, also run EXPLAIN ANALYZE to capture actual "
            "execution timing into history. Executes every read query a "
            "second time — leave off unless you're actively tuning."
        )
        self.auto_profile_chk.setStyleSheet("QCheckBox { color: #8e8e93; font-size: 12px; }")
        self.auto_profile_chk.setChecked(preferences.get("auto_profile_queries", False))
        self.auto_profile_chk.stateChanged.connect(
            lambda state: preferences.set("auto_profile_queries", bool(state)))
        run_layout.addWidget(self.auto_profile_chk)

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
        # Icon-only nav buttons are too narrow for _action_style's 10px
        # horizontal padding (it eats the whole fixed width, hiding the glyph).
        _nav_style = (
            "QPushButton{padding:0;height:26px;border:1px solid #3a3a3c;"
            "border-radius:4px;background:transparent;color:#e5e5ea;font-size:12px;}"
            "QPushButton:hover{background:#3a3a3c;}"
            "QPushButton:disabled{color:#48484a;border-color:#2c2c2e;}"
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

        # Visible toggle for the replace row (issue #112) — Replace was only
        # reachable via a keyboard shortcut that macOS intercepted for its
        # own "Hide Application" command, so there was no way to discover or
        # open it at all from the UI. Checkable so its state stays in sync
        # with however the row was actually opened/closed (shortcut or Esc).
        self._fb_replace_toggle_btn = QPushButton("⇄ Replace")
        self._fb_replace_toggle_btn.setCheckable(True)
        self._fb_replace_toggle_btn.setToolTip("Toggle Replace (Ctrl+Alt+F)")
        self._fb_replace_toggle_btn.setFixedHeight(22)
        self._fb_replace_toggle_btn.setStyleSheet(_toggle_style)

        self._prev_match_btn = QPushButton("▲")
        self._prev_match_btn.setFixedSize(24, 26)
        self._prev_match_btn.setStyleSheet(_nav_style)
        self._prev_match_btn.setToolTip("Previous match (Shift+Enter)")
        self._prev_match_btn.setEnabled(False)

        self._next_match_btn = QPushButton("▼")
        self._next_match_btn.setFixedSize(24, 26)
        self._next_match_btn.setStyleSheet(_nav_style)
        self._next_match_btn.setToolTip("Next match (Enter)")
        self._next_match_btn.setEnabled(False)

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
        _replace_lbl = QLabel("Replace:")
        _replace_lbl.setFixedWidth(52)
        rr_layout.addWidget(_replace_lbl)
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
        _find_lbl = QLabel("Find:")
        _find_lbl.setFixedWidth(52)
        find_row_l.addWidget(_find_lbl)
        find_row_l.addWidget(self._find_input, 2)
        find_row_l.addWidget(self._fb_case_btn)
        find_row_l.addWidget(self._fb_word_btn)
        find_row_l.addWidget(self._fb_regex_btn)
        find_row_l.addWidget(self._fb_replace_toggle_btn)
        find_row_l.addWidget(self._find_match_lbl)
        find_row_l.addWidget(self._prev_match_btn)
        find_row_l.addWidget(self._next_match_btn)
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
        self._fb_replace_toggle_btn.toggled.connect(self._on_replace_toggle_clicked)
        self._next_match_btn.clicked.connect(self._find_next)
        self._prev_match_btn.clicked.connect(self._find_prev)
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

        # ── Result-row icon toolbar (issue #178) — download/filter already
        # exist elsewhere (export_data(), the Ctrl+F filter panel); this
        # just surfaces them as icons next to the row-count summary. Grid
        # is the only real view so its button is a plain state indicator;
        # chart is a visible stub (no chart view exists anywhere yet).
        self._result_actions_bar = QWidget()
        _actions_layout = QHBoxLayout(self._result_actions_bar)
        _actions_layout.setContentsMargins(0, 0, 4, 0)
        _actions_layout.setSpacing(2)
        _actions_layout.addStretch()

        def _icon_btn(symbol: str, tooltip: str, checkable: bool = False) -> QPushButton:
            b = QPushButton(symbol)
            b.setFixedSize(26, 24)
            b.setToolTip(tooltip)
            b.setCheckable(checkable)
            b.setStyleSheet("""
                QPushButton {
                    background: transparent;
                    color: #8e8e93;
                    border: none;
                    border-radius: 4px;
                    font-size: 13px;
                }
                QPushButton:hover:!disabled { background: #3a3a3c; color: #e5e5ea; }
                QPushButton:checked { color: #0A84FF; }
                QPushButton:disabled { color: #48484a; }
            """)
            return b

        self._grid_view_btn = _icon_btn("▦", "Grid view (current)", checkable=True)
        self._grid_view_btn.setChecked(True)
        self._grid_view_btn.setEnabled(False)   # only view that exists today
        _actions_layout.addWidget(self._grid_view_btn)

        self._chart_view_btn = _icon_btn("📈", "Chart view — coming soon")
        self._chart_view_btn.setEnabled(False)
        _actions_layout.addWidget(self._chart_view_btn)

        self._download_icon_btn = _icon_btn("⬇", "Export results")
        self._download_icon_btn.clicked.connect(self.export_data)
        _actions_layout.addWidget(self._download_icon_btn)

        self._filter_icon_btn = _icon_btn("▽", "Filter results (Ctrl+F)")
        self._filter_icon_btn.clicked.connect(self.toggle_filter)
        _actions_layout.addWidget(self._filter_icon_btn)

        self._result_actions_bar.hide()   # shown only alongside a successful result

        # ── Empty-state illustration for a successful 0-row query (issue
        # #178) — a genuine SELECT with columns but no rows previously just
        # showed a blank grid with nothing to explain why.
        self._empty_state = QWidget()
        _empty_layout = QVBoxLayout(self._empty_state)
        _empty_layout.setAlignment(Qt.AlignCenter)
        _empty_layout.setSpacing(6)
        _empty_icon = QLabel("🔍")
        _empty_icon.setAlignment(Qt.AlignCenter)
        _empty_icon.setStyleSheet("font-size: 40px; background: transparent;")
        _empty_heading = QLabel("No rows returned")
        _empty_heading.setAlignment(Qt.AlignCenter)
        _empty_heading.setStyleSheet(
            "color: #e5e5ea; font-size: 15px; font-weight: 600; background: transparent;")
        _empty_subtitle = QLabel("The query executed successfully.")
        _empty_subtitle.setAlignment(Qt.AlignCenter)
        _empty_subtitle.setStyleSheet("color: #8e8e93; font-size: 12px; background: transparent;")
        _empty_layout.addWidget(_empty_icon)
        _empty_layout.addWidget(_empty_heading)
        _empty_layout.addWidget(_empty_subtitle)
        self._empty_state.hide()

        # ── Structured error card (issue #178) — replaces show_error()'s
        # single plain-text banner with a title, the message, an "Error
        # Location" section, a monospace snippet with a caret under the
        # error column (best-effort — see _sql_error_location), and an
        # "Error Details" section for the existing hint text.
        self._error_card = QWidget()
        self._error_card.setStyleSheet("""
            QWidget#errorCard {
                background-color: #3a1a1a;
                border-radius: 6px;
                border-left: 3px solid #f48771;
            }
        """)
        self._error_card.setObjectName("errorCard")
        _err_layout = QVBoxLayout(self._error_card)
        _err_layout.setContentsMargins(14, 12, 14, 12)
        _err_layout.setSpacing(8)

        _err_title_row = QHBoxLayout()
        _err_title_row.setSpacing(8)
        self._error_title_icon = QLabel("⊗")
        self._error_title_icon.setStyleSheet(
            "color: #f48771; font-size: 16px; background: transparent;")
        self._error_title_lbl = QLabel("")
        self._error_title_lbl.setStyleSheet(
            "color: #f48771; font-size: 14px; font-weight: 600; background: transparent;")
        _err_title_row.addWidget(self._error_title_icon)
        _err_title_row.addWidget(self._error_title_lbl, 1)
        _err_layout.addLayout(_err_title_row)

        self._error_message_lbl = QLabel("")
        self._error_message_lbl.setWordWrap(True)
        self._error_message_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._error_message_lbl.setStyleSheet(
            "color: #e5c6c1; font-size: 12px; background: transparent;")
        _err_layout.addWidget(self._error_message_lbl)

        self._error_elapsed_lbl = QLabel("")
        self._error_elapsed_lbl.setStyleSheet(
            "color: #8e6a63; font-size: 11px; background: transparent;")
        _err_layout.addWidget(self._error_elapsed_lbl)

        self._error_location_section = QWidget()
        _loc_layout = QVBoxLayout(self._error_location_section)
        _loc_layout.setContentsMargins(0, 4, 0, 0)
        _loc_layout.setSpacing(3)
        _loc_heading = QLabel("Error Location")
        _loc_heading.setStyleSheet(
            "color: #f4a99a; font-size: 11px; font-weight: 600; background: transparent;")
        self._error_location_lbl = QLabel("")
        self._error_location_lbl.setStyleSheet(
            "color: #e5c6c1; font-size: 12px; background: transparent;")
        self._error_snippet = QPlainTextEdit()
        self._error_snippet.setReadOnly(True)
        self._error_snippet.setFixedHeight(46)
        self._error_snippet.setLineWrapMode(QPlainTextEdit.NoWrap)
        self._error_snippet.setStyleSheet("""
            QPlainTextEdit {
                background-color: #2a1414;
                color: #d0d0d0;
                font-family: Menlo, Consolas, monospace;
                font-size: 12px;
                border: 1px solid #4a2a2a;
                border-radius: 4px;
                padding: 4px 8px;
            }
        """)
        _loc_layout.addWidget(_loc_heading)
        _loc_layout.addWidget(self._error_location_lbl)
        _loc_layout.addWidget(self._error_snippet)
        _err_layout.addWidget(self._error_location_section)
        self._error_location_section.hide()   # only shown when a location was found

        self._error_details_section = QWidget()
        _det_layout = QVBoxLayout(self._error_details_section)
        _det_layout.setContentsMargins(0, 4, 0, 0)
        _det_layout.setSpacing(3)
        _det_heading = QLabel("Error Details")
        _det_heading.setStyleSheet(
            "color: #f4a99a; font-size: 11px; font-weight: 600; background: transparent;")
        self._error_details_lbl = QLabel("")
        self._error_details_lbl.setWordWrap(True)
        self._error_details_lbl.setStyleSheet(
            "color: #e5c6c1; font-size: 12px; background: transparent;")
        _det_layout.addWidget(_det_heading)
        _det_layout.addWidget(self._error_details_lbl)
        _err_layout.addWidget(self._error_details_section)
        self._error_details_section.hide()   # only shown when a hint matched

        # Scrollable, not just fixed-size (issue #147/#178): a pathologically
        # long message + hint could otherwise still exceed the splitter's
        # bottom pane with no way to see the rest — the exact class of bug
        # #147 fixed for the old plain-text banner, now guarded here too.
        self._error_card_scroll = QScrollArea()
        self._error_card_scroll.setWidgetResizable(True)
        self._error_card_scroll.setFrameShape(QScrollArea.NoFrame)
        self._error_card_scroll.setWidget(self._error_card)
        self._error_card_scroll.hide()

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

        # Keep the grid hidden until a real result set arrives — showing an
        # empty placeholder grid here made it look like a result already
        # existed before any query had run (issue #24). The status area
        # takes the space instead.
        self.result_table.hide()

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

        self._status_row = QWidget()
        status_row_layout = QHBoxLayout(self._status_row)
        status_row_layout.setContentsMargins(0, 0, 0, 0)
        status_row_layout.setSpacing(0)
        status_row_layout.addWidget(self.status_label, 1)
        status_row_layout.addWidget(self._result_actions_bar)
        bottom_layout.addWidget(self._status_row)
        self._set_status("Run a query to see results here.")

        bottom_layout.addWidget(self._error_card_scroll)
        bottom_layout.addWidget(self._empty_state)
        bottom_layout.addWidget(self.result_table)
        bottom_layout.addWidget(self._pagination_bar)
        bottom_widget.setLayout(bottom_layout)
        
        # Add widgets to splitter
        self.splitter.addWidget(top_widget)
        self.splitter.addWidget(bottom_widget)
        self.splitter.setSizes([300, 500])  # Initial sizes
        
        layout.addWidget(self.splitter)
        layout.addWidget(self._build_tab_status_bar())

        self.setLayout(layout)

        # ==================================
        # KEYBOARD SHORTCUTS
        # ==================================
        
        # Add keyboard shortcut for run
        from PySide6.QtGui import QShortcut, QKeySequence
        self.run_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        self.run_shortcut.activated.connect(self.run_btn.click)

        # Run every statement in the editor, regardless of selection/cursor
        # — the explicit counterpart to plain Run's cursor/selection scoping.
        self.run_all_shortcut = QShortcut(QKeySequence("Ctrl+Shift+Return"), self)
        self.run_all_shortcut.activated.connect(self.run_all_requested.emit)

        # Add keyboard shortcuts for SQL formatting
        self.beautify_shortcut = QShortcut(QKeySequence("Ctrl+I"), self)
        self.beautify_shortcut.activated.connect(self.format_sql)
        
        self.minify_shortcut = QShortcut(QKeySequence("Ctrl+Shift+I"), self)
        self.minify_shortcut.activated.connect(self.minify_sql)

        # Ctrl+Alt+F (Cmd+Option+F on macOS) — find & replace (shows replace
        # row). NOT Ctrl+H/Cmd+H (issue #112): on macOS, Cmd+H is the
        # system-wide "Hide Application" shortcut and is intercepted by the
        # OS before Qt ever sees it, so that binding silently hid the whole
        # window instead of opening Replace — indistinguishable, from the
        # user's side, from Replace not existing at all. Cmd+Option+F
        # matches the convention used by Xcode/Sublime/VS Code on Mac.
        self.find_shortcut = QShortcut(QKeySequence("Ctrl+Alt+F"), self)
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
    
    def set_schema(self, tables, columns_dict, column_details=None,
                    foreign_keys=None, views=None, functions=None):
        """Update autocomplete with schema information"""
        self.completer.set_schema(tables, columns_dict, column_details=column_details,
                                   foreign_keys=foreign_keys, views=views, functions=functions)
        self._schedule_schema_validation()

    # ── Schema validation (unknown table/column squiggle) ────────────────

    def _schedule_schema_validation(self):
        """Debounce so a full-document regex pass doesn't run on every raw
        keystroke — same interval class as autocomplete's own latency budget."""
        if self._validation_timer is None:
            from PySide6.QtCore import QTimer
            self._validation_timer = QTimer(self)
            self._validation_timer.setSingleShot(True)
            self._validation_timer.setInterval(250)
            self._validation_timer.timeout.connect(self._update_schema_validation)
        self._validation_timer.start()

    def _update_schema_validation(self):
        """Squiggly-underline table/view names after FROM/JOIN/UPDATE/INTO
        and alias.col / table.col references that don't resolve against the
        active connection's schema. Conservative by design (see set_schema
        callers / SqlCompleter): only flags identifiers in positions the
        parser is already confident about, skips whatever's still being
        typed at the cursor, and does nothing at all until a real schema
        (not an empty/just-connecting one) has loaded."""
        known_tables = self.completer.known_tables()
        if not known_tables:
            self._validation_selections = []
            self._validation_issues = []
            self._apply_extra_selections()
            return

        query = self.editor.toPlainText()
        pos = self.editor.textCursor().position()
        doc = self.editor.document()

        fmt = QTextCharFormat()
        fmt.setUnderlineStyle(QTextCharFormat.SpellCheckUnderline)
        fmt.setUnderlineColor(QColor("#ff453a"))

        selections = []
        issues = []   # [{"start", "end", "text", "candidates"}] — feeds quick-fixes (#207)

        def flag(start: int, end: int, candidates: list[str]):
            c = QTextCursor(doc)
            c.setPosition(start)
            c.setPosition(end, QTextCursor.KeepAnchor)
            es = QTextEdit.ExtraSelection()
            es.cursor = c
            es.format = fmt
            selections.append(es)
            issues.append({"start": start, "end": end,
                            "text": query[start:end], "candidates": candidates})

        # Unknown table/view names right after FROM/JOIN/UPDATE/INTO —
        # only once the name is followed by a real boundary (not mid-typed).
        for m in _re.finditer(
                r'(?:FROM|JOIN|UPDATE|INTO)\s+[`"]?(\w+)[`"]?(?=[\s,;)]|$)',
                query, _re.IGNORECASE):
            name, end = m.group(1), m.end(1)
            if end == pos:
                continue
            if name not in known_tables:
                flag(m.start(1), end, sorted(known_tables))

        # Unknown columns in alias.col / table.col, only where the left side
        # resolves to a known table/alias with known columns — an
        # unresolved left side is ambiguous (could be a function, a JSON
        # path, …) and is deliberately left alone.
        for m in _re.finditer(r'\b(\w+)\.(\w+)\b', query):
            obj, col, end = m.group(1), m.group(2), m.end(2)
            if end == pos:
                continue
            table = self.completer.resolve_table(obj)
            if not table:
                continue
            cols = self.completer.table_columns(table)
            if not cols:
                continue
            if col not in cols and col.lower() not in (c.lower() for c in cols):
                flag(m.start(2), end, cols)

        self._validation_selections = selections
        self._validation_issues = issues
        self._apply_extra_selections()

    def _resolve_word_at(self, position: int) -> tuple[str, str | None]:
        """Word under `position` plus, for a dot-notation reference
        (alias.col / table.col), the table its left side resolves to.
        Shared by the hover tooltip and go-to-definition — both need the
        same "what identifier is this and what table (if any) qualifies
        it" resolution."""
        cursor = QTextCursor(self.editor.document())
        cursor.setPosition(position)
        cursor.select(QTextCursor.WordUnderCursor)
        word = cursor.selectedText()
        if not word:
            return "", None

        block_text = cursor.block().text()
        col_in_block = cursor.positionInBlock()
        word_start = block_text.rfind(word, 0, col_in_block)

        table = None
        if word_start > 0 and block_text[word_start - 1] == '.':
            left = block_text[:word_start - 1]
            m = _re.search(r'(\w+)$', left)
            if m:
                table = self.completer.resolve_table(m.group(1))
        return word, table

    def _resolve_definition_target(self, word: str, table: str | None) -> str | None:
        """The table/view whose structure `word` should navigate to, or
        None if it doesn't resolve to anything the schema knows about."""
        if not word:
            return None
        if table:
            cols = self.completer.table_columns(table)
            if word in cols or word.lower() in (c.lower() for c in cols):
                return table
            return None
        if word in self.completer.known_tables():
            return word
        return self.completer.alias_target(word)

    def _go_to_definition(self, position: int):
        """Ctrl/Cmd-click handler (CodeEditor.identifier_clicked): jump to
        the clicked table/column's structure view."""
        word, table = self._resolve_word_at(position)
        target = self._resolve_definition_target(word, table)
        if target:
            self._on_result_show_structure(target)

    def _quick_fix_at(self, position: int) -> dict | None:
        """The validation issue (see _update_schema_validation) covering
        `position`, if any — the span, bad text, and its candidate pool
        (known tables/views, or the owning table's columns)."""
        for issue in self._validation_issues:
            if issue["start"] <= position <= issue["end"]:
                return issue
        return None

    def _apply_quick_fix(self, start: int, end: int, replacement: str):
        c = QTextCursor(self.editor.document())
        c.setPosition(start)
        c.setPosition(end, QTextCursor.KeepAnchor)
        c.insertText(replacement)
        self._update_schema_validation()

    def _show_editor_context_menu(self, event) -> bool:
        """Standard edit menu plus a "Go to Definition" entry when the
        right-clicked identifier resolves against the active schema, and
        "Did you mean …?" quick-fixes when it's sitting on a
        schema-validation squiggle (#207)."""
        cursor = self.editor.cursorForPosition(event.pos())
        click_pos = cursor.position()
        word, table = self._resolve_word_at(click_pos)
        target = self._resolve_definition_target(word, table)
        issue = self._quick_fix_at(click_pos)

        menu = self.editor.createStandardContextMenu()

        if issue:
            matches = difflib.get_close_matches(
                issue["text"], issue["candidates"], n=3, cutoff=0.6)
            if matches:
                menu.addSeparator()
                for suggestion in matches:
                    action = menu.addAction(f"Did you mean “{suggestion}”?")
                    action.triggered.connect(
                        lambda checked=False, s=issue["start"], e=issue["end"], r=suggestion:
                            self._apply_quick_fix(s, e, r))

        if target:
            menu.addSeparator()
            action = menu.addAction(f"Go to Definition — {target}")
            action.triggered.connect(
                lambda: self._on_result_show_structure(target))

        menu.exec(event.globalPos())
        return True

    def _show_schema_hover(self, event) -> bool:
        """Resolve the identifier under the mouse against the completer's
        schema (table.col / alias.col / bare table or column name) and show
        a QToolTip with type/nullable/default/key — same source the
        autocomplete badges use, no extra fetch."""
        from PySide6.QtWidgets import QToolTip

        cursor = self.editor.cursorForPosition(event.pos())
        word, table = self._resolve_word_at(cursor.position())
        if not word:
            QToolTip.hideText()
            return True

        text = None
        known_tables = self.completer.known_tables()
        if table:
            meta = self.completer.column_meta(table, word)
            if meta:
                bits = [meta.get("type", "") or "?"]
                bits.append("NULL" if meta.get("nullable") else "NOT NULL")
                if meta.get("key") == "PRI":
                    bits.append("PRIMARY KEY")
                else:
                    fk = self.completer.foreign_key_for(table, word)
                    if fk:
                        bits.append(f"→ {fk['ref_table']}.{fk['ref_column']}")
                if meta.get("default") is not None:
                    bits.append(f"default {meta['default']}")
                text = f"{table}.{word}  —  " + "  ·  ".join(bits)
        elif word in known_tables:
            n_cols = len(self.completer.table_columns(word))
            kind = "view" if self.completer.is_view(word) else "table"
            text = f"{word}  ({kind}, {n_cols} column{'s' if n_cols != 1 else ''})"
        else:
            alias_target = self.completer.alias_target(word)
            if alias_target:
                text = f"{word}  →  alias for {alias_target}"

        if text:
            QToolTip.showText(event.globalPos(), text, self.editor)
        else:
            QToolTip.hideText()
        return True

    def _apply_extra_selections(self):
        """Merge CodeEditor's own selections (current-line highlight +
        matching-bracket highlight, reset on every cursor move by its
        cursorPositionChanged handler) with the find-match and
        schema-validation layers, each tracked in its own list so neither
        clobbers the other."""
        base = self.editor.own_extra_selections()
        self.editor.setExtraSelections(
            base + self._find_selections + self._validation_selections)

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

        # self.esc_shortcut (bare "Esc", WindowShortcut context) is matched
        # by Qt's shortcut dispatch *before* a real KeyPress is ever
        # delivered to self.editor — via a ShortcutOverride event sent to
        # the focus widget first. self.editor never claims it, so Escape
        # always fired hide_filter() and the KeyPress-based Key_Escape
        # branch below never ran while the popup was visible (issue #152).
        # Claiming ShortcutOverride for that one case pre-empts the
        # shortcut so the real KeyPress reaches the handling below instead;
        # every other Escape (popup not visible) still falls through to
        # the shortcut exactly as before.
        if (obj is self.editor and event.type() == event.Type.ShortcutOverride
                and event.key() == Qt.Key_Escape and self.completer.popup_visible):
            event.accept()
            return True

        # Schema-aware hover tooltip: type/nullable/default/key for a column,
        # or row/column counts for a table — same metadata the autocomplete
        # badges use, via QEvent.ToolTip (Qt's standard per-position-tooltip
        # hook, delivered on hover-still without needing setMouseTracking).
        if obj is self.editor and event.type() == event.Type.ToolTip:
            return self._show_schema_hover(event)

        # Go-to-Definition context-menu entry (issue #206)
        if obj is self.editor and event.type() == event.Type.ContextMenu:
            return self._show_editor_context_menu(event)

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
            self._schedule_schema_validation()
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
            self._schedule_schema_validation()
            return result

        # 6. Any other key (Enter for new line, space, punctuation…): hide popup
        if key not in (Qt.Key_Shift, Qt.Key_Control, Qt.Key_Alt, Qt.Key_Meta,
                       Qt.Key_CapsLock, Qt.Key_NumLock):
            self.completer.hide_popup()
            self._schedule_schema_validation()

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

        # A write statement (INSERT/UPDATE/DELETE/DDL) comes back with no
        # columns at all (no result set), not just zero rows — a genuine
        # SELECT with an empty result still has real column headers. Only
        # show the grid for an actual result set; a write's "N rows |
        # execution time" already comes from update_status() (issue #24 —
        # writes should show execution details, not an empty grid).
        if len(dataframe.columns) > 0:
            if len(dataframe) == 0:
                # A genuine SELECT that matched nothing — show the empty-
                # state illustration instead of a blank grid (issue #178).
                self.result_table.hide()
                self._pagination_bar.hide()
                self._empty_state.show()
            else:
                self._empty_state.hide()
                self.result_table.show()
                self._pagination_bar.show()
                self._refresh_result_view()
            self._expand_result_area()
        else:
            self._empty_state.hide()
            self.result_table.hide()
            self._pagination_bar.hide()
            self._collapse_result_area()

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
        for i, (lbl, df, *_rest) in enumerate(results):
            bar.addTab(f"Query {i+1}")
            bar.setTabToolTip(i, lbl)
        bar.blockSignals(False)
        bar.show()

        # Show first result
        bar.setCurrentIndex(0)
        self._on_multi_result_tab(0)
        # A failed statement's tab holds an Exception, not a DataFrame — it
        # contributes no rows to this total rather than breaking len().
        total_rows = sum(len(df) for _, df, *_rest in results if not isinstance(df, Exception))
        self.update_status(total_rows, elapsed)

    def _on_multi_result_tab(self, index: int):
        if not hasattr(self, '_multi_results') or index >= len(self._multi_results):
            return
        # cost is this statement's own CostEstimate|None — 2-tuple entries
        # (older callers, tests) simply carry no cost badge.
        entry = self._multi_results[index]
        _, df, cost = entry if len(entry) == 3 else (*entry, None)
        if isinstance(df, Exception):
            self.clear_cost_estimate()
            self.show_error(str(df))
            return
        self.current_df = df
        self.original_df = df.copy()
        self._result_view_df = df.copy()
        self._result_page = 0
        if len(df.columns) > 0:
            self._update_filter_columns(list(df.columns))
        self.result_table.show()
        self.set_cost_estimate(cost)
        self._refresh_result_view()
        self._expand_result_area()

    
    def add_filter_headers(self):
        """Add filter input boxes to column headers"""
        from ui.filter_header import FilterHeaderWidget
        
        for col in range(self.result_table.real_column_count()):
            col_name = self.result_table.horizontalHeaderItem(col).text()
            filter_widget = FilterHeaderWidget(col, col_name)
            filter_widget.filter_changed.connect(self.result_table.apply_column_filter)
            # Note: QTableWidget doesn't support setCellWidget for headers directly
            # Instead, we'll use the inline filtering in the table itself
    

    # ── Pagination helpers for SQL result table ───────────────────────────

    def _apply_result_primary_keys(self):
        """Tell result_table the real primary-key column(s) of
        current_table_name (from the already-loaded schema metadata, no
        extra fetch) so get_changes() can build a WHERE clause that
        actually identifies one row instead of assuming column 0 is the
        key. Empty/unknown table name or no cached PK info both fall back
        to result_table's own "match every column" default."""
        pk = (self.completer.primary_key_columns(self.current_table_name)
              if self.current_table_name else [])
        self.result_table.set_primary_key_columns(pk)

    def _refresh_result_view(self):
        """Display the current page of _result_view_df in result_table.

        Only 500 rows are rendered at a time so the main thread is never
        blocked building a 14k-row QTableWidget.
        """
        df = self._result_view_df
        if df is None or df.empty:
            self.result_table.load_data(df, self.current_table_name)
            self._apply_result_primary_keys()
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
        self._apply_result_primary_keys()
        self.result_table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)

        # Re-apply sort arrow/highlight so it survives load_data's reset —
        # setSortIndicator() alone doesn't render once the header carries a
        # custom stylesheet, see EditableTableWidget._apply_sort_header_labels.
        if self._result_sort_col >= 0:
            self.result_table._sort_col = self._result_sort_col
            self.result_table._sort_asc = self._result_sort_asc
            self.result_table._apply_sort_header_labels()

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



    # ─── Bottom status bar (issue #178) ──────────────────────────────────────

    _CONNECTION_STATE_STYLE = {
        # status -> (label text, dot/text colour)
        "idle":         ("Ready",        "#30d158"),
        "running":      ("Running…",     "#0A84FF"),
        "connecting":   ("Connecting…",  "#ff9f0a"),
        "disconnected": ("Disconnected", "#f48771"),
    }

    def _build_tab_status_bar(self) -> QWidget:
        """Persistent bar under the splitter — always visible, unlike the
        per-query status_label/error card above which only show up once a
        query has run. Reflects *connection* state (idle/running/
        connecting/disconnected), not query success/failure: a failed
        query still leaves the connection idle, and the error itself is
        already shown by the error card, so duplicating "Error" here would
        just race ConnectionPanel's own health_changed('idle') that fires
        right after every show_error() call anyway."""
        bar = QWidget()
        bar.setFixedHeight(24)
        bar.setStyleSheet("background-color: #1e1e1e; border-top: 1px solid #2c2c2e;")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(14)

        def _seg(text: str = "") -> QLabel:
            lbl = QLabel(text)
            lbl.setStyleSheet("color: #8e8e93; font-size: 11px; background: transparent;")
            layout.addWidget(lbl)
            return lbl

        self._readiness_lbl = _seg("●  Ready")
        self._readiness_lbl.setStyleSheet(
            "color: #30d158; font-size: 11px; font-weight: 600; background: transparent;")
        self._query_time_lbl = _seg("Query time: —")
        self._rows_status_lbl = _seg("Rows: —")

        # Pre-run cost badge (issue: query costing) — hidden until a
        # read-only query's EXPLAIN-based estimate arrives; best-effort,
        # so most write-only tabs never show it. Clicking opens the
        # consolidated Analyze Query dialog's Cost & Profile tab.
        self._cost_badge_btn = QPushButton("")
        self._cost_badge_btn.setFlat(True)
        self._cost_badge_btn.setCursor(Qt.PointingHandCursor)
        self._cost_badge_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none;"
            " font-size: 11px; padding: 0; }"
        )
        self._cost_badge_btn.clicked.connect(self.open_analyzer.emit)
        self._cost_badge_btn.hide()
        layout.addWidget(self._cost_badge_btn)

        layout.addStretch()
        self._cursor_pos_lbl = _seg("Ln 1, Col 1")
        _seg("UTF-8")
        self._dialect_lbl = _seg("")

        self.editor.cursorPositionChanged.connect(self._update_cursor_position_label)
        self._update_cursor_position_label()

        return bar

    def _update_cursor_position_label(self):
        cursor = self.editor.textCursor()
        self._cursor_pos_lbl.setText(f"Ln {cursor.blockNumber() + 1}, Col {cursor.columnNumber() + 1}")

    def set_connection_state(self, status: str):
        """Called by ConnectionPanel whenever its health_changed signal
        fires, for every open SqlTab (issue #178)."""
        text, color = self._CONNECTION_STATE_STYLE.get(status, ("Ready", "#8e8e93"))
        self._readiness_lbl.setText(f"●  {text}")
        self._readiness_lbl.setStyleSheet(
            f"color: {color}; font-size: 11px; font-weight: 600; background: transparent;")

    def set_dialect(self, dialect: str):
        """Called once per tab at creation (mirrors set_schema()) with the
        connection's DB type, e.g. 'MySQL' (issue #178)."""
        self._dialect_lbl.setText(dialect)

    # Cap on the status/error banner's height before it scrolls instead of
    # growing further (issue #147, raised from 150 — a multi-line error +
    # hint routinely exceeded that with nothing to indicate more was
    # hidden below the fold).
    _STATUS_MAX_HEIGHT = 300

    def _set_status(self, text: str, style: str = "", height: int = 28):
        """Helper: show text in the status_label (QPlainTextEdit), sized to
        fit the text's actual wrapped height rather than just counting
        literal '\\n's — a long single line that word-wraps across several
        visual lines was undercounted as one, which is what let it get
        clipped below the fixed height with no visible way to see the
        rest (issue #147). Still capped at _STATUS_MAX_HEIGHT; anything
        beyond that scrolls (setVerticalScrollBarPolicy(ScrollBarAsNeeded)
        in init_ui)."""
        self.status_label.setPlainText(text)
        if style:
            self.status_label.setStyleSheet(style)

        width = self.status_label.viewport().width() or self.status_label.width()
        if width > 0:
            # QPlainTextEdit's internal document uses QPlainTextDocumentLayout,
            # which (unlike QTextEdit's QTextDocumentLayout) doesn't compute a
            # meaningful wrapped size via document().setTextWidth()/.size() —
            # it reports a near-flat height regardless of wrapping. Measuring
            # with QFontMetrics against the same width instead gives the
            # actual wrapped line count.
            fm = QFontMetrics(self.status_label.font())
            rect = fm.boundingRect(QRect(0, 0, width, 100_000), Qt.TextWordWrap, text)
            # +32 covers the widget's non-text chrome: the QSS "padding"
            # each caller's stylesheet sets (up to 16px vertical, e.g.
            # show_error's "padding: 8px 10px") plus QTextDocument's own
            # 4px default document margin on each side (8px). Measured
            # empirically against real render output — a too-small value
            # here reintroduces the exact clipping this fix exists to
            # prevent, just by a few px instead of by a full line.
            content_h = rect.height() + 32
        else:
            # Not yet laid out (e.g. the very first call, from init_ui,
            # before the tab has a real window/width) — fall back to the
            # old literal-newline estimate. Only ever used for the short
            # initial placeholder text; a real query error can't happen
            # before the tab is part of a shown, sized window.
            lines = text.count('\n') + 1
            content_h = lines * 20 + 16

        h = min(max(height, content_h), self._STATUS_MAX_HEIGHT)
        self.status_label.setFixedHeight(int(h))
        self.status_label.show()
        self._status_row.show()

    def _cost_badge_color(self, score: int) -> str:
        """Mirrors ui/query_analyzer_dialog.py's _score_color so the badge
        and the dialog it opens agree on what "expensive" looks like."""
        if score == 0:
            return "#30d158"
        if score < 20:
            return "#0A84FF"
        if score < 50:
            return "#ff9f0a"
        return "#ff453a"

    def set_cost_estimate(self, estimate):
        """Populate the status-bar cost badge from a
        services.query_cost.CostEstimate. Silently hides the badge on
        error — best-effort, never surfaces EXPLAIN failures to the user
        here (they'd just re-run Estimate Cost in the dialog if curious)."""
        if estimate is None or getattr(estimate, "error", ""):
            self.clear_cost_estimate()
            return
        color = self._cost_badge_color(estimate.score)
        self._cost_badge_btn.setText(f"●  Cost: {estimate.score} · {estimate.label}")
        self._cost_badge_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none;"
            f" color: {color}; font-size: 11px; padding: 0; }}"
            f" QPushButton:hover {{ text-decoration: underline; }}"
        )
        self._cost_badge_btn.setToolTip(
            "\n".join(f"[{i.severity}] {i.message}" for i in estimate.issues[:8])
            or "No issues detected — click for the full Cost & Profile view."
        )
        self._cost_badge_btn.show()

    def clear_cost_estimate(self):
        self._cost_badge_btn.hide()

    def update_status(self, rows, execution_time, truncated=False):
        """Row count and query time live only in the bottom status bar —
        no text/icon row above the grid on a normal successful query
        (issue #178 follow-up: that row was removed entirely per
        feedback against a live screenshot). The one exception is the
        truncation warning, which the bottom bar has no room for."""
        self._error_card_scroll.hide()
        self._result_actions_bar.hide()
        if rows > 0:
            self._empty_state.hide()
        if truncated:
            self._set_status(
                "⚠  Result truncated — add a LIMIT to see more",
                """
                QPlainTextEdit {
                    color: #ff9f0a;
                    padding: 5px;
                    font-size: 12px;
                    font-weight: 500;
                    background: transparent;
                    border: none;
                }
                """,
            )
        else:
            self.status_label.hide()
            self.status_label.setFixedHeight(0)
            self._status_row.hide()
        self._query_time_lbl.setText(f"Query time: {execution_time * 1000:.0f} ms")
        self._rows_status_lbl.setText(f"Rows: {rows}")

    # Fits status_label's tallest case (_STATUS_MAX_HEIGHT) + margins.
    _COLLAPSED_RESULT_HEIGHT = _STATUS_MAX_HEIGHT + 20

    def _collapse_result_area(self):
        """Shrink the splitter's bottom pane to just fit the status line,
        giving the reclaimed space to the editor. Without this, hiding
        result_table on error/cancel/no-result-set left the bottom pane at
        whatever size it last was, showing a large empty gap below the
        status message (issue #31)."""
        sizes = self.splitter.sizes()
        if len(sizes) != 2:
            return
        total = sum(sizes)
        if sizes[1] > self._COLLAPSED_RESULT_HEIGHT:
            self._expanded_splitter_sizes = sizes
        self.splitter.setSizes([total - self._COLLAPSED_RESULT_HEIGHT, self._COLLAPSED_RESULT_HEIGHT])

    def _expand_result_area(self):
        """Restore the splitter to its pre-collapse size once a real
        result grid is being shown again."""
        sizes = getattr(self, '_expanded_splitter_sizes', None)
        if sizes:
            self.splitter.setSizes(sizes)

    def clear_for_run(self):
        """Hide any previous result grid or error card before a new run
        starts, so re-running in the same tab doesn't leave stale output
        on screen for the duration of the new query (issue #260)."""
        if hasattr(self, '_multi_result_bar') and self._multi_result_bar is not None:
            self._multi_result_bar.hide()
        self.result_table.clearContents()
        self.result_table.setRowCount(0)
        self.result_table.setColumnCount(0)
        self.result_table.hide()
        self._pagination_bar.hide()
        self._error_card_scroll.hide()
        self._empty_state.hide()
        self._result_actions_bar.hide()
        self.status_label.hide()
        self.status_label.setFixedHeight(0)
        self._status_row.hide()
        self._query_time_lbl.setText("Query time: —")
        self._rows_status_lbl.setText("Rows: —")
        self._collapse_result_area()

    def show_error(self, message: str, query: str = "", elapsed: float = 0.0):
        """Display a SQL error as a structured card — title, message, best-
        effort location + snippet, and the existing hint text (issue
        #178) — always selectable."""
        self.result_table.clearContents()
        self.result_table.setRowCount(0)
        self.result_table.setColumnCount(0)
        self.result_table.hide()
        self._pagination_bar.hide()
        self._empty_state.hide()
        self._result_actions_bar.hide()
        self.status_label.hide()
        self.status_label.setFixedHeight(0)
        self._status_row.hide()

        self._error_title_lbl.setText(_sql_error_title(message))
        self._error_message_lbl.setText(message)
        self._error_elapsed_lbl.setText(f"({elapsed:.3f}s)" if elapsed > 0 else "")

        location = _sql_error_location(message, query)
        if location:
            line, col = location
            self._error_location_lbl.setText(f"Line {line}, Column {col}")
            lines = query.split("\n") if query else []
            line_idx = min(max(line - 1, 0), len(lines) - 1) if lines else -1
            snippet_line = lines[line_idx] if 0 <= line_idx < len(lines) else query
            caret = " " * max(0, col - 1) + "^"
            self._error_snippet.setPlainText(f"{snippet_line}\n{caret}")
            self._error_location_section.show()
        else:
            self._error_location_section.hide()

        hint = _sql_error_hint(message, query)
        if hint:
            self._error_details_lbl.setText(hint)
            self._error_details_section.show()
        else:
            self._error_details_section.hide()

        self._error_card_scroll.show()
        self._expand_result_area()
        self._query_time_lbl.setText(f"Query time: {elapsed * 1000:.0f} ms" if elapsed > 0 else "Query time: —")
        self._rows_status_lbl.setText("Rows: 0")

    def show_cancelled(self):
        """Show a neutral 'query cancelled' status."""
        self.result_table.clearContents()
        self.result_table.setRowCount(0)
        self.result_table.setColumnCount(0)
        self.result_table.hide()
        self._pagination_bar.hide()
        self._error_card_scroll.hide()
        self._empty_state.hide()
        self._result_actions_bar.hide()
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
        self._collapse_result_area()

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
            file_size = os.path.getsize(file_name)
        except OSError as ex:
            QMessageBox.critical(self, "Error", f"Could not read file:\n{ex}")
            return
        if file_size > _IMPORT_MAX_FILE_SIZE_BYTES:
            QMessageBox.critical(
                self, "Error",
                f"File is {file_size / (1024 * 1024):,.0f} MiB, over the "
                f"{_IMPORT_MAX_FILE_SIZE_BYTES // (1024 * 1024):,} MiB import limit.")
            return

        _import_t0 = time.perf_counter()
        try:
            # Determine format from file extension
            if file_name.endswith('.csv'):
                df = pd.read_csv(file_name, nrows=_IMPORT_MAX_ROWS + 1)
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

            if len(df) > _IMPORT_MAX_ROWS:
                QMessageBox.critical(
                    self, "Error",
                    f"File has more than {_IMPORT_MAX_ROWS:,} rows — over the import limit.")
                return

            # Load the imported data into the table
            self.load_dataframe(df)
            perf_metrics.record("import_export", "file_import", (time.perf_counter() - _import_t0) * 1000)
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

    def insert_text_at_cursor(self, text: str):
        """Insert text at the cursor (replacing any selection), placing the
        cursor at the `{cursor}` marker if present — same placeholder
        convention SQL snippets use."""
        cursor = self.editor.textCursor()
        marker = "{cursor}"
        if marker in text:
            offset = text.index(marker)
            start = cursor.selectionStart() if cursor.hasSelection() else cursor.position()
            cursor.insertText(text.replace(marker, ""))
            cursor.setPosition(start + offset)
        else:
            cursor.insertText(text)
        self.editor.setTextCursor(cursor)
        self.editor.setFocus()

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
        # "Raw SQL" is a pseudo-column (not a real dataframe column) —
        # picking it searches every column for the typed value instead of
        # one specific column, replacing the old standalone "search all
        # columns" box with a row in this same list.
        column_combo = QComboBox()
        column_combo.setObjectName("column_combo")
        column_combo.setMinimumWidth(120)
        column_combo.addItem(_RAW_SQL_COLUMN)
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

        def _on_column_changed(text):
            is_raw_sql = text == _RAW_SQL_COLUMN
            operator_combo.setEnabled(not is_raw_sql)
            value_input.setPlaceholderText(
                "Search all columns…" if is_raw_sql else "Enter value..."
            )
        column_combo.currentTextChanged.connect(_on_column_changed)
        _on_column_changed(column_combo.currentText())

        row_layout.addStretch()
        
        # Remove button. No padding override previously meant the inherited
        # default QPushButton padding (5px 16px) squeezed the glyph out of
        # a 24px box, leaving what looked like a solid red block.
        remove_btn = QPushButton("×")
        remove_btn.setObjectName("remove_btn")
        remove_btn.setToolTip("Remove this filter condition")
        remove_btn.setFixedSize(24, 24)
        remove_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #d13438;
                border: 1px solid #d13438;
                border-radius: 4px;
                padding: 0;
                font-size: 15px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #d13438;
                color: #ffffff;
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

            if column == _RAW_SQL_COLUMN:
                if len(filtered_df.columns) == 0:
                    continue
                try:
                    mask = pd.Series(False, index=filtered_df.index)
                    for col in filtered_df.columns:
                        mask |= filtered_df[col].map(str).str.contains(value, case=False, na=False)
                    filtered_df = filtered_df[mask]
                except Exception as e:
                    from utils.logger import get_logger
                    logger = get_logger()
                    logger.error(f"Filter error: {str(e)}")
                continue

            # Apply filter based on operator
            try:
                col_s = filtered_df[column].map(str)
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
                    column_combo.addItem(_RAW_SQL_COLUMN)
                    column_combo.addItems(columns)
                    if current in columns or current == _RAW_SQL_COLUMN:
                        column_combo.setCurrentText(current)
    
    def get_query_at_cursor(self):
        """Get the SQL query where the cursor is positioned"""
        full_text = self.editor.toPlainText()
        cursor_pos = self.editor.textCursor().position()

        if not full_text.strip():
            return None

        # sqlparse.parse() is string-literal/comment aware, unlike a plain
        # ';'.split() — it won't break a statement at a semicolon that's
        # inside a string (e.g. WHERE msg = 'a;b') or a comment (issue #166).
        # Statement values reconstruct the original text exactly, so summing
        # their lengths gives correct offsets into full_text.
        try:
            statements = sqlparse.parse(full_text)
        except Exception:
            # e.g. SQLParseError on pathologically large input — fall back
            # to running the whole editor content (get_query()'s behavior
            # when this method returns None).
            return None

        offset = 0
        for stmt in statements:
            text = str(stmt)
            start, end = offset, offset + len(text)
            if start <= cursor_pos <= end and text.strip():
                return text.strip()
            offset = end

        return None

    # ── Find / Replace ────────────────────────────────────────────────────────

    # ─── Find / Replace ───────────────────────────────────────────────────────

    def _toggle_find_bar(self):
        """Cmd+F: open Quick Filter when the result grid has focus (issue
        #2), otherwise show the find bar (replace row hidden) for the
        SQL editor."""
        if hasattr(self, 'result_table') and self.result_table.hasFocus():
            self.toggle_filter()
            return
        if self._find_bar.isHidden():
            self._set_replace_row_visible(False)
            self._find_bar.show()
            self._find_input.setFocus()
            self._find_input.selectAll()
            self._find_live_update()
        else:
            self._hide_find_bar()

    def _toggle_find_replace(self):
        """Ctrl+Alt+F (Cmd+Option+F on macOS): show find+replace bar."""
        self._set_replace_row_visible(True)
        self._find_bar.show()
        self._find_input.setFocus()
        self._find_input.selectAll()
        self._find_live_update()

    def _on_replace_toggle_clicked(self, checked: bool):
        """The visible 'Replace' toggle button in the find bar (issue #112)
        — lets Replace be discovered and opened without needing to know any
        keyboard shortcut."""
        self._set_replace_row_visible(checked)
        if not self._find_bar.isHidden() and checked:
            self._replace_input.setFocus()

    def _set_replace_row_visible(self, visible: bool):
        """Single place that shows/hides the replace row and keeps the
        toggle button's checked state in sync with it, regardless of which
        entry point (shortcut, button, Esc) drove the change."""
        self._replace_row.setVisible(visible)
        if self._fb_replace_toggle_btn.isChecked() != visible:
            self._fb_replace_toggle_btn.blockSignals(True)
            self._fb_replace_toggle_btn.setChecked(visible)
            self._fb_replace_toggle_btn.blockSignals(False)

    def _hide_find_bar(self):
        self._find_bar.hide()
        self._set_replace_row_visible(False)
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
        self._find_selections = []
        self._apply_extra_selections()

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
            self._prev_match_btn.setEnabled(False)
            self._next_match_btn.setEnabled(False)
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

        self._find_selections = cursors
        self._apply_extra_selections()

        count = len(self._find_matches)
        self._prev_match_btn.setEnabled(count > 0)
        self._next_match_btn.setEnabled(count > 0)
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

    def set_transaction_state(self, active: bool):
        """Reflect whether this tab has an open manual transaction:
        toggles Begin/Commit/Rollback availability and the status label.
        Driven by ConnectionPanel._refresh_transaction_indicator — this
        tab has no transaction state of its own, it just displays it."""
        self.begin_tx_btn.setEnabled(not active)
        self.commit_tx_btn.setEnabled(active)
        self.rollback_tx_btn.setEnabled(active)
        if active:
            self.tx_status_lbl.setText("● Transaction open")
            self.tx_status_lbl.show()
        else:
            self.tx_status_lbl.hide()