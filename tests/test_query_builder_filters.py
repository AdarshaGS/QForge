"""Tests for the WHERE/HAVING filter tree (VQB.4, issue #194) —
FilterGroup/FilterCondition nesting, format_condition_sql's quoting rules,
and cascade-on-table-removal."""
from services.query_builder_model import (
    FilterCondition, FilterGroup, QueryBuilderState, format_condition_sql,
)


def test_format_condition_sql_quoting_rules():
    assert format_condition_sql("orders.status", "IS NULL", "") == "orders.status IS NULL"
    assert format_condition_sql("orders.status", "=", "") == ""  # blank value, non-NULL op -> omitted
    assert format_condition_sql("orders.total", ">", "100") == "orders.total > 100"
    assert format_condition_sql("orders.status", "=", "shipped") == "orders.status = 'shipped'"
    assert format_condition_sql("orders.status", "=", "'shipped'") == "orders.status = 'shipped'"
    assert format_condition_sql("orders.id", "IN", "1, 2, 3") == "orders.id IN (1, 2, 3)"
    assert format_condition_sql("orders.id", "IN", "(1, 2, 3)") == "orders.id IN (1, 2, 3)"
    assert format_condition_sql("orders.name", "LIKE", "abc") == "orders.name LIKE 'abc'"


def test_filter_group_nested_and_or_rendering():
    root = FilterGroup(conjunction="AND")
    root.add_condition(table="orders", column="status", operator="=", value="shipped")
    or_group = root.add_group(conjunction="OR")
    or_group.add_condition(table="orders", column="total", operator=">", value="100")
    or_group.add_condition(table="orders", column="priority", operator="=", value="high")

    sql = root.to_sql()
    assert sql == "orders.status = 'shipped' AND (orders.total > 100 OR orders.priority = 'high')"


def test_filter_group_single_child_nested_group_not_parenthesized():
    root = FilterGroup(conjunction="AND")
    root.add_condition(table="orders", column="status", operator="=", value="shipped")
    nested = root.add_group(conjunction="OR")
    nested.add_condition(table="orders", column="total", operator=">", value="100")

    assert root.to_sql() == "orders.status = 'shipped' AND orders.total > 100"


def test_filter_group_skips_blank_conditions():
    root = FilterGroup()
    root.add_condition(table="orders", column="status", operator="=", value="")
    assert root.to_sql() == ""

    root.add_condition(table="orders", column="total", operator=">", value="100")
    assert root.to_sql() == "orders.total > 100"


def test_remove_table_cascades_to_where_and_having_trees():
    state = QueryBuilderState()
    state.add_table("orders")
    state.add_table("customers")

    state.where_root.add_condition(table="orders", column="status", operator="=", value="shipped")
    state.where_root.add_condition(table="customers", column="active", operator="=", value="1")
    nested = state.where_root.add_group()
    nested.add_condition(table="orders", column="total", operator=">", value="100")

    state.having_root.add_condition(table="orders", column="id", operator="!=", value="0")

    state.remove_table("customers")

    remaining = [c.table for c in state.where_root.children if isinstance(c, FilterCondition)]
    assert remaining == ["orders"]
    assert isinstance(state.where_root.children[-1], FilterGroup)  # nested group survives, even if emptied
    assert state.having_root.children[0].table == "orders"


def test_filter_group_to_dict_from_dict_round_trip():
    root = FilterGroup(conjunction="OR")
    root.add_condition(table="orders", column="status", operator="=", value="shipped")
    nested = root.add_group(conjunction="AND")
    nested.add_condition(table="orders", column="total", operator=">", value="100")

    restored = FilterGroup.from_dict(root.to_dict())
    assert restored.to_sql() == root.to_sql()
    assert restored.conjunction == "OR"
    assert isinstance(restored.children[1], FilterGroup)
