from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QPushButton,
    QLineEdit,
    QComboBox,
    QMessageBox,
    QToolTip,
    QTabWidget,
    QMenu,
    QProgressBar,
)
from PySide6.QtGui import QShortcut, QKeySequence, QCursor
from ui.advanced_filter_dialog import AdvancedFilterDialog
from ui.theme_manager import ThemeManager

from ui.sql_tab import SqlTab
from ui.edit_error_dialog import show_save_errors
from services.db_service import DbService
from services import grid_layout
from services import preferences
from utils.logger import get_logger
from utils import perf_metrics
import pandas as pd
import threading
import time

logger = get_logger()

# Issue #251: user-configurable rows-per-page, persisted via
# services/preferences.py. A closed preset list (rather than a free-typed
# spinbox) keeps the persisted value always one of these, so the combo box
# never has to fabricate an extra entry to show the current selection.
_PAGE_SIZE_PREF_KEY = "table_page_size"
_DEFAULT_PAGE_SIZE = 100
_PAGE_SIZE_PRESETS = [100, 200, 500, 1000]
_RAW_SQL_COLUMN = "Raw SQL"


def _new_readonly_table(headers: list) -> QTableWidget:
    tbl = QTableWidget(0, len(headers))
    tbl.setHorizontalHeaderLabels(headers)
    tbl.verticalHeader().setVisible(False)
    tbl.setEditTriggers(QTableWidget.NoEditTriggers)
    tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    tbl.horizontalHeader().setStretchLastSection(True)
    return tbl


def _fill_columns_table(tbl: QTableWidget, cols):
    tbl.setRowCount(len(cols))
    for r, c in enumerate(cols):
        if isinstance(c, dict):
            tbl.setItem(r, 0, QTableWidgetItem(str(c.get("Field", ""))))
            tbl.setItem(r, 1, QTableWidgetItem(str(c.get("Type",  ""))))
            tbl.setItem(r, 2, QTableWidgetItem(str(c.get("Null",  ""))))
            tbl.setItem(r, 3, QTableWidgetItem(str(c.get("Key",   ""))))
            tbl.setItem(r, 4, QTableWidgetItem(str(c.get("Default", ""))))
        else:
            for ci, val in enumerate(list(c)[:5]):
                tbl.setItem(r, ci, QTableWidgetItem(str(val)))


def _fill_indexes_table(tbl: QTableWidget, idxs):
    tbl.setRowCount(len(idxs))
    for r, idx in enumerate(idxs):
        tbl.setItem(r, 0, QTableWidgetItem(str(idx.get("name", ""))))
        tbl.setItem(r, 1, QTableWidgetItem(str(idx.get("columns", ""))))
        tbl.setItem(r, 2, QTableWidgetItem("✔" if idx.get("unique") else ""))
        tbl.setItem(r, 3, QTableWidgetItem(str(idx.get("type", ""))))


def _fill_fk_table(tbl: QTableWidget, fks):
    tbl.setRowCount(len(fks))
    for r, fk in enumerate(fks):
        tbl.setItem(r, 0, QTableWidgetItem(str(fk.get("column", ""))))
        tbl.setItem(r, 1, QTableWidgetItem(str(fk.get("ref_table", ""))))
        tbl.setItem(r, 2, QTableWidgetItem(str(fk.get("ref_column", ""))))


def _filter_table_rows(tbl: QTableWidget, text: str, match_columns: tuple):
    """Live, in-memory, case-insensitive row filter (issue #53)."""
    needle = text.strip().lower()
    for row in range(tbl.rowCount()):
        if not needle:
            tbl.setRowHidden(row, False)
            continue
        haystack = " ".join(
            tbl.item(row, c).text() for c in match_columns if tbl.item(row, c)
        ).lower()
        tbl.setRowHidden(row, needle not in haystack)


def _wrap_with_search(tbl: QTableWidget, placeholder: str, match_columns: tuple) -> tuple:
    """A search box above *tbl* that filters its rows as the user types."""
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    search = QLineEdit()
    search.setPlaceholderText(placeholder)
    search.setClearButtonEnabled(True)
    search.textChanged.connect(lambda text: _filter_table_rows(tbl, text, match_columns))
    layout.addWidget(search)
    layout.addWidget(tbl)
    return container, search


class TableViewWidget(QWidget):
    """Widget that shows a table with streaming data loading"""

    execute_query_signal = Signal(object)  # Signal to execute query in query editor tab
    dirty_changed = Signal(bool)           # True = unsaved changes, False = clean
    find_table_usages_signal = Signal(str)         # (table_name) — issue #236
    find_column_usages_signal = Signal(str, str)   # (table_name, column_name) — issue #236
    find_database_usages_signal = Signal()         # issue #236

    # Bridge signals for load_table_data()/commit_changes()'s background
    # threading.Thread workers (same pattern as SchemaCompareDialog/
    # DataCompareDialog's _worker()+signal.emit()) — Qt auto-marshals the
    # connected slot to this widget's (main) thread, so the worker thread
    # itself needs no QThread/event-loop machinery of its own.
    _page_loaded = Signal(object)
    _page_load_errored = Signal(str)
    _commit_done = Signal(object)
    _commit_errored = Signal(str)
    _structure_load_done = Signal(object)   # (cols, idxs, fks) — issue #237
    _structure_load_errored = Signal(str)

    def __init__(self, db_service, table_name, config=None, parent=None):
        super().__init__(parent)

        self.db_service = db_service
        self.table_name = table_name
        self.config = config
        # A dedicated connection for background queries/writes — never the
        # shared self.db_service, which every other open tab and the schema
        # tree also touch with no locking (services/db_service.py:373-377).
        # Opened lazily by _get_worker_db(); closed by the owning
        # ConnectionPanel._close_tab.
        self._dedicated_db = None
        self._loading = False
        self.current_filter = ""
        self.columns = []
        self._primary_keys = None   # lazily fetched once — see load_table_data()
        self.sort_column = None
        self.sort_order = None  # 'DESC' or 'ASC'
        self.filter_conditions = []  # List of (column, operator, value) tuples
        self.filter_visible = False
        self._structure_loaded = False
        # Set by load_table_data() — lets a caller (ConnectionPanel, once a
        # (re)connect actually completes) tell apart a tab stuck on a
        # connection error from one that's already showing real data,
        # without string-matching limit_label's text (issue #176).
        self._load_failed = False

        # Pagination state
        self.current_page = 1          # 1-indexed
        self.total_rows = None         # total rows in the table (if known)
        # Issue #251: last-used value persists across sessions/tabs.
        self.page_size = preferences.get(_PAGE_SIZE_PREF_KEY, _DEFAULT_PAGE_SIZE)
        if self.page_size not in _PAGE_SIZE_PRESETS:
            self.page_size = _DEFAULT_PAGE_SIZE

        self.init_ui()

        # Add Cmd+F shortcut for filter
        self.filter_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        self.filter_shortcut.activated.connect(self.toggle_filter)

        # Add Esc shortcut to close filter
        self.esc_shortcut = QShortcut(QKeySequence("Esc"), self)
        self.esc_shortcut.activated.connect(self.hide_filter)

        # Add Cmd+S shortcut to save changes
        self.save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        self.save_shortcut.activated.connect(self.commit_changes)

        self._page_loaded.connect(self._apply_page_result)
        self._page_load_errored.connect(self._on_page_load_failed)
        self._commit_done.connect(self._apply_commit_result)
        self._commit_errored.connect(self._on_commit_failed)
        self._structure_load_done.connect(self._apply_structure_result)
        self._structure_load_errored.connect(self._on_structure_load_failed)

        # Load first page of data
        self.reset_and_load_first_page()

    def init_ui(self):
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        self.view_tabs = QTabWidget()
        self.view_tabs.setDocumentMode(True)  # left-align tabs (macOS centers by default)
        outer_layout.addWidget(self.view_tabs)

        data_page = QWidget()
        layout = QVBoxLayout(data_page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.view_tabs.addTab(data_page, "Data")

        # "Structure" is a group header, not real content (issue #46): clicking
        # it just reveals and jumps to Columns/Indexes/Foreign Keys, which live
        # as ordinary tabs on this same bar, hidden until then via
        # setTabVisible so they never show as a second row of tabs.
        self.structure_page = QWidget()
        QVBoxLayout(self.structure_page).setContentsMargins(0, 0, 0, 0)
        self.view_tabs.addTab(self.structure_page, "Structure")

        self.col_tbl = _new_readonly_table(["Column", "Type", "Null", "Key", "Default"])
        self.col_tbl.setContextMenuPolicy(Qt.CustomContextMenu)
        self.col_tbl.customContextMenuRequested.connect(self._show_column_context_menu)
        self.idx_tbl = _new_readonly_table(["Name", "Columns", "Unique", "Type"])
        self.fk_tbl = _new_readonly_table(["Column", "References Table", "References Column"])
        # Match columns per issue #53's spec: name always, type/key for Columns,
        # name+columns for Indexes, all fields for Foreign Keys.
        self.col_container, self.col_search = _wrap_with_search(
            self.col_tbl, "🔍 Search columns...", (0, 1, 3)
        )
        self.idx_container, self.idx_search = _wrap_with_search(
            self.idx_tbl, "🔍 Search indexes...", (0, 1)
        )
        self.fk_container, self.fk_search = _wrap_with_search(
            self.fk_tbl, "🔍 Search foreign keys...", (0, 1, 2)
        )
        for container, label in (
            (self.col_container, "Columns"),
            (self.idx_container, "Indexes"),
            (self.fk_container, "Foreign Keys"),
        ):
            self.view_tabs.addTab(container, label)
        self._structure_tab_indices = (
            self.view_tabs.indexOf(self.col_container),
            self.view_tabs.indexOf(self.idx_container),
            self.view_tabs.indexOf(self.fk_container),
        )
        for i in self._structure_tab_indices:
            self.view_tabs.setTabVisible(i, False)

        self.view_tabs.currentChanged.connect(self._on_view_tab_changed)

        # Refresh/Filter/Columns toolbar, floated over the tab bar's own row
        # at its top-right rather than costing a whole extra row of vertical
        # space. Issue #262: an explicit Refresh button lives here too, next
        # to Filter/Columns, so reloading the grid doesn't require knowing
        # the Cmd+R/F5 shortcut.
        #
        # A free child of view_tabs, positioned by hand (_position_view_tab_tools,
        # called from resizeEvent) rather than QTabWidget.setCornerWidget(): corner widgets turned
        # out to have two real, hard-to-diagnose problems here — (1) their
        # ownership isn't reliably recognized by PySide as a reparent, so a
        # bare local var for the container got garbage-collected once
        # init_ui() returned, corrupting the buttons parented to it (a
        # segfault, not at the point of any Python call); (2) even once
        # fixed, the corner slot's contents didn't reliably composite at
        # all — confirmed by grabbing the container directly and seeing
        # only its background, no buttons, matching what was reported from
        # the real app too. A plain overlay child widget doesn't have
        # either failure mode.
        self._view_tab_tools = QWidget(self.view_tabs)
        view_tab_tools = self._view_tab_tools
        view_tab_tools_layout = QHBoxLayout(view_tab_tools)
        view_tab_tools_layout.setContentsMargins(0, 0, 0, 0)
        view_tab_tools_layout.setSpacing(4)
        # Fixed HEIGHT only (comfortable next to the Data/Structure tabs) —
        # width is left to the layout so the icon+label text isn't clipped
        # the way a fixed square size would.
        _TAB_TOOL_BTN_HEIGHT = 26
        self.refresh_btn = QPushButton("↻ Refresh")
        self.refresh_btn.setFixedHeight(_TAB_TOOL_BTN_HEIGHT)
        self.refresh_btn.setToolTip("Refresh data (Cmd+R)")
        self.refresh_btn.clicked.connect(self.refresh_current_view)
        view_tab_tools_layout.addWidget(self.refresh_btn)

        self.filter_toggle_btn = QPushButton("▽ Filter")
        self.filter_toggle_btn.setCheckable(True)
        self.filter_toggle_btn.setFixedHeight(_TAB_TOOL_BTN_HEIGHT)
        self.filter_toggle_btn.setToolTip("Filter rows (Ctrl+F)")
        self.filter_toggle_btn.clicked.connect(self.toggle_filter)
        view_tab_tools_layout.addWidget(self.filter_toggle_btn)

        self.columns_btn = QPushButton("▦ Columns")
        self.columns_btn.setFixedHeight(_TAB_TOOL_BTN_HEIGHT)
        self.columns_btn.setToolTip("Show/hide columns")
        self.columns_btn.clicked.connect(lambda: self.data_table.manage_columns())
        view_tab_tools_layout.addWidget(self.columns_btn)

        view_tab_tools.adjustSize()
        view_tab_tools.raise_()
        self._position_view_tab_tools()

        # Top controls bar - only shown when filtering
        self.filter_container = QWidget()
        self.filter_container.hide()
        filter_main_layout = QVBoxLayout(self.filter_container)
        filter_main_layout.setContentsMargins(5, 5, 5, 5)
        filter_main_layout.setSpacing(4)

        # Filter rows container
        self.filter_rows_layout = QVBoxLayout()
        self.filter_rows_layout.setSpacing(3)
        filter_main_layout.addLayout(self.filter_rows_layout)

        # Add first filter row
        self.add_filter_row()

        # Action buttons at bottom
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

        apply_all_btn = QPushButton("Apply")
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

        layout.addWidget(self.filter_container)

        # Data table with a loading progress bar above it
        table_container = QWidget()
        table_layout = QVBoxLayout(table_container)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.setSpacing(0)

        # Issue #262: a slim progress bar above the grid replaces the old
        # full-view dark "Loading..." overlay — indeterminate (busy) range
        # since page loads/commits don't report real progress, and it no
        # longer blocks the grid from view while a reload is in flight.
        self.loading_progress_bar = QProgressBar()
        self.loading_progress_bar.setRange(0, 0)
        self.loading_progress_bar.setTextVisible(False)
        self.loading_progress_bar.setFixedHeight(3)
        self.loading_progress_bar.hide()
        table_layout.addWidget(self.loading_progress_bar)

        # Data table
        from ui.editable_table import EditableTableWidget
        self.data_table = EditableTableWidget()

        # Sorting here is server-side (re-queries with ORDER BY/LIMIT/OFFSET);
        # disable EditableTableWidget's own client-side handler so it doesn't
        # also fire on the same click and rewrite the header text with a
        # sort arrow before on_column_header_clicked reads the column name.
        try:
            self.data_table.horizontalHeader().sectionClicked.disconnect(
                self.data_table.on_header_clicked
            )
        except Exception:
            pass
        self.data_table.horizontalHeader().sectionClicked.connect(self.on_column_header_clicked)

        # Issue #182: persist column width/order/pinned-count per table.
        # EditableTableWidget only reports/accepts layout state — it doesn't
        # know the connection/database context needed to key the saved file.
        self.data_table.layout_changed.connect(self._save_grid_layout)

        table_layout.addWidget(self.data_table)

        # Bottom controls - row count center, pager right
        bottom_controls = QHBoxLayout()
        bottom_controls.setContentsMargins(3, 3, 3, 3)
        bottom_controls.setSpacing(3)

        bottom_controls.addStretch()

        # Row count in center
        self.limit_label = QLabel("")
        self.limit_label.setStyleSheet("color: gray; font-size: 11px;")
        bottom_controls.addWidget(self.limit_label)

        bottom_controls.addStretch()

        # Pager on the right
        pager_btn_style = """
            QPushButton {
                background-color: #0078d4;
                color: #ffffff;
                border: none;
                border-radius: 3px;
                padding: 4px 12px;
                font-weight: 500;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #1890ff;
            }
            QPushButton:disabled {
                background-color: #6e6e73;
                color: #ffffff;
            }
        """
        # Issue #251: rows-per-page control, persisted across sessions.
        self.page_size_combo = QComboBox()
        self.page_size_combo.setToolTip("Rows per page")
        self.page_size_combo.addItems([str(v) for v in _PAGE_SIZE_PRESETS])
        self.page_size_combo.setCurrentText(str(self.page_size))
        self.page_size_combo.setFixedWidth(70)
        self.page_size_combo.setStyleSheet("font-size: 11px;")
        # Connected after setCurrentText() above so restoring the saved
        # value on tab-open doesn't itself trigger a reload.
        self.page_size_combo.currentTextChanged.connect(self._on_page_size_changed)
        bottom_controls.addWidget(self.page_size_combo)

        self.prev_btn = QPushButton("‹ Prev")
        self.prev_btn.clicked.connect(self.prev_page)
        self.prev_btn.setEnabled(False)
        self.prev_btn.setStyleSheet(pager_btn_style)
        bottom_controls.addWidget(self.prev_btn)

        self.page_label = QLabel("1/1")
        self.page_label.setStyleSheet("color: gray; font-size: 11px;")
        self.page_label.setAlignment(Qt.AlignCenter)
        self.page_label.setFixedWidth(48)
        bottom_controls.addWidget(self.page_label)

        self.next_btn = QPushButton("Next ›")
        self.next_btn.clicked.connect(self.next_page)
        self.next_btn.setEnabled(False)
        self.next_btn.setStyleSheet(pager_btn_style)
        bottom_controls.addWidget(self.next_btn)

        layout.addWidget(table_container)
        layout.addLayout(bottom_controls)

    def _position_view_tab_tools(self):
        """Keep the Filter/Columns overlay pinned to the tab bar's
        top-right corner (see its construction comment in init_ui for why
        this is a manually-positioned overlay rather than a
        QTabWidget.setCornerWidget())."""
        if not hasattr(self, '_view_tab_tools'):
            return
        tools = self._view_tab_tools
        bar_h = self.view_tabs.tabBar().sizeHint().height()
        x = self.view_tabs.width() - tools.width() - 8
        y = max(0, (bar_h - tools.height()) // 2)
        tools.move(x, y)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_view_tab_tools()

    def update_theme(self, is_dark=True):
        """Update filter container theme"""
        if is_dark:
            style = ThemeManager.get_filter_container_style_dark()
        else:
            style = ThemeManager.get_filter_container_style_light()
        self.filter_container.setStyleSheet(style)

        # Filter/Columns toolbar (issue #252 follow-up). Two things this
        # overrides that the app-wide QPushButton rule would otherwise win
        # on: (1) background — a plain QWidget floated over the tab bar
        # doesn't pick up the `QTabBar { background: RAISED }` rule (that
        # selector only matches actual QTabBar instances), so without this
        # it kept the generic `QWidget { background: BG }` rule instead, a
        # visibly different color right next to the real tab bar; (2)
        # padding — the global `QPushButton { padding: 5px 16px; }` rule
        # isn't replaced by a per-widget stylesheet unless the same
        # property is set again here, and 16px of horizontal padding alone
        # exceeds these buttons' entire 26px fixed width, which was
        # rendering the icon completely outside its own clipped content
        # rect (invisible, not just low-contrast).
        if hasattr(self, '_view_tab_tools'):
            T = ThemeManager
            raised, hover, blue = (T.D_RAISED, T.D_HOVER, T.D_BLUE) if is_dark else (T.L_RAISED, T.L_HOVER, T.L_BLUE)
            text2 = T.D_TEXT2 if is_dark else T.L_TEXT2
            text = T.D_TEXT if is_dark else T.L_TEXT
            self._view_tab_tools.setStyleSheet(f"background: {raised};")
            icon_btn_style = f"""
                QPushButton {{
                    background: transparent;
                    color: {text2};
                    border: none;
                    border-radius: 5px;
                    padding: 0 8px;
                    font-size: 12px;
                }}
                QPushButton:hover {{ background: {hover}; color: {text}; }}
                QPushButton:checked {{ color: {blue}; }}
            """
            self.refresh_btn.setStyleSheet(icon_btn_style)
            self.filter_toggle_btn.setStyleSheet(icon_btn_style)
            self.columns_btn.setStyleSheet(icon_btn_style)

        # Update data table theme
        if hasattr(self, 'data_table'):
            self.data_table.update_theme(is_dark)

        # Update loading progress bar theme
        if hasattr(self, 'loading_progress_bar'):
            T = ThemeManager
            bg, blue = (T.D_RAISED, T.D_BLUE) if is_dark else (T.L_RAISED, T.L_BLUE)
            self.loading_progress_bar.setStyleSheet(f"""
                QProgressBar {{
                    background-color: {bg};
                    border: none;
                }}
                QProgressBar::chunk {{
                    background-color: {blue};
                }}
            """)

    def on_column_header_clicked(self, logical_index: int):
        """
        Handle column header click to sort the table by the clicked column

        Args:
            logical_index: The index of the clicked column
        """
        # Read the real column name, not the header's display text — that
        # text may carry a " ▲"/" ▼" sort-arrow suffix once a sort is active,
        # which would otherwise get embedded straight into the ORDER BY.
        if logical_index >= len(self.columns):
            return
        column_name = self.columns[logical_index]

        # Toggle sort order if clicking the same column, otherwise set to ascending
        if self.sort_column == column_name:
            # Same column - toggle sort order
            self.sort_order = "DESC" if self.sort_order == "ASC" else "ASC"
        else:
            # Different column - set as new sort column with ascending order
            self.sort_column = column_name
            self.sort_order = "ASC"

        # Reset and reload with new sort settings — sort doesn't change
        # which rows match, so skip the recount.
        self.reset_and_load_first_page(keep_count=True)

    def _get_worker_db(self):
        """The connection background loads/saves should use. Opens (once)
        a connection dedicated to this tab so its queries can run on a
        background thread without racing the shared self.db_service other
        tabs use. Falls back to self.db_service — synchronously, on the
        caller's thread — if no config was given or the dedicated connect
        fails, so the tab still works, just without the async benefit."""
        if self._dedicated_db is not None:
            return self._dedicated_db
        if not self.config:
            return self.db_service
        try:
            db = DbService()
            db.connect(self.config)
            self._dedicated_db = db
            return db
        except Exception as ex:
            logger.debug(f"Dedicated connection failed for {self.table_name}, falling back to shared: {ex}")
            return self.db_service

    def _warn_and_discard_changes(self):
        """Every reload path (refresh, page change, sort, filter) overwrites
        the grid with a fresh query result — there was previously no warning
        anywhere except inside the Cmd+S commit handler, so uncommitted
        edits could vanish with no trace. A blocking confirmation dialog on
        every refresh/page/sort turned out to be too disruptive in
        practice, so this discards and surfaces it via a brief non-blocking
        toast instead — losing a still-uncommitted grid edit is low-stakes
        (nothing's reached the database yet), it just shouldn't be silent."""
        if not self.data_table.has_changes():
            return
        n = len(self.data_table.modified_rows | self.data_table.new_rows | self.data_table.deleted_rows)
        from utils.toast import show_toast
        show_toast(
            self, f"Discarded {n} uncommitted change{'s' if n != 1 else ''} to {self.table_name}",
            icon="⚠", kind="warning",
        )

    def reset_and_load_first_page(self, keep_count: bool = False, warn: bool = True):
        """Reset to the first page and load it. *keep_count* skips the
        total_rows reset for callers (e.g. sort) that don't change which
        rows match, only their order — avoiding a needless recount.
        *warn* controls the discard toast: callers reloading after a
        successful save pass False since those changes are persisted,
        not discarded."""
        if warn:
            self._warn_and_discard_changes()
        self.current_page = 1
        if not keep_count:
            self.total_rows = None
        self.load_table_data()

    def load_table_data(self):
        """Load the current page for current filter and sort, refreshing row
        count. The DB work runs on a background thread (via _fetch_page) so
        a slow/remote query doesn't freeze the UI — every caller
        (reset_and_load_first_page, on_column_header_clicked, prev_page,
        next_page, apply_all_filters, clear_all_filters,
        refresh_current_view, commit_changes) routes through here, so this
        one re-entrancy guard covers all of them."""
        if self._loading:
            return
        self._loading = True
        self._page_load_t0 = time.perf_counter()
        self._page_load_wall_start = time.time()
        self.loading_progress_bar.show()
        self.prev_btn.setEnabled(False)
        self.next_btn.setEnabled(False)
        self.page_size_combo.setEnabled(False)

        def _worker():
            try:
                result = self._fetch_page()
            except Exception as ex:
                self._page_load_errored.emit(str(ex))
            else:
                self._page_loaded.emit(result)

        threading.Thread(target=_worker, daemon=True).start()

    def _fetch_page(self):
        """Runs on the background thread started by load_table_data() —
        must never touch Qt widgets, only self's plain attributes (safe:
        the _loading guard means nothing else mutates them meanwhile) and
        the DB. Returns a dict consumed by _apply_page_result() on the main
        thread."""
        db = self._get_worker_db()
        total_rows = self.total_rows

        # Cached across page turns and sort changes — those don't alter
        # the row count, so re-running a count on every next/prev click
        # was pure waste. Only filter changes, refresh, and save reset
        # total_rows to None to force a recount.
        #
        # For an unfiltered table, prefer the catalog's stats-based row
        # estimate over a real COUNT(*): on a huge table (hundreds of
        # GB) a real COUNT(*) is a full scan that can block the UI for
        # minutes just to open the table. The estimate can be stale, but
        # it can't hide real data — the "bump up if a full page came
        # back" logic below already corrects any undercount, so the only
        # downside of a stale estimate is a wrong-looking "of N rows"
        # label, never missing rows. A WHERE clause has no such catalog
        # stat, so filtered loads still pay for a real COUNT(*).
        if total_rows is None:
            estimate = None
            if not self.current_filter:
                get_estimate = getattr(db, "get_estimated_row_count", None)
                if get_estimate:
                    try:
                        estimate = get_estimate(self.table_name)
                    except Exception as ex:
                        logger.debug(f"Row count estimate failed for {self.table_name}: {ex}")

            if estimate is not None:
                total_rows = estimate
            else:
                try:
                    where_clause = f" WHERE {self.current_filter}" if self.current_filter else ""
                    count_query = f"SELECT COUNT(*) as total FROM {self.table_name}{where_clause}"  # nosec B608
                    count_df = db.execute_query(count_query)
                    total_rows = int(count_df.iloc[0]['total'])
                except Exception as ex:
                    logger.debug(f"Row count failed for {self.table_name}: {ex}")
                    total_rows = 1000000

        # Calculate offset
        offset = (self.current_page - 1) * self.page_size

        # Build ORDER BY clause
        order_clause = ""
        if self.sort_column:
            order_clause = f" ORDER BY {self.sort_column} {self.sort_order}"

        # Build query with pagination
        if self.current_filter:
            query = f"SELECT * FROM {self.table_name} WHERE {self.current_filter}{order_clause} LIMIT {self.page_size} OFFSET {offset}"  # nosec B608
        else:
            query = f"SELECT * FROM {self.table_name}{order_clause} LIMIT {self.page_size} OFFSET {offset}"  # nosec B608

        df = db.execute_query(query)

        # Store column names for filter - even if table is empty
        columns = self.columns
        if not columns:
            # Try to get columns from table structure if data is empty
            if len(df.columns) > 0:
                columns = list(df.columns)
            else:
                # Fallback: get structure from database
                try:
                    cols = db.get_columns(self.table_name)
                    columns = [col.get('Field', '') for col in cols]
                except Exception:
                    columns = []

        # Any approximate row-count statistic (MySQL's TABLE_ROWS, or a
        # stale/cached COUNT) can be wrong in either direction — not
        # just "reads 0 right after a bulk load", but stale-too-small
        # on any table with enough writes since the last ANALYZE. Never
        # let it override what was actually fetched: a full page means
        # there's likely more beyond it, so bump the estimate up rather
        # than letting a too-small stale number cap pagination and hide
        # real data on every later page.
        if len(df) > 0:
            got_full_page = len(df) == self.page_size
            min_known_rows = offset + len(df) + (1 if got_full_page else 0)
            total_rows = max(total_rows, min_known_rows)
        elif total_rows == 0 and columns:
            df = pd.DataFrame(columns=columns)

        primary_keys = self._primary_keys
        if primary_keys is None:
            try:
                primary_keys = db.get_primary_keys(self.table_name)
            except Exception as ex:
                logger.debug(f"get_primary_keys failed for {self.table_name}: {ex}")
                primary_keys = []

        return {
            "df": df, "offset": offset, "total_rows": total_rows,
            "columns": columns, "primary_keys": primary_keys,
        }

    def _finish_loading(self):
        self._loading = False
        self.loading_progress_bar.hide()
        self.page_size_combo.setEnabled(True)

    def _apply_page_result(self, result):
        """Main-thread handler for load_table_data()'s worker `_page_loaded`
        signal — applies a _fetch_page() result to the widgets."""
        self._finish_loading()
        df = result["df"]
        offset = result["offset"]
        self.total_rows = result["total_rows"]
        self.columns = result["columns"]
        self._primary_keys = result["primary_keys"]

        if self.columns:
            # Update all filter row column combos
            for i in range(self.filter_rows_layout.count()):
                row_widget = self.filter_rows_layout.itemAt(i).widget()
                if row_widget:
                    column_combo = row_widget.findChild(QComboBox, "column_combo")
                    if column_combo:
                        current = column_combo.currentText()
                        column_combo.clear()
                        column_combo.addItem(_RAW_SQL_COLUMN)
                        column_combo.addItems(self.columns)
                        if current in self.columns or current == _RAW_SQL_COLUMN:
                            column_combo.setCurrentText(current)

        self.data_table.load_data(df, table_name=self.table_name)
        self.data_table.set_primary_key_columns(self._primary_keys)
        self._restore_grid_layout()
        _page_load_ms = (time.perf_counter() - self._page_load_t0) * 1000
        # issue #174: a page load spanning a detected system
        # suspend/sleep isn't a real measurement of this operation's
        # cost — don't let it pollute page_load's mean/p95 with a
        # number that has nothing to do with query or render speed.
        if perf_metrics.likely_suspended_between(self._page_load_wall_start, time.time()):
            perf_metrics.counter_inc("suspend_filtered", "page_load")
            logger.info(
                f"load_table_data took {_page_load_ms:.0f}ms but overlapped a detected "
                "system suspend — not recorded as a normal page_load sample"
            )
        else:
            perf_metrics.record("result_grid", "page_load", _page_load_ms)
        perf_metrics.record("result_grid", "rows_rendered", len(df))
        # load_data() resets the grid's own sort state — restore it so
        # the header shows the arrow/highlight for the column this page
        # was actually ordered by (sorting itself is done server-side,
        # above, via ORDER BY, not by EditableTableWidget).
        if self.sort_column and self.sort_column in df.columns:
            self.data_table._sort_col = list(df.columns).index(self.sort_column)
            self.data_table._sort_asc = (self.sort_order == "ASC")
            self.data_table._apply_sort_header_labels()
        total_pages = (self.total_rows + self.page_size - 1) // self.page_size
        self.page_label.setText(f"{self.current_page}/{max(1, total_pages)}")

        start_row = offset + 1 if self.total_rows > 0 else 0
        end_row = min(offset + self.page_size, self.total_rows)

        if self.total_rows == 0:
            # For empty tables, show message in center
            self.limit_label.setText(f"No data - 0 rows")
        elif self.current_filter:
            self.limit_label.setText(f"Showing {start_row}-{end_row} of {self.total_rows} rows (filtered)")
        else:
            self.limit_label.setText(f"Showing {start_row}-{end_row} of {self.total_rows} rows")

        # Update pagination buttons
        self.prev_btn.setEnabled(self.current_page > 1)
        self.next_btn.setEnabled(self.current_page < total_pages)

        logger.info(f"Loaded page {self.current_page} ({len(df)} rows) from {self.table_name}")
        self._load_failed = False

    # ── Grid layout persistence (issue #182) ────────────────────────────
    # data_table.load_data() rebuilds the grid's columns from scratch on
    # every page/sort/filter refresh (and always resets pinned columns to
    # 0 while doing it — see its own docstring), so this runs after every
    # _apply_page_result, not just on first open.

    def _restore_grid_layout(self):
        saved = grid_layout.get_layout(
            self.config.get("id", "") if self.config else "",
            self.config.get("database", "") if self.config else "",
            self.table_name,
        )
        if saved:
            self.data_table.apply_layout_state(saved)

    def _save_grid_layout(self):
        grid_layout.save_layout(
            self.config.get("id", "") if self.config else "",
            self.config.get("database", "") if self.config else "",
            self.table_name,
            self.data_table.get_layout_state(),
        )

    def _on_page_load_failed(self, msg):
        """Main-thread handler for load_table_data()'s worker
        `_page_load_errored` signal."""
        self._finish_loading()
        logger.error(f"Failed to load table data: {msg}")
        self.limit_label.setText(f"Error: {msg}")
        self._load_failed = True
        if self.total_rows is not None:
            total_pages = (self.total_rows + self.page_size - 1) // self.page_size
            self.prev_btn.setEnabled(self.current_page > 1)
            self.next_btn.setEnabled(self.current_page < total_pages)

    def reload_if_errored(self):
        """Retry the current page if the last load attempt failed (issue
        #176) — e.g. this tab was restored/opened before the connection
        was actually live. A no-op for a tab that's already showing real
        data, so it's safe to call on every open TableViewWidget whenever
        a (re)connect completes."""
        if self._load_failed:
            self.load_table_data()

    def _on_page_size_changed(self, text: str):
        """Rows-per-page combo box changed (issue #251) — persists as the
        default for newly opened table views and reloads this one at the
        new size, starting back at page 1 (page boundaries shift under a
        different page size, so resuming at the old page number could land
        past the end or repeat/skip rows)."""
        try:
            new_size = int(text)
        except ValueError:
            return
        if new_size == self.page_size:
            return
        self.page_size = new_size
        preferences.set(_PAGE_SIZE_PREF_KEY, new_size)
        # total_rows doesn't change with page_size — same table/filter,
        # just a different grouping into pages — so skip the recount.
        self.reset_and_load_first_page(keep_count=True)

    def prev_page(self):
        """Load previous page"""
        if self.current_page > 1:
            self._warn_and_discard_changes()
            self.current_page -= 1
            self.load_table_data()

    def next_page(self):
        """Load next page"""
        total_pages = (self.total_rows + self.page_size - 1) // self.page_size
        if self.current_page < total_pages:
            self._warn_and_discard_changes()
            self.current_page += 1
            self.load_table_data()

    def add_filter_row(self):
        """Add a new filter row"""
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(4)

        # Column selector. "Raw SQL" is a pseudo-column (not a real
        # self.columns entry) — picking it searches every column for the
        # typed value instead of one specific column, replacing the old
        # standalone "search all columns" box with a row in this same list.
        column_combo = QComboBox()
        column_combo.setObjectName("column_combo")
        column_combo.setMinimumWidth(120)
        column_combo.addItem(_RAW_SQL_COLUMN)
        if self.columns:
            column_combo.addItems(self.columns)
        row_layout.addWidget(column_combo)

        # Operator selector
        operator_combo = QComboBox()
        operator_combo.setObjectName("operator_combo")
        operator_combo.addItems(["CONTAINS", "=", "!=", ">", ">=", "<", "<=", "STARTS WITH", "ENDS WITH"])
        operator_combo.setMinimumWidth(100)
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

        # Hide remove button if this is the first row
        if self.filter_rows_layout.count() == 0:
            remove_btn.hide()
        else:
            # Show remove button on all rows if we have more than 1
            for i in range(self.filter_rows_layout.count()):
                existing_row = self.filter_rows_layout.itemAt(i).widget()
                if existing_row:
                    btn = existing_row.findChild(QPushButton, "remove_btn")
                    if btn:
                        btn.show()

        self.filter_rows_layout.addWidget(row_widget)

    def remove_filter_row(self, row_widget):
        """Remove a filter row"""
        if self.filter_rows_layout.count() <= 1:
            return  # Keep at least one row

        row_widget.deleteLater()
        self.filter_rows_layout.removeWidget(row_widget)

        # Hide remove button if only one row left
        if self.filter_rows_layout.count() == 1:
            first_row = self.filter_rows_layout.itemAt(0).widget()
            if first_row:
                btn = first_row.findChild(QPushButton, "remove_btn")
                if btn:
                    btn.hide()

    def apply_all_filters(self):
        """Apply all filter conditions and reset data loading"""
        filter_conditions = []

        for i in range(self.filter_rows_layout.count()):
            row_widget = self.filter_rows_layout.itemAt(i).widget()
            if not row_widget:
                continue

            column_combo = row_widget.findChild(QComboBox, "column_combo")
            operator_combo = row_widget.findChild(QComboBox, "operator_combo")
            value_input = row_widget.findChild(QLineEdit, "value_input")

            if not column_combo or not operator_combo:
                continue

            column = column_combo.currentText()
            operator = operator_combo.currentText()
            value = value_input.text().strip() if value_input else ""

            if not column:
                continue

            if column == _RAW_SQL_COLUMN:
                if not value or not self.columns:
                    continue
                escaped = value.replace("'", "''")
                or_clause = " OR ".join(f"{col} LIKE '%{escaped}%'" for col in self.columns)
                filter_conditions.append(f"({or_clause})")
                continue

            # Build filter condition
            if operator in ["IS NULL", "IS NOT NULL"]:
                filter_conditions.append(f"{column} {operator}")
            elif not value:
                continue
            elif operator == "CONTAINS":
                # Auto-add % wildcards for easy contains search
                if not (value.startswith("'") and value.endswith("'")):
                    value = f"'%{value}%'"
                filter_conditions.append(f"{column} LIKE {value}")
            elif operator == "STARTS WITH":
                if not (value.startswith("'") and value.endswith("'")):
                    value = f"'{value}'"
                filter_conditions.append(f"{column} LIKE {value}%")
            elif operator == "ENDS WITH":
                if not (value.startswith("'") and value.endswith("'")):
                    value = f"'{value}'"
                filter_conditions.append(f"{column} LIKE %{value}")
            else:
                # Numeric or string comparison
                if not value.replace(".", "", 1).replace("-", "", 1).isdigit():
                    if not (value.startswith("'") and value.endswith("'")):
                        value = f"'{value}'"
                filter_conditions.append(f"{column} {operator} {value}")

        # Combine all conditions with AND
        if filter_conditions:
            self.current_filter = " AND ".join(filter_conditions)
        else:
            self.current_filter = ""

        # Reset data loading when filters change
        self.reset_and_load_first_page()

    def filter_by_column_value(self, column: str, value):
        """Filter the grid down to rows where *column* equals *value* and
        jump to the first page. Drives the same filter-row UI
        apply_all_filters() reads (rather than poking self.current_filter
        directly) so the filter panel reflects what's active and "Clear
        Filter" keeps working afterward. Used by FK-arrow/"Go to
        ref_table.column" navigation and the grid's right-click Quick
        Filter chips."""
        if column not in self.columns:
            return

        # Collapse to a single filter row so this replaces, rather than
        # adds to, whatever filter was already in place.
        while self.filter_rows_layout.count() > 1:
            item = self.filter_rows_layout.itemAt(self.filter_rows_layout.count() - 1)
            if item and item.widget():
                item.widget().deleteLater()
                self.filter_rows_layout.removeItem(item)
        if self.filter_rows_layout.count() == 0:
            self.add_filter_row()

        row_widget = self.filter_rows_layout.itemAt(0).widget()
        column_combo = row_widget.findChild(QComboBox, "column_combo") if row_widget else None
        operator_combo = row_widget.findChild(QComboBox, "operator_combo") if row_widget else None
        value_input = row_widget.findChild(QLineEdit, "value_input") if row_widget else None
        if not column_combo or not operator_combo or not value_input:
            return

        column_combo.setCurrentText(column)
        operator_combo.setCurrentText("=")
        value_input.setText(str(value))

        self.filter_visible = True
        self.filter_container.show()
        self.filter_toggle_btn.setChecked(True)

        self.apply_all_filters()

    def clear_all_filters(self):
        """Clear all filters and reset"""
        self.current_filter = ""

        # Clear all filter rows
        while self.filter_rows_layout.count() > 1:
            item = self.filter_rows_layout.itemAt(self.filter_rows_layout.count() - 1)
            if item and item.widget():
                item.widget().deleteLater()
                self.filter_rows_layout.removeItem(item)

        # Reset first row
        if self.filter_rows_layout.count() > 0:
            first_row = self.filter_rows_layout.itemAt(0).widget()
            if first_row:
                value_input = first_row.findChild(QLineEdit, "value_input")
                if value_input:
                    value_input.clear()
                operator_combo = first_row.findChild(QComboBox, "operator_combo")
                if operator_combo:
                    operator_combo.setCurrentIndex(0)

        # Reset data loading
        self.reset_and_load_first_page()

    def toggle_filter(self):
        """Toggle filter visibility with Cmd+F, or the Filter toolbar
        button — keep that button's checked look in sync regardless of
        which of the two triggered this (it auto-toggles itself when
        clicked directly, but not when Ctrl+F does)."""
        self.filter_visible = not self.filter_visible
        self.filter_toggle_btn.setChecked(self.filter_visible)
        if self.filter_visible:
            self.filter_container.show()
            # Focus on first value input
            if self.filter_rows_layout.count() > 0:
                first_row = self.filter_rows_layout.itemAt(0).widget()
                if first_row:
                    value_input = first_row.findChild(QLineEdit, "value_input")
                    if value_input:
                        value_input.setFocus()
        else:
            self.filter_container.hide()

    def hide_filter(self):
        """Hide filter with Esc key"""
        if self.filter_visible:
            self.filter_visible = False
            self.filter_toggle_btn.setChecked(False)
            self.filter_container.hide()

    # ─── Refresh ───────────────────────────────────────────────────────────────

    def refresh_current_view(self):
        """Reload current page (reset and reload first page)"""
        self.reset_and_load_first_page()

    # ─── Table/query tabs ─────────────────────────────────────────────────────

    def open_table_view(self, table_name: str):
        """Open a table view; re-focus if already open."""
        # This method is kept for compatibility but not used in this widget
        pass

    def add_new_tab(self):
        """Open a blank SQL query tab."""
        # This method is kept for compatibility but not used in this widget
        pass

    def _run_query_in_tab(self, tab):
        """Execute the SQL in `tab` using this connection's db_service."""
        # This method is kept for compatibility but not used in this widget
        pass

    def _close_tab(self, index):
        """Close tab at index."""
        # This method is kept for compatibility but not used in this widget
        pass

    def _rename_tab(self, index):
        """Rename tab at index."""
        # This method is kept for compatibility but not used in this widget
        pass

    # ─── Structure tab (issue #27) ──────────────────────────────────────────────

    def show_structure_tab(self):
        """Switch to the Structure tab, loading it on first use."""
        self.view_tabs.setCurrentWidget(self.structure_page)

    def _show_column_context_menu(self, position):
        """Impact Analysis for the right-clicked column (issue #236) — the
        actual lookup runs in ui/connection_panel.py, which owns the
        (Pro-gated) dialogs; this widget only knows its own table/column
        names, not entitlements or dialog wiring. Table is listed first/
        default (analyzing the whole table is the common case); Column is
        already known here (no picker needed, unlike the schema tree's
        version of this menu); Database needs neither."""
        row = self.col_tbl.rowAt(position.y())
        if row < 0:
            return
        item = self.col_tbl.item(row, 0)
        if not item or not item.text():
            return
        column_name = item.text()
        menu = QMenu(self)
        impact_menu = menu.addMenu("🔎 Impact Analysis")
        table_action = impact_menu.addAction("Table")
        column_action = impact_menu.addAction(f"Column ({column_name})")
        impact_menu.addSeparator()
        database_action = impact_menu.addAction("Database")
        action = menu.exec_(self.col_tbl.viewport().mapToGlobal(position))
        if action == table_action:
            self.find_table_usages_signal.emit(self.table_name)
        elif action == column_action:
            self.find_column_usages_signal.emit(self.table_name, column_name)
        elif action == database_action:
            self.find_database_usages_signal.emit()

    def _on_view_tab_changed(self, index):
        widget = self.view_tabs.widget(index)
        if widget is self.structure_page:
            if not self._structure_loaded:
                self._load_structure_tab()
            for i in self._structure_tab_indices:
                self.view_tabs.setTabVisible(i, True)
            self.view_tabs.setCurrentWidget(self.col_container)
        elif widget not in (self.col_container, self.idx_container, self.fk_container):
            for i in self._structure_tab_indices:
                self.view_tabs.setTabVisible(i, False)
        else:
            # Auto-focus that tab's search box (issue #53)
            search = {
                self.col_container: self.col_search,
                self.idx_container: self.idx_search,
                self.fk_container: self.fk_search,
            }[widget]
            search.setFocus()

    def _load_structure_tab(self):
        """Issue #237: get_columns/get_foreign_keys/get_indexes run on a
        background thread via the tab's own dedicated connection
        (_get_worker_db(), same as load_table_data()) instead of blocking
        the UI thread."""
        self._structure_loaded = True
        sig_done, sig_error = self._structure_load_done, self._structure_load_errored

        def _worker():
            try:
                db = self._get_worker_db()
                cols = db.get_columns(self.table_name)
                fks = db.get_foreign_keys(self.table_name)
                try:
                    idxs = db.get_indexes(self.table_name)
                except Exception:
                    idxs = []
            except Exception as ex:
                sig_error.emit(str(ex))
            else:
                sig_done.emit((cols, idxs, fks))

        threading.Thread(target=_worker, daemon=True).start()

    def _apply_structure_result(self, result):
        cols, idxs, fks = result
        _fill_columns_table(self.col_tbl, cols)
        _fill_indexes_table(self.idx_tbl, idxs)
        _fill_fk_table(self.fk_tbl, fks)
        col_i, idx_i, fk_i = self._structure_tab_indices
        self.view_tabs.setTabText(col_i, f"Columns ({len(cols)})")
        self.view_tabs.setTabText(idx_i, f"Indexes ({len(idxs)})")
        self.view_tabs.setTabText(fk_i, f"Foreign Keys ({len(fks)})")

    def _on_structure_load_failed(self, msg: str):
        QMessageBox.warning(self, "Structure", f"Could not load structure:\n{msg}")

    # ─── Structure editor ─────────────────────────────────────────────────────

    def show_structure_editor(self):
        """Show structure editor dialog."""
        # This method is kept for compatibility but not used in this widget
        pass

    def show_alter_table_editor(self, table_name: str):
        """Show alter table editor dialog."""
        # This method is kept for compatibility but not used in this widget
        pass

    # ─── Quick search ─────────────────────────────────────────────────────────

    def show_quick_search(self):
        """Show quick search dialog."""
        # This method is kept for compatibility but not used in this widget
        pass

    def _on_quick_search(self, item_type, item_name):
        """Handle quick search selection."""
        # This method is kept for compatibility but not used in this widget
        pass

    # ─── Query history ────────────────────────────────────────────────────────

    def show_query_history(self):
        """Show query history dialog."""
        # This method is kept for compatibility but not used in this widget
        pass

    # ─── Table filter ─────────────────────────────────────────────────────────

    def filter_tables(self, search_text: str):
        """Filter tables in schema tree (not implemented in this streaming widget)"""
        # This method is kept for compatibility but not used in this widget
        pass

    # ─── Theme ────────────────────────────────────────────────────────────────

    def _apply_pill_style(self):
        """Apply pill style (not used in this widget)"""
        pass

    # ─── Session helpers (called by MainWindow) ───────────────────────────────

    def get_session_tabs(self) -> list:
        """Return serialisable list of open tabs."""
        # This method is kept for compatibility but not used in this widget
        return []

    def restore_session_tabs(self, tabs: list):
        """Reopen tabs from saved session data."""
        # This method is kept for compatibility but not used in this widget
        pass

    # ─── Public helpers ───────────────────────────────────────────────────────

    @property
    def label(self) -> str:
        """Return the table label"""
        return self.table_name

    def disconnect(self):
        """Disconnect from database"""
        try:
            self.db_service.disconnect()
        except Exception:
            pass

    # ─── Data editing ─────────────────────────────────────────────────────────

    def commit_changes(self):
        """Save all changes to the database (Cmd+S). The actual SQL
        execution runs on a background thread (reusing load_table_data's
        _loading guard/overlay, since a save and a page load would
        otherwise contend for the same dedicated connection) so a large
        batch of edits doesn't freeze the UI."""
        logger.info("🔵 Cmd+S pressed - checking for changes...")

        if self._loading:
            return

        if not self.data_table.has_changes():
            logger.info("⚪ No changes to save")
            return

        if self.db_service.read_only:
            logger.info("🔒 Commit blocked — connection is read-only")
            QMessageBox.warning(
                self, "Read-only Connection",
                "This connection is read-only, so QForge blocked these changes "
                "before sending them to the database.\n\n"
                "Turn off Read-only for this connection in the Connection Manager "
                "if you intend to make changes."
            )
            return

        logger.info(f"📝 Found changes: {len(self.data_table.modified_rows)} modified, {len(self.data_table.new_rows)} new, {len(self.data_table.deleted_rows)} deleted")

        # Get SQL statements
        changes = self.data_table.get_changes()
        if not changes:
            logger.warning("⚠️ has_changes() returned True but get_changes() returned None")
            return

        logger.info(f"📊 Generated SQL: {len(changes['updates'])} UPDATEs, {len(changes['inserts'])} INSERTs, {len(changes['deletes'])} DELETEs")

        self._loading = True
        self.loading_progress_bar.show()

        def _worker():
            try:
                result = self._run_commit_sql(changes)
            except Exception as ex:
                self._commit_errored.emit(str(ex))
            else:
                self._commit_done.emit(result)

        threading.Thread(target=_worker, daemon=True).start()

    def _run_commit_sql(self, changes):
        """Runs on the background thread started by commit_changes() —
        executes the staged DELETE/UPDATE/INSERT statements against the
        dedicated connection and returns (errors, success_count)."""
        db = self._get_worker_db()
        errors = []
        success_count = 0

        # Execute DELETEs first
        for sql in changes['deletes']:
            try:
                affected = db.execute_update(sql)
                if affected == 0:
                    raise Exception("matched 0 rows — the row may have already changed or its key no longer matches")
                success_count += 1
                logger.info(f"✓ DELETE: {sql}")
            except Exception as e:
                errors.append({"kind": "DELETE", "sql": sql, "error": str(e)})
                logger.error(f"✗ DELETE failed: {sql} - {str(e)}")

        # Then UPDATEs
        for sql in changes['updates']:
            try:
                affected = db.execute_update(sql)
                if affected == 0:
                    raise Exception("matched 0 rows — the row may have already changed or its key no longer matches")
                success_count += 1
                logger.info(f"✓ UPDATE: {sql}")
            except Exception as e:
                errors.append({"kind": "UPDATE", "sql": sql, "error": str(e)})
                logger.error(f"✗ UPDATE failed: {sql} - {str(e)}")

        # Finally INSERTs
        for sql in changes['inserts']:
            try:
                db.execute_update(sql)
                success_count += 1
                logger.info(f"✓ INSERT: {sql}")
            except Exception as e:
                errors.append({"kind": "INSERT", "sql": sql, "error": str(e)})
                logger.error(f"✗ INSERT failed: {sql} - {str(e)}")

        return errors, success_count

    def _apply_commit_result(self, result):
        """Main-thread handler for commit_changes()'s worker `_commit_done`
        signal."""
        self._finish_loading()
        errors, success_count = result

        # Only show a dialog if there are errors (issue #143: structured
        # summary + per-failure classification instead of the raw
        # exception text as the primary message)
        if errors:
            show_save_errors(self, success_count, errors)
        else:
            # Success - log only, no popup
            logger.info(f"✓✓✓ Saved {success_count} changes successfully")

        # Reload table data to show saved changes — these were just
        # persisted, not discarded, so skip the discard-warning toast.
        self.reset_and_load_first_page(warn=False)
        # Notify parent tab is now clean
        self.dirty_changed.emit(False)

    def _on_commit_failed(self, msg):
        """Main-thread handler for commit_changes()'s worker
        `_commit_errored` signal — an exception outside the per-statement
        try/excepts."""
        self._finish_loading()
        QMessageBox.critical(
            self,
            "Save Error",
            f"Failed to save:\\n{msg}"
        )
        logger.error(f"Save failed: {msg}")