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
import threading

from PySide6.QtCore import Qt, Signal, QThread, QObject
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTreeWidget, QTreeWidgetItem, QTabWidget, QTabBar,
    QLineEdit, QMessageBox, QInputDialog,
    QMenu, QProgressDialog, QPushButton, QLabel,
    QListWidget, QListWidgetItem, QStackedWidget,
)
from PySide6.QtGui import QShortcut, QKeySequence, QCursor

from services.db_service import DbService
from services.query_history import QueryHistory
from services.schema_snapshot import fetch_schema_snapshot
from ui.sql_tab import SqlTab
from ui.table_view_widget import TableViewWidget
from ui.quick_search_dialog import QuickSearchDialog
from ui.structure_editor import StructureEditorDialog
from ui.db_switcher_dialog import DbSwitcherDialog
from ui.query_history_dialog import QueryHistoryDialog
from ui.theme_manager import ThemeManager
from ui import query_guard_dialog
from utils.logger import get_logger
from utils import environment
from services import query_classifier

logger = get_logger()

# CSV imports at or above this row count are treated as a "mass write" for
# the dangerous-query guard on Staging/Production, even though a plain
# INSERT alone isn't otherwise flagged (ai/load-context.md: "mass writes,
# where a reliable row-count estimate is available" — a CSV import's
# DataFrame length is exactly that).
MASS_WRITE_ROW_THRESHOLD = 5000


# ── Background query worker (must be a top-level class for PySide6) ──────────

class _QueryWorker(QObject):
    """Runs one or more SQL statements on a QThread and emits the result.
    Receives a *dedicated* DbService connection so it never shares state
    with the main connection used for schema browsing."""
    done       = Signal(object, float)   # (DataFrame, elapsed_seconds)
    multi_done = Signal(list,  float)    # ([(label, df|Exception), ...], elapsed)
    errored    = Signal(str,   float)    # (error_message, elapsed_seconds)
    cancelled  = Signal()

    def __init__(self, db_service, query: str, cancel_flag, multi: bool = False):
        super().__init__()
        self._db    = db_service
        self._q     = query
        self._flag  = cancel_flag
        self._multi = multi

    def run(self):
        import sqlparse as _sp
        t0 = time.time()
        try:
            # Multi-statement: detect 2+ non-empty statements.
            # sqlparse.split raises SQLParseError on very large queries (>10k tokens);
            # fall back to treating the whole text as a single statement in that case.
            try:
                stmts = [s.strip() for s in _sp.split(self._q) if s.strip()]
            except Exception:
                stmts = [self._q.strip()]
            if self._multi or len(stmts) > 1:
                results = self._db.execute_multi_query(self._q)
                elapsed = time.time() - t0
                if self._flag.is_set():
                    self.cancelled.emit()
                else:
                    self.multi_done.emit(results, elapsed)
            else:
                df = self._db.execute_query(self._q)
                elapsed = time.time() - t0
                if self._flag.is_set():
                    self.cancelled.emit()
                else:
                    self.done.emit(df, elapsed)
        except Exception as ex:
            elapsed = time.time() - t0
            if self._flag.is_set():
                self.cancelled.emit()
            else:
                self.errored.emit(str(ex), elapsed)
        finally:
            # Close the dedicated query connection when done
            try:
                self._db.disconnect()
            except Exception:
                pass


class ConnectionPanel(QWidget):
    """One database connection panel (sidebar + content tabs)."""

    # Emitted when this connection should be closed.
    close_requested = Signal(object)    # emits self
    # Emitted when the connection label changes.
    label_changed = Signal(object, str) # emits (self, new_label)

    # ── Bridge signals used by _run_query_in_tab ──────────────────────────
    # These live on a QWidget (main thread), so QueuedConnection guarantees
    # the slots run on the main-thread event loop even if the worker thread
    # emits them. Using plain Python closures as QueuedConnection targets is
    # unreliable in PySide6 — bridge signals solve that cleanly.
    _q_done       = Signal(object, object, float)  # (tab, DataFrame, elapsed)
    _q_multi_done = Signal(object, list,   float)  # (tab, [(label,df),...], elapsed)
    _q_errored    = Signal(object, str,    float)  # (tab, message, elapsed)
    _q_cancelled  = Signal(object)                 # (tab,)
    # ── Public observability signals ─────────────────────────────────────
    # 'idle' / 'running' / 'disconnected'
    health_changed = Signal(str)
    # brief human-readable message (e.g. "Reconnected to MySQL")
    reconnected    = Signal(str)
    # Bridge signals for background schema load
    _schema_done   = Signal(object)   # (schema_data dict)
    _schema_error  = Signal(str)      # error message
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

        # Wire bridge signals → main-thread handlers (connected once here so
        # QueuedConnection always delivers on the main thread event loop).
        self._q_done.connect(self._on_query_done, Qt.QueuedConnection)
        self._q_multi_done.connect(self._on_query_multi_done, Qt.QueuedConnection)
        self._q_errored.connect(self._on_query_errored, Qt.QueuedConnection)
        self._q_cancelled.connect(self._on_query_cancelled, Qt.QueuedConnection)
        self._schema_done.connect(self._on_schema_loaded, Qt.QueuedConnection)
        self._schema_error.connect(self._on_schema_error, Qt.QueuedConnection)
        self._column_cache: dict = {}   # {table: [col, ...]} for autocomplete

        self._build_ui()
        self.load_schema()

        # ── Periodic health check (every 30 s) ──────────────────────────
        from PySide6.QtCore import QTimer
        self._health_timer = QTimer(self)
        self._health_timer.setInterval(30_000)
        self._health_timer.timeout.connect(self._check_health)
        self._health_timer.start()

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

        # ── Refresh Schema + Reconnect row ───────────────────────────
        _action_row = QWidget()
        _ar_layout = QHBoxLayout(_action_row)
        _ar_layout.setContentsMargins(0, 0, 0, 0)
        _ar_layout.setSpacing(4)

        _btn_style = """
            QPushButton {
                background: transparent;
                color: #8e8e93;
                border: 1px solid #3a3a3c;
                border-radius: 4px;
                padding: 2px 8px;
                font-size: 11px;
            }
            QPushButton:hover { color: #e5e5ea; border-color: #636366; }
        """
        self._refresh_schema_btn = QPushButton("↺ Schema")
        self._refresh_schema_btn.setToolTip("Refresh schema tree (Cmd+Shift+R)")
        self._refresh_schema_btn.setStyleSheet(_btn_style)
        self._refresh_schema_btn.clicked.connect(self.load_schema)
        _ar_layout.addWidget(self._refresh_schema_btn)

        self._reconnect_btn = QPushButton("⟳ Reconnect")
        self._reconnect_btn.setToolTip("Reconnect to the database")
        self._reconnect_btn.setStyleSheet(_btn_style)
        self._reconnect_btn.clicked.connect(self._do_reconnect)
        _ar_layout.addWidget(self._reconnect_btn)

        left_layout.addWidget(_action_row)

        # Cmd+Shift+R — refresh schema
        QShortcut(QKeySequence("Ctrl+Shift+R"), self).activated.connect(self.load_schema)

        self.table_search = QLineEdit()
        self.table_search.setPlaceholderText("Search tables...")
        self.table_search.textChanged.connect(self.filter_tables)
        left_layout.addWidget(self.table_search)

        # ── Schema / History toggle ──────────────────────────────────
        sidebar_toggle = QWidget()
        toggle_layout = QHBoxLayout(sidebar_toggle)
        toggle_layout.setContentsMargins(0, 0, 0, 0)
        toggle_layout.setSpacing(0)

        self._schema_btn = QPushButton("Schema")
        self._schema_btn.setCheckable(True)
        self._schema_btn.setChecked(True)
        self._schema_btn.setFlat(True)
        self._schema_btn.clicked.connect(lambda: self._switch_sidebar(0))

        self._history_btn = QPushButton("History")
        self._history_btn.setCheckable(True)
        self._history_btn.setChecked(False)
        self._history_btn.setFlat(True)
        self._history_btn.clicked.connect(lambda: self._switch_sidebar(1))

        _toggle_style = """
            QPushButton {
                background: transparent;
                color: #8e8e93;
                border: none;
                border-bottom: 2px solid transparent;
                padding: 4px 12px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:checked {
                color: #e5e5ea;
                border-bottom: 2px solid #0A84FF;
            }
            QPushButton:hover:!checked { color: #c7c7cc; }
        """
        self._schema_btn.setStyleSheet(_toggle_style)
        self._history_btn.setStyleSheet(_toggle_style)
        toggle_layout.addWidget(self._schema_btn)
        toggle_layout.addWidget(self._history_btn)
        toggle_layout.addStretch()
        left_layout.addWidget(sidebar_toggle)

        # ── Stacked: page 0 = schema tree, page 1 = history list ────────
        self._sidebar_stack = QStackedWidget()

        self.schema_tree = QTreeWidget()
        self.schema_tree.setHeaderHidden(True)
        self.schema_tree.setIndentation(15)
        self.schema_tree.setAnimated(True)
        self.schema_tree.itemClicked.connect(self._on_item_clicked)
        self.schema_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.schema_tree.customContextMenuRequested.connect(self._show_context_menu)
        self._sidebar_stack.addWidget(self.schema_tree)   # index 0

        # History panel
        history_panel = QWidget()
        hp_layout = QVBoxLayout(history_panel)
        hp_layout.setContentsMargins(0, 0, 0, 0)
        hp_layout.setSpacing(2)

        self._history_search = QLineEdit()
        self._history_search.setPlaceholderText("Search history...")
        self._history_search.textChanged.connect(self._filter_history_list)
        hp_layout.addWidget(self._history_search)

        self._history_list = QListWidget()
        self._history_list.setWordWrap(False)
        self._history_list.itemDoubleClicked.connect(self._use_history_item)
        hp_layout.addWidget(self._history_list)

        clear_hist_btn = QPushButton("Clear History")
        clear_hist_btn.setFlat(True)
        clear_hist_btn.clicked.connect(self._clear_history)
        hp_layout.addWidget(clear_hist_btn)

        self._sidebar_stack.addWidget(history_panel)         # index 1

        left_layout.addWidget(self._sidebar_stack)

        splitter.addWidget(left)

        # ── Right panel (content tabs) ────────────────────────────────────────
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(False)  # custom per-tab × buttons used instead
        self.tabs.setMovable(True)
        self.tabs.tabBarDoubleClicked.connect(self._rename_tab)

        # ── "+" button pinned right-next-to the last tab ──────────────
        new_tab_btn = QPushButton("＋")
        new_tab_btn.setToolTip("New query tab (Ctrl+T)")
        new_tab_btn.setFixedSize(28, 26)
        new_tab_btn.clicked.connect(self.add_new_tab)
        new_tab_btn.setStyleSheet("""
            QPushButton {
                background: #2c2c2e;
                color: #e5e5ea;
                border: 1px solid #48484a;
                border-radius: 5px;
                font-size: 16px;
                font-weight: 400;
                padding: 0;
                margin: 2px 4px;
            }
            QPushButton:hover  { background: #3a3a3c; color: #ffffff; border-color: #636366; }
            QPushButton:pressed { background: #1c1c1e; }
        """)
        self.tabs.setCornerWidget(new_tab_btn, Qt.TopLeftCorner)

        right_layout.addWidget(self.tabs)

        splitter.addWidget(right)
        splitter.setSizes([200, 1400])
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        root.addWidget(splitter)

    def _guard_write(self, sql: str, extra_reason: str = None) -> bool:
        """Classify *sql* (one statement or a whole script) and show
        whatever dialog is needed before a write reaches the database.
        Returns True if the caller should proceed.

        Read-only connections block outright, regardless of environment.
        Staging/Production connections additionally require confirmation
        for statements the classifier flags as dangerous (destructive DDL
        — DROP/TRUNCATE — requires typing the connection name). Local/
        Development/Unclassified and non-flagged statements return True
        with no dialog, leaving today's existing per-entry-point confirms
        (if any) unaffected.

        services/db_service.py's own read-only guard is the real backstop
        — this method is the UX layer (explain + confirm + cancel) on top
        of it, per ai/load-context.md's "explain why, offer a clear cancel
        path" principle.
        """
        statements = query_classifier.split_statements(sql)
        if not statements:
            return True

        classifications = [query_classifier.classify(s) for s in statements]
        connection_name = self.config.get("name", "Connection")
        env = environment.normalize(self.config.get("environment"))

        if self.config.get("read_only") and any(c.is_write for c in classifications):
            blocked = [c.statement for c in classifications if c.is_write]
            query_guard_dialog.show_read_only_blocked(self, connection_name, env, blocked)
            return False

        if env in (environment.STAGING, environment.PRODUCTION):
            dangerous = [c for c in classifications if query_classifier.is_dangerous(c)]
            reasons = [r for c in dangerous for r in c.reasons]
            if extra_reason:
                reasons.append(extra_reason)
            if dangerous or extra_reason:
                shown_statements = [c.statement for c in dangerous] or statements
                require_typed_name = any(c.is_destructive_ddl for c in dangerous)
                return query_guard_dialog.show_dangerous_confirmation(
                    self, connection_name, env, shown_statements, reasons,
                    require_typed_name=require_typed_name,
                )

        return True

    # ─── Schema loading ───────────────────────────────────────────────────────

    def load_schema(self):
        # Don't attempt schema load if not connected
        if not self.db_service or not self.db_service.connection:
            self.schema_tree.clear()
            self.all_tables.clear()
            self.all_table_items.clear()
            self.all_schema_items.clear()
            return

        # Clear immediately so the user sees empty tree straight away
        self.schema_tree.clear()
        self.all_tables.clear()
        self.all_table_items.clear()
        self.all_schema_items.clear()

        # Show a loading indicator
        loading_item = QTreeWidgetItem(["Loading…"])
        self.schema_tree.addTopLevelItem(loading_item)

        # Fetch the schema on a daemon thread using a *dedicated* connection
        # (see services/schema_snapshot.py) — never touches self.db_service,
        # which the main thread may be using concurrently (query tabs, table
        # views). Results come back via _schema_done.
        conf = dict(self.config)
        sig_done  = self._schema_done
        sig_error = self._schema_error

        def _worker():
            try:
                sig_done.emit(fetch_schema_snapshot(conf))
            except Exception as ex:
                # Suppress silent "not connected" errors (e.g. (0, '') on startup)
                msg = str(ex)
                if msg in ("(0, '')", "0", "") or "not connected" in msg.lower():
                    return  # connection not ready yet — no error shown
                sig_error.emit(msg)

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

    def _on_schema_loaded(self, result: dict):
        """Main-thread: populate the schema tree from background result."""
        # Update DB list + pill
        dbs = result.get("dbs", [])
        self._available_dbs = dbs
        if "switched_db" in result:
            new_db = result["switched_db"]
            self.config["database"] = new_db
            # The snapshot was fetched over its own dedicated connection, so
            # the live connection's selected database needs updating here too.
            if self.db_service.db_type == "mysql" and self.db_service.connection:
                try:
                    self.db_service.connection.select_db(new_db)
                except Exception as ex:
                    logger.warning(f"Failed to switch live connection to database {new_db}: {ex}")
        self._update_pill_label()

        if result.get("server_version"):
            self._server_version = result["server_version"]
            self.label_changed.emit(self, self.label)

        tables    = result.get("tables", [])
        columns   = result.get("columns", {})
        views     = result.get("views", [])
        functions = result.get("functions", [])

        self._column_cache = columns

        # Rebuild tree
        self.schema_tree.clear()
        self.all_tables.clear()
        self.all_table_items.clear()
        self.all_schema_items.clear()
        self.table_index.clear()

        tables_cat    = QTreeWidgetItem(["Tables"])
        views_cat     = QTreeWidgetItem(["Views"])
        functions_cat = QTreeWidgetItem(["Functions/Procedures"])

        for table_name in tables:
            self.all_tables.append(table_name)
            self.all_schema_items.append(("table", table_name))
            if len(table_name) >= 3:
                self.table_index.setdefault(
                    table_name[:3].lower(), []).append(table_name)
            item = QTreeWidgetItem([table_name])
            self.all_table_items[table_name] = item
            tables_cat.addChild(item)

        for v in views:
            self.all_schema_items.append(("view", v))
            views_cat.addChild(QTreeWidgetItem([v]))

        for fn in functions:
            self.all_schema_items.append(("function", fn))
            functions_cat.addChild(QTreeWidgetItem([fn]))

        self.schema_tree.addTopLevelItem(tables_cat)
        if views:
            self.schema_tree.addTopLevelItem(views_cat)
        if functions:
            self.schema_tree.addTopLevelItem(functions_cat)
        tables_cat.setExpanded(True)

        # Update autocomplete in existing SQL tabs (TableViewWidget is a data
        # grid, not an editor — it has no autocomplete/schema to push).
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            if isinstance(tab, SqlTab):
                tab.set_schema(tables, columns)

    def _on_schema_error(self, msg: str):
        self.schema_tree.clear()
        err = QTreeWidgetItem([f"⚠ {msg}"])
        self.schema_tree.addTopLevelItem(err)
        logger.error(f"Schema load error: {msg}")

    def _load_databases(self):
        """Sync fallback — only used from _do_reconnect path."""
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
        # Update pill immediately so the UI feels instant
        self.config["database"] = new_db
        self._update_pill_label()

        # Show spinner in schema tree
        self.schema_tree.clear()
        self.schema_tree.addTopLevelItem(QTreeWidgetItem(["Switching database…"]))
        from PySide6.QtWidgets import QApplication as _QApp
        _QApp.processEvents()

        # Reconnect the shared connection synchronously (fast) so nothing else
        # can be using it mid-reconnect. The (potentially slower) schema
        # listing then runs on a background thread over its OWN dedicated
        # connection (services/schema_snapshot.py) — never touching
        # self.db_service concurrently.
        try:
            self.db_service.disconnect()
            self.db_service.connect(dict(self.config))
        except Exception as ex:
            self._on_schema_error(str(ex))
            return

        conf = dict(self.config)
        sig_done  = self._schema_done
        sig_error = self._schema_error

        def _load():
            try:
                sig_done.emit(fetch_schema_snapshot(conf))
            except Exception as ex:
                msg = str(ex)
                if msg in ("(0, '')", "0", "") or "not connected" in msg.lower():
                    return
                sig_error.emit(msg)

        t = threading.Thread(target=_load, daemon=True)
        t.start()

    # ─── Database management (create/refresh/drop) ────────────────────────────

    _VALID_DB_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

    def _check_db_management_supported(self) -> bool:
        if self.db_service.db_type not in ("mysql", "postgresql"):
            QMessageBox.information(
                self, "Not Supported",
                "Creating, dropping, and listing databases is only supported "
                "for MySQL and PostgreSQL connections.")
            return False
        return True

    def create_database(self):
        if not self._check_db_management_supported():
            return
        name, ok = QInputDialog.getText(self, "Create Database", "Database name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if not self._VALID_DB_NAME.match(name):
            QMessageBox.warning(
                self, "Invalid Name",
                "Database name must start with a letter or underscore and "
                "contain only letters, digits, and underscores.")
            return

        sql = f"CREATE DATABASE {name}"
        if not self._guard_write(sql):
            return
        reply = QMessageBox.question(
            self, "Create Database",
            f"Execute the following SQL?\n\n{sql}",
            QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        try:
            self.db_service.execute_update(sql)
        except Exception as ex:
            QMessageBox.critical(self, "Create Database Failed", str(ex))
            return
        QMessageBox.information(self, "Success", f"Database '{name}' created.")
        self._load_databases()

    def refresh_databases(self):
        if not self._check_db_management_supported():
            return
        self._load_databases()

    def drop_database(self):
        if not self._check_db_management_supported():
            return
        if not self._available_dbs:
            self._load_databases()
        if not self._available_dbs:
            QMessageBox.information(self, "Drop Database", "No databases found.")
            return

        name, ok = QInputDialog.getItem(
            self, "Drop Database", "Database to drop:",
            self._available_dbs, editable=False)
        if not ok or not name:
            return
        if not self._VALID_DB_NAME.match(name):
            QMessageBox.warning(self, "Invalid Name", "Unrecognized database name.")
            return
        if name == self.config.get("database"):
            QMessageBox.warning(
                self, "Drop Database",
                f"'{name}' is the database this connection is currently using. "
                "Switch to a different database first, then drop it.")
            return

        sql = f"DROP DATABASE {name}"
        if not self._guard_write(sql):
            return
        reply = QMessageBox.question(
            self, "Drop Database",
            f"Execute the following SQL?\n\n{sql}",
            QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        try:
            self.db_service.execute_update(sql)
        except Exception as ex:
            QMessageBox.critical(self, "Drop Database Failed", str(ex))
            return
        QMessageBox.information(self, "Success", f"Database '{name}' dropped.")
        self._load_databases()

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
        structure_action = menu.addAction("🔍 View Structure")
        edit_action = menu.addAction("✏️ Edit Structure")
        menu.addSeparator()
        import_action = menu.addAction("📥 Import CSV into Table…")
        menu.addSeparator()
        refresh_action = menu.addAction("🔄 Refresh Schema")

        action = menu.exec_(self.schema_tree.mapToGlobal(position))
        if action == open_action:
            self.open_table_view(item.text(0))
        elif action == structure_action:
            self._show_table_structure(item.text(0))
        elif action == edit_action:
            self.show_alter_table_editor(item.text(0))
        elif action == import_action:
            self._import_csv_into_table(item.text(0))
        elif action == refresh_action:
            self.load_schema()

    # ─── Table/query tabs ─────────────────────────────────────────────────────

    def open_table_view(self, table_name: str):
        """Open a table view; re-focus if already open."""
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, TableViewWidget) and w.table_name == table_name:
                self.tabs.setCurrentIndex(i)
                return

        tv = TableViewWidget(self.db_service, table_name)
        tv.execute_query_signal.connect(self._run_query_in_tab)
        tab_index = self.tabs.addTab(tv, table_name)
        self._attach_close_btn(tab_index)
        self.tabs.setCurrentIndex(tab_index)

        # ── FK metadata: load async-style (non-blocking) ──────────────────────
        try:
            fk_list = self.db_service.get_foreign_keys(table_name)
            if hasattr(tv, 'data_table'):
                tv.data_table.set_fk_map(fk_list)
        except Exception:
            pass

        # ── Wire filter-chip signal ────────────────────────────────────────────
        def _on_filter_chip(col_name: str, value: str, _tv=tv):
            if hasattr(_tv, 'filter_by_column_value'):
                _tv.filter_by_column_value(col_name, value)

        # ── Wire FK navigation signal ──────────────────────────────────────────
        def _on_fk_nav(ref_table: str, ref_col: str, value: str):
            self.open_table_view(ref_table)
            # After tab opens, apply filter
            from PySide6.QtCore import QTimer
            def _apply():
                for i in range(self.tabs.count()):
                    w = self.tabs.widget(i)
                    if isinstance(w, TableViewWidget) and w.table_name == ref_table:
                        if hasattr(w, 'filter_by_column_value'):
                            w.filter_by_column_value(ref_col, value)
                        break
            QTimer.singleShot(300, _apply)

        # ── Wire Show Structure signal ─────────────────────────────────────────
        def _on_show_structure(tbl_name: str):
            self._show_table_structure(tbl_name)

        if hasattr(tv, 'data_table'):
            tv.data_table.filter_by_value.connect(_on_filter_chip)
            tv.data_table.navigate_fk.connect(_on_fk_nav)
            tv.data_table.show_structure.connect(_on_show_structure)

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
        tab.verify_btn.clicked.connect(lambda: self._open_verify_dialog(tab))
        # Wire inline-edit commit: execute SQL with our db_service
        tab.commit_sql.connect(lambda sqls, t=tab: self._execute_commit_sql(sqls, t))
        # Push current schema so autocomplete works immediately
        tab.set_schema(self.all_tables, self._column_cache)
        count = self.tabs.count() + 1
        idx = self.tabs.addTab(tab, f"Tab {count}")
        self._attach_close_btn(idx)
        self.tabs.setCurrentWidget(tab)
        tab.update_theme(self.current_theme == "dark")
        # Focus the editor after the tab is fully shown
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, tab.editor.setFocus)

    def _execute_commit_sql(self, sql_list: list, tab):
        """Execute inline-edit SQL statements against the live connection."""
        if not self._guard_write("\n".join(sql_list)):
            return
        errors = []
        success = 0
        for sql in sql_list:
            try:
                self.db_service.execute_update(sql)
                success += 1
            except Exception as ex:
                errors.append(str(ex))
        if errors:
            QMessageBox.warning(
                self, "Save Errors",
                f"Saved {success}/{len(sql_list)} changes.\n\n" + "\n".join(errors[:3])
            )
        else:
            tab.revert_changes()   # clear dirty state — data was saved
            # Re-run the last query so results reflect the saved data
            if getattr(tab, '_last_query', ''):
                self._run_query_in_tab(tab)

    # ── Bridge signal handlers (always run on main thread) ────────────────

    @staticmethod
    def _restore_run_btn(tab):
        """Restore the Run button to its default ready state."""
        tab.run_btn.setText('▶  Run')
        tab.run_btn.setEnabled(True)
        tab.run_btn.setStyleSheet("""
            QPushButton {
                background: #0A84FF;
                color: #fff;
                border: none;
                border-radius: 5px;
                padding: 0 18px;
                font-weight: 600;
                font-size: 13px;
            }
            QPushButton:hover  { background: #228BFF; }
            QPushButton:pressed { background: #0066CC; }
        """)

    def _wire_result_fk(self, tab, table_name: str):
        """Load FK map for *table_name* into the result grid and ensure the
        navigate_fk signal is wired (idempotent — only connects once)."""
        if not table_name or not hasattr(tab, 'result_table'):
            return
        try:
            fk_list = self.db_service.get_foreign_keys(table_name)
            tab.result_table.set_fk_map(fk_list)
        except Exception:
            fk_list = []

        # Wire navigate_fk only once per tab (guard with a flag)
        if getattr(tab, '_fk_nav_wired', False):
            return
        tab._fk_nav_wired = True

        def _on_result_fk_nav(ref_table: str, ref_col: str, value: str, _tab=tab):
            self.open_table_view(ref_table)
            from PySide6.QtCore import QTimer
            def _apply():
                for i in range(self.tabs.count()):
                    w = self.tabs.widget(i)
                    if isinstance(w, TableViewWidget) and w.table_name == ref_table:
                        if hasattr(w, 'filter_by_column_value'):
                            w.filter_by_column_value(ref_col, value)
                        break
            QTimer.singleShot(300, _apply)

        tab.result_table.navigate_fk.connect(_on_result_fk_nav)

    def _on_query_done(self, tab, df, elapsed):
        """Receives worker `done` signal via bridge — guaranteed main thread."""
        tab._query_running = False
        self._restore_run_btn(tab)
        tab.cancel_btn.setEnabled(False)
        if hasattr(tab, '_query_thread'):
            tab._query_thread.quit()
        query = getattr(tab, '_last_query', '')
        table_name = self._extract_table_name(query)
        tab.load_dataframe(df, table_name)
        tab.update_status(len(df), elapsed)
        # Load FK map so right-click "Go to …" works in the result grid
        self._wire_result_fk(tab, table_name)
        self.query_history.add_query(query, self.config["name"], len(df), elapsed)
        if self._sidebar_stack.currentIndex() == 1:
            self._reload_history_list(self._history_search.text())
        self.health_changed.emit('idle')
        if self.tabs.currentWidget() is not tab:
            tab_name = self.tabs.tabText(self.tabs.indexOf(tab))
            self._show_query_toast(tab_name, len(df), elapsed)

    def _on_query_multi_done(self, tab, results: list, elapsed: float):
        """Multi-statement result handler — shows each SELECT in its own sub-tab."""
        tab._query_running = False
        self._restore_run_btn(tab)
        tab.cancel_btn.setEnabled(False)
        if hasattr(tab, '_query_thread'):
            tab._query_thread.quit()
        query = getattr(tab, '_last_query', '')
        select_results = [(lbl, obj) for lbl, obj in results
                          if obj is not None and not isinstance(obj, Exception)]
        error_results  = [(lbl, obj) for lbl, obj in results if isinstance(obj, Exception)]

        total_rows = sum(len(df) for _, df in select_results)
        self.query_history.add_query(query, self.config["name"], total_rows, elapsed)
        if self._sidebar_stack.currentIndex() == 1:
            self._reload_history_list(self._history_search.text())

        if len(select_results) == 1:
            # single result — display inline as normal
            lbl, df = select_results[0]
            tab.load_dataframe(df, self._extract_table_name(query))
            tab.update_status(len(df), elapsed)
        elif len(select_results) > 1:
            # multiple results — hand off to tab's multi-result view
            tab.load_multi_results(select_results, elapsed)
        elif error_results:
            lbl, ex = error_results[0]
            tab.show_error(str(ex), query=lbl, elapsed=elapsed)
        else:
            tab.update_status(0, elapsed)

        if error_results and len(select_results) >= 0:
            msgs = "\n".join(f"[{lbl}] {ex}" for lbl, ex in error_results)
            tab.show_error(msgs, elapsed=elapsed)

        self.health_changed.emit('idle')


    def _on_query_errored(self, tab, message, elapsed):
        """Receives worker `errored` signal via bridge — guaranteed main thread."""
        tab._query_running = False
        self._restore_run_btn(tab)
        tab.cancel_btn.setEnabled(False)
        if hasattr(tab, '_query_thread'):
            tab._query_thread.quit()
        query = getattr(tab, '_last_query', '')
        tab.show_error(message, query=query, elapsed=elapsed)
        # Connection errors flip to disconnected; others stay idle
        if any(k in message.lower() for k in ('lost', 'disconnect', 'gone away', 'server has gone')):
            self.health_changed.emit('disconnected')
        else:
            self.health_changed.emit('idle')

    def _on_query_cancelled(self, tab):
        """Receives worker `cancelled` signal via bridge — guaranteed main thread."""
        tab._query_running = False
        self._restore_run_btn(tab)
        tab.cancel_btn.setEnabled(False)
        if hasattr(tab, '_query_thread'):
            tab._query_thread.quit()
        tab.show_cancelled()

    # ── Parameterised query helpers ────────────────────────────────────────────

    @staticmethod
    def _extract_params(query: str) -> list[str]:
        """Return unique {{param}} names found in *query*, in order of appearance."""
        seen: set[str] = set()
        out: list[str] = []
        for m in re.finditer(r'\{\{(\w+)\}\}', query):
            name = m.group(1)
            if name not in seen:
                seen.add(name)
                out.append(name)
        return out

    def _prompt_params(self, query: str) -> str | None:
        """If *query* contains {{params}}, show an inline dialog and substitute.
        Returns the substituted query, or None if the user cancelled."""
        params = self._extract_params(query)
        if not params:
            return query

        from PySide6.QtWidgets import (
            QDialog, QFormLayout, QDialogButtonBox, QLineEdit, QLabel, QVBoxLayout
        )
        dlg = QDialog(self)
        dlg.setWindowTitle("Query Parameters")
        dlg.setMinimumWidth(340)
        outer = QVBoxLayout(dlg)
        outer.setSpacing(8)
        outer.setContentsMargins(16, 12, 16, 12)

        lbl = QLabel("Fill in the <b>{{parameter}}</b> values:")
        lbl.setStyleSheet("font-size: 13px; margin-bottom: 4px;")
        outer.addWidget(lbl)

        form = QFormLayout()
        form.setSpacing(8)
        form.setHorizontalSpacing(12)
        inputs: dict[str, QLineEdit] = {}
        for name in params:
            le = QLineEdit()
            le.setPlaceholderText(f"Value for {name}")
            form.addRow(f"{name}:", le)
            inputs[name] = le
        outer.addLayout(form)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        outer.addWidget(btns)

        # Focus first field
        if inputs:
            list(inputs.values())[0].setFocus()

        if dlg.exec() != QDialog.Accepted:
            return None

        for name, le in inputs.items():
            query = query.replace(f"{{{{{name}}}}}", le.text())
        return query

    def _run_query_in_tab(self, tab):
        """Execute the SQL in `tab` on a background thread; Cancel actually stops it."""
        query = tab.get_query().strip()
        if not query:
            return

        # Format on run if user has the toggle active
        if getattr(tab, '_format_on_run', False):
            import sqlparse
            try:
                query = sqlparse.format(query, reindent=True, keyword_case="upper")
                tab.editor.setPlainText(query)
            except Exception:
                pass  # query too large for sqlparse — run as-is

        # ── Parameterised queries: prompt for {{var}} values ──────────────────
        resolved = self._prompt_params(query)
        if resolved is None:
            return          # user cancelled
        query = resolved

        if not self._guard_write(query):
            return

        # Don't allow concurrent queries on the same tab
        if getattr(tab, '_query_running', False):
            return

        tab._query_running = True
        tab._last_query    = query
        tab._cancel_flag   = threading.Event()
        tab._query_start_time = time.time()
        tab.run_btn.setEnabled(False)
        tab.run_btn.setText('⏳  0.0s')
        tab.run_btn.setStyleSheet("""
            QPushButton {
                background: #2c4a2e;
                color: #30d158;
                border: 1px solid #30d158;
                border-radius: 5px;
                padding: 0 18px;
                font-weight: 600;
                font-size: 13px;
            }
        """)
        tab.cancel_btn.setEnabled(True)
        self.health_changed.emit('running')

        # Flush the UI immediately so the button turns green before any
        # blocking work (DB connect, sqlparse format) happens on this thread.
        from PySide6.QtWidgets import QApplication as _QApp
        _QApp.processEvents()

        # ── Live elapsed-time ticker (updates run button every 100ms) ─────────
        from PySide6.QtCore import QTimer as _QTimer
        elapsed_timer = _QTimer(self)
        elapsed_timer.setInterval(100)
        def _tick():
            if getattr(tab, '_query_running', False):
                secs = time.time() - tab._query_start_time
                tab.run_btn.setText(f'⏳  {secs:.1f}s')
            else:
                elapsed_timer.stop()
                elapsed_timer.deleteLater()
        elapsed_timer.timeout.connect(_tick)
        elapsed_timer.start()
        tab._elapsed_timer = elapsed_timer

        # ── Dedicated connection for this query (prevents shared-connection races) ──
        query_db = DbService()
        try:
            query_db.connect(self.config)
        except Exception as ex:
            tab._query_running = False
            self._restore_run_btn(tab)
            tab.cancel_btn.setEnabled(False)
            tab.show_error(str(ex))
            return

        worker = _QueryWorker(query_db, query, tab._cancel_flag)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)

        # Keep refs alive until the thread finishes
        tab._query_thread = thread
        tab._query_worker = worker

        # Worker signals → bridge signals on ConnectionPanel (a QWidget on the
        # main thread).  Qt routes QueuedConnection to the *receiver* object's
        # thread, so these slots are guaranteed to run on the main thread.
        worker.done.connect(
            lambda df, elapsed: self._q_done.emit(tab, df, elapsed))
        worker.multi_done.connect(
            lambda results, elapsed: self._q_multi_done.emit(tab, results, elapsed))
        worker.errored.connect(
            lambda msg, elapsed: self._q_errored.emit(tab, msg, elapsed))
        worker.cancelled.connect(
            lambda: self._q_cancelled.emit(tab))

        # Safely (re)connect Cancel button — Cancel kills on the dedicated connection
        cancel_slot = getattr(tab, '_cancel_slot', None)
        if cancel_slot is not None:
            try:
                tab.cancel_btn.clicked.disconnect(cancel_slot)
            except Exception:
                pass

        def _cancel():
            tab._cancel_flag.set()
            try:
                query_db.kill_current_query()
            except Exception:
                pass

        tab._cancel_slot = _cancel
        tab.cancel_btn.clicked.connect(_cancel)

        # Clean up C++ objects once the thread is fully done
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        thread.start()

    def _open_verify_dialog(self, tab):
        """Open the Query Verifier dialog pre-populated with the current tab's query."""
        from ui.query_verifier_dialog import QueryVerifierDialog
        query = tab.get_query().strip()
        dlg = QueryVerifierDialog(self.db_service, initial_query=query, parent=self)
        dlg.show()

    @staticmethod
    def _extract_table_name(query: str):
        m = re.search(r"from\s+`?(\w+)`?", query.lower())
        return m.group(1) if m else None

    def _attach_close_btn(self, index: int):
        """Place a visible × QPushButton on the tab at the given index."""
        btn = QPushButton("×")
        btn.setFixedSize(18, 18)
        btn.setStyleSheet(
            "QPushButton { background: transparent; color: #8e8e93;"
            " border: none; font-size: 16px; font-weight: bold; padding: 0; margin: 0; }"
            "QPushButton:hover { color: #ff453a; }"
        )
        def _close(*, _b=btn):
            for i in range(self.tabs.count()):
                if self.tabs.tabBar().tabButton(i, QTabBar.RightSide) is _b:
                    self._close_tab(i)
                    break
        btn.clicked.connect(_close)
        self.tabs.tabBar().setTabButton(index, QTabBar.RightSide, btn)

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
                if not self._guard_write(sql):
                    return
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

    def _show_table_structure(self, table_name: str):
        """Show a read-only structure popup for *table_name*: columns, indexes, FKs."""
        from PySide6.QtWidgets import (
            QDialog, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
            QTabWidget, QDialogButtonBox, QHeaderView
        )
        try:
            cols = self.db_service.get_columns(table_name)
            fks  = self.db_service.get_foreign_keys(table_name)
            try:
                idxs = self.db_service.get_indexes(table_name)
            except Exception:
                idxs = []
        except Exception as ex:
            QMessageBox.warning(self, "Structure", str(ex))
            return

        dlg = QDialog(self)
        dlg.setWindowTitle(f"📊 Structure — {table_name}")
        dlg.resize(680, 460)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(12, 10, 12, 10)

        tabs = QTabWidget()
        tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #3a3a3a;
                border-radius: 4px;
                background: #1e1e1e;
            }
            QTabBar::tab {
                background: #2a2a2a;
                color: #888888;
                padding: 7px 18px;
                border: 1px solid #3a3a3a;
                border-bottom: none;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                font-size: 12px;
                min-width: 100px;
            }
            QTabBar::tab:selected {
                background: #1e1e1e;
                color: #ffffff;
                border-bottom: 2px solid #0078d4;
                font-weight: 600;
            }
            QTabBar::tab:hover:!selected {
                background: #333333;
                color: #cccccc;
            }
        """)

        # ── Columns tab ────────────────────────────────────────────────────────────
        col_tbl = QTableWidget(len(cols), 5)
        col_tbl.setHorizontalHeaderLabels(["Column", "Type", "Null", "Key", "Default"])
        col_tbl.verticalHeader().setVisible(False)
        col_tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        col_tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        col_tbl.horizontalHeader().setStretchLastSection(True)
        for r, c in enumerate(cols):
            if isinstance(c, dict):
                col_tbl.setItem(r, 0, QTableWidgetItem(str(c.get("Field", ""))))
                col_tbl.setItem(r, 1, QTableWidgetItem(str(c.get("Type",  ""))))
                col_tbl.setItem(r, 2, QTableWidgetItem(str(c.get("Null",  ""))))
                col_tbl.setItem(r, 3, QTableWidgetItem(str(c.get("Key",   ""))))
                col_tbl.setItem(r, 4, QTableWidgetItem(str(c.get("Default", ""))))
            else:
                for ci, val in enumerate(list(c)[:5]):
                    col_tbl.setItem(r, ci, QTableWidgetItem(str(val)))
        tabs.addTab(col_tbl, f"Columns ({len(cols)})")

        # ── Indexes tab ───────────────────────────────────────────────────────────────
        idx_tbl = QTableWidget(len(idxs), 4)
        idx_tbl.setHorizontalHeaderLabels(["Name", "Columns", "Unique", "Type"])
        idx_tbl.verticalHeader().setVisible(False)
        idx_tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        idx_tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        idx_tbl.horizontalHeader().setStretchLastSection(True)
        for r, idx in enumerate(idxs):
            idx_tbl.setItem(r, 0, QTableWidgetItem(str(idx.get("name", ""))))
            idx_tbl.setItem(r, 1, QTableWidgetItem(str(idx.get("columns", ""))))
            idx_tbl.setItem(r, 2, QTableWidgetItem("✔" if idx.get("unique") else ""))
            idx_tbl.setItem(r, 3, QTableWidgetItem(str(idx.get("type", ""))))
        tabs.addTab(idx_tbl, f"Indexes ({len(idxs)})")

        # ── Foreign Keys tab ───────────────────────────────────────────────────────────
        fk_tbl = QTableWidget(len(fks), 3)
        fk_tbl.setHorizontalHeaderLabels(["Column", "References Table", "References Column"])
        fk_tbl.verticalHeader().setVisible(False)
        fk_tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        fk_tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        fk_tbl.horizontalHeader().setStretchLastSection(True)
        for r, fk in enumerate(fks):
            fk_tbl.setItem(r, 0, QTableWidgetItem(str(fk.get("column", ""))))
            fk_tbl.setItem(r, 1, QTableWidgetItem(str(fk.get("ref_table", ""))))
            fk_tbl.setItem(r, 2, QTableWidgetItem(str(fk.get("ref_column", ""))))
        tabs.addTab(fk_tbl, f"Foreign Keys ({len(fks)})")

        lay.addWidget(tabs)
        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(dlg.accept)
        lay.addWidget(btns)
        dlg.exec()



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
                if not self._guard_write(sql):
                    return
                reply = QMessageBox.question(
                    self, "Alter Table",
                    f"Execute the following SQL?\n\n{sql}",
                    QMessageBox.Yes | QMessageBox.No)
                if reply == QMessageBox.Yes:
                    for stmt in query_classifier.split_statements(sql):
                        self.db_service.execute_update(stmt)
                    QMessageBox.information(
                        self, "Success", f"Table {table_name} altered successfully")
                    self.load_schema()
            except Exception as ex:
                QMessageBox.critical(self, "Error", str(ex))

    # ─── CSV Import ──────────────────────────────────────────────────────────

    def _import_csv_into_table(self, table_name: str):
        """Read a CSV file and INSERT all rows into *table_name*."""
        from PySide6.QtWidgets import QFileDialog, QProgressDialog
        import pandas as pd

        file_path, _ = QFileDialog.getOpenFileName(
            self, f"Import CSV into {table_name}", "",
            "CSV Files (*.csv);;Tab-separated (*.tsv *.txt);;All Files (*)"
        )
        if not file_path:
            return

        try:
            sep = "\t" if file_path.endswith((".tsv", ".txt")) else ","
            df = pd.read_csv(file_path, sep=sep, keep_default_na=False)
        except Exception as ex:
            QMessageBox.critical(self, "Import Error", f"Could not read file:\n{ex}")
            return

        if df.empty:
            QMessageBox.information(self, "Import", "The file contains no data rows.")
            return

        # Confirm
        reply = QMessageBox.question(
            self, "Confirm Import",
            f"Insert <b>{len(df):,}</b> rows into <b>{table_name}</b>?<br>"
            f"Columns: {', '.join(df.columns.tolist()[:8])}"
            + (" …" if len(df.columns) > 8 else ""),
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        # Build INSERT statements and execute in batches
        db_type = self.db_service.db_type
        ph = "%s"  # MySQL / PostgreSQL
        if db_type == "sqlite":
            ph = "?"

        cols_sql = ", ".join(
            f"`{c}`" if db_type == "mysql" else f'"{c}"'
            for c in df.columns
        )
        placeholders = ", ".join([ph] * len(df.columns))
        insert_sql = (
            f"INSERT INTO `{table_name}` ({cols_sql}) VALUES ({placeholders})"
            if db_type == "mysql"
            else f'INSERT INTO "{table_name}" ({cols_sql}) VALUES ({placeholders})'
        )

        mass_write_reason = (
            f"Importing {len(df):,} rows — treated as a mass write"
            if len(df) >= MASS_WRITE_ROW_THRESHOLD else None
        )
        if not self._guard_write(insert_sql, extra_reason=mass_write_reason):
            return

        progress = QProgressDialog(
            f"Importing {len(df):,} rows…", "Cancel", 0, len(df), self)
        progress.setWindowTitle("Import CSV")
        progress.setMinimumDuration(0)

        BATCH = 500
        rows = [
            tuple(None if v == "" else v for v in row)
            for row in df.itertuples(index=False, name=None)
        ]
        try:
            inserted, errors = self.db_service.execute_batch(
                insert_sql, rows,
                batch_size=BATCH,
                on_batch=lambda start, n: progress.setValue(start + n),
                should_cancel=progress.wasCanceled,
            )
        except Exception as ex:
            QMessageBox.critical(self, "Import Error", str(ex))
            return
        finally:
            progress.close()

        msg = f"Imported <b>{inserted:,}</b> rows into <b>{table_name}</b>."
        if errors:
            msg += f"<br>{errors} batch(es) failed — check logs."
        QMessageBox.information(self, "Import Complete", msg)

        # Refresh the open table view if it exists
        panel = self
        for i in range(panel.tabs.count()):
            w = panel.tabs.widget(i)
            from ui.table_view_widget import TableViewWidget
            if isinstance(w, TableViewWidget) and w.table_name == table_name:
                w.current_page = 1
                w.load_table_data()
                break

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

    # ─── Sidebar schema/history toggle ─────────────────────────────────────────

    def _switch_sidebar(self, index: int):
        self._sidebar_stack.setCurrentIndex(index)
        self._schema_btn.setChecked(index == 0)
        self._history_btn.setChecked(index == 1)
        self.table_search.setVisible(index == 0)
        if index == 1:
            self._reload_history_list()

    def _reload_history_list(self, filter_text: str = ''):
        self._history_list.clear()
        entries = self.query_history.get_recent_queries(limit=100)
        ft = filter_text.lower()
        for entry in entries:
            q = entry.get('query', '').strip()
            ts = entry.get('timestamp', '')
            rows = entry.get('rows', '')
            if ft and ft not in q.lower():
                continue
            display = q.replace('\n', ' ')[:80]
            item = QListWidgetItem(display)
            item.setToolTip(f'{ts}  |  {rows} rows\n\n{q}')
            item.setData(Qt.UserRole, q)
            self._history_list.addItem(item)

    def _filter_history_list(self, text: str):
        self._reload_history_list(filter_text=text)

    def _use_history_item(self, item: QListWidgetItem):
        query = item.data(Qt.UserRole)
        if not query:
            return
        current = self.tabs.currentWidget()
        if isinstance(current, SqlTab):
            current.set_query(query)
        else:
            self.add_new_tab()
            self.tabs.currentWidget().set_query(query)
        self._switch_sidebar(0)

    def _clear_history(self):
        self.query_history.queries.clear()
        self.query_history.save_history()
        self._history_list.clear()

    # ─── Table filter ─────────────────────────────────────────────────────────

    def filter_tables(self, search_text: str):
        """Fuzzy-match table names; bold-highlight matched characters."""
        from PySide6.QtGui import QBrush, QColor

        raw = search_text.strip()
        query = raw.lower()

        # ── Reset all items ───────────────────────────────────────────────────
        for table_name, item in self.all_table_items.items():
            item.setHidden(False)
            item.setText(0, table_name)   # clear previous highlight
            item.setForeground(0, QBrush(QColor("#e5e5ea")))

        if not query:
            return

        # ── Score every table ─────────────────────────────────────────────────
        def _score(name: str) -> int:
            nl = name.lower()
            if nl == query:                    return 1000
            if nl.startswith(query):           return 900
            if query in nl:                    return 800
            # fuzzy: all chars of query appear in order in name
            idx = 0
            for ch in nl:
                if idx < len(query) and ch == query[idx]:
                    idx += 1
            if idx == len(query):              return 700
            return -1   # no match

        scored = []
        for table_name, item in self.all_table_items.items():
            s = _score(table_name)
            item.setHidden(s < 0)
            if s >= 0:
                scored.append((s, table_name, item))

        # ── Highlight matched characters in green ─────────────────────────────
        # QTreeWidget doesn't support rich text, so we colour the whole item
        # for prefix/exact matches and use normal colour for fuzzy hits.
        for s, table_name, item in scored:
            if s >= 800:
                # Direct substring match — tint green
                item.setForeground(0, QBrush(QColor("#89d185")))
            elif s == 700:
                # Fuzzy match — dim tint to distinguish from prefix matches
                item.setForeground(0, QBrush(QColor("#6cba68")))

        # ── Re-sort visible items so best matches appear first ────────────────
        # QTreeWidget doesn't have a built-in sort by custom score, so we
        # reorder children of each top-level category item.
        def _reorder(parent_item):
            children = []
            for i in range(parent_item.childCount()):
                child = parent_item.child(i)
                if not child.isHidden():
                    name = child.text(0)
                    children.append((_score(name), name, child))
            # Sort descending by score, then alphabetically
            children.sort(key=lambda x: (-x[0], x[1]))
            for rank, (_, _, child) in enumerate(children):
                parent_item.removeChild(child)
                parent_item.insertChild(rank, child)

        root = self.schema_tree.invisibleRootItem()
        for i in range(root.childCount()):
            _reorder(root.child(i))

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
                result.append({"type": "table", "name": w.table_name})
            elif isinstance(w, SqlTab):
                query = w.get_query() if hasattr(w, "get_query") else ""
                result.append({
                    "type": "query",
                    "name": self.tabs.tabText(i),
                    "query": query,
                    "pinned": getattr(w, 'pinned', False),
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
                    if tab.get("pinned") and hasattr(w, 'pin_btn'):
                        w.pin_btn.setChecked(True)
                        w.pinned = True
                        # Add ★ prefix to tab label if not already
                        if not label.startswith("★ "):
                            self.tabs.setTabText(idx, f"★ {label}")
            except Exception as ex:
                logger.error(f"Failed to restore tab {tab}: {ex}")

    def _do_reconnect(self):
        """Manually reconnect to the database and reload the schema."""
        reconnected_ok = False
        try:
            self._reconnect_btn.setEnabled(False)
            self._reconnect_btn.setText("…")

            # Pick up any edits made in the Connection Manager while this
            # tab stayed open (host, port, credentials, environment, ...)
            # instead of reusing whatever was captured when the tab was
            # first opened (GitHub issue #17).
            from ui.connection_dialog import ConnectionDialog
            fresh = ConnectionDialog.load_connection_by_id(self.config.get("id"))
            if fresh is not None:
                self.config.clear()
                self.config.update(fresh)

            self.db_service._reconnect(self.config)
            reconnected_ok = True
            # Refresh version label
            try:
                self._server_version = self.db_service.get_server_version()
            except Exception:
                pass
            self.load_schema()
            self.health_changed.emit('idle')
            db_type = self.config.get('type', 'DB').upper()
            self.reconnected.emit(f"Reconnected to {db_type} — {self.label}")
        except Exception as ex:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Reconnect Failed", str(ex))
            self.health_changed.emit('disconnected')
        finally:
            self._reconnect_btn.setEnabled(True)
            self._reconnect_btn.setText("⟳ Reconnect")

    # ─── Health check ────────────────────────────────────────────────────────────

    def _check_health(self):
        """Called every 30s — runs the actual ping on a daemon thread so the
        UI never blocks, and skips when a query is already in flight on the
        shared connection (prevents packet-sequence corruption)."""
        # Skip entirely if any tab is mid-query on this connection
        for i in range(self.tabs.count()):
            if getattr(self.tabs.widget(i), '_query_running', False):
                return

        import threading
        def _ping():
            try:
                was_disconnected = getattr(self, '_last_health', 'idle') == 'disconnected'
                ok = self.db_service.is_connected()
                status = 'idle' if ok else 'disconnected'
                self._last_health = status
                # Emit on main thread via a queued call
                self.health_changed.emit(status)
                if ok and was_disconnected:
                    db_type = self.config.get('type', 'DB').upper()
                    self.reconnected.emit(f"Reconnected to {db_type} — {self.label}")
            except Exception:
                pass

        t = threading.Thread(target=_ping, daemon=True)
        t.start()

    # ─── Pinned tab persistence ───────────────────────────────────────────────────

    def _save_pinned_tabs(self):
        """Persist all pinned SQL tabs to pinned_tabs.json."""
        from utils import pinned_tabs as _pt
        conn_name = self.label
        all_pinned = _pt.load()
        pinned_list = []
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, SqlTab) and getattr(w, 'pinned', False):
                pinned_list.append({
                    "name": self.tabs.tabText(i).lstrip("★ "),
                    "query": w.get_query() if hasattr(w, 'get_query') else "",
                })
        all_pinned[conn_name] = pinned_list
        _pt.save(all_pinned)

    def restore_pinned_tabs(self):
        """Reopen pinned tabs from pinned_tabs.json (called on startup)."""
        from utils import pinned_tabs as _pt
        conn_name = self.label
        pinned_list = _pt.load().get(conn_name, [])
        for entry in pinned_list:
            self.add_new_tab()
            idx = self.tabs.count() - 1
            w = self.tabs.widget(idx)
            name = entry.get("name", f"Tab {idx + 1}")
            self.tabs.setTabText(idx, f"★ {name}")
            if hasattr(w, 'set_query') and entry.get('query'):
                w.set_query(entry['query'])
            if hasattr(w, 'pin_btn'):
                w.pin_btn.setChecked(True)
                w.pinned = True

    # ─── Public helpers ───────────────────────────────────────────────────────

    @property
    def label(self) -> str:
        base = self.config.get("name", "Connection")
        ver = getattr(self, '_server_version', '')
        env = environment.normalize(self.config.get("environment"))
        env_suffix = f"  {environment.BADGE_LABELS[env]}" if env != environment.UNCLASSIFIED else ""
        read_only_suffix = "  🔒 READ-ONLY" if self.config.get("read_only") else ""
        ver_suffix = f"  [{ver}]" if ver else ""
        return f"{base}{env_suffix}{read_only_suffix}{ver_suffix}"

    def disconnect(self):
        try:
            self._health_timer.stop()
        except Exception as ex:
            logger.debug(f"Failed to stop health timer: {ex}")
        try:
            self.db_service.disconnect()
        except Exception as ex:
            logger.debug(f"Failed to disconnect cleanly: {ex}")

    # ─── Query-done toast notification ───────────────────────────────────────

    def _show_query_toast(self, tab_name: str, row_count: int, elapsed: float):
        """Slide-in notification from the right when a background query finishes."""
        from PySide6.QtWidgets import QFrame, QLabel, QHBoxLayout
        from PySide6.QtCore import QTimer, QPropertyAnimation, QRect, QEasingCurve
        from PySide6.QtGui import QColor

        # Build toast widget parented to this panel (so it clips to its bounds)
        toast = QFrame(self)
        toast.setWindowFlags(Qt.FramelessWindowHint | Qt.ToolTip)
        toast.setAttribute(Qt.WA_TranslucentBackground, False)
        toast.setStyleSheet("""
            QFrame {
                background: #1e3a2e;
                border: 1px solid #30d158;
                border-radius: 8px;
            }
            QLabel { color: #e5e5ea; font-size: 13px; background: transparent; border: none; }
        """)

        h = QHBoxLayout(toast)
        h.setContentsMargins(14, 10, 14, 10)
        h.setSpacing(8)
        icon = QLabel("✓")
        icon.setStyleSheet("color: #30d158; font-size: 16px; font-weight: bold; background:transparent; border:none;")
        text = QLabel(f"<b>{tab_name}</b> — {row_count} row{'s' if row_count != 1 else ''} in {elapsed:.2f}s")
        h.addWidget(icon)
        h.addWidget(text)

        toast.adjustSize()
        tw, th = toast.width(), toast.height()

        # Position: bottom-right corner of this panel
        pw, ph = self.width(), self.height()
        margin = 16
        shown_x  = pw - tw - margin
        hidden_x = pw + tw          # starts off-screen to the right
        y        = ph - th - margin

        toast.setGeometry(hidden_x, y, tw, th)
        toast.show()
        toast.raise_()

        # Slide in
        anim_in = QPropertyAnimation(toast, b"geometry")
        anim_in.setDuration(280)
        anim_in.setEasingCurve(QEasingCurve.OutCubic)
        anim_in.setStartValue(QRect(hidden_x, y, tw, th))
        anim_in.setEndValue(QRect(shown_x,  y, tw, th))
        anim_in.start()
        # Keep a reference so GC doesn't kill it
        toast._anim_in = anim_in

        def _slide_out():
            anim_out = QPropertyAnimation(toast, b"geometry")
            anim_out.setDuration(280)
            anim_out.setEasingCurve(QEasingCurve.InCubic)
            anim_out.setStartValue(QRect(shown_x,  y, tw, th))
            anim_out.setEndValue(QRect(hidden_x, y, tw, th))
            anim_out.finished.connect(toast.deleteLater)
            anim_out.start()
            toast._anim_out = anim_out

        QTimer.singleShot(2800, _slide_out)
