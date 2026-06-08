from PySide6.QtCore import Qt, Signal, QEvent
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTabWidget,
    QStackedWidget,
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
from utils import col_widths as _cw

logger = get_logger()


class TableViewWidget(QWidget):
    """Widget that shows a table with 3 tabs: Data, Structure, Query Editor"""
    
    execute_query_signal = Signal(object)  # Signal to execute query in query editor tab
    dirty_changed = Signal(bool)           # True = unsaved changes, False = clean
    
    def __init__(self, db_service, table_name, parent=None):
        super().__init__(parent)
        
        self.db_service = db_service
        self.table_name = table_name
        self.current_filter = ""
        self.columns = []
        self.current_page = 1
        self.page_size = 100
        self.total_rows = 0
        self.filter_visible = False
        self.sort_column = None
        self.sort_order = None  # 'DESC' or 'ASC'
        self.filter_conditions = []  # List of (column, operator, value) tuples
        
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
        
    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Stacked content: index 0 = Data, index 1 = Structure ────────────
        self._view_stack = QStackedWidget()
        self.data_tab = self.create_data_tab()
        self.struct_tab = self.create_structure_tab()
        self._view_stack.addWidget(self.data_tab)    # 0
        self._view_stack.addWidget(self.struct_tab)  # 1
        layout.addWidget(self._view_stack)

        # ── Bottom bar ───────────────────────────────────────────────────────
        _BTN_ACTIVE = (
            "QPushButton { background:#0a7aff; color:#fff; border:none;"
            " border-radius:4px; padding:3px 10px; font-size:12px;"
            " font-weight:600; }"
        )
        _BTN_INACTIVE = (
            "QPushButton { background:transparent; color:#aaaaaa; border:none;"
            " border-radius:4px; padding:3px 10px; font-size:12px; }"
            " QPushButton:hover { background:#3a3a3c; color:#ffffff; }"
        )

        bottom_bar = QWidget()
        bottom_bar.setFixedHeight(32)
        bb = QHBoxLayout(bottom_bar)
        bb.setContentsMargins(6, 0, 6, 0)
        bb.setSpacing(2)

        self._btn_data = QPushButton("Data")
        self._btn_structure = QPushButton("Structure")
        self._btn_add_row = QPushButton("+ Row")
        self._btn_data.setStyleSheet(_BTN_ACTIVE)
        self._btn_structure.setStyleSheet(_BTN_INACTIVE)
        self._btn_add_row.setStyleSheet(_BTN_INACTIVE)
        self._btn_data.clicked.connect(self._show_data_view)
        self._btn_structure.clicked.connect(self._show_structure_view)
        self._btn_add_row.clicked.connect(lambda: self.data_table.add_new_row())

        bb.addWidget(self._btn_data)
        bb.addWidget(self._btn_structure)
        bb.addSpacing(8)
        bb.addWidget(self._btn_add_row)
        bb.addStretch()

        # Row count (center)
        self.limit_label = QLabel("")
        self.limit_label.setStyleSheet("color: gray; font-size: 11px;")
        bb.addWidget(self.limit_label)
        bb.addStretch()

        # Pagination (right)
        self.prev_btn = QPushButton("<")
        self.prev_btn.clicked.connect(self.prev_page)
        self.prev_btn.setEnabled(False)
        self.prev_btn.setFixedWidth(30)
        self.prev_btn.setStyleSheet("""
            QPushButton {
                background-color: #2d2d2d;
                color: #cccccc;
                border: 1px solid #404040;
                border-radius: 3px;
                padding: 5px;
                font-size: 12px;
            }
            QPushButton:hover:enabled {
                background-color: #3d3d3d;
                border-color: #505050;
            }
            QPushButton:pressed {
                background-color: #1d1d1d;
            }
            QPushButton:disabled {
                background-color: #1e1e1e;
                color: #666666;
                border-color: #2d2d2d;
            }
        """)

        self.page_label = QLabel("Page 1")
        self.page_label.setStyleSheet("color: #cccccc; font-size: 11px; margin: 0 8px;")

        self.next_btn = QPushButton(">")
        self.next_btn.clicked.connect(self.next_page)
        self.next_btn.setFixedWidth(30)
        self.next_btn.setStyleSheet("""
            QPushButton {
                background-color: #2d2d2d;
                color: #cccccc;
                border: 1px solid #404040;
                border-radius: 3px;
                padding: 5px;
                font-size: 12px;
            }
            QPushButton:hover:enabled {
                background-color: #3d3d3d;
                border-color: #505050;
            }
            QPushButton:pressed {
                background-color: #1d1d1d;
            }
            QPushButton:disabled {
                background-color: #1e1e1e;
                color: #666666;
                border-color: #2d2d2d;
            }
        """)

        bb.addWidget(self.prev_btn)
        bb.addWidget(self.page_label)
        bb.addWidget(self.next_btn)

        layout.addWidget(bottom_bar)

        # Load data initially
        self.load_table_data()
        
    def create_data_tab(self):
        """Create the data viewing tab"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        
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
        
        # Data table
        from ui.editable_table import EditableTableWidget
        self.data_table = EditableTableWidget()
        
        # Server-side sort owns this table; disconnect the widget's built-in
        # client-side sort handler to prevent the double-fire that corrupts state.
        try:
            self.data_table.horizontalHeader().sectionClicked.disconnect(
                self.data_table.on_header_clicked
            )
        except Exception:
            pass

        # Enable column sorting on header click
        self.data_table.horizontalHeader().sectionClicked.connect(self.on_column_header_clicked)

        # Column-width memory: save whenever user resizes a column
        self.data_table.horizontalHeader().sectionResized.connect(self._on_column_resized)

        # Column stats tooltip on header hover
        self.data_table.horizontalHeader().viewport().installEventFilter(self)
        
        layout.addWidget(self.data_table)

        # Notify parent when data is modified
        self.data_table.changes_made.connect(lambda: self.dirty_changed.emit(True))
        
        return widget
    
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

    # ── Bottom-bar view switching ────────────────────────────────────────────
    _BTN_ACTIVE = (
        "QPushButton { background:#0a7aff; color:#fff; border:none;"
        " border-radius:4px; padding:3px 10px; font-size:12px; font-weight:600; }"
    )
    _BTN_INACTIVE = (
        "QPushButton { background:transparent; color:#aaaaaa; border:none;"
        " border-radius:4px; padding:3px 10px; font-size:12px; }"
        " QPushButton:hover { background:#3a3a3c; color:#ffffff; }"
    )

    def _show_data_view(self):
        self._view_stack.setCurrentIndex(0)
        self._btn_data.setStyleSheet(self._BTN_ACTIVE)
        self._btn_structure.setStyleSheet(self._BTN_INACTIVE)
        for w in (self.prev_btn, self.page_label, self.next_btn, self.limit_label):
            w.show()

    def _show_structure_view(self):
        self._view_stack.setCurrentIndex(1)
        self._btn_data.setStyleSheet(self._BTN_INACTIVE)
        self._btn_structure.setStyleSheet(self._BTN_ACTIVE)
        for w in (self.prev_btn, self.page_label, self.next_btn, self.limit_label):
            w.hide()
        self.load_table_structure()

    def create_structure_tab(self):
        """Create the structure viewing tab"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Structure table
        self.structure_table = QTableWidget()
        self.structure_table.setColumnCount(6)
        self.structure_table.setHorizontalHeaderLabels([
            "Column", "Type", "Nullable", "Key", "Default", "Extra"
        ])
        self.structure_table.horizontalHeader().setStretchLastSection(True)
        self.structure_table.setAlternatingRowColors(True)
        
        layout.addWidget(self.structure_table)
        
        return widget
    
    def load_table_data(self):
        """Load data from the table with pagination - OPTIMIZED for large tables"""
        try:
            # PERFORMANCE FIX: Skip COUNT for large tables, use fast approximate
            # Just fetch data directly with LIMIT - much faster for tables with millions of rows
            if self.current_filter:
                # For filtered queries, still need accurate count
                count_query = f"SELECT COUNT(*) as total FROM {self.table_name} WHERE {self.current_filter}"
                count_df = self.db_service.execute_query(count_query)
                self.total_rows = int(count_df.iloc[0]['total'])
            else:
                # For large unfiltered tables, use approximation or skip count
                try:
                    # Try to get approximate count (MySQL specific - very fast)
                    if self.db_service.db_type == 'mysql':
                        approx_query = f"SELECT TABLE_ROWS FROM information_schema.TABLES WHERE TABLE_NAME = '{self.table_name}' AND TABLE_SCHEMA = DATABASE()"
                        approx_df = self.db_service.execute_query(approx_query)
                        if len(approx_df) > 0 and approx_df.iloc[0]['TABLE_ROWS'] is not None:
                            self.total_rows = int(approx_df.iloc[0]['TABLE_ROWS'])
                        else:
                            # Fallback: set to large number
                            self.total_rows = 1000000
                    else:
                        # For other DBs, use COUNT but with timeout protection
                        count_query = f"SELECT COUNT(*) as total FROM {self.table_name}"
                        count_df = self.db_service.execute_query(count_query)
                        self.total_rows = int(count_df.iloc[0]['total'])
                except:
                    # If approximate fails, set to large number to allow pagination
                    self.total_rows = 1000000
            
            # Calculate offset
            offset = (self.current_page - 1) * self.page_size
            
            # Build ORDER BY clause
            order_clause = ""
            if self.sort_column:
                order_clause = f" ORDER BY {self.sort_column} {self.sort_order}"
            
            # Build query with pagination
            if self.current_filter:
                query = f"SELECT * FROM {self.table_name} WHERE {self.current_filter}{order_clause} LIMIT {self.page_size} OFFSET {offset}"
            else:
                query = f"SELECT * FROM {self.table_name}{order_clause} LIMIT {self.page_size} OFFSET {offset}"
            
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
            
            # For empty tables, create empty DataFrame with column structure
            if self.total_rows == 0 and self.columns:
                import pandas as pd
                # Create empty DataFrame with columns to show structure
                df = pd.DataFrame(columns=self.columns)
            
            self.data_table.load_data(df, table_name=self.table_name)
            self._restore_col_widths()
            total_pages = (self.total_rows + self.page_size - 1) // self.page_size
            self.page_label.setText(f"Page {self.current_page}/{max(1, total_pages)}")
            
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
        except Exception as ex:
            logger.error(f"Failed to load table data: {str(ex)}")
            self.limit_label.setText(f"Error: {str(ex)}")
    
    def prev_page(self):
        """Load previous page"""
        if self.current_page > 1:
            self.current_page -= 1
            self.load_table_data()
    
    def next_page(self):
        """Load next page"""
        total_pages = (self.total_rows + self.page_size - 1) // self.page_size
        if self.current_page < total_pages:
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
        operator_combo.addItems(["CONTAINS", "=", "!=", ">", ">=", "<", "<=", "LIKE", "NOT LIKE", "IN", "IS NULL", "IS NOT NULL"])
        operator_combo.setMinimumWidth(100)
        operator_combo.currentTextChanged.connect(lambda op, v=None: self.on_operator_changed_multi(operator_combo, row_widget))
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
        
        # Remove button (only show if more than 1 filter)
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
    
    def on_operator_changed_multi(self, operator_combo, row_widget):
        """Handle operator change for multi-filter rows"""
        operator = operator_combo.currentText()
        value_input = row_widget.findChild(QLineEdit, "value_input")
        
        if value_input:
            if operator in ["IS NULL", "IS NOT NULL"]:
                value_input.setEnabled(False)
                value_input.clear()
            else:
                value_input.setEnabled(True)
    
    def apply_all_filters(self):
        """Apply all filter conditions"""
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
            elif operator == "LIKE" or operator == "NOT LIKE":
                # Expect user to provide wildcards
                if not (value.startswith("'") and value.endswith("'")):
                    value = f"'{value}'"
                filter_conditions.append(f"{column} {operator} {value}")
            elif operator == "IN":
                # Expect comma-separated values
                filter_conditions.append(f"{column} {operator} ({value})")
            else:
                # Numeric or string comparison
                if not value.replace(".", "", 1).replace("-", "", 1).isdigit():
                    if not (value.startswith("'") and value.endswith("'")):
                        value = f"'{value}'"
                filter_conditions.append(f"{column} {operator} {value}")
        
        # Combine all conditions with AND
        if filter_conditions:
            self.current_filter = " AND ".join(filter_conditions)
            self.current_page = 1
            self.load_table_data()
    
    def clear_all_filters(self):
        """Clear all filters and reset"""
        self.current_filter = ""
        self.current_page = 1
        
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
        
        self.load_table_data()
    
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
        
        dialog = AdvancedFilterDialog(self.columns, self)
        if dialog.exec():
            condition = dialog.get_filter_condition()
            if condition:
                self.current_filter = condition
                self.current_page = 1
                self.load_table_data()
    
    def load_table_structure(self):
        """Load table structure"""
        try:
            columns = self.db_service.get_columns(self.table_name)
            
            self.structure_table.setRowCount(len(columns))
            
            for i, col in enumerate(columns):
                # MySQL uses: Field, Type, Null, Key, Default, Extra
                # PostgreSQL/SQLite converted to similar format
                self.structure_table.setItem(i, 0, QTableWidgetItem(str(col.get('Field', ''))))
                self.structure_table.setItem(i, 1, QTableWidgetItem(str(col.get('Type', ''))))
                self.structure_table.setItem(i, 2, QTableWidgetItem(str(col.get('Null', ''))))
                self.structure_table.setItem(i, 3, QTableWidgetItem(str(col.get('Key', ''))))
                self.structure_table.setItem(i, 4, QTableWidgetItem(str(col.get('Default', ''))))
                self.structure_table.setItem(i, 5, QTableWidgetItem(str(col.get('Extra', ''))))
            
            logger.info(f"Loaded structure for {self.table_name}: {len(columns)} columns")
        except Exception as ex:
            logger.error(f"Failed to load table structure: {str(ex)}")
    
    def execute_query_editor(self):
        """Execute query from the query editor tab - removed since no query tab"""
        pass
    
    def set_schema(self, schema_data):
        """Update the query tab with schema information - removed since no query tab"""
        pass
    
    def on_column_header_clicked(self, logical_index):
        """Handle column header click for sorting"""
        if not self.columns or logical_index >= len(self.columns):
            return
        
        column_name = self.columns[logical_index]
        
        # Toggle sort order: first click DESC (latest to earliest), second click ASC, third click remove sort
        if self.sort_column == column_name:
            if self.sort_order == 'DESC':
                self.sort_order = 'ASC'
            elif self.sort_order == 'ASC':
                # Remove sorting
                self.sort_column = None
                self.sort_order = None
            else:
                self.sort_order = 'DESC'
        else:
            # New column - start with DESC
            self.sort_column = column_name
            self.sort_order = 'DESC'
        
        # Reload data with new sort
        self.current_page = 1
        self.load_table_data()
        
        logger.info(f"Sorting by {column_name} {self.sort_order if self.sort_order else 'NONE'}")
    
    def get_table_name(self):
        """Return the table name"""
        return self.table_name

    def filter_by_column_value(self, col_name: str, value: str):
        """Instantly apply a WHERE col = value filter (column-value filter chip)."""
        # Set the search box text to trigger the existing search/filter path
        if hasattr(self, 'search_input'):
            # Build a simple pseudo-filter: col:value
            self.search_input.setText(f"{col_name}:{value}")
        # If there's a direct filter row mechanism, populate it too
        if hasattr(self, '_apply_column_filter'):
            self._apply_column_filter(col_name, value)

    def save_changes(self):
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
            self.load_table_data()
            # Notify parent tab is now clean
            self.dirty_changed.emit(False)

        except Exception as e:
            QMessageBox.critical(
                self,
                "Save Error",
                f"Failed to save:\\n{str(e)}"
            )
            logger.error(f"Save failed: {str(e)}")

    # ── Column width memory ───────────────────────────────────────────────────

    def _restore_col_widths(self):
        """Apply saved column widths after loading data."""
        widths = _cw.load().get(self.table_name, {})
        if not widths:
            return
        hdr = self.data_table.horizontalHeader()
        for i, col in enumerate(self.columns):
            if col in widths:
                hdr.resizeSection(i, widths[col])

    def _on_column_resized(self, logical_idx: int, _old: int, new_w: int):
        """Persist column width whenever the user drags a header separator."""
        if not self.columns or logical_idx >= len(self.columns):
            return
        all_w = _cw.load()
        tbl = all_w.get(self.table_name, {})
        tbl[self.columns[logical_idx]] = new_w
        all_w[self.table_name] = tbl
        _cw.save(all_w)

    # ── Column stats tooltip ──────────────────────────────────────────────────

    def eventFilter(self, obj, event):
        """Show column statistics tooltip when hovering over a header section."""
        if (obj is self.data_table.horizontalHeader().viewport()
                and event.type() == QEvent.ToolTip):
            hdr = self.data_table.horizontalHeader()
            logical = hdr.logicalIndexAt(event.pos())
            if 0 <= logical < len(self.columns):
                tip = self._col_stats_tooltip(self.columns[logical], logical)
                if tip:
                    QToolTip.showText(QCursor.pos(), tip, obj)
                    return True
        return super().eventFilter(obj, event)

    def _col_stats_tooltip(self, col_name: str, col_idx: int) -> str:
        """Compute min/max/avg/nulls for a column from the loaded data."""
        try:
            import pandas as pd
            df = self.data_table.original_data
            if df is None or df.empty or col_idx >= len(df.columns):
                return ""
            series = df.iloc[:, col_idx]
            total = len(series)
            nulls = int(series.isna().sum())
            lines = [f"<b>{col_name}</b>", f"Rows: {total}  |  Nulls: {nulls}"]
            numeric = pd.to_numeric(series, errors="coerce").dropna()
            if len(numeric) > 0:
                lines.append(
                    f"Min: {numeric.min():.4g}  |  Max: {numeric.max():.4g}  |  Avg: {numeric.mean():.4g}"
                )
            return "<br>".join(lines)
        except Exception:
            return ""
