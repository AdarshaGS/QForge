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
)
from PySide6.QtGui import QShortcut, QKeySequence, QCursor
from ui.advanced_filter_dialog import AdvancedFilterDialog
from ui.theme_manager import ThemeManager

from ui.sql_tab import SqlTab
from utils.logger import get_logger
from utils import perf_metrics
import pandas as pd
import time

logger = get_logger()


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

    def __init__(self, db_service, table_name, parent=None):
        super().__init__(parent)

        self.db_service = db_service
        self.table_name = table_name
        self.current_filter = ""
        self.columns = []
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
        self.page_size = 100           # rows per page

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

        # Data table with loading overlay
        table_container = QWidget()
        table_layout = QVBoxLayout(table_container)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.setSpacing(0)

        # Loading overlay
        self.loading_overlay = QWidget(table_container)
        self.loading_overlay.setObjectName("loadingOverlay")
        self.loading_overlay.setGeometry(table_container.rect())
        self.loading_overlay.hide()

        # Loading spinner label
        self.loading_label = QLabel("Loading...", self.loading_overlay)
        self.loading_label.setAlignment(Qt.AlignCenter)
        self.loading_label.setStyleSheet("""
            QLabel {
                color: white;
                font-size: 16px;
                background: rgba(0, 0, 0, 150);
                border-radius: 8px;
                padding: 20px;
            }
        """)
        # Center the label in the overlay
        self.loading_label.setAttribute(Qt.WA_TransparentForMouseEvents)

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

    def _update_loading_overlay_geometry(self):
        """Update loading overlay to cover the table container"""
        if hasattr(self, 'loading_overlay') and self.loading_overlay:
            # Find the table container parent
            parent = self.loading_overlay.parent()
            if parent:
                self.loading_overlay.setGeometry(0, 0, parent.width(), parent.height())
                # Center the loading label
                if hasattr(self, 'loading_label') and self.loading_label:
                    label_width = self.loading_label.width()
                    label_height = self.loading_label.height()
                    parent_width = parent.width()
                    parent_height = parent.height()
                    self.loading_label.setGeometry(
                        (parent_width - label_width) // 2,
                        (parent_height - label_height) // 2,
                        label_width,
                        label_height
                    )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_loading_overlay_geometry()

    def update_theme(self, is_dark=True):
        """Update filter container theme"""
        if is_dark:
            style = ThemeManager.get_filter_container_style_dark()
        else:
            style = ThemeManager.get_filter_container_style_light()
        self.filter_container.setStyleSheet(style)

        # Update data table theme
        if hasattr(self, 'data_table'):
            self.data_table.update_theme(is_dark)

        # Update loading overlay theme
        if hasattr(self, 'loading_overlay'):
            self.loading_overlay.setStyleSheet("""
                QWidget#loadingOverlay {
                    background-color: rgba(0, 0, 0, 180);
                }
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

        # Reset and reload with new sort settings
        self.reset_and_load_first_page()

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

    def reset_and_load_first_page(self):
        """Reset to the first page and load it"""
        self._warn_and_discard_changes()
        self.current_page = 1
        self.total_rows = None
        self.load_table_data()

    def load_table_data(self):
        """Load the current page for current filter and sort, refreshing row count"""
        _page_load_t0 = time.perf_counter()
        _page_load_wall_start = time.time()
        try:
            # A real COUNT(*) — MySQL's INFORMATION_SCHEMA.TABLES.TABLE_ROWS
            # looked appealingly fast, but it's only an approximate
            # statistic that goes stale by any margin after inserts/deletes
            # since the last ANALYZE TABLE, which silently capped
            # pagination or hid real rows outright. A real count costs a
            # bit more but is the only accurate answer, and Postgres/SQLite
            # already paid this cost, so this just makes MySQL consistent.
            try:
                where_clause = f" WHERE {self.current_filter}" if self.current_filter else ""
                count_query = f"SELECT COUNT(*) as total FROM {self.table_name}{where_clause}"  # nosec B608
                count_df = self.db_service.execute_query(count_query)
                self.total_rows = int(count_df.iloc[0]['total'])
            except Exception as ex:
                logger.debug(f"Row count failed for {self.table_name}: {ex}")
                self.total_rows = 1000000
            
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
            
            df = self.db_service.execute_query(query)
            
            # Store column names for filter - even if table is empty
            if not self.columns:
                # Try to get columns from table structure if data is empty
                if len(df.columns) > 0:
                    self.columns = list(df.columns)
                else:
                    # Fallback: get structure from database
                    try:
                        cols = self.db_service.get_columns(self.table_name)
                        self.columns = [col.get('Field', '') for col in cols]
                    except Exception:
                        pass
                
                if self.columns:
                    # Update all filter row column combos
                    for i in range(self.filter_rows_layout.count()):
                        row_widget = self.filter_rows_layout.itemAt(i).widget()
                        if row_widget:
                            column_combo = row_widget.findChild(QComboBox, "column_combo")
                            if column_combo:
                                current = column_combo.currentText()
                                column_combo.clear()
                                column_combo.addItems(self.columns)
                                if current in self.columns:
                                    column_combo.setCurrentText(current)
            
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
                self.total_rows = max(self.total_rows, min_known_rows)
            elif self.total_rows == 0 and self.columns:
                import pandas as pd
                df = pd.DataFrame(columns=self.columns)
            
            self.data_table.load_data(df, table_name=self.table_name)
            _page_load_ms = (time.perf_counter() - _page_load_t0) * 1000
            # issue #174: a page load spanning a detected system
            # suspend/sleep isn't a real measurement of this operation's
            # cost — don't let it pollute page_load's mean/p95 with a
            # number that has nothing to do with query or render speed.
            if perf_metrics.likely_suspended_between(_page_load_wall_start, time.time()):
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
        except Exception as ex:
            logger.error(f"Failed to load table data: {str(ex)}")
            self.limit_label.setText(f"Error: {str(ex)}")
            self._load_failed = True

    def reload_if_errored(self):
        """Retry the current page if the last load attempt failed (issue
        #176) — e.g. this tab was restored/opened before the connection
        was actually live. A no-op for a tab that's already showing real
        data, so it's safe to call on every open TableViewWidget whenever
        a (re)connect completes."""
        if self._load_failed:
            self.load_table_data()

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

        # Column selector
        column_combo = QComboBox()
        column_combo.setObjectName("column_combo")
        column_combo.setMinimumWidth(120)
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
        """Toggle filter visibility with Cmd+F"""
        self.filter_visible = not self.filter_visible
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
        self._structure_loaded = True
        try:
            cols = self.db_service.get_columns(self.table_name)
            fks = self.db_service.get_foreign_keys(self.table_name)
            try:
                idxs = self.db_service.get_indexes(self.table_name)
            except Exception:
                idxs = []
        except Exception as ex:
            QMessageBox.warning(self, "Structure", f"Could not load structure:\n{ex}")
            return
        _fill_columns_table(self.col_tbl, cols)
        _fill_indexes_table(self.idx_tbl, idxs)
        _fill_fk_table(self.fk_tbl, fks)
        col_i, idx_i, fk_i = self._structure_tab_indices
        self.view_tabs.setTabText(col_i, f"Columns ({len(cols)})")
        self.view_tabs.setTabText(idx_i, f"Indexes ({len(idxs)})")
        self.view_tabs.setTabText(fk_i, f"Foreign Keys ({len(fks)})")

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
        """Save all changes to the database (Cmd+S)"""
        logger.info("🔵 Cmd+S pressed - checking for changes...")

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

        try:
            # Execute all statements silently
            errors = []
            success_count = 0

            # Execute DELETEs first
            for sql in changes['deletes']:
                try:
                    self.db_service.execute_update(sql)
                    success_count += 1
                    logger.info(f"✓ DELETE: {sql}")
                except Exception as e:
                    errors.append(f"DELETE: {str(e)}")
                    logger.error(f"✗ DELETE failed: {sql} - {str(e)}")

            # Then UPDATEs
            for sql in changes['updates']:
                try:
                    self.db_service.execute_update(sql)
                    success_count += 1
                    logger.info(f"✓ UPDATE: {sql}")
                except Exception as e:
                    errors.append(f"UPDATE: {str(e)}")
                    logger.error(f"✗ UPDATE failed: {sql} - {str(e)}")

            # Finally INSERTs
            for sql in changes['inserts']:
                try:
                    self.db_service.execute_update(sql)
                    success_count += 1
                    logger.info(f"✓ INSERT: {sql}")
                except Exception as e:
                    errors.append(f"INSERT: {str(e)}")
                    logger.error(f"✗ INSERT failed: {sql} - {str(e)}")

            # Only show message if there are errors
            if errors:
                QMessageBox.warning(
                    self,
                    "Save Errors",
                    f"Saved {success_count} changes, but {len(errors)} failed:\\n\\n" +
                    "\\n".join(errors[:3])
                )
            else:
                # Success - log only, no popup
                logger.info(f"✓✓✓ Saved {success_count} changes successfully")

            # Reload table data to show saved changes
            self.reset_and_load_first_page()
            # Notify parent tab is now clean
            self.dirty_changed.emit(False)

        except Exception as e:
            QMessageBox.critical(
                self,
                "Save Error",
                f"Failed to save:\\n{str(e)}"
            )
            logger.error(f"Save failed: {str(e)}")