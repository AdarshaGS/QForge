from PySide6.QtWidgets import (
    QDialog, 
    QVBoxLayout, 
    QHBoxLayout, 
    QPushButton, 
    QComboBox, 
    QLineEdit, 
    QLabel,
    QFormLayout
)
from PySide6.QtCore import Qt


class ColumnFilterDialog(QDialog):
    """Dialog for filtering table data by column and value"""
    
    def __init__(self, columns, parent=None):
        super().__init__(parent)
        
        self.setWindowTitle("Filter Data")
        self.setMinimumWidth(400)
        
        self.columns = columns
        self.filters = {}  # {column_name: filter_value}
        
        self.init_ui()
    
    def init_ui(self):
        """Initialize the UI"""
        layout = QVBoxLayout()
        
        # Instructions
        label = QLabel("Filter data by column values:")
        layout.addWidget(label)
        
        # Form for column and value
        form_layout = QFormLayout()
        
        # Column dropdown
        self.column_combo = QComboBox()
        self.column_combo.addItems(self.columns)
        form_layout.addRow("Column:", self.column_combo)
        
        # Value input
        self.value_input = QLineEdit()
        self.value_input.setPlaceholderText("Enter filter value...")
        form_layout.addRow("Value:", self.value_input)
        
        layout.addLayout(form_layout)
        
        # Buttons
        button_layout = QHBoxLayout()
        
        self.add_filter_btn = QPushButton("Add Filter")
        self.add_filter_btn.clicked.connect(self.add_filter)
        
        self.clear_filters_btn = QPushButton("Clear All")
        self.clear_filters_btn.clicked.connect(self.clear_filters)
        
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.clicked.connect(self.accept)
        
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        
        button_layout.addWidget(self.add_filter_btn)
        button_layout.addWidget(self.clear_filters_btn)
        button_layout.addStretch()
        button_layout.addWidget(self.apply_btn)
        button_layout.addWidget(self.cancel_btn)
        
        layout.addLayout(button_layout)
        
        # Active filters display
        self.filters_label = QLabel("Active filters: None")
        self.filters_label.setWordWrap(True)
        layout.addWidget(self.filters_label)
        
        self.setLayout(layout)
    
    def add_filter(self):
        """Add a filter"""
        column = self.column_combo.currentText()
        value = self.value_input.text().strip()
        
        if column and value:
            self.filters[column] = value
            self.update_filters_display()
            self.value_input.clear()
    
    def clear_filters(self):
        """Clear all filters"""
        self.filters.clear()
        self.update_filters_display()
    
    def update_filters_display(self):
        """Update the display of active filters"""
        if not self.filters:
            self.filters_label.setText("Active filters: None")
        else:
            filter_text = ", ".join([f"{col}={val}" for col, val in self.filters.items()])
            self.filters_label.setText(f"Active filters: {filter_text}")
    
    def get_filters(self):
        """Get the current filters"""
        return self.filters.copy()
