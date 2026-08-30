"""Schema Compare — read-only structural diff between two saved connections
(issue #68), rendering the diff built by services/schema_diff.py, plus an
optional "Generate Migration SQL" step (issue #69) that turns that diff into
a reviewable DDL script via services/schema_migration.py.

No schema-modifying actions live here, mirroring ui/erd_dialog.py's stated
scope for the ER diagram: this dialog (and the migration-review dialog it
opens) only ever reads metadata from the two selected connections and never
writes to either database — the generated SQL is text for the user to copy,
export, and run themselves wherever they choose.

UI note: Added/Removed/Modified/Unchanged counts are grouped into
collapsible sections with lazy "Show N more…" pagination (services never
change, only how many QTreeWidgetItems get built for a given render —
building nested column/index/FK detail for every modified table up front
was the actual cost on a large schema, so that detail is now built once, on
demand, for whichever single table is selected in the right-hand panel)."""
import json
import os
import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QPushButton,
    QComboBox, QLabel, QLineEdit, QTreeWidget, QTreeWidgetItem, QMessageBox,
    QPlainTextEdit, QFileDialog, QMenu, QFrame, QSplitter, QStackedWidget,
    QWidget,
)

from services.db_service import DbService
from services.schema_diff import build_schema_diff
from services.schema_migration import generate_migration_sql
from ui.connection_dialog import ConnectionDialog
from ui.theme_manager import ThemeManager
from utils.logger import get_logger

logger = get_logger()

# (is_dark) -> {change -> color}. Chosen to stay legible against both a dark
# (#1c1c1e-ish) and light (#ffffff-ish) tree background. Values are QColor
# (not hex strings) — ui/data_compare_dialog.py imports this dict and feeds
# entries straight into QBrush(), so the shape here is a public contract.
_CHANGE_COLORS = {
    True: {
        "added": QColor("#30D158"), "removed": QColor("#FF453A"),
        "modified": QColor("#FF9F0A"), "unchanged": QColor(ThemeManager.D_BLUE),
    },
    False: {
        "added": QColor("#1b8a3a"), "removed": QColor("#c62828"),
        "modified": QColor("#b8720b"), "unchanged": QColor(ThemeManager.L_BLUE),
    },
}

# (icon, label) per change bucket — used for section headers, summary cards,
# and the detail-panel badge.
_CHANGE_META = {
    "added": ("+", "Added"),
    "removed": ("−", "Removed"),
    "modified": ("✎", "Modified"),
    "unchanged": ("●", "Unchanged"),
}

# How many rows a section renders before collapsing the rest behind a
# "Show N more…" row — the point of issue #235-style large-schema handling:
# never build a QTreeWidgetItem (and, for Modified, its nested column/index/
# FK children) for an object nobody has asked to see yet.
_PAGE_SIZE = 50


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


def _table_change_summary(table_diff) -> str:
    """A concise one-line summary of a modified table's changes, e.g.
    "2 columns added, 1 column modified, 1 index added" — entirely derived
    from the already-computed TableDiff, nothing fabricated. Falls back to
    a plain label on the rare table_diff with has_changes True but no
    column/index/FK entries this summary knows how to describe."""

    def _counts(items):
        counts = {"added": 0, "removed": 0, "modified": 0}
        for item in items:
            counts[item.change] = counts.get(item.change, 0) + 1
        return counts

    parts = []
    col_counts = _counts(table_diff.columns)
    for change in ("added", "removed", "modified"):
        n = col_counts.get(change, 0)
        if n:
            parts.append(f"{n} column{'s' if n != 1 else ''} {change}")

    idx_counts = _counts(table_diff.indexes)
    for change in ("added", "removed", "modified"):
        n = idx_counts.get(change, 0)
        if n:
            parts.append(f"{n} index{'es' if n != 1 else ''} {change}")

    fk_total = len(table_diff.foreign_keys)
    if fk_total:
        parts.append(f"{fk_total} foreign key{'s' if fk_total != 1 else ''} changed")

    return ", ".join(parts) if parts else "Structure changed"


def _diff_is_destructive(diff, table_name: str = None) -> bool:
    """True if turning *diff* (optionally scoped to just *table_name*) into
    migration SQL would emit a DROP TABLE / DROP COLUMN / dropped index
    against Target. Mirrors services.schema_migration's own drop decisions
    exactly (tables_added -> DROP TABLE; a column/index present in Target
    only, i.e. change == "added" -> DROP COLUMN / DROP INDEX) rather than
    re-deriving them independently, so this can never disagree with what
    generate_migration_sql actually emits."""
    if table_name is not None:
        if table_name in diff.tables_added:
            return True
        table_diffs = [t for t in diff.tables_modified if t.name == table_name]
    else:
        if diff.tables_added:
            return True
        table_diffs = diff.tables_modified

    for t in table_diffs:
        if any(c.change == "added" for c in t.columns):
            return True
        if any(i.change == "added" for i in t.indexes):
            return True
    return False


class _Placeholder(QWidget):
    """Centered icon/heading/subtitle empty-state, mutable in place so the
    same widget can move through "ready to compare" -> "comparing…" ->
    "failed"/"no differences" without rebuilding it (pattern borrowed from
    ui/sql_tab.py's zero-row empty state)."""

    def __init__(self, is_dark: bool, icon: str, heading: str, subtitle: str, parent=None):
        super().__init__(parent)
        self._is_dark = is_dark
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(6)

        self._icon = QLabel()
        self._icon.setAlignment(Qt.AlignCenter)
        self._icon.setStyleSheet("font-size: 32px; background: transparent;")
        self._heading = QLabel()
        self._heading.setAlignment(Qt.AlignCenter)
        self._subtitle = QLabel()
        self._subtitle.setAlignment(Qt.AlignCenter)
        self._subtitle.setWordWrap(True)
        self._subtitle.setMaximumWidth(320)

        layout.addWidget(self._icon)
        layout.addWidget(self._heading)
        layout.addWidget(self._subtitle)
        self.set_message(icon, heading, subtitle)

    def set_message(self, icon: str, heading: str, subtitle: str):
        text = ThemeManager.D_TEXT if self._is_dark else ThemeManager.L_TEXT
        text2 = ThemeManager.D_TEXT2 if self._is_dark else ThemeManager.L_TEXT2
        self._icon.setText(icon)
        self._heading.setText(heading)
        self._heading.setStyleSheet(f"color: {text}; font-size: 14px; font-weight: 600; background: transparent;")
        self._subtitle.setText(subtitle)
        self._subtitle.setStyleSheet(f"color: {text2}; font-size: 12px; background: transparent;")


class _SummaryCard(QFrame):
    """One clickable Added/Removed/Modified/Unchanged tile — doubles as a
    filter toggle (issue request: cards "act as filters"). Deliberately
    plain (flat border, no gradient/shadow) to match QForge's restrained
    developer-tool aesthetic rather than a generic SaaS dashboard card."""

    clicked = Signal()

    def __init__(self, icon: str, label: str, color: QColor, is_dark: bool, parent=None):
        super().__init__(parent)
        self._color = color
        self._is_dark = is_dark
        self._active = False
        self.setObjectName("summaryCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumWidth(120)
        self.setToolTip(f"Filter to {label}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(6)
        icon_label = QLabel(icon)
        icon_label.setStyleSheet(
            f"color: {color.name()}; font-size: 13px; font-weight: 700; background: transparent;")
        head.addWidget(icon_label)
        title_label = QLabel(label.upper())
        title_label.setStyleSheet(
            f"color: {color.name()}; font-size: 11px; font-weight: 600; "
            "letter-spacing: 0.4px; background: transparent;")
        head.addWidget(title_label)
        head.addStretch()
        layout.addLayout(head)

        self.count_label = QLabel("0")
        text = ThemeManager.D_TEXT if is_dark else ThemeManager.L_TEXT
        self.count_label.setStyleSheet(f"color: {text}; font-size: 19px; font-weight: 700; background: transparent;")
        layout.addWidget(self.count_label)

        self._apply_style()

    def _apply_style(self):
        border = ThemeManager.D_BORDER if self._is_dark else ThemeManager.L_BORDER
        border_color = self._color.name() if self._active else border
        bg = ThemeManager._alpha(self._color.name(), "1f") if self._active else "transparent"
        self.setStyleSheet(f"""
            QFrame#summaryCard {{
                background: {bg};
                border: 1px solid {border_color};
                border-radius: 8px;
            }}
            QFrame#summaryCard:hover {{ border-color: {self._color.name()}; }}
        """)

    def set_count(self, n: int):
        self.count_label.setText(f"{n:,}")

    def set_active(self, active: bool):
        self._active = active
        self._apply_style()

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)


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
    _databases_loaded = Signal(object, dict, list, str)

    def __init__(self, current_connection_id: str = "", is_dark: bool = True, parent=None):
        super().__init__(parent)
        self._is_dark = is_dark
        self._last_diff = None
        self._source_config = None
        self._target_config = None
        self._target_label = ""
        self._migration_table_name = None

        # cat -> list[str] (added/removed/unchanged) or list[TableDiff] (modified)
        self._category_data = {"added": [], "removed": [], "modified": [], "unchanged": []}
        self._category_loaded = {}          # cat -> how many rows are currently rendered
        self._modified_by_name = {}         # name -> TableDiff, for detail-panel lookups
        self._active_filter = "all"
        self._detail_selection = None       # (change, name) of whatever's shown on the right
        self._cards = {}

        self.setWindowTitle("Schema Compare")
        self.resize(1180, 720)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # ── Source → Target direction ────────────────────────────────────
        picker_row = QFormLayout()
        self.source_combo = QComboBox()
        self.target_combo = QComboBox()
        # A saved connection profile is host-level and can hold several
        # databases — this lets the same server be picked for both Source
        # and Target (e.g. comparing a duplicated database against its
        # original on one host) instead of being stuck with whichever
        # single "database" happened to be saved on the profile. Left
        # disabled/empty for sqlite, where the connection *is* a single
        # database file (same distinction ConnectionPanel's own
        # DbSwitcherDialog already makes).
        self.source_db_combo = QComboBox()
        self.source_db_combo.setEnabled(False)
        self.target_db_combo = QComboBox()
        self.target_db_combo.setEnabled(False)

        source_row = QHBoxLayout()
        source_row.addWidget(self.source_combo, 1)
        source_row.addWidget(QLabel("DB:"))
        source_row.addWidget(self.source_db_combo, 1)
        picker_row.addRow("Source:", source_row)

        # Migration direction, verified against services/schema_migration.py
        # (generate_migration_sql's own docstring: "DDL, as one string, that
        # transforms *target* toward *source*") rather than assumed — this
        # label goes stale the moment it disagrees with that module.
        direction_label = QLabel("↓  Compare Source against Target — Migration SQL updates Target to match Source.")
        text3 = ThemeManager.D_TEXT3 if is_dark else ThemeManager.L_TEXT3
        direction_label.setStyleSheet(f"color: {text3}; font-size: 11px; padding: 2px 0 2px 2px;")
        picker_row.addRow("", direction_label)

        target_row = QHBoxLayout()
        target_row.addWidget(self.target_combo, 1)
        target_row.addWidget(QLabel("DB:"))
        target_row.addWidget(self.target_db_combo, 1)
        picker_row.addRow("Target:", target_row)
        layout.addLayout(picker_row)

        self._profiles = _load_connection_profiles()
        self._populate_combo(self.source_combo, preselect_id=current_connection_id)
        self._populate_combo(self.target_combo, preselect_id="")

        # ── toolbar: Compare / Generate Migration SQL / status ───────────
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

        self.destructive_label = QLabel("")
        danger = ThemeManager.D_DANGER if is_dark else ThemeManager.L_DANGER
        self.destructive_label.setStyleSheet(f"color: {danger}; font-size: 11px; font-weight: 600;")
        toolbar.addWidget(self.destructive_label)

        toolbar.addStretch()
        self.status_label = QLabel("Select a source and target connection, then Compare.")
        toolbar.addWidget(self.status_label)
        layout.addLayout(toolbar)

        # ── summary cards (also act as one-click filters) ────────────────
        cards_row = QHBoxLayout()
        cards_row.setSpacing(8)
        for key in ("added", "removed", "modified", "unchanged"):
            icon, label = _CHANGE_META[key]
            card = _SummaryCard(icon, label, _CHANGE_COLORS[is_dark][key], is_dark)
            card.clicked.connect(lambda k=key: self._on_card_clicked(k))
            cards_row.addWidget(card)
            self._cards[key] = card
        layout.addLayout(cards_row)

        # ── search + change-type filter ───────────────────────────────────
        search_row = QHBoxLayout()
        self.filter_box = QLineEdit()
        self.filter_box.setPlaceholderText("Search tables...")
        self.filter_box.textChanged.connect(self._on_search_text_changed)
        search_row.addWidget(self.filter_box, 1)

        self.change_filter_combo = QComboBox()
        self.change_filter_combo.addItem("All Changes", "all")
        for key in ("added", "removed", "modified", "unchanged"):
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

        # ── split view: change list (left) | selected-object detail (right)
        splitter = QSplitter(Qt.Horizontal)

        self.left_stack = QStackedWidget()
        self.left_placeholder = _Placeholder(
            is_dark, "🔍", "Ready to compare",
            "Select a source and target connection, then click Compare.")
        self.left_stack.addWidget(self.left_placeholder)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Object", "Change", "Detail"])
        self.tree.setColumnWidth(0, 280)
        self.tree.setColumnWidth(1, 90)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_tree_context_menu)
        self.tree.itemClicked.connect(self._on_tree_item_clicked)
        self.tree.itemSelectionChanged.connect(self._on_tree_selection_changed)
        self.left_stack.addWidget(self.tree)
        self.left_stack.setCurrentWidget(self.left_placeholder)
        splitter.addWidget(self.left_stack)

        self.detail_stack = QStackedWidget()
        self.detail_placeholder = _Placeholder(
            is_dark, "👈", "No object selected",
            "Select a changed object to inspect its differences.")
        self.detail_stack.addWidget(self.detail_placeholder)
        self.detail_page = self._build_detail_page()
        self.detail_stack.addWidget(self.detail_page)
        self.detail_stack.setCurrentWidget(self.detail_placeholder)
        splitter.addWidget(self.detail_stack)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([680, 460])
        layout.addWidget(splitter, 1)

        self._diff_loaded.connect(self._on_diff_loaded)
        self._diff_load_error.connect(self._on_diff_error)
        self._migration_ready.connect(self._on_migration_ready)
        self._migration_error.connect(self._on_migration_error)
        self._databases_loaded.connect(self._on_databases_loaded)

        self.source_combo.currentIndexChanged.connect(
            lambda: self._load_databases(self.source_combo, self.source_db_combo))
        self.target_combo.currentIndexChanged.connect(
            lambda: self._load_databases(self.target_combo, self.target_db_combo))
        self._load_databases(self.source_combo, self.source_db_combo)
        self._load_databases(self.target_combo, self.target_db_combo)

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

    def _effective_config(self, conn_combo: QComboBox, db_combo: QComboBox):
        """*conn_combo*'s resolved config, with "database" overridden to
        whatever *db_combo* has selected — lets the same host be picked for
        both Source and Target (e.g. a duplicated database vs. its original
        on one server) instead of being stuck with the profile's own saved
        database."""
        config = self._resolve_config(conn_combo)
        if config is None:
            return None
        db_name = db_combo.currentText().strip()
        return dict(config, database=db_name) if db_name else config

    def _load_databases(self, conn_combo: QComboBox, db_combo: QComboBox):
        db_combo.clear()
        db_combo.setEnabled(False)
        config = self._resolve_config(conn_combo)
        if config is None or config.get("type") == "sqlite":
            return

        sig = self._databases_loaded

        def _worker():
            names, err = [], ""
            db = DbService()
            try:
                db.connect(config)
                names = db.get_databases()
            except Exception as ex:
                err = str(ex)
                logger.warning(f"Schema Compare: failed to list databases: {ex}")
            finally:
                db.disconnect()
            sig.emit(db_combo, config, names, err)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_databases_loaded(self, db_combo: QComboBox, config: dict, names: list, err: str):
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

    # ── diffing (background thread — same pattern as
    # ErdDialog._reload) ────────────────────────────────────────────

    def _run_compare(self):
        source_config = self._effective_config(self.source_combo, self.source_db_combo)
        target_config = self._effective_config(self.target_combo, self.target_db_combo)
        if source_config is None or target_config is None:
            QMessageBox.information(self, "Schema Compare", "Choose both a source and a target connection.")
            return

        self.compare_btn.setEnabled(False)
        self.migrate_btn.setEnabled(False)
        self.destructive_label.setText("")
        self.status_label.setText("⏳ Comparing schemas…")
        self.left_placeholder.set_message(
            "⏳", "Comparing schemas…", "This can take a moment for a large schema.")
        self.left_stack.setCurrentWidget(self.left_placeholder)
        self._clear_detail()

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
        self.left_placeholder.set_message("⚠", "Schema comparison failed", msg)
        self.left_stack.setCurrentWidget(self.left_placeholder)

    def _on_diff_loaded(self, diff):
        self.compare_btn.setEnabled(True)
        self._last_diff = diff
        self._migration_table_name = None

        self._category_data = {
            "added": list(diff.tables_added),
            "removed": list(diff.tables_removed),
            "modified": list(diff.tables_modified),
            "unchanged": list(diff.tables_unchanged_names),
        }
        self._modified_by_name = {t.name: t for t in diff.tables_modified}
        self._category_loaded = {key: _PAGE_SIZE for key in self._category_data}
        for key, card in self._cards.items():
            card.set_count(len(self._category_data[key]))

        has_changes = bool(diff.tables_added or diff.tables_removed or diff.tables_modified)
        self.migrate_btn.setEnabled(has_changes)
        self.migrate_btn.setToolTip("" if has_changes else "No differences to migrate")

        if _diff_is_destructive(diff):
            self.destructive_label.setText("⚠ Includes DROP TABLE/COLUMN — review before running.")
        else:
            self.destructive_label.setText("")

        if has_changes:
            self.status_label.setText(
                f"{len(diff.tables_added)} added, {len(diff.tables_removed)} removed, "
                f"{len(diff.tables_modified)} modified, {diff.tables_unchanged} unchanged")
        else:
            self.status_label.setText(f"✅ No schema differences found — {diff.tables_unchanged} tables match.")

        self.left_stack.setCurrentWidget(self.tree)
        self._clear_detail()
        self._render_tree()

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
        data = item.data(0, Qt.UserRole) if item else None
        if not data or data[0] != "object":
            return
        _, change, name = data
        if change == "unchanged":
            return  # nothing to migrate for a table that didn't change
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
        destructive = (
            _diff_is_destructive(self._last_diff, table_name=self._migration_table_name)
            if self._last_diff is not None else False
        )
        dialog = _MigrationReviewDialog(sql, label, self._is_dark, destructive, self)
        dialog.exec()

    def _on_migration_error(self, msg: str):
        self.migrate_btn.setEnabled(True)
        self.migrate_btn.setText("Generate Migration SQL")
        QMessageBox.warning(self, "Generate Migration SQL", f"Failed to generate migration SQL: {msg}")

    # ── change list (left panel) ────────────────────────────────────────

    def _colored_item(self, parent, text: str, change: str, detail: str = ""):
        item = QTreeWidgetItem(parent, [text, change.capitalize(), detail])
        color = _CHANGE_COLORS[self._is_dark][change]
        item.setForeground(1, QBrush(color))
        return item

    def _render_tree(self):
        """Rebuilds the left tree from self._category_data, honoring the
        active change-type filter, search text, and each category's current
        "how many rows are loaded" count. Cheap even on a large diff: only
        the rows actually being shown get a QTreeWidgetItem, and Modified
        rows stay flat here — their column/index/FK detail is built once,
        lazily, in _show_detail for whichever single table is selected."""
        self.tree.clear()
        if self._last_diff is None:
            return

        text = self.filter_box.text().strip().lower()
        order = ("added", "removed", "modified", "unchanged")
        shown_any = False
        for change in order:
            if self._active_filter != "all" and self._active_filter != change:
                continue
            data = self._category_data[change]
            if text:
                filtered = [e for e in data if text in (e.name if change == "modified" else e).lower()]
            else:
                filtered = data
            if not filtered:
                continue
            shown_any = True
            self._build_category(change, filtered)

        if not shown_any:
            empty = QTreeWidgetItem(self.tree, ["No matching objects.", "", ""])
            f = empty.font(0)
            f.setItalic(True)
            empty.setFont(0, f)

    def _build_category(self, change: str, filtered: list):
        icon, label = _CHANGE_META[change]
        color = _CHANGE_COLORS[self._is_dark][change]
        root = QTreeWidgetItem(self.tree, [f"{icon}  {label} ({len(filtered)})", "", ""])
        f = root.font(0)
        f.setBold(True)
        root.setFont(0, f)
        root.setForeground(0, QBrush(color))
        root.setData(0, Qt.UserRole, ("category", change))

        loaded = self._category_loaded.get(change, _PAGE_SIZE)
        shown = filtered[:loaded]
        for entry in shown:
            if change == "modified":
                name = entry.name
                detail = _table_change_summary(entry)
            else:
                name = entry
                detail = "No changes" if change == "unchanged" else "Table"
            item = self._colored_item(root, name, change, detail)
            item.setData(0, Qt.UserRole, ("object", change, name))

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
        _, change, name = data
        self._show_detail(change, name)

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
        self.detail_tree.setHeaderLabels(["Field", "Change", "Detail"])
        self.detail_tree.setColumnWidth(0, 200)
        self.detail_tree.setColumnWidth(1, 90)
        page_layout.addWidget(self.detail_tree, 1)

        self.detail_warning = QLabel("")
        danger = ThemeManager.D_DANGER if self._is_dark else ThemeManager.L_DANGER
        self.detail_warning.setWordWrap(True)
        self.detail_warning.setStyleSheet(f"color: {danger}; font-size: 11px; font-weight: 600; background: transparent;")
        page_layout.addWidget(self.detail_warning)

        self.detail_migrate_btn = QPushButton("Generate Migration SQL for this table")
        self.detail_migrate_btn.clicked.connect(self._on_detail_migrate_clicked)
        page_layout.addWidget(self.detail_migrate_btn)

        return page

    def _on_detail_migrate_clicked(self):
        if self._detail_selection is None:
            return
        _, name = self._detail_selection
        self._generate_migration(table_name=name)

    def _show_detail(self, change: str, name: str):
        self._detail_selection = (change, name)
        self.detail_stack.setCurrentWidget(self.detail_page)

        icon, label = _CHANGE_META[change]
        color = _CHANGE_COLORS[self._is_dark][change]
        self.detail_title.setText(name)
        self.detail_badge.setText(f"{icon} {label}")
        self.detail_badge.setStyleSheet(
            f"color: {color.name()}; font-size: 11px; font-weight: 700; background: transparent;")

        self.detail_tree.clear()
        if change == "modified":
            table_diff = self._modified_by_name.get(name)
            if table_diff is not None:
                self.detail_note.setText(_table_change_summary(table_diff))
                self._build_table_detail(self.detail_tree, table_diff)
                self.detail_tree.expandAll()
            self.detail_tree.show()
        elif change == "added":
            self.detail_note.setText(
                "Present in Target only — missing from Source. Migration SQL will DROP this table "
                "in Target. Column-level detail isn't available for a table that exists on only one "
                "side of the comparison.")
            self.detail_tree.hide()
        elif change == "removed":
            self.detail_note.setText(
                "Present in Source only — missing from Target. Migration SQL will recreate this "
                "table in Target from Source's definition. Column-level detail isn't available for "
                "a table that exists on only one side of the comparison.")
            self.detail_tree.hide()
        else:  # unchanged
            self.detail_note.setText("No differences — this table's structure matches on both sides.")
            self.detail_tree.hide()

        can_migrate = change in ("added", "removed", "modified")
        self.detail_migrate_btn.setVisible(can_migrate)
        if can_migrate and self._last_diff is not None and _diff_is_destructive(self._last_diff, table_name=name):
            self.detail_warning.setText("⚠ Includes a DROP for this table — review before running.")
        else:
            self.detail_warning.setText("")

    def _clear_detail(self):
        self._detail_selection = None
        self.detail_stack.setCurrentWidget(self.detail_placeholder)

    # ── per-table diff detail (built lazily, on selection, into
    # detail_tree — same rendering used regardless of how many other
    # Modified tables exist in the diff) ─────────────────────────────────

    def _build_table_detail(self, table_item, table_diff):
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


class _MigrationReviewDialog(QDialog):
    """Shows generated migration SQL for review — copy/export only, no Run
    button. Executing it (if the user chooses to) happens in a normal SQL
    tab, where the app's existing dangerous-query/read-only guards apply."""

    def __init__(self, sql: str, target_label: str, is_dark: bool, destructive: bool = False, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Migration SQL")
        self.resize(760, 560)
        self._sql = sql

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(f"Target: {target_label}  —  review before running anywhere."))

        if destructive:
            danger = ThemeManager.D_DANGER if is_dark else ThemeManager.L_DANGER
            warning = QLabel(
                "⚠ This script may contain DROP TABLE / DROP COLUMN / DROP INDEX statements that can "
                "delete data in Target. Review it carefully before running it anywhere.")
            warning.setWordWrap(True)
            warning.setStyleSheet(f"color: {danger}; font-weight: 600; font-size: 12px;")
            layout.addWidget(warning)

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
