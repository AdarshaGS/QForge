"""Side panels for the Visual Query Builder (ui/query_builder_dialog.py).
SELECT-list picker lands here first (VQB.3, issue #193); WHERE/HAVING
(VQB.4) and GROUP BY/ORDER BY/LIMIT (VQB.5) join it as siblings in the
same panel stack per the design note (ai/vqb-design-spike.md).

Column metadata comes from the same ErdGraph the join canvas already
loaded (services/erd_model.py) — no separate schema fetch.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from services.query_builder_model import AGGREGATE_FUNCS

_TABLE_ROLE = Qt.UserRole
_COLUMN_ROLE = Qt.UserRole + 1


class SelectPanel(QWidget):
    """Per-table column checkboxes (grouped, one section per table on the
    canvas) with an aggregate combo + alias field per row, plus a small
    add-your-own-expression list below. Mutates *state* directly and calls
    on_change() after every edit; owns no query-execution/SQL-generation
    logic itself (VQB.5)."""

    def __init__(self, state, on_change, parent=None):
        super().__init__(parent)
        self._state = state
        self._on_change = on_change
        self._updating = False  # guards itemChanged while refresh() rebuilds rows

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        title = QLabel("SELECT")
        f = title.font()
        f.setBold(True)
        title.setFont(f)
        layout.addWidget(title)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Column", "Aggregate", "Alias"])
        self.tree.setColumnWidth(0, 150)
        self.tree.setColumnWidth(1, 80)
        self.tree.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.tree, 1)

        layout.addWidget(QLabel("Expressions"))

        expr_row = QHBoxLayout()
        self.expr_input = QLineEdit()
        self.expr_input.setPlaceholderText("e.g. price * qty")
        self.expr_alias_input = QLineEdit()
        self.expr_alias_input.setPlaceholderText("Alias")
        self.expr_alias_input.setMaximumWidth(90)
        add_expr_btn = QPushButton("+ Add")
        add_expr_btn.clicked.connect(self._add_expression)
        expr_row.addWidget(self.expr_input, 1)
        expr_row.addWidget(self.expr_alias_input)
        expr_row.addWidget(add_expr_btn)
        layout.addLayout(expr_row)

        self.expr_list = QTreeWidget()
        self.expr_list.setHeaderLabels(["Expression", "Alias", ""])
        self.expr_list.setColumnWidth(2, 28)
        self.expr_list.setMaximumHeight(120)
        layout.addWidget(self.expr_list)

    # ── rebuilding rows from the canvas's current tables ────────────────

    def refresh(self, graph, table_names: list):
        self._updating = True
        try:
            self.tree.clear()
            for table_name in table_names:
                table = graph.tables.get(table_name) if graph else None
                if table is None:
                    continue
                table_item = QTreeWidgetItem(self.tree, [table_name])
                f = table_item.font(0)
                f.setBold(True)
                table_item.setFont(0, f)
                table_item.setFlags(Qt.ItemIsEnabled)
                for col in table.columns:
                    self._add_column_row(table_item, table_name, col.name)
            self.tree.expandAll()
        finally:
            self._updating = False
        self._refresh_expression_list()

    def _add_column_row(self, table_item, table_name: str, column_name: str):
        item = QTreeWidgetItem(table_item, [column_name])
        item.setData(0, _TABLE_ROLE, table_name)
        item.setData(0, _COLUMN_ROLE, column_name)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        selected = self._state.get_select_item(table_name, column_name)
        item.setCheckState(0, Qt.Checked if selected else Qt.Unchecked)

        combo = QComboBox()
        combo.addItems(list(AGGREGATE_FUNCS))
        combo.setEnabled(selected is not None)
        if selected:
            combo.setCurrentText(selected.aggregate)
        combo.currentTextChanged.connect(
            lambda text, t=table_name, c=column_name: self._on_aggregate_changed(t, c, text))
        self.tree.setItemWidget(item, 1, combo)

        alias_edit = QLineEdit()
        alias_edit.setEnabled(selected is not None)
        if selected:
            alias_edit.setText(selected.alias)
        alias_edit.editingFinished.connect(
            lambda t=table_name, c=column_name, w=alias_edit: self._on_alias_changed(t, c, w.text()))
        self.tree.setItemWidget(item, 2, alias_edit)

    # ── column checkbox / aggregate / alias handlers ────────────────────

    def _on_item_changed(self, item, column_index):
        if self._updating or column_index != 0:
            return
        table_name = item.data(0, _TABLE_ROLE)
        column_name = item.data(0, _COLUMN_ROLE)
        if table_name is None:
            return  # a table header row, not a column row
        checked = item.checkState(0) == Qt.Checked
        self._state.set_column_selected(table_name, column_name, checked)
        combo = self.tree.itemWidget(item, 1)
        alias_edit = self.tree.itemWidget(item, 2)
        if combo is not None:
            combo.setEnabled(checked)
        if alias_edit is not None:
            alias_edit.setEnabled(checked)
        self._on_change()

    def _on_aggregate_changed(self, table_name: str, column_name: str, aggregate: str):
        if self._updating:
            return
        self._state.set_aggregate(table_name, column_name, aggregate)
        self._on_change()

    def _on_alias_changed(self, table_name: str, column_name: str, alias: str):
        if self._updating:
            return
        self._state.set_column_alias(table_name, column_name, alias)
        self._on_change()

    # ── expression columns ───────────────────────────────────────────────

    def _add_expression(self):
        expr = self.expr_input.text().strip()
        if not expr:
            return
        alias = self.expr_alias_input.text().strip()
        self._state.add_expression(expr, alias)
        self.expr_input.clear()
        self.expr_alias_input.clear()
        self._refresh_expression_list()
        self._on_change()

    def _refresh_expression_list(self):
        self.expr_list.clear()
        for item in self._state.expression_items():
            row = QTreeWidgetItem(self.expr_list, [item.expression, item.alias, ""])
            remove_btn = QPushButton("✕")
            remove_btn.setFixedWidth(24)
            remove_btn.clicked.connect(lambda _checked=False, it=item: self._remove_expression(it))
            self.expr_list.setItemWidget(row, 2, remove_btn)

    def _remove_expression(self, item):
        self._state.remove_select_item(item)
        self._refresh_expression_list()
        self._on_change()
