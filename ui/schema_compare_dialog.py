"""Schema Compare — read-only structural diff between two saved connections
(issue #68), rendering the diff built by services/schema_diff.py, plus an
optional "Generate Migration SQL" step (issue #69) that turns that diff into
a reviewable DDL script via services/schema_migration.py.

No schema-modifying actions live here, mirroring ui/erd_dialog.py's stated
scope for the ER diagram: this dialog (and the migration-review dialog it
opens) only ever reads metadata from the two selected connections and never
writes to either database — the generated SQL is text for the user to copy,
export, and run themselves wherever they choose.
"""
import json
import os
import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QBrush, QFont
from PySide6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QPushButton,
    QComboBox, QLabel, QLineEdit, QTreeWidget, QTreeWidgetItem, QMessageBox,
    QPlainTextEdit, QFileDialog, QMenu,
)

from services.schema_diff import build_schema_diff
from services.schema_migration import generate_migration_sql
from ui.connection_dialog import ConnectionDialog

# (is_dark) -> {change -> color}. Chosen to stay legible against both a dark
# (#1c1c1e-ish) and light (#ffffff-ish) tree background.
_CHANGE_COLORS = {
    True: {"added": QColor("#30D158"), "removed": QColor("#FF453A"), "modified": QColor("#FF9F0A")},
    False: {"added": QColor("#1b8a3a"), "removed": QColor("#c62828"), "modified": QColor("#b8720b")},
}


def _load_connection_profiles() -> list:
    """Raw (credential-free) connection list for populating the source/
    target pickers — full credentials are only resolved for the two
    connections actually chosen, at Compare time (see _resolve_config).

    Only used for display labels, so this doesn't need the full field
    validation ConnectionDialog._sanitize_connection_entry does — but a
    hand-edited or malicious connections.json (issue #116) can still put
    a non-list at the top level or non-dict entries in it, which would
    otherwise crash _profile_label's/.get() calls below. Drop anything
    that isn't a dict rather than trusting the file's shape."""
    path = ConnectionDialog.CONNECTION_FILE
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r") as f:
            raw = json.load(f)
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    return [conn for conn in raw if isinstance(conn, dict)]


def _profile_label(conn: dict) -> str:
    name = conn.get("name") or conn.get("host") or conn.get("id", "")
    db = conn.get("database")
    group = (conn.get("group") or "").strip()
    label = f"{name} — {db}" if db else name
    return f"[{group}] {label}" if group and group != "Default" else label


class SchemaCompareDialog(QDialog):
    """Opens standalone — the caller only supplies which connection id to
    preselect as Source (typically the panel it was opened from). Builds
    its own dedicated connections for both sides via
    services.schema_diff.build_schema_diff — never touches a caller's live
    connection."""

    _diff_loaded = Signal(object)
    _diff_load_error = Signal(str)
    _migration_ready = Signal(str)
    _migration_error = Signal(str)

    def __init__(self, current_connection_id: str = "", is_dark: bool = True, parent=None):
        super().__init__(parent)
        self._is_dark = is_dark
        self._last_diff = None
        self._source_config = None
        self._target_config = None
        self._target_label = ""
        self._migration_table_name = None
        self.setWindowTitle("Schema Compare")
        self.resize(900, 650)

        layout = QVBoxLayout(self)

        picker_row = QFormLayout()
        self.source_combo = QComboBox()
        self.target_combo = QComboBox()
        picker_row.addRow("Source:", self.source_combo)
        picker_row.addRow("Target:", self.target_combo)
        layout.addLayout(picker_row)

        self._profiles = _load_connection_profiles()
        self._populate_combo(self.source_combo, preselect_id=current_connection_id)
        self._populate_combo(self.target_combo, preselect_id="")

        toolbar = QHBoxLayout()
        self.compare_btn = QPushButton("Compare")
        self.compare_btn.clicked.connect(self._run_compare)
        toolbar.addWidget(self.compare_btn)

        self.migrate_btn = QPushButton("Generate Migration SQL")
        self.migrate_btn.setEnabled(False)
        self.migrate_btn.setToolTip("Run Compare first")
        # Wrapped in a lambda — QPushButton.clicked emits a `checked` bool
        # that would otherwise land in _generate_migration's table_name arg.
        self.migrate_btn.clicked.connect(lambda: self._generate_migration())
        toolbar.addWidget(self.migrate_btn)

        self.filter_box = QLineEdit()
        self.filter_box.setPlaceholderText("Filter tables...")
        self.filter_box.textChanged.connect(self._apply_filter)
        toolbar.addWidget(self.filter_box)

        self.status_label = QLabel("Pick a source and target, then Compare.")
        toolbar.addWidget(self.status_label, 1)
        layout.addLayout(toolbar)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Object", "Change", "Detail"])
        self.tree.setColumnWidth(0, 320)
        self.tree.setColumnWidth(1, 100)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_tree_context_menu)
        layout.addWidget(self.tree)

        self._diff_loaded.connect(self._on_diff_loaded)
        self._diff_load_error.connect(self._on_diff_error)
        self._migration_ready.connect(self._on_migration_ready)
        self._migration_error.connect(self._on_migration_error)

    # ── connection pickers ───────────────────────────────────────────

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

    # ── diffing (background thread — same pattern as
    # ErdDialog._reload) ────────────────────────────────────────────

    def _run_compare(self):
        source_config = self._resolve_config(self.source_combo)
        target_config = self._resolve_config(self.target_combo)
        if source_config is None or target_config is None:
            QMessageBox.information(self, "Schema Compare", "Choose both a source and a target connection.")
            return

        self.compare_btn.setEnabled(False)
        self.migrate_btn.setEnabled(False)
        self.status_label.setText("⏳ Comparing schemas…")
        self.tree.clear()

        self._source_config = source_config
        self._target_config = target_config
        self._target_label = self.target_combo.currentText()
        self._last_diff = None

        sig_done = self._diff_loaded
        sig_error = self._diff_load_error

        def _worker():
            try:
                sig_done.emit(build_schema_diff(source_config, target_config))
            except Exception as ex:
                sig_error.emit(str(ex))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_diff_error(self, msg: str):
        self.compare_btn.setEnabled(True)
        self.status_label.setText(f"⚠ Failed to compare schemas: {msg}")

    def _on_diff_loaded(self, diff):
        self.compare_btn.setEnabled(True)
        self.status_label.setText(
            f"{len(diff.tables_added)} added, {len(diff.tables_removed)} removed, "
            f"{len(diff.tables_modified)} modified, {diff.tables_unchanged} unchanged")
        self._build_tree(diff)
        self._last_diff = diff
        has_changes = bool(diff.tables_added or diff.tables_removed or diff.tables_modified)
        self.migrate_btn.setEnabled(has_changes)
        self.migrate_btn.setToolTip("" if has_changes else "No differences to migrate")

    # ── migration SQL (issue #69) ────────────────────────────────────

    def _generate_migration(self, table_name: str = None):
        if self._last_diff is None:
            return
        self.migrate_btn.setEnabled(False)
        self.migrate_btn.setText("⏳ Generating…")

        diff, source_config, target_config = self._last_diff, self._source_config, self._target_config
        self._migration_table_name = table_name

        def _worker():
            try:
                sql = generate_migration_sql(diff, source_config, target_config, table_name=table_name)
                self._migration_ready.emit(sql)
            except Exception as ex:
                self._migration_error.emit(str(ex))

        threading.Thread(target=_worker, daemon=True).start()

    def _show_tree_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        name = item.data(0, Qt.UserRole) if item else None
        if not name:
            return
        menu = QMenu(self)
        action = menu.addAction(f"Generate Migration SQL for '{name}'")
        action.triggered.connect(lambda: self._generate_migration(table_name=name))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _on_migration_ready(self, sql: str):
        self.migrate_btn.setEnabled(True)
        self.migrate_btn.setText("Generate Migration SQL")
        label = self._target_label
        if self._migration_table_name:
            label = f"{label} — table `{self._migration_table_name}`"
        dialog = _MigrationReviewDialog(sql, label, self._is_dark, self)
        dialog.exec()

    def _on_migration_error(self, msg: str):
        self.migrate_btn.setEnabled(True)
        self.migrate_btn.setText("Generate Migration SQL")
        QMessageBox.warning(self, "Generate Migration SQL", f"Failed to generate migration SQL: {msg}")

    # ── tree rendering ────────────────────────────────────────────────

    def _colored_item(self, parent, text: str, change: str, detail: str = ""):
        item = QTreeWidgetItem(parent, [text, change.capitalize(), detail])
        color = _CHANGE_COLORS[self._is_dark][change]
        item.setForeground(1, QBrush(color))
        return item

    def _build_tree(self, diff):
        self.tree.clear()

        tables_root = QTreeWidgetItem(self.tree, ["Tables", "", ""])
        f = tables_root.font(0)
        f.setBold(True)
        tables_root.setFont(0, f)

        # Table-level rows (but not their Columns/Indexes/FK children) carry
        # their table name in Qt.UserRole — _show_tree_context_menu uses
        # that to offer "Generate Migration SQL" scoped to just that table.
        if diff.tables_added:
            added_root = QTreeWidgetItem(tables_root, [f"Added ({len(diff.tables_added)})", "", ""])
            for name in diff.tables_added:
                item = self._colored_item(added_root, name, "added")
                item.setData(0, Qt.UserRole, name)

        if diff.tables_removed:
            removed_root = QTreeWidgetItem(tables_root, [f"Removed ({len(diff.tables_removed)})", "", ""])
            for name in diff.tables_removed:
                item = self._colored_item(removed_root, name, "removed")
                item.setData(0, Qt.UserRole, name)

        if diff.tables_modified:
            modified_root = QTreeWidgetItem(tables_root, [f"Modified ({len(diff.tables_modified)})", "", ""])
            for table_diff in diff.tables_modified:
                table_item = self._colored_item(modified_root, table_diff.name, "modified")
                table_item.setData(0, Qt.UserRole, table_diff.name)
                self._build_table_detail(table_item, table_diff)

        self.tree.expandItem(tables_root)
        if diff.tables_modified:
            self.tree.expandItem(modified_root)

    def _build_table_detail(self, table_item: QTreeWidgetItem, table_diff):
        if table_diff.columns:
            cols_root = QTreeWidgetItem(table_item, [f"Columns ({len(table_diff.columns)})", "", ""])
            for col_diff in table_diff.columns:
                self._build_column_detail(cols_root, col_diff)

        if table_diff.indexes:
            idx_root = QTreeWidgetItem(table_item, [f"Indexes ({len(table_diff.indexes)})", "", ""])
            for idx_diff in table_diff.indexes:
                self._build_index_detail(idx_root, idx_diff)

        if table_diff.foreign_keys:
            fk_root = QTreeWidgetItem(table_item, [f"Foreign Keys ({len(table_diff.foreign_keys)})", "", ""])
            for fk_diff in table_diff.foreign_keys:
                self._colored_item(fk_root, fk_diff.label, fk_diff.change)

    def _build_column_detail(self, cols_root: QTreeWidgetItem, col_diff):
        if col_diff.change == "added":
            info = col_diff.target
            detail = self._column_summary(info)
            self._colored_item(cols_root, col_diff.name, "added", detail)
        elif col_diff.change == "removed":
            info = col_diff.source
            detail = self._column_summary(info)
            self._colored_item(cols_root, col_diff.name, "removed", detail)
        else:
            col_item = self._colored_item(cols_root, col_diff.name, "modified")
            for field_name, (old, new) in col_diff.field_changes.items():
                QTreeWidgetItem(col_item, [field_name, "", f"{old} → {new}"])
            col_item.setExpanded(True)

    @staticmethod
    def _column_summary(info) -> str:
        if info is None:
            return ""
        parts = [info.data_type or ""]
        parts.append("NOT NULL" if not info.nullable else "NULL")
        if info.is_primary_key:
            parts.append("PK")
        return ", ".join(p for p in parts if p)

    def _build_index_detail(self, idx_root: QTreeWidgetItem, idx_diff):
        if idx_diff.change == "modified":
            idx_item = self._colored_item(idx_root, idx_diff.name, "modified")
            for field_name, (old, new) in idx_diff.field_changes.items():
                QTreeWidgetItem(idx_item, [field_name, "", f"{old} → {new}"])
            idx_item.setExpanded(True)
        else:
            info = idx_diff.target if idx_diff.change == "added" else idx_diff.source
            unique = "UNIQUE " if info and info.get("unique") else ""
            detail = f"{unique}({info.get('columns', '')})" if info else ""
            self._colored_item(idx_root, idx_diff.name, idx_diff.change, detail)

    # ── filter ────────────────────────────────────────────────────────

    def _apply_filter(self, text: str):
        text = text.strip().lower()
        root = self.tree.invisibleRootItem()
        tables_root = root.child(0) if root.childCount() else None
        if tables_root is None:
            return
        for i in range(tables_root.childCount()):
            bucket = tables_root.child(i)  # Added/Removed/Modified
            visible_count = 0
            for j in range(bucket.childCount()):
                table_item = bucket.child(j)
                match = (not text) or (text in table_item.text(0).lower())
                table_item.setHidden(not match)
                if match:
                    visible_count += 1
            bucket.setHidden(visible_count == 0)


class _MigrationReviewDialog(QDialog):
    """Shows generated migration SQL for review — copy/export only, no Run
    button. Executing it (if the user chooses to) happens in a normal SQL
    tab, where the app's existing dangerous-query/read-only guards apply."""

    def __init__(self, sql: str, target_label: str, is_dark: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Migration SQL")
        self.resize(760, 560)
        self._sql = sql

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(f"Target: {target_label}  —  review before running anywhere."))

        self.text = QPlainTextEdit(sql)
        self.text.setStyleSheet("font-family: Menlo, Monaco, 'Courier New', monospace; font-size: 12px;")
        layout.addWidget(self.text)

        btn_row = QHBoxLayout()
        copy_btn = QPushButton("Copy")
        copy_btn.clicked.connect(self._copy)
        btn_row.addWidget(copy_btn)

        export_btn = QPushButton("Export…")
        export_btn.clicked.connect(self._export)
        btn_row.addWidget(export_btn)

        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _copy(self):
        QApplication.clipboard().setText(self.text.toPlainText())

    def _export(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "Export Migration SQL", "migration.sql", "SQL Files (*.sql)")
        if not file_path:
            return
        try:
            with open(file_path, "w") as f:
                f.write(self.text.toPlainText())
        except Exception as ex:
            QMessageBox.warning(self, "Export Migration SQL", f"Failed to save file: {ex}")
