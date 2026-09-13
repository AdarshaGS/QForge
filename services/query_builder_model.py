"""Join-graph, SELECT-list, filter, and SQL-sync state for the Visual Query
Builder canvas (VQB.2-VQB.5, issues #192-#195).

Independent of the QGraphicsScene rendering in ui/query_builder_dialog.py —
mirrors the split services/erd_model.py keeps from ui/erd_dialog.py. Table
and column metadata is provided by services/erd_model.py's ErdGraph (a
build_erd_graph() call over the whole database is how the caller learns
what tables/FKs exist to offer and auto-suggest); this module only tracks
the subset of that graph the user has actually placed on the canvas, plus
the joins, SELECT list, WHERE/HAVING trees, and GROUP BY/ORDER BY/LIMIT
built on top of it — and how to render all of that into a single read-only
SQL string (build_sql), per ai/vqb-design-spike.md's one-way
builder-state -> SQL sync.
"""
from dataclasses import dataclass, field

JOIN_TYPES = ("INNER", "LEFT", "RIGHT", "FULL")
# "" means no aggregate — a plain column reference.
AGGREGATE_FUNCS = ("", "COUNT", "SUM", "AVG", "MIN", "MAX")

# Same operator set as ui/advanced_filter_dialog.py's AdvancedFilterDialog
# (the result-grid filter feature) — duplicated here rather than imported
# since services/ must not depend on ui/; format_condition_sql below mirrors
# that dialog's get_filter_condition() quoting rules for the same reason.
FILTER_OPERATORS = (
    "=", "!=", ">", ">=", "<", "<=", "LIKE", "NOT LIKE", "IN", "NOT IN",
    "IS NULL", "IS NOT NULL",
)

ORDER_DIRECTIONS = ("ASC", "DESC")


def format_condition_sql(column: str, operator: str, value: str) -> str:
    """Render one column/operator/value row as a SQL condition fragment.
    *column* is already fully qualified (e.g. "orders.status") by the
    caller. Mirrors AdvancedFilterDialog.get_filter_condition's rules:
    IS [NOT] NULL take no value, IN/NOT IN wrap bare values in parens,
    LIKE/NOT LIKE and non-numeric values get quoted, numeric values don't.
    Returns "" for a blank value on a non-NULL operator — same "leave it
    out rather than emit broken SQL" stance as an unfilled expression row.
    """
    if operator in ("IS NULL", "IS NOT NULL"):
        return f"{column} {operator}"

    value = (value or "").strip()
    if not value:
        return ""

    if operator in ("IN", "NOT IN"):
        if value.startswith("(") and value.endswith(")"):
            return f"{column} {operator} {value}"
        return f"{column} {operator} ({value})"

    if operator in ("LIKE", "NOT LIKE"):
        if not (value.startswith("'") and value.endswith("'")):
            value = f"'{value}'"
        return f"{column} {operator} {value}"

    try:
        float(value)
        return f"{column} {operator} {value}"
    except ValueError:
        if not (value.startswith("'") and value.endswith("'")):
            value = f"'{value}'"
        return f"{column} {operator} {value}"


@dataclass
class Join:
    # left/right are the two ends of the join as drawn (drag source/target),
    # not "which table comes first in FROM" — SQL generation (build_sql)
    # picks whatever order/orientation lets it attach each table onto an
    # already-placed one, it just needs a consistent ON pair.
    left_table: str
    left_column: str
    right_table: str
    right_column: str
    join_type: str = "INNER"
    # True until the user edits its type, redraws it, or otherwise confirms
    # it — distinguishes an auto-suggested FK join from a deliberate one
    # (see ui/query_builder_dialog.py's dashed-vs-solid pen).
    suggested: bool = False

    def connects(self, table_a: str, column_a: str, table_b: str, column_b: str) -> bool:
        """True if this join already connects these two exact column
        endpoints, checked in either direction."""
        pair = (self.left_table, self.left_column, self.right_table, self.right_column)
        return pair == (table_a, column_a, table_b, column_b) or \
            pair == (table_b, column_b, table_a, column_a)

    def to_dict(self) -> dict:
        return {
            "left_table": self.left_table, "left_column": self.left_column,
            "right_table": self.right_table, "right_column": self.right_column,
            "join_type": self.join_type, "suggested": self.suggested,
        }

    @staticmethod
    def from_dict(d: dict) -> "Join":
        return Join(
            left_table=d.get("left_table", ""), left_column=d.get("left_column", ""),
            right_table=d.get("right_table", ""), right_column=d.get("right_column", ""),
            join_type=d.get("join_type", "INNER"), suggested=bool(d.get("suggested", False)),
        )


@dataclass
class SelectItem:
    """One entry in the SELECT list — either a table column (kind="column",
    optionally wrapped in an aggregate) or a free-form expression
    (kind="expression", e.g. "price * qty"). *alias* is optional either
    way (VQB.3, issue #193)."""
    kind: str  # "column" | "expression"
    table: str = ""        # kind="column" only
    column: str = ""       # kind="column" only
    aggregate: str = ""    # kind="column" only — one of AGGREGATE_FUNCS
    expression: str = ""   # kind="expression" only
    alias: str = ""

    def to_dict(self) -> dict:
        return {
            "kind": self.kind, "table": self.table, "column": self.column,
            "aggregate": self.aggregate, "expression": self.expression, "alias": self.alias,
        }

    @staticmethod
    def from_dict(d: dict) -> "SelectItem":
        return SelectItem(
            kind=d.get("kind", "column"), table=d.get("table", ""), column=d.get("column", ""),
            aggregate=d.get("aggregate", ""), expression=d.get("expression", ""),
            alias=d.get("alias", ""),
        )


@dataclass
class FilterCondition:
    """One WHERE/HAVING leaf: a column/operator/value row, same shape as
    ui/advanced_filter_dialog.py's single-row filter — but here it's one
    possible child of a FilterGroup tree instead of the whole filter."""
    kind: str = "condition"
    table: str = ""
    column: str = ""
    operator: str = "="
    value: str = ""

    def to_sql(self) -> str:
        if not self.column:
            return ""
        col = f"{self.table}.{self.column}" if self.table else self.column
        return format_condition_sql(col, self.operator, self.value)

    def to_dict(self) -> dict:
        return {
            "kind": "condition", "table": self.table, "column": self.column,
            "operator": self.operator, "value": self.value,
        }

    @staticmethod
    def from_dict(d: dict) -> "FilterCondition":
        return FilterCondition(
            table=d.get("table", ""), column=d.get("column", ""),
            operator=d.get("operator", "="), value=d.get("value", ""),
        )


@dataclass
class FilterGroup:
    """A WHERE/HAVING AND/OR group — children are FilterCondition leaves or
    nested FilterGroup subtrees (VQB.4, issue #194). AdvancedFilterDialog/
    DataFilterDialog only support a flat AND-only list, which is why this
    is new rather than reused — only their operator list and value-quoting
    rules (format_condition_sql above) are shared."""
    kind: str = "group"
    conjunction: str = "AND"  # "AND" | "OR"
    children: list = field(default_factory=list)  # list[FilterCondition | FilterGroup]

    def add_condition(self, table: str = "", column: str = "", operator: str = "=", value: str = "") -> FilterCondition:
        cond = FilterCondition(table=table, column=column, operator=operator, value=value)
        self.children.append(cond)
        return cond

    def add_group(self, conjunction: str = "AND") -> "FilterGroup":
        group = FilterGroup(conjunction=conjunction)
        self.children.append(group)
        return group

    def remove_child(self, node) -> None:
        if node in self.children:
            self.children.remove(node)

    def is_empty(self) -> bool:
        return len(self.children) == 0

    def to_sql(self) -> str:
        parts = []
        for child in self.children:
            frag = child.to_sql()
            if not frag:
                continue
            if isinstance(child, FilterGroup) and len(child.children) > 1:
                frag = f"({frag})"
            parts.append(frag)
        return f" {self.conjunction} ".join(parts)

    def to_dict(self) -> dict:
        return {
            "kind": "group", "conjunction": self.conjunction,
            "children": [c.to_dict() for c in self.children],
        }

    @staticmethod
    def from_dict(d: dict) -> "FilterGroup":
        group = FilterGroup(conjunction=(d or {}).get("conjunction", "AND"))
        for c in (d or {}).get("children", []):
            if c.get("kind") == "group":
                group.children.append(FilterGroup.from_dict(c))
            else:
                group.children.append(FilterCondition.from_dict(c))
        return group


def _strip_table(group: FilterGroup, table: str) -> None:
    """Drop any FilterCondition referencing *table* from *group*'s tree,
    recursively. Nested groups are kept even if they end up empty — same
    "no SQL parsing, don't guess intent" stance as expression_items() below
    (an emptied-out group just renders no SQL, it isn't deleted outright)."""
    keep = []
    for child in group.children:
        if isinstance(child, FilterGroup):
            _strip_table(child, table)
            keep.append(child)
        elif child.table != table:
            keep.append(child)
    group.children = keep


@dataclass
class OrderItem:
    table: str
    column: str
    direction: str = "ASC"

    def to_dict(self) -> dict:
        return {"table": self.table, "column": self.column, "direction": self.direction}

    @staticmethod
    def from_dict(d: dict) -> "OrderItem":
        return OrderItem(table=d.get("table", ""), column=d.get("column", ""),
                          direction=d.get("direction", "ASC"))


class QueryBuilderState:
    """Tables currently on the canvas (by name, insertion order), the joins
    between them, the SELECT list, WHERE/HAVING filter trees, and
    GROUP BY/ORDER BY/LIMIT. Owns no schema metadata itself — the caller
    looks that up (via the ErdGraph it already loaded) whenever it needs a
    table's columns."""

    def __init__(self):
        self.table_names: list = []
        self.joins: list = []          # list[Join]
        self.select_items: list = []   # list[SelectItem]
        self.where_root = FilterGroup()
        self.having_root = FilterGroup()
        self.group_by: list = []       # list[tuple[str, str]] (table, column)
        self.order_by: list = []       # list[OrderItem]
        self.limit = None              # int | None

    def add_table(self, name: str) -> bool:
        """Returns False (no-op) if *name* is already on the canvas."""
        if name in self.table_names:
            return False
        self.table_names.append(name)
        return True

    def remove_table(self, name: str):
        if name in self.table_names:
            self.table_names.remove(name)
        self.joins = [j for j in self.joins if j.left_table != name and j.right_table != name]
        # Expression items are left alone even if their raw text happens to
        # reference the removed table — nothing here parses expressions
        # (see ai/vqb-design-spike.md's "no SQL parsing" stance), so there's
        # no reliable way to tell it applies without the user saying so.
        self.select_items = [
            i for i in self.select_items if not (i.kind == "column" and i.table == name)
        ]
        _strip_table(self.where_root, name)
        _strip_table(self.having_root, name)
        self.group_by = [(t, c) for (t, c) in self.group_by if t != name]
        self.order_by = [o for o in self.order_by if o.table != name]

    def add_join(self, join: Join):
        self.joins.append(join)

    def remove_join(self, join: Join):
        if join in self.joins:
            self.joins.remove(join)

    def has_join_between(self, table_a: str, column_a: str, table_b: str, column_b: str) -> bool:
        return any(j.connects(table_a, column_a, table_b, column_b) for j in self.joins)

    # ── SELECT list (VQB.3) ───────────────────────────────────────────────

    def get_select_item(self, table: str, column: str):
        for item in self.select_items:
            if item.kind == "column" and item.table == table and item.column == column:
                return item
        return None

    def set_column_selected(self, table: str, column: str, selected: bool):
        existing = self.get_select_item(table, column)
        if selected and existing is None:
            self.select_items.append(SelectItem(kind="column", table=table, column=column))
        elif not selected and existing is not None:
            self.select_items.remove(existing)

    def set_aggregate(self, table: str, column: str, aggregate: str):
        item = self.get_select_item(table, column)
        if item is not None:
            item.aggregate = aggregate

    def set_column_alias(self, table: str, column: str, alias: str):
        item = self.get_select_item(table, column)
        if item is not None:
            item.alias = alias

    def add_expression(self, expression: str, alias: str = "") -> SelectItem:
        item = SelectItem(kind="expression", expression=expression, alias=alias)
        self.select_items.append(item)
        return item

    def remove_select_item(self, item: SelectItem):
        if item in self.select_items:
            self.select_items.remove(item)

    def expression_items(self) -> list:
        return [i for i in self.select_items if i.kind == "expression"]

    # ── GROUP BY / ORDER BY / LIMIT (VQB.5) ─────────────────────────────

    def is_grouped(self, table: str, column: str) -> bool:
        return (table, column) in self.group_by

    def set_grouped(self, table: str, column: str, grouped: bool):
        key = (table, column)
        if grouped and key not in self.group_by:
            self.group_by.append(key)
        elif not grouped and key in self.group_by:
            self.group_by.remove(key)

    def add_order_by(self, table: str, column: str, direction: str = "ASC") -> OrderItem:
        item = OrderItem(table=table, column=column, direction=direction)
        self.order_by.append(item)
        return item

    def remove_order_by(self, item: OrderItem):
        if item in self.order_by:
            self.order_by.remove(item)

    def set_limit(self, limit) -> None:
        self.limit = limit

    # ── Persistence (VQB.6) ─────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "table_names": list(self.table_names),
            "joins": [j.to_dict() for j in self.joins],
            "select_items": [i.to_dict() for i in self.select_items],
            "where_root": self.where_root.to_dict(),
            "having_root": self.having_root.to_dict(),
            "group_by": [[t, c] for (t, c) in self.group_by],
            "order_by": [o.to_dict() for o in self.order_by],
            "limit": self.limit,
        }

    @staticmethod
    def from_dict(d: dict) -> "QueryBuilderState":
        d = d or {}
        state = QueryBuilderState()
        state.table_names = list(d.get("table_names", []))
        state.joins = [Join.from_dict(j) for j in d.get("joins", [])]
        state.select_items = [SelectItem.from_dict(i) for i in d.get("select_items", [])]
        state.where_root = FilterGroup.from_dict(d.get("where_root"))
        state.having_root = FilterGroup.from_dict(d.get("having_root"))
        state.group_by = [(t, c) for t, c in d.get("group_by", [])]
        state.order_by = [OrderItem.from_dict(o) for o in d.get("order_by", [])]
        state.limit = d.get("limit")
        return state


# ── SQL generation (VQB.5) ───────────────────────────────────────────────

def _select_clause(state: QueryBuilderState) -> str:
    if not state.select_items:
        return "*"
    parts = []
    for item in state.select_items:
        if item.kind == "expression":
            expr = item.expression
        else:
            col = f"{item.table}.{item.column}"
            expr = f"{item.aggregate}({col})" if item.aggregate else col
        if item.alias:
            expr += f" AS {item.alias}"
        parts.append(expr)
    return ", ".join(parts)


def _find_join_to(joins: list, table: str, included: list):
    """First join (if any) that connects *table* to a table already in
    *included* — either endpoint order."""
    for j in joins:
        if j.left_table == table and j.right_table in included:
            return j
        if j.right_table == table and j.left_table in included:
            return j
    return None


def _from_clause(state: QueryBuilderState) -> str:
    """Walk table_names in order, growing one or more connected components
    by attaching any remaining table reachable via a join from a table
    already placed in that component. Components with no join between
    them (rare — the user hasn't joined every table on the canvas) are
    combined with a comma-separated FROM, an old-style cross join — a
    deliberate, documented fallback rather than a bug."""
    remaining = list(state.table_names)
    components = []
    while remaining:
        start = remaining.pop(0)
        included = [start]
        clause = start
        progress = True
        while progress:
            progress = False
            for t in list(remaining):
                join = _find_join_to(state.joins, t, included)
                if join is None:
                    continue
                left_expr = f"{join.left_table}.{join.left_column}"
                right_expr = f"{join.right_table}.{join.right_column}"
                clause += f" {join.join_type} JOIN {t} ON {left_expr} = {right_expr}"
                included.append(t)
                remaining.remove(t)
                progress = True
        components.append(clause)
    return ", ".join(components)


def build_sql(state: QueryBuilderState) -> str:
    """Render *state* as a single read-only SQL SELECT statement — the
    one-way builder-state -> SQL sync described in ai/vqb-design-spike.md.
    Recomputed fresh on every call (no incremental diffing); returns ""
    for a table-less state so callers can treat that as "nothing to
    preview" rather than showing broken SQL."""
    if not state.table_names:
        return ""

    lines = [f"SELECT {_select_clause(state)}", f"FROM {_from_clause(state)}"]

    where_sql = state.where_root.to_sql()
    if where_sql:
        lines.append(f"WHERE {where_sql}")

    if state.group_by:
        lines.append("GROUP BY " + ", ".join(f"{t}.{c}" for t, c in state.group_by))

    having_sql = state.having_root.to_sql()
    if having_sql:
        lines.append(f"HAVING {having_sql}")

    if state.order_by:
        lines.append("ORDER BY " + ", ".join(
            f"{o.table}.{o.column} {o.direction}" for o in state.order_by))

    if state.limit:
        lines.append(f"LIMIT {state.limit}")

    return "\n".join(lines)
