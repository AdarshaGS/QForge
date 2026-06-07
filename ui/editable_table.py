from PySide6.QtWidgets import (
    QTableWidget, 
    QTableWidgetItem, 
    QHeaderView,
    QMenu,
    QMessageBox
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
import pandas as pd


class EditableTableWidget(QTableWidget):
    """Enhanced table widget with inline editing capabilities"""
    
    filter_changed = Signal()  # Signal when filters change
    changes_made = Signal()  # Signal when data is modified
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.original_data = None
        self.filtered_data = None  # Store filtered version
        self.column_filters = {}  # Store filter text for each column
        self.modified_rows = set()  # Track which rows have been modified
        self.new_rows = set()  # Track newly added rows
        self.deleted_rows = set()  # Track rows marked for deletion
        
        self.table_name = None
        self.primary_key_column = None
        
        # Enable editing
        self.setEditTriggers(QTableWidget.DoubleClicked | QTableWidget.EditKeyPressed)
        
        # Track item changes
        self.itemChanged.connect(self.on_item_changed)
        
        # Context menu
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)
        
        # Add keyboard shortcuts
        from PySide6.QtGui import QShortcut, QKeySequence
        
        # Cmd+D to duplicate row
        self.duplicate_shortcut = QShortcut(QKeySequence("Ctrl+D"), self)
        self.duplicate_shortcut.activated.connect(self.duplicate_selected_rows)
        
        # Styling
        self.verticalHeader().setVisible(False)  # Hide row numbers
        self.setAlternatingRowColors(True)
        # Sorting is handled server-side (via ORDER BY in the query);
        # Qt's built-in client-side sort must be OFF to keep row data intact.
        self.setSortingEnabled(False)
        
        # Connect header click for manual sorting (to avoid breaking modified state)
        self.horizontalHeader().sectionClicked.connect(self.on_header_clicked)
        
        # Disable column header selection highlighting
        self.horizontalHeader().setHighlightSections(False)
        
        # Theme will be set by update_theme method
        self.current_theme = 'dark'
        self.update_theme(is_dark=True)
    
    def update_theme(self, is_dark=True):
        """Update table theme"""
        self.current_theme = 'dark' if is_dark else 'light'
        
        if is_dark:
            # Dark theme
            self.setStyleSheet("""
                QTableWidget {
                    gridline-color: #2c2c2e;
                    background-color: #1c1c1e;
                    alternate-background-color: #202022;
                    color: #e5e5ea;
                    selection-background-color: #0A84FF33;
                    selection-color: #e5e5ea;
                    border: none;
                    outline: none;
                }
                QTableWidget::item { padding: 2px 6px; border: none; }
                QTableWidget::item:selected { background: #0A84FF33; }
                QHeaderView::section {
                    background: #2c2c2e;
                    color: #8e8e93;
                    border: none;
                    border-right: 1px solid #3a3a3c;
                    border-bottom: 1px solid #3a3a3c;
                    padding: 4px 8px;
                    font-size: 12px;
                    font-weight: 600;
                }
                QHeaderView::section:hover { background: #3a3a3c; color: #e5e5ea; }
            """)
        else:
            # Light theme
            self.setStyleSheet("""
                QTableWidget {
                    gridline-color: #d0d0d0;
                    background-color: #ffffff;
                    alternate-background-color: #f8f8f8;
                    color: #000000;
                    selection-background-color: #cce8ff;
                    selection-color: #000000;
                }
                QTableWidget::item:selected {
                    background-color: #cce8ff;
                }
                QHeaderView::section {
                    background-color: #f3f3f3;
                    color: #000000;
                    border: 1px solid #d0d0d0;
                    padding: 5px;
                }
                QHeaderView::section:hover {
                    background-color: #e8e8e8;
                }
            """)
        
    def load_data(self, dataframe: pd.DataFrame, table_name=None):
        """Load data from DataFrame"""
        self.original_data = dataframe.copy() if dataframe is not None else None
        self.filtered_data = dataframe.copy() if dataframe is not None else None
        self.table_name = table_name
        self.modified_rows.clear()
        self.new_rows.clear()
        self.deleted_rows.clear()
        self.column_filters.clear()
        
        self._display_data(dataframe)
    
    def _display_data(self, dataframe):
        """Display dataframe in the table"""
        # Temporarily disconnect itemChanged signal
        self.itemChanged.disconnect(self.on_item_changed)
        
        self.clear()
        self.setRowCount(0)
        
        if dataframe is None:
            self.itemChanged.connect(self.on_item_changed)
            return
        
        # Show columns even for empty tables
        self.setColumnCount(len(dataframe.columns))
        self.setHorizontalHeaderLabels([str(col) for col in dataframe.columns])
        
        if dataframe.empty:
            # For empty tables, show column headers with 10 empty rows (like TablePlus)
            self.setRowCount(10)
            for row in range(10):
                for col in range(len(dataframe.columns)):
                    item = QTableWidgetItem("")
                    self.setItem(row, col, item)
            
            self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
            self.horizontalHeader().setStretchLastSection(True)
            self.itemChanged.connect(self.on_item_changed)
            return
        
        # Display actual data
        self.setRowCount(len(dataframe))
        
        for row in range(len(dataframe)):
            for col in range(len(dataframe.columns)):
                value = dataframe.iloc[row, col]
                
                if pd.isna(value):
                    value = ""
                
                item = QTableWidgetItem(str(value))
                item.setData(Qt.UserRole, dataframe.iloc[row, col])  # Store original value
                self.setItem(row, col, item)
        
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.horizontalHeader().setStretchLastSection(True)
        
        # Reconnect signal
        self.itemChanged.connect(self.on_item_changed)
    
    def apply_column_filter(self, column_index, filter_text):
        """Apply filter to a specific column"""
        if not filter_text:
            if column_index in self.column_filters:
                del self.column_filters[column_index]
        else:
            self.column_filters[column_index] = filter_text.lower()
        
        self._apply_all_filters()
    
    def _apply_all_filters(self):
        """Apply all active column filters"""
        if self.original_data is None or self.original_data.empty:
            return
        
        filtered = self.original_data.copy()
        
        # Apply each column filter
        for col_idx, filter_text in self.column_filters.items():
            if col_idx < len(filtered.columns):
                col_name = filtered.columns[col_idx]
                filtered = filtered[
                    filtered[col_name].astype(str).str.lower().str.contains(filter_text, na=False)
                ]
        
        self.filtered_data = filtered
        self._display_data(filtered)
        self.filter_changed.emit()
    
    def get_filter_status(self):
        """Get current filter status"""
        if self.column_filters:
            return f"{len(self.column_filters)} column filter(s) active"
        return ""
    
    def on_item_changed(self, item):
        """Track when an item is modified"""
        row = item.row()
        
        # Check if this is a formula
        text = item.text()
        if text.startswith('='):
            self.evaluate_formula(item)
            return
        
        if row not in self.new_rows:
            # Check if value actually changed
            original_value = item.data(Qt.UserRole)
            current_value = item.text()
            
            # Convert for comparison
            if current_value == "" and pd.isna(original_value):
                return  # No actual change
            
            if str(original_value) != current_value:
                self.modified_rows.add(row)
                # Highlight modified row with prominent amber/orange
                self.highlight_row(row, QColor(180, 100, 30))  # Bright amber for visibility
                self.changes_made.emit()  # Notify parent
    
    def highlight_row(self, row, color):
        """Highlight a row with a specific color"""
        for col in range(self.columnCount()):
            item = self.item(row, col)
            if item:
                item.setBackground(color)
    
    def add_new_row(self):
        """Add a new empty row"""
        row_count = self.rowCount()
        self.insertRow(row_count)
        
        # Mark as new row
        self.new_rows.add(row_count)
        
        # Create empty items
        for col in range(self.columnCount()):
            item = QTableWidgetItem("")
            item.setData(Qt.UserRole, None)
            self.setItem(row_count, col, item)
        
        # Highlight as new with bright green
        self.highlight_row(row_count, QColor(60, 140, 60))
        self.changes_made.emit()  # Notify parent
        
        # Start editing first cell
        self.editItem(self.item(row_count, 0))
    
    def delete_selected_rows(self):
        """Mark selected rows for deletion"""
        selected_rows = set()
        for item in self.selectedItems():
            selected_rows.add(item.row())
        
        if not selected_rows:
            QMessageBox.warning(self, "Warning", "No rows selected")
            return
        
        reply = QMessageBox.question(
            self,
            "Delete Rows",
            f"Mark {len(selected_rows)} row(s) for deletion?",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            for row in selected_rows:
                self.deleted_rows.add(row)
                # Highlight as deleted with subtle red
                self.highlight_row(row, QColor(70, 50, 50))
            self.changes_made.emit()  # Notify parent
    
    def revert_changes(self):
        """Revert all changes"""
        if self.original_data is not None:
            self.load_data(self.original_data, self.table_name)
    
    def get_changes(self):
        """
        Get all changes as SQL statements
        Returns dict with 'updates', 'inserts', 'deletes' lists
        """
        if not self.table_name:
            return None
        
        changes = {
            'updates': [],
            'inserts': [],
            'deletes': []
        }
        
        # Generate UPDATE statements for modified rows
        for row in self.modified_rows:
            if row in self.deleted_rows or row in self.new_rows:
                continue
            
            set_parts = []
            where_parts = []
            
            for col in range(self.columnCount()):
                col_name = self.horizontalHeaderItem(col).text()
                item = self.item(row, col)
                new_value = item.text()
                
                # Escape and quote string values
                if new_value == "":
                    new_value = "NULL"
                else:
                    new_value = f"'{new_value.replace(chr(39), chr(39)+chr(39))}'"
                
                set_parts.append(f"{col_name} = {new_value}")
                
                # Use original value for WHERE clause
                if col == 0:  # Assume first column is primary key
                    original_value = item.data(Qt.UserRole)
                    if pd.isna(original_value):
                        where_parts.append(f"{col_name} IS NULL")
                    else:
                        where_parts.append(f"{col_name} = '{original_value}'")
            
            if set_parts and where_parts:
                sql = f"UPDATE {self.table_name} SET {', '.join(set_parts)} WHERE {' AND '.join(where_parts)};"
                changes['updates'].append(sql)
        
        # Generate INSERT statements for new rows
        for row in self.new_rows:
            if row in self.deleted_rows:
                continue
            
            columns = []
            values = []
            
            for col in range(self.columnCount()):
                col_name = self.horizontalHeaderItem(col).text()
                item = self.item(row, col)
                value = item.text()
                
                if value != "":
                    columns.append(col_name)
                    values.append(f"'{value.replace(chr(39), chr(39)+chr(39))}'")
            
            if columns:
                sql = f"INSERT INTO {self.table_name} ({', '.join(columns)}) VALUES ({', '.join(values)});"
                changes['inserts'].append(sql)
        
        # Generate DELETE statements
        for row in self.deleted_rows:
            if row in self.new_rows:
                continue
            
            where_parts = []
            for col in range(self.columnCount()):
                col_name = self.horizontalHeaderItem(col).text()
                item = self.item(row, col)
                original_value = item.data(Qt.UserRole)
                
                if pd.isna(original_value):
                    where_parts.append(f"{col_name} IS NULL")
                else:
                    where_parts.append(f"{col_name} = '{original_value}'")
                
                if col == 0:  # Only use first column (primary key)
                    break
            
            if where_parts:
                sql = f"DELETE FROM {self.table_name} WHERE {' AND '.join(where_parts)};"
                changes['deletes'].append(sql)
        
        return changes
    
    def on_header_clicked(self, logical_index):
        """Handle column header click for sorting"""
        # Just toggle sorting - QTableWidget handles the actual sorting
        pass
    
    def has_changes(self):
        """Check if there are any uncommitted changes"""
        return bool(self.modified_rows or self.new_rows or self.deleted_rows)
    
    def show_context_menu(self, position):
        """Show comprehensive context menu like TablePlus"""
        menu = QMenu(self)
        
        # Get current selection
        selected_items = self.selectedItems()
        has_selection = len(selected_items) > 0
        
        if has_selection:
            current_item = self.currentItem()
            
            # Paste
            paste_action = menu.addAction("Paste")
            paste_action.setShortcut("Ctrl+V")
            paste_action.triggered.connect(self.paste_from_clipboard)
            
            # Duplicate
            duplicate_action = menu.addAction("Duplicate")
            duplicate_action.setShortcut("Ctrl+D")
            duplicate_action.triggered.connect(self.duplicate_row)
            
            menu.addSeparator()
            
            # Copy options
            copy_action = menu.addAction("Copy")
            copy_action.setShortcut("Ctrl+C")
            copy_action.triggered.connect(self.copy_to_clipboard)
            
            copy_cell_action = menu.addAction("Copy Cell Value")
            copy_cell_action.triggered.connect(self.copy_cell_value)
            
            copy_column_action = menu.addAction("Copy All Column Values")
            copy_column_action.triggered.connect(self.copy_column_values)
            
            # Copy Row As submenu
            copy_row_menu = menu.addMenu("Copy Row As")
            copy_row_menu.addAction("JSON").triggered.connect(lambda: self.copy_row_as("json"))
            copy_row_menu.addAction("CSV").triggered.connect(lambda: self.copy_row_as("csv"))
            copy_row_menu.addAction("SQL INSERT").triggered.connect(lambda: self.copy_row_as("sql"))
            
            menu.addSeparator()
            
            # Export
            export_action = menu.addAction("Export result...")
            export_action.triggered.connect(self.export_selected)
            
            menu.addSeparator()
            
            # Delete
            delete_action = menu.addAction("Delete")
            delete_action.setShortcut("Delete")
            delete_action.triggered.connect(self.delete_selected_rows)
            
            menu.addSeparator()
            
            # Set NULL
            set_null_action = menu.addAction("Set NULL")
            set_null_action.triggered.connect(self.set_cell_null)
            
            # Set Default
            set_default_action = menu.addAction("Set Default Value")
            set_default_action.triggered.connect(self.set_cell_default)
            
        else:
            # No selection - show row operations
            add_row_action = menu.addAction("Add New Row")
            add_row_action.triggered.connect(self.add_new_row)
        
        menu.addSeparator()
        
        # Always available
        revert_action = menu.addAction("Revert Changes")
        revert_action.triggered.connect(self.revert_changes)
        
        menu.exec_(self.mapToGlobal(position))
    
    def copy_to_clipboard(self):
        """Copy selected cells to clipboard"""
        from PySide6.QtWidgets import QApplication
        selected = self.selectedItems()
        if not selected:
            return
        
        # Get selected range
        rows = sorted(set(item.row() for item in selected))
        cols = sorted(set(item.column() for item in selected))
        
        # Build tab-separated text
        text = []
        for row in rows:
            row_data = []
            for col in cols:
                item = self.item(row, col)
                row_data.append(item.text() if item else "")
            text.append("\t".join(row_data))
        
        QApplication.clipboard().setText("\n".join(text))
    
    def copy_cell_value(self):
        """Copy current cell value"""
        from PySide6.QtWidgets import QApplication
        current = self.currentItem()
        if current:
            QApplication.clipboard().setText(current.text())
    
    def copy_column_values(self):
        """Copy all values from selected column"""
        from PySide6.QtWidgets import QApplication
        current = self.currentItem()
        if not current:
            return
        
        col = current.column()
        values = []
        for row in range(self.rowCount()):
            item = self.item(row, col)
            if item:
                values.append(item.text())
        
        QApplication.clipboard().setText("\n".join(values))
    
    def copy_row_as(self, format_type):
        """Copy row in specified format"""
        from PySide6.QtWidgets import QApplication
        import json
        
        current = self.currentItem()
        if not current:
            return
        
        row = current.row()
        row_data = {}
        
        for col in range(self.columnCount()):
            col_name = self.horizontalHeaderItem(col).text()
            item = self.item(row, col)
            row_data[col_name] = item.text() if item else ""
        
        if format_type == "json":
            text = json.dumps(row_data, indent=2)
        elif format_type == "csv":
            text = ",".join(f'"{v}"' for v in row_data.values())
        elif format_type == "sql":
            cols = ", ".join(row_data.keys())
            vals = ", ".join(f"'{v}'" for v in row_data.values())
            text = f"INSERT INTO {self.table_name or 'table'} ({cols}) VALUES ({vals});"
        else:
            text = str(row_data)
        
        QApplication.clipboard().setText(text)
    
    def paste_from_clipboard(self):
        """Paste from clipboard"""
        from PySide6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        text = clipboard.text()
        
        if not text:
            return
        
        current = self.currentItem()
        if not current:
            return
        
        start_row = current.row()
        start_col = current.column()
        
        # Parse clipboard (tab-separated)
        lines = text.split("\n")
        for i, line in enumerate(lines):
            values = line.split("\t")
            for j, value in enumerate(values):
                row = start_row + i
                col = start_col + j
                if row < self.rowCount() and col < self.columnCount():
                    item = self.item(row, col)
                    if item:
                        item.setText(value)
    
    def duplicate_row(self):
        """Duplicate current row"""
        current = self.currentItem()
        if not current:
            return
        
        source_row = current.row()
        self.insertRow(source_row + 1)
        
        # Copy data
        for col in range(self.columnCount()):
            source_item = self.item(source_row, col)
            if source_item:
                new_item = QTableWidgetItem(source_item.text())
                self.setItem(source_row + 1, col, new_item)
        
        self.new_rows.add(source_row + 1)
        self.highlight_row(source_row + 1, QColor(50, 70, 50))
    
    def export_selected(self):
        """Export selected rows"""
        QMessageBox.information(self, "Export", "Export feature - use Export Data button in toolbar")
    
    def set_cell_null(self):
        """Set current cell to NULL"""
        current = self.currentItem()
        if current:
            current.setText("")
    
    def set_cell_default(self):
        """Set cell to default value"""
        current = self.currentItem()
        if current:
            original = current.data(Qt.UserRole)
            if original is not None:
                current.setText(str(original))
    
    def duplicate_selected_rows(self):
        """Duplicate all selected rows (Cmd+D)"""
        selected_rows = sorted(set(item.row() for item in self.selectedItems()))
        
        if not selected_rows:
            return
        
        # Disconnect signal to avoid multiple triggers
        self.itemChanged.disconnect(self.on_item_changed)
        
        # Duplicate each row from bottom to top to maintain correct indices
        for source_row in reversed(selected_rows):
            # Insert new row after source
            self.insertRow(source_row + 1)
            
            # Copy all cell data
            for col in range(self.columnCount()):
                source_item = self.item(source_row, col)
                if source_item:
                    new_item = QTableWidgetItem(source_item.text())
                    new_item.setData(Qt.UserRole, source_item.data(Qt.UserRole))
                    self.setItem(source_row + 1, col, new_item)
            
            # Mark as new row
            self.new_rows.add(source_row + 1)
            self.highlight_row(source_row + 1, QColor(50, 70, 50))  # Green for new
        
        # Reconnect signal
        self.itemChanged.connect(self.on_item_changed)
    
    def bulk_edit_dialog(self):
        """Show dialog to edit multiple rows at once"""
        selected_rows = sorted(set(item.row() for item in self.selectedItems()))
        
        if len(selected_rows) < 2:
            QMessageBox.information(self, "Bulk Edit", "Please select at least 2 rows to bulk edit")
            return
        
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QLineEdit, QPushButton
        
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Bulk Edit {len(selected_rows)} Rows")
        dialog.setMinimumWidth(400)
        
        layout = QVBoxLayout(dialog)
        
        # Info label
        info = QLabel(f"Edit column value for {len(selected_rows)} selected rows:")
        layout.addWidget(info)
        
        # Column selector
        col_layout = QHBoxLayout()
        col_layout.addWidget(QLabel("Column:"))
        column_combo = QComboBox()
        for col in range(self.columnCount()):
            column_combo.addItem(self.horizontalHeaderItem(col).text())
        col_layout.addWidget(column_combo)
        layout.addLayout(col_layout)
        
        # Value input
        val_layout = QHBoxLayout()
        val_layout.addWidget(QLabel("New Value:"))
        value_input = QLineEdit()
        value_input.setPlaceholderText("Enter value or formula (e.g., =UPPER({value}))")
        val_layout.addWidget(value_input)
        layout.addLayout(val_layout)
        
        # Help text
        help_label = QLabel("Tip: Use {value} to reference current value (e.g., =UPPER({value}))")
        help_label.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(help_label)
        
        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(cancel_btn)
        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(dialog.accept)
        apply_btn.setDefault(True)
        btn_layout.addWidget(apply_btn)
        layout.addLayout(btn_layout)
        
        if dialog.exec_() == QDialog.Accepted:
            column_idx = column_combo.currentIndex()
            new_value = value_input.text()
            
            # Disconnect signal
            self.itemChanged.disconnect(self.on_item_changed)
            
            # Apply to all selected rows
            for row in selected_rows:
                item = self.item(row, column_idx)
                if item:
                    # Check if it's a formula with {value} placeholder
                    if '{value}' in new_value:
                        old_value = item.text()
                        result = new_value.replace('{value}', old_value)
                        if result.startswith('='):
                            # Evaluate formula
                            result = self.evaluate_formula_string(result, old_value)
                        item.setText(result)
                    else:
                        item.setText(new_value)
                    
                    # Mark as modified
                    self.modified_rows.add(row)
                    self.highlight_row(row, QColor(80, 70, 50))
            
            # Reconnect signal
            self.itemChanged.connect(self.on_item_changed)
    
    def evaluate_formula(self, item):
        """Evaluate formula in cell (e.g., =NOW(), =UPPER(text))"""
        formula = item.text()
        if not formula.startswith('='):
            return
        
        result = self.evaluate_formula_string(formula)
        
        # Disconnect to avoid recursion
        self.itemChanged.disconnect(self.on_item_changed)
        item.setText(result)
        self.itemChanged.connect(self.on_item_changed)
    
    def evaluate_formula_string(self, formula, context_value=None):
        """Evaluate a formula string and return result"""
        formula = formula[1:]  # Remove '='
        formula_upper = formula.upper()
        
        try:
            # Date/Time functions
            if formula_upper == 'NOW()':
                from datetime import datetime
                return datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            elif formula_upper == 'TODAY()':
                from datetime import date
                return date.today().strftime('%Y-%m-%d')
            elif formula_upper == 'TIMESTAMP()':
                import time
                return str(int(time.time()))
            
            # Text functions
            elif formula_upper.startswith('UPPER('):
                text = formula[6:-1]  # Extract content between UPPER( and )
                if context_value:
                    text = context_value
                return text.upper()
            elif formula_upper.startswith('LOWER('):
                text = formula[6:-1]
                if context_value:
                    text = context_value
                return text.lower()
            elif formula_upper.startswith('TRIM('):
                text = formula[5:-1]
                if context_value:
                    text = context_value
                return text.strip()
            
            # Math functions
            elif formula_upper.startswith('RANDOM('):
                import random
                params = formula[7:-1].split(',')
                if len(params) == 2:
                    return str(random.randint(int(params[0]), int(params[1])))
                else:
                    return str(random.random())
            
            # Try to evaluate as Python expression
            elif any(op in formula for op in ['+', '-', '*', '/', '%']):
                result = eval(formula, {"__builtins__": {}}, {})
                return str(result)
            
            return formula  # Return as-is if not recognized
        except Exception:
            return f"#ERROR: {formula}"