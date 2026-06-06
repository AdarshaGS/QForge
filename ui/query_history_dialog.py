from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QLabel,
    QTextEdit,
    QLineEdit,
    QMessageBox
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QShortcut, QKeySequence


class QueryHistoryDialog(QDialog):
    """Dialog to view and select from query history"""

    def __init__(self, history_service, parent=None):
        super().__init__(parent)
        
        self.history_service = history_service
        self.selected_query = None
        
        self.setWindowTitle("Query History")
        self.resize(800, 600)
        
        # Add Cmd+W shortcut to close dialog
        close_shortcut = QShortcut(QKeySequence("Ctrl+W"), self)
        close_shortcut.activated.connect(self.reject)
        
        self.init_ui()
        self.load_history()

    def init_ui(self):
        """Initialize the UI"""
        layout = QVBoxLayout()

        # Search box
        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("Search:"))
        
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search queries...")
        self.search_input.textChanged.connect(self.filter_history)
        search_layout.addWidget(self.search_input)
        
        layout.addLayout(search_layout)

        # History list
        self.history_list = QListWidget()
        self.history_list.currentItemChanged.connect(self.on_selection_changed)
        layout.addWidget(self.history_list)

        # Query preview
        preview_label = QLabel("Query Preview:")
        layout.addWidget(preview_label)
        
        self.query_preview = QTextEdit()
        self.query_preview.setReadOnly(True)
        self.query_preview.setMaximumHeight(150)
        layout.addWidget(self.query_preview)

        # Buttons
        button_layout = QHBoxLayout()
        
        self.clear_btn = QPushButton("Clear History")
        self.clear_btn.clicked.connect(self.clear_history)
        button_layout.addWidget(self.clear_btn)
        
        button_layout.addStretch()
        
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)
        
        self.use_btn = QPushButton("Use Query")
        self.use_btn.clicked.connect(self.use_selected_query)
        button_layout.addWidget(self.use_btn)
        
        layout.addLayout(button_layout)
        
        self.setLayout(layout)

    def load_history(self, queries=None):
        """Load history into the list"""
        self.history_list.clear()
        
        if queries is None:
            queries = self.history_service.get_recent_queries()
        
        for entry in queries:
            # Format the display text
            timestamp = entry["timestamp"]
            connection = entry["connection"]
            rows = entry.get("rows", 0)
            time = entry.get("execution_time", 0)
            
            # Truncate query for display
            query_preview = entry["query"].replace("\n", " ")[:80]
            if len(entry["query"]) > 80:
                query_preview += "..."
            
            display_text = f"{timestamp} | {connection} | {rows} rows | {time:.2f}s\n{query_preview}"
            
            item = QListWidgetItem(display_text)
            item.setData(Qt.UserRole, entry)
            self.history_list.addItem(item)
        
        if queries:
            self.history_list.setCurrentRow(0)

    def filter_history(self):
        """Filter history based on search text"""
        search_text = self.search_input.text().strip()
        
        if not search_text:
            self.load_history()
            return
        
        filtered = self.history_service.search_queries(search_text)
        self.load_history(filtered)

    def on_selection_changed(self, current, previous):
        """Handle selection change"""
        if current is None:
            self.query_preview.clear()
            return
        
        entry = current.data(Qt.UserRole)
        self.query_preview.setPlainText(entry["query"])

    def use_selected_query(self):
        """Use the selected query"""
        current_item = self.history_list.currentItem()
        
        if current_item is None:
            QMessageBox.warning(self, "Warning", "Please select a query")
            return
        
        entry = current_item.data(Qt.UserRole)
        self.selected_query = entry["query"]
        self.accept()

    def clear_history(self):
        """Clear all history"""
        reply = QMessageBox.question(
            self,
            "Clear History",
            "Are you sure you want to clear all query history?",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            self.history_service.clear_history()
            self.load_history()

    def get_selected_query(self):
        """Get the selected query"""
        return self.selected_query
