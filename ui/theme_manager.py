"""
Theme Manager for SQL Workbench
Provides dark and light theme support
"""

class ThemeManager:
    
    @staticmethod
    def get_dark_theme():
        """Get dark theme stylesheet"""
        return """
            /* Main Window */
            QMainWindow {
                background-color: #1e1e1e;
                color: #ffffff;
            }
            
            /* Widgets */
            QWidget {
                background-color: #1e1e1e;
                color: #ffffff;
            }
            
            /* Tree Widget (Schema Browser) */
            QTreeWidget {
                background-color: #252526;
                color: #cccccc;
                border: 1px solid #3c3c3c;
                outline: none;
            }
            
            QTreeWidget::item:hover {
                background-color: #2a2d2e;
            }
            
            QTreeWidget::item:selected {
                background-color: #005fb8;
                color: #ffffff;
            }
            
            QTreeWidget::branch:has-children:!has-siblings:closed,
            QTreeWidget::branch:closed:has-children:has-siblings {
                image: url(none);
            }
            
            /* Tab Widget */
            QTabWidget::pane {
                border: 1px solid #3c3c3c;
                background-color: #1e1e1e;
            }
            
            QTabBar::tab {
                background-color: #2d2d30;
                color: #cccccc;
                padding: 8px 16px;
                border: 1px solid #3c3c3c;
                border-bottom: none;
                margin-right: 2px;
            }
            
            QTabBar::tab:selected {
                background-color: #1e1e1e;
                color: #ffffff;
                border-bottom: 2px solid #005fb8;
            }
            
            QTabBar::tab:hover:!selected {
                background-color: #383838;
            }
            
            /* Text Edit (SQL Editor) */
            QTextEdit {
                background-color: #1e1e1e;
                color: #d4d4d4;
                border: 1px solid #3c3c3c;
                font-family: 'Monaco', 'Menlo', 'Courier New', monospace;
                font-size: 13px;
                selection-background-color: #264f78;
                selection-color: #ffffff;
            }
            
            /* Line Edit */
            QLineEdit {
                background-color: #3c3c3c;
                color: #cccccc;
                border: 1px solid #555555;
                padding: 5px;
                border-radius: 3px;
            }
            
            QLineEdit:focus {
                border: 1px solid #005fb8;
            }
            
            /* Combo Box */
            QComboBox {
                background-color: #3c3c3c;
                color: #cccccc;
                border: 1px solid #555555;
                padding: 5px;
                border-radius: 3px;
            }
            
            QComboBox:hover {
                border: 1px solid #005fb8;
            }
            
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            
            QComboBox QAbstractItemView {
                background-color: #3c3c3c;
                color: #cccccc;
                selection-background-color: #005fb8;
                border: 1px solid #555555;
            }
            
            /* Buttons */
            QPushButton {
                background-color: #005fb8;
                color: #ffffff;
                border: none;
                padding: 6px 16px;
                border-radius: 3px;
                font-weight: 500;
            }
            
            QPushButton:hover {
                background-color: #0078d4;
            }
            
            QPushButton:pressed {
                background-color: #004a99;
            }
            
            QPushButton:disabled {
                background-color: #3c3c3c;
                color: #666666;
            }
            
            /* Menu Bar */
            QMenuBar {
                background-color: #2d2d30;
                color: #cccccc;
                border-bottom: 1px solid #3c3c3c;
            }
            
            QMenuBar::item:selected {
                background-color: #005fb8;
            }
            
            /* Menu */
            QMenu {
                background-color: #252526;
                color: #cccccc;
                border: 1px solid #454545;
            }
            
            QMenu::item:selected {
                background-color: #005fb8;
            }
            
            /* Splitter */
            QSplitter::handle {
                background-color: #3c3c3c;
                width: 2px;
            }
            
            QSplitter::handle:hover {
                background-color: #005fb8;
            }
            
            /* Scroll Bar */
            QScrollBar:vertical {
                background-color: #1e1e1e;
                width: 14px;
                border: none;
            }
            
            QScrollBar::handle:vertical {
                background-color: #424242;
                min-height: 30px;
                border-radius: 7px;
            }
            
            QScrollBar::handle:vertical:hover {
                background-color: #4f4f4f;
            }
            
            QScrollBar:horizontal {
                background-color: #1e1e1e;
                height: 14px;
                border: none;
            }
            
            QScrollBar::handle:horizontal {
                background-color: #424242;
                min-width: 30px;
                border-radius: 7px;
            }
            
            QScrollBar::handle:horizontal:hover {
                background-color: #4f4f4f;
            }
            
            QScrollBar::add-line, QScrollBar::sub-line {
                border: none;
                background: none;
            }
            
            /* Labels */
            QLabel {
                color: #cccccc;
                background-color: transparent;
            }
            
            /* Table Widget - defined in editable_table.py */
        """
    
    @staticmethod
    def get_filter_container_style_dark():
        """Get dark theme styles for filter containers"""
        return """
            QWidget {
                background-color: #2a2a2a;
                border: 1px solid #3c3c3c;
                border-radius: 4px;
            }
            QComboBox {
                padding: 3px 6px;
                border: 1px solid #555555;
                border-radius: 2px;
                background-color: #3c3c3c;
                color: #cccccc;
                min-height: 20px;
                font-size: 12px;
            }
            QComboBox:hover {
                border-color: #0078d4;
            }
            QComboBox::drop-down {
                border: none;
                width: 16px;
            }
            QComboBox QAbstractItemView {
                background-color: #3c3c3c;
                color: #cccccc;
                selection-background-color: #0078d4;
                border: 1px solid #555555;
            }
            QLineEdit {
                padding: 3px 6px;
                border: 1px solid #555555;
                border-radius: 2px;
                background-color: #3c3c3c;
                color: #cccccc;
                min-height: 20px;
                font-size: 12px;
            }
            QLineEdit:focus {
                border-color: #0078d4;
            }
        """
    
    @staticmethod
    def get_filter_container_style_light():
        """Get light theme styles for filter containers"""
        return """
            QWidget {
                background-color: #f8f8f8;
                border: 1px solid #d0d0d0;
                border-radius: 4px;
            }
            QComboBox {
                padding: 3px 6px;
                border: 1px solid #c0c0c0;
                border-radius: 2px;
                background-color: #ffffff;
                color: #000000;
                min-height: 20px;
                font-size: 12px;
            }
            QComboBox:hover {
                border-color: #0078d4;
            }
            QComboBox::drop-down {
                border: none;
                width: 16px;
            }
            QComboBox QAbstractItemView {
                background-color: #ffffff;
                color: #000000;
                selection-background-color: #cce8ff;
                selection-color: #000000;
                border: 1px solid #c0c0c0;
            }
            QLineEdit {
                padding: 3px 6px;
                border: 1px solid #c0c0c0;
                border-radius: 2px;
                background-color: #ffffff;
                color: #000000;
                min-height: 20px;
                font-size: 12px;
            }
            QLineEdit:focus {
                border-color: #0078d4;
            }
        """
    
    @staticmethod
    def get_light_theme():
        """Get light theme stylesheet"""
        return """
            /* Main Window */
            QMainWindow {
                background-color: #ffffff;
                color: #000000;
            }
            
            /* Widgets */
            QWidget {
                background-color: #ffffff;
                color: #000000;
            }
            
            /* Tree Widget (Schema Browser) */
            QTreeWidget {
                background-color: #ffffff;
                color: #000000;
                border: 1px solid #d0d0d0;
                outline: none;
            }
            
            QTreeWidget::item:hover {
                background-color: #f0f0f0;
            }
            
            QTreeWidget::item:selected {
                background-color: #0078d4;
                color: #ffffff;
            }
            
            /* Tab Widget */
            QTabWidget::pane {
                border: 1px solid #d0d0d0;
                background-color: #ffffff;
            }
            
            QTabBar::tab {
                background-color: #f3f3f3;
                color: #000000;
                padding: 8px 16px;
                border: 1px solid #d0d0d0;
                border-bottom: none;
                margin-right: 2px;
            }
            
            QTabBar::tab:selected {
                background-color: #ffffff;
                color: #000000;
                border-bottom: 2px solid #0078d4;
            }
            
            QTabBar::tab:hover:!selected {
                background-color: #e8e8e8;
            }
            
            /* Text Edit (SQL Editor) */
            QTextEdit {
                background-color: #ffffff;
                color: #000000;
                border: 1px solid #d0d0d0;
                font-family: 'Monaco', 'Menlo', 'Courier New', monospace;
                font-size: 13px;
                selection-background-color: #cce8ff;
                selection-color: #000000;
            }
            
            /* Line Edit */
            QLineEdit {
                background-color: #ffffff;
                color: #000000;
                border: 1px solid #d0d0d0;
                padding: 5px;
                border-radius: 3px;
            }
            
            QLineEdit:focus {
                border: 1px solid #0078d4;
            }
            
            /* Combo Box */
            QComboBox {
                background-color: #ffffff;
                color: #000000;
                border: 1px solid #d0d0d0;
                padding: 5px;
                border-radius: 3px;
            }
            
            QComboBox:hover {
                border: 1px solid #0078d4;
            }
            
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            
            QComboBox QAbstractItemView {
                background-color: #ffffff;
                color: #000000;
                selection-background-color: #0078d4;
                selection-color: #ffffff;
                border: 1px solid #d0d0d0;
            }
            
            /* Buttons */
            QPushButton {
                background-color: #0078d4;
                color: #ffffff;
                border: 1px solid #0078d4;
                padding: 6px 16px;
                border-radius: 3px;
                font-weight: 500;
            }
            
            QPushButton:hover {
                background-color: #1890ff;
                border-color: #1890ff;
            }
            
            QPushButton:pressed {
                background-color: #0066b8;
                border-color: #0066b8;
            }
            
            QPushButton:disabled {
                background-color: #f0f0f0;
                color: #a0a0a0;
                border-color: #d0d0d0;
            }
            
            QPushButton:checked {
                background-color: #005a9e;
                border-color: #005a9e;
            }
            
            /* Menu Bar */
            QMenuBar {
                background-color: #f3f3f3;
                color: #000000;
                border-bottom: 1px solid #d0d0d0;
            }
            
            QMenuBar::item:selected {
                background-color: #e8e8e8;
            }
            
            /* Menu */
            QMenu {
                background-color: #ffffff;
                color: #000000;
                border: 1px solid #d0d0d0;
            }
            
            QMenu::item:selected {
                background-color: #e8e8e8;
            }
            
            /* Splitter */
            QSplitter::handle {
                background-color: #d0d0d0;
                width: 2px;
            }
            
            QSplitter::handle:hover {
                background-color: #0078d4;
            }
            
            /* Scroll Bar */
            QScrollBar:vertical {
                background-color: #f0f0f0;
                width: 14px;
                border: none;
            }
            
            QScrollBar::handle:vertical {
                background-color: #c1c1c1;
                min-height: 30px;
                border-radius: 7px;
            }
            
            QScrollBar::handle:vertical:hover {
                background-color: #a8a8a8;
            }
            
            QScrollBar:horizontal {
                background-color: #f0f0f0;
                height: 14px;
                border: none;
            }
            
            QScrollBar::handle:horizontal {
                background-color: #c1c1c1;
                min-width: 30px;
                border-radius: 7px;
            }
            
            QScrollBar::handle:horizontal:hover {
                background-color: #a8a8a8;
            }
            
            QScrollBar::add-line, QScrollBar::sub-line {
                border: none;
                background: none;
            }
            
            /* Labels */
            QLabel {
                color: #000000;
                background-color: transparent;
            }
        """
