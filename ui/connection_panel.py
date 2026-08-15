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
    QApplication,
)
from PySide6.QtGui import QShortcut, QKeySequence, QCursor

from services.db_service import DbService
from services.query_history import QueryHistory
from services.saved_queries import SavedQueries
from services.schema_snapshot import fetch_schema_snapshot
from ui.sql_tab import SqlTab
from ui.table_view_widget import TableViewWidget
from ui.quick_search_dialog import QuickSearchDialog
from ui.snippet_manager import SnippetManager
from ui.structure_editor import StructureEditorDialog
from ui.db_switcher_dialog import DbSwitcherDialog
from ui.query_history_dialog import QueryHistoryDialog
from ui.query_library_dialog import QueryLibraryDialog
from ui.export_scope_dialog import ExportScopeDialog
from ui.column_selection_dialog import ColumnSelectionDialog
from ui.theme_manager import ThemeManager
from ui.erd_dialog import ErdDialog
from ui.schema_compare_dialog import SchemaCompareDialog
from ui import query_guard_dialog
from utils.logger import get_logger
from utils import environment
from utils import schema_cache
from utils.df_export import export_dataframe, _to_sql_inserts
from services import query_classifier
from services import table_organization

logger = get_logger()

# CSV imports at or above this row count are treated as a "mass write" for
# the dangerous-query guard on Staging/Production, even though a plain
# INSERT alone isn't otherwise flagged (ai/load-context.md: "mass writes,
# where a reliable row-count estimate is available" — a CSV import's
# DataFrame length is exactly that).
MASS_WRITE_ROW_THRESHOLD = 5000


# ── Background query worker (must be a top-level class for PySide6) ──────────

# Cap ad-hoc SQL editor results at this many rows so a stray `SELECT *`
# on a multi-million-row table can't pull the whole thing into memory
# before pagination ever gets a chance to run (issue #74). Doesn't apply
# to exports/"browse table", which need (or already paginate) full data.
_MAX_RESULT_ROWS = 100_000


class _QueryWorker(QObject):
    """Runs one or more SQL statements on a QThread and emits the result.
    Receives a *dedicated* DbService connection so it never shares state
    with the main connection used for schema browsing. That connection is
    normally closed once this run finishes (see `finally` below) — but if
    the run left it inside a manual transaction (Slice 4, ai/load-
    context.md), closing it here would silently lose or half-commit the
    user's open transaction, so the caller (ConnectionPanel) is responsible
    for keeping it alive across subsequent runs in that case."""
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
                results = self._db.execute_multi_query(self._q, max_rows=_MAX_RESULT_ROWS)
                elapsed = time.time() - t0
                if self._flag.is_set():
                    self.cancelled.emit()
                else:
                    self.multi_done.emit(results, elapsed)
            else:
                df = self._db.execute_query(self._q, max_rows=_MAX_RESULT_ROWS)
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
            # Close the dedicated query connection when done — unless this
            # run left it inside an open transaction, in which case it must
            # survive to the next Run/Commit/Rollback click on this tab.
            if not self._db.in_transaction:
                try:
                    self._db.disconnect()
                except Exception:
                    pass


class _ClickableRow(QWidget):
    """A QWidget that behaves like a button for the schema sidebar's
    category rows (All Tables / Views / Functions) — plain QPushButton
    can't lay out an icon + label + right-aligned count cleanly."""
    clicked = Signal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Plain QWidget ignores a stylesheet "background" unless told to
        # paint it — without this the active-category highlight silently
        # never renders (caught by grabbing an offscreen render, not by any
        # of the structural checks, which only look at the stylesheet
        # *string*, not the actual paint).
        self.setAttribute(Qt.WA_StyledBackground, True)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


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
    # 'idle' / 'running' / 'disconnected' / 'connecting'
    health_changed = Signal(str)
    # brief human-readable message (e.g. "Reconnected to MySQL")
    reconnected    = Signal(str)
    # Bridge signals for background schema load
    _schema_done   = Signal(object)   # (schema_data dict)
    _schema_error  = Signal(str)      # error message
    _schema_fast   = Signal(list, dict)   # (tables, columns) — arrives ahead of _schema_done
    # Bridge signal for the optimistic-open background connect (issue: lag on
    # previously-visited remote/SSH connections despite a warm schema cache)
    _bg_connect_done = Signal(str)    # error message, "" on success

    def __init__(self, config: dict, db_service: DbService,
                 query_history: QueryHistory, saved_queries: SavedQueries = None,
                 parent=None, already_connected: bool = True):
        super().__init__(parent)

        self.config = config
        self.db_service = db_service
        self.query_history = query_history
        self.saved_queries = saved_queries if saved_queries is not None else SavedQueries()
        # True while db_service.connect() is still running on a background
        # thread (see _connect_in_background). Cached schema is shown
        # immediately regardless; write/reconnect actions are held off
        # until this clears, since they'd otherwise race the connect call.
        self._connecting = not already_connected

        self.all_tables = []
        self.all_table_items = {}
        self.all_views = []
        self.all_view_items = {}
        self.all_functions = []
        self.all_function_items = {}
        self.table_index = {}
        self.all_schema_items = []
        self.current_theme = "dark"
        self._available_dbs: list[str] = []

        # Schema sidebar category filter (All Tables / Views / Functions) —
        # a flat, single-category-at-a-time list styled after a categorized
        # sidebar with live counts, rather than a nested Tables/Views/
        # Functions tree the user always has to expand.
        self._active_category = "tables"
        self._category_rows = {}
        self._category_count_labels = {}
        self._category_icon_emoji = {"tables": "\U0001F5C3", "views": "\U0001F441", "functions": "ƒ"}
        self._icon_cache = {}

        # Schema-loading progress indicator (issue #57) — ticks the elapsed
        # time on whichever top-level tree row represents an in-flight
        # fetch (first-time "Loading…" or a stale-cache "refreshing…" row),
        # so it reads as visibly active rather than a frozen placeholder.
        self._schema_load_timer = None
        self._schema_load_start = 0.0
        self._schema_status_item = None
        self._schema_tables_seen = 0
        self._schema_retry_item = None
        self._notify_schema_refresh = False

        # Wire bridge signals → main-thread handlers (connected once here so
        # QueuedConnection always delivers on the main thread event loop).
        self._q_done.connect(self._on_query_done, Qt.QueuedConnection)
        self._q_multi_done.connect(self._on_query_multi_done, Qt.QueuedConnection)
        self._q_errored.connect(self._on_query_errored, Qt.QueuedConnection)
        self._q_cancelled.connect(self._on_query_cancelled, Qt.QueuedConnection)
        self._schema_done.connect(self._on_schema_loaded, Qt.QueuedConnection)
        self._schema_error.connect(self._on_schema_error, Qt.QueuedConnection)
        self._schema_fast.connect(self._on_schema_tables_ready, Qt.QueuedConnection)
        self._bg_connect_done.connect(self._on_background_connect_done, Qt.QueuedConnection)
        self._column_cache: dict = {}   # {table: [col, ...]} for autocomplete

        self._build_ui()
        self.load_schema()

        if self._connecting:
            self.health_changed.emit('connecting')
            self._connect_in_background()

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

        # Refresh Schema / Reconnect / ER Diagram / Compare Schema live in
        # the Database menu (main.py) instead of dedicated toolbar buttons
        # here (issue #68).

        # Cmd+Shift+R — refresh schema
        QShortcut(QKeySequence("Ctrl+Shift+R"), self).activated.connect(self.load_schema)

        # ── Category filter: All Tables / Views / Functions, with live
        # counts — a flat single-category list instead of an always-nested
        # Tables/Views/Functions tree the user has to expand every time.
        cat_container = QWidget()
        cat_layout = QVBoxLayout(cat_container)
        cat_layout.setContentsMargins(0, 2, 0, 2)
        cat_layout.setSpacing(1)
        for key, label in (("tables", "All Tables"), ("views", "Views"), ("functions", "Functions")):
            row = _ClickableRow()
            row.setCursor(Qt.PointingHandCursor)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(8, 5, 8, 5)
            row_layout.setSpacing(8)
            icon_lbl = QLabel(self._category_icon_emoji[key])
            icon_lbl.setFixedWidth(16)
            row_layout.addWidget(icon_lbl)
            row_layout.addWidget(QLabel(label))
            row_layout.addStretch()
            count_lbl = QLabel("0")
            row_layout.addWidget(count_lbl)
            row.clicked.connect(lambda checked=False, k=key: self._set_active_category(k))
            cat_layout.addWidget(row)
            self._category_rows[key] = row
            self._category_count_labels[key] = count_lbl
        left_layout.addWidget(cat_container)
        self._update_category_row_styles()

        self.table_search = QLineEdit()
        self.table_search.setPlaceholderText("Search tables...")
        self.table_search.textChanged.connect(self.filter_tables)
        left_layout.addWidget(self.table_search)

        # ── Schema / Queries / History toggle ──────────────────────────
        # Order is Schema, Queries, History (issue #130) — Queries sits
        # ahead of History since it's the more actively-used workflow.
        sidebar_toggle = QWidget()
        toggle_layout = QHBoxLayout(sidebar_toggle)
        toggle_layout.setContentsMargins(0, 0, 0, 0)
        toggle_layout.setSpacing(0)

        self._schema_btn = QPushButton("Schema")
        self._schema_btn.setCheckable(True)
        self._schema_btn.setChecked(True)
        self._schema_btn.setFlat(True)
        self._schema_btn.clicked.connect(lambda: self._switch_sidebar(0))

        self._queries_btn = QPushButton("Queries")
        self._queries_btn.setCheckable(True)
        self._queries_btn.setChecked(False)
        self._queries_btn.setFlat(True)
        self._queries_btn.clicked.connect(lambda: self._switch_sidebar(1))

        self._history_btn = QPushButton("History")
        self._history_btn.setCheckable(True)
        self._history_btn.setChecked(False)
        self._history_btn.setFlat(True)
        self._history_btn.clicked.connect(lambda: self._switch_sidebar(2))

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
        self._queries_btn.setStyleSheet(_toggle_style)
        self._history_btn.setStyleSheet(_toggle_style)
        toggle_layout.addWidget(self._schema_btn)
        toggle_layout.addWidget(self._queries_btn)
        toggle_layout.addWidget(self._history_btn)
        toggle_layout.addStretch()
        left_layout.addWidget(sidebar_toggle)

        # ── Stacked: page 0 = schema tree, page 1 = queries, page 2 = history ──
        self._sidebar_stack = QStackedWidget()

        self.schema_tree = QTreeWidget()
        self.schema_tree.setHeaderHidden(True)
        self.schema_tree.setIndentation(15)
        self.schema_tree.setAnimated(True)
        self.schema_tree.itemClicked.connect(self._on_item_clicked)
        self.schema_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.schema_tree.customContextMenuRequested.connect(self._show_context_menu)
        self._sidebar_stack.addWidget(self.schema_tree)   # index 0

        # Queries panel (favorite + saved queries — issue #130)
        queries_panel = QWidget()
        qp_layout = QVBoxLayout(queries_panel)
        qp_layout.setContentsMargins(0, 0, 0, 0)
        qp_layout.setSpacing(2)

        self._queries_search = QLineEdit()
        self._queries_search.setPlaceholderText("Search queries...")
        self._queries_search.textChanged.connect(self._filter_queries_list)
        qp_layout.addWidget(self._queries_search)

        self._queries_list = QListWidget()
        self._queries_list.setWordWrap(False)
        qp_layout.addWidget(self._queries_list)

        view_all_btn = QPushButton("View all saved queries...")
        view_all_btn.setFlat(True)
        view_all_btn.clicked.connect(self._open_query_library)
        qp_layout.addWidget(view_all_btn)

        save_query_btn = QPushButton("＋ Save Current Query")
        save_query_btn.setFlat(True)
        save_query_btn.clicked.connect(self._save_current_query)
        qp_layout.addWidget(save_query_btn)

        self._sidebar_stack.addWidget(queries_panel)          # index 1

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

        self._sidebar_stack.addWidget(history_panel)         # index 2

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

    # ─── Schema sidebar category filter ────────────────────────────────────────

    def _emoji_icon(self, emoji: str):
        """Rasterize *emoji* to a small QIcon (cached) — this app has no
        icon image assets, everything is unicode/emoji, consistent with
        menu actions elsewhere in this file."""
        if emoji in self._icon_cache:
            return self._icon_cache[emoji]
        from PySide6.QtGui import QIcon, QPixmap, QPainter, QFont
        size = 16
        pix = QPixmap(size, size)
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        f = QFont()
        f.setPointSize(11)
        painter.setFont(f)
        painter.drawText(pix.rect(), Qt.AlignCenter, emoji)
        painter.end()
        icon = QIcon(pix)
        self._icon_cache[emoji] = icon
        return icon

    def _update_category_row_styles(self):
        for key, row in self._category_rows.items():
            active = key == self._active_category
            row.setStyleSheet(
                f"background: {'#0A84FF' if active else 'transparent'}; border-radius: 4px;")
            for child in row.findChildren(QLabel):
                child.setStyleSheet(
                    f"color: {'#ffffff' if active else '#c7c7cc'}; background: transparent;")

    def _set_active_category(self, category: str):
        if category == self._active_category:
            return
        self._active_category = category
        self._update_category_row_styles()
        self.table_search.setPlaceholderText(f"Search {category}...")
        self.table_search.blockSignals(True)
        self.table_search.clear()
        self.table_search.blockSignals(False)
        self._render_active_category()

    def _active_category_items(self) -> dict:
        return {
            "tables": self.all_table_items,
            "views": self.all_view_items,
            "functions": self.all_function_items,
        }[self._active_category]

    def _render_active_category(self):
        """Rebuild schema_tree as a flat list of just the active category's
        items — no folder wrapper, matching a categorized-sidebar layout
        instead of an always-nested Tables/Views/Functions tree."""
        self.schema_tree.clear()
        names = {
            "tables": self.all_tables,
            "views": self.all_views,
            "functions": self.all_functions,
        }[self._active_category]
        items_map = self._active_category_items()
        items_map.clear()
        icon = self._emoji_icon(self._category_icon_emoji[self._active_category])

        # Pin to Top / Add to Favorites (issue #142's Organization group) —
        # only meaningful for tables/views, which is all the context menu
        # that sets them covers.
        pinned, favorites = set(), set()
        if self._active_category in ("tables", "views"):
            conn_id = self.config.get("id", "")
            database = self.config.get("database", "")
            pinned = table_organization.get_pinned(conn_id, database)
            favorites = table_organization.get_favorites(conn_id, database)
            names = sorted(names, key=lambda n: (n not in pinned, n))

        for name in names:
            item = QTreeWidgetItem([name])
            if name in pinned:
                item.setIcon(0, self._emoji_icon("📌"))
                item.setToolTip(0, "Pinned" + (" · Favorite" if name in favorites else ""))
            elif name in favorites:
                item.setIcon(0, self._emoji_icon("⭐"))
                item.setToolTip(0, "Favorite")
            else:
                item.setIcon(0, icon)
            items_map[name] = item
            self.schema_tree.addTopLevelItem(item)
        self.filter_tables(self.table_search.text())

    def _clear_schema_state(self):
        self.all_tables.clear()
        self.all_table_items.clear()
        self.all_views.clear()
        self.all_view_items.clear()
        self.all_functions.clear()
        self.all_function_items.clear()
        self.all_schema_items.clear()
        self.schema_tree.clear()
        for lbl in self._category_count_labels.values():
            lbl.setText("0")

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

        if self.config.get("read_only") and any(
            c.kind in query_classifier.READ_ONLY_BLOCKED_KINDS for c in classifications
        ):
            blocked = [
                c.statement for c in classifications
                if c.kind in query_classifier.READ_ONLY_BLOCKED_KINDS
            ]
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

    def load_schema(self, notify: bool = False):
        """notify=True (explicit "Refresh Schema" actions only, not the
        initial connect / db-switch calls) shows a toast once the live
        background fetch actually completes. Needed because when a fresh
        cache hit exists (the common case — issue #71), the tree repaints
        instantly from disk and the live refresh underneath is otherwise
        completely silent: same tree, no ticker, no marker, nothing to tell
        the user anything happened at all."""
        # Don't attempt schema load if not connected — except while an
        # optimistic background connect is in flight (self._connecting):
        # the live fetch below uses its own dedicated connection anyway
        # (services/schema_snapshot.py), so it doesn't need self.db_service
        # to be live, and the cache check further down still applies.
        if not self.db_service or (not self.db_service.connection and not self._connecting):
            self._stop_schema_loading_indicator()
            self._clear_schema_state()
            return

        # Clear immediately so the user sees empty tree straight away
        self._stop_schema_loading_indicator()
        self._schema_retry_item = None
        self._clear_schema_state()

        # Issue #71: a previously visited connection/database populates the
        # tree and autocomplete instantly from disk, no network round-trip.
        # The live fetch below still runs and silently refreshes it. This
        # cache-paint call is intentionally excluded from the notify below —
        # it isn't the refresh completing, just the instant first paint.
        cached = schema_cache.load(
            self.config.get("id", ""), self.config.get("database", ""))
        if not self._apply_cached_schema(cached):
            # Issue #57: a first-time connect had nothing but a static
            # "Loading…" row for however long the fetch took — easy to
            # mistake for a frozen app on a large schema. Tick elapsed time
            # (mirrors the Run button's own "⏳ 0.0s" ticker) so it visibly
            # keeps moving, and upgrade the text with a table count the
            # moment that's known (_on_schema_tables_ready, ahead of the
            # slower dbs/views/functions round-trips per issue #16).
            loading_item = QTreeWidgetItem(["⏳ Loading schema…"])
            self.schema_tree.addTopLevelItem(loading_item)
            self._start_schema_loading_indicator(loading_item)

        self._notify_schema_refresh = notify
        self._spawn_schema_fetch(dict(self.config))

    def _apply_cached_schema(self, cached: dict | None) -> bool:
        """Populate the tree/autocomplete from a cached snapshot (issue
        #71) immediately; True on a hit. When the cache is past its
        freshness window (issue #72), flags it in the tree — the marker
        disappears on its own the moment the live background refresh
        (_on_schema_loaded) rebuilds the tree with current data."""
        if not cached:
            return False
        self._on_schema_tables_ready(cached.get("tables", []), cached.get("columns", {}))
        self._on_schema_loaded(cached)
        if schema_cache.is_stale(cached):
            stale_item = QTreeWidgetItem(["⏱ Cached schema (stale) — refreshing…"])
            self.schema_tree.insertTopLevelItem(0, stale_item)
            self._start_schema_loading_indicator(stale_item)
        return True

    def _start_schema_loading_indicator(self, item: QTreeWidgetItem):
        """Attach a live elapsed-time ticker to *item* (already inserted in
        the tree) for the duration of the in-flight fetch (issue #57)."""
        self._schema_status_item = item
        self._schema_tables_seen = 0
        self._schema_load_start = time.time()
        if self._schema_load_timer is None:
            from PySide6.QtCore import QTimer
            self._schema_load_timer = QTimer(self)
            self._schema_load_timer.setInterval(200)
            self._schema_load_timer.timeout.connect(self._tick_schema_loading)
        self._tick_schema_loading()
        self._schema_load_timer.start()

    def _tick_schema_loading(self):
        if self._schema_status_item is None:
            return
        elapsed = time.time() - self._schema_load_start
        if self._schema_tables_seen:
            text = (f"⏳ {self._schema_tables_seen:,} table(s) found — "
                    f"loading details… {elapsed:.1f}s")
        else:
            text = f"⏳ Loading schema… {elapsed:.1f}s"
        self._schema_status_item.setText(0, text)

    def _stop_schema_loading_indicator(self):
        if self._schema_load_timer is not None:
            self._schema_load_timer.stop()
        self._schema_status_item = None

    def _spawn_schema_fetch(self, conf: dict):
        """Fetch schema on a daemon thread using a *dedicated* connection
        (see services/schema_snapshot.py) — never touches self.db_service,
        which the main thread may be using concurrently (query tabs, table
        views). Results come back via _schema_done/_schema_fast."""
        sig_done  = self._schema_done
        sig_error = self._schema_error
        sig_fast  = self._schema_fast

        def _worker():
            try:
                sig_done.emit(fetch_schema_snapshot(
                    conf, on_tables_ready=lambda t, c: sig_fast.emit(t, c)))
            except Exception as ex:
                # Suppress silent "not connected" errors (e.g. (0, '') on startup)
                msg = str(ex)
                if msg in ("(0, '')", "0", "") or "not connected" in msg.lower():
                    return  # connection not ready yet — no error shown
                sig_error.emit(msg)

        threading.Thread(target=_worker, daemon=True).start()

    def _connect_in_background(self):
        """Optimistic open (previously-visited remote/SSH connection with a
        warm schema cache, see main.py): the panel is already showing
        cached schema, so run the real db_service.connect() off the main
        thread instead of blocking behind a modal dialog. Only this thread
        touches self.db_service until _on_background_connect_done fires —
        _switch_database/_do_reconnect refuse to run concurrently with it
        (self._connecting guard)."""
        conf = dict(self.config)
        sig_done = self._bg_connect_done

        def _worker():
            try:
                self.db_service.connect(conf)
                sig_done.emit("")
            except Exception as ex:
                sig_done.emit(str(ex) or "Connection failed")

        threading.Thread(target=_worker, daemon=True).start()

    def _on_background_connect_done(self, error: str):
        self._connecting = False
        self._update_pill_label()
        if error:
            self.health_changed.emit('disconnected')
            QMessageBox.critical(self, "Connection Failed", error)
        else:
            self.health_changed.emit('idle')
            # Now that self.db_service is genuinely live, re-run the schema
            # fetch so the tree/autocomplete reflect the real connection
            # (e.g. if the configured database didn't exist and MySQL fell
            # back to another one — see fetch_schema_snapshot's switched_db).
            self.load_schema()

    def _on_schema_tables_ready(self, tables: list, columns: dict):
        """Push tables/columns to autocomplete as soon as they're fetched —
        ahead of the slower dbs/views/functions/server_version round-trips
        that _on_schema_loaded waits for (issue #16)."""
        self._column_cache = columns
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            if isinstance(tab, SqlTab):
                tab.set_schema(tables, columns)
        # Issue #57: surface the table count on the loading indicator the
        # moment it's known, rather than leaving it a bare "Loading…" for
        # the remainder of the (slower) dbs/views/functions round-trips.
        if self._schema_status_item is not None:
            self._schema_tables_seen = len(tables)
            self._tick_schema_loading()

    def _on_schema_loaded(self, result: dict):
        """Main-thread: populate the schema tree from background result."""
        self._stop_schema_loading_indicator()
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
                    self.db_service.select_db(new_db)
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

        # Populate the flat name/index state; _render_active_category()
        # below builds the visible tree from just the active category.
        self.all_tables = list(tables)
        self.all_views = list(views)
        self.all_functions = list(functions)
        self.all_schema_items = (
            [("table", t) for t in tables]
            + [("view", v) for v in views]
            + [("function", fn) for fn in functions]
        )
        self.table_index.clear()
        for table_name in tables:
            if len(table_name) >= 3:
                self.table_index.setdefault(
                    table_name[:3].lower(), []).append(table_name)

        self._category_count_labels["tables"].setText(str(len(tables)))
        self._category_count_labels["views"].setText(str(len(views)))
        self._category_count_labels["functions"].setText(str(len(functions)))
        self._render_active_category()

        # Update autocomplete in existing SQL tabs (TableViewWidget is a data
        # grid, not an editor — it has no autocomplete/schema to push).
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            if isinstance(tab, SqlTab):
                tab.set_schema(tables, columns)

        if getattr(self, "_notify_schema_refresh", False):
            self._notify_schema_refresh = False
            from utils.toast import show_toast
            show_toast(
                self,
                f"Schema refreshed — {len(tables)} table(s), {len(views)} view(s)",
                icon="✓", kind="success",
            )

    def _on_schema_error(self, msg: str):
        self._stop_schema_loading_indicator()
        self.schema_tree.clear()
        err = QTreeWidgetItem([f"⚠ Failed to load schema: {msg}"])
        self.schema_tree.addTopLevelItem(err)
        if getattr(self, "_notify_schema_refresh", False):
            self._notify_schema_refresh = False
            from utils.toast import show_toast
            show_toast(self, "Failed to refresh schema", icon="⚠", kind="warning")
        # Issue #57: give failure an explicit retry affordance rather than
        # just leaving a dead-end error row — reuses load_schema() via the
        # existing itemClicked wiring (_on_item_clicked), same action as
        # the "↺ Schema" toolbar button.
        retry = QTreeWidgetItem(["↺  Click to retry"])
        self.schema_tree.addTopLevelItem(retry)
        self._schema_retry_item = retry
        logger.error(f"Schema load error: {msg}")

    def _load_databases(self) -> bool:
        """Blocking DB-list fetch used by refresh/create/drop-database flows.
        Returns True on success, False if the fetch failed — callers that
        need to surface that (e.g. refresh_databases) check the result."""
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
            self._update_pill_label()
            return True
        except Exception as ex:
            logger.error(f"Failed to load databases: {ex}")
            self._available_dbs = []
            self._update_pill_label()
            return False

    def _update_pill_label(self):
        current_db = self.config.get("database", "") or "(no database)"
        suffix = "  ⌘K" if self._available_dbs else ""
        self.db_pill.setText(f"  {current_db}{suffix}")
        # Kept disabled while the background connect from an optimistic
        # open is still in flight, even though cached "dbs" may already be
        # populated — switching would race db_service.connect() (see
        # _connect_in_background).
        self.db_pill.setEnabled(bool(self._available_dbs) and not self._connecting)

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
        if self._connecting:
            QMessageBox.information(
                self, "Connecting…",
                "Still connecting to the database — try switching in a moment.")
            return

        # Show spinner in schema tree
        self.schema_tree.clear()
        self.schema_tree.addTopLevelItem(QTreeWidgetItem(["Switching database…"]))
        from PySide6.QtWidgets import QApplication as _QApp
        _QApp.processEvents()

        # MySQL can point the existing connection at the new database with a
        # single lightweight command (COM_INIT_DB) — no new TCP handshake,
        # auth round-trip, or (potentially tunnelled) SSH setup. Previously
        # every switch paid the full disconnect+reconnect cost, which is
        # where the multi-second freeze reported in issue #23 actually came
        # from. Postgres/SQLite connections are bound to one database for
        # their lifetime, so they still need a real reconnect — done
        # synchronously (as before) so nothing else can use the shared
        # connection mid-reconnect. The (potentially slower) schema listing
        # always runs on a background thread over its OWN dedicated
        # connection (services/schema_snapshot.py) either way.
        if self.db_service.db_type == "mysql" and self.db_service.connection:
            try:
                self.db_service.select_db(new_db)
            except Exception as ex:
                self._on_schema_error(str(ex))
                return
        else:
            try:
                self.db_service.disconnect()
                self.db_service.connect(dict(self.config, database=new_db))
            except Exception as ex:
                self._on_schema_error(str(ex))
                return

        # Only commit the switch to tracked state/the pill once the
        # connection has actually confirmed it. Setting these eagerly
        # (before the try/except above) meant a failed switch left the UI
        # and self.config claiming new_db while the live connection was
        # still silently on the old database — every query in this tab
        # would then run against the wrong database with no indication.
        self.config["database"] = new_db
        self._update_pill_label()

        # Issue #71: populate instantly from disk if this database was
        # visited before; the background fetch below still refreshes it.
        self._apply_cached_schema(schema_cache.load(self.config.get("id", ""), new_db))

        self._spawn_schema_fetch(dict(self.config))

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
        """Issue #138: previously ran _load_databases() with no visual
        feedback at all — a slow/remote connection looked frozen and a
        failure was silent. Busy cursor covers the "in progress" window
        (the fetch is synchronous), a toast confirms the outcome either
        way, matching the pattern already used for query-done toasts."""
        if not self._check_db_management_supported():
            return
        from PySide6.QtWidgets import QApplication as _QApp
        _QApp.setOverrideCursor(Qt.WaitCursor)
        try:
            ok = self._load_databases()
        finally:
            _QApp.restoreOverrideCursor()

        from utils.toast import show_toast
        if ok:
            n = len(self._available_dbs)
            show_toast(
                self, f"Database list refreshed — {n} found",
                icon="✓", kind="success",
            )
        else:
            show_toast(
                self, "Failed to refresh database list",
                icon="⚠", kind="warning",
            )

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
        # Issue #57: the "↺ Click to retry" row shown after a failed
        # schema load — same action as the toolbar's "↺ Schema" button.
        if item is self._schema_retry_item:
            self._schema_retry_item = None
            self.load_schema(notify=True)
            return
        # The tree is a flat list of just the active category's items now
        # (no Tables/Views/Functions folder wrapper) — gate by membership in
        # that category's item map rather than a parent-folder check, so a
        # transient "Loading…"/error row (never added to that map) is a
        # no-op instead of trying to open a table that doesn't exist.
        if self._active_category in ("tables", "views") and item.text(0) in self._active_category_items():
            self.open_table_view(item.text(0))

    def _show_context_menu(self, position):
        """Full table/view context menu (issue #142, TablePlus parity).
        Grouped: navigation, copy, organization, export/import/new/script
        submenus, then table operations with destructive ones visually
        separated at the bottom. Actions with no backing implementation
        (Open in New Window, Item Overview — no multi-window architecture
        or defined behavior to build on) are intentionally omitted rather
        than shown disabled, per the issue's own "do not display actions
        that are not implemented" requirement."""
        item = self.schema_tree.itemAt(position)
        if not item:
            return
        if self._active_category not in ("tables", "views") or item.text(0) not in self._active_category_items():
            return
        table_name = item.text(0)
        is_view = self._active_category == "views"

        conn_id = self.config.get("id", "")
        database = self.config.get("database", "")
        pinned = table_name in table_organization.get_pinned(conn_id, database)
        favorite = table_name in table_organization.get_favorites(conn_id, database)

        menu = QMenu(self)

        # ── Navigation ──────────────────────────────────────────────────
        open_new_tab_action = menu.addAction("📋 Open in New Tab")
        structure_action = menu.addAction("🔍 Open Structure")
        edit_action = menu.addAction("✏️ Edit Structure")
        diagram_action = None
        if not is_view:
            diagram_action = menu.addAction("🗺️ Show Diagram")
        menu.addSeparator()

        # ── Copy ─────────────────────────────────────────────────────────
        copy_name_action = menu.addAction("Copy Name")
        copy_full_name_action = menu.addAction("Copy Full Name")
        menu.addSeparator()

        # ── Organization ────────────────────────────────────────────────
        pin_action = menu.addAction("📌 Unpin from Top" if pinned else "📌 Pin to Top")
        favorite_action = menu.addAction("⭐ Remove from Favorites" if favorite else "⭐ Add to Favorites")
        menu.addSeparator()

        # ── Export submenu ──────────────────────────────────────────────
        export_menu = menu.addMenu("📤 Export")
        export_action = export_menu.addAction("Export Table…")
        export_sql_action = export_menu.addAction("Export Table as SQL")
        export_cols_action = export_menu.addAction("Export Table with Column Selection…")
        export_data_action = export_menu.addAction("Export Table Data")

        # ── Import submenu ──────────────────────────────────────────────
        import_action = None
        if not is_view:
            import_menu = menu.addMenu("📥 Import")
            import_action = import_menu.addAction("Import Data…")

        # ── New submenu ─────────────────────────────────────────────────
        new_menu = menu.addMenu("🆕 New")
        new_table_action = new_menu.addAction("New Table…")
        new_view_action = new_menu.addAction("New View…")

        # ── Copy Script As submenu ──────────────────────────────────────
        script_menu = menu.addMenu("📄 Copy Script As")
        copy_create_action = script_menu.addAction("CREATE Table")
        copy_insert_action = script_menu.addAction("INSERT Data")
        menu.addSeparator()

        # ── Table operations ────────────────────────────────────────────
        clone_action = None
        truncate_action = None
        if not is_view:
            clone_action = menu.addAction("Clone")
        refresh_action = menu.addAction("🔄 Refresh Schema")
        refresh_action.setShortcut(QKeySequence("Ctrl+Shift+R"))  # mirrors the real global binding below
        menu.addSeparator()
        if not is_view:
            truncate_action = menu.addAction("⚠️ Truncate…")
        delete_action = menu.addAction(f"🗑️ Delete {'View' if is_view else 'Table'}…")

        action = menu.exec_(self.schema_tree.mapToGlobal(position))
        if action is None:
            return
        elif action == open_new_tab_action:
            self.open_table_view(table_name, force_new=True)
        elif action == structure_action:
            self._show_table_structure(table_name)
        elif action == edit_action:
            self.show_alter_table_editor(table_name)
        elif diagram_action is not None and action == diagram_action:
            self.open_erd_view(focus_table=table_name)
        elif action == copy_name_action:
            self._copy_table_name(table_name)
        elif action == copy_full_name_action:
            self._copy_table_full_name(table_name)
        elif action == pin_action:
            self._toggle_pin_table(table_name)
        elif action == favorite_action:
            self._toggle_favorite_table(table_name)
        elif action == export_action:
            self._export_table(table_name)
        elif action == export_sql_action:
            self._export_table_as_sql(table_name)
        elif action == export_cols_action:
            self._export_table_with_column_selection(table_name)
        elif action == export_data_action:
            self._export_table_data_only(table_name)
        elif import_action is not None and action == import_action:
            self._import_csv_into_table(table_name)
        elif action == new_table_action:
            self._new_table()
        elif action == new_view_action:
            self._new_view()
        elif action == copy_create_action:
            self._copy_create_table_script(table_name)
        elif action == copy_insert_action:
            self._copy_insert_script(table_name)
        elif clone_action is not None and action == clone_action:
            self._clone_table(table_name)
        elif action == refresh_action:
            self.load_schema(notify=True)
        elif truncate_action is not None and action == truncate_action:
            self._truncate_table(table_name)
        elif action == delete_action:
            self._delete_table(table_name)

    # ─── Table/query tabs ─────────────────────────────────────────────────────

    def open_erd_view(self, focus_table: str = None):
        """Open a read-only ER diagram of the current database (issue #62).
        Builds its own dedicated connection (services/erd_model.py) — never
        touches self.db_service. *focus_table*, when given (context menu's
        "Show Diagram"), selects and centers that table once the graph loads
        rather than dropping the user on the unfocused whole-database view."""
        if not self.db_service or not self.db_service.connection:
            QMessageBox.information(self, "ER Diagram", "Connect to a database first.")
            return
        dlg = ErdDialog(dict(self.config), is_dark=(self.current_theme == "dark"), parent=self,
                         focus_table=focus_table)
        dlg.open_table.connect(self.open_table_view)
        dlg.view_structure.connect(self._show_table_structure)
        dlg.exec_()

    def open_schema_compare(self):
        """Open the read-only Schema Compare dialog (issue #68), preselecting
        this connection as Source. Builds its own dedicated connections for
        both sides (services/schema_diff.py) — never touches self.db_service."""
        dlg = SchemaCompareDialog(
            self.config.get("id", ""), is_dark=(self.current_theme == "dark"), parent=self)
        dlg.exec_()

    def open_table_view(self, table_name: str, force_new: bool = False):
        """Open a table view; re-focus if already open, unless *force_new*
        (context menu's "Open in New Tab") asks for a fresh tab regardless."""
        if not force_new:
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

    def ensure_at_least_one_tab(self):
        """Guarantee this panel has at least one query tab. Called once
        right after a panel becomes interactive (fresh connect or session
        restore) so its tab bar's native view is realized immediately —
        before the user can ever switch the main window to full screen.
        Confirmed via live testing (real screenshots of a real full-screen
        session, not just offscreen flag inspection): the first time Qt has
        to realize brand-new native tab content inside an *already*
        full-screen window, macOS briefly slides the window out to reveal
        another Space (issue #25). Front-loading that realization while
        still windowed avoids the trigger for the common case of a panel
        that starts with zero tabs."""
        if self.tabs.count() == 0:
            self.add_new_tab()

    def add_new_tab(self):
        """Open a blank SQL query tab."""
        tab = SqlTab()
        # Reparent into the real tab widget FIRST, before any other setup.
        # SqlTab() itself is a fairly heavy construction (dozens of child
        # widgets), and until it's added here it's a parentless — hence
        # top-level — widget. Empirically (screenshots of a real full-screen
        # session, not just offscreen flag-checking), macOS briefly slides
        # the fullscreen window out to reveal the desktop the moment the
        # event loop gets a chance to notice that parentless top-level
        # widget, even though it's never actually shown — the same class of
        # bug fixed for the autocomplete popup under issue #15, just for the
        # tab itself this time (issue #25). Keeping this gap as short as
        # possible (one line, no signal connects or other work first)
        # closes the window for it to happen.
        count = self.tabs.count() + 1
        idx = self.tabs.addTab(tab, f"Tab {count}")
        tab.run_btn.clicked.connect(lambda: self._run_query_in_tab(tab))
        tab.verify_btn.clicked.connect(lambda: self._open_verify_dialog(tab))
        tab.begin_tx_btn.clicked.connect(lambda: self._run_transaction_control(tab, "BEGIN"))
        tab.commit_tx_btn.clicked.connect(lambda: self._run_transaction_control(tab, "COMMIT"))
        tab.rollback_tx_btn.clicked.connect(lambda: self._run_transaction_control(tab, "ROLLBACK"))
        # Wire inline-edit commit: execute SQL with our db_service
        tab.commit_sql.connect(lambda sqls, t=tab: self._execute_commit_sql(sqls, t))
        # Push current schema so autocomplete works immediately
        tab.set_schema(self.all_tables, self._column_cache)
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
        tab.update_status(len(df), elapsed, truncated=df.attrs.get("truncated", False))
        # Load FK map so right-click "Go to …" works in the result grid
        self._wire_result_fk(tab, table_name)
        self.query_history.add_query(query, self.config["name"], len(df), elapsed)
        if self._sidebar_stack.currentIndex() == 2:
            self._reload_history_list(self._history_search.text())
        self.health_changed.emit('idle')
        if self.tabs.currentWidget() is not tab:
            tab_name = self.tabs.tabText(self.tabs.indexOf(tab))
            self._show_query_toast(tab_name, len(df), elapsed)
        self._finalize_query_connection(tab)

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
        if self._sidebar_stack.currentIndex() == 2:
            self._reload_history_list(self._history_search.text())

        if len(select_results) == 1:
            # single result — display inline as normal
            lbl, df = select_results[0]
            tab.load_dataframe(df, self._extract_table_name(query))
            tab.update_status(len(df), elapsed, truncated=df.attrs.get("truncated", False))
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
        self._finalize_query_connection(tab)


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
        self._finalize_query_connection(tab)

    def _on_query_cancelled(self, tab):
        """Receives worker `cancelled` signal via bridge — guaranteed main thread."""
        tab._query_running = False
        self._restore_run_btn(tab)
        tab.cancel_btn.setEnabled(False)
        if hasattr(tab, '_query_thread'):
            tab._query_thread.quit()
        tab.show_cancelled()
        self._finalize_query_connection(tab)

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

    def _run_query_in_tab(self, tab, override_query: str = None):
        """Execute the SQL in `tab` on a background thread; Cancel actually
        stops it. *override_query* (set by the Begin/Commit/Rollback
        buttons) bypasses the editor content and the format/param-prompt
        steps below, but still goes through the same guard, connection
        handling, and worker dispatch as typed SQL — a single code path so
        transaction control can't accidentally skip the write guard."""
        query = override_query if override_query is not None else tab.get_query().strip()
        if not query:
            return
        if self._connecting:
            QMessageBox.information(
                self, "Connecting…",
                "Still connecting to the database — try again in a moment.")
            return

        if override_query is None:
            # Format on run if user has the toggle active
            if getattr(tab, '_format_on_run', False):
                import sqlparse
                try:
                    query = sqlparse.format(query, reindent=True, keyword_case="upper")
                    tab.editor.setPlainText(query)
                except Exception:
                    pass  # query too large for sqlparse — run as-is

            # ── Parameterised queries: prompt for {{var}} values ───────────────
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

        # ── Dedicated connection for this query (prevents shared-connection
        # races) — reused across runs if this tab already has an open
        # transaction, so BEGIN...COMMIT can span multiple Run clicks.
        existing_tx_db = getattr(tab, '_tx_db_service', None)
        if existing_tx_db is not None:
            query_db = existing_tx_db
        else:
            query_db = DbService()
            try:
                query_db.connect(self.config)
            except Exception as ex:
                tab._query_running = False
                self._restore_run_btn(tab)
                tab.cancel_btn.setEnabled(False)
                tab.show_error(str(ex))
                return
        tab._active_query_db = query_db

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

    def _run_transaction_control(self, tab, stmt: str):
        """Handler for the tab's Begin/Commit/Rollback buttons — runs
        *stmt* through the exact same path as typed SQL (see
        _run_query_in_tab's override_query)."""
        if getattr(tab, '_query_running', False):
            return
        self._run_query_in_tab(tab, override_query=stmt)

    def _finalize_query_connection(self, tab):
        """Called at the end of every query-completion handler. Decides
        whether this tab's connection survives past the run that just
        finished (it does iff that run left a transaction open) and
        refreshes the tab's transaction indicator either way."""
        query_db = getattr(tab, '_active_query_db', None)
        if query_db is None:
            return
        tab._tx_db_service = query_db if query_db.in_transaction else None
        self._refresh_transaction_indicator(tab)

    def _refresh_transaction_indicator(self, tab):
        active = getattr(tab, '_tx_db_service', None) is not None
        tab.set_transaction_state(active)
        idx = self.tabs.indexOf(tab)
        if idx < 0:
            return
        title = self.tabs.tabText(idx)
        has_marker = title.endswith(" ⏳")
        if active and not has_marker:
            self.tabs.setTabText(idx, f"{title} ⏳")
        elif not active and has_marker:
            self.tabs.setTabText(idx, title[:-2])

    def has_open_transactions(self) -> bool:
        """True if any tab in this connection has an open manual
        transaction — used to warn before closing the connection/tab."""
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if getattr(w, '_tx_db_service', None) is not None:
                return True
        return False

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
        w = self.tabs.widget(index)
        tx_db = getattr(w, '_tx_db_service', None)
        if tx_db is not None:
            reply = QMessageBox.question(
                self, "Open Transaction",
                "This tab has an open transaction — closing it will roll "
                "back any uncommitted changes.\n\nClose anyway?",
                QMessageBox.Yes | QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
            try:
                tx_db.disconnect()
            except Exception as ex:
                logger.debug(f"Failed to close tab transaction connection: {ex}")
            w._tx_db_service = None
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
        """Open (or focus) *table_name*'s tab and switch it to the Structure
        sub-tab (issue #27) — no more separate popup window to lose context in."""
        self.open_table_view(table_name)
        w = self.tabs.currentWidget()
        if isinstance(w, TableViewWidget):
            w.show_structure_tab()



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

    def export_database(self):
        """Export chosen tables' structure and/or data to a single SQL dump
        (issue #39: whole-database export, independent of any query tab).
        Structure-only/data-only/both and which tables to include are all
        chosen up front via ExportScopeDialog — previously this always did
        every table's structure + data with no way to narrow either."""
        from PySide6.QtWidgets import QFileDialog, QProgressDialog

        all_tables = self.db_service.get_tables()
        if not all_tables:
            QMessageBox.information(self, "Export Database", "No tables to export.")
            return

        scope = ExportScopeDialog(all_tables, parent=self)
        if not scope.exec():
            return
        tables = scope.selected_tables()
        content = scope.content_mode()
        if not tables:
            QMessageBox.information(self, "Export Database", "No tables selected.")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export Database", "database.sql", "SQL Dump (*.sql)"
        )
        if not file_path:
            return

        progress = QProgressDialog(
            "Exporting tables…", "Cancel", 0, len(tables), self)
        progress.setWindowTitle("Export Database")
        progress.setMinimumDuration(0)

        failures = []
        try:
            with open(file_path, "w") as fh:
                for i, table in enumerate(tables):
                    if progress.wasCanceled():
                        break
                    progress.setLabelText(f"Exporting {table}…")
                    progress.setValue(i)
                    try:
                        self._write_table_export(fh, table, content)
                    except Exception as ex:
                        failures.append(f"{table}: {ex}")
                progress.setValue(len(tables))
        except OSError as ex:
            QMessageBox.critical(self, "Export Error", f"Could not write file:\n{ex}")
            return

        summary = f"Exported {len(tables) - len(failures)}/{len(tables)} tables to:\n{file_path}"
        if failures:
            summary += "\n\nFailed:\n" + "\n".join(failures)
            QMessageBox.warning(self, "Export Database", summary)
        else:
            QMessageBox.information(self, "Export Database", summary)

    def _write_table_export(self, fh, table: str, content: str):
        """Write *table*'s structure and/or data to the already-open file
        handle *fh*, per content mode ('structure' | 'data' | 'both')."""
        if content in ("structure", "both"):
            fh.write(f"-- Table: {table}\n")
            fh.write(self.db_service.get_table_ddl(table) + "\n\n")
        if content in ("data", "both"):
            df = self.db_service.execute_query(f"SELECT * FROM {table}")  # nosec B608
            if not df.empty:
                fh.write(_to_sql_inserts(df, table) + "\n\n")

    def _export_table(self, table_name: str):
        """Export a single table without needing an open query tab (issue
        #39). 'Data only' keeps the existing multi-format path (CSV/JSON/
        Excel/SQL inserts via export_dataframe) — structure doesn't fit
        those formats, so 'Structure only'/'Structure + Data' write a
        single .sql file instead, matching export_database()'s shape."""
        scope = ExportScopeDialog([table_name], parent=self)
        if not scope.exec():
            return
        content = scope.content_mode()

        if content == "data":
            try:
                df = self.db_service.execute_query(f"SELECT * FROM {table_name}")  # nosec B608
            except Exception as ex:
                QMessageBox.critical(self, "Export Error", f"Could not read table:\n{ex}")
                return
            export_dataframe(self, df, f"{table_name}.csv", table_name)
            return

        from PySide6.QtWidgets import QFileDialog
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export Table", f"{table_name}.sql", "SQL Dump (*.sql)"
        )
        if not file_path:
            return
        try:
            with open(file_path, "w", encoding="utf-8") as fh:
                self._write_table_export(fh, table_name, content)
            QMessageBox.information(self, "Export Table", f"Exported to:\n{file_path}")
        except Exception as ex:
            QMessageBox.critical(self, "Export Error", str(ex))

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
            f"INSERT INTO `{table_name}` ({cols_sql}) VALUES ({placeholders})"  # nosec B608
            if db_type == "mysql"
            else f'INSERT INTO "{table_name}" ({cols_sql}) VALUES ({placeholders})'  # nosec B608
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
                w._warn_and_discard_changes()
                w.current_page = 1
                w.load_table_data()
                break

    # ─── Table context-menu operations (issue #142) ────────────────────────────

    def _qualified_name(self, table_name: str, quote: bool = False) -> str:
        db_type = self.db_service.db_type
        if not quote:
            return table_name
        return f"`{table_name}`" if db_type == "mysql" else f'"{table_name}"'

    def _copy_table_name(self, table_name: str):
        QApplication.clipboard().setText(table_name)

    def _copy_table_full_name(self, table_name: str):
        database = self.config.get("database", "")
        full = f"{database}.{table_name}" if database else table_name
        QApplication.clipboard().setText(full)

    def _toggle_pin_table(self, table_name: str):
        table_organization.toggle_pinned(
            self.config.get("id", ""), self.config.get("database", ""), table_name)
        self._render_active_category()

    def _toggle_favorite_table(self, table_name: str):
        table_organization.toggle_favorite(
            self.config.get("id", ""), self.config.get("database", ""), table_name)
        self._render_active_category()

    def _copy_create_table_script(self, table_name: str):
        try:
            ddl = self.db_service.get_table_ddl(table_name)
        except Exception as ex:
            QMessageBox.critical(self, "Copy Script Error", f"Could not build CREATE TABLE:\n{ex}")
            return
        QApplication.clipboard().setText(ddl)

    def _copy_insert_script(self, table_name: str):
        try:
            df = self.db_service.execute_query(f"SELECT * FROM {table_name}")  # nosec B608
        except Exception as ex:
            QMessageBox.critical(self, "Copy Script Error", f"Could not read table:\n{ex}")
            return
        if df.empty:
            QMessageBox.information(self, "Copy Script", f"'{table_name}' has no rows to script.")
            return
        QApplication.clipboard().setText(_to_sql_inserts(df, table_name))

    def _export_table_data_only(self, table_name: str):
        """Export just the rows (context menu's "Export Table Data") without
        the ExportScopeDialog's structure/data/both prompt — same CSV/JSON/
        Excel/SQL-inserts picker _export_table() already uses for "data"."""
        try:
            df = self.db_service.execute_query(f"SELECT * FROM {table_name}")  # nosec B608
        except Exception as ex:
            QMessageBox.critical(self, "Export Error", f"Could not read table:\n{ex}")
            return
        export_dataframe(self, df, f"{table_name}.csv", table_name)

    def _export_table_as_sql(self, table_name: str):
        """Export structure + data as a single .sql file (context menu's
        "Export Table as SQL") — skips ExportScopeDialog since both are
        implied, reusing _write_table_export()'s 'both' path."""
        from PySide6.QtWidgets import QFileDialog
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export Table as SQL", f"{table_name}.sql", "SQL Dump (*.sql)"
        )
        if not file_path:
            return
        try:
            with open(file_path, "w", encoding="utf-8") as fh:
                self._write_table_export(fh, table_name, "both")
            QMessageBox.information(self, "Export Table as SQL", f"Exported to:\n{file_path}")
        except Exception as ex:
            QMessageBox.critical(self, "Export Error", str(ex))

    def _export_table_with_column_selection(self, table_name: str):
        try:
            columns = [str(c.get("Field", c.get("name", ""))) for c in self.db_service.get_columns(table_name)]
        except Exception as ex:
            QMessageBox.critical(self, "Export Error", f"Could not load columns:\n{ex}")
            return
        if not columns:
            QMessageBox.information(self, "Export Table", f"'{table_name}' has no columns.")
            return

        dlg = ColumnSelectionDialog(columns, parent=self)
        if not dlg.exec():
            return
        selected = dlg.selected_columns()
        if not selected:
            QMessageBox.information(self, "Export Table", "No columns selected.")
            return

        db_type = self.db_service.db_type
        cols_sql = ", ".join(
            (f"`{c}`" if db_type == "mysql" else f'"{c}"') for c in selected
        )
        try:
            df = self.db_service.execute_query(
                f"SELECT {cols_sql} FROM {table_name}")  # nosec B608
        except Exception as ex:
            QMessageBox.critical(self, "Export Error", f"Could not read table:\n{ex}")
            return
        export_dataframe(self, df, f"{table_name}.csv", table_name)

    def _new_table(self):
        """StructureEditorDialog already supports a "New Table" mode
        (table_name=None) — same dialog show_alter_table_editor() uses for
        Alter, just without existing columns to seed it."""
        dialog = StructureEditorDialog(db_type=self.db_service.db_type, parent=self)
        if not dialog.exec():
            return
        try:
            sql = dialog.get_sql()
            if not sql.strip() or sql.strip().startswith("--"):
                return
            if not self._guard_write(sql):
                return
            for stmt in query_classifier.split_statements(sql):
                self.db_service.execute_update(stmt)
            QMessageBox.information(self, "Success", "Table created successfully.")
            self.load_schema()
        except Exception as ex:
            QMessageBox.critical(self, "Error", str(ex))

    def _new_view(self):
        """No dedicated CREATE VIEW UI exists — open a fresh SQL tab with a
        template so the user writes the SELECT and runs it themselves,
        consistent with how the app already treats view creation as a plain
        SQL statement rather than a form."""
        self.add_new_tab()
        tab = self.tabs.currentWidget()
        if hasattr(tab, "editor"):
            tab.editor.setPlainText("CREATE VIEW view_name AS\nSELECT *\nFROM table_name;\n")

    def _clone_table(self, table_name: str):
        new_name, ok = QInputDialog.getText(
            self, "Clone Table", "New table name:", text=f"{table_name}_copy")
        if not ok or not new_name.strip():
            return
        new_name = new_name.strip()
        if not self._VALID_DB_NAME.match(new_name):
            QMessageBox.warning(self, "Invalid Name", "Unrecognized table name.")
            return

        quoted_new = self._qualified_name(new_name, quote=True)
        sql = f"CREATE TABLE {quoted_new} AS SELECT * FROM {table_name}"  # nosec B608
        if not self._guard_write(sql):
            return
        reply = QMessageBox.question(
            self, "Clone Table",
            f"Execute the following SQL?\n\n{sql}",
            QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        try:
            self.db_service.execute_update(sql)
        except Exception as ex:
            QMessageBox.critical(self, "Clone Table Failed", str(ex))
            return
        QMessageBox.information(self, "Success", f"'{table_name}' cloned to '{new_name}'.")
        self.load_schema()

    def _truncate_table(self, table_name: str):
        db_type = self.db_service.db_type
        quoted = self._qualified_name(table_name, quote=True)
        # SQLite has no TRUNCATE statement — DELETE FROM is the equivalent.
        sql = f"DELETE FROM {quoted}" if db_type == "sqlite" else f"TRUNCATE TABLE {quoted}"  # nosec B608
        if not self._guard_write(sql):
            return
        reply = QMessageBox.question(
            self, "Truncate Table",
            f"This permanently deletes ALL rows in '{table_name}'. This cannot be undone.\n\n"
            f"Execute the following SQL?\n\n{sql}",
            QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        try:
            self.db_service.execute_update(sql)
        except Exception as ex:
            QMessageBox.critical(self, "Truncate Failed", str(ex))
            return
        QMessageBox.information(self, "Success", f"'{table_name}' truncated.")
        self._refresh_open_table_tab(table_name)

    def _delete_table(self, table_name: str):
        kind = "VIEW" if self._active_category == "views" else "TABLE"
        quoted = self._qualified_name(table_name, quote=True)
        sql = f"DROP {kind} {quoted}"
        if not self._guard_write(sql):
            return
        reply = QMessageBox.question(
            self, f"Delete {kind.title()}",
            f"This permanently drops '{table_name}' and all its data. This cannot be undone.\n\n"
            f"Execute the following SQL?\n\n{sql}",
            QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        try:
            self.db_service.execute_update(sql)
        except Exception as ex:
            QMessageBox.critical(self, "Delete Failed", str(ex))
            return
        QMessageBox.information(self, "Success", f"'{table_name}' deleted.")
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, TableViewWidget) and w.table_name == table_name:
                self.tabs.removeTab(i)
                break
        self.load_schema()

    def _refresh_open_table_tab(self, table_name: str):
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, TableViewWidget) and w.table_name == table_name:
                w.current_page = 1
                w.load_table_data()
                break

    # ─── Quick search ─────────────────────────────────────────────────────────

    def _gather_quick_search_items(self):
        """Build the full (item_type, display_text, payload) list for the
        command palette: schema items plus columns, recent query history,
        and SQL snippets. Rebuilt on every open since history/snippets
        change independently of schema reloads."""
        items = [(item_type, name, None) for item_type, name in self.all_schema_items]

        for table, cols in self._column_cache.items():
            for col in cols:
                items.append(("column", f"{table}.{col}", col))

        for entry in self.query_history.get_recent_queries(limit=100):
            query = entry.get("query", "").strip()
            if query:
                items.append(("history", query.replace("\n", " ")[:80], query))

        for trigger, data in SnippetManager().get_all().items():
            label = f"{trigger} — {data.get('name', trigger)}"
            items.append(("snippet", label, data.get("body", "")))

        return items

    def show_quick_search(self):
        items = self._gather_quick_search_items()
        if not items:
            QMessageBox.information(self, "No Items", "Nothing to search yet")
            return
        dialog = QuickSearchDialog(items, self)
        dialog.item_selected.connect(self._on_quick_search)
        dialog.exec()

    def _active_sql_tab(self) -> SqlTab:
        """Return the current tab if it's a SQL editor, else open a new one."""
        current = self.tabs.currentWidget()
        if isinstance(current, SqlTab):
            return current
        self.add_new_tab()
        return self.tabs.currentWidget()

    def _on_quick_search(self, item_type, display_text, payload):
        if item_type in ("table", "view"):
            self.open_table_view(display_text)
        elif item_type == "history":
            self._active_sql_tab().set_query(payload)
        else:  # function, column, snippet — insert at cursor
            self._active_sql_tab().insert_text_at_cursor(payload or display_text)

    # ─── Query history ────────────────────────────────────────────────────────

    def show_query_history(self):
        dialog = QueryHistoryDialog(self.query_history, self)
        if dialog.exec():
            query = dialog.get_selected_query()
            if query:
                self._active_sql_tab().set_query(query)

    # ─── Sidebar schema/history toggle ─────────────────────────────────────────

    def _switch_sidebar(self, index: int):
        self._sidebar_stack.setCurrentIndex(index)
        self._schema_btn.setChecked(index == 0)
        self._queries_btn.setChecked(index == 1)
        self._history_btn.setChecked(index == 2)
        self.table_search.setVisible(index == 0)
        if index == 1:
            self._reload_queries_list()
        elif index == 2:
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
        self._active_sql_tab().set_query(query)
        self._switch_sidebar(0)

    def _clear_history(self):
        self.query_history.queries.clear()
        self.query_history.save_history()
        self._history_list.clear()

    # ─── Sidebar saved queries (issue #130) ────────────────────────────────────

    def _build_query_row(self, entry: dict) -> QWidget:
        """One clickable row in the Queries panel — click loads the query
        into the active tab, the ⋮ button opens per-row actions. Reuses
        _ClickableRow so a click anywhere except the buttons fires (same
        pattern as the schema category rows)."""
        row = _ClickableRow()
        row.setAttribute(Qt.WA_StyledBackground, True)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(8, 4, 4, 4)
        row_layout.setSpacing(6)

        icon_lbl = QLabel("★" if entry.get("favorite") else "📄")
        icon_lbl.setFixedWidth(18)
        icon_lbl.setStyleSheet(
            "color: #FFCC00; font-size: 13px; background: transparent;" if entry.get("favorite")
            else "color: #8e8e93; font-size: 12px; background: transparent;")
        row_layout.addWidget(icon_lbl)

        text_col = QVBoxLayout()
        text_col.setSpacing(0)
        name_lbl = QLabel(entry["name"])
        name_lbl.setStyleSheet("color: #e5e5ea; font-size: 12px; font-weight: 600; background: transparent;")
        preview = entry.get("query", "").replace("\n", " ").strip()[:48]
        preview_lbl = QLabel(preview)
        preview_lbl.setStyleSheet("color: #0A84FF; font-size: 11px; background: transparent;")
        text_col.addWidget(name_lbl)
        text_col.addWidget(preview_lbl)
        row_layout.addLayout(text_col)
        row_layout.addStretch()

        menu_btn = QPushButton("⋮")
        menu_btn.setFlat(True)
        menu_btn.setFixedWidth(22)
        menu_btn.setStyleSheet("""
            QPushButton { color: #8e8e93; border: none; background: transparent; font-size: 14px; }
            QPushButton:hover { color: #e5e5ea; }
        """)
        menu_btn.clicked.connect(lambda checked=False, e=entry, b=menu_btn: self._show_saved_query_menu(e, b))
        row_layout.addWidget(menu_btn)

        row.clicked.connect(lambda e=entry: self._use_saved_query(e))
        return row

    def _add_query_section_header(self, text: str, count: int):
        item = QListWidgetItem()
        item.setFlags(Qt.ItemIsEnabled)
        header = QWidget()
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(6, 8, 6, 2)
        label = QLabel(text)
        label.setStyleSheet("color: #8e8e93; font-size: 11px; font-weight: 600; background: transparent;")
        h_layout.addWidget(label)
        h_layout.addStretch()
        count_lbl = QLabel(str(count))
        count_lbl.setStyleSheet("color: #636366; font-size: 11px; background: transparent;")
        h_layout.addWidget(count_lbl)
        item.setSizeHint(header.sizeHint())
        self._queries_list.addItem(item)
        self._queries_list.setItemWidget(item, header)

    def _add_query_rows(self, entries: list):
        for entry in entries:
            item = QListWidgetItem()
            item.setFlags(Qt.ItemIsEnabled)
            row = self._build_query_row(entry)
            item.setSizeHint(row.sizeHint())
            self._queries_list.addItem(item)
            self._queries_list.setItemWidget(item, row)

    def _reload_queries_list(self, filter_text: str = ''):
        self._queries_list.clear()
        ft = filter_text.strip()
        entries = self.saved_queries.search(ft) if ft else list(self.saved_queries.queries)
        favorites = [q for q in entries if q.get("favorite")]
        saved = [q for q in entries if not q.get("favorite")]

        if favorites:
            self._add_query_section_header("★ Favorite Queries", len(favorites))
            self._add_query_rows(favorites)
        if saved:
            self._add_query_section_header("☆ Saved Queries", len(saved))
            self._add_query_rows(saved)
        if not favorites and not saved:
            empty_item = QListWidgetItem("No saved queries yet — save one from the SQL editor.")
            empty_item.setFlags(Qt.NoItemFlags)
            self._queries_list.addItem(empty_item)

    def _filter_queries_list(self, text: str):
        self._reload_queries_list(filter_text=text)

    def _use_saved_query(self, entry: dict):
        query = entry.get("query", "")
        if not query:
            return
        self._active_sql_tab().set_query(query)
        self._switch_sidebar(0)

    def _show_saved_query_menu(self, entry: dict, anchor: QPushButton):
        menu = QMenu(self)
        run_action = menu.addAction("Load into Tab")
        fav_action = menu.addAction("Remove from Favorites" if entry.get("favorite") else "Add to Favorites")
        rename_action = menu.addAction("Rename…")
        delete_action = menu.addAction("Delete")
        action = menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))
        if action == run_action:
            self._use_saved_query(entry)
        elif action == fav_action:
            self.saved_queries.toggle_favorite(entry["id"])
            self._reload_queries_list(self._queries_search.text())
        elif action == rename_action:
            self._rename_saved_query(entry)
        elif action == delete_action:
            self._delete_saved_query(entry)

    def _rename_saved_query(self, entry: dict):
        name, ok = QInputDialog.getText(self, "Rename Query", "Name:", text=entry["name"])
        if ok and name.strip():
            self.saved_queries.update(entry["id"], name=name.strip())
            self._reload_queries_list(self._queries_search.text())

    def _delete_saved_query(self, entry: dict):
        reply = QMessageBox.question(
            self, "Delete Query", f"Delete '{entry['name']}'?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.saved_queries.delete(entry["id"])
            self._reload_queries_list(self._queries_search.text())

    def _save_current_query(self):
        tab = self.tabs.currentWidget()
        query = tab.editor.toPlainText().strip() if isinstance(tab, SqlTab) else ""
        if not query:
            QMessageBox.information(self, "Nothing to Save", "Write a query in the editor first.")
            return
        name, ok = QInputDialog.getText(self, "Save Query", "Name:")
        if ok and name.strip():
            self.saved_queries.add(name.strip(), query)
            self._switch_sidebar(1)

    def _open_query_library(self):
        dialog = QueryLibraryDialog(self.saved_queries, self)
        if dialog.exec():
            query = dialog.get_selected_query()
            if query:
                self._active_sql_tab().set_query(query)
                self._switch_sidebar(0)
        self._reload_queries_list(self._queries_search.text())

    # ─── Table filter ─────────────────────────────────────────────────────────

    def filter_tables(self, search_text: str):
        """Fuzzy-match the active category's item names (tables, views, or
        functions — whichever sidebar category filter is selected);
        bold-highlight matched characters."""
        from PySide6.QtGui import QBrush, QColor

        items_map = self._active_category_items()
        raw = search_text.strip()
        query = raw.lower()

        # ── Reset all items ───────────────────────────────────────────────────
        for name, item in items_map.items():
            item.setHidden(False)
            item.setText(0, name)   # clear previous highlight
            item.setForeground(0, QBrush(QColor("#e5e5ea")))

        if not query:
            return

        # ── Score every item ──────────────────────────────────────────────────
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
        for name, item in items_map.items():
            s = _score(name)
            item.setHidden(s < 0)
            if s >= 0:
                scored.append((s, name, item))

        # ── Highlight matched characters in green ─────────────────────────────
        # QTreeWidget doesn't support rich text, so we colour the whole item
        # for prefix/exact matches and use normal colour for fuzzy hits.
        for s, name, item in scored:
            if s >= 800:
                # Direct substring match — tint green
                item.setForeground(0, QBrush(QColor("#89d185")))
            elif s == 700:
                # Fuzzy match — dim tint to distinguish from prefix matches
                item.setForeground(0, QBrush(QColor("#6cba68")))

        # ── Re-sort visible items so best matches appear first ────────────────
        # QTreeWidget doesn't have a built-in sort by custom score. The tree
        # is a flat list of the active category now (no folder wrapper), so
        # reorder the root's direct children.
        root = self.schema_tree.invisibleRootItem()
        children = []
        for i in range(root.childCount()):
            child = root.child(i)
            if not child.isHidden():
                children.append((_score(child.text(0)), child.text(0), child))
        children.sort(key=lambda x: (-x[0], x[1]))
        for rank, (_, _, child) in enumerate(children):
            root.removeChild(child)
            root.insertChild(rank, child)

    # ─── Refresh ──────────────────────────────────────────────────────────────

    def refresh_current_view(self):
        w = self.tabs.currentWidget()
        if isinstance(w, TableViewWidget):
            w._warn_and_discard_changes()
            w.current_page = 1
            w.load_table_data()
        elif isinstance(w, SqlTab) and w.get_query().strip():
            self._run_query_in_tab(w)
        else:
            self.load_schema(notify=True)

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
        if self._connecting:
            QMessageBox.information(
                self, "Connecting…",
                "Already connecting in the background — please wait.")
            return
        reconnected_ok = False
        try:
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
            QMessageBox.critical(self, "Reconnect Failed", str(ex))
            self.health_changed.emit('disconnected')

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
        # Close any per-tab transaction connections too — closing rolls
        # back whatever hadn't been committed, matching normal database
        # semantics for a dropped connection. main.py warns the user before
        # calling disconnect() if a transaction is open; this is the cleanup
        # once that's confirmed (or for a tab that never got a warning, e.g.
        # app quit).
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            tx_db = getattr(w, '_tx_db_service', None)
            if tx_db is not None:
                try:
                    tx_db.disconnect()
                except Exception as ex:
                    logger.debug(f"Failed to close tab transaction connection: {ex}")
                w._tx_db_service = None
        try:
            self.db_service.disconnect()
        except Exception as ex:
            logger.debug(f"Failed to disconnect cleanly: {ex}")

    # ─── Query-done toast notification ───────────────────────────────────────

    def _show_query_toast(self, tab_name: str, row_count: int, elapsed: float):
        """Slide-in notification from the right when a background query finishes."""
        from utils.toast import show_toast
        show_toast(
            self,
            f"<b>{tab_name}</b> — {row_count} row{'s' if row_count != 1 else ''} in {elapsed:.2f}s",
            icon="✓", kind="success",
        )
