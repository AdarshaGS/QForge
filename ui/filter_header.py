from PySide6.QtWidgets import QWidget, QVBoxLayout, QLineEdit, QLabel
from PySide6.QtCore import Qt, Signal


class FilterHeaderWidget(QWidget):
    """Custom header widget with inline filter"""
    
    filter_changed = Signal(int, str)  # column_index, filter_text
    
    def __init__(self, column_index, column_name, parent=None):
        super().__init__(parent)
        
        self.column_index = column_index
        
        layout = QVBoxLayout()
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)
        
        # Column name label
        label = QLabel(column_name)
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)
        
        # Filter input
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("Filter...")
        self.filter_input.setMaximumHeight(20)
        self.filter_input.textChanged.connect(self.on_filter_changed)
        layout.addWidget(self.filter_input)
        
        self.setLayout(layout)
    
    def on_filter_changed(self, text):
        """Emit signal when filter text changes"""
        self.filter_changed.emit(self.column_index, text)
    
    def clear_filter(self):
        """Clear the filter input"""
        self.filter_input.clear()
