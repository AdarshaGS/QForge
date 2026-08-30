"""Data Compare — read-only row-level data diff between two saved connections
(issue #204, design note ai/data-compare-design.md), rendering the diff built
by services/data_diff.py.

Modeled directly on ui/schema_compare_dialog.py's SchemaCompareDialog: same
Source/Target connection-picker pattern, same background-thread compare flow,
same Added/Removed/Modified tree with old→new cell rendering — extended here
to row-level (not column/index-level) changes, plus a Table/Query mode
toggle, a per-side database picker (one saved connection can point at a host
with several databases), and an optional key-column field — see
services/data_diff.py's module docstring for what an empty key falls back
to.

No schema/data-modifying actions live here — like SchemaCompareDialog, this
only ever reads from the two selected connections."""
import threading

from PySide6.QtCore import Signal
from PySide6.QtGui import QBrush
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QPushButton,
    QComboBox, QLabel, QLineEdit, QPlainTextEdit, QSpinBox, QCheckBox,
    QTreeWidget, QTreeWidgetItem, QMessageBox, QStackedWidget, QWidget,
)

from services.data_diff import build_data_diff, table_select_sql
from services.db_service import DbService
from ui.connection_dialog import ConnectionDialog
from ui.schema_compare_dialog import _CHANGE_COLORS, _load_connection_profiles, _profile_label
from utils.logger import get_logger

logger = get_logger()

_DEFAULT_ROW_LIMIT = 50000


class DataCompareDialog(QDialog):
    """Opens standalone — the caller only supplies which connection id to
    preselect as Source. Builds its own dedicated connections for both
    sides via services.data_diff.build_data_diff — never touches a
    caller's live connection."""

    _diff_loaded = Signal(object)
    _diff_load_error = Signal(str)
    _databases_loaded = Signal(object, object, dict, list, str)
    _tables_loaded = Signal(object, list, str)
    _pk_loaded = Signal(list)

    def __init__(self, current_connection_id: str = "", is_dark: bool = True, parent=None):
        super().__init__(parent)
        self._is_dark = is_dark
        self.setWindowTitle("Data Compare")
        self.resize(920, 680)

        layout = QVBoxLayout(self)

        picker_row = QFormLayout()
        self.source_combo = QComboBox()
        self.target_combo = QComboBox()
        # A saved connection profile is host-level and can hold several
        # databases (issue feedback: one MySQL/Postgres host, many DBs) — this
        # combo lets the user pick which one on that host to actually compare,
        # instead of being stuck with whatever "database" happened to be
        # saved on the profile. Left disabled/empty for sqlite, where the
        # connection *is* a single database file (same distinction
        # ConnectionPanel's own DbSwitcherDialog already makes).
        self.source_db_combo = QComboBox()
        self.source_db_combo.setEnabled(False)
        self.target_db_combo = QComboBox()
        self.target_db_combo.setEnabled(False)

        source_row = QHBoxLayout()
        source_row.addWidget(self.source_combo, 1)
        source_row.addWidget(QLabel("DB:"))
        source_row.addWidget(self.source_db_combo, 1)
        picker_row.addRow("Source:", source_row)

        target_row = QHBoxLayout()
        target_row.addWidget(self.target_combo, 1)
        target_row.addWidget(QLabel("DB:"))
        target_row.addWidget(self.target_db_combo, 1)
        picker_row.addRow("Target:", target_row)
        layout.addLayout(picker_row)

        self._profiles = _load_connection_profiles()
        self._populate_combo(self.source_combo, preselect_id=current_connection_id)
        self._populate_combo(self.target_combo, preselect_id="")

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Table", "Custom Query"])
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        mode_row = QFormLayout()
        mode_row.addRow("Mode:", self.mode_combo)
        layout.addLayout(mode_row)

        self.mode_stack = QStackedWidget()
        layout.addWidget(self.mode_stack)

        table_page = QWidget()
        table_form = QFormLayout(table_page)
        self.source_table_combo = QComboBox()
        self.target_table_combo = QComboBox()
        table_form.addRow("Source table:", self.source_table_combo)
        table_form.addRow("Target table:", self.target_table_combo)
        self.mode_stack.addWidget(table_page)

        query_page = QWidget()
        query_form = QFormLayout(query_page)
        self.source_query = QPlainTextEdit()
        self.source_query.setPlaceholderText("SELECT ... FROM ...")
        self.source_query.setMaximumHeight(70)
        self.target_query = QPlainTextEdit()
        self.target_query.setPlaceholderText("SELECT ... FROM ...")
        self.target_query.setMaximumHeight(70)
        query_form.addRow("Source query:", self.source_query)
        query_form.addRow("Target query:", self.target_query)
        self.mode_stack.addWidget(query_page)

        key_row = QFormLayout()
        self.key_field = QLineEdit()
        self.key_field.setPlaceholderText(
            "Optional — id, or tenant_id, sku for a composite key. Leave blank to compare whole rows.")
        key_row.addRow("Key column(s):", self.key_field)

        limit_row = QHBoxLayout()
        self.row_limit_spin = QSpinBox()
        self.row_limit_spin.setRange(1, 5_000_000)
        self.row_limit_spin.setValue(_DEFAULT_ROW_LIMIT)
        self.row_limit_spin.setSuffix(" rows/side")
        limit_row.addWidget(self.row_limit_spin)
        self.full_table_check = QCheckBox("Full table (no limit)")
        self.full_table_check.toggled.connect(self.row_limit_spin.setDisabled)
        limit_row.addWidget(self.full_table_check)
        limit_row.addStretch()
        key_row.addRow("Row cap:", limit_row)
        layout.addLayout(key_row)

        toolbar = QHBoxLayout()
        self.compare_btn = QPushButton("Compare")
        self.compare_btn.clicked.connect(self._run_compare)
        toolbar.addWidget(self.compare_btn)

        self.filter_box = QLineEdit()
        self.filter_box.setPlaceholderText("Filter by key...")
        self.filter_box.textChanged.connect(self._apply_filter)
        toolbar.addWidget(self.filter_box)

        self.status_label = QLabel("Pick a source and target, then Compare (key column optional).")
        toolbar.addWidget(self.status_label, 1)
        layout.addLayout(toolbar)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Row", "Change", "Detail"])
        self.tree.setColumnWidth(0, 260)
        self.tree.setColumnWidth(1, 100)
        layout.addWidget(self.tree)

        self.source_combo.currentIndexChanged.connect(
            lambda: self._load_databases_and_tables(self.source_combo, self.source_db_combo, self.source_table_combo))
        self.target_combo.currentIndexChanged.connect(
            lambda: self._load_databases_and_tables(self.target_combo, self.target_db_combo, self.target_table_combo))
        self.source_db_combo.currentIndexChanged.connect(
            lambda: self._load_tables(self._effective_config(self.source_combo, self.source_db_combo),
                                       self.source_table_combo))
        self.target_db_combo.currentIndexChanged.connect(
            lambda: self._load_tables(self._effective_config(self.target_combo, self.target_db_combo),
                                       self.target_table_combo))
        self.source_table_combo.currentIndexChanged.connect(self._maybe_autofill_key)

        self._diff_loaded.connect(self._on_diff_loaded)
        self._diff_load_error.connect(self._on_diff_error)
        self._databases_loaded.connect(self._on_databases_loaded)
        self._tables_loaded.connect(self._on_tables_loaded)
        self._pk_loaded.connect(self._on_pk_loaded)

        self._load_databases_and_tables(self.source_combo, self.source_db_combo, self.source_table_combo)
        self._load_databases_and_tables(self.target_combo, self.target_db_combo, self.target_table_combo)

    # ── connection / table pickers ───────────────────────────────────────

    def _populate_combo(self, combo: QComboBox, preselect_id: str):
        combo.addItem("Select a connection…", None)
        ordered = sorted(self._profiles, key=lambda c: _profile_label(c).lower())
        select_index = 0
        for conn in ordered:
            combo.addItem(_profile_label(conn), conn.get("id"))
            if preselect_id and conn.get("id") == preselect_id:
                select_index = combo.count() - 1
        combo.setCurrentIndex(select_index)

    def _resolve_config(self, combo: QComboBox):
        conn_id = combo.currentData()
        if not conn_id:
            return None
        return ConnectionDialog.load_connection_by_id(conn_id)

    def _effective_config(self, conn_combo: QComboBox, db_combo: QComboBox):
        """*conn_combo*'s resolved config, with "database" overridden to
        whatever *db_combo* currently has selected — the host may hold
        several databases, and the saved profile's own "database" is just
        whichever one was picked when the connection was first set up."""
        config = self._resolve_config(conn_combo)
        if config is None:
            return None
        db_name = db_combo.currentText().strip()
        return dict(config, database=db_name) if db_name else config

    def _on_mode_changed(self, index: int):
        self.mode_stack.setCurrentIndex(index)

    def _load_databases_and_tables(self, conn_combo: QComboBox, db_combo: QComboBox, table_combo: QComboBox):
        db_combo.clear()
        db_combo.setEnabled(False)
        table_combo.clear()
        config = self._resolve_config(conn_combo)
        if config is None:
            return

        if config.get("type") == "sqlite":
            # One file == one database — nothing to switch between.
            self._load_tables(config, table_combo)
            return

        sig = self._databases_loaded
        self.status_label.setText("⏳ Loading databases…")

        def _worker():
            names, err = [], ""
            db = DbService()
            try:
                db.connect(config)
                names = db.get_databases()
            except Exception as ex:
                err = str(ex)
                logger.warning(f"Data Compare: failed to list databases: {ex}")
            finally:
                db.disconnect()
            sig.emit(db_combo, table_combo, config, names, err)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_databases_loaded(self, db_combo: QComboBox, table_combo: QComboBox, config: dict,
                              names: list, err: str):
        db_combo.blockSignals(True)
        db_combo.clear()
        db_combo.addItems(names)
        current_db = config.get("database", "")
        if current_db and current_db in names:
            db_combo.setCurrentText(current_db)
        db_combo.setEnabled(bool(names))
        db_combo.blockSignals(False)
        if err:
            self.status_label.setText(f"⚠ Failed to load databases: {err}")
        self._load_tables(dict(config, database=db_combo.currentText() or current_db), table_combo)

    def _load_tables(self, config: dict, table_combo: QComboBox):
        table_combo.clear()
        if config is None:
            return

        sig = self._tables_loaded
        self.status_label.setText("⏳ Loading tables…")

        def _worker():
            db = DbService()
            names, err = [], ""
            try:
                db.connect(config)
                names = sorted(db.get_tables())
            except Exception as ex:
                err = str(ex)
                logger.warning(f"Data Compare: failed to list tables: {ex}")
            finally:
                db.disconnect()
            sig.emit(table_combo, names, err)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_tables_loaded(self, table_combo: QComboBox, names: list, err: str):
        table_combo.clear()
        table_combo.addItems(names)
        if err:
            self.status_label.setText(f"⚠ Failed to load tables: {err}")
        elif not names:
            self.status_label.setText("Connection has no tables, or hasn't loaded yet.")
        else:
            self.status_label.setText("Pick a source and target, then Compare (key column optional).")

    def _maybe_autofill_key(self):
        if self.key_field.text().strip():
            return
        table = self.source_table_combo.currentText()
        config = self._effective_config(self.source_combo, self.source_db_combo)
        if not table or config is None:
            return

        sig = self._pk_loaded

        def _worker():
            db = DbService()
            try:
                db.connect(config)
                pks = db.get_primary_keys(table)
            except Exception as ex:
                pks = []
                logger.debug(f"Data Compare: failed to read primary keys for {table}: {ex}")
            finally:
                db.disconnect()
            sig.emit(pks)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_pk_loaded(self, pks: list):
        if pks and not self.key_field.text().strip():
            self.key_field.setText(", ".join(pks))

    # ── diffing (background thread — same pattern as
    # SchemaCompareDialog._run_compare) ──────────────────────────────────

    def _run_compare(self):
        source_config = self._effective_config(self.source_combo, self.source_db_combo)
        target_config = self._effective_config(self.target_combo, self.target_db_combo)
        if source_config is None or target_config is None:
            QMessageBox.information(self, "Data Compare", "Choose both a source and a target connection.")
            return

        # Key column(s) are optional — an empty key_columns list makes
        # build_data_diff fall back to whole-row matching (see
        # services/data_diff.py module docstring).
        key_columns = [c.strip() for c in self.key_field.text().split(",") if c.strip()]

        if self.mode_combo.currentIndex() == 0:
            source_table = self.source_table_combo.currentText()
            target_table = self.target_table_combo.currentText()
            if not source_table or not target_table:
                QMessageBox.information(self, "Data Compare", "Choose both a source and a target table.")
                return
            source_sql = table_select_sql(source_table, source_config.get("type"))
            target_sql = table_select_sql(target_table, target_config.get("type"))
        else:
            source_sql = self.source_query.toPlainText().strip()
            target_sql = self.target_query.toPlainText().strip()
            if not source_sql or not target_sql:
                QMessageBox.information(self, "Data Compare", "Enter both a source and a target query.")
                return

        row_limit = 0 if self.full_table_check.isChecked() else self.row_limit_spin.value()
        if self.full_table_check.isChecked():
            proceed = QMessageBox.question(
                self, "Data Compare",
                "Comparing full tables with no row cap can load large result sets into memory. Continue?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if proceed != QMessageBox.Yes:
                return

        self.compare_btn.setEnabled(False)
        self.status_label.setText("⏳ Comparing data…")
        self.tree.clear()

        sig_done = self._diff_loaded
        sig_error = self._diff_load_error

        def _worker():
            try:
                sig_done.emit(build_data_diff(source_config, target_config, source_sql, target_sql,
                                               key_columns, row_limit=row_limit))
            except Exception as ex:
                sig_error.emit(str(ex))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_diff_error(self, msg: str):
        self.compare_btn.setEnabled(True)
        self.status_label.setText(f"⚠ Failed to compare data: {msg}")

    def _on_diff_loaded(self, diff):
        self.compare_btn.setEnabled(True)
        status = (f"{diff.rows_added_total} added, {diff.rows_removed_total} removed, "
                   f"{diff.rows_modified_total} modified, {diff.rows_unchanged} unchanged")
        if diff.truncated_source or diff.truncated_target:
            status += "  —  ⚠ row cap reached on " + " and ".join(
                s for s, t in (("source", diff.truncated_source), ("target", diff.truncated_target)) if t)
        self.status_label.setText(status)
        self._build_tree(diff)

    # ── tree rendering ────────────────────────────────────────────────

    def _colored_item(self, parent, text: str, change: str, detail: str = ""):
        item = QTreeWidgetItem(parent, [text, change.capitalize(), detail])
        color = _CHANGE_COLORS[self._is_dark][change]
        item.setForeground(1, QBrush(color))
        return item

    def _build_tree(self, diff):
        self.tree.clear()

        if diff.rows_added:
            added_root = QTreeWidgetItem(self.tree, [f"Added ({diff.rows_added_total})", "", ""])
            for row_diff in diff.rows_added:
                self._colored_item(added_root, row_diff.key, "added", self._row_summary(row_diff.target_row))

        if diff.rows_removed:
            removed_root = QTreeWidgetItem(self.tree, [f"Removed ({diff.rows_removed_total})", "", ""])
            for row_diff in diff.rows_removed:
                self._colored_item(removed_root, row_diff.key, "removed", self._row_summary(row_diff.source_row))

        if diff.rows_modified:
            modified_root = QTreeWidgetItem(self.tree, [f"Modified ({diff.rows_modified_total})", "", ""])
            for row_diff in diff.rows_modified:
                row_item = self._colored_item(modified_root, row_diff.key, "modified")
                for col_name, (old, new) in row_diff.field_changes.items():
                    QTreeWidgetItem(row_item, [col_name, "", f"{old} → {new}"])
                row_item.setExpanded(True)

        self.tree.expandAll()

    @staticmethod
    def _row_summary(row: dict) -> str:
        if not row:
            return ""
        parts = [f"{k}={v}" for k, v in list(row.items())[:4]]
        return ", ".join(parts)

    # ── filter ────────────────────────────────────────────────────────

    def _apply_filter(self, text: str):
        text = text.strip().lower()
        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            bucket = root.child(i)  # Added/Removed/Modified
            visible_count = 0
            for j in range(bucket.childCount()):
                row_item = bucket.child(j)
                match = (not text) or (text in row_item.text(0).lower())
                row_item.setHidden(not match)
                if match:
                    visible_count += 1
            bucket.setHidden(visible_count == 0)
