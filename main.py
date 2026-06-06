import sys
import time
import signal

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QSplitter,
    QMessageBox,
    QTabWidget,
    QInputDialog,
    QMenu,
    QLineEdit,
    QLabel,
    QComboBox,
    QProgressDialog
)

from services.db_service import DbService
from services.query_history import QueryHistory
from ui.connection_dialog import ConnectionDialog
from ui.sql_tab import SqlTab
from ui.query_history_dialog import QueryHistoryDialog
from ui.structure_editor import StructureEditorDialog
from ui.table_view_widget import TableViewWidget
from ui.quick_search_dialog import QuickSearchDialog
from ui.theme_manager import ThemeManager
from utils.logger import setup_logger, get_logger

# Setup logger
logger = setup_logger()


class MainWindow(QMainWindow):

    def __init__(self):

        super().__init__()

        self.db_service = DbService()
        self.query_history = QueryHistory()

        self.connection_config = None
        
        # Store all tables for filtering
        self.all_tables = []
        self.all_table_items = {}
        
        # OPTIMIZATION: Index for fast table search
        self.table_index = {}  # {prefix: [table_names]}
        
        # Store all schema items for quick search
        self.all_schema_items = []
        
        # Store multiple database connections
        self.db_connections = []  # List of {config, db_service, name}
        self.current_connection_index = 0
        
        # Theme management
        self.current_theme = 'dark'  # Default to dark theme
        self.apply_theme()

        self.init_connection()

        self.init_ui()

        self.load_schema()

    # =================================================
    # CONNECTION
    # =================================================

    def init_connection(self):

        while True:

            dialog = ConnectionDialog(auto_connect_last=True)

            result = dialog.exec()

            if not result:
                logger.info("User cancelled connection dialog")
                sys.exit()

            config = dialog.get_selected_connection()

            if not config:
                continue

            try:

                logger.info(f"Attempting connection to: {config['name']}")
                
                # Show progress dialog
                progress = QProgressDialog("Connecting to database...", None, 0, 0)
                progress.setWindowTitle("Connecting")
                progress.setWindowModality(Qt.WindowModal)
                progress.setCancelButton(None)
                progress.setMinimumDuration(0)
                progress.show()
                
                # Process events to show the dialog
                from PySide6.QtCore import QCoreApplication
                QCoreApplication.processEvents()
                
                self.db_service.connect(config)

                self.connection_config = config
                
                progress.setLabelText("Loading schema...")
                QCoreApplication.processEvents()
                
                logger.info(f"Successfully connected to: {config['name']}")
                
                # Add initial connection to list
                self.db_connections.append({
                    'config': config,
                    'db_service': self.db_service,
                    'name': config['name']
                })
                
                progress.close()
                
                return

            except Exception as ex:

                logger.error(f"Connection failed: {str(ex)}")
                print("Connection Error")
                print(str(ex))

                QMessageBox.critical(
                    None,
                    "Connection Error",
                    str(ex)
                )
    # =================================================
    # UI
    # =================================================

    def init_ui(self):

        self.setWindowTitle(
            f"SQL Workbench - {self.connection_config['name']}"
        )

        self.resize(1600, 900)
        self.setMinimumSize(1200, 700)  # Allow resizing with minimum bounds
        
        # Create menu bar
        self.create_menu_bar()

        central_widget = QWidget()

        self.setCentralWidget(
            central_widget
        )

        main_layout = QHBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        splitter = QSplitter(
            Qt.Horizontal
        )
        splitter.setHandleWidth(1)
        splitter.setContentsMargins(0, 0, 0, 0)

        # =====================================
        # LEFT PANEL
        # =====================================
        
        left_panel = QWidget()
        left_layout = QVBoxLayout()
        left_layout.setContentsMargins(2, 2, 2, 2)
        left_layout.setSpacing(2)
        
        # Database selector with smooth styling
        self.database_combo = QComboBox()
        self.database_combo.currentIndexChanged.connect(self.switch_database)
        # Theme will be applied by update_database_combo_theme()
        self.update_database_combo_theme(is_dark=True)  # Apply initial dark theme
        left_layout.addWidget(self.database_combo)
        
        # Table search/filter
        self.table_search = QLineEdit()
        self.table_search.setPlaceholderText("Search tables...")
        self.table_search.textChanged.connect(self.filter_tables)
        left_layout.addWidget(self.table_search)
        
        # Connections section removed for cleaner UI

        self.schema_tree = QTreeWidget()
        self.schema_tree.setHeaderLabel("Schema")
        self.schema_tree.setIndentation(15)  # Add indentation for collapsible categories
        self.schema_tree.setAnimated(True)  # Smooth expand/collapse animation
        self.schema_tree.itemClicked.connect(self.table_clicked)
        
        # Add context menu for schema tree
        self.schema_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.schema_tree.customContextMenuRequested.connect(
            self.show_schema_context_menu
        )

        left_layout.addWidget(
            self.schema_tree
        )
        
        left_panel.setLayout(left_layout)
        
        splitter.addWidget(
            left_panel
        )

        # =====================================
        # RIGHT PANEL
        # =====================================

        right_panel = QWidget()

        right_layout = QVBoxLayout()
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)  # Cleaner look
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.tabBarDoubleClicked.connect(self.rename_tab)
        
        # Remove extra spacing around tabs
        self.tabs.setContentsMargins(0, 0, 0, 0)
        
        right_layout.addWidget(self.tabs)

        right_panel.setLayout(
            right_layout
        )

        splitter.addWidget(
            right_panel
        )

        splitter.setSizes(
            [200, 1400]
        )
        
        # Enable collapsible panels
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        splitter.setStretchFactor(0, 0)  # Left panel doesn't stretch
        splitter.setStretchFactor(1, 1)  # Right panel stretches

        main_layout.addWidget(
            splitter
        )

        central_widget.setLayout(
            main_layout
        )

        # Button connections removed - no toolbar buttons

        # Don't add initial tab - let user click on a table first
        # self.add_new_tab()

        # Status bar removed - cleaner UI like TablePlus
        self.statusBar().hide()
        
        # Add keyboard shortcuts
        from PySide6.QtGui import QShortcut, QKeySequence
        
        # Cmd+T for new tab
        self.new_tab_shortcut = QShortcut(QKeySequence("Ctrl+T"), self)
        self.new_tab_shortcut.activated.connect(self.add_new_tab)
        
        # Cmd+P for quick search
        self.quick_search_shortcut = QShortcut(QKeySequence("Ctrl+P"), self)
        self.quick_search_shortcut.activated.connect(self.show_quick_search)
        
        # F5 for refresh
        self.refresh_shortcut = QShortcut(QKeySequence("F5"), self)
        self.refresh_shortcut.activated.connect(self.refresh_current_view)
    
    def refresh_current_view(self):
        """Refresh the current active tab"""
        current_widget = self.tabs.currentWidget()
        
        if isinstance(current_widget, TableViewWidget):
            # Refresh table data
            current_widget.current_page = 1
            current_widget.load_table_data()
            self.statusBar().showMessage(f"Refreshed {current_widget.get_table_name()}", 2000)
        elif isinstance(current_widget, SqlTab):
            # Re-run the last query
            if hasattr(current_widget, 'get_query') and current_widget.get_query().strip():
                self.execute_query(current_widget)
                self.statusBar().showMessage("Query re-executed", 2000)
            else:
                self.statusBar().showMessage("No query to refresh", 2000)
        else:
            # Refresh schema if no specific tab
            self.load_schema()
            self.statusBar().showMessage("Schema refreshed", 2000)

    # =================================================
    # TABS
    # =================================================

    def add_new_tab(self):

        tab = SqlTab()

        tab.run_btn.clicked.connect(
            lambda: self.execute_query(tab)
        )

        count = self.tabs.count() + 1

        self.tabs.addTab(
            tab,
            f"Tab {count}"
        )
        self.tabs.setCurrentWidget(tab)
        
        # Apply current theme to new tab
        is_dark = self.current_theme == 'dark'
        tab.update_theme(is_dark)

        self.tabs.setCurrentWidget(
            tab
        )

    def close_tab(self, index):
        """Close tab by index"""
        self.tabs.removeTab(index)
    
    def close_current_tab(self):
        """Close the currently active tab"""
        current_index = self.tabs.currentIndex()
        if current_index >= 0:
            self.close_tab(current_index)

    def rename_tab(self, index):

        if index < 0:
            return

        current_name = (
            self.tabs.tabText(index)
        )

        new_name, ok = (
            QInputDialog.getText(
                self,
                "Rename Tab",
                "Tab Name:",
                text=current_name
            )
        )

        if ok and new_name:

            self.tabs.setTabText(
                index,
                new_name
            )

    # =================================================
    # SCHEMA
    # =================================================

    def load_schema(self):

        self.schema_tree.clear()
        self.all_tables.clear()
        self.all_table_items.clear()
        self.all_schema_items.clear()
        
        # Load available databases
        self.load_databases()

        try:
            # Create main categories
            tables_category = QTreeWidgetItem(["Tables"])
            tables_category.setExpanded(True)
            
            views_category = QTreeWidgetItem(["Views"])
            views_category.setExpanded(True)
            
            functions_category = QTreeWidgetItem(["Functions/Procedures"])
            functions_category.setExpanded(True)

            tables = (
                self.db_service.get_tables()
            )
            
            # Store columns for autocomplete
            columns_dict = {}

            # OPTIMIZATION: Load tables first, columns lazily when needed
            for table_name in tables:
                
                self.all_tables.append(table_name)
                self.all_schema_items.append(('table', table_name))
                
                # OPTIMIZATION: Build search index (first 3 chars)
                if len(table_name) >= 3:
                    prefix = table_name[:3].lower()
                    if prefix not in self.table_index:
                        self.table_index[prefix] = []
                    self.table_index[prefix].append(table_name)

                table_item = (
                    QTreeWidgetItem(
                        [table_name]
                    )
                )
                
                self.all_table_items[table_name] = table_item

                # Don't load columns here - huge performance bottleneck!
                # Columns will be loaded lazily when opening table or for autocomplete
                tables_category.addChild(
                    table_item
                )
            
            # Skip column loading - load on-demand for faster schema loading
            logger.info(f"Loading schema: {len(tables)} tables found")
            
            # Get views (don't load columns - lazy load)
            views = self.db_service.get_views()
            logger.info(f"Loading schema: {len(views)} views found")
            for view_name in views:
                self.all_schema_items.append(('view', view_name))
                view_item = QTreeWidgetItem([view_name])
                views_category.addChild(view_item)
            
            # Get functions/procedures
            functions = self.db_service.get_functions()
            for func_name in functions:
                self.all_schema_items.append(('function', func_name))
                func_item = QTreeWidgetItem([func_name])
                functions_category.addChild(func_item)
            
            # Add categories to tree
            self.schema_tree.addTopLevelItem(tables_category)
            if views:  # Only show if there are views
                self.schema_tree.addTopLevelItem(views_category)
            if functions:  # Only show if there are functions
                self.schema_tree.addTopLevelItem(functions_category)
            
            # Expand Tables category by default
            tables_category.setExpanded(True)
            
            # Update autocomplete for all tabs
            for i in range(self.tabs.count()):
                tab = self.tabs.widget(i)
                if isinstance(tab, SqlTab):
                    tab.set_schema(tables, columns_dict)
                elif isinstance(tab, TableViewWidget):
                    tab.set_schema((tables, columns_dict))

        except Exception as ex:

            QMessageBox.critical(
                self,
                "Schema Error",
                str(ex)
            )

    # =================================================
    # TABLE CLICK
    # =================================================

    def table_clicked(
        self,
        item,
        column
    ):
        # Skip if it's a category header or function
        if item.parent() is None:
            return
        
        # Only open table view for tables and views (not functions)
        parent = item.parent()
        if parent.text(0) in ["Tables", "Views"]:
            self.open_table_view(item.text(0))
    
    def show_schema_context_menu(self, position):
        """Show context menu for schema tree"""
        item = self.schema_tree.itemAt(position)
        
        if not item:
            return
        
        # Check if it's a table or view (not a category or function)
        if item.parent() is None:
            return
        
        parent = item.parent()
        if parent.text(0) not in ["Tables", "Views"]:
            return
        
        menu = QMenu(self)
        
        view_table_action = menu.addAction("📋 Open Table")
        menu.addSeparator()
        refresh_action = menu.addAction("🔄 Refresh Schema")
        
        action = menu.exec_(self.schema_tree.mapToGlobal(position))
        
        if action == view_table_action:
            self.open_table_view(item.text(0))
        elif action == refresh_action:
            self.load_schema()
    
    def show_quick_search(self):
        """Show quick search dialog for all schema items"""
        if not self.all_schema_items:
            QMessageBox.information(self, "No Items", "No tables, views, or functions available to search")
            return
        
        # Create and show quick search dialog
        dialog = QuickSearchDialog(self.all_schema_items, self)
        dialog.item_selected.connect(self.on_quick_search_selected)
        dialog.exec()
    
    def on_quick_search_selected(self, item_type, item_name):
        """Handle item selected from quick search"""
        if item_type in ['table', 'view']:
            # Open table/view
            self.open_table_view(item_name)
        elif item_type == 'function':
            # For functions, just show info
            QMessageBox.information(
                self,
                "Function Selected",
                f"Function: {item_name}\n\nFunctions cannot be opened as tables. Use query editor to call this function."
            )
    
    def open_table_view(self, table_name):
        """Open a table view widget with data, structure, and query tabs"""
        # Check if table is already open
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            if isinstance(tab, TableViewWidget) and tab.get_table_name() == table_name:
                self.tabs.setCurrentIndex(i)
                return
        
        # Create new table view
        table_view = TableViewWidget(self.db_service, table_name)
        table_view.execute_query_signal.connect(self.execute_query)
        
        # No query tab in table views - removed set_schema call
        
        # Add tab
        self.tabs.addTab(table_view, table_name)
        self.tabs.setCurrentWidget(table_view)
        
        # Apply current theme to new table view
        is_dark = self.current_theme == 'dark'
        table_view.update_theme(is_dark)

    # =================================================
    # EXECUTE
    # =================================================

    def execute_query(self, tab):

        query = (
            tab.get_query()
            .strip()
        )

        if not query:
            return

        # Create progress dialog for long-running queries
        progress = QProgressDialog("Executing query...", None, 0, 0, self)
        progress.setWindowTitle("Running Query")
        progress.setWindowModality(Qt.WindowModal)
        progress.setCancelButton(None)
        progress.setMinimumDuration(2000)  # Show after 2 seconds
        progress.show()
        
        try:

            logger.debug(f"Executing query: {query[:100]}...")
            start = time.time()

            df = (
                self.db_service.execute_query(
                    query
                )
            )

            execution_time = (
                time.time() - start
            )
            
            progress.close()
            
            # Try to extract table name from query for inline editing
            table_name = self.extract_table_name(query)

            tab.load_dataframe(df, table_name)

            tab.update_status(
                len(df),
                execution_time
            )
            
            logger.info(f"Query executed successfully: {len(df)} rows in {execution_time:.3f}s")
            
            # Save to history
            self.query_history.add_query(
                query,
                self.connection_config['name'],
                len(df),
                execution_time
            )

        except Exception as ex:

            progress.close()
            logger.error(f"Query execution failed: {str(ex)}")
            QMessageBox.critical(
                self,
                "SQL Error",
                str(ex)
            )
    
    def extract_table_name(self, query):
        """Extract table name from SELECT query"""
        import re
        query_lower = query.lower()
        
        # Simple regex to find FROM table_name
        match = re.search(r'from\s+`?(\w+)`?', query_lower)
        if match:
            return match.group(1)
        return None
    
    def show_query_history(self, tab):
        """Show query history dialog"""
        dialog = QueryHistoryDialog(self.query_history, self)
        
        if dialog.exec():
            query = dialog.get_selected_query()
            if query:
                tab.set_query(query)
    
    def show_structure_editor(self):
        """Show structure editor for creating tables"""
        dialog = StructureEditorDialog(self.db_service.db_type, parent=self)
        
        if dialog.exec():
            try:
                sql = dialog.get_sql()
                
                # Show confirmation
                reply = QMessageBox.question(
                    self,
                    "Create Table",
                    f"Execute the following SQL?\n\n{sql}",
                    QMessageBox.Yes | QMessageBox.No
                )
                
                if reply == QMessageBox.Yes:
                    self.db_service.execute_update(sql)
                    QMessageBox.information(self, "Success", "Table created successfully")
                    
                    # Reload schema
                    self.load_schema()
                    
            except Exception as ex:
                logger.error(f"Failed to create table: {str(ex)}")
                QMessageBox.critical(self, "Error", str(ex))
    
    def create_menu_bar(self):
        """Create menu bar with Help menu"""
        menubar = self.menuBar()
        
        # File Menu
        file_menu = menubar.addMenu("File")
        
        new_tab_action = file_menu.addAction("New Query Tab")
        new_tab_action.triggered.connect(self.add_new_tab)
        new_tab_action.setShortcut("Ctrl+T")
        
        close_tab_action = file_menu.addAction("Close Tab")
        close_tab_action.triggered.connect(self.close_current_tab)
        close_tab_action.setShortcut("Ctrl+W")
        
        file_menu.addSeparator()
        
        export_action = file_menu.addAction("Export Data...")
        export_action.triggered.connect(self.export_current_data)
        export_action.setShortcut("Ctrl+E")
        
        import_action = file_menu.addAction("Import Data...")
        import_action.triggered.connect(self.import_data_to_table)
        import_action.setShortcut("Ctrl+Shift+I")
        
        file_menu.addSeparator()
        
        new_connection_action = file_menu.addAction("New Connection Window")
        new_connection_action.triggered.connect(self.open_new_connection)
        new_connection_action.setShortcut("Ctrl+N")
        
        file_menu.addSeparator()
        
        quit_action = file_menu.addAction("Quit")
        quit_action.triggered.connect(self.close)
        quit_action.setShortcut("Ctrl+Q")
        
        # View Menu (with Refresh)
        view_menu = menubar.addMenu("View")
        
        refresh_action = view_menu.addAction("Refresh")
        refresh_action.triggered.connect(self.refresh_current_view)
        refresh_action.setShortcut("Ctrl+R")
        
        view_menu.addSeparator()
        
        quick_search_action = view_menu.addAction("Quick Search")
        quick_search_action.triggered.connect(self.show_quick_search)
        quick_search_action.setShortcut("Ctrl+P")
        
        view_menu.addSeparator()
        
        # Theme toggle
        self.theme_action = view_menu.addAction("Switch to Light Theme")
        self.theme_action.triggered.connect(self.toggle_theme)
        
        view_menu.addSeparator()
        
        # Zoom options
        zoom_in_action = view_menu.addAction("Zoom In")
        zoom_in_action.setShortcut("Ctrl++")
        zoom_in_action.triggered.connect(self.zoom_in)
        
        zoom_out_action = view_menu.addAction("Zoom Out")
        zoom_out_action.setShortcut("Ctrl+-")
        zoom_out_action.triggered.connect(self.zoom_out)
        
        reset_zoom_action = view_menu.addAction("Reset Zoom")
        reset_zoom_action.setShortcut("Ctrl+0")
        reset_zoom_action.triggered.connect(self.reset_zoom)
        
        # Help Menu
        help_menu = menubar.addMenu("Help")
        
        shortcuts_action = help_menu.addAction("Keyboard Shortcuts")
        shortcuts_action.triggered.connect(self.show_keyboard_shortcuts)
    
    def open_new_connection(self):
        """Add a new connection in the same window"""
        dialog = ConnectionDialog(auto_connect_last=False)
        
        if dialog.exec():
            config = dialog.get_selected_connection()
            
            if config:
                try:
                    # Create new database service
                    new_db_service = DbService()
                    new_db_service.connect(config)
                    
                    # Add to connections list
                    conn_data = {
                        'config': config,
                        'db_service': new_db_service,
                        'name': config['name']
                    }
                    self.db_connections.append(conn_data)
                    
                    # Connections UI removed - just switch to this connection
                    
                    # Switch to this connection
                    self.current_connection_index = len(self.db_connections) - 1
                    self.switch_to_connection(self.current_connection_index)
                    
                except Exception as ex:
                    QMessageBox.critical(self, "Connection Error", str(ex))
    
    def show_keyboard_shortcuts(self):
        """Show keyboard shortcuts dialog"""
        shortcuts_text = """
<b>SQL Workbench Keyboard Shortcuts</b>

<b>Query Execution:</b>
• Ctrl+Return / Cmd+Return  —  Run query (selected, at cursor, or all)
• Ctrl+I / Cmd+I           —  Beautify SQL query (format & indent)
• Ctrl+Shift+I / Cmd+⇧+I   —  Minify SQL query (compress)
• Ctrl+Space               —  Trigger autocomplete manually

<b>Quick Search & Refresh:</b>
• Ctrl+P / Cmd+P  —  Quick search for tables, views, functions (shows top 15 results)
• Ctrl+R / Cmd+R  —  Refresh current view (table data or schema)

<b>Tabs & Navigation:</b>
• Ctrl+T / Cmd+T  —  New query tab
• Ctrl+W / Cmd+W  —  Close current tab/dialog
• Ctrl+Tab        —  Switch between tabs
• Click + button  —  New query tab (when table is open)

<b>Table Data:</b>
• Single click    —  Open table (fast!)
• Click column    —  Sort by column (1st click: DESC, 2nd: ASC, 3rd: no sort)
• Ctrl+F / Cmd+F  —  Toggle inline filter
• CONTAINS        —  Easy partial match filter (auto-adds % wildcards)
• ◀ Prev / Next ▶ —  Navigate pages (100 rows per page)

<b>Editing:</b>
• Ctrl+A / Cmd+A  —  Select all text
• Ctrl+C / Cmd+C  —  Copy
• Ctrl+V / Cmd+V  —  Paste
• Ctrl+Z / Cmd+Z  —  Undo
• Ctrl+Y / Cmd+Y  —  Redo
• Double-click    —  Edit cell value
• Delete          —  Mark row for deletion

<b>Application:</b>
• Ctrl+N / Cmd+N  —  New connection window
• Ctrl+Q / Cmd+Q  —  Quit application
        """
        
        QMessageBox.information(self, "Keyboard Shortcuts", shortcuts_text)
    
    def toggle_theme(self):
        """Toggle between dark and light themes"""
        if self.current_theme == 'dark':
            self.current_theme = 'light'
            self.theme_action.setText("Switch to Dark Theme")
        else:
            self.current_theme = 'dark'
            self.theme_action.setText("Switch to Light Theme")
        
        self.apply_theme()
    
    def apply_theme(self):
        """Apply the current theme to the application"""
        if self.current_theme == 'dark':
            stylesheet = ThemeManager.get_dark_theme()
            is_dark = True
        else:
            stylesheet = ThemeManager.get_light_theme()
            is_dark = False
        
        # Apply to application
        QApplication.instance().setStyleSheet(stylesheet)
        
        # Update database selector theme (if it exists)
        if hasattr(self, 'database_combo'):
            self.update_database_combo_theme(is_dark)
        
        # Update all tabs with filter containers (if tabs exist)
        if hasattr(self, 'tabs'):
            for i in range(self.tabs.count()):
                tab = self.tabs.widget(i)
                if hasattr(tab, 'update_theme'):
                    tab.update_theme(is_dark)
    
    def update_database_combo_theme(self, is_dark=True):
        """Update database combo box theme"""
        if is_dark:
            self.database_combo.setStyleSheet("""
                QComboBox {
                    padding: 6px 10px;
                    border: 1px solid #3c3c3c;
                    border-radius: 4px;
                    background-color: #2a2a2a;
                    color: #cccccc;
                    font-size: 13px;
                    font-weight: 500;
                }
                QComboBox:hover {
                    border-color: #0078d4;
                    background-color: #2d2d2d;
                }
                QComboBox::drop-down {
                    border: none;
                    width: 20px;
                }
                QComboBox::down-arrow {
                    image: none;
                    border-left: 4px solid transparent;
                    border-right: 4px solid transparent;
                    border-top: 5px solid #888888;
                    margin-right: 5px;
                }
                QComboBox QAbstractItemView {
                    background-color: #2a2a2a;
                    border: 1px solid #3c3c3c;
                    selection-background-color: #0078d4;
                    selection-color: #ffffff;
                    padding: 4px;
                }
            """)
        else:
            self.database_combo.setStyleSheet("""
                QComboBox {
                    padding: 6px 10px;
                    border: 1px solid #d0d0d0;
                    border-radius: 4px;
                    background-color: #ffffff;
                    color: #000000;
                    font-size: 13px;
                    font-weight: 500;
                }
                QComboBox:hover {
                    border-color: #0078d4;
                    background-color: #f8f8f8;
                }
                QComboBox::drop-down {
                    border: none;
                    width: 20px;
                }
                QComboBox::down-arrow {
                    image: none;
                    border-left: 4px solid transparent;
                    border-right: 4px solid transparent;
                    border-top: 5px solid #555555;
                    margin-right: 5px;
                }
                QComboBox QAbstractItemView {
                    background-color: #ffffff;
                    border: 1px solid #d0d0d0;
                    selection-background-color: #cce8ff;
                    selection-color: #000000;
                    padding: 4px;
                }
            """)
    
    def zoom_in(self):
        """Zoom in the interface"""
        current_tab = self.tabs.currentWidget()
        # Try SqlTab first (check for editor widget)
        if hasattr(current_tab, 'editor') and current_tab.editor is not None:
            font = current_tab.editor.font()
            current_size = font.pointSize()
            if current_size > 0 and current_size < 30:  # Safety check + Maximum zoom
                font.setPointSize(current_size + 1)
                current_tab.editor.setFont(font)
                self.statusBar().showMessage(f"Zoom: {current_size + 1}pt", 2000)
            elif current_size <= 0:
                # Fix invalid font size
                font.setPointSize(13)
                current_tab.editor.setFont(font)
                self.statusBar().showMessage("Zoom: Reset to 13pt", 2000)
        elif isinstance(current_tab, TableViewWidget):
            # Zoom table view
            if hasattr(current_tab, 'data_table'):
                font = current_tab.data_table.font()
                current_size = font.pointSize()
                if current_size > 0 and current_size < 30:
                    font.setPointSize(current_size + 1)
                    current_tab.data_table.setFont(font)
                    self.statusBar().showMessage(f"Zoom: {current_size + 1}pt", 2000)
        else:
            self.statusBar().showMessage("Zoom not available for this tab", 2000)
    
    def zoom_out(self):
        """Zoom out the interface"""
        current_tab = self.tabs.currentWidget()
        # Try SqlTab first (check for editor widget)
        if hasattr(current_tab, 'editor') and current_tab.editor is not None:
            font = current_tab.editor.font()
            current_size = font.pointSize()
            if current_size > 8:  # Minimum zoom
                font.setPointSize(current_size - 1)
                current_tab.editor.setFont(font)
                self.statusBar().showMessage(f"Zoom: {current_size - 1}pt", 2000)
            elif current_size <= 0:
                # Fix invalid font size
                font.setPointSize(13)
                current_tab.editor.setFont(font)
                self.statusBar().showMessage("Zoom: Reset to 13pt", 2000)
        elif isinstance(current_tab, TableViewWidget):
            # Zoom table view
            if hasattr(current_tab, 'data_table'):
                font = current_tab.data_table.font()
                current_size = font.pointSize()
                if current_size > 8:
                    font.setPointSize(current_size - 1)
                    current_tab.data_table.setFont(font)
                    self.statusBar().showMessage(f"Zoom: {current_size - 1}pt", 2000)
        else:
            self.statusBar().showMessage("Zoom not available for this tab", 2000)
    
    def reset_zoom(self):
        """Reset zoom to default"""
        current_tab = self.tabs.currentWidget()
        if hasattr(current_tab, 'editor'):
            font = current_tab.editor.font()
            font.setPointSize(13)  # Default size
            current_tab.editor.setFont(font)
            self.statusBar().showMessage("Zoom: Reset to 13pt", 2000)
        elif isinstance(current_tab, TableViewWidget):
            # Reset table view zoom
            if hasattr(current_tab, 'data_table'):
                font = current_tab.data_table.font()
                font.setPointSize(13)
                current_tab.data_table.setFont(font)
                self.statusBar().showMessage("Zoom: Reset to 13pt", 2000)
    
    def export_current_data(self):
        """Export data from current tab"""
        current_widget = self.tabs.currentWidget()
        if isinstance(current_widget, SqlTab):
            if current_widget.current_df is not None:
                current_widget.export_data()
            else:
                QMessageBox.information(self, "No Data", "No data to export. Run a query first.")
        elif isinstance(current_widget, TableViewWidget):
            # Export table data
            QMessageBox.information(self, "Export", "Table export feature - use export in query tab")
    
    def import_data_to_table(self):
        """Import data to table"""
        current_widget = self.tabs.currentWidget()
        if isinstance(current_widget, SqlTab):
            current_widget.import_data()
        else:
            QMessageBox.information(self, "Import", "Please open a query tab to import data")
    
    def filter_tables(self, search_text):
        """Filter tables in schema tree based on search text (OPTIMIZED with indexing)"""
        search_text = search_text.lower().strip()
        
        if not search_text:
            # Show all tables
            for table_name, table_item in self.all_table_items.items():
                table_item.setHidden(False)
        else:
            # OPTIMIZATION: Use index for prefix search first (fastest)
            search_prefix = search_text[:3] if len(search_text) >= 3 else search_text
            
            # Check index first for common prefix matches
            indexed_matches = set()
            if search_prefix in self.table_index:
                indexed_matches = set(self.table_index[search_prefix])
            
            # Fuzzy filter - remove underscores and special chars for matching
            search_normalized = search_text.replace('_', '').replace('-', '').replace(' ', '')
            
            for table_name, table_item in self.all_table_items.items():
                # Fast path: check index first
                if table_name in indexed_matches:
                    table_item.setHidden(False)
                    continue
                
                # Fallback: normalized fuzzy matching
                table_normalized = table_name.lower().replace('_', '').replace('-', '').replace(' ', '')
                
                # Match if normalized search is in normalized table name
                if search_normalized in table_normalized:
                    table_item.setHidden(False)
                else:
                    table_item.setHidden(True)
    
    def load_databases(self):
        """Load available databases on the current host"""
        try:
            # Block signals to prevent triggering switch_database during initialization
            self.database_combo.blockSignals(True)
            self.database_combo.clear()
            
            if self.db_service.db_type == 'mysql':
                # Get all databases
                databases_df = self.db_service.execute_query("SHOW DATABASES")
                databases = databases_df.iloc[:, 0].tolist()
                
                # Filter out system databases
                databases = [db for db in databases if db not in ['information_schema', 'mysql', 'performance_schema', 'sys']]
                
                self.database_combo.addItems(databases)
                self.database_combo.setEnabled(True)
                
                # Set current database if one was provided
                current_db = self.connection_config.get('database', '')
                if current_db:
                    index = self.database_combo.findText(current_db)
                    if index >= 0:
                        self.database_combo.setCurrentIndex(index)
                    else:
                        # Database not found, select first available
                        if len(databases) > 0:
                            self.database_combo.setCurrentIndex(0)
                            # Trigger database selection
                            self.connection_config['database'] = databases[0]
                            self.db_service.connection.select_db(databases[0])
                else:
                    # No database selected - prompt user to select one
                    if len(databases) > 0:
                        self.database_combo.setCurrentIndex(0)
                        # Auto-select first database
                        self.connection_config['database'] = databases[0]
                        self.db_service.connection.select_db(databases[0])
                    
            elif self.db_service.db_type == 'postgresql':
                # Get all databases
                databases_df = self.db_service.execute_query("SELECT datname FROM pg_database WHERE datistemplate = false")
                databases = databases_df['datname'].tolist()
                
                self.database_combo.addItems(databases)
                
                # Set current database
                current_db = self.connection_config.get('database', '')
                if current_db:
                    index = self.database_combo.findText(current_db)
                    if index >= 0:
                        self.database_combo.setCurrentIndex(index)
            else:
                # SQLite doesn't have multiple databases
                self.database_combo.setEnabled(False)
            
            # Unblock signals after initialization
            self.database_combo.blockSignals(False)
                
        except Exception as ex:
            logger.error(f"Failed to load databases: {str(ex)}")
            self.database_combo.setEnabled(False)
            self.database_combo.blockSignals(False)
    
    def switch_database(self, index):
        """Switch to a different database"""
        if index < 0:
            return
            
        new_database = self.database_combo.currentText()
        
        if new_database and new_database != self.connection_config.get('database', ''):
            try:
                # Show loading indicator
                from PySide6.QtCore import QCoreApplication
                progress = QProgressDialog("Switching database...", None, 0, 0)
                progress.setWindowTitle("Switching")
                progress.setWindowModality(Qt.WindowModal)
                progress.setCancelButton(None)
                progress.setMinimumDuration(0)
                progress.show()
                QCoreApplication.processEvents()
                
                # Update connection config
                self.connection_config['database'] = new_database
                
                # Reconnect with new database
                self.db_service.disconnect()
                self.db_service.connect(self.connection_config)
                
                progress.setLabelText("Loading schema...")
                QCoreApplication.processEvents()
                
                # Reload schema
                self.load_schema()
                
                progress.close()
                
                self.statusBar().showMessage(f"Switched to database: {new_database}")
            except Exception as ex:
                logger.error(f"Failed to switch database: {str(ex)}")
                QMessageBox.critical(self, "Error", f"Failed to switch database: {str(ex)}")
    
    def switch_connection(self, index):
        """Switch to a different connection tab"""
        if index >= 0 and index < len(self.db_connections):
            self.switch_to_connection(index)
    
    def switch_to_connection(self, index):
        """Switch to a specific connection"""
        if index >= 0 and index < len(self.db_connections):
            conn_data = self.db_connections[index]
            self.db_service = conn_data['db_service']
            self.connection_config = conn_data['config']
            self.current_connection_index = index
            
            # Update window title
            self.setWindowTitle(f"SQL Workbench - {conn_data['name']}")
            
            # Reload schema
            self.load_schema()
    
    def close_connection_tab(self, index):
        """Close a connection tab"""
        if len(self.db_connections) <= 1:
            QMessageBox.warning(self, "Warning", "Cannot close the last connection")
            return
        
        conn_data = self.db_connections[index]
        
        reply = QMessageBox.question(
            self,
            "Close Connection",
            f"Close connection to '{conn_data['name']}'?",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            # Disconnect and remove
            conn_data['db_service'].disconnect()
            self.db_connections.pop(index)
            # Connection tabs UI removed
            
            # Switch to another connection
            if index > 0:
                self.switch_to_connection(index - 1)
            else:
                self.switch_to_connection(0)


# =====================================================
# APP
# =====================================================

if __name__ == "__main__":
    # Handle Ctrl+C gracefully
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    logger.info("Starting SQL Workbench application")
    app = QApplication(sys.argv)

    window = MainWindow()

    window.show()

    sys.exit(app.exec())