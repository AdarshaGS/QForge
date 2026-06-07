from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLineEdit,
    QComboBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QCheckBox,
    QSpinBox,
    QLabel,
    QMessageBox,
    QHeaderView
)
from PySide6.QtCore import Qt


class StructureEditorDialog(QDialog):
    """Dialog for creating/editing table structure"""
    
    DATA_TYPES = [
        "INT", "BIGINT", "SMALLINT", "TINYINT",
        "VARCHAR", "CHAR", "TEXT", "MEDIUMTEXT", "LONGTEXT",
        "DATE", "DATETIME", "TIMESTAMP", "TIME", "YEAR",
        "FLOAT", "DOUBLE", "DECIMAL",
        "BOOLEAN", "BOOL",
        "JSON", "BLOB", "ENUM"
    ]
    
    def __init__(self, db_type="mysql", table_name=None, existing_columns=None, parent=None):
        super().__init__(parent)

        self.db_type = db_type
        self.table_name = table_name
        self.is_new_table = (table_name is None)
        # existing_columns: list of dicts with keys Field, Type, Null, Default, Key, Extra
        self.existing_columns = existing_columns or []

        title = "Create Table" if self.is_new_table else f"Edit Structure: {table_name}"
        self.setWindowTitle(title)
        self.resize(800, 600)

        self.init_ui()
    
    def init_ui(self):
        """Initialize UI"""
        layout = QVBoxLayout()
        
        # Table name input (only for new tables)
        if self.is_new_table:
            name_layout = QHBoxLayout()
            name_layout.addWidget(QLabel("Table Name:"))
            
            self.table_name_input = QLineEdit()
            self.table_name_input.setPlaceholderText("Enter table name...")
            name_layout.addWidget(self.table_name_input)
            
            layout.addLayout(name_layout)
        
        # Column definition section
        layout.addWidget(QLabel("Columns:"))
        
        # Column input form
        column_form = QFormLayout()
        
        self.col_name_input = QLineEdit()
        self.col_name_input.setPlaceholderText("Column name")
        
        self.col_type_combo = QComboBox()
        self.col_type_combo.addItems(self.DATA_TYPES)
        self.col_type_combo.currentTextChanged.connect(self.on_type_changed)
        
        self.col_length_input = QSpinBox()
        self.col_length_input.setRange(1, 65535)
        self.col_length_input.setValue(255)
        
        self.col_nullable_check = QCheckBox("Nullable")
        self.col_nullable_check.setChecked(True)
        
        self.col_primary_check = QCheckBox("Primary Key")
        
        self.col_auto_increment_check = QCheckBox("Auto Increment")
        self.col_auto_increment_check.setEnabled(False)
        
        self.col_default_input = QLineEdit()
        self.col_default_input.setPlaceholderText("Default value (optional)")
        
        column_form.addRow("Name:", self.col_name_input)
        column_form.addRow("Type:", self.col_type_combo)
        column_form.addRow("Length:", self.col_length_input)
        column_form.addRow("Options:", self.col_nullable_check)
        column_form.addRow("", self.col_primary_check)
        column_form.addRow("", self.col_auto_increment_check)
        column_form.addRow("Default:", self.col_default_input)
        
        layout.addLayout(column_form)
        
        # Add/Remove column buttons
        col_btn_layout = QHBoxLayout()
        
        self.add_col_btn = QPushButton("+ Add Column")
        self.add_col_btn.clicked.connect(self.add_column)
        
        self.remove_col_btn = QPushButton("- Remove Selected")
        self.remove_col_btn.clicked.connect(self.remove_column)
        
        col_btn_layout.addWidget(self.add_col_btn)
        col_btn_layout.addWidget(self.remove_col_btn)
        col_btn_layout.addStretch()
        
        layout.addLayout(col_btn_layout)
        
        # Columns table
        self.columns_table = QTableWidget()
        self.columns_table.setColumnCount(7)
        self.columns_table.setHorizontalHeaderLabels([
            "Name", "Type", "Length", "Nullable", "Primary Key", "Auto Increment", "Default"
        ])
        self.columns_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.columns_table.setSelectionBehavior(QTableWidget.SelectRows)
        
        layout.addWidget(self.columns_table)
        
        # Dialog buttons
        button_layout = QHBoxLayout()
        
        self.preview_btn = QPushButton("Preview SQL")
        self.preview_btn.clicked.connect(self.preview_sql)
        
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        
        self.create_btn = QPushButton("Create Table" if self.is_new_table else "Alter Table")
        self.create_btn.clicked.connect(self.accept)
        
        button_layout.addWidget(self.preview_btn)
        button_layout.addStretch()
        button_layout.addWidget(self.cancel_btn)
        button_layout.addWidget(self.create_btn)
        
        layout.addLayout(button_layout)
        
        self.setLayout(layout)
        
        # Connect primary key checkbox to auto increment
        self.col_primary_check.stateChanged.connect(self.on_primary_key_changed)

        # If editing an existing table, pre-populate the columns grid
        if not self.is_new_table and self.existing_columns:
            self._populate_existing_columns()
    
    def on_type_changed(self, data_type):
        """Enable/disable length input based on type"""
        needs_length = data_type in ["VARCHAR", "CHAR", "DECIMAL"]
        self.col_length_input.setEnabled(needs_length)
        
        # Enable auto increment for integer types
        is_integer = data_type in ["INT", "BIGINT", "SMALLINT", "TINYINT"]
        if is_integer and self.col_primary_check.isChecked():
            self.col_auto_increment_check.setEnabled(True)
        else:
            self.col_auto_increment_check.setEnabled(False)
            self.col_auto_increment_check.setChecked(False)
    
    def on_primary_key_changed(self, state):
        """Enable/disable auto increment when primary key is toggled"""
        data_type = self.col_type_combo.currentText()
        is_integer = data_type in ["INT", "BIGINT", "SMALLINT", "TINYINT"]
        
        if state == Qt.Checked and is_integer:
            self.col_auto_increment_check.setEnabled(True)
        else:
            self.col_auto_increment_check.setEnabled(False)
            self.col_auto_increment_check.setChecked(False)
    
    def add_column(self):
        """Add column to the table"""
        name = self.col_name_input.text().strip()
        
        if not name:
            QMessageBox.warning(self, "Warning", "Column name is required")
            return
        
        # Check for duplicate column names
        for row in range(self.columns_table.rowCount()):
            if self.columns_table.item(row, 0).text() == name:
                QMessageBox.warning(self, "Warning", "Column name already exists")
                return
        
        col_type = self.col_type_combo.currentText()
        length = str(self.col_length_input.value()) if self.col_length_input.isEnabled() else ""
        nullable = "YES" if self.col_nullable_check.isChecked() else "NO"
        primary = "YES" if self.col_primary_check.isChecked() else "NO"
        auto_inc = "YES" if self.col_auto_increment_check.isChecked() else "NO"
        default = self.col_default_input.text()
        
        # Add to table
        row = self.columns_table.rowCount()
        self.columns_table.insertRow(row)
        
        self.columns_table.setItem(row, 0, QTableWidgetItem(name))
        self.columns_table.setItem(row, 1, QTableWidgetItem(col_type))
        self.columns_table.setItem(row, 2, QTableWidgetItem(length))
        self.columns_table.setItem(row, 3, QTableWidgetItem(nullable))
        self.columns_table.setItem(row, 4, QTableWidgetItem(primary))
        self.columns_table.setItem(row, 5, QTableWidgetItem(auto_inc))
        self.columns_table.setItem(row, 6, QTableWidgetItem(default))
        
        # Clear inputs
        self.col_name_input.clear()
        self.col_type_combo.setCurrentIndex(0)
        self.col_length_input.setValue(255)
        self.col_nullable_check.setChecked(True)
        self.col_primary_check.setChecked(False)
        self.col_auto_increment_check.setChecked(False)
        self.col_default_input.clear()
    
    def remove_column(self):
        """Remove selected column"""
        current_row = self.columns_table.currentRow()
        
        if current_row < 0:
            QMessageBox.warning(self, "Warning", "Please select a column to remove")
            return
        
        self.columns_table.removeRow(current_row)
    
    def generate_sql(self):
        """Generate CREATE TABLE or ALTER TABLE SQL"""
        if self.is_new_table:
            return self.generate_create_sql()
        else:
            return self.generate_alter_sql()
    
    def generate_create_sql(self):
        """Generate CREATE TABLE SQL"""
        if self.is_new_table:
            table_name = self.table_name_input.text().strip()
            if not table_name:
                raise ValueError("Table name is required")
        else:
            table_name = self.table_name
        
        if self.columns_table.rowCount() == 0:
            raise ValueError("At least one column is required")
        
        columns = []
        primary_keys = []
        
        for row in range(self.columns_table.rowCount()):
            name = self.columns_table.item(row, 0).text()
            col_type = self.columns_table.item(row, 1).text()
            length = self.columns_table.item(row, 2).text()
            nullable = self.columns_table.item(row, 3).text()
            primary = self.columns_table.item(row, 4).text()
            auto_inc = self.columns_table.item(row, 5).text()
            default = self.columns_table.item(row, 6).text()
            
            # Build column definition
            col_def = f"`{name}` {col_type}"
            
            if length:
                col_def += f"({length})"
            
            if nullable == "NO":
                col_def += " NOT NULL"
            
            if auto_inc == "YES":
                col_def += " AUTO_INCREMENT"
            
            if default:
                if default.upper() in ["NULL", "CURRENT_TIMESTAMP"]:
                    col_def += f" DEFAULT {default.upper()}"
                else:
                    col_def += f" DEFAULT '{default}'"
            
            columns.append(col_def)
            
            if primary == "YES":
                primary_keys.append(f"`{name}`")
        
        sql = f"CREATE TABLE `{table_name}` (\n  "
        sql += ",\n  ".join(columns)
        
        if primary_keys:
            sql += f",\n  PRIMARY KEY ({', '.join(primary_keys)})"
        
        sql += "\n);"
        
        return sql
    
    def _populate_existing_columns(self):
        """Pre-fill the columns grid with the table's current columns."""
        for col in self.existing_columns:
            raw_type = str(col.get('Type', col.get('type', 'VARCHAR')))
            # Split type and length: VARCHAR(255) -> VARCHAR, 255
            import re
            m = re.match(r'(\w+)\((\d+)\)', raw_type)
            if m:
                col_type = m.group(1).upper()
                length = m.group(2)
            else:
                col_type = raw_type.split('(')[0].upper()
                length = ""

            nullable = "YES" if str(col.get('Null', col.get('nullable', 'YES'))).upper() in ('YES', 'TRUE', '1') else "NO"
            key_val = str(col.get('Key', col.get('key', '')))
            primary = "YES" if key_val.upper() in ('PRI', 'PRIMARY KEY') else "NO"
            extra = str(col.get('Extra', col.get('extra', '')))
            auto_inc = "YES" if 'auto_increment' in extra.lower() else "NO"
            default = str(col.get('Default', col.get('default', '')) or '')
            name = str(col.get('Field', col.get('column_name', col.get('name', ''))))

            row = self.columns_table.rowCount()
            self.columns_table.insertRow(row)
            self.columns_table.setItem(row, 0, QTableWidgetItem(name))
            self.columns_table.setItem(row, 1, QTableWidgetItem(col_type))
            self.columns_table.setItem(row, 2, QTableWidgetItem(length))
            self.columns_table.setItem(row, 3, QTableWidgetItem(nullable))
            self.columns_table.setItem(row, 4, QTableWidgetItem(primary))
            self.columns_table.setItem(row, 5, QTableWidgetItem(auto_inc))
            self.columns_table.setItem(row, 6, QTableWidgetItem(default))

    def generate_alter_sql(self):
        """Generate ALTER TABLE SQL by diffing existing vs new column list."""
        table = self.table_name
        quote = '`' if self.db_type == 'mysql' else '"'

        # Build dict of existing columns: name -> dict
        old_cols = {}
        for col in self.existing_columns:
            name = str(col.get('Field', col.get('column_name', col.get('name', ''))))
            if name:
                old_cols[name] = col

        # Build dict of new columns from the grid
        new_cols = {}  # name -> (type, length, nullable, primary, auto_inc, default)
        new_order = []
        for row in range(self.columns_table.rowCount()):
            name = self.columns_table.item(row, 0).text().strip()
            if not name:
                continue
            col_type  = self.columns_table.item(row, 1).text()
            length    = self.columns_table.item(row, 2).text()
            nullable  = self.columns_table.item(row, 3).text()
            primary   = self.columns_table.item(row, 4).text()
            auto_inc  = self.columns_table.item(row, 5).text()
            default   = self.columns_table.item(row, 6).text()
            new_cols[name] = (col_type, length, nullable, primary, auto_inc, default)
            new_order.append(name)

        if not new_cols:
            raise ValueError("At least one column is required")

        def col_def(name, col_type, length, nullable, auto_inc, default):
            defn = f"{quote}{name}{quote} {col_type}"
            if length:
                defn += f"({length})"
            if nullable == "NO":
                defn += " NOT NULL"
            if auto_inc == "YES" and self.db_type == 'mysql':
                defn += " AUTO_INCREMENT"
            if default:
                up = default.upper()
                if up in ("NULL", "CURRENT_TIMESTAMP"):
                    defn += f" DEFAULT {up}"
                else:
                    defn += f" DEFAULT '{default}'"
            return defn

        statements = []

        # DROP columns that were removed
        for name in list(old_cols.keys()):
            if name not in new_cols:
                statements.append(f"ALTER TABLE {quote}{table}{quote} DROP COLUMN {quote}{name}{quote};")

        # ADD or MODIFY columns
        for name in new_order:
            col_type, length, nullable, primary, auto_inc, default = new_cols[name]
            defn = col_def(name, col_type, length, nullable, auto_inc, default)
            if name not in old_cols:
                statements.append(f"ALTER TABLE {quote}{table}{quote} ADD COLUMN {defn};")
            else:
                # Check if anything changed — always emit MODIFY to be safe
                old = old_cols[name]
                old_type = str(old.get('Type', old.get('type', ''))).upper()
                new_type_full = col_type + (f"({length})" if length else "")
                old_nullable = "YES" if str(old.get('Null', 'YES')).upper() in ('YES', 'TRUE', '1') else "NO"
                if new_type_full.upper() != old_type or nullable != old_nullable:
                    if self.db_type == 'mysql':
                        statements.append(f"ALTER TABLE {quote}{table}{quote} MODIFY COLUMN {defn};")
                    else:
                        # PostgreSQL uses separate clauses
                        statements.append(f"ALTER TABLE {quote}{table}{quote} ALTER COLUMN {quote}{name}{quote} TYPE {col_type}{f'({length})' if length else ''};")
                        null_clause = "DROP NOT NULL" if nullable == "YES" else "SET NOT NULL"
                        statements.append(f"ALTER TABLE {quote}{table}{quote} ALTER COLUMN {quote}{name}{quote} {null_clause};")

        if not statements:
            return f"-- No changes detected for table {table}"

        return "\n".join(statements)
    
    def preview_sql(self):
        """Show SQL preview"""
        try:
            sql = self.generate_sql()
            QMessageBox.information(
                self,
                "SQL Preview",
                sql
            )
        except ValueError as e:
            QMessageBox.warning(self, "Error", str(e))
    
    def get_sql(self):
        """Get the generated SQL"""
        return self.generate_sql()
