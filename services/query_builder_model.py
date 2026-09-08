"""Join-graph state for the Visual Query Builder canvas (VQB.2, issue #192).

Independent of the QGraphicsScene rendering in ui/query_builder_dialog.py —
mirrors the split services/erd_model.py keeps from ui/erd_dialog.py. Table
and column metadata is provided by services/erd_model.py's ErdGraph (a
build_erd_graph() call over the whole database is how the caller learns
what tables/FKs exist to offer and auto-suggest); this module only tracks
the subset of that graph the user has actually placed on the canvas, plus
the joins between them.
"""
from dataclasses import dataclass

JOIN_TYPES = ("INNER", "LEFT", "RIGHT", "FULL")
# "" means no aggregate — a plain column reference.
AGGREGATE_FUNCS = ("", "COUNT", "SUM", "AVG", "MIN", "MAX")


@dataclass
class Join:
    # left/right are the two ends of the join as drawn (drag source/target),
    # not "which table comes first in FROM" — SQL generation (VQB.5) is free
    # to order tables however it likes, it just needs a consistent ON pair.
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


class QueryBuilderState:
    """Tables currently on the canvas (by name, insertion order) and the
    joins between them. Owns no schema metadata itself — the caller looks
    that up (via the ErdGraph it already loaded) whenever it needs a
    table's columns."""

    def __init__(self):
        self.table_names: list = []
        self.joins: list = []          # list[Join]
        self.select_items: list = []   # list[SelectItem]

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
