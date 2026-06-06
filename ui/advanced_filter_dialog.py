from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QComboBox,
    QLineEdit,
    QPushButton,
    QLabel,
    QDialogButtonBox
)
from PySide6.QtCore import Qt


class AdvancedFilterDialog(QDialog):
    """Advanced filter dialog with column, operator, and value selection"""
    
    def __init__(self, columns, parent=None):
        super().__init__(parent)
        
        self.columns = columns
        self.setWindowTitle("Filter Data")
        self.setMinimumWidth(500)
        
        self.init_ui()
    
    def init_ui(self):
        layout = QVBoxLayout(self)
        
        # Filter row
        filter_layout = QHBoxLayout()
        
        # Column dropdown
        col_label = QLabel("Column:")
        self.column_combo = QComboBox()
        self.column_combo.addItems(self.columns)
        self.column_combo.setMinimumWidth(150)
        
        # Operator dropdown
        op_label = QLabel("Operator:")
        self.operator_combo = QComboBox()
        self.operator_combo.addItems([
            "=",
            "!=",
            ">",
            ">=",
            "<",
            "<=",
            "LIKE",
            "NOT LIKE",
            "IN",
            "NOT IN",
            "IS NULL",
            "IS NOT NULL"
        ])
        self.operator_combo.setMinimumWidth(120)
        
        # Value input
        val_label = QLabel("Value:")
        self.value_input = QLineEdit()
        self.value_input.setPlaceholderText("Enter value...")
        self.value_input.setMinimumWidth(200)
        
        filter_layout.addWidget(col_label)
        filter_layout.addWidget(self.column_combo)
        filter_layout.addWidget(op_label)
        filter_layout.addWidget(self.operator_combo)
        filter_layout.addWidget(val_label)
        filter_layout.addWidget(self.value_input)
        
        layout.addLayout(filter_layout)
        
        # Help text
        help_label = QLabel("Tips: Use '%' for wildcards with LIKE, comma-separated values for IN")
        help_label.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(help_label)
        
        # Buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        
        layout.addWidget(button_box)
        
        # Update value input based on operator
        self.operator_combo.currentTextChanged.connect(self.update_value_input)
    
    def update_value_input(self, operator):
        """Update value input based on selected operator"""
        if operator in ["IS NULL", "IS NOT NULL"]:
            self.value_input.setEnabled(False)
            self.value_input.clear()
        else:
            self.value_input.setEnabled(True)
            
            if operator in ["LIKE", "NOT LIKE"]:
                self.value_input.setPlaceholderText("e.g., %test%")
            elif operator in ["IN", "NOT IN"]:
                self.value_input.setPlaceholderText("e.g., 1, 2, 3 or 'a', 'b', 'c'")
            else:
                self.value_input.setPlaceholderText("Enter value...")
    
    def get_filter_condition(self):
        """Build and return the WHERE condition"""
        column = self.column_combo.currentText()
        operator = self.operator_combo.currentText()
        value = self.value_input.text().strip()
        
        if operator in ["IS NULL", "IS NOT NULL"]:
            return f"{column} {operator}"
        
        if not value:
            return ""
        
        # Handle IN and NOT IN operators
        if operator in ["IN", "NOT IN"]:
            # If values already have parentheses, use as-is
            if value.startswith("(") and value.endswith(")"):
                return f"{column} {operator} {value}"
            else:
                return f"{column} {operator} ({value})"
        
        # Handle LIKE operators
        if operator in ["LIKE", "NOT LIKE"]:
            # Add quotes if not already present
            if not (value.startswith("'") and value.endswith("'")):
                value = f"'{value}'"
            return f"{column} {operator} {value}"
        
        # For other operators, try to detect if it's a string or number
        # If it contains non-numeric characters (except . and -), quote it
        try:
            float(value)
            # It's a number, use as-is
            return f"{column} {operator} {value}"
        except ValueError:
            # It's a string, add quotes if not already present
            if not (value.startswith("'") and value.endswith("'")):
                value = f"'{value}'"
            return f"{column} {operator} {value}"
