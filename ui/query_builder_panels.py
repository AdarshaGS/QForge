"""Side panels for the Visual Query Builder (ui/query_builder_dialog.py).
SELECT-list picker (VQB.3, issue #193), WHERE/HAVING filter builder
(VQB.4, issue #194), and GROUP BY/ORDER BY/LIMIT (VQB.5, issue #195) are
siblings in the same panel stack per the design note
(ai/vqb-design-spike.md).

Column metadata comes from the same ErdGraph the join canvas already
loaded (services/erd_model.py) — no separate schema fetch.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from services.query_builder_model import (
    AGGREGATE_FUNCS, FILTER_OPERATORS, ORDER_DIRECTIONS, FilterCondition,
    FilterGroup,
)

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
        self._build_ui(layout)

    def set_state(self, state):
        """Repoint this panel at a different QueryBuilderState — used when
        loading a saved visual query replaces self.state wholesale
        (ui/query_builder_dialog.py's _load_saved_query)."""
        self._state = state

    def _build_ui(self, layout):

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


class FilterPanel(QWidget):
    """WHERE or HAVING filter-tree editor (VQB.4, issue #194) — one
    instance per target ("WHERE"/"HAVING"), rendering a FilterGroup tree
    (services/query_builder_model.py) as nested rows: group rows show an
    AND/OR combo plus add/remove controls, leaf rows show column/operator/
    value editors. AdvancedFilterDialog/DataFilterDialog only support a
    flat AND-only list, so this is a new tree widget rather than a reused
    one — but its operator choices (FILTER_OPERATORS) are ported from
    AdvancedFilterDialog rather than invented fresh. Mutates *root*
    in place and calls on_change() after every edit, same convention as
    SelectPanel."""

    def __init__(self, label: str, root: FilterGroup, on_change, parent=None):
        super().__init__(parent)
        self._root = root
        self._on_change = on_change
        self._columns = []  # list[(display_label, table, column)]

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        title = QLabel(label)
        f = title.font()
        f.setBold(True)
        title.setFont(f)
        layout.addWidget(title)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(1)
        layout.addWidget(self.tree, 1)

        add_row = QHBoxLayout()
        add_cond_btn = QPushButton("+ Condition")
        add_cond_btn.clicked.connect(lambda: self._add_condition(self._root))
        add_group_btn = QPushButton("+ Group")
        add_group_btn.clicked.connect(lambda: self._add_group(self._root))
        add_row.addWidget(add_cond_btn)
        add_row.addWidget(add_group_btn)
        add_row.addStretch()
        layout.addLayout(add_row)

    def set_root(self, root: FilterGroup):
        """Repoint this panel at a different FilterGroup tree — used when
        loading a saved visual query (ui/query_builder_dialog.py's
        _load_saved_query)."""
        self._root = root

    # ── column source (table.column pairs, plus — for HAVING — aggregate/
    # expression SELECT items, since HAVING typically filters on those
    # rather than raw columns) ───────────────────────────────────────────

    def set_columns(self, columns: list):
        """*columns*: list of (display_label, table, column) tuples this
        panel's condition rows can pick from — refreshed by the dialog
        whenever the canvas's tables or SELECT list changes. For a
        "column" entry, *column* holds the bare column name (qualified
        with *table* in SQL); for a synthetic HAVING entry (an aggregate
        or expression), *table* is "" and *column* already holds the full
        expression text, per FilterCondition.to_sql's table-optional
        qualification."""
        self._columns = columns
        self.refresh()

    def refresh(self):
        self.tree.clear()
        self._build_group_rows(self._root, self.tree.invisibleRootItem())
        self.tree.expandAll()

    def _build_group_rows(self, group: FilterGroup, parent_item):
        for child in list(group.children):
            if isinstance(child, FilterGroup):
                item = QTreeWidgetItem(parent_item)
                self.tree.setItemWidget(item, 0, self._group_row_widget(group, child))
                self._build_group_rows(child, item)
            else:
                item = QTreeWidgetItem(parent_item)
                self.tree.setItemWidget(item, 0, self._condition_row_widget(group, child))

    def _group_row_widget(self, parent_group: FilterGroup, group: FilterGroup) -> QWidget:
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel("Group:"))
        combo = QComboBox()
        combo.addItems(["AND", "OR"])
        combo.setCurrentText(group.conjunction)
        combo.currentTextChanged.connect(lambda text, g=group: self._set_conjunction(g, text))
        row.addWidget(combo)
        add_cond_btn = QPushButton("+ Condition")
        add_cond_btn.clicked.connect(lambda _checked=False, g=group: self._add_condition(g))
        add_group_btn = QPushButton("+ Group")
        add_group_btn.clicked.connect(lambda _checked=False, g=group: self._add_group(g))
        row.addWidget(add_cond_btn)
        row.addWidget(add_group_btn)
        row.addStretch()
        remove_btn = QPushButton("✕")
        remove_btn.setFixedWidth(24)
        remove_btn.clicked.connect(lambda _checked=False, p=parent_group, g=group: self._remove_node(p, g))
        row.addWidget(remove_btn)
        return w

    def _condition_row_widget(self, parent_group: FilterGroup, cond: FilterCondition) -> QWidget:
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)

        col_combo = QComboBox()
        for label, table, column in self._columns:
            col_combo.addItem(label, (table, column))
        idx = self._index_for(cond)
        if idx >= 0:
            col_combo.setCurrentIndex(idx)
        col_combo.currentIndexChanged.connect(
            lambda i, c=cond, combo=col_combo: self._set_condition_column(c, combo.itemData(i)))

        op_combo = QComboBox()
        op_combo.addItems(list(FILTER_OPERATORS))
        op_combo.setCurrentText(cond.operator)
        op_combo.currentTextChanged.connect(lambda text, c=cond: self._set_condition_operator(c, text))

        value_edit = QLineEdit()
        value_edit.setText(cond.value)
        value_edit.setPlaceholderText("Value…")
        value_edit.editingFinished.connect(
            lambda c=cond, w=value_edit: self._set_condition_value(c, w.text()))

        remove_btn = QPushButton("✕")
        remove_btn.setFixedWidth(24)
        remove_btn.clicked.connect(lambda _checked=False, p=parent_group, c=cond: self._remove_node(p, c))

        row.addWidget(col_combo, 2)
        row.addWidget(op_combo, 1)
        row.addWidget(value_edit, 2)
        row.addWidget(remove_btn)
        return w

    def _index_for(self, cond: FilterCondition) -> int:
        for i, (_label, table, column) in enumerate(self._columns):
            if table == cond.table and column == cond.column:
                return i
        return -1

    # ── mutation handlers ────────────────────────────────────────────────

    def _add_condition(self, group: FilterGroup):
        table, column = (self._columns[0][1], self._columns[0][2]) if self._columns else ("", "")
        group.add_condition(table=table, column=column)
        self.refresh()
        self._on_change()

    def _add_group(self, group: FilterGroup):
        group.add_group()
        self.refresh()
        self._on_change()

    def _remove_node(self, parent_group: FilterGroup, node):
        parent_group.remove_child(node)
        self.refresh()
        self._on_change()

    def _set_conjunction(self, group: FilterGroup, conjunction: str):
        group.conjunction = conjunction
        self._on_change()

    def _set_condition_column(self, cond: FilterCondition, table_column):
        if not table_column:
            return
        table, column = table_column
        cond.table, cond.column = table, column
        self._on_change()

    def _set_condition_operator(self, cond: FilterCondition, operator: str):
        cond.operator = operator
        self._on_change()

    def _set_condition_value(self, cond: FilterCondition, value: str):
        cond.value = value
        self._on_change()


class GroupOrderLimitPanel(QWidget):
    """GROUP BY / ORDER BY / LIMIT controls (VQB.5, issue #195). Column
    checkboxes for GROUP BY and an ordered list of column+direction rows
    for ORDER BY are sourced the same way SelectPanel sources columns;
    LIMIT is a plain spin box (0 = unlimited). Mutates *state* directly
    and calls on_change() after every edit."""

    def __init__(self, state, on_change, parent=None):
        super().__init__(parent)
        self._state = state
        self._on_change = on_change
        self._updating = False
        self._columns = []  # list[(label, table, column)]

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        gb_title = QLabel("GROUP BY")
        f = gb_title.font()
        f.setBold(True)
        gb_title.setFont(f)
        layout.addWidget(gb_title)

        self.group_tree = QTreeWidget()
        self.group_tree.setHeaderHidden(True)
        self.group_tree.setMaximumHeight(110)
        self.group_tree.itemChanged.connect(self._on_group_item_changed)
        layout.addWidget(self.group_tree)

        ob_title = QLabel("ORDER BY")
        f2 = ob_title.font()
        f2.setBold(True)
        ob_title.setFont(f2)
        layout.addWidget(ob_title)

        add_row = QHBoxLayout()
        self.order_column_combo = QComboBox()
        add_order_btn = QPushButton("+ Add")
        add_order_btn.clicked.connect(self._add_order_by)
        add_row.addWidget(self.order_column_combo, 1)
        add_row.addWidget(add_order_btn)
        layout.addLayout(add_row)

        self.order_list = QTreeWidget()
        self.order_list.setHeaderLabels(["Column", "Direction", ""])
        self.order_list.setColumnWidth(2, 28)
        self.order_list.setMaximumHeight(110)
        layout.addWidget(self.order_list)

        limit_row = QHBoxLayout()
        limit_row.addWidget(QLabel("LIMIT"))
        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(0, 1_000_000)
        self.limit_spin.setSpecialValueText("No limit")
        self.limit_spin.valueChanged.connect(self._on_limit_changed)
        limit_row.addWidget(self.limit_spin)
        limit_row.addStretch()
        layout.addLayout(limit_row)

    def set_state(self, state):
        """Repoint this panel at a different QueryBuilderState — used when
        loading a saved visual query (ui/query_builder_dialog.py's
        _load_saved_query)."""
        self._state = state

    def set_columns(self, columns: list):
        self._columns = columns
        self.refresh()

    def refresh(self):
        self._updating = True
        try:
            self.group_tree.clear()
            for label, table, column in self._columns:
                item = QTreeWidgetItem(self.group_tree, [label])
                item.setData(0, _TABLE_ROLE, table)
                item.setData(0, _COLUMN_ROLE, column)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                checked = self._state.is_grouped(table, column)
                item.setCheckState(0, Qt.Checked if checked else Qt.Unchecked)

            self.order_column_combo.clear()
            for label, table, column in self._columns:
                self.order_column_combo.addItem(label, (table, column))

            self.order_list.clear()
            for order_item in self._state.order_by:
                row = QTreeWidgetItem(self.order_list, [f"{order_item.table}.{order_item.column}"])
                dir_combo = QComboBox()
                dir_combo.addItems(list(ORDER_DIRECTIONS))
                dir_combo.setCurrentText(order_item.direction)
                dir_combo.currentTextChanged.connect(
                    lambda text, it=order_item: self._set_direction(it, text))
                self.order_list.setItemWidget(row, 1, dir_combo)
                remove_btn = QPushButton("✕")
                remove_btn.setFixedWidth(24)
                remove_btn.clicked.connect(lambda _checked=False, it=order_item: self._remove_order_by(it))
                self.order_list.setItemWidget(row, 2, remove_btn)

            self.limit_spin.blockSignals(True)
            self.limit_spin.setValue(self._state.limit or 0)
            self.limit_spin.blockSignals(False)
        finally:
            self._updating = False

    def _on_group_item_changed(self, item, _column_index):
        if self._updating:
            return
        table = item.data(0, _TABLE_ROLE)
        column = item.data(0, _COLUMN_ROLE)
        checked = item.checkState(0) == Qt.Checked
        self._state.set_grouped(table, column, checked)
        self._on_change()

    def _add_order_by(self):
        idx = self.order_column_combo.currentIndex()
        if idx < 0:
            return
        table, column = self.order_column_combo.itemData(idx)
        if any(o.table == table and o.column == column for o in self._state.order_by):
            return
        self._state.add_order_by(table, column)
        self.refresh()
        self._on_change()

    def _set_direction(self, order_item, direction: str):
        order_item.direction = direction
        self._on_change()

    def _remove_order_by(self, order_item):
        self._state.remove_order_by(order_item)
        self.refresh()
        self._on_change()

    def _on_limit_changed(self, value: int):
        if self._updating:
            return
        self._state.set_limit(value if value > 0 else None)
        self._on_change()
