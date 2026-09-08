"""Test for the SELECT-list state (VQB.3, issue #193) — column toggling,
aggregate/alias editing, expressions, and cascade removal on table delete."""
from services.query_builder_model import QueryBuilderState


def test_select_state_tracks_columns_aggregates_and_expressions():
    state = QueryBuilderState()
    state.add_table("orders")

    state.set_column_selected("orders", "total", True)
    item = state.get_select_item("orders", "total")
    assert item is not None
    assert item.aggregate == ""

    state.set_aggregate("orders", "total", "SUM")
    state.set_column_alias("orders", "total", "total_sum")
    item = state.get_select_item("orders", "total")
    assert item.aggregate == "SUM"
    assert item.alias == "total_sum"

    expr = state.add_expression("total * 1.1", "total_with_tax")
    assert state.expression_items() == [expr]

    # Removing the table drops its column selection but leaves the
    # expression (nothing here parses expression text) — see
    # ai/vqb-design-spike.md's no-SQL-parsing stance.
    state.remove_table("orders")
    assert state.get_select_item("orders", "total") is None
    assert state.expression_items() == [expr]
