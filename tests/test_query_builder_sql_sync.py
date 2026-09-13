"""Tests for build_sql() — the builder-state -> SQL live sync (VQB.5,
issue #195). Per ai/vqb-design-spike.md this is one-way, pure string
generation; these tests exercise representative states rather than every
possible combination."""
from services.query_builder_model import Join, QueryBuilderState, build_sql


def test_build_sql_returns_empty_string_for_no_tables():
    assert build_sql(QueryBuilderState()) == ""


def test_build_sql_defaults_to_select_star_with_no_select_items():
    state = QueryBuilderState()
    state.add_table("orders")
    assert build_sql(state) == "SELECT *\nFROM orders"


def test_build_sql_joins_connected_tables():
    state = QueryBuilderState()
    state.add_table("orders")
    state.add_table("customers")
    state.add_join(Join(left_table="orders", left_column="customer_id",
                         right_table="customers", right_column="id", join_type="LEFT"))
    state.set_column_selected("orders", "id", True)

    sql = build_sql(state)
    assert "FROM orders LEFT JOIN customers ON orders.customer_id = customers.id" in sql


def test_build_sql_disconnected_tables_fall_back_to_comma_from():
    state = QueryBuilderState()
    state.add_table("orders")
    state.add_table("audit_log")  # no join between them

    sql = build_sql(state)
    assert "FROM orders, audit_log" in sql


def test_build_sql_select_list_with_aggregate_alias_and_expression():
    state = QueryBuilderState()
    state.add_table("orders")
    state.set_column_selected("orders", "total", True)
    state.set_aggregate("orders", "total", "SUM")
    state.set_column_alias("orders", "total", "total_sum")
    state.add_expression("total * 1.1", "with_tax")

    sql = build_sql(state)
    assert sql.startswith("SELECT SUM(orders.total) AS total_sum, total * 1.1 AS with_tax\n")


def test_build_sql_where_group_by_having_order_by_limit():
    state = QueryBuilderState()
    state.add_table("orders")
    state.set_column_selected("orders", "status", True)
    state.where_root.add_condition(table="orders", column="status", operator="=", value="shipped")
    state.set_grouped("orders", "status", True)
    state.having_root.add_condition(table="orders", column="status", operator="!=", value="")
    state.having_root.add_condition(table="", column="COUNT(orders.id)", operator=">", value="1")
    state.add_order_by("orders", "status", "DESC")
    state.set_limit(10)

    sql = build_sql(state)
    lines = sql.split("\n")
    assert lines[0] == "SELECT orders.status"
    assert lines[1] == "FROM orders"
    assert "WHERE orders.status = 'shipped'" in lines
    assert "GROUP BY orders.status" in lines
    assert "HAVING COUNT(orders.id) > 1" in lines
    assert "ORDER BY orders.status DESC" in lines
    assert "LIMIT 10" in lines


def test_build_sql_omits_empty_where_and_having():
    state = QueryBuilderState()
    state.add_table("orders")
    sql = build_sql(state)
    assert "WHERE" not in sql
    assert "HAVING" not in sql
