"""Schema Compare — read-only structural diff between two saved connections
(issue #68), rendering the diff built by services/schema_diff.py.

No schema-modifying actions live here, mirroring ui/erd_dialog.py's stated
scope for the ER diagram: this dialog only ever reads metadata from the two
selected connections and never writes to either database.
"""
import json
import os
import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QBrush, QFont
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QPushButton, QComboBox,
    QLabel, QLineEdit, QTreeWidget, QTreeWidgetItem, QMessageBox,
)

from services.schema_diff import build_schema_diff
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
    connections actually chosen, at Compare time (see _resolve_config)."""
    path = ConnectionDialog.CONNECTION_FILE
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return []


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

    def __init__(self, current_connection_id: str = "", is_dark: bool = True, parent=None):
        super().__init__(parent)
        self._is_dark = is_dark
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
        layout.addWidget(self.tree)

        self._diff_loaded.connect(self._on_diff_loaded)
        self._diff_load_error.connect(self._on_diff_error)

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
        self.status_label.setText("⏳ Comparing schemas…")
        self.tree.clear()

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

        if diff.tables_added:
            added_root = QTreeWidgetItem(tables_root, [f"Added ({len(diff.tables_added)})", "", ""])
            for name in diff.tables_added:
                self._colored_item(added_root, name, "added")

        if diff.tables_removed:
            removed_root = QTreeWidgetItem(tables_root, [f"Removed ({len(diff.tables_removed)})", "", ""])
            for name in diff.tables_removed:
                self._colored_item(removed_root, name, "removed")

        if diff.tables_modified:
            modified_root = QTreeWidgetItem(tables_root, [f"Modified ({len(diff.tables_modified)})", "", ""])
            for table_diff in diff.tables_modified:
                table_item = self._colored_item(modified_root, table_diff.name, "modified")
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
