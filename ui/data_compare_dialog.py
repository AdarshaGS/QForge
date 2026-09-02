"""Data Compare — read-only row-level data diff between two saved connections
(issue #204, design note ai/data-compare-design.md), rendering the diff built
by services/data_diff.py.

Modeled directly on ui/schema_compare_dialog.py's SchemaCompareDialog: same
Source/Target connection-picker pattern, same background-thread compare flow,
same summary-cards-plus-filterable-change-list-plus-detail-panel layout —
extended here to row-level (not column/index-level) changes, plus a
Table/Query mode toggle, a per-side database picker (one saved connection can
point at a host with several databases), and an optional key-column field —
see services/data_diff.py's module docstring for what an empty key falls
back to.

Unlike schema's Unchanged tables, matched rows aren't individually retained
(services/data_diff.py only increments a counter — keeping every matched row
for a full-table compare would defeat the point of the row cap), so the
Unchanged summary card here is count-only and not a clickable filter.

No schema/data-modifying actions live here — like SchemaCompareDialog, this
only ever reads from the two selected connections."""
import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QPushButton,
    QComboBox, QLabel, QLineEdit, QPlainTextEdit, QSpinBox, QCheckBox,
    QTreeWidget, QTreeWidgetItem, QMessageBox, QStackedWidget, QWidget,
    QSplitter,
)

from services.data_diff import build_data_diff, table_select_sql
from services.db_service import DbService
from ui.connection_dialog import ConnectionDialog
from ui.schema_compare_dialog import (
    _CHANGE_COLORS, _CHANGE_META, _PAGE_SIZE, _Placeholder, _SummaryCard,
    _load_connection_profiles, _profile_label,
)
from ui.theme_manager import ThemeManager
from utils.logger import get_logger

logger = get_logger()

_DEFAULT_ROW_LIMIT = 50000


def _row_preview(row: dict, limit: int = 4) -> str:
    if not row:
        return ""
    parts = [f"{k}={v}" for k, v in list(row.items())[:limit]]
    return ", ".join(parts) + (", …" if len(row) > limit else "")


def _row_change_summary(row_diff) -> str:
    """A concise one-line Detail-column summary — "3 fields changed" for a
    modified row, or a preview of the row itself for an added/removed one,
    mirroring ui/schema_compare_dialog.py's _table_change_summary."""
    if row_diff.change == "modified":
        n = len(row_diff.field_changes)
        return f"{n} field{'s' if n != 1 else ''} changed"
    row = row_diff.target_row if row_diff.change == "added" else row_diff.source_row
    return _row_preview(row)


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
        self._last_diff = None

        # cat -> list[RowDiff], for "added"/"removed"/"modified" only —
        # "unchanged" has no per-row data to list (see module docstring).
        self._category_data = {"added": [], "removed": [], "modified": []}
        self._category_loaded = {}     # cat -> how many rows are currently rendered
        self._active_filter = "all"
        self._detail_selection = None  # the RowDiff currently shown on the right
        self._cards = {}

        self.setWindowTitle("Data Compare")
        self.resize(1180, 720)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        picker_row = QFormLayout()
        self.source_combo = QComboBox()
        self.target_combo = QComboBox()
        # A saved connection profile is host-level and can hold several
        # databases (issue feedback: one MySQL/Postgres host, many DBs) — this
        # combo lets the user pick which one on that host to actually compare,
        # instead of being stuck with whatever "database" happened to be
        # saved on the profile.
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

        # ── toolbar: Compare / status ──────────────────────────────────
        toolbar = QHBoxLayout()
        self.compare_btn = QPushButton("Compare")
        self.compare_btn.clicked.connect(self._run_compare)
        toolbar.addWidget(self.compare_btn)

        self.truncated_label = QLabel("")
        warn = _CHANGE_COLORS[is_dark]["modified"].name()
        self.truncated_label.setStyleSheet(f"color: {warn}; font-size: 11px; font-weight: 600;")
        toolbar.addWidget(self.truncated_label)

        toolbar.addStretch()
        self.status_label = QLabel("Pick a source and target, then Compare (key column optional).")
        toolbar.addWidget(self.status_label)
        layout.addLayout(toolbar)

        # ── summary cards (Added/Removed/Modified also act as filters —
        # Unchanged is count-only, see module docstring) ─────────────────
        cards_row = QHBoxLayout()
        cards_row.setSpacing(8)
        for key in ("added", "removed", "modified", "unchanged"):
            icon, label = _CHANGE_META[key]
            card = _SummaryCard(icon, label, _CHANGE_COLORS[is_dark][key], is_dark)
            if key == "unchanged":
                card.setCursor(Qt.ArrowCursor)
                card.setToolTip("Matched rows aren't kept in memory — shown as a count only.")
            else:
                card.clicked.connect(lambda k=key: self._on_card_clicked(k))
            cards_row.addWidget(card)
            self._cards[key] = card
        layout.addLayout(cards_row)

        # ── search + change-type filter ───────────────────────────────────
        search_row = QHBoxLayout()
        self.filter_box = QLineEdit()
        self.filter_box.setPlaceholderText("Search by key...")
        self.filter_box.textChanged.connect(self._on_search_text_changed)
        search_row.addWidget(self.filter_box, 1)

        self.change_filter_combo = QComboBox()
        self.change_filter_combo.addItem("All Changes", "all")
        for key in ("added", "removed", "modified"):
            self.change_filter_combo.addItem(_CHANGE_META[key][1], key)
        self.change_filter_combo.currentIndexChanged.connect(self._on_filter_combo_changed)
        search_row.addWidget(self.change_filter_combo)
        layout.addLayout(search_row)

        # Debounced — re-renders 200ms after the user stops typing rather
        # than on every keystroke, so search stays smooth on a large diff.
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)
        self._search_timer.timeout.connect(self._render_tree)

        # ── split view: change list (left) | selected-row detail (right)
        splitter = QSplitter(Qt.Horizontal)

        self.left_stack = QStackedWidget()
        self.left_placeholder = _Placeholder(
            is_dark, "🔍", "Ready to compare",
            "Select a source and target, pick a table (or query), then click Compare.")
        self.left_stack.addWidget(self.left_placeholder)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Row", "Change", "Detail"])
        self.tree.setColumnWidth(0, 260)
        self.tree.setColumnWidth(1, 90)
        self.tree.itemClicked.connect(self._on_tree_item_clicked)
        self.tree.itemSelectionChanged.connect(self._on_tree_selection_changed)
        self.left_stack.addWidget(self.tree)
        self.left_stack.setCurrentWidget(self.left_placeholder)
        splitter.addWidget(self.left_stack)

        self.detail_stack = QStackedWidget()
        self.detail_placeholder = _Placeholder(
            is_dark, "👈", "No row selected",
            "Select a changed row to inspect its differences.")
        self.detail_stack.addWidget(self.detail_placeholder)
        self.detail_page = self._build_detail_page()
        self.detail_stack.addWidget(self.detail_page)
        self.detail_stack.setCurrentWidget(self.detail_placeholder)
        splitter.addWidget(self.detail_stack)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([680, 460])
        layout.addWidget(splitter, 1)

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
        self.truncated_label.setText("")
        self.status_label.setText("⏳ Comparing data…")
        self.left_placeholder.set_message(
            "⏳", "Comparing data…", "This can take a moment for a large row cap.")
        self.left_stack.setCurrentWidget(self.left_placeholder)
        self._clear_detail()

        self._last_diff = None

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
        self.left_placeholder.set_message("⚠", "Data comparison failed", msg)
        self.left_stack.setCurrentWidget(self.left_placeholder)

    def _on_diff_loaded(self, diff):
        self.compare_btn.setEnabled(True)
        self._last_diff = diff

        self._category_data = {
            "added": list(diff.rows_added),
            "removed": list(diff.rows_removed),
            "modified": list(diff.rows_modified),
        }
        self._category_loaded = {key: _PAGE_SIZE for key in self._category_data}
        for key in ("added", "removed", "modified"):
            self._cards[key].set_count(getattr(diff, f"rows_{key}_total"))
        self._cards["unchanged"].set_count(diff.rows_unchanged)

        has_changes = bool(diff.rows_added_total or diff.rows_removed_total or diff.rows_modified_total)

        if diff.truncated_source or diff.truncated_target:
            sides = " and ".join(s for s, t in (("source", diff.truncated_source),
                                                 ("target", diff.truncated_target)) if t)
            self.truncated_label.setText(f"⚠ Row cap reached on {sides} — comparison may be incomplete.")
        else:
            self.truncated_label.setText("")

        if has_changes:
            self.status_label.setText(
                f"{diff.rows_added_total} added, {diff.rows_removed_total} removed, "
                f"{diff.rows_modified_total} modified, {diff.rows_unchanged} unchanged")
        else:
            self.status_label.setText(f"✅ No data differences found — {diff.rows_unchanged} rows match.")

        self.left_stack.setCurrentWidget(self.tree)
        self._clear_detail()
        self._render_tree()

    # ── change list (left panel) ────────────────────────────────────────

    def _colored_item(self, parent, text: str, change: str, detail: str = ""):
        item = QTreeWidgetItem(parent, [text, change.capitalize(), detail])
        color = _CHANGE_COLORS[self._is_dark][change]
        item.setForeground(1, QBrush(color))
        return item

    def _render_tree(self):
        """Rebuilds the left tree from self._category_data, honoring the
        active change-type filter, search text, and each category's current
        "how many rows are loaded" count. Mirrors
        SchemaCompareDialog._render_tree's lazy "Show N more…" pagination —
        each bucket is already capped at services/data_diff.py's
        _DISPLAY_CAP, but a full-table compare can still hit that cap, so
        this avoids building a QTreeWidgetItem for every one of those rows
        up front."""
        self.tree.clear()
        if self._last_diff is None:
            return

        text = self.filter_box.text().strip().lower()
        order = ("added", "removed", "modified")
        shown_any = False
        for change in order:
            if self._active_filter != "all" and self._active_filter != change:
                continue
            data = self._category_data[change]
            filtered = [rd for rd in data if not text or text in rd.key.lower()] if text else data
            if not filtered:
                continue
            shown_any = True
            self._build_category(change, filtered)

        if not shown_any:
            empty = QTreeWidgetItem(self.tree, ["No matching rows.", "", ""])
            f = empty.font(0)
            f.setItalic(True)
            empty.setFont(0, f)

    def _build_category(self, change: str, filtered: list):
        icon, label = _CHANGE_META[change]
        color = _CHANGE_COLORS[self._is_dark][change]
        total = getattr(self._last_diff, f"rows_{change}_total")
        capped_note = " (capped)" if total > len(self._category_data[change]) else ""
        root = QTreeWidgetItem(self.tree, [f"{icon}  {label} ({len(filtered)}{capped_note})", "", ""])
        f = root.font(0)
        f.setBold(True)
        root.setFont(0, f)
        root.setForeground(0, QBrush(color))
        root.setData(0, Qt.UserRole, ("category", change))

        loaded = self._category_loaded.get(change, _PAGE_SIZE)
        shown = filtered[:loaded]
        for row_diff in shown:
            item = self._colored_item(root, row_diff.key, change, _row_change_summary(row_diff))
            item.setData(0, Qt.UserRole, ("object", change, row_diff))

        remaining = len(filtered) - len(shown)
        if remaining > 0:
            more = QTreeWidgetItem(root, [f"Show {remaining} more…", "", ""])
            mf = more.font(0)
            mf.setItalic(True)
            more.setFont(0, mf)
            muted = ThemeManager.D_TEXT3 if self._is_dark else ThemeManager.L_TEXT3
            more.setForeground(0, QBrush(QColor(muted)))
            more.setData(0, Qt.UserRole, ("show_more", change))

        self.tree.expandItem(root)

    def _on_tree_item_clicked(self, item, _column):
        data = item.data(0, Qt.UserRole)
        if not data or data[0] != "show_more":
            return
        change = data[1]
        self._category_loaded[change] = self._category_loaded.get(change, _PAGE_SIZE) + _PAGE_SIZE
        self._render_tree()

    def _on_tree_selection_changed(self):
        items = self.tree.selectedItems()
        if not items:
            return
        data = items[0].data(0, Qt.UserRole)
        if not data or data[0] != "object":
            return
        _, change, row_diff = data
        self._show_detail(change, row_diff)

    # ── search / filter ──────────────────────────────────────────────────

    def _on_search_text_changed(self, _text: str):
        for change in self._category_loaded:
            self._category_loaded[change] = _PAGE_SIZE
        self._search_timer.start()

    def _on_filter_combo_changed(self, _index: int):
        self._set_active_filter(self.change_filter_combo.currentData(), from_combo=True)

    def _on_card_clicked(self, key: str):
        new_filter = "all" if self._active_filter == key else key
        self._set_active_filter(new_filter, from_combo=False)

    def _set_active_filter(self, key: str, from_combo: bool):
        self._active_filter = key
        for card_key, card in self._cards.items():
            if card_key != "unchanged":
                card.set_active(card_key == key)
        if not from_combo:
            idx = self.change_filter_combo.findData(key)
            if idx >= 0:
                self.change_filter_combo.blockSignals(True)
                self.change_filter_combo.setCurrentIndex(idx)
                self.change_filter_combo.blockSignals(False)
        for change in self._category_loaded:
            self._category_loaded[change] = _PAGE_SIZE
        self._render_tree()

    # ── detail panel (right) ─────────────────────────────────────────────

    def _build_detail_page(self) -> QWidget:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(10, 10, 10, 10)
        page_layout.setSpacing(8)

        header = QHBoxLayout()
        self.detail_title = QLabel("")
        self.detail_title.setStyleSheet("font-size: 14px; font-weight: 700; background: transparent;")
        header.addWidget(self.detail_title)
        self.detail_badge = QLabel("")
        header.addWidget(self.detail_badge)
        header.addStretch()
        page_layout.addLayout(header)

        self.detail_note = QLabel("")
        self.detail_note.setWordWrap(True)
        text2 = ThemeManager.D_TEXT2 if self._is_dark else ThemeManager.L_TEXT2
        self.detail_note.setStyleSheet(f"color: {text2}; font-size: 12px; background: transparent;")
        page_layout.addWidget(self.detail_note)

        self.detail_tree = QTreeWidget()
        self.detail_tree.setHeaderLabels(["Field", "Value"])
        self.detail_tree.setColumnWidth(0, 200)
        page_layout.addWidget(self.detail_tree, 1)

        return page

    def _show_detail(self, change: str, row_diff):
        self._detail_selection = row_diff
        self.detail_stack.setCurrentWidget(self.detail_page)

        icon, label = _CHANGE_META[change]
        color = _CHANGE_COLORS[self._is_dark][change]
        self.detail_title.setText(row_diff.key)
        self.detail_badge.setText(f"{icon} {label}")
        self.detail_badge.setStyleSheet(
            f"color: {color.name()}; font-size: 11px; font-weight: 700; background: transparent;")

        self.detail_tree.clear()
        columns = self._last_diff.columns if self._last_diff is not None else []
        if change == "modified":
            self.detail_note.setText(_row_change_summary(row_diff))
            for field_name, (old, new) in row_diff.field_changes.items():
                QTreeWidgetItem(self.detail_tree, [field_name, f"{old} → {new}"])
        elif change == "added":
            self.detail_note.setText("Present in Target only — missing from Source.")
            for col in columns:
                if col in row_diff.target_row:
                    QTreeWidgetItem(self.detail_tree, [col, str(row_diff.target_row[col])])
        else:  # removed
            self.detail_note.setText("Present in Source only — missing from Target.")
            for col in columns:
                if col in row_diff.source_row:
                    QTreeWidgetItem(self.detail_tree, [col, str(row_diff.source_row[col])])

    def _clear_detail(self):
        self._detail_selection = None
        self.detail_stack.setCurrentWidget(self.detail_placeholder)
