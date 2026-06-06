from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLineEdit,
    QComboBox,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QLabel
)
from PySide6.QtCore import Qt


class DataFilterDialog(QDialog):
    """Dialog for building data filters"""
    
    def __init__(self, columns, parent=None):
        super().__init__(parent)
        
        self.columns = columns
        self.filters = []
        
        self.setWindowTitle("Filter Data")
        self.resize(500, 400)
        
        self.init_ui()
    
    def init_ui(self):
        """Initialize UI"""
        layout = QVBoxLayout()
        
        # Filter builder section
        filter_form = QFormLayout()
        
        self.column_combo = QComboBox()
        self.column_combo.addItems(self.columns)
        
        self.operator_combo = QComboBox()
        self.operator_combo.addItems([
            "equals (=)",
            "not equals (!=)",
            "contains (LIKE)",
            "starts with (LIKE)",
            "ends with (LIKE)",
            "greater than (>)",
            "less than (<)",
            "greater or equal (>=)",
            "less or equal (<=)",
            "is NULL",
            "is NOT NULL"
        ])
        self.operator_combo.currentTextChanged.connect(self.on_operator_changed)
        
        self.value_input = QLineEdit()
        self.value_input.setPlaceholderText("Filter value...")
        
        filter_form.addRow("Column:", self.column_combo)
        filter_form.addRow("Operator:", self.operator_combo)
        filter_form.addRow("Value:", self.value_input)
        
        layout.addLayout(filter_form)
        
        # Buttons for adding/removing filters
        button_layout = QHBoxLayout()
        
        self.add_filter_btn = QPushButton("+ Add Filter")
        self.add_filter_btn.clicked.connect(self.add_filter)
        
        self.remove_filter_btn = QPushButton("- Remove Selected")
        self.remove_filter_btn.clicked.connect(self.remove_filter)
        
        button_layout.addWidget(self.add_filter_btn)
        button_layout.addWidget(self.remove_filter_btn)
        button_layout.addStretch()
        
        layout.addLayout(button_layout)
        
        # Active filters list
        layout.addWidget(QLabel("Active Filters:"))
        self.filter_list = QListWidget()
        layout.addWidget(self.filter_list)
        
        # Dialog buttons
        dialog_buttons = QHBoxLayout()
        
        self.clear_btn = QPushButton("Clear All")
        self.clear_btn.clicked.connect(self.clear_filters)
        
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        
        self.apply_btn = QPushButton("Apply Filters")
        self.apply_btn.clicked.connect(self.accept)
        
        dialog_buttons.addWidget(self.clear_btn)
        dialog_buttons.addStretch()
        dialog_buttons.addWidget(self.cancel_btn)
        dialog_buttons.addWidget(self.apply_btn)
        
        layout.addLayout(dialog_buttons)
        
        self.setLayout(layout)
    
    def on_operator_changed(self, operator):
        """Disable value input for NULL checks"""
        if "NULL" in operator:
            self.value_input.setEnabled(False)
            self.value_input.clear()
        else:
            self.value_input.setEnabled(True)
    
    def add_filter(self):
        """Add a filter to the list"""
        column = self.column_combo.currentText()
        operator = self.operator_combo.currentText()
        value = self.value_input.text()
        
        if "NULL" not in operator and not value:
            return
        
        filter_obj = {
            'column': column,
            'operator': operator,
            'value': value
        }
        
        self.filters.append(filter_obj)
        
        # Display in list
        display_text = f"{column} {operator}"
        if value:
            display_text += f" '{value}'"
        
        item = QListWidgetItem(display_text)
        item.setData(Qt.UserRole, filter_obj)
        self.filter_list.addItem(item)
        
        # Clear inputs
        self.value_input.clear()
    
    def remove_filter(self):
        """Remove selected filter"""
        current_item = self.filter_list.currentItem()
        if not current_item:
            return
        
        row = self.filter_list.row(current_item)
        self.filters.pop(row)
        self.filter_list.takeItem(row)
    
    def clear_filters(self):
        """Clear all filters"""
        self.filters.clear()
        self.filter_list.clear()
    
    def get_where_clause(self):
        """Generate SQL WHERE clause from filters"""
        if not self.filters:
            return ""
        
        conditions = []
        
        for f in self.filters:
            column = f['column']
            operator = f['operator']
            value = f['value']
            
            if "equals (=)" in operator and "not" not in operator:
                conditions.append(f"{column} = '{value}'")
            elif "not equals" in operator:
                conditions.append(f"{column} != '{value}'")
            elif "contains" in operator:
                conditions.append(f"{column} LIKE '%{value}%'")
            elif "starts with" in operator:
                conditions.append(f"{column} LIKE '{value}%'")
            elif "ends with" in operator:
                conditions.append(f"{column} LIKE '%{value}'")
            elif "greater than (>)" in operator and "=" not in operator:
                conditions.append(f"{column} > {value}")
            elif "less than (<)" in operator and "=" not in operator:
                conditions.append(f"{column} < {value}")
            elif "greater or equal" in operator:
                conditions.append(f"{column} >= {value}")
            elif "less or equal" in operator:
                conditions.append(f"{column} <= {value}")
            elif "is NULL" in operator:
                conditions.append(f"{column} IS NULL")
            elif "is NOT NULL" in operator:
                conditions.append(f"{column} IS NOT NULL")
        
        return " AND ".join(conditions)
    
    def set_existing_filters(self, filters):
        """Load existing filters"""
        self.filters = filters
        self.filter_list.clear()
        
        for f in filters:
            display_text = f"{f['column']} {f['operator']}"
            if f['value']:
                display_text += f" '{f['value']}'"
            
            item = QListWidgetItem(display_text)
            item.setData(Qt.UserRole, f)
            self.filter_list.addItem(item)
