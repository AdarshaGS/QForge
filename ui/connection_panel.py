"""
ConnectionPanel
═══════════════
A self-contained widget that owns one database connection and provides:
  • Left sidebar  – database selector, table search, schema tree
  • Right area    – tab bar with table views / SQL query tabs

Multiple ConnectionPanels are stacked in MainWindow behind a top-level
connection tab bar, giving a TablePlus-style multi-connection experience.
"""

import os
import time
import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTreeWidget, QTreeWidgetItem, QTabWidget,
    QLineEdit, QMessageBox, QInputDialog,
    QMenu, QProgressDialog, QPushButton, QLabel
)
from PySide6.QtGui import QShortcut, QKeySequence, QCursor

from services.db_service import DbService
from services.query_history import QueryHistory
from ui.sql_tab import SqlTab
from ui.table_view_widget import TableViewWidget
from ui.quick_search_dialog import QuickSearchDialog
from ui.structure_editor import StructureEditorDialog
from ui.db_switcher_dialog import DbSwitcherDialog
from ui.query_history_dialog import QueryHistoryDialog
from utils.logger import get_logger

logger = get_logger()


class ConnectionPanel(QWidget):
    """One database connection panel (sidebar + content tabs)."""

    # Emitted when this connection should be closed.
    close_requested = Signal(object)    # emits self
    # Emitted when the connection label changes.
    label_changed = Signal(object, str) # emits (self, new_label)

    def __init__(self, config: dict, db_service: DbService,
                 query_history: QueryHistory, parent=None):
        super().__init__(parent)

        self.config = config
        self.db_service = db_service
        self.query_history = query_history

        self.all_tables = []
        self.all_table_items = {}
        self.table_index = {}
        self.all_schema_items = []
        self.current_theme = "dark"
        self._available_dbs: list[str] = []
        self._column_cache: dict = {}   # {table: [col, ...]} for autocomplete

        self._build_ui()
        self.load_schema()

    # ─── UI construction ──────────────────────────────────────────────────────

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(1)

        # ── Left panel ────────────────────────────────────────────────────────
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(2, 2, 2, 2)
        left_layout.setSpacing(2)

        # Database pill button (replaces QComboBox)
        self.db_pill = QPushButton()
        self.db_pill.setToolTip("Switch database (Cmd+K)")
        self.db_pill.clicked.connect(self.show_db_switcher)
        self._apply_pill_style()
        left_layout.addWidget(self.db_pill)

        # Cmd+K shortcut (also works as Ctrl+K on non-mac)
        QShortcut(QKeySequence("Ctrl+K"), self).activated.connect(self.show_db_switcher)

        self.table_search = QLineEdit()
        self.table_search.setPlaceholderText("Search tables...")
        self.table_search.textChanged.connect(self.filter_tables)
        left_layout.addWidget(self.table_search)

        self.schema_tree = QTreeWidget()
        self.schema_tree.setHeaderLabel("Schema")
        self.schema_tree.setIndentation(15)
        self.schema_tree.setAnimated(True)
        self.schema_tree.itemClicked.connect(self._on_item_clicked)
        self.schema_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.schema_tree.customContextMenuRequested.connect(self._show_context_menu)
        left_layout.addWidget(self.schema_tree)

        splitter.addWidget(left)

        # ── Right panel (content tabs) ────────────────────────────────────────
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.tabs.tabBarDoubleClicked.connect(self._rename_tab)
        right_layout.addWidget(self.tabs)

        splitter.addWidget(right)
        splitter.setSizes([200, 1400])
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        root.addWidget(splitter)

    # ─── Schema loading ───────────────────────────────────────────────────────

    def load_schema(self):
        self.schema_tree.clear()
        self.all_tables.clear()
        self.all_table_items.clear()
        self.all_schema_items.clear()

        self._load_databases()

        try:
            tables_cat = QTreeWidgetItem(["Tables"])
            tables_cat.setExpanded(True)
            views_cat = QTreeWidgetItem(["Views"])
            functions_cat = QTreeWidgetItem(["Functions/Procedures"])

            tables = self.db_service.get_tables()
            # Load all column names in one query for autocomplete
            try:
                self._column_cache = self.db_service.get_all_columns()
            except Exception:
                self._column_cache = {}

            for table_name in tables:
                self.all_tables.append(table_name)
                self.all_schema_items.append(("table", table_name))

                if len(table_name) >= 3:
                    prefix = table_name[:3].lower()
                    self.table_index.setdefault(prefix, []).append(table_name)

                item = QTreeWidgetItem([table_name])
                self.all_table_items[table_name] = item
                tables_cat.addChild(item)

            views = self.db_service.get_views()
            for v in views:
                self.all_schema_items.append(("view", v))
                views_cat.addChild(QTreeWidgetItem([v]))

            functions = self.db_service.get_functions()
            for fn in functions:
                self.all_schema_items.append(("function", fn))
                functions_cat.addChild(QTreeWidgetItem([fn]))

            self.schema_tree.addTopLevelItem(tables_cat)
            if views:
                self.schema_tree.addTopLevelItem(views_cat)
            if functions:
                self.schema_tree.addTopLevelItem(functions_cat)
            tables_cat.setExpanded(True)

            # Update autocomplete in existing tabs
            for i in range(self.tabs.count()):
                tab = self.tabs.widget(i)
                if isinstance(tab, SqlTab):
                    tab.set_schema(tables, self._column_cache)
                elif isinstance(tab, TableViewWidget):
                    tab.set_schema((tables, self._column_cache))

        except Exception as ex:
            QMessageBox.critical(self, "Schema Error", str(ex))

    def _load_databases(self):
        """Fetch available databases and update pill label."""
        try:
            db_type = self.db_service.db_type
            if db_type == "mysql":
                df = self.db_service.execute_query("SHOW DATABASES")
                dbs = [d for d in df.iloc[:, 0].tolist()
                       if d not in ("information_schema", "mysql",
                                    "performance_schema", "sys")]
                self._available_dbs = dbs

                current_db = self.config.get("database", "")
                if current_db not in dbs and dbs:
                    current_db = dbs[0]
                    self.config["database"] = current_db
                    self.db_service.connection.select_db(current_db)

            elif db_type == "postgresql":
                df = self.db_service.execute_query(
                    "SELECT datname FROM pg_database WHERE datistemplate = false")
                self._available_dbs = df["datname"].tolist()
            else:
                self._available_dbs = []
        except Exception as ex:
            logger.error(f"Failed to load databases: {ex}")
            self._available_dbs = []

        self._update_pill_label()

    def _update_pill_label(self):
        current_db = self.config.get("database", "") or "(no database)"
        suffix = "  ⌘K" if self._available_dbs else ""
        self.db_pill.setText(f"  {current_db}{suffix}")
        self.db_pill.setEnabled(bool(self._available_dbs))

    def show_db_switcher(self):
        """Open the Cmd+K database switcher dialog."""
        if not self._available_dbs:
            return
        current_db = self.config.get("database", "")
        dialog = DbSwitcherDialog(self._available_dbs, current_db, self)
        # Center below the pill button
        dialog.move(self.db_pill.mapToGlobal(
            self.db_pill.rect().bottomLeft()))
        dialog.db_selected.connect(self._switch_database)
        dialog.exec()

    def _switch_database(self, new_db: str):
        if new_db == self.config.get("database", ""):
            return
        try:
            from PySide6.QtCore import QCoreApplication
            self.config["database"] = new_db
            self.db_service.disconnect()
            self.db_service.connect(self.config)
            self.load_schema()
        except Exception as ex:
            QMessageBox.critical(self, "Error", f"Failed to switch database: {ex}")

    # ─── Schema tree interaction ──────────────────────────────────────────────

    def _on_item_clicked(self, item, column):
        if item.parent() is None:
            return
        if item.parent().text(0) in ("Tables", "Views"):
            self.open_table_view(item.text(0))

    def _show_context_menu(self, position):
        item = self.schema_tree.itemAt(position)
        if not item or item.parent() is None:
            return
        if item.parent().text(0) not in ("Tables", "Views"):
            return

        menu = QMenu(self)
        open_action = menu.addAction("📋 Open Table")
        menu.addSeparator()
        edit_action = menu.addAction("✏️ Edit Structure")
        menu.addSeparator()
        refresh_action = menu.addAction("🔄 Refresh Schema")

        action = menu.exec_(self.schema_tree.mapToGlobal(position))
        if action == open_action:
            self.open_table_view(item.text(0))
        elif action == edit_action:
            self.show_alter_table_editor(item.text(0))
        elif action == refresh_action:
            self.load_schema()

    # ─── Table/query tabs ─────────────────────────────────────────────────────

    def open_table_view(self, table_name: str):
        """Open a table view; re-focus if already open."""
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, TableViewWidget) and w.get_table_name() == table_name:
                self.tabs.setCurrentIndex(i)
                return

        tv = TableViewWidget(self.db_service, table_name)
        tv.execute_query_signal.connect(self._run_query_in_tab)
        tab_index = self.tabs.addTab(tv, table_name)
        self.tabs.setCurrentIndex(tab_index)

        def _on_dirty(is_dirty, widget=tv):
            real_idx = self.tabs.indexOf(widget)
            if real_idx < 0:
                return
            title = self.tabs.tabText(real_idx)
            if is_dirty and not title.startswith("* "):
                self.tabs.setTabText(real_idx, f"* {title}")
            elif not is_dirty and title.startswith("* "):
                self.tabs.setTabText(real_idx, title[2:])

        tv.dirty_changed.connect(_on_dirty)

        is_dark = self.current_theme == "dark"
        tv.update_theme(is_dark)

    def add_new_tab(self):
        """Open a blank SQL query tab."""
        tab = SqlTab()
        tab.run_btn.clicked.connect(lambda: self._run_query_in_tab(tab))
        # Push current schema so autocomplete works immediately
        tab.set_schema(self.all_tables, self._column_cache)
        count = self.tabs.count() + 1
        self.tabs.addTab(tab, f"Tab {count}")
        self.tabs.setCurrentWidget(tab)
        tab.update_theme(self.current_theme == "dark")
        # Focus the editor after the tab is fully shown
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, tab.editor.setFocus)

    def _run_query_in_tab(self, tab):
        """Execute the SQL in `tab` using this connection's db_service."""
        query = tab.get_query().strip()
        if not query:
            return

        progress = QProgressDialog("Executing query…", None, 0, 0, self)
        progress.setWindowTitle("Running Query")
        progress.setWindowModality(Qt.WindowModal)
        progress.setCancelButton(None)
        progress.setMinimumDuration(2000)
        progress.show()

        try:
            from PySide6.QtCore import QCoreApplication
            QCoreApplication.processEvents()

            start = time.time()
            df = self.db_service.execute_query(query)
            elapsed = time.time() - start
            progress.close()

            table_name = self._extract_table_name(query)
            tab.load_dataframe(df, table_name)
            tab.update_status(len(df), elapsed)

            self.query_history.add_query(
                query, self.config["name"], len(df), elapsed)
        except Exception as ex:
            progress.close()
            if hasattr(tab, 'show_error'):
                tab.show_error(str(ex))
            else:
                QMessageBox.critical(self, "SQL Error", str(ex))

    @staticmethod
    def _extract_table_name(query: str):
        m = re.search(r"from\s+`?(\w+)`?", query.lower())
        return m.group(1) if m else None

    def _close_tab(self, index):
        self.tabs.removeTab(index)

    def _rename_tab(self, index):
        if index < 0:
            return
        current = self.tabs.tabText(index)
        new_name, ok = QInputDialog.getText(
            self, "Rename Tab", "Tab name:", text=current)
        if ok and new_name:
            self.tabs.setTabText(index, new_name)

    # ─── Structure editor ─────────────────────────────────────────────────────

    def show_structure_editor(self):
        dialog = StructureEditorDialog(self.db_service.db_type, parent=self)
        if dialog.exec():
            try:
                sql = dialog.get_sql()
                reply = QMessageBox.question(
                    self, "Create Table",
                    f"Execute the following SQL?\n\n{sql}",
                    QMessageBox.Yes | QMessageBox.No)
                if reply == QMessageBox.Yes:
                    self.db_service.execute_update(sql)
                    QMessageBox.information(self, "Success", "Table created successfully")
                    self.load_schema()
            except Exception as ex:
                QMessageBox.critical(self, "Error", str(ex))

    def show_alter_table_editor(self, table_name: str):
        try:
            existing_columns = self.db_service.get_columns(table_name)
        except Exception as ex:
            QMessageBox.critical(self, "Error",
                                 f"Could not load columns for {table_name}:\n{ex}")
            return

        dialog = StructureEditorDialog(
            db_type=self.db_service.db_type,
            table_name=table_name,
            existing_columns=existing_columns,
            parent=self)

        if dialog.exec():
            try:
                sql = dialog.get_sql()
                if sql.strip().startswith("--"):
                    QMessageBox.information(self, "No Changes", sql)
                    return
                reply = QMessageBox.question(
                    self, "Alter Table",
                    f"Execute the following SQL?\n\n{sql}",
                    QMessageBox.Yes | QMessageBox.No)
                if reply == QMessageBox.Yes:
                    for stmt in sql.strip().split(";\n"):
                        s = stmt.strip().rstrip(";")
                        if s:
                            self.db_service.execute_update(s + ";")
                    QMessageBox.information(
                        self, "Success", f"Table {table_name} altered successfully")
                    self.load_schema()
            except Exception as ex:
                QMessageBox.critical(self, "Error", str(ex))

    # ─── Quick search ─────────────────────────────────────────────────────────

    def show_quick_search(self):
        if not self.all_schema_items:
            QMessageBox.information(self, "No Items",
                                    "No tables, views, or functions available")
            return
        dialog = QuickSearchDialog(self.all_schema_items, self)
        dialog.item_selected.connect(self._on_quick_search)
        dialog.exec()

    def _on_quick_search(self, item_type, item_name):
        if item_type in ("table", "view"):
            self.open_table_view(item_name)

    # ─── Query history ────────────────────────────────────────────────────────

    def show_query_history(self):
        dialog = QueryHistoryDialog(self.query_history, self)
        if dialog.exec():
            query = dialog.get_selected_query()
            if query:
                current = self.tabs.currentWidget()
                if isinstance(current, SqlTab):
                    current.set_query(query)
                else:
                    self.add_new_tab()
                    self.tabs.currentWidget().set_query(query)

    # ─── Table filter ─────────────────────────────────────────────────────────

    def filter_tables(self, search_text: str):
        search_text = search_text.lower().strip()
        if not search_text:
            for item in self.all_table_items.values():
                item.setHidden(False)
            return

        search_prefix = search_text[:3] if len(search_text) >= 3 else search_text
        indexed = set(self.table_index.get(search_prefix, []))
        search_norm = search_text.replace("_", "").replace("-", "").replace(" ", "")

        for table_name, item in self.all_table_items.items():
            if table_name in indexed:
                item.setHidden(False)
            else:
                norm = table_name.lower().replace("_", "").replace("-", "").replace(" ", "")
                item.setHidden(search_norm not in norm)

    # ─── Refresh ──────────────────────────────────────────────────────────────

    def refresh_current_view(self):
        w = self.tabs.currentWidget()
        if isinstance(w, TableViewWidget):
            w.current_page = 1
            w.load_table_data()
        elif isinstance(w, SqlTab) and w.get_query().strip():
            self._run_query_in_tab(w)
        else:
            self.load_schema()

    # ─── Theme ────────────────────────────────────────────────────────────────

    def _apply_pill_style(self):
        is_dark = self.current_theme == "dark"
        if is_dark:
            self.db_pill.setStyleSheet("""
                QPushButton {
                    text-align: left;
                    padding: 5px 10px;
                    border: 1px solid #3a3a3c;
                    border-radius: 6px;
                    background-color: #2c2c2e;
                    color: #e5e5ea;
                    font-size: 12px;
                    font-weight: 500;
                }
                QPushButton:hover {
                    border-color: #0A84FF;
                    background-color: #3a3a3c;
                    color: #ffffff;
                }
                QPushButton:pressed { background-color: #0A84FF33; }
            """)
        else:
            self.db_pill.setStyleSheet("""
                QPushButton {
                    text-align: left;
                    padding: 5px 10px;
                    border: 1px solid #c6c6c8;
                    border-radius: 6px;
                    background-color: #ffffff;
                    color: #1c1c1e;
                    font-size: 12px;
                    font-weight: 500;
                }
                QPushButton:hover {
                    border-color: #007AFF;
                    background-color: #f2f2f7;
                }
                QPushButton:pressed { background-color: #007AFF22; }
            """)

    def update_theme(self, is_dark: bool):
        self.current_theme = "dark" if is_dark else "light"
        self._apply_pill_style()

        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if hasattr(w, "update_theme"):
                w.update_theme(is_dark)

    # ─── Session helpers (called by MainWindow) ───────────────────────────────

    def get_session_tabs(self) -> list:
        """Return serialisable list of open tabs."""
        result = []
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, TableViewWidget):
                result.append({"type": "table", "name": w.get_table_name()})
            elif isinstance(w, SqlTab):
                query = w.get_query() if hasattr(w, "get_query") else ""
                result.append({
                    "type": "query",
                    "name": self.tabs.tabText(i),
                    "query": query
                })
        return result

    def restore_session_tabs(self, tabs: list):
        """Reopen tabs from saved session data."""
        for tab in tabs:
            try:
                if tab.get("type") == "table":
                    name = tab.get("name", "")
                    if name in self.all_tables:
                        self.open_table_view(name)
                elif tab.get("type") == "query":
                    self.add_new_tab()
                    idx = self.tabs.count() - 1
                    w = self.tabs.widget(idx)
                    label = tab.get("name", f"Tab {idx + 1}")
                    self.tabs.setTabText(idx, label)
                    if hasattr(w, "set_query") and tab.get("query"):
                        w.set_query(tab["query"])
            except Exception as ex:
                logger.error(f"Failed to restore tab {tab}: {ex}")

    # ─── Public helpers ───────────────────────────────────────────────────────

    @property
    def label(self) -> str:
        return self.config.get("name", "Connection")

    def disconnect(self):
        try:
            self.db_service.disconnect()
        except Exception:
            pass
