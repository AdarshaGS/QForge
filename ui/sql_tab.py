import pandas as pd
import sqlparse

from PySide6.QtCore import Qt
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
    QLineEdit
)
from PySide6.QtGui import QTextCursor, QKeyEvent, QShortcut, QKeySequence

from ui.sql_highlighter import SqlHighlighter
from ui.sql_completer import SqlCompleter
from ui.editable_table import EditableTableWidget
from ui.column_filter_dialog import ColumnFilterDialog
from ui.theme_manager import ThemeManager


class SqlTab(QWidget):

    def __init__(self):
        super().__init__()

        self.current_df = None
        self.current_table_name = None
        self.filter_visible = False
        self.filter_conditions = []  # List of (column, operator, value) tuples
        self.original_df = None  # Store original unfiltered data

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

        self.editor = QTextEdit()

        self.editor.setPlaceholderText(
            "Write SQL here..."
        )

        self.editor.setMinimumHeight(150)
        
        # Set monospace font - Menlo first (always available on macOS)
        from PySide6.QtGui import QFont
        font = QFont("Menlo", 13)
        if not font.exactMatch():
            font = QFont("Monaco", 13)
        if not font.exactMatch():
            font = QFont("Courier New", 13)
        self.editor.setFont(font)

        # Apply syntax highlighting
        self.highlighter = SqlHighlighter(self.editor.document())
        
        # Apply autocomplete
        self.completer = SqlCompleter(self.editor)
        self.completer.setWidget(self.editor)
        # Connect to activated signal with string parameter
        self.completer.activated[str].connect(self.insert_completion)
        
        # Install event filter for better control
        self.editor.installEventFilter(self)
        
        # ==================================
        # BUTTONS TOOLBAR (after editor)
        # ==================================
        
        run_layout = QHBoxLayout()
        run_layout.setContentsMargins(5, 5, 5, 5)
        run_layout.addStretch()
        
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #3a3a3a;
                color: #cccccc;
                border: 1px solid #555555;
                border-radius: 4px;
                padding: 5px 15px;
                font-weight: bold;
            }
            QPushButton:disabled {
                background-color: #2a2a2a;
                color: #666666;
            }
        """)
        run_layout.addWidget(self.cancel_btn)
        
        self.run_btn = QPushButton("▶ Run")
        self.run_btn.setStyleSheet("""
            QPushButton {
                background-color: #0078d4;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 5px 20px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #1084d8;
            }
            QPushButton:pressed {
                background-color: #006cbe;
            }
        """)
        run_layout.addWidget(self.run_btn)
        
        # ==================================
        # FILTER BAR (Similar to TablePlus)
        # ==================================
        
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

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("""
            QLabel {
                color: #888888;
                padding: 3px 5px;
                font-size: 11px;
            }
        """)

        # ==================================
        # RESULT GRID
        # ==================================

        self.result_table = EditableTableWidget()

        # remove serial number column

        self.result_table.verticalHeader().setVisible(
            False
        )

        self.result_table.setAlternatingRowColors(
            True
        )

        self.result_table.setSortingEnabled(
            True
        )
        
        # Connect filter signal
        self.result_table.filter_changed.connect(self.on_filter_changed)
        
        # Hide results table by default - only show after query execution
        self.result_table.hide()

        # ==================================
        # ADD TO LAYOUT WITH SPLITTER
        # ==================================
        
        # Top widget: editor + run button
        top_widget = QWidget()
        top_layout = QVBoxLayout()
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(2)
        top_layout.addWidget(self.editor)
        top_layout.addLayout(run_layout)
        top_widget.setLayout(top_layout)
        
        # Bottom widget: filter + status + results
        bottom_widget = QWidget()
        bottom_layout = QVBoxLayout()
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(2)
        bottom_layout.addWidget(self.filter_container)
        bottom_layout.addWidget(self.status_label)
        bottom_layout.addWidget(self.result_table)
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
        
        # Add keyboard shortcut for save (Cmd+S)
        self.save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        self.save_shortcut.activated.connect(self.save_changes)
        
        # Add Cmd+F shortcut for filter
        self.filter_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        self.filter_shortcut.activated.connect(self.toggle_filter)
        
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
    
    def eventFilter(self, obj, event):
        """Handle key events for autocomplete"""
        if obj == self.editor and event.type() == event.Type.KeyPress:
            key_event = event
            
            # Check if completer popup is visible
            if self.completer.popup().isVisible():
                # Handle navigation in popup
                if key_event.key() in (Qt.Key_Enter, Qt.Key_Return, Qt.Key_Escape, Qt.Key_Tab):
                    return False
            
            # Trigger autocomplete on specific keys
            if key_event.key() in (Qt.Key_Space,) and key_event.modifiers() == Qt.ControlModifier:
                # Ctrl+Space: force autocomplete
                self.update_completer()
                return True
            elif key_event.text().isalnum() or key_event.text() in ('_', '.'):
                # Auto-trigger on alphanumeric characters
                result = super().eventFilter(obj, event)
                self.update_completer()
                return result
        
        return super().eventFilter(obj, event)
    
    def update_completer(self):
        """Update completer popup with context-aware suggestions"""
        cursor = self.editor.textCursor()
        
        # Get full query text and cursor position
        query_text = self.editor.toPlainText()
        cursor_position = cursor.position()
        
        # Get the word under cursor (including dot notation)
        cursor.select(QTextCursor.WordUnderCursor)
        word = cursor.selectedText()
        
        # For dot notation, extend selection to include prefix
        if '.' not in word:
            # Check if there's a dot just before the word
            temp_cursor = self.editor.textCursor()
            temp_cursor.setPosition(cursor_position)
            temp_cursor.movePosition(QTextCursor.Left, QTextCursor.MoveAnchor, len(word) + 1)
            temp_cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor, len(word) + 1)
            extended_word = temp_cursor.selectedText()
            if '.' in extended_word:
                word = extended_word
        
        if len(word) >= 1:  # Start suggesting after 1 character
            # Update suggestions based on context (this rebuilds the list)
            count = self.completer.update_suggestions(query_text, cursor_position, word)
            
            # Show popup if there are matches
            if count > 0:
                # Set completion prefix for filtering
                self.completer.setCompletionPrefix(word)
                
                # Position popup at cursor
                rect = self.editor.cursorRect()
                rect.setWidth(400)  # Wider popup to show type information
                self.completer.complete(rect)
            else:
                self.completer.popup().hide()
        else:
            self.completer.popup().hide()
    
    def insert_completion(self, completion):
        """Insert the selected completion at cursor - REPLACE mode"""
        # Extract just the text if it includes type info
        if "    [" in completion:
            completion = completion.split("    [")[0]
        
        cursor = self.editor.textCursor()
        
        # Find the start of the current word by moving left until we hit whitespace or special char
        end_pos = cursor.position()
        start_pos = end_pos
        
        text = self.editor.toPlainText()
        
        # Move back to find word start (including dots for table.column notation)
        while start_pos > 0:
            char = text[start_pos - 1]
            if char.isalnum() or char in ('_', '.'):
                start_pos -= 1
            else:
                break
        
        # Select the partial word
        cursor.setPosition(start_pos)
        cursor.setPosition(end_pos, QTextCursor.KeepAnchor)
        
        # Replace with completion
        cursor.insertText(completion)
        
        # Update the editor cursor
        self.editor.setTextCursor(cursor)
    
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
            # Signal parent to execute the SQL
            self.pending_commit_sql = all_sql
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

        formatted = sqlparse.format(
            query,
            reindent=True,
            keyword_case="upper"
        )

        self.editor.setPlainText(
            formatted
        )
    
    def minify_sql(self):
        """Minify/compress SQL query (remove extra whitespace)"""
        query = self.editor.toPlainText()

        if not query.strip():
            return

        # Minify: remove extra whitespace, newlines
        minified = sqlparse.format(
            query,
            reindent=False,
            keyword_case="upper",
            strip_comments=True
        )
        
        # Further compress by removing extra spaces
        import re
        minified = re.sub(r'\s+', ' ', minified).strip()

        self.editor.setPlainText(minified)    
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

        self.current_df = dataframe
        self.original_df = dataframe.copy()  # Store original for filtering
        self.current_table_name = table_name
        
        # Update filter column options
        if len(dataframe.columns) > 0:
            self._update_filter_columns(list(dataframe.columns))
        
        # Show results table when data is loaded
        self.result_table.show()
        
        # Load into editable table
        self.result_table.load_data(dataframe, table_name)
        
        # Add filter headers
        if dataframe is not None and not dataframe.empty:
            self.add_filter_headers()
        
        # Update button states
        self.commit_btn.setEnabled(False)
        self.revert_btn.setEnabled(False)
    
    def add_filter_headers(self):
        """Add filter input boxes to column headers"""
        from ui.filter_header import FilterHeaderWidget
        
        for col in range(self.result_table.columnCount()):
            col_name = self.result_table.horizontalHeaderItem(col).text()
            filter_widget = FilterHeaderWidget(col, col_name)
            filter_widget.filter_changed.connect(self.result_table.apply_column_filter)
            # Note: QTableWidget doesn't support setCellWidget for headers directly
            # Instead, we'll use the inline filtering in the table itself
    
    def on_filter_changed(self):
        """Update status when filters change"""
        filter_status = self.result_table.get_filter_status()
        if filter_status:
            row_count = self.result_table.rowCount()
            self.status_label.setText(f"Filtered: {row_count} rows | {filter_status}")
        else:
            row_count = self.result_table.rowCount()
            self.status_label.setText(f"Rows: {row_count}")

    # ======================================
    # STATUS
    # ======================================

    def update_status(
        self,
        rows,
        execution_time
    ):

        self.status_label.setText(
            f"{rows} rows | {execution_time:.3f}s"
        )
        self.status_label.setStyleSheet("""
            QLabel {
                color: #0078d4;
                padding: 5px;
                font-size: 12px;
                font-weight: 500;
            }
        """)

    # ======================================
    # EXPORT DATA
    # ======================================

    def export_data(self):
        """Export data in multiple formats: CSV, JSON, Excel, Parquet"""
        if self.current_df is None:
            QMessageBox.information(
                self,
                "Info",
                "No data to export"
            )
            return

        # Show file dialog with multiple format options
        file_name, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export Data",
            "results.csv",
            "CSV Files (*.csv);;SQL Insert (*.sql);;JSON Files (*.json);;Excel Files (*.xlsx);;Parquet Files (*.parquet)"
        )

        if not file_name:
            return

        try:
            # Determine format from filter or file extension
            if selected_filter == "CSV Files (*.csv)" or file_name.endswith('.csv'):
                self.current_df.to_csv(file_name, index=False)
                format_name = "CSV"
            elif selected_filter == "SQL Insert (*.sql)" or file_name.endswith('.sql'):
                # Generate SQL INSERT statements
                table_name = getattr(self, 'current_table_name', 'table')
                with open(file_name, 'w') as f:
                    for _, row in self.current_df.iterrows():
                        columns = ', '.join([f"`{col}`" for col in self.current_df.columns])
                        values = []
                        for val in row:
                            if pd.isna(val):
                                values.append('NULL')
                            elif isinstance(val, str):
                                values.append(f"'{val.replace(chr(39), chr(39)+chr(39))}'")
                            else:
                                values.append(str(val))
                        values_str = ', '.join(values)
                        f.write(f"INSERT INTO `{table_name}` ({columns}) VALUES ({values_str});\n")
                format_name = "SQL"
            elif selected_filter == "JSON Files (*.json)" or file_name.endswith('.json'):
                self.current_df.to_json(file_name, orient='records', indent=2)
                format_name = "JSON"
            elif selected_filter == "Excel Files (*.xlsx)" or file_name.endswith('.xlsx'):
                self.current_df.to_excel(file_name, index=False, engine='openpyxl')
                format_name = "Excel"
            elif selected_filter == "Parquet Files (*.parquet)" or file_name.endswith('.parquet'):
                self.current_df.to_parquet(file_name, index=False)
                format_name = "Parquet"
            else:
                # Default to CSV
                self.current_df.to_csv(file_name, index=False)
                format_name = "CSV"

            QMessageBox.information(
                self,
                "Success",
                f"{format_name} file exported successfully"
            )
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to export: {str(e)}"
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
            self.status_label.setText(f"Imported {len(df)} rows from {format_name} file")

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
        """Get query text - returns selected text, query at cursor, or all text"""
        cursor = self.editor.textCursor()
        
        # If text is selected, return selection
        if cursor.hasSelection():
            return cursor.selectedText().replace('\u2029', '\n')
        
        # Try to find query at cursor position
        query_at_cursor = self.get_query_at_cursor()
        if query_at_cursor:
            return query_at_cursor
        
        # Fall back to all text
        return self.editor.toPlainText()
    
    def update_theme(self, is_dark=True):
        """Update filter container theme"""
        if is_dark:
            style = ThemeManager.get_filter_container_style_dark()
        else:
            style = ThemeManager.get_filter_container_style_light()
        self.filter_container.setStyleSheet(style)
        
        # Update result table theme
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
                if operator == "CONTAINS":
                    filtered_df = filtered_df[filtered_df[column].astype(str).str.contains(value, case=False, na=False)]
                elif operator == "STARTS WITH":
                    filtered_df = filtered_df[filtered_df[column].astype(str).str.startswith(value, na=False)]
                elif operator == "ENDS WITH":
                    filtered_df = filtered_df[filtered_df[column].astype(str).str.endswith(value, na=False)]
                elif operator == "=":
                    filtered_df = filtered_df[filtered_df[column].astype(str) == value]
                elif operator == "!=":
                    filtered_df = filtered_df[filtered_df[column].astype(str) != value]
                elif operator == ">":
                    filtered_df = filtered_df[pd.to_numeric(filtered_df[column], errors='coerce') > float(value)]
                elif operator == ">=":
                    filtered_df = filtered_df[pd.to_numeric(filtered_df[column], errors='coerce') >= float(value)]
                elif operator == "<":
                    filtered_df = filtered_df[pd.to_numeric(filtered_df[column], errors='coerce') < float(value)]
                elif operator == "<=":
                    filtered_df = filtered_df[pd.to_numeric(filtered_df[column], errors='coerce') <= float(value)]
            except Exception as e:
                from utils.logger import get_logger
                logger = get_logger()
                logger.error(f"Filter error: {str(e)}")
                continue
        
        # Update current_df and reload table
        self.current_df = filtered_df
        self.result_table.load_data(self.current_df, self.current_table_name)
        self.result_table.show()
        
        # Update status
        self.status_label.setText(f"{len(filtered_df)} rows (filtered from {len(self.original_df)})")
    
    def clear_all_filters(self):
        """Clear all filters and show original data"""
        # Reset to original data
        if self.original_df is not None:
            self.current_df = self.original_df.copy()
            self.result_table.load_data(self.current_df, self.current_table_name)
            self.result_table.show()
            self.status_label.setText(f"{len(self.current_df)} rows")
        
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

    def set_query(
        self,
        query
    ):

        self.editor.setPlainText(
            query
        )