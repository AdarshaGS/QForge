from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QLabel
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent, QShortcut, QKeySequence


class QuickSearchDialog(QDialog):
    """Quick search dialog for searching tables, databases, functions, views"""
    
    item_selected = Signal(str, str)  # (item_type, item_name)
    
    def __init__(self, all_items, parent=None):
        super().__init__(parent)
        
        # Add Cmd+W shortcut to close dialog
        close_shortcut = QShortcut(QKeySequence("Ctrl+W"), self)
        close_shortcut.activated.connect(self.reject)
        
        
        self.all_items = all_items  # List of (type, name) tuples
        self.setWindowTitle("Quick Search")
        self.setMinimumWidth(700)
        self.setMinimumHeight(500)
        
        # Modern styling like Spotlight
        self.setStyleSheet("""
            QDialog {
                background-color: #2b2b2b;
                border-radius: 12px;
            }
            QLineEdit {
                background-color: #3a3a3a;
                border: 2px solid #4a4a4a;
                border-radius: 8px;
                padding: 12px;
                font-size: 16px;
                color: white;
            }
            QLineEdit:focus {
                border: 2px solid #0066cc;
            }
            QListWidget {
                background-color: #2b2b2b;
                border: none;
                font-size: 14px;
                color: white;
                outline: none;
            }
            QListWidget::item {
                padding: 8px;
                border-radius: 6px;
                margin: 2px 4px;
            }
            QListWidget::item:selected {
                background-color: #0066cc;
            }
            QListWidget::item:hover {
                background-color: #3a3a3a;
            }
            QLabel {
                color: #999;
            }
        """)
        
        self.init_ui()
        
    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        
        # Search input
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Type 2-3 letters to search tables, functions, views...")
        self.search_input.textChanged.connect(self.filter_items)
        self.search_input.installEventFilter(self)  # Install event filter for arrow keys
        layout.addWidget(self.search_input)
        
        # Results count
        self.count_label = QLabel()
        self.count_label.setStyleSheet("color: #888; font-size: 12px; margin: 0 5px;")
        layout.addWidget(self.count_label)
        
        # Results list
        self.results_list = QListWidget()
        self.results_list.itemDoubleClicked.connect(self.on_item_selected)
        self.results_list.itemActivated.connect(self.on_item_selected)  # Enter key
        layout.addWidget(self.results_list)
        
        # Help text
        help_text = QLabel("⏎ Enter to open  |  Esc to close  |  ↑↓ to navigate")
        help_text.setStyleSheet("color: #666; font-size: 11px; margin: 5px; text-align: center;")
        help_text.setAlignment(Qt.AlignCenter)
        layout.addWidget(help_text)
        
        # Don't populate initially - wait for user input
        
    def eventFilter(self, obj, event):
        """Handle arrow key navigation from search input"""
        if obj == self.search_input and event.type() == event.Type.KeyPress:
            if event.key() == Qt.Key_Down:
                # Move focus to results list and select first item
                if self.results_list.count() > 0:
                    self.results_list.setFocus()
                    if self.results_list.currentRow() < 0:
                        self.results_list.setCurrentRow(0)
                return True
            elif event.key() == Qt.Key_Up:
                # Move focus to results list and select last item
                if self.results_list.count() > 0:
                    self.results_list.setFocus()
                    self.results_list.setCurrentRow(self.results_list.count() - 1)
                return True
            elif event.key() in (Qt.Key_Return, Qt.Key_Enter):
                # Select current item
                if self.results_list.currentItem():
                    self.on_item_selected(self.results_list.currentItem())
                return True
        return super().eventFilter(obj, event)
        self.count_label.setText("Type to search...")
        
        # Focus on search input
        self.search_input.setFocus()
    
    def filter_items(self, search_text):
        """Filter items based on search text"""
        self.results_list.clear()
        search_text = search_text.lower().strip()
        
        # Allow 1+ characters for search (improved from 2)
        if len(search_text) < 1:
            self.count_label.setText("Type to search...")
            return
        
        matching_items = []
        exact_matches = []
        starts_with_matches = []
        contains_matches = []
        fuzzy_matches = []
        
        for item_type, item_name in self.all_items:
            item_name_lower = item_name.lower()
            
            # Prioritize exact matches
            if search_text == item_name_lower:
                exact_matches.append((item_type, item_name))
            # Then starts with matches
            elif item_name_lower.startswith(search_text):
                starts_with_matches.append((item_type, item_name))
            # Then contains matches
            elif search_text in item_name_lower:
                contains_matches.append((item_type, item_name))
            # Finally fuzzy matches
            elif self.fuzzy_match(search_text, item_name_lower):
                fuzzy_matches.append((item_type, item_name))
        
        # Combine in priority order
        matching_items = exact_matches + starts_with_matches + contains_matches + fuzzy_matches
        
        # Limit to 15 results for best UX (like Spotlight)
        for item_type, item_name in matching_items[:15]:
            # Create display text without icon
            display_text = item_name
            
            item = QListWidgetItem(display_text)
            item.setData(Qt.UserRole, (item_type, item_name))
            self.results_list.addItem(item)
        
        # Update count
        total_count = len(matching_items)
        shown_count = min(total_count, 15)
        if total_count > 15:
            self.count_label.setText(f"Showing top {shown_count} of {total_count} results")
        elif total_count > 0:
            self.count_label.setText(f"{total_count} result{'s' if total_count != 1 else ''}")
        else:
            self.count_label.setText("No results found")
        
        # Select first item
        if self.results_list.count() > 0:
            self.results_list.setCurrentRow(0)
    
    def fuzzy_match(self, search, text):
        """Check if search characters appear in order in text"""
        search_idx = 0
        for char in text:
            if search_idx < len(search) and char == search[search_idx]:
                search_idx += 1
        return search_idx == len(search)
    
    def get_icon(self, item_type):
        """Get icon for item type - removed, no icons"""
        return ""
    
    def on_item_selected(self, item):
        """Handle item selection"""
        item_type, item_name = item.data(Qt.UserRole)
        self.item_selected.emit(item_type, item_name)
        self.accept()
    
    def keyPressEvent(self, event: QKeyEvent):
        """Handle key presses"""
        if event.key() == Qt.Key_Return or event.key() == Qt.Key_Enter:
            # Open selected item
            current_item = self.results_list.currentItem()
            if current_item:
                self.on_item_selected(current_item)
        elif event.key() == Qt.Key_Escape:
            # Close dialog
            self.reject()
        elif event.key() in (Qt.Key_Down, Qt.Key_Up):
            # Let list handle navigation
            self.results_list.keyPressEvent(event)
        else:
            # Pass to search input
            self.search_input.keyPressEvent(event)
            self.search_input.setFocus()
