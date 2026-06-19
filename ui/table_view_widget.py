from PySide6.QtCore import Qt, Signal, QObject, QRunnable, Slot
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
)
from PySide6.QtGui import QShortcut, QKeySequence, QCursor
from ui.advanced_filter_dialog import AdvancedFilterDialog
from ui.theme_manager import ThemeManager

from ui.sql_tab import SqlTab
from utils.logger import get_logger
import pandas as pd

logger = get_logger()


# Worker for loading table data in a separate thread
class TableDataLoaderSignals(QObject):
    finished = Signal(object, object)  # dataframe, error
    progress = Signal(int)  # percentage


class TableDataLoader(QRunnable):
    def __init__(self, db_service, table_name, current_filter="",
                 sort_column=None, sort_order=None, page=1, page_size=100):
        super().__init__()
        self.db_service = db_service
        self.table_name = table_name
        self.current_filter = current_filter
        self.sort_column = sort_column
        self.sort_order = sort_order
        self.page = page
        self.page_size = page_size
        self.signals = TableDataLoaderSignals()
        self.is_cancelled = False

    @Slot()
    def run(self):
        try:
            # Build query with pagination
            offset = (self.page - 1) * self.page_size
            order_clause = ""
            if self.sort_column:
                order_clause = f" ORDER BY {self.sort_column} {self.sort_order}"

            if self.current_filter:
                query = f"SELECT * FROM {self.table_name} WHERE {self.current_filter}{order_clause} LIMIT {self.page_size} OFFSET {offset}"
            else:
                query = f"SELECT * FROM {self.table_name}{order_clause} LIMIT {self.page_size} OFFSET {offset}"

            # Execute query
            df = self.db_service.execute_query(query)

            if not self.is_cancelled:
                self.signals.finished.emit(df, None)

        except Exception as e:
            if not self.is_cancelled:
                self.signals.finished.emit(None, e)

    def cancel(self):
        self.is_cancelled = True


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

        # Streaming data attributes
        self.accumulated_data = None   # pandas DataFrame of all loaded rows
        self.next_page = 1             # next page to load (1-indexed)
        self.total_rows = None         # total rows in the table (if known)
        self.page_size = 100           # rows per page for loading
        self.is_loading = False        # to prevent multiple concurrent loads
        self.threadpool = QThreadPool()
        self.current_loader = None

        self.init_ui()

        # Add Cmd+F shortcut for filter
        self.filter_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        self.filter_shortcut.activated.connect(self.toggle_filter)

        # Add Esc shortcut to close filter
        self.esc_shortcut = QShortcut(QKeySequence("Esc"), self)
        self.esc_shortcut.activated.connect(self.hide_filter)

        # Add Cmd+S shortcut to save changes
        self.save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        self.save_shortcut.activated.connect(self.save_changes)

        # Load first page of data
        self.reset_and_load_first_page()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

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

        # Enable column sorting on header click
        self.data_table.horizontalHeader().sectionClicked.connect(self.on_column_header_clicked)

        table_layout.addWidget(self.data_table)

        # Bottom controls - row count center, load more button right
        bottom_controls = QHBoxLayout()
        bottom_controls.setContentsMargins(3, 3, 3, 3)
        bottom_controls.setSpacing(3)

        bottom_controls.addStretch()

        # Row count in center
        self.limit_label = QLabel("")
        self.limit_label.setStyleSheet("color: gray; font-size: 11px;")
        bottom_controls.addWidget(self.limit_label)

        bottom_controls.addStretch()

        # Load more button on right
        self.load_more_btn = QPushButton("Load More Rows")
        self.load_more_btn.clicked.connect(self.load_more_rows)
        self.load_more_btn.setStyleSheet("""
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
        """)
        bottom_controls.addWidget(self.load_more_btn)

        layout.addWidget(table_container)
        layout.addLayout(bottom_controls)

        # Connect resize event to update loading overlay geometry
        table_container.resized.connect(self._update_loading_overlay_geometry)
        self.data_table.resized.connect(self._update_loading_overlay_geometry)

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

    def reset_and_load_first_page(self):
        """Reset accumulated data and load the first page"""
        # Cancel any ongoing load
        if self.current_loader:
            self.current_loader.cancel()

        # Reset streaming state
        self.accumulated_data = None
        self.next_page = 1
        self.total_rows = None
        self.is_loading = False

        # Get total row count for the current filter (for display and knowing when to stop)
        self._update_total_rows()

        # Load first page
        self.load_page(self.next_page)

    def _update_total_rows(self):
        """Get total row count for current filter and sort"""
        try:
            if self.current_filter:
                count_query = f"SELECT COUNT(*) as total FROM {self.table_name} WHERE {self.current_filter}"
            else:
                count_query = f"SELECT COUNT(*) as total FROM {self.table_name}"

            count_df = self.db_service.execute_query(count_query)
            if len(count_df) > 0:
                self.total_rows = int(count_df.iloc[0]['total'])
            else:
                self.total_rows = 0
        except Exception as ex:
            logger.error(f"Failed to get total row count: {str(ex)}")
            self.total_rows = None  # Unknown total

    def load_page(self, page_num):
        """Load a specific page of data"""
        if self.is_loading:
            return  # Prevent concurrent loads

        self.is_loading = True
        self.loading_overlay.show()
        self.loading_overlay.raise_()
        self.load_more_btn.setEnabled(False)

        # Create and start worker
        self.current_loader = TableDataLoader(
            self.db_service, self.table_name, self.current_filter,
            self.sort_column, self.sort_order, page_num, self.page_size
        )
        self.current_loader.signals.finished.connect(self._on_page_loaded)
        self.threadpool.start(self.current_loader)

    def _on_page_loaded(self, df, error):
        """Handle completion of page loading"""
        self.is_loading = False
        self.loading_overlay.hide()
        self.load_more_btn.setEnabled(True)

        if error:
            self.limit_label.setText(f"Error: {str(error)}")
            logger.error(f"Failed to load page: {str(error)}")
            return

        # Accumulate data
        if self.accumulated_data is None:
            self.accumulated_data = df
        else:
            self.accumulated_data = pd.concat([self.accumulated_data, df], ignore_index=True)

        # Update table view with accumulated data
        self.data_table.load_data(self.accumulated_data, table_name=self.table_name)

        # Update UI
        self._update_ui_after_load(len(df))

        # Prepare for next page
        self.next_page += 1

        # Check if we've loaded all rows
        if self.total_rows is not None and len(self.accumulated_data) >= self.total_rows:
            self.load_more_btn.setEnabled(False)
            self.load_more_btn.setText("All Rows Loaded")
        elif len(df) < self.page_size:
            # Last page had less than a full page of results
            self.load_more_btn.setEnabled(False)
            self.load_more_btn.setText("No More Rows")
        else:
            self.load_more_btn.setEnabled(True)
            self.load_more_btn.setText("Load More Rows")

    def _update_ui_after_load(self, newly_loaded_rows):
        """Update labels and buttons after loading a page"""
        loaded_rows = len(self.accumulated_data) if self.accumulated_data is not None else 0

        if self.total_rows is not None:
            self.limit_label.setText(
                f"Showing {loaded_rows} rows (of {self.total_rows} total)"
            )
        else:
            self.limit_label.setText(f"Showing {loaded_rows} rows")

    def load_more_rows(self):
        """Load the next page and append to current view"""
        if not self.is_loading:
            self.load_page(self.next_page)

    # ─── Filtering ─────────────────────────────────────────────────────────────

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