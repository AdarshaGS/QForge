import ast
import operator

from PySide6.QtWidgets import (
    QTableWidget,
    QTableWidgetItem,
    QTableView,
    QHeaderView,
    QMenu,
    QMessageBox,
    QAbstractItemView,
    QApplication,
    QFrame,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QBrush, QShortcut, QKeySequence, QCursor
import pandas as pd
from utils.df_export import export_dataframe


# ── Safe arithmetic formula evaluator (issue #161) ──────────────────────
# Replaces a raw eval() — even with {"__builtins__": {}}, Python object
# introspection (e.g. ().__class__.__base__.__subclasses__()) still reaches
# arbitrary classes without needing the builtins dict, so that was never a
# real sandbox. This whitelists an AST to exactly number literals, +/-/*/
# //%/** and unary +/-, with no names, calls, attributes, or subscripts —
# nothing to introspect through.
_SAFE_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_SAFE_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _safe_eval_arithmetic(expr: str):
    """Evaluate a numeric expression (digits, +-*/%**, parens) with no
    access to names, calls, attributes, or builtins. Raises ValueError on
    anything outside that grammar."""
    def _eval(node):
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _SAFE_BIN_OPS:
            return _SAFE_BIN_OPS[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _SAFE_UNARY_OPS:
            return _SAFE_UNARY_OPS[type(node.op)](_eval(node.operand))
        raise ValueError(f"Unsupported expression: {ast.dump(node)}")

    return _eval(ast.parse(expr, mode="eval"))


# ── Colour palette ───────────────────────────────────────────────────
# Dark theme
_D_SEL_ROW   = QColor("#1B4F8A")   # selected row  → TablePlus-style blue
_D_SEL_TEXT  = QColor("#ffffff")
_D_MOD_ROW   = QColor("#2a1e00")   # row that has at least one changed cell
_D_MOD_CELL  = QColor("#4d3500")   # the exact cell whose value changed
_D_MOD_TEXT  = QColor("#ffd60a")   # amber text on changed cell
_D_NEW_ROW   = QColor("#0d2a0d")   # newly added row
_D_NEW_TEXT  = QColor("#4ec9a0")
_D_DEL_ROW   = QColor("#2a0d0d")   # row marked for deletion
_D_DEL_TEXT  = QColor("#f48771")
# Light theme
_L_SEL_ROW   = QColor("#0A84FF")
_L_SEL_TEXT  = QColor("#ffffff")
_L_MOD_ROW   = QColor("#fff9e6")
_L_MOD_CELL  = QColor("#ffe58a")
_L_MOD_TEXT  = QColor("#7a5c00")
_L_NEW_ROW   = QColor("#e6ffed")
_L_NEW_TEXT  = QColor("#1a6e3c")
_L_DEL_ROW   = QColor("#ffe6e6")
_L_DEL_TEXT  = QColor("#b00020")
# Active sort column highlight (issue #134). Painted by hand in
# _SortHighlightHeader.paintSection — a QHeaderView::section QSS rule with
# border/padding (needed for the grid look) makes Qt ignore the header
# item's BackgroundRole/ForegroundRole entirely, so per-column tinting via
# the model can't work; only a paintSection override can.
_HDR_SORT_BG   = QColor("#0A84FF")
_HDR_SORT_TEXT = QColor("#ffffff")


class _SortHighlightHeader(QHeaderView):
    """QHeaderView that hand-paints the active sort column instead of
    relying on the model's header roles, which the ::section stylesheet
    (border/padding) makes Qt ignore. `owner` is the EditableTableWidget
    holding the current _sort_col — shared by the main header and the
    frozen-columns overlay header, both of which may show the sort column."""

    def __init__(self, owner, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self._owner = owner
        # QTableWidget's auto-created header defaults sectionsClickable to
        # True; a standalone QHeaderView defaults to False, which silently
        # kills every sectionClicked-driven sort handler once installed here.
        self.setSectionsClickable(True)

    def paintSection(self, painter, rect, logical_index):
        if self._owner._sort_col < 0 or logical_index != self._owner._sort_col:
            super().paintSection(painter, rect, logical_index)
            return
        painter.save()
        painter.fillRect(rect, _HDR_SORT_BG)
        painter.setPen(_HDR_SORT_TEXT)
        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        text = self.model().headerData(logical_index, Qt.Horizontal, Qt.DisplayRole)
        painter.drawText(rect.adjusted(8, 0, -8, 0), Qt.AlignVCenter | Qt.AlignLeft, str(text or ""))
        painter.restore()


# ── Undo/redo command stack (issue #124) ────────────────────────────────
# Each command stores absolute row indices. Any operation that physically
# inserts/removes a grid row (add/duplicate/undo-of-add) must renumber every
# row index still held by pending commands and by the dirty-state tracking
# sets — see EditableTableWidget._shift_row_refs — otherwise a later undo
# would act on the wrong row once rows have shifted underneath it.

def _shift_row(row: int, at_row: int, delta: int) -> int:
    if delta > 0:  # a row was inserted at at_row
        return row + delta if row >= at_row else row
    return row + delta if row > at_row else row  # a row was removed at at_row


def _cell_display_text(value) -> str:
    """Text shown for one grid cell, and compared against to detect an
    edit. Bytes (a BLOB column — e.g. a stored encryption key) are shown
    as an uppercase hex string, matching what TablePlus/DBeaver show for
    the same column, instead of Python's raw bytes repr (b'\\x04\\x8f...'),
    which isn't just harder to read — comparing it against the same value
    shown as hex elsewhere looks like a mismatch when the underlying bytes
    are actually identical."""
    if isinstance(value, (bytes, bytearray)):
        return value.hex().upper()
    return str(value)


def _sql_string_literal(text: str) -> str:
    """Quote *text* as a MySQL string literal. Escapes backslash as well as
    the quote char — MySQL treats backslash as an escape character inside
    '...' by default (unless NO_BACKSLASH_ESCAPES is set), so an unescaped
    backslash silently eats the next character instead of raising: saving
    'C:\\Users\\test' actually stores 'C:Users\\test' with no error at all."""
    escaped = text.replace("\\", "\\\\").replace("'", "''")
    return f"'{escaped}'"


class _CellEditCommand:
    """One cell's text changed from old_text to new_text."""
    __slots__ = ("row", "col", "old_text", "new_text")

    def __init__(self, row, col, old_text, new_text):
        self.row = row
        self.col = col
        self.old_text = old_text
        self.new_text = new_text

    def undo(self, table):
        table._restore_cell_text(self.row, self.col, self.old_text)

    def redo(self, table):
        table._restore_cell_text(self.row, self.col, self.new_text)

    def shift_rows(self, at_row, delta):
        self.row = _shift_row(self.row, at_row, delta)

    def row_shift_effect(self, is_undo):
        """(at_row, delta) this command's own undo/redo causes to every
        OTHER row index — None for commands that never change row count."""
        return None


class _RowInsertCommand:
    """A new row was inserted at row with the given per-column text values."""
    __slots__ = ("row", "values")

    def __init__(self, row, values):
        self.row = row
        self.values = list(values)

    def undo(self, table):
        table._remove_row_and_shift(self.row)

    def redo(self, table):
        table._insert_row_with_values(self.row, self.values)

    def shift_rows(self, at_row, delta):
        self.row = _shift_row(self.row, at_row, delta)

    def row_shift_effect(self, is_undo):
        return (self.row, -1) if is_undo else (self.row, +1)


class _RowDeleteMarkCommand:
    """row was toggled into/out of the pending-deletion set."""
    __slots__ = ("row",)

    def __init__(self, row):
        self.row = row

    def undo(self, table):
        table.deleted_rows.discard(self.row)
        table._repaint_row(self.row)
        table.changes_made.emit()

    def redo(self, table):
        table.deleted_rows.add(self.row)
        table._repaint_row(self.row)
        table.changes_made.emit()

    def shift_rows(self, at_row, delta):
        self.row = _shift_row(self.row, at_row, delta)

    def row_shift_effect(self, is_undo):
        return None  # toggling deleted_rows never changes row count


class _CompositeCommand:
    """Groups several commands (e.g. a paste or a multi-row duplicate) into
    one undo/redo step.

    A sub-command's own undo()/redo() only renumbers the table's flat
    _undo_stack/_redo_stack (via _shift_row_refs) — it has no way to reach
    its *siblings* inside this same composite, since the composite itself
    isn't sitting in either stack while it's mid-execution. So after each
    sub-command runs, explicitly apply the same row shift to every other
    sibling here. Excluding the just-run command itself matters: it was
    already invoked with its own pre-shift row value, so re-shifting it
    afterwards would double-count its own insertion/removal.
    """
    __slots__ = ("commands",)

    def __init__(self, commands):
        self.commands = list(commands)

    def undo(self, table):
        for cmd in reversed(self.commands):
            effect = cmd.row_shift_effect(is_undo=True)
            cmd.undo(table)
            if effect:
                at_row, delta = effect
                for other in self.commands:
                    if other is not cmd:
                        other.shift_rows(at_row, delta)

    def redo(self, table):
        for cmd in self.commands:
            effect = cmd.row_shift_effect(is_undo=False)
            cmd.redo(table)
            if effect:
                at_row, delta = effect
                for other in self.commands:
                    if other is not cmd:
                        other.shift_rows(at_row, delta)

    def shift_rows(self, at_row, delta):
        for cmd in self.commands:
            cmd.shift_rows(at_row, delta)


class EditableTableWidget(QTableWidget):
    """Enhanced table widget with inline editing capabilities"""
    
    filter_changed = Signal()  # Signal when filters change
    changes_made = Signal()  # Signal when data is modified
    
    def __init__(self, parent=None):
        super().__init__(parent)

        self.original_data = None
        self.filtered_data = None  # Store filtered version
        self.column_filters = {}  # Store filter text for each column
        self.modified_rows = set()    # rows with at least one changed cell
        self.modified_cells = set()   # (row, col) of individually changed cells
        self.new_rows = set()         # newly inserted rows
        self.deleted_rows = set()     # rows marked for deletion

        # Issue #124: step-by-step undo/redo. _cell_snapshot tracks each
        # cell's last-known text (distinct from Qt.UserRole, which holds the
        # original DB value) so a cell edited more than once still undoes
        # one step at a time. Reset on every full redisplay (load/filter/
        # sort) rather than trying to keep row indices valid across it.
        self._undo_stack = []
        self._redo_stack = []
        self._undo_batch = None   # None, or a list accumulating one grouped step
        self._cell_snapshot = {}
        self._UNDO_LIMIT = 500

        # Client-side sort state
        self._sort_col = -1   # -1 = no active sort
        self._sort_asc = True

        self.table_name = None
        # Real primary-key column name(s) for self.table_name, set via
        # set_primary_key_columns() by whoever loaded the data (they're the
        # ones who know the table and can ask db_service). Empty means
        # "unknown" — get_changes() then falls back to matching on every
        # column rather than silently guessing one.
        self.primary_key_columns: list[str] = []

        # Issue #125: freeze/pin leading columns. QTableWidget is item-based
        # (no QAbstractTableModel of its own to split into a model/view
        # pair), but it IS a QTableView over a private internal model —
        # self.model() returns that same model instance for the widget's
        # whole lifetime (clear()/setRowCount()/setColumnCount() mutate it
        # in place, they don't replace it). So a second, plain QTableView
        # sharing that model + selection model can overlay the leading
        # columns with zero data duplication: edits, dirty-highlighting
        # (background/foreground are model data), and undo/redo all operate
        # on the same QTableWidgetItem objects regardless of which view
        # touched them.
        self._frozen_col_count = 0
        self._frozen_view = None
        self._movable_before_freeze = None
        self._syncing_frozen_width = False

        # Enable editing
        self.setEditTriggers(QTableWidget.DoubleClicked | QTableWidget.EditKeyPressed)

        # Track item changes
        self.itemChanged.connect(self.on_item_changed)

        # Context menu
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)

        # Add keyboard shortcuts

        # Cmd+D to duplicate row
        self.duplicate_shortcut = QShortcut(QKeySequence("Ctrl+D"), self)
        self.duplicate_shortcut.activated.connect(self.duplicate_selected_rows)

        # Cmd+Backspace to delete selected row(s)
        self.delete_shortcut = QShortcut(QKeySequence("Ctrl+Backspace"), self)
        self.delete_shortcut.activated.connect(self.delete_selected_rows)

        # Issue #124: Cmd+Z / Cmd+Shift+Z undo/redo. Scoped to
        # WidgetWithChildrenShortcut (not the default WindowShortcut) so it
        # only fires while focus is in this grid or one of its cell editors
        # — otherwise it would also hijack the SQL editor's own Cmd+Z
        # elsewhere in the same window.
        self.undo_shortcut = QShortcut(QKeySequence.Undo, self)
        self.undo_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self.undo_shortcut.activated.connect(self.undo)

        self.redo_shortcut = QShortcut(QKeySequence.Redo, self)
        self.redo_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self.redo_shortcut.activated.connect(self.redo)

        # Full-row selection (like TablePlus)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)

        # Styling
        self.verticalHeader().setVisible(False)  # Hide row numbers
        self.setAlternatingRowColors(True)
        # Sorting is handled server-side (via ORDER BY in the query);
        # Qt's built-in client-side sort must be OFF to keep row data intact.
        self.setSortingEnabled(False)
        
        # Connect header click for manual sorting (to avoid breaking modified state)
        self.setHorizontalHeader(_SortHighlightHeader(self, self))
        hdr = self.horizontalHeader()
        hdr.sectionClicked.connect(self.on_header_clicked)
        hdr.setSortIndicatorShown(True)   # show ▲▼ arrows without enabling Qt sort
        hdr.setHighlightSections(False)
        # Issue #122: drag-to-reorder columns. sectionClicked above still
        # fires with the *logical* index regardless of visual position, so
        # sorting is unaffected — but copy/paste below had to be audited
        # separately, since those iterated logical indices assuming that
        # matched left-to-right visual order (true before this, not after).
        hdr.setSectionsMovable(True)

        # Issue #125: right-click a header section to freeze/unfreeze.
        hdr.setContextMenuPolicy(Qt.CustomContextMenu)
        hdr.customContextMenuRequested.connect(self._show_header_context_menu)
        hdr.sectionResized.connect(self._on_main_section_resized)

        # Theme will be set by update_theme method
        self.current_theme = 'dark'
        self.update_theme(is_dark=True)
    
    def update_theme(self, is_dark=True):
        """Update table theme"""
        self.current_theme = 'dark' if is_dark else 'light'
        
        if is_dark:
            # Dark theme
            self.setStyleSheet("""
                QTableWidget {
                    gridline-color: #333336;
                    background-color: #1c1c1e;
                    alternate-background-color: #1c1c1e;
                    color: #e5e5ea;
                    selection-background-color: #1a3a5c;
                    selection-color: #e5e5ea;
                    border: none;
                    outline: none;
                }
                QTableWidget::item {
                    padding: 1px 8px;
                    border: none;
                    border-bottom: 1px solid #2a2a2d;
                    border-right: 1px solid #2a2a2d;
                }
                QTableWidget::item:selected {
                    background: #1a3a5c;
                    color: #ffffff;
                }
                QHeaderView::section {
                    background: #252528;
                    color: #8e8e93;
                    border: none;
                    border-right: 1px solid #333336;
                    border-bottom: 2px solid #444448;
                    padding: 3px 8px;
                    font-size: 11px;
                    font-weight: 600;
                    text-align: left;
                }
                QHeaderView::section:hover { background: #2e2e32; color: #e5e5ea; }
                QHeaderView::section:first { border-left: none; }
            """)
        else:
            # Light theme
            self.setStyleSheet("""
                QTableWidget {
                    gridline-color: #e0e0e3;
                    background-color: #ffffff;
                    alternate-background-color: #ffffff;
                    color: #1c1c1e;
                    selection-background-color: #d0e8ff;
                    selection-color: #1c1c1e;
                    border: none;
                    outline: none;
                }
                QTableWidget::item {
                    padding: 1px 8px;
                    border: none;
                    border-bottom: 1px solid #e8e8eb;
                    border-right: 1px solid #e8e8eb;
                }
                QTableWidget::item:selected {
                    background: #d0e8ff;
                    color: #1c1c1e;
                }
                QHeaderView::section {
                    background: #f4f4f6;
                    color: #636366;
                    border: none;
                    border-right: 1px solid #dcdcdf;
                    border-bottom: 2px solid #c8c8cc;
                    padding: 3px 8px;
                    font-size: 11px;
                    font-weight: 600;
                    text-align: left;
                }
                QHeaderView::section:hover { background: #e8e8eb; color: #1c1c1e; }
                QHeaderView::section:first { border-left: none; }
            """)

        if self._frozen_view is not None:
            accent = "#0A84FF"
            self._frozen_view.setStyleSheet(
                self.styleSheet() + f"\nQTableView {{ border: none; border-right: 2px solid {accent}; }}"
            )

    def load_data(self, dataframe: pd.DataFrame, table_name=None):
        """Load data from DataFrame"""
        # Issue #125: a fresh dataset may have entirely different columns —
        # don't carry a stale freeze count over from whatever was loaded
        # before.
        self.set_frozen_columns(0)
        self.original_data = dataframe.copy() if dataframe is not None else None
        self.filtered_data = dataframe.copy() if dataframe is not None else None
        self.table_name = table_name
        self.primary_key_columns = []   # caller sets via set_primary_key_columns()
        self.modified_rows.clear()
        self.modified_cells.clear()
        self.new_rows.clear()
        self.deleted_rows.clear()
        self.column_filters.clear()
        self._sort_col = -1
        self._sort_asc = True
        self.horizontalHeader().setSortIndicator(-1, Qt.AscendingOrder)
        
        self._display_data(dataframe)

    def set_primary_key_columns(self, columns: list[str]):
        """The real primary-key column name(s) of self.table_name (from
        db_service.get_primary_keys()), used by get_changes() to build a
        WHERE clause that actually identifies one row. Call after load_data()
        once the caller has looked them up."""
        self.primary_key_columns = list(columns or [])

    def _display_data(self, dataframe):
        """Display dataframe in the table"""
        # Re-entrancy guard: if we're already rebuilding the table (e.g. a
        # second sectionClicked handler fires while _display_data is running),
        # skip silently to avoid double-disconnect and widget corruption.
        if getattr(self, '_displaying', False):
            return
        self._displaying = True
        try:
            self._display_data_impl(dataframe)
        finally:
            self._displaying = False

    def _display_data_impl(self, dataframe):
        """Inner (non-reentrant) implementation of _display_data."""
        # Temporarily disconnect itemChanged signal
        try:
            self.itemChanged.disconnect(self.on_item_changed)
        except Exception:
            pass  # already disconnected; safe to continue

        # Issue #124: a full redisplay (load/revert/filter/sort) invalidates
        # any row indices held by pending undo/redo commands, so reset
        # history here rather than trying to keep it consistent across it.
        self._undo_stack = []
        self._redo_stack = []
        self._undo_batch = None
        self._cell_snapshot = {}

        self.clear()
        self.setRowCount(0)

        if dataframe is None:
            self.itemChanged.connect(self.on_item_changed)
            return
        
        # Show columns even for empty tables
        self.setColumnCount(len(dataframe.columns))
        self.setHorizontalHeaderLabels([str(col) for col in dataframe.columns])
        
        if dataframe.empty:
            # For empty tables, show column headers with placeholder rows
            # filling the grid (like TablePlus), not just a handful.
            self.setRowCount(self._EMPTY_PLACEHOLDER_ROWS)
            for row in range(self._EMPTY_PLACEHOLDER_ROWS):
                for col in range(len(dataframe.columns)):
                    item = QTableWidgetItem("")
                    self.setItem(row, col, item)
                    self._cell_snapshot[(row, col)] = ""
        else:
            # Display actual data
            self.setRowCount(len(dataframe))

            for row in range(len(dataframe)):
                for col in range(len(dataframe.columns)):
                    value = dataframe.iloc[row, col]

                    # pd.isna() on a non-scalar (a Postgres array-typed
                    # column, or a JSON value that decoded to a list)
                    # returns an array of bools, not one bool — ambiguous
                    # in this condition. A NULL always arrives as scalar
                    # None, so a non-scalar here is a real value, not a
                    # NULL; skip straight to display text for it.
                    is_na = pd.api.types.is_scalar(value) and pd.isna(value)
                    display_text = "" if is_na else _cell_display_text(value)

                    item = QTableWidgetItem(display_text)
                    item.setData(Qt.UserRole, dataframe.iloc[row, col])  # Store original value
                    self.setItem(row, col, item)
                    self._cell_snapshot[(row, col)] = display_text

        hdr = self.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setStretchLastSection(False)
        self._set_compact_column_widths(dataframe)
        self._apply_sort_header_labels()
        
        # Reconnect signal
        self.itemChanged.connect(self.on_item_changed)

        # Re-apply colour for any rows that were already dirty before this
        # display call (e.g. after a client-side sort or filter refresh).
        self._restore_dirty_highlights()

        # Issue #125: clear()/setColumnCount() rebuilt columns from scratch
        # above — the frozen view's per-column hidden state is view-local
        # and stale until reapplied. Freeze state itself (the count) is
        # deliberately NOT reset here so it survives a filter/sort refresh;
        # load_data() resets it explicitly for a genuinely new dataset.
        if self._frozen_col_count > 0:
            self._frozen_col_count = min(self._frozen_col_count, self.columnCount())
            self._apply_frozen_visibility()
            self._update_frozen_geometry()

    def _restore_dirty_highlights(self):
        """Re-apply colour to all rows that have a known dirty state.
        Called after every _display_data so highlights survive sort/filter refreshes."""
        for row in (self.modified_rows | self.new_rows | self.deleted_rows):
            if 0 <= row < self.rowCount():
                self._repaint_row(row)

    # ── Freeze/pin leading columns (issue #125) ─────────────────────────
    # See the __init__ comment above _frozen_col_count for why a second
    # QTableView sharing this widget's own model is the chosen technique.

    def _ensure_frozen_view(self):
        """Lazily create the overlay view the first time a freeze is requested."""
        if self._frozen_view is not None:
            return
        fv = QTableView(self)
        fv.setModel(self.model())
        fv.setSelectionModel(self.selectionModel())
        fv.setFocusPolicy(Qt.NoFocus)
        fv.verticalHeader().setVisible(False)
        fv.setEditTriggers(self.editTriggers())
        fv.setAlternatingRowColors(self.alternatingRowColors())
        fv.setSelectionBehavior(QAbstractItemView.SelectRows)
        fv.setSelectionMode(QAbstractItemView.ExtendedSelection)
        fv.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        fv.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        fv.setFrameShape(QFrame.NoFrame)
        fv.setHorizontalHeader(_SortHighlightHeader(self, fv))
        fv.horizontalHeader().setSectionsMovable(False)
        fv.horizontalHeader().setHighlightSections(False)
        fv.horizontalHeader().setSortIndicatorShown(True)
        fv.horizontalHeader().sectionClicked.connect(self.on_header_clicked)
        fv.horizontalHeader().sectionResized.connect(self._on_frozen_section_resized)
        fv.setContextMenuPolicy(Qt.CustomContextMenu)
        fv.customContextMenuRequested.connect(lambda _pos: self.show_context_menu(_pos))
        self.verticalScrollBar().valueChanged.connect(fv.verticalScrollBar().setValue)
        fv.verticalScrollBar().valueChanged.connect(self.verticalScrollBar().setValue)
        fv.hide()
        self._frozen_view = fv
        self.update_theme(is_dark=(self.current_theme == 'dark'))

    def set_frozen_columns(self, count: int):
        """Freeze the leading *count* (current visual order) columns so they
        stay visible while scrolling horizontally. count=0 unfreezes."""
        count = max(0, min(count, self.columnCount()))
        if count == self._frozen_col_count:
            return
        was_frozen = self._frozen_col_count > 0
        self._frozen_col_count = count
        hdr = self.horizontalHeader()

        if count > 0:
            self._ensure_frozen_view()
            if not was_frozen:
                # Column order can't change while frozen — the frozen view's
                # own header order is only synced once, at freeze time (see
                # _sync_frozen_header_order), not continuously.
                self._movable_before_freeze = hdr.sectionsMovable()
            hdr.setSectionsMovable(False)
            self._sync_frozen_header_order()
            self._apply_frozen_visibility()
            self._frozen_view.show()
            self._update_frozen_geometry()
        else:
            if self._movable_before_freeze is not None:
                hdr.setSectionsMovable(self._movable_before_freeze)
                self._movable_before_freeze = None
            if self._frozen_view is not None:
                self._frozen_view.hide()

    def _sync_frozen_header_order(self):
        """Arrange the frozen view's header sections in the same
        left-to-right visual order as the main header, for the columns
        being frozen (runs once per freeze, since reordering is disabled
        while a freeze is active)."""
        main_hdr = self.horizontalHeader()
        frozen_hdr = self._frozen_view.horizontalHeader()
        for target_visual in range(self._frozen_col_count):
            logical = main_hdr.logicalIndex(target_visual)
            current_visual = frozen_hdr.visualIndex(logical)
            if current_visual != target_visual:
                frozen_hdr.moveSection(current_visual, target_visual)

    def _apply_frozen_visibility(self):
        """Show only the frozen columns in the overlay view; the main view
        keeps showing all columns as before (frozen ones just end up
        scrolled out of its visible area once the overlay pins them)."""
        if self._frozen_view is None:
            return
        hdr = self.horizontalHeader()
        for logical in range(self.columnCount()):
            visual = hdr.visualIndex(logical)
            self._frozen_view.setColumnHidden(
                logical, visual == -1 or visual >= self._frozen_col_count)

    def _on_main_section_resized(self, logical, _old, new):
        if self._frozen_col_count <= 0 or self._frozen_view is None or self._syncing_frozen_width:
            self._update_frozen_geometry()
            return
        if self.horizontalHeader().visualIndex(logical) < self._frozen_col_count:
            self._syncing_frozen_width = True
            self._frozen_view.setColumnWidth(logical, new)
            self._syncing_frozen_width = False
        self._update_frozen_geometry()

    def _on_frozen_section_resized(self, logical, _old, new):
        if self._syncing_frozen_width or self._frozen_view is None:
            return
        self._syncing_frozen_width = True
        self.setColumnWidth(logical, new)
        self._syncing_frozen_width = False
        self._update_frozen_geometry()

    def _update_frozen_geometry(self):
        """Position/size the overlay so it exactly covers the frozen
        columns' header + rows at the left edge of this widget."""
        if self._frozen_col_count <= 0 or self._frozen_view is None:
            return
        hdr = self.horizontalHeader()
        width = sum(self.columnWidth(hdr.logicalIndex(v))
                    for v in range(self._frozen_col_count))
        hsb = self.horizontalScrollBar()
        hsb_h = hsb.height() if hsb.isVisible() else 0
        self._frozen_view.setGeometry(0, 0, width, max(0, self.height() - hsb_h))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_frozen_geometry()

    def _show_header_context_menu(self, position):
        """Right-click a column header: freeze up through that column, or
        unfreeze if a freeze is already active."""
        hdr = self.horizontalHeader()
        logical = hdr.logicalIndexAt(position)
        menu = QMenu(self)
        if logical >= 0:
            visual = hdr.visualIndex(logical)
            header_item = self.horizontalHeaderItem(logical)
            col_name = header_item.text() if header_item else ""
            freeze_action = menu.addAction(f"📌  Freeze Up To “{col_name}”")
            freeze_action.triggered.connect(lambda: self.set_frozen_columns(visual + 1))
        if self._frozen_col_count > 0:
            menu.addAction("Unfreeze Columns").triggered.connect(
                lambda: self.set_frozen_columns(0))
        if menu.actions():
            menu.exec_(hdr.mapToGlobal(position))

    _COL_WIDTH_MIN = 60     # never narrower than this
    _COL_WIDTH_MAX = 300    # never wider than this without manual resize
    _COL_WIDTH_DEF = 120    # default when content is tiny
    # ponytail: fixed count rather than measuring the viewport at load time
    # (viewport().height() isn't reliably final yet on first load — the tab
    # may not have been laid out). 50 rows comfortably fills any realistic
    # window height; upgrade to a dynamic viewport-height calculation if a
    # pathologically tall window ever needs more (issue #26).
    _EMPTY_PLACEHOLDER_ROWS = 50

    def _set_compact_column_widths(self, dataframe):
        """Set column widths: sample the first 50 rows to pick a sensible
        width, clamped to [_COL_WIDTH_MIN, _COL_WIDTH_MAX]. Does NOT
        resize very-wide columns so long text values stay compact."""
        from PySide6.QtGui import QFontMetrics
        from PySide6.QtWidgets import QApplication
        fm = QFontMetrics(QApplication.font())
        hdr = self.horizontalHeader()
        for col_idx, col_name in enumerate(dataframe.columns):
            # Header text width
            header_w = fm.horizontalAdvance(str(col_name)) + 24  # padding
            # Sample up to 50 rows. .map(str) (not .astype(str)): a raw
            # BLOB column holds real bytes (e.g. a binary encryption key,
            # left undecoded on purpose since it's not text), and pandas'
            # astype(str) casts via numpy's string dtype, which tries to
            # UTF-8-decode bytes and crashes the entire page load on the
            # first non-UTF-8 byte. Python's own str() just reprs it.
            sample = dataframe.iloc[:50, col_idx].fillna('').map(str)
            content_w = sample.map(lambda s: fm.horizontalAdvance(str(s))).max() if not sample.empty else 0
            content_w += 20  # cell padding
            best = max(header_w, content_w, self._COL_WIDTH_DEF)
            width = min(best, self._COL_WIDTH_MAX)
            width = max(width, self._COL_WIDTH_MIN)
            hdr.resizeSection(col_idx, width)

        # Cap the widget itself to the columns' total width (+ row header,
        # frame, scrollbar) so a few narrow columns don't stretch across the
        # whole editor with dead space after the last column (issue #29).
        # Uncapped (large) when there's nothing to size to, e.g. no columns.
        if len(dataframe.columns):
            from PySide6.QtWidgets import QStyle
            vheader_w = self.verticalHeader().width() if not self.verticalHeader().isHidden() else 0
            scrollbar_w = QApplication.style().pixelMetric(QStyle.PM_ScrollBarExtent)
            self.setMaximumWidth(hdr.length() + vheader_w + 2 * self.frameWidth() + scrollbar_w)
        else:
            self.setMaximumWidth(16777215)  # QWIDGETSIZE_MAX — no columns, don't constrain

    def keyPressEvent(self, event):
        """Cmd+Enter opens a detail popup for the current cell value."""
        if event.key() == Qt.Key_Return and event.modifiers() == Qt.ControlModifier:
            item = self.currentItem()
            if item and item.text():
                self._open_cell_detail(item)
                return
        super().keyPressEvent(event)

    def _open_cell_detail(self, item):
        """Show a resizable read-only popup with the full cell value."""
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QPlainTextEdit, QDialogButtonBox
        col_name = self.horizontalHeaderItem(item.column()).text() if self.horizontalHeaderItem(item.column()) else ""
        dlg = QDialog(self.window())
        dlg.setWindowTitle(f"Cell value — {col_name}")
        dlg.resize(600, 400)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(10, 10, 10, 10)
        te = QPlainTextEdit()
        te.setPlainText(item.text())
        te.setReadOnly(True)
        te.setFont(QApplication.font())
        te.setStyleSheet("""
            QPlainTextEdit {
                background: #1c1c1e;
                color: #e5e5ea;
                border: 1px solid #3a3a3c;
                border-radius: 4px;
                font-family: 'Menlo', 'Monaco', monospace;
                font-size: 13px;
                padding: 6px;
            }
        """)
        layout.addWidget(te)
        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)
        dlg.exec()

    def apply_column_filter(self, column_index, filter_text):
        """Apply filter to a specific column"""
        if not filter_text:
            if column_index in self.column_filters:
                del self.column_filters[column_index]
        else:
            self.column_filters[column_index] = filter_text.lower()
        
        self._apply_all_filters()
    
    def _apply_all_filters(self):
        """Apply all active column filters"""
        if self.original_data is None or self.original_data.empty:
            return
        
        filtered = self.original_data.copy()
        
        # Apply each column filter
        for col_idx, filter_text in self.column_filters.items():
            if col_idx < len(filtered.columns):
                col_name = filtered.columns[col_idx]
                filtered = filtered[
                    filtered[col_name].map(str).str.lower().str.contains(filter_text, na=False)
                ]
        
        self.filtered_data = filtered
        self._display_data(filtered)
        self.filter_changed.emit()
    
    def get_filter_status(self):
        """Get current filter status"""
        if self.column_filters:
            return f"{len(self.column_filters)} column filter(s) active"
        return ""
    
    def on_item_changed(self, item):
        """Track when an item is modified, push an undo step, and paint
        changed cell + row."""
        row = item.row()
        col = item.column()

        # Formula evaluation
        text = item.text()
        if text.startswith('='):
            self.evaluate_formula(item)
            return

        old_text = self._cell_snapshot.get((row, col), "")
        if old_text == text:
            return  # no actual change from the last known state
        self._cell_snapshot[(row, col)] = text

        self._push_history(_CellEditCommand(row, col, old_text, text))

        if row not in self.new_rows:
            self._recompute_cell_dirty_state(row, col)
        self.changes_made.emit()

    def _recompute_cell_dirty_state(self, row: int, col: int):
        """Update modified_rows/modified_cells for one cell against its
        original DB value (Qt.UserRole) and repaint the row. Used both for
        live edits and for undo/redo restores, so a cell/row that's been
        undone back to its original value is correctly un-highlighted."""
        item = self.item(row, col)
        if item is None:
            return

        original_value = item.data(Qt.UserRole)
        current_value = item.text()
        # See _display_data_impl's identical guard: pd.isna() on a
        # non-scalar (array/list) value is ambiguous, not falsy.
        original_is_na = pd.api.types.is_scalar(original_value) and pd.isna(original_value)
        unchanged = (current_value == "" and original_is_na) or \
            _cell_display_text(original_value) == current_value

        if unchanged:
            self.modified_cells.discard((row, col))
            if not any(r == row for (r, _c) in self.modified_cells):
                self.modified_rows.discard(row)
        else:
            self.modified_cells.add((row, col))
            self.modified_rows.add(row)

        self._repaint_row(row)

    # ── Undo/redo (issue #124) ──────────────────────────────────────────

    def _push_history(self, cmd):
        """Record cmd as the next undoable step, or fold it into the batch
        currently being accumulated (see _begin_batch)."""
        if self._undo_batch is not None:
            self._undo_batch.append(cmd)
            return
        self._undo_stack.append(cmd)
        if len(self._undo_stack) > self._UNDO_LIMIT:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def _begin_batch(self):
        """Start grouping subsequent _push_history calls into one undo step
        (e.g. a paste touching many cells, or duplicating several rows)."""
        self._undo_batch = []

    def _end_batch(self):
        batch, self._undo_batch = self._undo_batch, None
        if not batch:
            return  # nothing actually changed — don't touch the stacks
        self._undo_stack.append(_CompositeCommand(batch))
        if len(self._undo_stack) > self._UNDO_LIMIT:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def undo(self):
        if not self._undo_stack:
            return
        cmd = self._undo_stack.pop()
        cmd.undo(self)
        self._redo_stack.append(cmd)

    def redo(self):
        if not self._redo_stack:
            return
        cmd = self._redo_stack.pop()
        cmd.redo(self)
        self._undo_stack.append(cmd)

    def _restore_cell_text(self, row: int, col: int, text: str):
        """Set a cell's text without going through on_item_changed (so
        restoring it doesn't itself push a new undo step), then bring
        tracking state back in sync."""
        item = self.item(row, col)
        if item is None:
            return
        try:
            self.itemChanged.disconnect(self.on_item_changed)
        except Exception:
            pass
        item.setText(text)
        self.itemChanged.connect(self.on_item_changed)

        self._cell_snapshot[(row, col)] = text
        if row not in self.new_rows:
            self._recompute_cell_dirty_state(row, col)
        else:
            self._repaint_row(row)
        self.changes_made.emit()

    def _insert_row_with_values(self, row: int, values: list):
        """Physically insert a new row at *row* with the given per-column
        text, marking it new. Shifts every row-indexed piece of tracking
        state (and any pending undo/redo commands) that sits at or below
        the insertion point."""
        self._shift_row_refs(row, +1)
        self.insertRow(row)
        try:
            self.itemChanged.disconnect(self.on_item_changed)
        except Exception:
            pass
        for col, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setData(Qt.UserRole, None)
            self.setItem(row, col, item)
            self._cell_snapshot[(row, col)] = value
        self.itemChanged.connect(self.on_item_changed)
        self.new_rows.add(row)
        self._repaint_row(row)
        self.changes_made.emit()

    def _remove_row_and_shift(self, row: int):
        """Physically remove *row* (undo of a row insert) and shift every
        row-indexed piece of tracking state below it back down."""
        self.new_rows.discard(row)
        self.modified_rows.discard(row)
        self.deleted_rows.discard(row)
        self.modified_cells = {(r, c) for (r, c) in self.modified_cells if r != row}
        self._cell_snapshot = {(r, c): v for (r, c), v in self._cell_snapshot.items() if r != row}
        self.removeRow(row)
        self._shift_row_refs(row, -1)
        self.changes_made.emit()

    def _shift_row_refs(self, at_row: int, delta: int):
        """Renumber every row index this widget tracks — the dirty-state
        sets, the cell snapshot, and every command still on either stack or
        the batch in progress — after a physical row insert/remove."""
        self.modified_rows = {_shift_row(r, at_row, delta) for r in self.modified_rows}
        self.new_rows = {_shift_row(r, at_row, delta) for r in self.new_rows}
        self.deleted_rows = {_shift_row(r, at_row, delta) for r in self.deleted_rows}
        self.modified_cells = {
            (_shift_row(r, at_row, delta), c) for (r, c) in self.modified_cells
        }
        self._cell_snapshot = {
            (_shift_row(r, at_row, delta), c): v for (r, c), v in self._cell_snapshot.items()
        }

        for cmd in self._undo_stack:
            cmd.shift_rows(at_row, delta)
        for cmd in self._redo_stack:
            cmd.shift_rows(at_row, delta)
        if self._undo_batch is not None:
            for cmd in self._undo_batch:
                cmd.shift_rows(at_row, delta)


    def _repaint_row(self, row: int):
        """Apply the correct colour to every cell in *row* based on its state."""
        dark = self.current_theme == 'dark'

        if row in self.deleted_rows:
            row_bg   = _D_DEL_ROW   if dark else _L_DEL_ROW
            row_fg   = _D_DEL_TEXT  if dark else _L_DEL_TEXT
            for col in range(self.columnCount()):
                item = self.item(row, col)
                if item:
                    item.setBackground(QBrush(row_bg))
                    item.setForeground(QBrush(row_fg))
            return

        if row in self.new_rows:
            row_bg = _D_NEW_ROW  if dark else _L_NEW_ROW
            row_fg = _D_NEW_TEXT if dark else _L_NEW_TEXT
            for col in range(self.columnCount()):
                item = self.item(row, col)
                if item:
                    item.setBackground(QBrush(row_bg))
                    item.setForeground(QBrush(row_fg))
            return

        if row in self.modified_rows:
            row_bg   = _D_MOD_ROW  if dark else _L_MOD_ROW
            cell_bg  = _D_MOD_CELL if dark else _L_MOD_CELL
            cell_fg  = _D_MOD_TEXT if dark else _L_MOD_TEXT
            default_fg = QColor("#e5e5ea") if dark else QColor("#1c1c1e")
            for col in range(self.columnCount()):
                item = self.item(row, col)
                if item:
                    if (row, col) in self.modified_cells:
                        # Changed cell: bright amber background + amber text
                        item.setBackground(QBrush(cell_bg))
                        item.setForeground(QBrush(cell_fg))
                    else:
                        # Rest of the row: subtle amber tint
                        item.setBackground(QBrush(row_bg))
                        item.setForeground(QBrush(default_fg))
            return

        # Unmodified row — restore default (clear any previous colour)
        default_bg = QColor(0, 0, 0, 0)   # transparent → falls back to stylesheet
        default_fg = QColor("#e5e5ea") if dark else QColor("#1c1c1e")
        for col in range(self.columnCount()):
            item = self.item(row, col)
            if item:
                item.setBackground(QBrush(default_bg))
                item.setForeground(QBrush(default_fg))

    def highlight_row(self, row: int, color: QColor):
        """Legacy helper — delegates to _repaint_row when state is already set."""
        # Colour callers just add the row to the right set first;
        # we repaint via _repaint_row for consistency.
        self._repaint_row(row)
    
    def add_new_row(self):
        """Add a new empty row"""
        row = self.rowCount()
        values = [""] * self.columnCount()
        self._insert_row_with_values(row, values)
        self._push_history(_RowInsertCommand(row, values))

        # Start editing first cell
        self.editItem(self.item(row, 0))

    def delete_selected_rows(self):
        """Mark selected rows for deletion (Ctrl/Cmd+Z steps back through
        them one row at a time via _RowDeleteMarkCommand)."""
        selected_rows = {item.row() for item in self.selectedItems()}

        if not selected_rows:
            return

        self._begin_batch()
        for row in selected_rows:
            if row not in self.deleted_rows:
                self.deleted_rows.add(row)
                self._repaint_row(row)
                self._push_history(_RowDeleteMarkCommand(row))
        self._end_batch()
        self.changes_made.emit()  # Notify parent

    def revert_changes(self):
        """Revert all changes"""
        if self.original_data is not None:
            self.load_data(self.original_data, self.table_name)
    
    def _key_column_indices(self) -> list[int]:
        """Column indices an UPDATE/DELETE WHERE clause should match on.
        Prefers the table's real primary-key column(s) (set via
        set_primary_key_columns() — composite keys included); when those
        aren't known (no PK, or the grid doesn't show one of them — e.g. a
        hand-written SELECT that left a PK column out), falls back to every
        column rather than guessing column 0. Column 0 is very often the
        PK by convention but is not the PK by definition, and treating it
        as one silently produced a WHERE clause that could match zero rows
        (edit appears to do nothing) or more than one (edit lands on the
        wrong row) whenever that convention didn't hold."""
        if self.primary_key_columns:
            name_to_idx = {
                self.horizontalHeaderItem(c).text().lower(): c
                for c in range(self.columnCount())
            }
            idxs = [name_to_idx[name.lower()] for name in self.primary_key_columns
                    if name.lower() in name_to_idx]
            if idxs:
                return idxs
        return list(range(self.columnCount()))

    def get_changes(self):
        """
        Get all changes as SQL statements
        Returns dict with 'updates', 'inserts', 'deletes' lists
        """
        if not self.table_name:
            return None

        changes = {
            'updates': [],
            'inserts': [],
            'deletes': []
        }

        # Columns that uniquely identify a row for UPDATE/DELETE WHERE
        # clauses: the table's real primary key when known, else every
        # column. Column 0 is NOT reliably the primary key (SELECT * order
        # isn't guaranteed to put it first) — using it unconditionally
        # built a WHERE clause that matched zero rows on tables where it
        # wasn't, silently discarding the edit.
        where_cols = self._key_column_indices()

        # Generate UPDATE statements for modified rows
        for row in self.modified_rows:
            if row in self.deleted_rows or row in self.new_rows:
                continue

            set_parts = []

            # Only columns actually edited (self.modified_cells) — not
            # every column in the row. A row containing an untouched BLOB
            # (e.g. a binary key) previously got that column's display
            # text — Python's str(bytes) repr, not the real value —
            # re-sent as a plain string SET on every edit to *any* other
            # column in the same row, corrupting it (or breaking the SQL
            # outright, since that repr text is full of backslashes).
            for col in range(self.columnCount()):
                if (row, col) not in self.modified_cells:
                    continue
                col_name = self.horizontalHeaderItem(col).text()
                item = self.item(row, col)
                new_value = item.text()

                # Escape and quote string values
                if new_value == "":
                    new_value = "NULL"
                else:
                    new_value = _sql_string_literal(new_value)

                set_parts.append(f"{col_name} = {new_value}")

            # Use the original (pre-edit) value of each key column for the
            # WHERE clause, so editing a key column's own value still
            # matches the row it used to be.
            where_parts = self._where_parts_for_row(row, where_cols)

            if set_parts and where_parts:
                sql = f"UPDATE {self.table_name} SET {', '.join(set_parts)} WHERE {' AND '.join(where_parts)};"  # nosec B608
                changes['updates'].append(sql)
        
        # Generate INSERT statements for new rows
        for row in self.new_rows:
            if row in self.deleted_rows:
                continue
            
            columns = []
            values = []
            
            for col in range(self.columnCount()):
                col_name = self.horizontalHeaderItem(col).text()
                item = self.item(row, col)
                value = item.text()
                
                if value != "":
                    columns.append(col_name)
                    values.append(_sql_string_literal(value))
            
            if columns:
                sql = f"INSERT INTO {self.table_name} ({', '.join(columns)}) VALUES ({', '.join(values)});"  # nosec B608
                changes['inserts'].append(sql)
        
        # Generate DELETE statements
        for row in self.deleted_rows:
            if row in self.new_rows:
                continue

            where_parts = self._where_parts_for_row(row, where_cols)

            if where_parts:
                sql = f"DELETE FROM {self.table_name} WHERE {' AND '.join(where_parts)};"  # nosec B608
                changes['deletes'].append(sql)

        return changes

    def _where_parts_for_row(self, row, where_cols):
        where_parts = []
        for col in where_cols:
            col_name = self.horizontalHeaderItem(col).text()
            item = self.item(row, col)
            original_value = item.data(Qt.UserRole)
            # See _display_data_impl's identical guard: pd.isna() on a
            # non-scalar (array/list) value is ambiguous, not falsy — and
            # never actually a NULL (psycopg2 already represents NULL as
            # scalar None).
            if pd.api.types.is_scalar(original_value) and pd.isna(original_value):
                where_parts.append(f"{col_name} IS NULL")
            elif isinstance(original_value, (bytes, bytearray)):
                # A BLOB column's raw value needs a binary literal (X'...')
                # to match — a quoted string of its hex/repr text would
                # never equal the column's actual binary content.
                where_parts.append(f"{col_name} = X'{original_value.hex()}'")
            else:
                where_parts.append(f"{col_name} = {_sql_string_literal(str(original_value))}")
        return where_parts
    
    def _apply_sort_header_labels(self):
        """Draw the active sort column/direction directly into the header
        text. Qt's native QHeaderView sort arrow (setSortIndicator) is
        unreliable once QHeaderView::section carries a custom stylesheet —
        the arrow sub-control silently stops rendering — so the indicator
        is spelled out in the label itself instead. The section highlight
        itself is painted in _SortHighlightHeader.paintSection, since the
        same stylesheet quirk also makes Qt ignore the header item's
        background/foreground roles."""
        if self.filtered_data is None:
            return
        arrow = " ▲" if self._sort_asc else " ▼"
        for col, name in enumerate(self.filtered_data.columns):
            item = self.horizontalHeaderItem(col)
            if item is None:
                continue
            item.setText(f"{name}{arrow}" if col == self._sort_col else str(name))
        self.horizontalHeader().viewport().update()
        if self._frozen_view is not None:
            self._frozen_view.horizontalHeader().viewport().update()

    def on_header_clicked(self, col: int):
        """Sort the currently displayed data by the clicked column (client-side)."""
        if self.filtered_data is None or self.filtered_data.empty:
            return

        # Sorting while there are unsaved edits would corrupt the row-index
        # tracking (modified_rows/modified_cells) because sort reorders rows.
        # Block it — the same behaviour as TablePlus.
        if self.has_changes():
            return

        # Toggle direction if same column, else start ascending
        if self._sort_col == col:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col
            self._sort_asc = True

        order = Qt.AscendingOrder if self._sort_asc else Qt.DescendingOrder
        self.horizontalHeader().setSortIndicator(col, order)
        if self._frozen_view is not None:
            self._frozen_view.horizontalHeader().setSortIndicator(col, order)

        col_name = self.filtered_data.columns[col]
        self.filtered_data = self.filtered_data.sort_values(
            col_name, ascending=self._sort_asc, na_position='last'
        ).reset_index(drop=True)
        self._display_data(self.filtered_data)
    
    def has_changes(self):
        """Check if there are any uncommitted changes"""
        return bool(self.modified_rows or self.new_rows or self.deleted_rows)
    
    # ── Signals used by FK navigation and filter chips ───────────────────────
    from PySide6.QtCore import Signal as _Signal
    filter_by_value  = _Signal(str, str, str)  # (column_name, operator, value)
    navigate_fk      = _Signal(str, str, str)  # (ref_table, ref_column, value)
    show_structure   = _Signal(str)        # (table_name)

    def show_context_menu(self, position):
        """Show comprehensive context menu like TablePlus"""
        menu = QMenu(self)

        selected_items = self.selectedItems()
        has_selection  = len(selected_items) > 0
        current_item   = self.currentItem() if has_selection else None

        if has_selection:
            # ── Quick-look editor (cell viewer) ──────────────────────────────
            ql_action = menu.addAction("🔍  Quick Look Editor")
            ql_action.setShortcut("Ctrl+Return")
            ql_action.triggered.connect(self._quick_look_cell)
            menu.addSeparator()

            # ── Paste / Duplicate ─────────────────────────────────────────────
            paste_action = menu.addAction("Paste")
            paste_action.setShortcut("Ctrl+V")
            paste_action.triggered.connect(self.paste_from_clipboard)
            duplicate_action = menu.addAction("Duplicate")
            duplicate_action.setShortcut("Ctrl+D")
            duplicate_action.triggered.connect(self.duplicate_row)
            menu.addSeparator()

            # ── Copy ──────────────────────────────────────────────────────────
            copy_action = menu.addAction("Copy")
            copy_action.setShortcut("Ctrl+C")
            copy_action.triggered.connect(self.copy_to_clipboard)
            menu.addAction("Copy Cell Value").triggered.connect(self.copy_cell_value)
            menu.addAction("Copy All Column Values").triggered.connect(self.copy_column_values)

            # ── Copy Rows As sub-menu (TablePlus-style) ───────────────────────
            crm = menu.addMenu("Copy Rows As")
            crm.addAction("Plain Text").triggered.connect(
                lambda: self.copy_rows_as("plain"))
            crm.addSeparator()
            crm.addAction("JSON").triggered.connect(
                lambda: self.copy_rows_as("json"))
            crm.addAction("HTML").triggered.connect(
                lambda: self.copy_rows_as("html"))
            crm.addAction("Markdown Table").triggered.connect(
                lambda: self.copy_rows_as("markdown"))
            crm.addSeparator()
            crm.addAction("CSV").triggered.connect(
                lambda: self.copy_rows_as("csv"))
            crm.addAction("CSV include fields name").triggered.connect(
                lambda: self.copy_rows_as("csv_header"))
            crm.addSeparator()
            crm.addAction("SQL Insert Statement").triggered.connect(
                lambda: self.copy_rows_as("sql_insert"))
            crm.addAction("SQL Insert Statement (no auto_inc)").triggered.connect(
                lambda: self.copy_rows_as("sql_insert_no_id"))

            menu.addSeparator()

            # -- Quick Filter submenu ----------------------------------------
            if current_item:
                col_name = (self.horizontalHeaderItem(current_item.column()).text()
                            if self.horizontalHeaderItem(current_item.column()) else "")
                cell_val = current_item.text()

                qf = menu.addMenu("Quick Filter")

                def _add(label, op, val=cell_val, cn=col_name):
                    qf.addAction(label).triggered.connect(
                        lambda: self.filter_by_value.emit(cn, op, val))

                _add(f"{col_name} = '{cell_val}'",           "=")
                _add(f"{col_name} <> '{cell_val}'",          "<>")
                _add(f"{col_name} < '{cell_val}'",           "<")
                _add(f"{col_name} > '{cell_val}'",           ">")
                _add(f"{col_name} <= '{cell_val}'",          "<=")
                _add(f"{col_name} >= '{cell_val}'",          ">=")
                qf.addSeparator()
                _add(f"{col_name} Contains '{cell_val}'",     "CONTAINS")
                _add(f"{col_name} Not contains '{cell_val}'", "NOT CONTAINS")
                qf.addSeparator()
                _add(f"{col_name} Has prefix '{cell_val}'",   "STARTS WITH")
                _add(f"{col_name} Has suffix '{cell_val}'",   "ENDS WITH")
                qf.addSeparator()
                _add(f"{col_name} IN ({cell_val})",           "IN")
                _add(f"{col_name} NOT IN ({cell_val})",       "NOT IN")
                qf.addSeparator()
                _add(f"{col_name} IS NULL",                   "IS NULL",     "")
                _add(f"{col_name} IS NOT NULL",               "IS NOT NULL", "")

                # ── FK navigation ────────────────────────────────────────────
                fk_info = getattr(self, '_fk_map', {}).get(col_name)
                if fk_info:
                    ref_tbl = fk_info['ref_table']
                    ref_col = fk_info['ref_column']
                    fk_action = menu.addAction(
                        f"🔗  Go to {ref_tbl}.{ref_col} = ‘{cell_val[:20]}'")
                    fk_action.triggered.connect(
                        lambda: self.navigate_fk.emit(ref_tbl, ref_col, cell_val))

            menu.addSeparator()

            # ── Export ────────────────────────────────────────────────────────
            menu.addAction("Export result...").triggered.connect(self.export_selected)
            # Issue #123: "Export result..." above exports everything
            # regardless of selection despite export_selected()'s name —
            # this is the actual selection-aware counterpart, kept
            # alongside it rather than changing the existing action.
            menu.addAction("Export Selected Rows...").triggered.connect(
                self.export_selected_rows)
            menu.addSeparator()

            # ── Delete / NULL / Default ───────────────────────────────────────
            del_action = menu.addAction("Delete")
            del_action.setShortcut("Delete")
            del_action.triggered.connect(self.delete_selected_rows)
            menu.addSeparator()
            menu.addAction("Set NULL").triggered.connect(self.set_cell_null)
            menu.addAction("Set Default Value").triggered.connect(self.set_cell_default)

        else:
            menu.addAction("Add New Row").triggered.connect(self.add_new_row)

        menu.addSeparator()
        undo_action = menu.addAction("Undo")
        undo_action.setShortcut(QKeySequence.Undo)
        undo_action.setEnabled(bool(self._undo_stack))
        undo_action.triggered.connect(self.undo)
        redo_action = menu.addAction("Redo")
        redo_action.setShortcut(QKeySequence.Redo)
        redo_action.setEnabled(bool(self._redo_stack))
        redo_action.triggered.connect(self.redo)
        menu.addSeparator()
        menu.addAction("Revert Changes").triggered.connect(self.revert_changes)
        # QCursor.pos() rather than self.mapToGlobal(position): this menu can
        # also be triggered from the frozen overlay view (issue #125), whose
        # local coordinates aren't in self's coordinate space.
        menu.exec_(QCursor.pos())

    def set_fk_map(self, fk_list: list):
        """Store FK metadata: list of {column, ref_table, ref_column} dicts."""
        self._fk_map = {fk['column']: fk for fk in (fk_list or [])}

    def _quick_look_cell(self):
        """Open a resizable text viewer for the current cell value."""
        item = self.currentItem()
        if not item:
            return
        col_name = (self.horizontalHeaderItem(item.column()).text()
                    if self.horizontalHeaderItem(item.column()) else "col")
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QPlainTextEdit, QDialogButtonBox
        dlg = QDialog(self.window())
        dlg.setWindowTitle(f"🔍 {col_name}")
        dlg.resize(520, 320)
        lay = QVBoxLayout(dlg)
        te = QPlainTextEdit(item.text())
        te.setReadOnly(True)
        lay.addWidget(te)
        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(dlg.accept)
        lay.addWidget(btns)
        dlg.exec()

    # ── Copy rows in multiple formats ─────────────────────────────────────────

    def _selected_rows_data(self) -> tuple[list[str], list[list[str]]]:
        """Return (headers, [[row values], ...]) for currently selected rows,
        in the columns' current *visual* (on-screen) order — matters once
        columns are drag-reordered (issue #122); logical column index no
        longer matches display order at that point."""
        rows = sorted({item.row() for item in self.selectedItems()})
        hdr = self.horizontalHeader()
        cols = [hdr.logicalIndex(v) for v in range(self.columnCount())]
        headers = [self.horizontalHeaderItem(c).text()
                   if self.horizontalHeaderItem(c) else str(c) for c in cols]
        data = []
        for r in rows:
            data.append([self.item(r, c).text() if self.item(r, c) else "" for c in cols])
        return headers, data

    def copy_rows_as(self, fmt: str):
        """Copy selected rows to clipboard in the requested format."""
        from PySide6.QtWidgets import QApplication
        import json
        headers, rows = self._selected_rows_data()
        if not rows:
            return

        if fmt == "plain":
            lines = ["\t".join(headers)]
            for row in rows:
                lines.append("\t".join(row))
            text = "\n".join(lines)

        elif fmt == "json":
            objs = [dict(zip(headers, row)) for row in rows]
            text = json.dumps(objs if len(objs) > 1 else objs[0], indent=2, ensure_ascii=False)

        elif fmt == "html":
            th = "".join(f"<th>{h}</th>" for h in headers)
            trs = ""
            for row in rows:
                tds = "".join(f"<td>{v}</td>" for v in row)
                trs += f"<tr>{tds}</tr>\n"
            text = f"<table>\n<thead><tr>{th}</tr></thead>\n<tbody>\n{trs}</tbody></table>"

        elif fmt == "markdown":
            sep = "| " + " | ".join("-" * max(len(h), 3) for h in headers) + " |"
            hdr = "| " + " | ".join(headers) + " |"
            body = "\n".join("| " + " | ".join(row) + " |" for row in rows)
            text = f"{hdr}\n{sep}\n{body}"

        elif fmt == "csv":
            lines = [",".join(f'"{v}"' for v in row) for row in rows]
            text = "\n".join(lines)

        elif fmt == "csv_header":
            lines = [",".join(f'"{h}"' for h in headers)]
            for row in rows:
                lines.append(",".join(f'"{v}"' for v in row))
            text = "\n".join(lines)

        elif fmt == "sql_insert":
            tbl = self.table_name or "table"
            col_list = ", ".join(f"`{h}`" for h in headers)
            stmts = []
            for row in rows:
                vals = ", ".join(
                    "NULL" if v == "" else _sql_string_literal(v)
                    for v in row
                )
                stmts.append(f"INSERT INTO `{tbl}` ({col_list}) VALUES ({vals});")  # nosec B608
            text = "\n".join(stmts)

        elif fmt == "sql_insert_no_id":
            # Exclude the first column if its name is 'id' or ends with '_id' and
            # looks like an auto-increment primary key (single-word, all lower/upper).
            tbl = self.table_name or "table"
            skip = {i for i, h in enumerate(headers)
                    if h.lower() == "id" or h.lower().endswith("_id") and i == 0}
            # If no obvious id column found, skip index 0 by default
            if not skip:
                skip = {0}
            filt_headers = [h for i, h in enumerate(headers) if i not in skip]
            col_list = ", ".join(f"`{h}`" for h in filt_headers)
            stmts = []
            for row in rows:
                filt_vals = [v for i, v in enumerate(row) if i not in skip]
                vals = ", ".join(
                    "NULL" if v == "" else _sql_string_literal(v)
                    for v in filt_vals
                )
                stmts.append(f"INSERT INTO `{tbl}` ({col_list}) VALUES ({vals});")  # nosec B608
            text = "\n".join(stmts)
        else:
            text = ""

        QApplication.clipboard().setText(text)

    # keep legacy single-row method for backward compat
    def copy_row_as(self, format_type):
        _map = {"json": "json", "csv": "csv", "sql": "sql_insert"}
        self.copy_rows_as(_map.get(format_type, format_type))

    def copy_to_clipboard(self):
        """Copy selected cells to clipboard, in visual column order (issue
        #122 — plain sorted() on logical index would silently reorder
        pasted output once columns have been drag-reordered)."""
        from PySide6.QtWidgets import QApplication
        selected = self.selectedItems()
        if not selected:
            return
        rows = sorted(set(item.row() for item in selected))
        hdr = self.horizontalHeader()
        cols = sorted(set(item.column() for item in selected), key=hdr.visualIndex)
        text = []
        for row in rows:
            row_data = [self.item(row, col).text() if self.item(row, col) else "" for col in cols]
            text.append("\t".join(row_data))
        QApplication.clipboard().setText("\n".join(text))

    def copy_cell_value(self):
        """Copy current cell value"""
        from PySide6.QtWidgets import QApplication
        current = self.currentItem()
        if current:
            QApplication.clipboard().setText(current.text())

    def copy_column_values(self):
        """Copy all values from selected column"""
        from PySide6.QtWidgets import QApplication
        current = self.currentItem()
        if not current:
            return
        col = current.column()
        values = [self.item(row, col).text() for row in range(self.rowCount())
                  if self.item(row, col)]
        QApplication.clipboard().setText("\n".join(values))

    def paste_from_clipboard(self):
        """Paste from clipboard, walking *visually* adjacent columns from
        the anchor cell (issue #122) — walking logical index would paste
        into the wrong columns once they've been drag-reordered, since
        adjacent logical indices are no longer adjacent on screen."""
        from PySide6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        text = clipboard.text()

        if not text:
            return

        current = self.currentItem()
        if not current:
            return

        start_row = current.row()
        hdr = self.horizontalHeader()
        start_visual_col = hdr.visualIndex(current.column())

        # Parse clipboard (tab-separated); grouped into one undo step.
        self._begin_batch()
        lines = text.split("\n")
        for i, line in enumerate(lines):
            values = line.split("\t")
            for j, value in enumerate(values):
                row = start_row + i
                visual_col = start_visual_col + j
                if row < self.rowCount() and visual_col < self.columnCount():
                    col = hdr.logicalIndex(visual_col)
                    item = self.item(row, col)
                    if item:
                        item.setText(value)
        self._end_batch()

    def duplicate_row(self):
        """Duplicate current row"""
        current = self.currentItem()
        if not current:
            return

        source_row = current.row()
        values = [self.item(source_row, col).text() if self.item(source_row, col) else ""
                  for col in range(self.columnCount())]
        row = source_row + 1
        self._insert_row_with_values(row, values)
        self._push_history(_RowInsertCommand(row, values))

    def export_selected(self):
        """Export visible table data to CSV / JSON / Excel / SQL — despite
        the name, this has always exported *all* rows (filtered/original),
        not the current selection; kept as-is since it's a useful action on
        its own. export_selected_rows() below is the actual
        selection-scoped export (issue #123)."""
        df = self.filtered_data if self.filtered_data is not None else self.original_data
        export_dataframe(self, df, f"{self.table_name or 'data'}.csv", self.table_name or "table")

    def export_selected_rows(self):
        """Export only the currently-selected rows (issue #123). Reads
        from the same typed DataFrame export_selected() uses (not the
        grid's stringified cell text, unlike copy_rows_as()) so numeric/
        date columns keep their real types in JSON/Excel output — row
        positions in filtered_data/original_data always match the grid's
        row indices 1:1, since load_data() reloads both together."""
        selected_rows = sorted({item.row() for item in self.selectedItems()})
        if not selected_rows:
            QMessageBox.information(self, "Export", "No rows selected.")
            return
        base_df = self.filtered_data if self.filtered_data is not None else self.original_data
        if base_df is None:
            QMessageBox.information(self, "Export", "No data to export.")
            return
        df = base_df.iloc[selected_rows]
        export_dataframe(
            self, df, f"{self.table_name or 'data'}_selection.csv", self.table_name or "table")
    
    def set_cell_null(self):
        """Set current cell to NULL"""
        current = self.currentItem()
        if current:
            current.setText("")
    
    def set_cell_default(self):
        """Set cell to default value"""
        current = self.currentItem()
        if current:
            original = current.data(Qt.UserRole)
            if original is not None:
                current.setText(_cell_display_text(original))
    
    def duplicate_selected_rows(self):
        """Duplicate all selected rows (Cmd+D)"""
        selected_rows = sorted(set(item.row() for item in self.selectedItems()))

        if not selected_rows:
            return

        self._begin_batch()
        # Duplicate each row from bottom to top so a source row's index is
        # never disturbed by an insertion made earlier in this same loop.
        for source_row in reversed(selected_rows):
            values = [self.item(source_row, col).text() if self.item(source_row, col) else ""
                      for col in range(self.columnCount())]
            row = source_row + 1
            self._insert_row_with_values(row, values)
            self._push_history(_RowInsertCommand(row, values))
        self._end_batch()

    def bulk_edit_dialog(self):
        """Show dialog to edit multiple rows at once"""
        selected_rows = sorted(set(item.row() for item in self.selectedItems()))
        
        if len(selected_rows) < 2:
            QMessageBox.information(self, "Bulk Edit", "Please select at least 2 rows to bulk edit")
            return
        
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QLineEdit, QPushButton
        
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Bulk Edit {len(selected_rows)} Rows")
        dialog.setMinimumWidth(400)
        
        layout = QVBoxLayout(dialog)
        
        # Info label
        info = QLabel(f"Edit column value for {len(selected_rows)} selected rows:")
        layout.addWidget(info)
        
        # Column selector
        col_layout = QHBoxLayout()
        col_layout.addWidget(QLabel("Column:"))
        column_combo = QComboBox()
        for col in range(self.columnCount()):
            column_combo.addItem(self.horizontalHeaderItem(col).text())
        col_layout.addWidget(column_combo)
        layout.addLayout(col_layout)
        
        # Value input
        val_layout = QHBoxLayout()
        val_layout.addWidget(QLabel("New Value:"))
        value_input = QLineEdit()
        value_input.setPlaceholderText("Enter value or formula (e.g., =UPPER({value}))")
        val_layout.addWidget(value_input)
        layout.addLayout(val_layout)
        
        # Help text
        help_label = QLabel("Tip: Use {value} to reference current value (e.g., =UPPER({value}))")
        help_label.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(help_label)
        
        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(cancel_btn)
        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(dialog.accept)
        apply_btn.setDefault(True)
        btn_layout.addWidget(apply_btn)
        layout.addLayout(btn_layout)
        
        if dialog.exec_() == QDialog.Accepted:
            column_idx = column_combo.currentIndex()
            new_value = value_input.text()

            # itemChanged stays connected so on_item_changed's normal
            # dirty-tracking/undo capture runs per cell (grouped into one
            # undo step via the batch below).
            self._begin_batch()
            for row in selected_rows:
                item = self.item(row, column_idx)
                if item:
                    # Check if it's a formula with {value} placeholder
                    if '{value}' in new_value:
                        old_value = item.text()
                        result = new_value.replace('{value}', old_value)
                        if result.startswith('='):
                            # Evaluate formula
                            result = self.evaluate_formula_string(result, old_value)
                        item.setText(result)
                    else:
                        item.setText(new_value)
            self._end_batch()

    def evaluate_formula(self, item):
        """Evaluate formula in cell (e.g., =NOW(), =UPPER(text))"""
        formula = item.text()
        if not formula.startswith('='):
            return
        
        result = self.evaluate_formula_string(formula)
        
        # Disconnect to avoid recursion
        self.itemChanged.disconnect(self.on_item_changed)
        item.setText(result)
        self.itemChanged.connect(self.on_item_changed)
    
    def evaluate_formula_string(self, formula, context_value=None):
        """Evaluate a formula string and return result"""
        formula = formula[1:]  # Remove '='
        formula_upper = formula.upper()
        
        try:
            # Date/Time functions
            if formula_upper == 'NOW()':
                from datetime import datetime
                return datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            elif formula_upper == 'TODAY()':
                from datetime import date
                return date.today().strftime('%Y-%m-%d')
            elif formula_upper == 'TIMESTAMP()':
                import time
                return str(int(time.time()))
            
            # Text functions
            elif formula_upper.startswith('UPPER('):
                text = formula[6:-1]  # Extract content between UPPER( and )
                if context_value:
                    text = context_value
                return text.upper()
            elif formula_upper.startswith('LOWER('):
                text = formula[6:-1]
                if context_value:
                    text = context_value
                return text.lower()
            elif formula_upper.startswith('TRIM('):
                text = formula[5:-1]
                if context_value:
                    text = context_value
                return text.strip()
            
            # Math functions
            elif formula_upper.startswith('RANDOM('):
                # Spreadsheet-style RAND()/RANDBETWEEN() cell formula, not a
                # security/crypto value — the stdlib PRNG is the right tool.
                import random
                params = formula[7:-1].split(',')
                if len(params) == 2:
                    return str(random.randint(int(params[0]), int(params[1])))  # nosec B311
                else:
                    return str(random.random())  # nosec B311
            
            # Try to evaluate as an arithmetic expression
            elif any(op in formula for op in ['+', '-', '*', '/', '%']):
                # AST-whitelisted, not eval() — see _safe_eval_arithmetic
                # (issue #161). This formula can be reached with database-
                # sourced content (bulk column edit's {value} substitution
                # splices in the cell's current DB value), so it must be
                # safe for arbitrary attacker-controlled input, not just
                # the local user's own typed formula.
                result = _safe_eval_arithmetic(formula)
                return str(result)
            
            return formula  # Return as-is if not recognized
        except Exception:
            return f"#ERROR: {formula}"