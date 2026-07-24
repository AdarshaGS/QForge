from PySide6.QtWidgets import (
    QTableWidget, 
    QTableWidgetItem, 
    QHeaderView,
    QMenu,
    QMessageBox,
    QAbstractItemView,
    QApplication,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QBrush
import pandas as pd
from utils.df_export import export_dataframe


# ── Colour palette ───────────────────────────────────────────────────
# Dark theme
_D_SEL_ROW   = QColor("#1B4F8A")   # selected row  → TablePlus-style blue
_D_SEL_TEXT  = QColor("#ffffff")
_D_MOD_ROW   = QColor("#2a1e00")   # row that has at least one changed cell
_D_MOD_CELL  = QColor("#4d3500")   # the exact cell whose value changed
_D_MOD_TEXT  = QColor("#ffd60a")   # amber text on changed cell
_D_NEW_ROW   = QColor("#0d2a0d")   # newly added row
_D_NEW_TEXT  = QColor("#4ec9a0")
_D_DEL_ROW   = QColor("#2a0d0d")   # row marked for deletion
_D_DEL_TEXT  = QColor("#f48771")
# Light theme
_L_SEL_ROW   = QColor("#0A84FF")
_L_SEL_TEXT  = QColor("#ffffff")
_L_MOD_ROW   = QColor("#fff9e6")
_L_MOD_CELL  = QColor("#ffe58a")
_L_MOD_TEXT  = QColor("#7a5c00")
_L_NEW_ROW   = QColor("#e6ffed")
_L_NEW_TEXT  = QColor("#1a6e3c")
_L_DEL_ROW   = QColor("#ffe6e6")
_L_DEL_TEXT  = QColor("#b00020")


class EditableTableWidget(QTableWidget):
    """Enhanced table widget with inline editing capabilities"""
    
    filter_changed = Signal()  # Signal when filters change
    changes_made = Signal()  # Signal when data is modified
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.original_data = None
        self.filtered_data = None  # Store filtered version
        self.column_filters = {}  # Store filter text for each column
        self.modified_rows = set()    # rows with at least one changed cell
        self.modified_cells = set()   # (row, col) of individually changed cells
        self.new_rows = set()         # newly inserted rows
        self.deleted_rows = set()     # rows marked for deletion

        # Client-side sort state
        self._sort_col = -1   # -1 = no active sort
        self._sort_asc = True

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

        # Cmd+Backspace to delete selected row(s)
        self.delete_shortcut = QShortcut(QKeySequence("Ctrl+Backspace"), self)
        self.delete_shortcut.activated.connect(self.delete_selected_rows)
        
        # Full-row selection (like TablePlus)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)

        # Styling
        self.verticalHeader().setVisible(False)  # Hide row numbers
        self.setAlternatingRowColors(True)
        # Sorting is handled server-side (via ORDER BY in the query);
        # Qt's built-in client-side sort must be OFF to keep row data intact.
        self.setSortingEnabled(False)
        
        # Connect header click for manual sorting (to avoid breaking modified state)
        hdr = self.horizontalHeader()
        hdr.sectionClicked.connect(self.on_header_clicked)
        hdr.setSortIndicatorShown(True)   # show ▲▼ arrows without enabling Qt sort
        hdr.setHighlightSections(False)
        
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
                    gridline-color: #333336;
                    background-color: #1c1c1e;
                    alternate-background-color: #1c1c1e;
                    color: #e5e5ea;
                    selection-background-color: #1a3a5c;
                    selection-color: #e5e5ea;
                    border: none;
                    outline: none;
                }
                QTableWidget::item {
                    padding: 1px 8px;
                    border: none;
                    border-bottom: 1px solid #2a2a2d;
                }
                QTableWidget::item:selected {
                    background: #1a3a5c;
                    color: #ffffff;
                }
                QHeaderView::section {
                    background: #252528;
                    color: #8e8e93;
                    border: none;
                    border-right: 1px solid #333336;
                    border-bottom: 2px solid #444448;
                    padding: 3px 8px;
                    font-size: 11px;
                    font-weight: 600;
                    text-align: left;
                }
                QHeaderView::section:hover { background: #2e2e32; color: #e5e5ea; }
                QHeaderView::section:first { border-left: none; }
            """)
        else:
            # Light theme
            self.setStyleSheet("""
                QTableWidget {
                    gridline-color: #e0e0e3;
                    background-color: #ffffff;
                    alternate-background-color: #ffffff;
                    color: #1c1c1e;
                    selection-background-color: #d0e8ff;
                    selection-color: #1c1c1e;
                    border: none;
                    outline: none;
                }
                QTableWidget::item {
                    padding: 1px 8px;
                    border: none;
                    border-bottom: 1px solid #e8e8eb;
                }
                QTableWidget::item:selected {
                    background: #d0e8ff;
                    color: #1c1c1e;
                }
                QHeaderView::section {
                    background: #f4f4f6;
                    color: #636366;
                    border: none;
                    border-right: 1px solid #dcdcdf;
                    border-bottom: 2px solid #c8c8cc;
                    padding: 3px 8px;
                    font-size: 11px;
                    font-weight: 600;
                    text-align: left;
                }
                QHeaderView::section:hover { background: #e8e8eb; color: #1c1c1e; }
                QHeaderView::section:first { border-left: none; }
            """)
        
    def load_data(self, dataframe: pd.DataFrame, table_name=None):
        """Load data from DataFrame"""
        self.original_data = dataframe.copy() if dataframe is not None else None
        self.filtered_data = dataframe.copy() if dataframe is not None else None
        self.table_name = table_name
        self.modified_rows.clear()
        self.modified_cells.clear()
        self.new_rows.clear()
        self.deleted_rows.clear()
        self.column_filters.clear()
        self._sort_col = -1
        self._sort_asc = True
        self.horizontalHeader().setSortIndicator(-1, Qt.AscendingOrder)
        
        self._display_data(dataframe)
    
    def _display_data(self, dataframe):
        """Display dataframe in the table"""
        # Re-entrancy guard: if we're already rebuilding the table (e.g. a
        # second sectionClicked handler fires while _display_data is running),
        # skip silently to avoid double-disconnect and widget corruption.
        if getattr(self, '_displaying', False):
            return
        self._displaying = True
        try:
            self._display_data_impl(dataframe)
        finally:
            self._displaying = False

    def _display_data_impl(self, dataframe):
        """Inner (non-reentrant) implementation of _display_data."""
        # Temporarily disconnect itemChanged signal
        try:
            self.itemChanged.disconnect(self.on_item_changed)
        except Exception:
            pass  # already disconnected; safe to continue
        
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
            hdr = self.horizontalHeader()
            hdr.setSectionResizeMode(QHeaderView.Interactive)
            hdr.setStretchLastSection(False)
            self._set_compact_column_widths(dataframe)
        
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
        
        hdr = self.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setStretchLastSection(False)
        self._set_compact_column_widths(dataframe)
        
        # Reconnect signal
        self.itemChanged.connect(self.on_item_changed)

        # Re-apply colour for any rows that were already dirty before this
        # display call (e.g. after a client-side sort or filter refresh).
        self._restore_dirty_highlights()
    
    def _restore_dirty_highlights(self):
        """Re-apply colour to all rows that have a known dirty state.
        Called after every _display_data so highlights survive sort/filter refreshes."""
        for row in (self.modified_rows | self.new_rows | self.deleted_rows):
            if 0 <= row < self.rowCount():
                self._repaint_row(row)

    _COL_WIDTH_MIN = 60     # never narrower than this
    _COL_WIDTH_MAX = 300    # never wider than this without manual resize
    _COL_WIDTH_DEF = 120    # default when content is tiny

    def _set_compact_column_widths(self, dataframe):
        """Set column widths: sample the first 50 rows to pick a sensible
        width, clamped to [_COL_WIDTH_MIN, _COL_WIDTH_MAX]. Does NOT
        resize very-wide columns so long text values stay compact."""
        from PySide6.QtGui import QFontMetrics
        from PySide6.QtWidgets import QApplication
        fm = QFontMetrics(QApplication.font())
        hdr = self.horizontalHeader()
        for col_idx, col_name in enumerate(dataframe.columns):
            # Header text width
            header_w = fm.horizontalAdvance(str(col_name)) + 24  # padding
            # Sample up to 50 rows
            sample = dataframe.iloc[:50, col_idx].fillna('').astype(str)
            content_w = sample.map(lambda s: fm.horizontalAdvance(str(s))).max() if not sample.empty else 0
            content_w += 20  # cell padding
            best = max(header_w, content_w, self._COL_WIDTH_DEF)
            width = min(best, self._COL_WIDTH_MAX)
            width = max(width, self._COL_WIDTH_MIN)
            hdr.resizeSection(col_idx, width)

    def keyPressEvent(self, event):
        """Cmd+Enter opens a detail popup for the current cell value."""
        if event.key() == Qt.Key_Return and event.modifiers() == Qt.ControlModifier:
            item = self.currentItem()
            if item and item.text():
                self._open_cell_detail(item)
                return
        super().keyPressEvent(event)

    def _open_cell_detail(self, item):
        """Show a resizable read-only popup with the full cell value."""
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QPlainTextEdit, QDialogButtonBox
        col_name = self.horizontalHeaderItem(item.column()).text() if self.horizontalHeaderItem(item.column()) else ""
        dlg = QDialog(self.window())
        dlg.setWindowTitle(f"Cell value — {col_name}")
        dlg.resize(600, 400)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(10, 10, 10, 10)
        te = QPlainTextEdit()
        te.setPlainText(item.text())
        te.setReadOnly(True)
        te.setFont(QApplication.font())
        te.setStyleSheet("""
            QPlainTextEdit {
                background: #1c1c1e;
                color: #e5e5ea;
                border: 1px solid #3a3a3c;
                border-radius: 4px;
                font-family: 'Menlo', 'Monaco', monospace;
                font-size: 13px;
                padding: 6px;
            }
        """)
        layout.addWidget(te)
        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)
        dlg.exec()

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
        """Track when an item is modified and paint changed cell + row."""
        row = item.row()
        col = item.column()

        # Formula evaluation
        text = item.text()
        if text.startswith('='):
            self.evaluate_formula(item)
            return

        if row not in self.new_rows:
            original_value = item.data(Qt.UserRole)
            current_value  = item.text()

            if current_value == "" and pd.isna(original_value):
                return  # no actual change

            if str(original_value) != current_value:
                self.modified_rows.add(row)
                self.modified_cells.add((row, col))
                self._repaint_row(row)
                self.changes_made.emit()
    
    def _repaint_row(self, row: int):
        """Apply the correct colour to every cell in *row* based on its state."""
        dark = self.current_theme == 'dark'

        if row in self.deleted_rows:
            row_bg   = _D_DEL_ROW   if dark else _L_DEL_ROW
            row_fg   = _D_DEL_TEXT  if dark else _L_DEL_TEXT
            for col in range(self.columnCount()):
                item = self.item(row, col)
                if item:
                    item.setBackground(QBrush(row_bg))
                    item.setForeground(QBrush(row_fg))
            return

        if row in self.new_rows:
            row_bg = _D_NEW_ROW  if dark else _L_NEW_ROW
            row_fg = _D_NEW_TEXT if dark else _L_NEW_TEXT
            for col in range(self.columnCount()):
                item = self.item(row, col)
                if item:
                    item.setBackground(QBrush(row_bg))
                    item.setForeground(QBrush(row_fg))
            return

        if row in self.modified_rows:
            row_bg   = _D_MOD_ROW  if dark else _L_MOD_ROW
            cell_bg  = _D_MOD_CELL if dark else _L_MOD_CELL
            cell_fg  = _D_MOD_TEXT if dark else _L_MOD_TEXT
            default_fg = QColor("#e5e5ea") if dark else QColor("#1c1c1e")
            for col in range(self.columnCount()):
                item = self.item(row, col)
                if item:
                    if (row, col) in self.modified_cells:
                        # Changed cell: bright amber background + amber text
                        item.setBackground(QBrush(cell_bg))
                        item.setForeground(QBrush(cell_fg))
                    else:
                        # Rest of the row: subtle amber tint
                        item.setBackground(QBrush(row_bg))
                        item.setForeground(QBrush(default_fg))
            return

        # Unmodified row — restore default (clear any previous colour)
        default_bg = QColor(0, 0, 0, 0)   # transparent → falls back to stylesheet
        default_fg = QColor("#e5e5ea") if dark else QColor("#1c1c1e")
        for col in range(self.columnCount()):
            item = self.item(row, col)
            if item:
                item.setBackground(QBrush(default_bg))
                item.setForeground(QBrush(default_fg))

    def highlight_row(self, row: int, color: QColor):
        """Legacy helper — delegates to _repaint_row when state is already set."""
        # Colour callers just add the row to the right set first;
        # we repaint via _repaint_row for consistency.
        self._repaint_row(row)
    
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
        
        self._repaint_row(row_count)
        self.changes_made.emit()  # Notify parent
        
        # Start editing first cell
        self.editItem(self.item(row_count, 0))
    
    def delete_selected_rows(self):
        """Mark selected rows for deletion"""
        selected_rows = set()
        for item in self.selectedItems():
            selected_rows.add(item.row())

        if not selected_rows:
            return

        for row in selected_rows:
            self.deleted_rows.add(row)
            self._repaint_row(row)
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
    
    def on_header_clicked(self, col: int):
        """Sort the currently displayed data by the clicked column (client-side)."""
        if self.filtered_data is None or self.filtered_data.empty:
            return

        # Sorting while there are unsaved edits would corrupt the row-index
        # tracking (modified_rows/modified_cells) because sort reorders rows.
        # Block it — the same behaviour as TablePlus.
        if self.has_changes():
            return

        # Toggle direction if same column, else start ascending
        if self._sort_col == col:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col
            self._sort_asc = True

        order = Qt.AscendingOrder if self._sort_asc else Qt.DescendingOrder
        self.horizontalHeader().setSortIndicator(col, order)

        col_name = self.filtered_data.columns[col]
        self.filtered_data = self.filtered_data.sort_values(
            col_name, ascending=self._sort_asc, na_position='last'
        ).reset_index(drop=True)
        self._display_data(self.filtered_data)
    
    def has_changes(self):
        """Check if there are any uncommitted changes"""
        return bool(self.modified_rows or self.new_rows or self.deleted_rows)
    
    # ── Signals used by FK navigation and filter chips ───────────────────────
    from PySide6.QtCore import Signal as _Signal
    filter_by_value  = _Signal(str, str, str)  # (column_name, operator, value)
    navigate_fk      = _Signal(str, str, str)  # (ref_table, ref_column, value)
    show_structure   = _Signal(str)        # (table_name)

    def show_context_menu(self, position):
        """Show comprehensive context menu like TablePlus"""
        menu = QMenu(self)

        selected_items = self.selectedItems()
        has_selection  = len(selected_items) > 0
        current_item   = self.currentItem() if has_selection else None

        if has_selection:
            # ── Quick-look editor (cell viewer) ──────────────────────────────
            ql_action = menu.addAction("🔍  Quick Look Editor")
            ql_action.setShortcut("Ctrl+Return")
            ql_action.triggered.connect(self._quick_look_cell)
            menu.addSeparator()

            # ── Paste / Duplicate ─────────────────────────────────────────────
            paste_action = menu.addAction("Paste")
            paste_action.setShortcut("Ctrl+V")
            paste_action.triggered.connect(self.paste_from_clipboard)
            duplicate_action = menu.addAction("Duplicate")
            duplicate_action.setShortcut("Ctrl+D")
            duplicate_action.triggered.connect(self.duplicate_row)
            menu.addSeparator()

            # ── Copy ──────────────────────────────────────────────────────────
            copy_action = menu.addAction("Copy")
            copy_action.setShortcut("Ctrl+C")
            copy_action.triggered.connect(self.copy_to_clipboard)
            menu.addAction("Copy Cell Value").triggered.connect(self.copy_cell_value)
            menu.addAction("Copy All Column Values").triggered.connect(self.copy_column_values)

            # ── Copy Rows As sub-menu (TablePlus-style) ───────────────────────
            crm = menu.addMenu("Copy Rows As")
            crm.addAction("Plain Text").triggered.connect(
                lambda: self.copy_rows_as("plain"))
            crm.addSeparator()
            crm.addAction("JSON").triggered.connect(
                lambda: self.copy_rows_as("json"))
            crm.addAction("HTML").triggered.connect(
                lambda: self.copy_rows_as("html"))
            crm.addAction("Markdown Table").triggered.connect(
                lambda: self.copy_rows_as("markdown"))
            crm.addSeparator()
            crm.addAction("CSV").triggered.connect(
                lambda: self.copy_rows_as("csv"))
            crm.addAction("CSV include fields name").triggered.connect(
                lambda: self.copy_rows_as("csv_header"))
            crm.addSeparator()
            crm.addAction("SQL Insert Statement").triggered.connect(
                lambda: self.copy_rows_as("sql_insert"))
            crm.addAction("SQL Insert Statement (no auto_inc)").triggered.connect(
                lambda: self.copy_rows_as("sql_insert_no_id"))

            menu.addSeparator()

            # -- Quick Filter submenu ----------------------------------------
            if current_item:
                col_name = (self.horizontalHeaderItem(current_item.column()).text()
                            if self.horizontalHeaderItem(current_item.column()) else "")
                cell_val = current_item.text()

                qf = menu.addMenu("Quick Filter")

                def _add(label, op, val=cell_val, cn=col_name):
                    qf.addAction(label).triggered.connect(
                        lambda: self.filter_by_value.emit(cn, op, val))

                _add(f"{col_name} = '{cell_val}'",           "=")
                _add(f"{col_name} <> '{cell_val}'",          "<>")
                _add(f"{col_name} < '{cell_val}'",           "<")
                _add(f"{col_name} > '{cell_val}'",           ">")
                _add(f"{col_name} <= '{cell_val}'",          "<=")
                _add(f"{col_name} >= '{cell_val}'",          ">=")
                qf.addSeparator()
                _add(f"{col_name} Contains '{cell_val}'",     "CONTAINS")
                _add(f"{col_name} Not contains '{cell_val}'", "NOT CONTAINS")
                qf.addSeparator()
                _add(f"{col_name} Has prefix '{cell_val}'",   "STARTS WITH")
                _add(f"{col_name} Has suffix '{cell_val}'",   "ENDS WITH")
                qf.addSeparator()
                _add(f"{col_name} IN ({cell_val})",           "IN")
                _add(f"{col_name} NOT IN ({cell_val})",       "NOT IN")
                qf.addSeparator()
                _add(f"{col_name} IS NULL",                   "IS NULL",     "")
                _add(f"{col_name} IS NOT NULL",               "IS NOT NULL", "")

                # ── FK navigation ────────────────────────────────────────────
                fk_info = getattr(self, '_fk_map', {}).get(col_name)
                if fk_info:
                    ref_tbl = fk_info['ref_table']
                    ref_col = fk_info['ref_column']
                    fk_action = menu.addAction(
                        f"🔗  Go to {ref_tbl}.{ref_col} = ‘{cell_val[:20]}'")
                    fk_action.triggered.connect(
                        lambda: self.navigate_fk.emit(ref_tbl, ref_col, cell_val))

            menu.addSeparator()

            # ── Export ────────────────────────────────────────────────────────
            menu.addAction("Export result...").triggered.connect(self.export_selected)
            menu.addSeparator()

            # ── Delete / NULL / Default ───────────────────────────────────────
            del_action = menu.addAction("Delete")
            del_action.setShortcut("Delete")
            del_action.triggered.connect(self.delete_selected_rows)
            menu.addSeparator()
            menu.addAction("Set NULL").triggered.connect(self.set_cell_null)
            menu.addAction("Set Default Value").triggered.connect(self.set_cell_default)

        else:
            menu.addAction("Add New Row").triggered.connect(self.add_new_row)

        menu.addSeparator()
        menu.addAction("Revert Changes").triggered.connect(self.revert_changes)
        menu.exec_(self.mapToGlobal(position))

    def set_fk_map(self, fk_list: list):
        """Store FK metadata: list of {column, ref_table, ref_column} dicts."""
        self._fk_map = {fk['column']: fk for fk in (fk_list or [])}

    def _quick_look_cell(self):
        """Open a resizable text viewer for the current cell value."""
        item = self.currentItem()
        if not item:
            return
        col_name = (self.horizontalHeaderItem(item.column()).text()
                    if self.horizontalHeaderItem(item.column()) else "col")
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QPlainTextEdit, QDialogButtonBox
        dlg = QDialog(self.window())
        dlg.setWindowTitle(f"🔍 {col_name}")
        dlg.resize(520, 320)
        lay = QVBoxLayout(dlg)
        te = QPlainTextEdit(item.text())
        te.setReadOnly(True)
        lay.addWidget(te)
        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(dlg.accept)
        lay.addWidget(btns)
        dlg.exec()

    # ── Copy rows in multiple formats ─────────────────────────────────────────

    def _selected_rows_data(self) -> tuple[list[str], list[list[str]]]:
        """Return (headers, [[row values], ...]) for currently selected rows."""
        rows = sorted({item.row() for item in self.selectedItems()})
        cols = list(range(self.columnCount()))
        headers = [self.horizontalHeaderItem(c).text()
                   if self.horizontalHeaderItem(c) else str(c) for c in cols]
        data = []
        for r in rows:
            data.append([self.item(r, c).text() if self.item(r, c) else "" for c in cols])
        return headers, data

    def copy_rows_as(self, fmt: str):
        """Copy selected rows to clipboard in the requested format."""
        from PySide6.QtWidgets import QApplication
        import json
        headers, rows = self._selected_rows_data()
        if not rows:
            return

        if fmt == "plain":
            lines = ["\t".join(headers)]
            for row in rows:
                lines.append("\t".join(row))
            text = "\n".join(lines)

        elif fmt == "json":
            objs = [dict(zip(headers, row)) for row in rows]
            text = json.dumps(objs if len(objs) > 1 else objs[0], indent=2, ensure_ascii=False)

        elif fmt == "html":
            th = "".join(f"<th>{h}</th>" for h in headers)
            trs = ""
            for row in rows:
                tds = "".join(f"<td>{v}</td>" for v in row)
                trs += f"<tr>{tds}</tr>\n"
            text = f"<table>\n<thead><tr>{th}</tr></thead>\n<tbody>\n{trs}</tbody></table>"

        elif fmt == "markdown":
            sep = "| " + " | ".join("-" * max(len(h), 3) for h in headers) + " |"
            hdr = "| " + " | ".join(headers) + " |"
            body = "\n".join("| " + " | ".join(row) + " |" for row in rows)
            text = f"{hdr}\n{sep}\n{body}"

        elif fmt == "csv":
            lines = [",".join(f'"{v}"' for v in row) for row in rows]
            text = "\n".join(lines)

        elif fmt == "csv_header":
            lines = [",".join(f'"{h}"' for h in headers)]
            for row in rows:
                lines.append(",".join(f'"{v}"' for v in row))
            text = "\n".join(lines)

        elif fmt == "sql_insert":
            tbl = self.table_name or "table"
            col_list = ", ".join(f"`{h}`" for h in headers)
            stmts = []
            for row in rows:
                vals = ", ".join(
                    "NULL" if v == "" else f"'{v.replace(chr(39), chr(39)*2)}'"
                    for v in row
                )
                stmts.append(f"INSERT INTO `{tbl}` ({col_list}) VALUES ({vals});")
            text = "\n".join(stmts)

        elif fmt == "sql_insert_no_id":
            # Exclude the first column if its name is 'id' or ends with '_id' and
            # looks like an auto-increment primary key (single-word, all lower/upper).
            tbl = self.table_name or "table"
            skip = {i for i, h in enumerate(headers)
                    if h.lower() == "id" or h.lower().endswith("_id") and i == 0}
            # If no obvious id column found, skip index 0 by default
            if not skip:
                skip = {0}
            filt_headers = [h for i, h in enumerate(headers) if i not in skip]
            col_list = ", ".join(f"`{h}`" for h in filt_headers)
            stmts = []
            for row in rows:
                filt_vals = [v for i, v in enumerate(row) if i not in skip]
                vals = ", ".join(
                    "NULL" if v == "" else f"'{v.replace(chr(39), chr(39)*2)}'"
                    for v in filt_vals
                )
                stmts.append(f"INSERT INTO `{tbl}` ({col_list}) VALUES ({vals});")
            text = "\n".join(stmts)
        else:
            text = ""

        QApplication.clipboard().setText(text)

    # keep legacy single-row method for backward compat
    def copy_row_as(self, format_type):
        _map = {"json": "json", "csv": "csv", "sql": "sql_insert"}
        self.copy_rows_as(_map.get(format_type, format_type))

    def copy_to_clipboard(self):
        """Copy selected cells to clipboard"""
        from PySide6.QtWidgets import QApplication
        selected = self.selectedItems()
        if not selected:
            return
        rows = sorted(set(item.row() for item in selected))
        cols = sorted(set(item.column() for item in selected))
        text = []
        for row in rows:
            row_data = [self.item(row, col).text() if self.item(row, col) else "" for col in cols]
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
        values = [self.item(row, col).text() for row in range(self.rowCount())
                  if self.item(row, col)]
        QApplication.clipboard().setText("\n".join(values))

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
        self._repaint_row(source_row + 1)
    
    def export_selected(self):
        """Export visible table data to CSV / JSON / Excel / SQL."""
        df = self.filtered_data if self.filtered_data is not None else self.original_data
        export_dataframe(self, df, f"{self.table_name or 'data'}.csv", self.table_name or "table")
    
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
            self._repaint_row(source_row + 1)
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
                    self.modified_cells.add((row, col))
                    self._repaint_row(row)
            
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