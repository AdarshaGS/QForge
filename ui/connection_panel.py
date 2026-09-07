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
import queue
import gzip
import uuid

from PySide6.QtCore import Qt, Signal, QThread, QObject
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTreeWidget, QTreeWidgetItem, QTabWidget, QTabBar,
    QLineEdit, QMessageBox, QInputDialog,
    QMenu, QProgressDialog, QPushButton, QLabel,
    QListWidget, QListWidgetItem, QStackedWidget,
    QApplication, QProgressBar,
)
from PySide6.QtGui import QShortcut, QKeySequence, QCursor, QFont

from services.db_service import DbService
from services import query_cost
from services import mock_data_generator as mock_gen
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
from ui.data_compare_dialog import DataCompareDialog
from ui.mock_data_dialog import MockDataDialog
from ui.dependency_dialog import ImpactAnalysisDialog, DatabaseImpactDialog, impact_warning_text
from ui import query_guard_dialog
from ui.upgrade_dialog import require_pro, require_under_limit
from services.entitlements import Feature, Limit, entitlements
from utils.logger import get_logger
from utils import environment
from utils import schema_cache
from utils import perf_metrics
from utils.df_export import (
    export_dataframe, _to_sql_inserts, drop_table_statement,
    SqlInsertStreamWriter, CsvRowStreamWriter, XmlRowStreamWriter,
    strip_auto_increment_value, strip_generated_column_clauses,
    _quote_identifier,
)
from services import query_classifier
from services import table_organization
from services import dependency_analyzer

logger = get_logger()

# CSV imports at or above this row count are treated as a "mass write" for
# the dangerous-query guard on Staging/Production, even though a plain
# INSERT alone isn't otherwise flagged (ai/load-context.md: "mass writes,
# where a reliable row-count estimate is available" — a CSV import's
# DataFrame length is exactly that).
MASS_WRITE_ROW_THRESHOLD = 5000

# Issue #115: guardrails on CSV/TSV import — a crafted or accidentally huge
# file (zip bomb-adjacent: a small file that decompresses/parses into an
# enormous row count isn't possible for plain-text CSV the way it is for
# .xlsx, but an ordinary multi-GB file is still an easy way to hang the app
# or exhaust memory) fails with a clear message instead of an unbounded read.
IMPORT_MAX_FILE_SIZE_BYTES = 500 * 1024 * 1024  # 500 MiB
IMPORT_MAX_ROWS = 2_000_000


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
    cost_ready = Signal(object)          # CostEstimate — best-effort, may never fire
    profile_ready = Signal(object)       # QueryProfile — only when auto_profile=True, best-effort

    def __init__(self, db_service, query: str, cancel_flag, multi: bool = False,
                 auto_profile: bool = False):
        super().__init__()
        self._db    = db_service
        self._q     = query
        self._flag  = cancel_flag
        self._multi = multi
        self._auto_profile = auto_profile

    def _maybe_emit_cost(self, stmt: str):
        """Best-effort pre-run cost estimate for the status-bar badge —
        plan-only (EXPLAIN never executes the statement), so this runs
        before the real query on the same connection with no data-scan
        cost of its own. Silently skipped for writes/unsupported
        statements; any EXPLAIN failure is swallowed the same way."""
        try:
            from services import query_classifier
            if query_classifier.classify(stmt).is_write:
                return
            self.cost_ready.emit(query_cost.estimate_cost(self._db, stmt))
        except Exception:
            pass

    def _maybe_emit_profile(self, stmt: str):
        """Opt-in (auto_profile=True — the SQL editor's "Auto-profile"
        toggle) post-run profile — genuinely re-executes *stmt* for real
        via EXPLAIN ANALYZE, on top of the real run that already happened
        just above. Only reached for a single, non-write statement — the
        same scope _maybe_emit_cost uses — so this never doubles a write's
        side effects or a multi-statement script's total execution count.
        Emitted after `done`, so the result grid is never held up waiting
        for a second execution the user may not even be watching for."""
        try:
            from services import query_classifier
            if query_classifier.classify(stmt).is_write:
                return
            profile = query_cost.build_profile(self._db, stmt)
            if not profile.error and profile.supported:
                self.profile_ready.emit(profile)
        except Exception:
            pass

    def run(self):
        import sqlparse as _sp
        try:
            # Multi-statement: detect 2+ non-empty statements.
            # sqlparse.split raises SQLParseError on very large queries (>10k tokens);
            # fall back to treating the whole text as a single statement in that case.
            try:
                stmts = [s.strip() for s in _sp.split(self._q) if s.strip()]
            except Exception:
                stmts = [self._q.strip()]
            is_multi = self._multi or len(stmts) > 1
            if not is_multi and stmts:
                self._maybe_emit_cost(stmts[0])
            # t0 starts here, after the cost estimate, so "Query time" keeps
            # reflecting only the real query — not the EXPLAIN round-trip
            # that precedes it.
            t0 = time.time()
            if is_multi:
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
                    if self._auto_profile and stmts and not self._flag.is_set():
                        self._maybe_emit_profile(stmts[0])
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


_GZIP_COMPRESSLEVEL = 1
# Exported SQL/Dot text is highly repetitive (INSERT boilerplate, repeated
# identifiers) — level 1 captures nearly all the achievable compression a
# plain gzip -9 would (measured <1% size difference) for a real speed win,
# so there's no real tradeoff to justify Python's slower default of 9 here.


def _open_export_file(path: str, gzip_output: bool):
    if gzip_output:
        return gzip.open(path, "wt", encoding="utf-8", compresslevel=_GZIP_COMPRESSLEVEL)
    return open(path, "wt", encoding="utf-8")


_UNSAFE_ZIP_CHARS = re.compile(r"[\\/\x00]")


def _safe_zip_entry_name(table: str, ext: str) -> str:
    """Sanitize a table name before using it as a zip member filename
    (issue #163). zipfile.ZipFile.open() does not sanitize path
    separators or ".." in the name it's given — table is a server-returned
    identifier, so a malicious/compromised DB server naming a table e.g.
    "../../../Library/LaunchAgents/evil" could otherwise produce a zip
    entry that writes outside the target directory ("Zip Slip") when
    extracted by a tool that doesn't sanitize archive paths itself."""
    safe = _UNSAFE_ZIP_CHARS.sub("_", table).strip(".") or "table"
    return f"{safe}.{ext}"


class _ExportWorker(QObject):
    """Runs export_database()/_export_table()'s write loop on a QThread
    (issue #158), streaming each table's rows via DbService.stream_table_rows()
    instead of loading the whole table into memory first. A producer thread
    walks *table_opts* and pushes structure/drop/row-chunk items onto a
    bounded queue; run() (on the QThread) drains that queue and writes to
    the output — so table N+1's fetch overlaps table N's disk/gzip flush
    rather than the two running strictly sequentially. Receives a
    *dedicated* DbService connection, same reasoning as _QueryWorker.

    *export_format* selects the writer (issue #159): 'sql' writes one flat
    (optionally gzip'd) .sql file via SqlInsertStreamWriter; 'csv'/'xml'
    write one zip archive with one member per table (ExportScopeDialog
    forces structure/drop off for these, so the same row-producer loop
    naturally only ever emits "rows" items for them); 'dot' skips the
    producer/queue machinery entirely — schema+FK metadata is small enough
    to fetch inline and write as a single Graphviz file."""

    progress  = Signal(str, int)   # (table, completed_count)
    finished  = Signal(list)       # failures: list[str]
    errored   = Signal(str)
    cancelled = Signal()

    def __init__(self, db_service, table_opts: dict, file_path: str,
                 export_format: str = "sql",
                 blob_as_hex: bool = True, batch_kib=None,
                 gzip_output: bool = False, use_bom: bool = False,
                 include_auto_increment: bool = True, strip_generated: bool = False,
                 cancel_flag: threading.Event = None):
        super().__init__()
        self._db = db_service
        self._table_opts = table_opts
        self._file_path = file_path
        self._format = export_format
        self._blob_as_hex = blob_as_hex
        self._batch_kib = batch_kib
        self._gzip = gzip_output
        self._bom = use_bom
        self._include_auto_increment = include_auto_increment
        self._strip_generated = strip_generated
        self._flag = cancel_flag

    def _produce_rows(self, q, done):
        """Producer thread body for 'sql'/'csv'/'xml': walks *table_opts* in
        order, pushing structure/drop/row-chunk items. For csv/xml,
        ExportScopeDialog already forces structure/drop to False, so this
        is shared unchanged between all three formats. Generated columns
        are always dropped from SQL content rows (issue #160) — they can't
        appear in an INSERT column list — but left alone for csv/xml, where
        their computed values are legitimate exportable data."""
        dialect = self._db.db_type
        try:
            for table, opts in self._table_opts.items():
                if self._flag.is_set():
                    break
                try:
                    if opts.get("drop"):
                        q.put(("drop", table, drop_table_statement(table, dialect)))
                    if opts.get("structure"):
                        ddl = self._db.get_table_ddl(table)
                        if not self._include_auto_increment:
                            ddl = strip_auto_increment_value(ddl)
                        if self._strip_generated:
                            ddl = strip_generated_column_clauses(ddl)
                        q.put(("structure", table, ddl))
                    if opts.get("content"):
                        generated = set(self._db.get_generated_columns(table)) if self._format == "sql" else set()
                        for columns, rows in self._db.stream_table_rows(table):
                            if self._flag.is_set():
                                break
                            if generated:
                                keep = [i for i, c in enumerate(columns) if c not in generated]
                                if len(keep) != len(columns):
                                    columns = [columns[i] for i in keep]
                                    rows = [tuple(r[i] for i in keep) for r in rows]
                            q.put(("rows", table, columns, rows))
                    q.put(("table_done", table, None))
                except Exception as ex:
                    q.put(("error", table, str(ex)))
        finally:
            q.put(done)

    def _disconnect(self):
        try:
            self._db.disconnect()
        except Exception:
            pass

    def _finish_or_cancel(self, failures):
        if self._flag.is_set():
            try:
                os.remove(self._file_path)
            except OSError:
                pass
            self.cancelled.emit()
        else:
            self.finished.emit(failures)

    def run(self):
        perf_metrics.task_started("export")
        _t0 = time.perf_counter()
        try:
            if self._format == "dot":
                self._run_dot()
            elif self._format in ("csv", "xml"):
                self._run_zip()
            else:
                self._run_sql()
        finally:
            perf_metrics.task_finished("export")
            perf_metrics.record("import_export", "export", (time.perf_counter() - _t0) * 1000)

    def _run_sql(self):
        q = queue.Queue(maxsize=4)
        done = object()
        producer = threading.Thread(target=self._produce_rows, args=(q, done), daemon=True)
        producer.start()

        failures = []
        completed = 0
        writer = None
        writer_table = None
        try:
            with _open_export_file(self._file_path, self._gzip) as fh:
                if self._bom:
                    fh.write("﻿")
                while True:
                    item = q.get()
                    if item is done:
                        break
                    if self._flag.is_set():
                        continue  # keep draining so the producer can't block forever
                    kind = item[0]
                    if kind == "error":
                        failures.append(f"{item[1]}: {item[2]}")
                        completed += 1
                        self.progress.emit(item[1], completed)
                    elif kind == "drop":
                        fh.write(item[2] + "\n")
                    elif kind == "structure":
                        fh.write(f"-- Table: {item[1]}\n")
                        fh.write(item[2] + "\n\n")
                    elif kind == "rows":
                        _, table, columns, rows = item
                        if writer is None or writer_table != table:
                            writer = SqlInsertStreamWriter(
                                fh, columns, table, dialect=self._db.db_type,
                                batch_kib=self._batch_kib, blob_as_hex=self._blob_as_hex)
                            writer_table = table
                        writer.write_rows(rows)
                    elif kind == "table_done":
                        if writer is not None and writer_table == item[1]:
                            writer.close()
                            fh.write("\n")
                            writer, writer_table = None, None
                        completed += 1
                        self.progress.emit(item[1], completed)
        except OSError as ex:
            producer.join(timeout=5)
            self._disconnect()
            self.errored.emit(str(ex))
            return

        producer.join(timeout=5)
        self._disconnect()
        self._finish_or_cancel(failures)

    def _run_zip(self):
        import zipfile
        import io as _io

        q = queue.Queue(maxsize=4)
        done = object()
        producer = threading.Thread(target=self._produce_rows, args=(q, done), daemon=True)
        producer.start()

        ext = "csv" if self._format == "csv" else "xml"
        failures = []
        completed = 0
        entry = None
        row_writer = None
        current_table = None
        try:
            with zipfile.ZipFile(self._file_path, "w", zipfile.ZIP_DEFLATED) as zf:
                while True:
                    item = q.get()
                    if item is done:
                        break
                    if self._flag.is_set():
                        continue  # keep draining so the producer can't block forever
                    kind = item[0]
                    if kind == "error":
                        failures.append(f"{item[1]}: {item[2]}")
                        completed += 1
                        self.progress.emit(item[1], completed)
                    elif kind == "rows":
                        _, table, columns, rows = item
                        if row_writer is None or current_table != table:
                            entry = _io.TextIOWrapper(
                                zf.open(_safe_zip_entry_name(table, ext), "w"), encoding="utf-8", newline="")
                            if self._bom:
                                entry.write("﻿")
                            row_writer = (
                                CsvRowStreamWriter(entry, columns, blob_as_hex=self._blob_as_hex)
                                if self._format == "csv"
                                else XmlRowStreamWriter(entry, columns, table, blob_as_hex=self._blob_as_hex)
                            )
                            current_table = table
                        row_writer.write_rows(rows)
                    elif kind == "table_done":
                        if row_writer is not None and current_table == item[1]:
                            row_writer.close()
                            entry.close()
                            entry, row_writer, current_table = None, None, None
                        completed += 1
                        self.progress.emit(item[1], completed)
        except OSError as ex:
            producer.join(timeout=5)
            self._disconnect()
            self.errored.emit(str(ex))
            return

        producer.join(timeout=5)
        self._disconnect()
        self._finish_or_cancel(failures)

    def _run_dot(self):
        from utils.dot_export import build_dot_graph
        tables = [t for t, opts in self._table_opts.items() if opts.get("structure")]
        try:
            dot_text = build_dot_graph(self._db, tables)
        except Exception as ex:
            self._disconnect()
            self.errored.emit(str(ex))
            return

        try:
            with _open_export_file(self._file_path, self._gzip) as fh:
                fh.write(dot_text + "\n")
        except OSError as ex:
            self._disconnect()
            self.errored.emit(str(ex))
            return

        self._disconnect()
        if not self._flag.is_set():
            self.progress.emit("schema", 1)
        self._finish_or_cancel([])


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
    _q_cost_ready = Signal(object, object)         # (tab, CostEstimate)
    _q_profile_ready = Signal(object, object)      # (tab, QueryProfile)
    # ── Public observability signals ─────────────────────────────────────
    # 'idle' / 'running' / 'disconnected' / 'connecting'
    health_changed = Signal(str)
    # brief human-readable message (e.g. "Reconnected to MySQL")
    reconnected    = Signal(str)
    # Bridge signals for background schema load
    _schema_done   = Signal(object)   # (schema_data dict)
    _schema_error  = Signal(str)      # error message
    _schema_fast   = Signal(list, dict, dict, dict)   # (tables, columns, column_details, foreign_keys) — arrives ahead of _schema_done
    # Bridge signal for the optimistic-open background connect (issue: lag on
    # previously-visited remote/SSH connections despite a warm schema cache)
    _bg_connect_done = Signal(str)    # error message, "" on success
    # Bridge signals for background export (_ExportWorker) — same reasoning
    # as _q_done etc.: only one export runs at a time per panel, so the
    # in-flight QProgressDialog/title/etc. are read from self._export_* by
    # the bound slots below rather than threaded through the signal args.
    _export_progress_sig = Signal(str, int)   # (table, completed_count)
    _export_finished_sig = Signal(list)       # failures: list[str]
    _export_errored_sig  = Signal(str)
    _export_cancelled_sig = Signal()
    # Generic op_id-keyed bridge for one-off background DB operations
    # (issue #237) — every call site below is a single request/response
    # against a *dedicated* connection (never self.db_service), so one
    # pair of signals dispatches to whichever on_done/on_error callback
    # _run_bg_db() registered for that op_id, instead of one bespoke
    # Signal per call site.
    _bg_op_done  = Signal(str, object)   # (op_id, result)
    _bg_op_error = Signal(str, str)      # (op_id, error_message)
    # Bridge signals for the CSV import batched-write loop (issue #237) —
    # progress/cancellation cross the thread boundary via signals since the
    # QProgressDialog itself must only ever be touched from the main thread.
    _csv_import_progress    = Signal(int)        # rows completed so far
    _csv_import_write_done  = Signal(int, int)   # (inserted, errors)
    _csv_import_write_error = Signal(str)
    # Bridge signal for _switch_database()'s non-MySQL (real reconnect)
    # path (issue #237) — same self._connecting guard as
    # _connect_in_background(), since this touches self.db_service itself
    # rather than a dedicated connection (switching *is* changing what
    # self.db_service points to).
    _db_switch_done = Signal(str, str, float)   # (new_db, error, switch_t0)

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
        self._available_schemas: list[str] = []

        # Quick Search recency signal (issue #242): monotonic counter per
        # table/view name, bumped on every open_table_view() call. In-memory
        # only (resets each session, like the rest of this panel's state) —
        # capped so it can't grow unbounded over a very long session.
        self._recent_table_opens: dict[str, int] = {}
        self._recent_open_counter = 0

        # Schema sidebar category filter (All Tables / Views / Functions) —
        # a flat, single-category-at-a-time list styled after a categorized
        # sidebar with live counts, rather than a nested Tables/Views/
        # Functions tree the user always has to expand.
        self._active_category = "tables"
        self._category_rows = {}
        self._category_count_labels = {}
        self._category_icon_emoji = {"tables": "\U0001F5C3", "views": "\U0001F441", "functions": "ƒ"}
        self._icon_cache = {}

        # Schema-loading progress indicator (issue #57, upgraded to a
        # progress bar in #263) — ticks elapsed time into the progress bar
        # at the top of the sidebar for an in-flight fetch (first-time load
        # or a stale-cache refresh), so it reads as visibly active rather
        # than a frozen placeholder.
        self._schema_load_timer = None
        self._schema_load_start = 0.0
        self._schema_loading = False
        self._schema_loading_stale = False
        self._schema_tables_seen = 0
        self._schema_retry_item = None
        self._schema_fetch_t0 = None  # perf_metrics: set by _spawn_schema_fetch, read by _on_schema_loaded
        self._notify_schema_refresh = False

        # Wire bridge signals → main-thread handlers (connected once here so
        # QueuedConnection always delivers on the main thread event loop).
        self._q_done.connect(self._on_query_done, Qt.QueuedConnection)
        self._q_multi_done.connect(self._on_query_multi_done, Qt.QueuedConnection)
        self._q_errored.connect(self._on_query_errored, Qt.QueuedConnection)
        self._q_cancelled.connect(self._on_query_cancelled, Qt.QueuedConnection)
        self._q_cost_ready.connect(self._on_query_cost_ready, Qt.QueuedConnection)
        self._q_profile_ready.connect(self._on_query_profile_ready, Qt.QueuedConnection)
        self._schema_done.connect(self._on_schema_loaded, Qt.QueuedConnection)
        self._schema_error.connect(self._on_schema_error, Qt.QueuedConnection)
        self._schema_fast.connect(self._on_schema_tables_ready, Qt.QueuedConnection)
        self._bg_connect_done.connect(self._on_background_connect_done, Qt.QueuedConnection)
        self._export_progress_sig.connect(self._on_export_progress, Qt.QueuedConnection)
        self._export_finished_sig.connect(self._on_export_finished, Qt.QueuedConnection)
        self._export_errored_sig.connect(self._on_export_errored, Qt.QueuedConnection)
        self._export_cancelled_sig.connect(self._on_export_cancelled, Qt.QueuedConnection)
        self._bg_ops: dict = {}   # op_id -> (on_done, on_error) for _run_bg_db()
        self._bg_op_done.connect(self._on_bg_op_done, Qt.QueuedConnection)
        self._bg_op_error.connect(self._on_bg_op_error, Qt.QueuedConnection)
        self._csv_import_progress_dialog = None
        self._csv_import_table = None
        self._csv_import_t0 = None
        self._csv_import_progress.connect(self._on_csv_import_progress, Qt.QueuedConnection)
        self._csv_import_write_done.connect(self._on_csv_import_write_done, Qt.QueuedConnection)
        self._csv_import_write_error.connect(self._on_csv_import_write_error, Qt.QueuedConnection)
        self._db_switch_done.connect(self._on_db_switch_done, Qt.QueuedConnection)
        self.health_changed.connect(self._update_tab_status_bars, Qt.QueuedConnection)
        self._column_cache: dict = {}   # {table: [col, ...]} for autocomplete
        self._column_details_cache: dict = {}   # {table: [{name,type,nullable,default,key}, ...]}
        self._foreign_keys_cache: dict = {}     # {table: [{column, ref_table, ref_column}, ...]}

        self._build_ui()
        self.load_schema()

        if self._connecting:
            self._emit_health('connecting')
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
        left_layout.addWidget(self.db_pill)

        # Schema pill — PostgreSQL only (a database can hold many schemas
        # beyond 'public'; get_tables()/etc. only ever see one at a time via
        # search_path). Hidden until _update_pill_label() finds schemas to
        # switch between.
        self.schema_pill = QPushButton()
        self.schema_pill.setToolTip("Switch schema")
        self.schema_pill.clicked.connect(self.show_schema_switcher)
        self.schema_pill.setVisible(False)
        left_layout.addWidget(self.schema_pill)

        self._apply_pill_style()

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

        # ── Tables / Queries / History toggle ──────────────────────────
        # Order is Tables, Queries, History (issue #130) — Queries sits
        # ahead of History since it's the more actively-used workflow.
        # Labeled "Tables" (issue #240): "Schema" read ambiguously next to
        # "Queries"/"History", as if it meant a schema-selector rather than
        # the tree of tables/views/functions it actually shows.
        sidebar_toggle = QWidget()
        toggle_layout = QHBoxLayout(sidebar_toggle)
        toggle_layout.setContentsMargins(0, 0, 0, 0)
        toggle_layout.setSpacing(0)

        self._schema_btn = QPushButton("Tables")
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

        _toggle_style = self._toggle_style_for(self.current_theme == "dark")
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

        schema_page = QWidget()
        schema_page_layout = QVBoxLayout(schema_page)
        schema_page_layout.setContentsMargins(0, 0, 0, 0)
        schema_page_layout.setSpacing(0)

        # Issue #263: a slim progress bar above the tree replaces the old
        # ticking-text tree row for "in-flight fetch" — indeterminate (busy)
        # range, since the total amount of schema work isn't known up
        # front. Qt's QProgressBar renders no text at all in busy mode
        # (text()/setFormat() are silently ignored once min==max==0), so
        # the elapsed-time/table-count ticker lives in a label under the
        # bar instead of being drawn over it.
        self._schema_progress_bar = QProgressBar()
        self._schema_progress_bar.setRange(0, 0)
        self._schema_progress_bar.setTextVisible(False)
        self._schema_progress_bar.setFixedHeight(4)
        self._schema_progress_bar.hide()
        schema_page_layout.addWidget(self._schema_progress_bar)

        self._schema_loading_label = QLabel("")
        self._schema_loading_label.setContentsMargins(8, 3, 8, 3)
        self._schema_loading_label.hide()
        schema_page_layout.addWidget(self._schema_loading_label)

        self._style_schema_progress_bar(self.current_theme == "dark")

        self.schema_tree = QTreeWidget()
        self.schema_tree.setHeaderHidden(True)
        self.schema_tree.setIndentation(15)
        self.schema_tree.setAnimated(True)
        self.schema_tree.itemClicked.connect(self._on_item_clicked)
        self.schema_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.schema_tree.customContextMenuRequested.connect(self._show_context_menu)
        schema_page_layout.addWidget(self.schema_tree)
        self._sidebar_stack.addWidget(schema_page)   # index 0

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
        self._history_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._history_list.customContextMenuRequested.connect(self._show_history_context_menu)
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
        # Wrapped in a lambda — QPushButton.clicked emits a `checked` bool
        # that would otherwise land in add_new_tab's `silent` arg.
        new_tab_btn.clicked.connect(lambda: self.add_new_tab())
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
        inactive_text = "#c7c7cc" if self.current_theme == "dark" else "#48484a"
        for key, row in self._category_rows.items():
            active = key == self._active_category
            row.setStyleSheet(
                f"background: {'#0A84FF' if active else 'transparent'}; border-radius: 4px;")
            for child in row.findChildren(QLabel):
                child.setStyleSheet(
                    f"color: {'#ffffff' if active else inactive_text}; background: transparent;")

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

        # Issue #256: an explicit refresh (or a db/schema switch, which also
        # routes through here) means the table namespace this connection
        # sees may have changed — drop its in-memory per-table metadata
        # cache (get_columns/get_foreign_keys/get_primary_keys/get_indexes)
        # so callers see current data, not whatever was cached earlier.
        self.db_service.clear_metadata_cache()

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
            # Issue #57/#263: a first-time connect had nothing but a static
            # "Loading…" row for however long the fetch took — easy to
            # mistake for a frozen app on a large schema. The progress bar
            # above the tree ticks elapsed time into its own text (mirrors
            # the Run button's own "⏳ 0.0s" ticker) so it visibly keeps
            # moving, and upgrades that text with a table count the moment
            # it's known (_on_schema_tables_ready, ahead of the slower
            # dbs/views/functions round-trips per issue #16).
            self._start_schema_loading_indicator()

        self._notify_schema_refresh = notify
        self._spawn_schema_fetch(dict(self.config))

    def _apply_cached_schema(self, cached: dict | None) -> bool:
        """Populate the tree/autocomplete from a cached snapshot (issue
        #71) immediately; True on a hit. When the cache is past its
        freshness window (issue #72), the progress bar reappears in its
        "refreshing" wording — it disappears on its own the moment the live
        background refresh (_on_schema_loaded) completes."""
        if not cached:
            return False
        self._on_schema_tables_ready(
            cached.get("tables", []), cached.get("columns", {}),
            cached.get("column_details", {}), cached.get("foreign_keys", {}))
        self._on_schema_loaded(cached)
        if schema_cache.is_stale(cached):
            self._start_schema_loading_indicator(stale=True)
        return True

    def _start_schema_loading_indicator(self, stale: bool = False):
        """Show the sidebar's progress bar with a live elapsed-time ticker
        for the duration of the in-flight fetch (issue #57), in its busy/
        indeterminate mode since the total amount of schema work isn't
        known up front."""
        self._schema_loading = True
        self._schema_loading_stale = stale
        self._schema_tables_seen = 0
        self._schema_load_start = time.time()
        self._schema_progress_bar.show()
        self._schema_loading_label.show()
        if self._schema_load_timer is None:
            from PySide6.QtCore import QTimer
            self._schema_load_timer = QTimer(self)
            self._schema_load_timer.setInterval(200)
            self._schema_load_timer.timeout.connect(self._tick_schema_loading)
        self._tick_schema_loading()
        self._schema_load_timer.start()

    def _tick_schema_loading(self):
        if not self._schema_loading:
            return
        elapsed = time.time() - self._schema_load_start
        if self._schema_tables_seen:
            text = f"{self._schema_tables_seen:,} table(s) found — loading details… {elapsed:.1f}s"
        elif self._schema_loading_stale:
            text = f"Cached schema (stale) — refreshing… {elapsed:.1f}s"
        else:
            text = f"Loading schema… {elapsed:.1f}s"
        self._schema_loading_label.setText(text)

    def _stop_schema_loading_indicator(self):
        if self._schema_load_timer is not None:
            self._schema_load_timer.stop()
        self._schema_loading = False
        self._schema_progress_bar.hide()
        self._schema_loading_label.hide()

    def _spawn_schema_fetch(self, conf: dict):
        """Fetch schema on a daemon thread using a *dedicated* connection
        (see services/schema_snapshot.py) — never touches self.db_service,
        which the main thread may be using concurrently (query tabs, table
        views). Results come back via _schema_done/_schema_fast."""
        sig_done  = self._schema_done
        sig_error = self._schema_error
        sig_fast  = self._schema_fast
        self._schema_fetch_t0 = time.perf_counter()

        def _worker():
            perf_metrics.task_started("schema_fetch")
            try:
                sig_done.emit(fetch_schema_snapshot(
                    conf, on_tables_ready=lambda t, c, cd, fk: sig_fast.emit(t, c, cd, fk)))
            except Exception as ex:
                # Suppress silent "not connected" errors (e.g. (0, '') on startup)
                msg = str(ex)
                if msg in ("(0, '')", "0", "") or "not connected" in msg.lower():
                    return  # connection not ready yet — no error shown
                sig_error.emit(msg)
            finally:
                perf_metrics.task_finished("schema_fetch")

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
            self._emit_health('disconnected')
            QMessageBox.critical(self, "Connection Failed", error)
        else:
            self._emit_health('idle')
            # Now that self.db_service is genuinely live, re-run the schema
            # fetch so the tree/autocomplete reflect the real connection
            # (e.g. if the configured database didn't exist and MySQL fell
            # back to another one — see fetch_schema_snapshot's switched_db).
            self.load_schema()
            # Session restore can create a TableViewWidget before this
            # background connect finishes (issue #176) — its first load
            # hits "No active database connection" and never retries on
            # its own. Now that db_service is genuinely live, retry those.
            self._reload_errored_table_tabs()

    def _run_bg_db(self, fn, on_done, on_error=None, config: dict = None):
        """Run `fn(dedicated_db) -> result` on a background thread against a
        fresh *dedicated* DbService — never self.db_service, which the main
        thread may be using concurrently (query tabs, table views, schema
        fetch) — then deliver the result via `on_done(result)` or the
        exception message via `on_error(message)` (default: a generic error
        dialog), both called back on the main thread. One dedicated
        connection per call, opened and closed around `fn` alone.

        Issue #237: the shared shape for every one-off background DB
        operation below (quick copy/export, CSV import write, truncate/
        drop, mock data insert, metadata fetch for a dialog, …) — same
        dedicated-connection + thread + Qt-signal-bridge pattern already
        used by _spawn_schema_fetch/_connect_in_background/
        SchemaCompareDialog, just genericized via an op_id instead of one
        bespoke Signal per call site."""
        op_id = uuid.uuid4().hex
        self._bg_ops[op_id] = (on_done, on_error)
        cfg = dict(config) if config is not None else dict(self.config)
        sig_done, sig_error = self._bg_op_done, self._bg_op_error

        def _worker():
            db = DbService()
            try:
                db.connect(cfg)
                result = fn(db)
            except Exception as ex:
                sig_error.emit(op_id, str(ex))
                return
            finally:
                try:
                    db.disconnect()
                except Exception:
                    pass
            sig_done.emit(op_id, result)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_bg_op_done(self, op_id: str, result):
        on_done, _ = self._bg_ops.pop(op_id, (None, None))
        if on_done:
            on_done(result)

    def _on_bg_op_error(self, op_id: str, error: str):
        _, on_error = self._bg_ops.pop(op_id, (None, None))
        if on_error:
            on_error(error)
        else:
            QMessageBox.critical(self, "Error", error)

    def _on_schema_tables_ready(self, tables: list, columns: dict,
                                 column_details: dict = None, foreign_keys: dict = None):
        """Push tables/columns to autocomplete as soon as they're fetched —
        ahead of the slower dbs/views/functions/server_version round-trips
        that _on_schema_loaded waits for (issue #16)."""
        self._column_cache = columns
        self._column_details_cache = column_details or {}
        self._foreign_keys_cache = foreign_keys or {}
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            if isinstance(tab, SqlTab):
                tab.set_schema(tables, columns,
                                column_details=self._column_details_cache,
                                foreign_keys=self._foreign_keys_cache)
        # Issue #57: surface the table count on the loading indicator the
        # moment it's known, rather than leaving it a bare "Loading…" for
        # the remainder of the (slower) dbs/views/functions round-trips.
        if self._schema_loading:
            self._schema_tables_seen = len(tables)
            self._tick_schema_loading()

    def _on_schema_loaded(self, result: dict):
        """Main-thread: populate the schema tree from background result."""
        self._stop_schema_loading_indicator()
        if self._schema_fetch_t0 is not None:
            perf_metrics.record("database", "schema_load", (time.perf_counter() - self._schema_fetch_t0) * 1000)
            self._schema_fetch_t0 = None
        # Update DB list + pill
        dbs = result.get("dbs", [])
        self._available_dbs = dbs
        self._available_schemas = result.get("schemas", [])
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

        tables         = result.get("tables", [])
        columns        = result.get("columns", {})
        column_details = result.get("column_details", {})
        foreign_keys   = result.get("foreign_keys", {})
        views          = result.get("views", [])
        functions      = result.get("functions", [])

        self._column_cache = columns
        self._column_details_cache = column_details
        self._foreign_keys_cache = foreign_keys

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
                tab.set_schema(tables, columns,
                                column_details=column_details, foreign_keys=foreign_keys,
                                views=views, functions=functions)

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

    @staticmethod
    def _fetch_db_list(db) -> list:
        """The actual DB-list query, dialect-aware — runs inside
        _load_databases()'s dedicated background connection (issue #237)."""
        db_type = db.db_type
        if db_type == "mysql":
            df = db.execute_query("SHOW DATABASES")
            return [d for d in df.iloc[:, 0].tolist()
                    if d not in ("information_schema", "mysql",
                                 "performance_schema", "sys")]
        elif db_type == "postgresql":
            df = db.execute_query(
                "SELECT datname FROM pg_database WHERE datistemplate = false")
            return df["datname"].tolist()
        return []

    def _load_databases(self, on_done=None):
        """DB-list fetch used by refresh/create/drop-database flows, on a
        dedicated background connection (issue #237). *on_done(ok: bool)*,
        if given, is called back on the main thread once self._available_dbs
        is up to date — callers that need the result (e.g. refresh_databases,
        drop_database's picker) pass one instead of reading it synchronously
        right after calling this."""
        def _done(dbs):
            self._available_dbs = dbs
            current_db = self.config.get("database", "")
            if self.db_service.db_type == "mysql" and current_db not in dbs and dbs:
                # Self-heal: the configured database no longer exists.
                # Runs here (main thread, after the background fetch has
                # already returned) rather than in the worker — this
                # mutates the shared self.db_service.connection, which only
                # the main thread may touch.
                current_db = dbs[0]
                self.config["database"] = current_db
                try:
                    self.db_service.connection.select_db(current_db)
                except Exception as ex:
                    logger.error(f"Failed to self-heal to database {current_db}: {ex}")
            self._update_pill_label()
            if on_done:
                on_done(True)

        def _error(err):
            logger.error(f"Failed to load databases: {err}")
            self._available_dbs = []
            self._update_pill_label()
            if on_done:
                on_done(False)

        self._run_bg_db(self._fetch_db_list, _done, _error)

    def _update_pill_label(self):
        current_db = self.config.get("database", "") or "(no database)"
        suffix = "  ⌘K" if self._available_dbs else ""
        self.db_pill.setText(f"  {current_db}{suffix}")
        # Kept disabled while the background connect from an optimistic
        # open is still in flight, even though cached "dbs" may already be
        # populated — switching would race db_service.connect() (see
        # _connect_in_background).
        self.db_pill.setEnabled(bool(self._available_dbs) and not self._connecting)

        # Every real Postgres database has a 'public' schema, so
        # get_schemas() is practically never empty — only worth a switcher
        # when there's actually more than one to pick from (issue #238).
        has_schemas = len(self._available_schemas) > 1
        self.schema_pill.setVisible(has_schemas)
        if has_schemas:
            current_schema = self.config.get("schema") or "public"
            self.schema_pill.setText(f"  schema: {current_schema}")
            self.schema_pill.setEnabled(not self._connecting)

    def show_schema_switcher(self):
        """Popup twin of show_db_switcher() below, for PostgreSQL schemas."""
        if len(self._available_schemas) <= 1:
            return
        current_schema = self.config.get("schema") or "public"
        dialog = DbSwitcherDialog(self._available_schemas, current_schema, self,
                                   placeholder="Switch schema…")
        dialog.move(self.schema_pill.mapToGlobal(
            self.schema_pill.rect().bottomLeft()))
        dialog.db_selected.connect(self._switch_schema)
        self._schema_switcher_dialog = dialog
        dialog.show()

    def _switch_schema(self, new_schema: str):
        if new_schema == (self.config.get("schema") or "public"):
            return
        if self._connecting:
            QMessageBox.information(
                self, "Connecting…",
                "Still connecting to the database — try switching in a moment.")
            return

        self.schema_tree.clear()
        self.schema_tree.addTopLevelItem(QTreeWidgetItem(["Switching schema…"]))
        from PySide6.QtWidgets import QApplication as _QApp
        _QApp.processEvents()

        # Unlike _switch_database, this never needs a reconnect — Postgres
        # schemas live inside one already-open database connection, so
        # just repointing search_path is enough (see DbService.set_schema).
        if self.db_service.connection:
            try:
                self.db_service.set_schema(new_schema)
            except Exception as ex:
                self._on_schema_error(str(ex))
                return

        self.config["schema"] = new_schema
        self._update_pill_label()
        self._spawn_schema_fetch(dict(self.config))

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
        # Issue #234: Qt.Popup (set in DbSwitcherDialog itself, for real
        # click-outside-to-close) is shown via .show(), not .exec() — an
        # app-modal .exec() loop blocks the very outside clicks this popup
        # needs to see. .show() returns immediately, so this reference has
        # to outlive the call or Python would garbage-collect the dialog
        # out from under its own still-open window.
        self._db_switcher_dialog = dialog
        dialog.show()

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
        # from — left synchronous here by design.
        if self.db_service.db_type == "mysql" and self.db_service.connection:
            _switch_t0 = time.perf_counter()
            try:
                self.db_service.select_db(new_db)
            except Exception as ex:
                self._on_schema_error(str(ex))
                return
            perf_metrics.record("database", "db_switch", (time.perf_counter() - _switch_t0) * 1000)
            self._commit_database_switch(new_db)
            return

        # Postgres connections are bound to one database for their
        # lifetime, so switching needs a real disconnect+reconnect — issue
        # #237: that now runs on a background thread, guarded by
        # self._connecting exactly the way _connect_in_background() is
        # (nothing else touches self.db_service until _on_db_switch_done
        # fires). The (potentially slower) schema listing always runs on a
        # background thread over its OWN dedicated connection
        # (services/schema_snapshot.py) either way.
        self._connecting = True
        self._update_pill_label()
        # Schemas are per-database — a schema pinned in the old database
        # may not exist in new_db, so don't carry it over (SET search_path
        # to a nonexistent schema silently resolves nothing rather than
        # erroring, which would look identical to "no tables" here).
        new_config = dict(self.config, database=new_db)
        new_config.pop("schema", None)
        switch_t0 = time.perf_counter()
        sig_done = self._db_switch_done

        def _worker():
            try:
                self.db_service.disconnect()
                self.db_service.connect(new_config)
            except Exception as ex:
                sig_done.emit(new_db, str(ex), switch_t0)
            else:
                sig_done.emit(new_db, "", switch_t0)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_db_switch_done(self, new_db: str, error: str, switch_t0: float):
        self._connecting = False
        if error:
            self._update_pill_label()
            self._on_schema_error(error)
            return
        perf_metrics.record("database", "db_switch", (time.perf_counter() - switch_t0) * 1000)
        self.config.pop("schema", None)
        self._commit_database_switch(new_db)

    def _commit_database_switch(self, new_db: str):
        # Only commit the switch to tracked state/the pill once the
        # connection has actually confirmed it. Setting these eagerly
        # meant a failed switch left the UI and self.config claiming
        # new_db while the live connection was still silently on the old
        # database — every query in this tab would then run against the
        # wrong database with no indication.
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
        def _done(_):
            QMessageBox.information(self, "Success", f"Database '{name}' created.")
            self._load_databases()

        def _error(msg):
            QMessageBox.critical(self, "Create Database Failed", msg)

        # Issue #237: DDL runs on a dedicated background connection.
        self._run_bg_db(lambda db: db.execute_update(sql), _done, _error)

    def refresh_databases(self):
        """Issue #138: previously ran _load_databases() with no visual
        feedback at all — a slow/remote connection looked frozen and a
        failure was silent. Busy cursor covers the "in progress" window;
        a toast confirms the outcome either way, matching the pattern
        already used for query-done toasts. Issue #237: the fetch itself
        now runs on a dedicated background connection."""
        if not self._check_db_management_supported():
            return
        from PySide6.QtWidgets import QApplication as _QApp
        _QApp.setOverrideCursor(Qt.WaitCursor)

        def _on_result(ok: bool):
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

        self._load_databases(on_done=_on_result)

    def drop_database(self):
        if not self._check_db_management_supported():
            return
        if self._available_dbs:
            self._show_drop_database_picker()
        else:
            self._load_databases(
                on_done=lambda ok: self._show_drop_database_picker() if ok else None)

    def _show_drop_database_picker(self):
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

        def _done(_):
            QMessageBox.information(self, "Success", f"Database '{name}' dropped.")
            self._load_databases()

        def _error(msg):
            QMessageBox.critical(self, "Drop Database Failed", msg)

        # Issue #237: DDL runs on a dedicated background connection.
        self._run_bg_db(lambda db: db.execute_update(sql), _done, _error)

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
        elif self._active_category == "functions" and item.text(0) in self._active_category_items():
            self._show_function_definition(item.text(0))

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
        if self._active_category == "functions" and item.text(0) in self._active_category_items():
            self._show_function_context_menu(item.text(0), position)
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
        open_new_tab_action = menu.addAction("Open in New Tab")
        structure_action = menu.addAction("Open Structure")
        edit_action = menu.addAction("Edit Structure")
        diagram_action = None
        if not is_view:
            diagram_action = menu.addAction("Show Diagram")

        # Impact Analysis (issue #236): Table first/default — analyzing the
        # already-selected table is the common case and needs no further
        # choice. Column needs one (which column?), listed as its own
        # submenu of this table's columns rather than a second dialog.
        # Database needs none either (it's not scoped to this table at
        # all) but sits alongside Table/Column for a single, compact entry
        # point rather than three separate top-level menu rows.
        impact_menu = menu.addMenu("Impact Analysis")
        impact_table_action = impact_menu.addAction("Table")
        impact_column_menu = impact_menu.addMenu("Column")
        impact_column_actions = {}
        try:
            table_columns = self.db_service.get_columns(table_name)
        except Exception:
            table_columns = []
        for col in table_columns:
            col_name = col.get("Field") if isinstance(col, dict) else str(col)
            if col_name:
                impact_column_actions[impact_column_menu.addAction(col_name)] = col_name
        impact_column_menu.setEnabled(bool(impact_column_actions))
        impact_menu.addSeparator()
        impact_database_action = impact_menu.addAction("Database")

        menu.addSeparator()

        # ── Copy ─────────────────────────────────────────────────────────
        copy_name_action = menu.addAction("Copy Name")
        copy_full_name_action = menu.addAction("Copy Full Name")
        menu.addSeparator()

        # ── Organization ────────────────────────────────────────────────
        pin_action = menu.addAction("Unpin from Top" if pinned else "Pin to Top")
        favorite_action = menu.addAction("Remove from Favorites" if favorite else "Add to Favorites")
        menu.addSeparator()

        # ── Export submenu ──────────────────────────────────────────────
        export_menu = menu.addMenu("Export")
        export_action = export_menu.addAction("Export Table…")
        export_sql_action = export_menu.addAction("Export Table as SQL")
        export_cols_action = export_menu.addAction("Export Table with Column Selection…")
        export_data_action = export_menu.addAction("Export Table Data")

        # ── Import submenu ──────────────────────────────────────────────
        import_action = None
        if not is_view:
            import_menu = menu.addMenu("Import")
            import_action = import_menu.addAction("Import Data…")

        # ── New submenu ─────────────────────────────────────────────────
        new_menu = menu.addMenu("New")
        new_table_action = new_menu.addAction("New Table…")
        new_view_action = new_menu.addAction("New View…")

        # ── Copy Script As submenu ──────────────────────────────────────
        script_menu = menu.addMenu("Copy Script As")
        copy_create_action = script_menu.addAction("CREATE Table")
        copy_insert_action = script_menu.addAction("INSERT Data")
        menu.addSeparator()

        # ── Table operations ────────────────────────────────────────────
        clone_action = None
        truncate_action = None
        mock_data_action = None
        if not is_view:
            clone_action = menu.addAction("Clone")
            mock_data_action = menu.addAction("Generate Mock Data…")
        refresh_action = menu.addAction("Refresh Schema")
        refresh_action.setShortcut(QKeySequence("Ctrl+Shift+R"))  # mirrors the real global binding below
        menu.addSeparator()
        if not is_view:
            truncate_action = menu.addAction("Truncate…")
        delete_action = menu.addAction(f"Delete {'View' if is_view else 'Table'}…")

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
        elif action == impact_table_action:
            self._find_table_usages(table_name)
        elif action == impact_database_action:
            self._find_database_usages()
        elif action in impact_column_actions:
            self._find_column_usages(table_name, impact_column_actions[action])
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
        elif mock_data_action is not None and action == mock_data_action:
            self.show_mock_data_generator(table_name)
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
        # Non-modal: it's a read-only viewer, so it shouldn't block the SQL
        # editor/rest of the app the way exec_() (app-modal) would.
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _find_table_usages(self, table_name: str):
        """Issue #236 — lists foreign keys referencing *table_name* plus
        views/functions/procedures whose definition mentions it
        (services/dependency_analyzer.py, schema-metadata driven — not
        text-search over saved queries)."""
        if not require_pro(Feature.IMPACT_ANALYSIS, "Impact Analysis", self):
            return
        report = dependency_analyzer.find_table_dependents(self.db_service, table_name)
        dlg = ImpactAnalysisDialog(report, parent=self)
        dlg.open_table.connect(self.open_table_view)
        dlg.exec_()

    def _find_column_usages(self, table_name: str, column_name: str):
        """Column-scoped sibling of _find_table_usages (issue #236),
        triggered from a table tab's Structure > Columns context menu."""
        if not require_pro(Feature.IMPACT_ANALYSIS, "Impact Analysis", self):
            return
        report = dependency_analyzer.find_column_dependents(self.db_service, table_name, column_name)
        dlg = ImpactAnalysisDialog(report, parent=self)
        dlg.open_table.connect(self.open_table_view)
        dlg.exec_()

    def _find_database_usages(self):
        """Database-scoped sibling of _find_table_usages (issue #236) —
        one dependency report per table, computed from a single shared
        bulk catalog fetch (services/dependency_analyzer.
        find_database_dependents) rather than a per-table round-trip."""
        if not require_pro(Feature.IMPACT_ANALYSIS, "Impact Analysis", self):
            return
        reports = dependency_analyzer.find_database_dependents(self.db_service)
        dlg = DatabaseImpactDialog(reports, parent=self)
        dlg.open_table.connect(self.open_table_view)
        dlg.exec_()

    def open_schema_compare(self):
        """Open the read-only Schema Compare dialog (issue #68), preselecting
        this connection as Source. Builds its own dedicated connections for
        both sides (services/schema_diff.py) — never touches self.db_service."""
        if not require_pro(Feature.SCHEMA_COMPARE, "Schema Compare", self):
            return
        dlg = SchemaCompareDialog(
            self.config.get("id", ""), is_dark=(self.current_theme == "dark"), parent=self)
        dlg.exec_()

    def open_data_compare(self):
        """Open the read-only Data Compare dialog (issue #197/#204),
        preselecting this connection as Source. Builds its own dedicated
        connections for both sides (services/data_diff.py) — never touches
        self.db_service."""
        if not require_pro(Feature.DATA_COMPARE, "Data Compare", self):
            return
        dlg = DataCompareDialog(
            self.config.get("id", ""), is_dark=(self.current_theme == "dark"), parent=self)
        dlg.exec_()

    _MAX_RECENT_TABLE_OPENS = 200

    def _record_recent_table_open(self, table_name: str):
        """Bump *table_name*'s Quick Search recency score (issue #242).
        Capped so a very long session opening many distinct tables can't
        grow this dict without bound — drops the least-recently-touched
        entries first, same spirit as QueryHistory's own cap-and-prune."""
        self._recent_open_counter += 1
        self._recent_table_opens[table_name] = self._recent_open_counter
        if len(self._recent_table_opens) > self._MAX_RECENT_TABLE_OPENS:
            oldest = sorted(self._recent_table_opens, key=self._recent_table_opens.get)
            for name in oldest[:len(self._recent_table_opens) - self._MAX_RECENT_TABLE_OPENS]:
                del self._recent_table_opens[name]

    def open_table_view(self, table_name: str, silent: bool = False, force_new: bool = False):
        """Open a table view; re-focus if already open. *silent* suppresses
        the Free-tier tab-cap prompt for callers restoring a saved session
        rather than acting on a click (issue #154). *force_new* skips the
        re-focus check so "Open in New Tab" always creates a fresh tab
        instead of jumping to an existing one for the same table (issue #179)."""
        self._record_recent_table_open(table_name)

        if not force_new:
            for i in range(self.tabs.count()):
                w = self.tabs.widget(i)
                if isinstance(w, TableViewWidget) and w.table_name == table_name:
                    self.tabs.setCurrentIndex(i)
                    return

        if not self._under_tab_limit(silent):
            return

        tv = TableViewWidget(self.db_service, table_name, self.config)
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
        tv.find_table_usages_signal.connect(self._find_table_usages)
        tv.find_column_usages_signal.connect(self._find_column_usages)
        tv.find_database_usages_signal.connect(self._find_database_usages)

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

    def focus_first_tab(self):
        """Make tab 0 the active tab and focus its editor. Call once after
        all of a panel's session/pinned tabs have been restored (issue
        #149): add_new_tab()/open_table_view() each set themselves as the
        current tab and grab focus, so restoring N tabs in a loop leaves
        whichever one was restored *last* active — not tab 1 — unless
        something resets it afterward."""
        if self.tabs.count() == 0:
            return
        self.tabs.setCurrentIndex(0)
        first_tab = self.tabs.widget(0)
        if hasattr(first_tab, 'editor'):
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, first_tab.editor.setFocus)

    def _under_tab_limit(self, silent: bool) -> bool:
        """True if one more tab stays within Limit.MAX_QUERY_TABS — query
        tabs and table-data tabs share this one limit (issue #154), since
        capping only query tabs would let a Free user route around it by
        browsing tables in unlimited tabs. *silent* checks the cap without
        popping the upgrade dialog, for callers restoring a saved session
        rather than acting on a click."""
        if silent:
            cap = entitlements.limit(Limit.MAX_QUERY_TABS)
            return cap is None or self.tabs.count() < cap
        return require_under_limit(Limit.MAX_QUERY_TABS, self.tabs.count(), "query tabs", self)

    def add_new_tab(self, silent: bool = False):
        """Open a blank SQL query tab. Returns the new tab, or None if the
        tab cap (issue #154) blocked it. *silent* suppresses the upgrade
        prompt for callers restoring a saved session rather than acting on
        a click."""
        if not self._under_tab_limit(silent):
            return None
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
        tab.run_all_requested.connect(lambda: self._run_query_in_tab(tab, run_all=True))
        tab.begin_tx_btn.clicked.connect(lambda: self._run_transaction_control(tab, "BEGIN"))
        tab.commit_tx_btn.clicked.connect(lambda: self._run_transaction_control(tab, "COMMIT"))
        tab.rollback_tx_btn.clicked.connect(lambda: self._run_transaction_control(tab, "ROLLBACK"))
        # Wire inline-edit commit: execute SQL with our db_service
        tab.commit_sql.connect(lambda sqls, t=tab: self._execute_commit_sql(sqls, t))
        tab.open_analyzer.connect(lambda: self.open_query_analyzer(focus_cost_tab=True))
        # Push current schema so autocomplete works immediately
        tab.set_schema(self.all_tables, self._column_cache,
                        column_details=self._column_details_cache,
                        foreign_keys=self._foreign_keys_cache,
                        views=self.all_views, functions=self.all_functions)
        tab.set_dialect(self._dialect_display_name())
        tab.set_connection_state(getattr(self, '_last_health', 'idle'))
        self._attach_close_btn(idx)
        self.tabs.setCurrentWidget(tab)
        tab.update_theme(self.current_theme == "dark")
        # Focus the editor after the tab is fully shown
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, tab.editor.setFocus)
        return tab

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
        perf_metrics.record("sql_editor", "query_execute", elapsed * 1000)
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
        cost = getattr(tab, '_last_cost_estimate', None)
        tab._last_history_entry_id = self.query_history.add_query(
            query, self.config["name"], len(df), elapsed,
            cost_score=cost.score if cost and not cost.error else None,
            cost_label=cost.label if cost and not cost.error else None,
            cost_detail=query_cost.estimate_to_dict(cost),
        )
        if self._sidebar_stack.currentIndex() == 2:
            self._reload_history_list(self._history_search.text())
        self._emit_health('idle')
        if self.tabs.currentWidget() is not tab:
            tab_name = self.tabs.tabText(self.tabs.indexOf(tab))
            self._show_query_toast(tab_name, len(df), elapsed)
        self._finalize_query_connection(tab)

    def _on_query_multi_done(self, tab, results: list, elapsed: float):
        """Multi-statement result handler. Every statement gets its own
        "Query N" sub-tab — a SELECT shows its rows, a write shows a
        rows-affected summary, and a statement that errored shows its own
        error when that tab is selected (tab.load_multi_results /
        _on_multi_result_tab), rather than only SELECT-producing statements
        being visible and everything else vanishing silently."""
        import pandas as pd

        perf_metrics.record("sql_editor", "query_execute", elapsed * 1000)
        tab._query_running = False
        self._restore_run_btn(tab)
        tab.cancel_btn.setEnabled(False)
        if hasattr(tab, '_query_thread'):
            tab._query_thread.quit()
        query = getattr(tab, '_last_query', '')
        select_results = [(lbl, obj) for lbl, obj, _ in results if isinstance(obj, pd.DataFrame)]

        total_rows = sum(len(df) for _, df in select_results)
        # Roll every statement's own plan-only estimate up into one score
        # for the history row — the worst statement wins, same "worst issue
        # decides the label" logic score_issues()/label_for() already use
        # within a single estimate's own issue list.
        costs = [cost for _, _, cost in results if cost is not None]
        worst_cost = max(costs, key=lambda c: c.score) if costs else None
        self.query_history.add_query(
            query, self.config["name"], total_rows, elapsed,
            cost_score=worst_cost.score if worst_cost else None,
            cost_label=worst_cost.label if worst_cost else None,
            cost_detail=query_cost.estimate_to_dict(worst_cost),
        )
        if self._sidebar_stack.currentIndex() == 2:
            self._reload_history_list(self._history_search.text())

        if len(results) == 1:
            # single statement — display inline as normal (in practice this
            # branch isn't reached: the worker only takes this multi-
            # statement path for 2+ statements — kept as a defensive
            # fallback rather than assumed unreachable).
            label, obj, cost = results[0]
            tab._last_cost_estimate = cost
            if isinstance(obj, Exception):
                tab.show_error(str(obj), query=label, elapsed=elapsed)
            elif isinstance(obj, pd.DataFrame):
                tab.load_dataframe(obj, self._extract_table_name(query))
                tab.update_status(len(obj), elapsed, truncated=obj.attrs.get("truncated", False))
                tab.set_cost_estimate(cost)
            else:
                tab.update_status(obj or 0, elapsed)
        else:
            # Every statement gets its own "Query N" tab. load_multi_results
            # already renders a DataFrame or an Exception per tab — a write
            # (an int affected-row count) gets wrapped in a one-row summary
            # DataFrame so it reuses that same rendering with no UI changes.
            # Its cost estimate travels alongside so the tab can show that
            # statement's own badge when its sub-tab is selected.
            display_results = [
                (label, obj if isinstance(obj, (pd.DataFrame, Exception))
                       else pd.DataFrame({"result": [f"{obj} row(s) affected"]}),
                 cost)
                for label, obj, cost in results
            ]
            tab.load_multi_results(display_results, elapsed)

        self._emit_health('idle')
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
            self._emit_health('disconnected')
        else:
            self._emit_health('idle')
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
        # Every other query-completion path (_on_query_done, _on_query_errored,
        # the multi-result path) emits health_changed('idle') right after —
        # this one didn't, leaving the status bar's readiness dot stuck on
        # "Running…" forever after a cancel (issue #178).
        self._emit_health('idle')

    def _on_query_cost_ready(self, tab, estimate):
        """Receives worker `cost_ready` signal via bridge — best-effort,
        never fires for a write, a multi-statement script, or an
        unsupported dialect/statement. Arrives before _on_query_done since
        the worker computes it first, so it's already on `tab` by the time
        query_history.add_query() reads it there."""
        tab._last_cost_estimate = estimate
        if hasattr(tab, 'set_cost_estimate'):
            tab.set_cost_estimate(estimate)

    def _on_query_profile_ready(self, tab, profile):
        """Receives worker `profile_ready` signal via bridge — only fires
        when the tab's Auto-profile toggle was on for this run. Arrives
        after _on_query_done (the worker only starts profiling once the
        real run's `done` has already been emitted), so tab.
        _last_history_entry_id is already set by the time this needs it;
        a no-op if that entry has since aged out of the history cap."""
        entry_id = getattr(tab, '_last_history_entry_id', None)
        if not entry_id:
            return
        self.query_history.update_entry(
            entry_id, profile_detail=query_cost.profile_to_dict(profile))
        if self._sidebar_stack.currentIndex() == 2:
            self._reload_history_list(self._history_search.text())

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

    def _run_query_in_tab(self, tab, override_query: str = None, run_all: bool = False):
        """Execute the SQL in `tab` on a background thread; Cancel actually
        stops it. *override_query* (set by the Begin/Commit/Rollback
        buttons) bypasses the editor content and the format/param-prompt
        steps below, but still goes through the same guard, connection
        handling, and worker dispatch as typed SQL — a single code path so
        transaction control can't accidentally skip the write guard.

        Plain Run (the Run button / Ctrl+Return) scopes to the selection,
        or the statement the cursor is inside if nothing is selected
        (`SqlTab.get_query()`'s selection → statement-at-cursor →
        whole-text fallback chain) — matching DataGrip/DBeaver/TablePlus'
        convention, and what a script with the cursor on one particular
        statement should do.

        *run_all=True* (Ctrl+Shift+Return / `SqlTab.run_all_requested`) is
        the deliberate, explicit way to run every statement in the editor
        regardless of selection or cursor — for an intentional multi-
        statement script, not as Run's silent default (a user previously
        relied on that silent default and got confused when only the
        cursor's statement ran; making "run everything" its own clearly
        separate action fixes that confusion without also making the
        common case — one statement, cursor somewhere in the editor —
        run every statement in the file). Each statement still runs
        independently via DbService.execute_multi_query, so one failing
        doesn't stop the rest, and each gets its own "Query N" tab in the
        results (_on_query_multi_done)."""
        if override_query is not None:
            query = override_query
        elif run_all:
            query = tab.editor.toPlainText().strip()
        else:
            query = tab.get_query().strip()
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
        tab._last_cost_estimate = None
        tab._last_history_entry_id = None
        if hasattr(tab, 'clear_cost_estimate'):
            tab.clear_cost_estimate()
        if hasattr(tab, 'clear_for_run'):
            tab.clear_for_run()
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
        self._emit_health('running')

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

        auto_profile = getattr(tab, 'auto_profile_chk', None) is not None and tab.auto_profile_chk.isChecked()
        worker = _QueryWorker(query_db, query, tab._cancel_flag, auto_profile=auto_profile)
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
        worker.cost_ready.connect(
            lambda estimate: self._q_cost_ready.emit(tab, estimate))
        worker.profile_ready.connect(
            lambda profile: self._q_profile_ready.emit(tab, profile))

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

    def open_query_analyzer(self, focus_cost_tab: bool = False, query: str = None,
                             cost_detail: dict = None, profile_detail: dict = None,
                             history_entry_id: str = None):
        """Open the consolidated Analyze Query dialog (Cost & Profile +
        Compare Queries), pre-populated with *query* — or, when omitted,
        the current tab's query (the original behavior, used by the
        Database menu and the status-bar cost badge). *query* lets other
        callers (e.g. the History panel's "View Cost & Profile" action)
        seed the dialog with an arbitrary past query instead. *cost_detail*
        /*profile_detail* are that query's stored query_cost.
        estimate_to_dict()/profile_to_dict() output, if any — shown
        immediately instead of the dialog's blank "run an analysis" state;
        the Estimate Cost / Run Profile buttons still work as normal for a
        live re-check against the database now. *history_entry_id*, when
        given, makes a live Estimate/Profile run from inside the dialog
        stick back onto that history entry — see _CostProfileTab.
        Free tier — no require_pro() gate, unlike Schema/Data Compare;
        this is a safety/education aid, not a power-user workflow."""
        if not self.db_service or not self.db_service.connection:
            QMessageBox.information(self, "Analyze Query", "Connect to a database first.")
            return
        from ui.query_analyzer_dialog import QueryAnalyzerDialog
        if query is None:
            tab = self.tabs.currentWidget()
            query = tab.get_query().strip() if tab and hasattr(tab, 'get_query') else ""
        dlg = QueryAnalyzerDialog(self.db_service, initial_query=query,
                                   initial_cost_detail=cost_detail,
                                   initial_profile_detail=profile_detail,
                                   query_history=self.query_history,
                                   history_entry_id=history_entry_id, parent=self)
        if focus_cost_tab:
            dlg.show_cost_tab()
        dlg.show()

    def run_all_statements(self):
        """Database menu / Ctrl+Shift+Return: run every statement in the
        current tab regardless of selection or cursor position — see
        _run_query_in_tab's run_all parameter for why this is a distinct
        action from plain Run rather than Run's default behavior."""
        tab = self.tabs.currentWidget()
        if isinstance(tab, SqlTab):
            self._run_query_in_tab(tab, run_all=True)

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
        dedicated_db = getattr(w, '_dedicated_db', None)
        if dedicated_db is not None:
            try:
                dedicated_db.disconnect()
            except Exception as ex:
                logger.debug(f"Failed to close tab's dedicated connection: {ex}")
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
        if not dialog.exec():
            return
        sql = dialog.get_sql()
        if not self._guard_write(sql):
            return
        reply = QMessageBox.question(
            self, "Create Table",
            f"Execute the following SQL?\n\n{sql}",
            QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        def _done(_):
            QMessageBox.information(self, "Success", "Table created successfully")
            self.load_schema()

        def _error(msg):
            QMessageBox.critical(self, "Error", msg)

        # Issue #237: DDL runs on a dedicated background connection.
        self._run_bg_db(lambda db: db.execute_update(sql), _done, _error)

    def _show_function_context_menu(self, name: str, position):
        menu = QMenu(self)
        view_action = menu.addAction("🔍 View Definition")
        copy_action = menu.addAction("Copy Definition")
        copy_name_action = menu.addAction("Copy Name")
        action = menu.exec_(self.schema_tree.mapToGlobal(position))
        if action == view_action:
            self._show_function_definition(name)
        elif action == copy_action:
            self._copy_function_definition(name)
        elif action == copy_name_action:
            QApplication.clipboard().setText(name)

    def _show_function_definition(self, name: str):
        """Read-only popup showing a function/procedure's CREATE statement
        (issue: functions in the sidebar had no way to see what's inside
        them — clicking/right-clicking one was a no-op)."""
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QPlainTextEdit, QDialogButtonBox
        try:
            ddl = self.db_service.get_function_definition(name)
        except Exception as ex:
            QMessageBox.critical(self, "Error", f"Could not load definition for {name}:\n{ex}")
            return
        if not ddl:
            QMessageBox.information(self, "View Definition", f"No definition found for '{name}'.")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Definition — {name}")
        dlg.resize(700, 500)
        layout = QVBoxLayout(dlg)
        text = QPlainTextEdit(dlg)
        text.setPlainText(ddl)
        text.setReadOnly(True)
        text.setFont(QFont("Menlo, Consolas, monospace"))
        layout.addWidget(text)
        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(dlg.reject)
        btns.accepted.connect(dlg.accept)
        copy_btn = btns.addButton("Copy", QDialogButtonBox.ActionRole)
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(ddl))
        layout.addWidget(btns)
        dlg.exec()

    def _copy_function_definition(self, name: str):
        try:
            ddl = self.db_service.get_function_definition(name)
        except Exception as ex:
            QMessageBox.critical(self, "Copy Definition Error", f"Could not load definition for {name}:\n{ex}")
            return
        QApplication.clipboard().setText(ddl)

    def _show_table_structure(self, table_name: str):
        """Open (or focus) *table_name*'s tab and switch it to the Structure
        sub-tab (issue #27) — no more separate popup window to lose context in."""
        self.open_table_view(table_name)
        w = self.tabs.currentWidget()
        # A blocked tab cap (issue #154) leaves whatever tab was already
        # active in place — only switch to Structure if it's actually the
        # requested table, not some other tab that happened to be current.
        if isinstance(w, TableViewWidget) and w.table_name == table_name:
            w.show_structure_tab()



    def show_alter_table_editor(self, table_name: str):
        def _got_columns(existing_columns):
            dialog = StructureEditorDialog(
                db_type=self.db_service.db_type,
                table_name=table_name,
                existing_columns=existing_columns,
                parent=self)

            if not dialog.exec():
                return
            sql = dialog.get_sql()
            if sql.strip().startswith("--"):
                QMessageBox.information(self, "No Changes", sql)
                return
            if not self._guard_write(sql):
                return

            # Issue #236: silent check (no upgrade nag) — warn about any
            # column this ALTER drops that other schema objects depend
            # on. Cheap metadata query per dropped column — left on the
            # main connection, same as the other "lowest priority"
            # metadata-only calls in issue #237.
            impact = ""
            if entitlements.is_enabled(Feature.IMPACT_ANALYSIS):
                texts = []
                for col in dialog.get_dropped_columns():
                    report = dependency_analyzer.find_column_dependents(
                        self.db_service, table_name, col)
                    text = impact_warning_text(report)
                    if text:
                        texts.append(text)
                if texts:
                    impact = "\n\n".join(texts) + "\n\n"

            reply = QMessageBox.question(
                self, "Alter Table",
                f"{impact}Execute the following SQL?\n\n{sql}",
                QMessageBox.Yes | QMessageBox.No)
            if reply != QMessageBox.Yes:
                return

            def _run(db):
                for stmt in query_classifier.split_statements(sql):
                    db.execute_update(stmt)

            def _done(_):
                QMessageBox.information(
                    self, "Success", f"Table {table_name} altered successfully")
                self.load_schema()

            def _error(msg):
                QMessageBox.critical(self, "Error", msg)

            # Issue #237: ALTER runs on a dedicated background connection.
            self._run_bg_db(_run, _done, _error)

        def _error(msg):
            QMessageBox.critical(self, "Error", f"Could not load columns for {table_name}:\n{msg}")

        # Issue #237: the columns fetch that seeds the dialog runs on a
        # dedicated background connection.
        self._run_bg_db(lambda db: db.get_columns(table_name), _got_columns, _error)

    def _sample_fk_values(self, ref_table: str, ref_column: str, limit: int = 200) -> list:
        """Read-only sample of existing values for a foreign-key target
        (issue #77's "Foreign-key references" generator) — a SELECT, so it
        works even on a read-only connection. Never raises; MockDataDialog
        treats a failed/empty sample as "no valid FK values available" and
        warns instead of blocking generation entirely. Also reused as-is
        for issue #215's multi-table generation: it's exactly the shape
        `generate_chain_dataframes`'s `external_pool_fn` needs (ref_table,
        ref_column) -> list[value], for any ancestor edge that falls back
        to live sampling instead of freshly generated in-memory values."""
        db_type = self.db_service.db_type
        table_sql = _quote_identifier(ref_table, db_type)
        col_sql = _quote_identifier(ref_column, db_type)
        df = self.db_service.execute_query(
            f"SELECT DISTINCT {col_sql} FROM {table_sql} LIMIT {int(limit)}",  # nosec B608
            max_rows=limit,
        )
        return df.iloc[:, 0].dropna().tolist()

    def _schema_for_table(self, table_name: str) -> tuple:
        """(columns, primary_keys, foreign_keys, generated_columns) for
        *table_name* — the same four calls show_mock_data_generator always
        made for the root table, factored out so issue #215's ancestor
        panel can fetch the same shape lazily, per ancestor, only once the
        user actually opts into "Include dependent tables"."""
        return (
            self.db_service.get_columns(table_name),
            self.db_service.get_primary_keys(table_name),
            self.db_service.get_foreign_keys(table_name),
            self.db_service.get_generated_columns(table_name),
        )

    def _existing_row_count(self, table_name: str) -> int:
        """Best-effort "does this ancestor table already have rows"
        check for issue #215's reuse-vs-generate default. Prefers the
        cheap stats-based estimate; only pays for a real read on the rare
        dialect/version where that estimate isn't available. Never
        raises — an unknown count is treated as 0 (safe default: generate
        fresh rather than silently produce an empty FK pool)."""
        try:
            estimate = self.db_service.get_estimated_row_count(table_name)
            if estimate is not None:
                return estimate
            table_sql = _quote_identifier(table_name, self.db_service.db_type)
            df = self.db_service.execute_query(f"SELECT 1 FROM {table_sql} LIMIT 1", max_rows=1)  # nosec B608
            return len(df)
        except Exception:
            return 0

    def _pk_offset(self, table_name: str, column: str) -> int:
        """MAX(existing value) + 1 for *column* in *table_name* — issue
        #215's generated ancestor rows start above this so they can never
        collide with real data already in the table. Never raises; 1 is a
        safe default (an empty/unreadable table has nothing to collide
        with)."""
        try:
            table_sql = _quote_identifier(table_name, self.db_service.db_type)
            col_sql = _quote_identifier(column, self.db_service.db_type)
            df = self.db_service.execute_query(
                f"SELECT COALESCE(MAX({col_sql}), 0) + 1 FROM {table_sql}", max_rows=1  # nosec B608
            )
            return int(df.iloc[0, 0])
        except Exception:
            return 1

    def show_mock_data_generator(self, table_name: str):
        """Opens the Mock Data Generator (issue #77). Environment/read-only
        safety gating happens up front, before the dialog even opens —
        Production is blocked outright and Read-only connections are
        blocked outright, matching the issue's Environment/Read-Only
        Protection tables; Staging requires an explicit confirmation first.
        _guard_write below is the existing backstop (mainly re-covers
        read-only if state changed mid-flow) — no extra_reason is passed to
        it so Staging isn't asked to confirm a second time.

        Issue #215: also offers to generate the table's FK ancestor chain
        (parents-before-children) when one exists — see
        services.mock_data_generator.build_dependency_chain. A single
        cheap bulk get_all_foreign_keys() call decides whether that offer
        is even shown; a table with no ancestors pays nothing extra and
        behaves exactly as before."""
        connection_name = self.config.get("name", "Connection")
        env = environment.normalize(self.config.get("environment"))
        read_only = bool(self.config.get("read_only"))
        if not query_guard_dialog.mock_data_generation_allowed(self, connection_name, env, read_only):
            return

        try:
            columns = self.db_service.get_columns(table_name)
            primary_keys = self.db_service.get_primary_keys(table_name)
            foreign_keys = self.db_service.get_foreign_keys(table_name)
            generated_columns = self.db_service.get_generated_columns(table_name)
        except Exception as ex:
            QMessageBox.critical(self, "Error", f"Could not load schema for {table_name}:\n{ex}")
            return

        try:
            all_fks = self.db_service.get_all_foreign_keys()
            dependency_chain = mock_gen.build_dependency_chain(all_fks, table_name)
        except Exception:
            dependency_chain = None  # best-effort — dialog falls back to single-table behavior

        dialog = MockDataDialog(
            table_name, columns, primary_keys, foreign_keys, generated_columns,
            dialect=self.db_service.db_type, fk_sampler=self._sample_fk_values,
            dependency_chain=dependency_chain, schema_fetcher=self._schema_for_table,
            existing_row_count_fetcher=self._existing_row_count, pk_offset_fetcher=self._pk_offset,
            parent=self,
        )
        if not dialog.exec():
            return

        sql = dialog.get_sql()
        if not sql.strip():
            QMessageBox.information(self, "No Data", "No columns were selected to insert.")
            return

        if not self._guard_write(sql):
            return
        reply = QMessageBox.question(
            self, "Insert Mock Data",
            f"Execute the following SQL?\n\n{sql[:2000]}" + ("\n…" if len(sql) > 2000 else ""),
            QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        generated_pk_columns = dialog.generated_pk_columns()

        def _run(db):
            for stmt in query_classifier.split_statements(sql):
                db.execute_update(stmt)
            for gen_table, pk_column in generated_pk_columns:
                db.bump_sequence_for_column(gen_table, pk_column)

        def _done(_):
            QMessageBox.information(self, "Success", f"Mock data inserted into {table_name}.")
            for i in range(self.tabs.count()):
                w = self.tabs.widget(i)
                if isinstance(w, TableViewWidget) and w.table_name == table_name:
                    w._warn_and_discard_changes()
                    w.current_page = 1
                    w.load_table_data()
                    break

        def _error(msg):
            QMessageBox.critical(self, "Error", msg)

        # Issue #237: write cost scales with the requested row count, so
        # this runs on a dedicated background connection.
        self._run_bg_db(_run, _done, _error)

    # ─── CSV Import ──────────────────────────────────────────────────────────

    def export_database(self):
        """Export chosen tables' structure and/or data to a single SQL dump
        (issue #39: whole-database export, independent of any query tab).
        Per-table Structure/Content/Drop and the advanced options (hex BLOBs,
        BOM, gzip, KiB-batched INSERTs) are all chosen up front via
        ExportScopeDialog (issue #157). The write itself streams rows off a
        background thread, pipelined across tables (issue #158) — see
        _run_export()/_ExportWorker."""
        all_tables = self.db_service.get_tables()
        if not all_tables:
            QMessageBox.information(self, "Export Database", "No tables to export.")
            return

        scope = ExportScopeDialog(all_tables, dialect=self.db_service.db_type, parent=self)
        if not scope.exec():
            return
        table_opts = scope.table_options()
        if not table_opts:
            QMessageBox.information(self, "Export Database", "No tables selected.")
            return

        file_path = self._pick_export_file("Export Database", "database", scope)
        if not file_path:
            return

        self._run_export(table_opts, file_path, scope, title="Export Database")

    def _pick_export_file(self, title: str, default_stem: str, scope) -> str | None:
        """Prompt for a save path whose filter/extension matches *scope*'s
        selected format tab (issue #159): a .sql dump, a .zip of per-table
        CSV/XML files, or a single .dot schema diagram."""
        from PySide6.QtWidgets import QFileDialog

        fmt = scope.export_format()
        filters = {
            "sql": "SQL Dump (*.sql)",
            "csv": "Zip Archive (*.zip)",
            "xml": "Zip Archive (*.zip)",
            "dot": "Graphviz Dot (*.dot)",
        }
        default_ext = {"sql": "sql", "csv": "zip", "xml": "zip", "dot": "dot"}[fmt]

        file_path, _ = QFileDialog.getSaveFileName(
            self, title, f"{default_stem}.{default_ext}", filters[fmt]
        )
        if not file_path:
            return None
        if fmt in ("sql", "dot") and scope.gzip_output() and not file_path.endswith(".gz"):
            file_path += ".gz"
        return file_path

    def _run_export(self, table_opts: dict, file_path: str, scope, title: str):
        """Stream *table_opts* to *file_path* on a background QThread via
        _ExportWorker (issue #158): a producer thread walks the tables and
        pushes structure/row-chunk items onto a bounded queue while the
        worker drains it and writes to the (optionally gzip'd) file, so
        table N+1's fetch overlaps table N's disk/gzip flush instead of
        running strictly sequentially. Uses a dedicated DbService connection
        so the export never shares state with the main schema-browsing
        connection (same reasoning as _QueryWorker's dedicated connection)."""
        export_db = DbService()
        try:
            export_db.connect(self.config)
        except Exception as ex:
            QMessageBox.critical(self, "Export Error", f"Could not open export connection:\n{ex}")
            return

        export_format = scope.export_format()
        progress_max = 1 if export_format == "dot" else len(table_opts)
        progress = QProgressDialog("Exporting…", "Cancel", 0, progress_max, self)
        progress.setWindowTitle(title)
        progress.setMinimumDuration(0)

        cancel_flag = threading.Event()
        worker = _ExportWorker(
            export_db, table_opts, file_path, export_format=export_format,
            blob_as_hex=scope.blob_as_hex(), batch_kib=scope.batch_kib(),
            gzip_output=scope.gzip_output(), use_bom=scope.use_bom(),
            include_auto_increment=scope.include_auto_increment(),
            strip_generated=scope.strip_generated_columns(),
            cancel_flag=cancel_flag,
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)

        # Keep refs alive until the thread finishes (same as _QueryWorker).
        self._export_thread = thread
        self._export_worker = worker
        # Read by the bound _on_export_* slots below — only one export runs
        # at a time per panel, so plain instance attrs are enough (same
        # pattern as _export_thread/_export_worker above).
        self._export_progress_dialog = progress
        self._export_title = title
        self._export_file_path = file_path
        self._export_table_opts = table_opts
        self._export_format = export_format

        progress.canceled.connect(cancel_flag.set)

        # _ExportWorker's signals fire on the worker thread. Connecting them
        # directly to plain closures here would run those closures (and any
        # QProgressDialog/QMessageBox call inside) on the worker thread too —
        # PySide6 doesn't reliably promote that to a queued, main-thread
        # call (see the bridge-signal comment above _bg_connect_done).
        # Routing through the QueuedConnection-bound _export_*_sig bridge
        # signals guarantees the actual GUI work happens on the main thread.
        worker.progress.connect(lambda table, count: self._export_progress_sig.emit(table, count))
        worker.finished.connect(lambda failures: self._export_finished_sig.emit(failures))
        worker.errored.connect(lambda msg: self._export_errored_sig.emit(msg))
        worker.cancelled.connect(lambda: self._export_cancelled_sig.emit())

        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        worker.finished.connect(thread.quit)
        worker.errored.connect(thread.quit)
        worker.cancelled.connect(thread.quit)

        thread.start()

    def _on_export_progress(self, table: str, count: int):
        self._export_progress_dialog.setLabelText(f"Exporting {table}…")
        self._export_progress_dialog.setValue(count)

    def _on_export_finished(self, failures: list[str]):
        self._export_progress_dialog.close()
        table_opts, file_path, title = self._export_table_opts, self._export_file_path, self._export_title
        if self._export_format == "dot":
            summary = f"Exported schema diagram to:\n{file_path}"
        else:
            summary = f"Exported {len(table_opts) - len(failures)}/{len(table_opts)} tables to:\n{file_path}"
        if failures:
            summary += "\n\nFailed:\n" + "\n".join(failures)
            QMessageBox.warning(self, title, summary)
        else:
            QMessageBox.information(self, title, summary)

    def _on_export_errored(self, msg: str):
        self._export_progress_dialog.close()
        QMessageBox.critical(self, "Export Error", f"Could not write file:\n{msg}")

    def _on_export_cancelled(self):
        self._export_progress_dialog.close()

    def _write_table_export(self, fh, table: str, opts: dict,
                             blob_as_hex: bool = True, batch_kib=None,
                             include_auto_increment: bool = True, strip_generated: bool = False):
        """Write *table*'s structure/content/drop to the already-open file
        handle *fh*, per *opts* (`{"structure", "content", "drop"} -> bool`,
        from ExportScopeDialog.table_options()). Generated columns are
        always excluded from the content SELECT (issue #160) — they can't
        appear in an INSERT column list regardless of any toggle."""
        dialect = self.db_service.db_type
        if opts.get("drop"):
            fh.write(drop_table_statement(table, dialect) + "\n")
        if opts.get("structure"):
            ddl = self.db_service.get_table_ddl(table)
            if not include_auto_increment:
                ddl = strip_auto_increment_value(ddl)
            if strip_generated:
                ddl = strip_generated_column_clauses(ddl)
            fh.write(f"-- Table: {table}\n")
            fh.write(ddl + "\n\n")
        if opts.get("content"):
            df = self.db_service.execute_query(
                f"SELECT {self.db_service.content_select_list(table)} FROM {table}")  # nosec B608
            if not df.empty:
                fh.write(_to_sql_inserts(
                    df, table, dialect=dialect, batch_kib=batch_kib,
                    blob_as_hex=blob_as_hex) + "\n\n")

    def _export_table(self, table_name: str):
        """Export a single table without needing an open query tab (issue
        #39), via the same per-table Structure/Content/Drop grid + advanced
        options as export_database() (issue #157). Plain CSV/JSON/Excel/SQL
        data-only export without the scope dialog is still available via
        the table context menu's "Export Table Data" (_export_table_data_only)."""
        scope = ExportScopeDialog([table_name], dialect=self.db_service.db_type, parent=self)
        if not scope.exec():
            return
        table_opts = scope.table_options()
        if not table_opts:
            QMessageBox.information(self, "Export Table", "Nothing selected to export.")
            return

        file_path = self._pick_export_file("Export Table", table_name, scope)
        if not file_path:
            return

        self._run_export(table_opts, file_path, scope, title="Export Table")

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
            file_size = os.path.getsize(file_path)
        except OSError as ex:
            QMessageBox.critical(self, "Import Error", f"Could not read file:\n{ex}")
            return
        if file_size > IMPORT_MAX_FILE_SIZE_BYTES:
            QMessageBox.critical(
                self, "Import Error",
                f"File is {file_size / (1024 * 1024):,.0f} MiB, over the "
                f"{IMPORT_MAX_FILE_SIZE_BYTES // (1024 * 1024):,} MiB import limit.")
            return

        try:
            sep = "\t" if file_path.endswith((".tsv", ".txt")) else ","
            df = pd.read_csv(
                file_path, sep=sep, keep_default_na=False,
                nrows=IMPORT_MAX_ROWS + 1,
            )
        except Exception as ex:
            QMessageBox.critical(self, "Import Error", f"Could not read file:\n{ex}")
            return

        if len(df) > IMPORT_MAX_ROWS:
            QMessageBox.critical(
                self, "Import Error",
                f"File has more than {IMPORT_MAX_ROWS:,} rows — over the import limit.")
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

        # Issue #114/#115: column names come straight from the imported
        # file's header row — untrusted input — so they're quoted and
        # escaped the same way _quote_identifier already handles exported
        # SQL, not just wrapped in bare backticks/quotes.
        cols_sql = ", ".join(_quote_identifier(c, db_type) for c in df.columns)
        placeholders = ", ".join([ph] * len(df.columns))
        insert_sql = (
            f"INSERT INTO {_quote_identifier(table_name, db_type)} "
            f"({cols_sql}) VALUES ({placeholders})"  # nosec B608 -- identifiers quoted/escaped via _quote_identifier(), values parameterized
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

        # Issue #237: the batched INSERT write loop runs on a dedicated
        # background connection instead of blocking the UI thread.
        # Progress/cancellation cross the thread boundary via Qt signals —
        # execute_batch()'s on_batch/should_cancel callbacks would otherwise
        # touch the QProgressDialog and a plain bool straight from the
        # worker thread, neither of which is safe.
        cancel_event = threading.Event()
        progress.canceled.connect(cancel_event.set)
        self._csv_import_progress_dialog = progress
        self._csv_import_table = table_name
        self._csv_import_t0 = time.perf_counter()

        cfg = dict(self.config)
        sig_progress = self._csv_import_progress
        sig_done = self._csv_import_write_done
        sig_error = self._csv_import_write_error

        def _worker():
            db = DbService()
            try:
                db.connect(cfg)
                inserted, errors = db.execute_batch(
                    insert_sql, rows,
                    batch_size=BATCH,
                    on_batch=lambda start, n: sig_progress.emit(start + n),
                    should_cancel=cancel_event.is_set,
                )
            except Exception as ex:
                sig_error.emit(str(ex))
                return
            finally:
                try:
                    db.disconnect()
                except Exception:
                    pass
            sig_done.emit(inserted, errors)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_csv_import_progress(self, n: int):
        if self._csv_import_progress_dialog is not None:
            self._csv_import_progress_dialog.setValue(n)

    def _on_csv_import_write_done(self, inserted: int, errors: int):
        dlg, table_name, t0 = (
            self._csv_import_progress_dialog, self._csv_import_table, self._csv_import_t0)
        self._csv_import_progress_dialog = None
        if dlg is not None:
            dlg.close()
        if t0 is not None:
            perf_metrics.record("import_export", "csv_import", (time.perf_counter() - t0) * 1000)

        msg = f"Imported <b>{inserted:,}</b> rows into <b>{table_name}</b>."
        if errors:
            msg += f"<br>{errors} batch(es) failed — check logs."
        QMessageBox.information(self, "Import Complete", msg)

        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, TableViewWidget) and w.table_name == table_name:
                w._warn_and_discard_changes()
                w.current_page = 1
                w.load_table_data()
                break

    def _on_csv_import_write_error(self, msg: str):
        dlg, t0 = self._csv_import_progress_dialog, self._csv_import_t0
        self._csv_import_progress_dialog = None
        if dlg is not None:
            dlg.close()
        if t0 is not None:
            perf_metrics.record("import_export", "csv_import", (time.perf_counter() - t0) * 1000)
        QMessageBox.critical(self, "Import Error", msg)

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
        """Issue #237: runs the full-table SELECT on a dedicated background
        connection instead of blocking the UI thread on a possibly huge
        table — the dialect's own DbService.content_select_list() needs
        that dedicated connection too, so it's read inside the worker."""
        dialect = self.db_service.db_type

        def _fetch(db):
            return db.execute_query(
                f"SELECT {db.content_select_list(table_name)} FROM {table_name}")  # nosec B608

        def _done(df):
            QApplication.restoreOverrideCursor()
            if df.empty:
                QMessageBox.information(self, "Copy Script", f"'{table_name}' has no rows to script.")
                return
            QApplication.clipboard().setText(_to_sql_inserts(df, table_name, dialect=dialect))

        def _error(msg):
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Copy Script Error", f"Could not read table:\n{msg}")

        QApplication.setOverrideCursor(Qt.WaitCursor)
        self._run_bg_db(_fetch, _done, _error)

    def _export_table_data_only(self, table_name: str):
        """Export just the rows (context menu's "Export Table Data") without
        the ExportScopeDialog's structure/data/both prompt — same CSV/JSON/
        Excel/SQL-inserts picker _export_table() already uses for "data".
        Issue #237: the full-table SELECT runs on a dedicated background
        connection instead of blocking the UI thread."""
        def _fetch(db):
            return db.execute_query(f"SELECT * FROM {table_name}")  # nosec B608

        def _done(df):
            QApplication.restoreOverrideCursor()
            export_dataframe(self, df, f"{table_name}.csv", table_name)

        def _error(msg):
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Export Error", f"Could not read table:\n{msg}")

        QApplication.setOverrideCursor(Qt.WaitCursor)
        self._run_bg_db(_fetch, _done, _error)

    def _export_table_as_sql(self, table_name: str):
        """Export structure + data as a single .sql file (context menu's
        "Export Table as SQL") — skips ExportScopeDialog since structure and
        content are both implied, no drop, safe/default advanced options."""
        from PySide6.QtWidgets import QFileDialog
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export Table as SQL", f"{table_name}.sql", "SQL Dump (*.sql)"
        )
        if not file_path:
            return
        try:
            with open(file_path, "w", encoding="utf-8") as fh:
                self._write_table_export(fh, table_name, {"structure": True, "content": True, "drop": False})
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

        # Issue #237: the full-table SELECT runs on a dedicated background
        # connection instead of blocking the UI thread.
        def _fetch(db):
            return db.execute_query(f"SELECT {cols_sql} FROM {table_name}")  # nosec B608

        def _done(df):
            QApplication.restoreOverrideCursor()
            export_dataframe(self, df, f"{table_name}.csv", table_name)

        def _error(msg):
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Export Error", f"Could not read table:\n{msg}")

        QApplication.setOverrideCursor(Qt.WaitCursor)
        self._run_bg_db(_fetch, _done, _error)

    def _new_table(self):
        """StructureEditorDialog already supports a "New Table" mode
        (table_name=None) — same dialog show_alter_table_editor() uses for
        Alter, just without existing columns to seed it."""
        dialog = StructureEditorDialog(db_type=self.db_service.db_type, parent=self)
        if not dialog.exec():
            return
        sql = dialog.get_sql()
        if not sql.strip() or sql.strip().startswith("--"):
            return
        if not self._guard_write(sql):
            return

        def _run(db):
            for stmt in query_classifier.split_statements(sql):
                db.execute_update(stmt)

        def _done(_):
            QMessageBox.information(self, "Success", "Table created successfully.")
            self.load_schema()

        def _error(msg):
            QMessageBox.critical(self, "Error", msg)

        # Issue #237: DDL runs on a dedicated background connection.
        self._run_bg_db(_run, _done, _error)

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

        def _done(_):
            QMessageBox.information(self, "Success", f"'{table_name}' cloned to '{new_name}'.")
            self.load_schema()

        def _error(msg):
            QMessageBox.critical(self, "Clone Table Failed", msg)

        # Issue #237: CREATE TABLE ... AS SELECT runs on a dedicated
        # background connection — on a large source table this can take a
        # while server-side.
        self._run_bg_db(lambda db: db.execute_update(sql), _done, _error)

    def _truncate_table(self, table_name: str):
        quoted = self._qualified_name(table_name, quote=True)
        sql = f"TRUNCATE TABLE {quoted}"  # nosec B608
        if not self._guard_write(sql):
            return
        reply = QMessageBox.question(
            self, "Truncate Table",
            f"This permanently deletes ALL rows in '{table_name}'. This cannot be undone.\n\n"
            f"Execute the following SQL?\n\n{sql}",
            QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        def _done(_):
            QMessageBox.information(self, "Success", f"'{table_name}' truncated.")
            self._refresh_open_table_tab(table_name)

        def _error(msg):
            QMessageBox.critical(self, "Truncate Failed", msg)

        # Issue #237: TRUNCATE runs on a dedicated background connection —
        # can take a while on a huge table.
        self._run_bg_db(lambda db: db.execute_update(sql), _done, _error)

    def _delete_table(self, table_name: str):
        kind = "VIEW" if self._active_category == "views" else "TABLE"
        quoted = self._qualified_name(table_name, quote=True)
        sql = f"DROP {kind} {quoted}"
        if not self._guard_write(sql):
            return

        # Issue #236: silent check (no upgrade nag — this runs on every
        # drop, not just an explicit Impact Analysis click). Cheap metadata
        # query — left on the main connection like the other metadata-only
        # calls (issue #237's "lowest priority" tier).
        impact = ""
        if entitlements.is_enabled(Feature.IMPACT_ANALYSIS):
            report = dependency_analyzer.find_table_dependents(self.db_service, table_name)
            text = impact_warning_text(report)
            if text:
                impact = text + "\n\n"

        reply = QMessageBox.question(
            self, f"Delete {kind.title()}",
            f"{impact}This permanently drops '{table_name}' and all its data. This cannot be undone.\n\n"
            f"Execute the following SQL?\n\n{sql}",
            QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        def _done(_):
            QMessageBox.information(self, "Success", f"'{table_name}' deleted.")
            for i in range(self.tabs.count()):
                w = self.tabs.widget(i)
                if isinstance(w, TableViewWidget) and w.table_name == table_name:
                    self.tabs.removeTab(i)
                    break
            self.load_schema()

        def _error(msg):
            QMessageBox.critical(self, "Delete Failed", msg)

        # Issue #237: DROP/TRUNCATE runs on a dedicated background
        # connection — can take a while on a huge table.
        self._run_bg_db(lambda db: db.execute_update(sql), _done, _error)

    def _refresh_open_table_tab(self, table_name: str):
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, TableViewWidget) and w.table_name == table_name:
                w.current_page = 1
                w.load_table_data()
                break

    # ─── Quick search ─────────────────────────────────────────────────────────

    def _gather_quick_search_items(self):
        """Build the default (item_type, display_text, payload) list for the
        command palette: schema items, recent query history, and SQL
        snippets. Rebuilt on every open since history/snippets change
        independently of schema reloads. Columns are deliberately excluded
        (issue #241): they used to dominate this list by sheer volume,
        diluting table/view search — see _gather_column_items() for the
        explicit "c:"-prefixed column search instead."""
        items = [(item_type, name, None) for item_type, name in self.all_schema_items]

        for entry in self.query_history.get_recent_queries(limit=100):
            query = entry.get("query", "").strip()
            if query:
                items.append(("history", query.replace("\n", " ")[:80], query))

        for trigger, data in SnippetManager().get_all().items():
            label = f"{trigger} — {data.get('name', trigger)}"
            items.append(("snippet", label, data.get("body", "")))

        return items

    def _gather_column_items(self):
        """Column-only items for Quick Search's "c:" filter (issue #241)."""
        items = []
        for table, cols in self._column_cache.items():
            for col in cols:
                items.append(("column", f"{table}.{col}", col))
        return items

    def _gather_recency_scores(self):
        """{(item_type, display_text): score} for Quick Search tie-breaking
        (issue #242) — higher score means more recently used. Table/view
        opens use the monotonic counter from _record_recent_table_open();
        history entries use their position in the already-newest-first
        history list, since that ordering *is* their recency."""
        scores = {}
        for name, counter in self._recent_table_opens.items():
            item_type = "view" if name in self.all_views else "table"
            scores[(item_type, name)] = counter

        recent_queries = self.query_history.get_recent_queries(limit=100)
        for idx, entry in enumerate(recent_queries):
            query = entry.get("query", "").strip()
            if query:
                display_text = query.replace("\n", " ")[:80]
                scores[("history", display_text)] = len(recent_queries) - idx
        return scores

    def _gather_recent_items(self, limit=8):
        """(item_type, display_text, payload) tuples for the "Recent" list
        shown when Quick Search opens with nothing typed (issue #242):
        the most recently opened tables/views, newest first."""
        recent_names = sorted(
            self._recent_table_opens, key=self._recent_table_opens.get, reverse=True
        )[:limit]
        items = []
        for name in recent_names:
            item_type = "view" if name in self.all_views else "table"
            items.append((item_type, name, None))
        return items

    def show_quick_search(self):
        items = self._gather_quick_search_items()
        column_items = self._gather_column_items()
        if not items and not column_items:
            QMessageBox.information(self, "No Items", "Nothing to search yet")
            return
        dialog = QuickSearchDialog(
            items, self, column_items=column_items,
            recency_scores=self._gather_recency_scores(),
            recent_items=self._gather_recent_items(),
        )
        dialog.item_selected.connect(self._on_quick_search)
        dialog.exec()

    def _active_sql_tab(self):
        """Return the current tab if it's a SQL editor, else the most
        recently opened SQL tab, else a fresh one. Returns None only if
        there's no existing SQL tab to fall back to *and* the Free-tier tab
        cap (issue #154) blocks opening a new one — callers must handle
        that case rather than assume a tab is always available."""
        current = self.tabs.currentWidget()
        if isinstance(current, SqlTab):
            return current
        for i in range(self.tabs.count() - 1, -1, -1):
            w = self.tabs.widget(i)
            if isinstance(w, SqlTab):
                # Found one, but it's not the visible tab (e.g. a table
                # Data/Structure view is active) — bring it to front so
                # callers that set/insert into it don't silently write to
                # a tab the user isn't looking at (issue #175).
                self.tabs.setCurrentWidget(w)
                return w
        return self.add_new_tab()

    def _on_quick_search(self, item_type, display_text, payload):
        if item_type in ("table", "view"):
            self.open_table_view(display_text)
        elif item_type == "history":
            tab = self._active_sql_tab()
            if tab:
                tab.set_query(payload)
        else:  # function, column, snippet — insert at cursor
            tab = self._active_sql_tab()
            if tab:
                tab.insert_text_at_cursor(payload or display_text)

    # ─── Query history ────────────────────────────────────────────────────────

    def show_query_history(self):
        dialog = QueryHistoryDialog(self.query_history, self)
        if dialog.exec():
            query = dialog.get_selected_query()
            if query:
                tab = self._active_sql_tab()
                if tab:
                    tab.set_query(query)

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

    @staticmethod
    def _history_cost_color(score: int) -> str:
        """Mirrors SqlTab._cost_badge_color so the history row's cost tint
        and the status-bar badge it came from agree on what "expensive"
        looks like."""
        if score == 0:
            return "#30d158"
        if score < 20:
            return "#0A84FF"
        if score < 50:
            return "#ff9f0a"
        return "#ff453a"

    def _reload_history_list(self, filter_text: str = ''):
        from PySide6.QtGui import QBrush, QColor

        self._history_list.clear()
        entries = self.query_history.get_recent_queries(limit=100)
        ft = filter_text.lower()
        for entry in entries:
            q = entry.get('query', '').strip()
            ts = entry.get('timestamp', '')
            rows = entry.get('rows', '')
            cost_score = entry.get('cost_score')
            cost_label = entry.get('cost_label')
            if ft and ft not in q.lower():
                continue
            display = q.replace('\n', ' ')[:80]
            tooltip = f'{ts}  |  {rows} rows'
            if cost_label is not None:
                display = f'●  {display}'
                tooltip += f'  |  Cost: {cost_score} ({cost_label})'
            if entry.get('profile_detail'):
                tooltip += '  |  Profiled'
            tooltip += f'\n\n{q}'
            item = QListWidgetItem(display)
            item.setToolTip(tooltip)
            # The whole entry, not just the query text — _show_history_
            # context_menu's "View Cost & Profile…" needs cost_detail too.
            item.setData(Qt.UserRole, entry)
            if cost_score is not None:
                item.setForeground(QBrush(QColor(self._history_cost_color(cost_score))))
            self._history_list.addItem(item)

    def _filter_history_list(self, text: str):
        self._reload_history_list(filter_text=text)

    def _use_history_item(self, item: QListWidgetItem):
        entry = item.data(Qt.UserRole) or {}
        query = entry.get('query')
        if not query:
            return
        tab = self._active_sql_tab()
        if not tab:
            return
        tab.set_query(query)

    def _show_history_context_menu(self, pos):
        item = self._history_list.itemAt(pos)
        if item is None:
            return
        entry = item.data(Qt.UserRole) or {}
        query = entry.get('query')
        if not query:
            return
        menu = QMenu(self)
        load_action = menu.addAction("Load into Tab")
        analyze_action = menu.addAction("View Cost && Profile…")
        action = menu.exec(self._history_list.mapToGlobal(pos))
        if action == load_action:
            self._use_history_item(item)
        elif action == analyze_action:
            self.open_query_analyzer(
                focus_cost_tab=True, query=query,
                cost_detail=entry.get('cost_detail'),
                profile_detail=entry.get('profile_detail'),
                history_entry_id=entry.get('id'),
            )

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
        tab = self._active_sql_tab()
        if not tab:
            return
        tab.set_query(query)
        name = entry.get("name", "").strip()
        if name:
            self.tabs.setTabText(self.tabs.indexOf(tab), name)

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
        if not require_under_limit(
            Limit.SAVED_QUERIES, len(self.saved_queries.queries), "saved queries", self,
        ):
            return
        name, ok = QInputDialog.getText(self, "Save Query", "Name:")
        if ok and name.strip():
            self.saved_queries.add(name.strip(), query)
            self.tabs.setTabText(self.tabs.indexOf(tab), name.strip())
            self._switch_sidebar(1)

    def _open_query_library(self):
        dialog = QueryLibraryDialog(self.saved_queries, self)
        if dialog.exec():
            query = dialog.get_selected_query()
            if query:
                tab = self._active_sql_tab()
                if tab:
                    tab.set_query(query)
                    name = (dialog.get_selected_name() or "").strip()
                    if name:
                        self.tabs.setTabText(self.tabs.indexOf(tab), name)
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
        default_color = ThemeManager.D_TEXT if self.current_theme == "dark" else ThemeManager.L_TEXT
        for name, item in items_map.items():
            item.setHidden(False)
            item.setText(0, name)   # clear previous highlight
            item.setForeground(0, QBrush(QColor(default_color)))

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
            style = """
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
            """
        else:
            style = """
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
            """
        self.db_pill.setStyleSheet(style)
        self.schema_pill.setStyleSheet(style)

    def _style_schema_progress_bar(self, is_dark: bool):
        """QSS for the schema-loading progress bar + its status label — set
        directly (not via the app-level theme stylesheet) like the
        pill/toggle buttons above."""
        if is_dark:
            bg, text, chunk = "#2c2c2e", "#8e8e93", "#0A84FF"
        else:
            bg, text, chunk = "#f2f2f7", "#6e6e73", "#007AFF"
        self._schema_progress_bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: {bg};
                border: none;
            }}
            QProgressBar::chunk {{
                background-color: {chunk};
            }}
        """)
        self._schema_loading_label.setStyleSheet(f"""
            QLabel {{
                background-color: {bg};
                color: {text};
                font-size: 11px;
            }}
        """)

    @staticmethod
    def _toggle_style_for(is_dark: bool) -> str:
        """QSS for the Schema/Queries/History toggle buttons — set directly
        per-widget (not via the app-level theme stylesheet), so it has to be
        regenerated on every theme switch rather than relying on the QSS
        cascade to pick up the new palette."""
        if is_dark:
            text, checked, hover = "#8e8e93", "#e5e5ea", "#c7c7cc"
        else:
            text, checked, hover = "#6e6e73", "#1c1c1e", "#3a3a3c"
        return f"""
            QPushButton {{
                background: transparent;
                color: {text};
                border: none;
                border-bottom: 2px solid transparent;
                padding: 4px 12px;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton:checked {{
                color: {checked};
                border-bottom: 2px solid #0A84FF;
            }}
            QPushButton:hover:!checked {{ color: {hover}; }}
        """

    def update_theme(self, is_dark: bool):
        self.current_theme = "dark" if is_dark else "light"
        self._apply_pill_style()
        self._style_schema_progress_bar(is_dark)
        # Schema tree item text color, and the category-row / sidebar-toggle
        # label colors below, are all set directly (not via the app-level
        # theme stylesheet), so each has to be explicitly refreshed here
        # rather than relying on the QSS cascade to pick up the new palette.
        self.filter_tables(self.table_search.text())
        self._update_category_row_styles()
        toggle_style = self._toggle_style_for(is_dark)
        self._schema_btn.setStyleSheet(toggle_style)
        self._queries_btn.setStyleSheet(toggle_style)
        self._history_btn.setStyleSheet(toggle_style)

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
        """Reopen tabs from saved session data. Stops once the tab cap
        (issue #154 — query tabs and table tabs share Limit.MAX_QUERY_TABS)
        is hit, rather than repeatedly failing (or popping the upgrade
        prompt) for the rest of a silent startup restore — a Free session
        saved with more tabs than the cap simply restores as many as fit."""
        for tab in tabs:
            try:
                if tab.get("type") == "table":
                    name = tab.get("name", "")
                    if name in self.all_tables:
                        if not self._under_tab_limit(silent=True):
                            break
                        self.open_table_view(name, silent=True)
                elif tab.get("type") == "query":
                    w = self.add_new_tab(silent=True)
                    if w is None:
                        break
                    idx = self.tabs.indexOf(w)
                    label = tab.get("name", f"Tab {idx + 1}")
                    self.tabs.setTabText(idx, label)
                    if tab.get("query"):
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
            self._emit_health('idle')
            db_type = self.config.get('type', 'DB').upper()
            self.reconnected.emit(f"Reconnected to {db_type} — {self.label}")
            # Any table tab still stuck on a stale connection error (from
            # before this reconnect, or from session restore racing an
            # earlier connect) can now be retried (issue #176).
            self._reload_errored_table_tabs()
        except Exception as ex:
            QMessageBox.critical(self, "Reconnect Failed", str(ex))
            self._emit_health('disconnected')

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
                self._emit_health(status)
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
        """Reopen pinned tabs from pinned_tabs.json (called on startup).
        Stops once the Free-tier tab cap (issue #154) is hit — same
        silent-restore treatment as restore_session_tabs."""
        from utils import pinned_tabs as _pt
        conn_name = self.label
        pinned_list = _pt.load().get(conn_name, [])
        for entry in pinned_list:
            w = self.add_new_tab(silent=True)
            if w is None:
                break
            idx = self.tabs.indexOf(w)
            name = entry.get("name", f"Tab {idx + 1}")
            self.tabs.setTabText(idx, f"★ {name}")
            if entry.get('query'):
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

    def _reload_errored_table_tabs(self):
        """Retry every open TableViewWidget currently stuck on a connection
        error, now that a (re)connect has actually completed (issue #176).
        A no-op for tabs that already loaded real data — mirrors the
        tx-cleanup loop in disconnect() below, just for the opposite
        direction (connection coming back, not going away)."""
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, TableViewWidget):
                w.reload_if_errored()

    def _emit_health(self, status: str):
        """Single choke point for health_changed — keeps self._last_health
        (read by _check_health's was-disconnected check, and by every new
        SqlTab's initial status-bar state, issue #178) in sync with every
        emission instead of just the _check_health polling path that used
        to be the only writer of it."""
        self._last_health = status
        self.health_changed.emit(status)

    def _update_tab_status_bars(self, status: str):
        """Fan health_changed out to every open SqlTab's bottom status bar
        (issue #178) — one connection per panel, shared by every tab."""
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, SqlTab):
                w.set_connection_state(status)

    def _dialect_display_name(self) -> str:
        return {"mysql": "MySQL", "postgresql": "PostgreSQL"}.get(
            self.config.get("type", ""), self.config.get("type", "DB").upper())

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
