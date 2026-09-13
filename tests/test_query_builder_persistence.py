"""Tests for VQB.6 (issue #196) persistence — QueryBuilderState.to_dict()/
from_dict() round-tripping the full builder state, and
services/saved_visual_queries.py's CRUD (mirrors tests/test_saved_queries.py)."""
from services import saved_visual_queries as saved_visual_queries_module
from services.query_builder_model import Join, QueryBuilderState, build_sql
from services.saved_visual_queries import SavedVisualQueries


def _store(tmp_path, monkeypatch):
    monkeypatch.setattr(saved_visual_queries_module, "_FILE", str(tmp_path / "saved_visual_queries.json"))
    return SavedVisualQueries()


def _sample_state() -> QueryBuilderState:
    state = QueryBuilderState()
    state.add_table("orders")
    state.add_table("customers")
    state.add_join(Join(left_table="orders", left_column="customer_id",
                         right_table="customers", right_column="id", join_type="LEFT"))
    state.set_column_selected("orders", "id", True)
    state.set_aggregate("orders", "id", "COUNT")
    state.set_column_alias("orders", "id", "order_count")
    state.add_expression("id * 2", "doubled")
    state.where_root.add_condition(table="orders", column="status", operator="=", value="shipped")
    nested = state.where_root.add_group(conjunction="OR")
    nested.add_condition(table="customers", column="active", operator="=", value="1")
    state.having_root.add_condition(table="", column="COUNT(orders.id)", operator=">", value="1")
    state.set_grouped("orders", "status", True)
    state.add_order_by("orders", "status", "DESC")
    state.set_limit(25)
    return state


def test_query_builder_state_round_trips_through_dict():
    state = _sample_state()
    restored = QueryBuilderState.from_dict(state.to_dict())

    assert restored.table_names == state.table_names
    assert [j.to_dict() for j in restored.joins] == [j.to_dict() for j in state.joins]
    assert [i.to_dict() for i in restored.select_items] == [i.to_dict() for i in state.select_items]
    assert restored.group_by == state.group_by
    assert [o.to_dict() for o in restored.order_by] == [o.to_dict() for o in state.order_by]
    assert restored.limit == state.limit
    assert build_sql(restored) == build_sql(state)


def test_saved_visual_queries_add_persists_and_reloads(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    state = _sample_state()
    entry = store.add("My Visual Query", "conn-1", "mydb", state.to_dict(),
                       {"orders": [0, 0], "customers": [220, 0]}, build_sql(state))

    reloaded = _store(tmp_path, monkeypatch)
    assert len(reloaded.queries) == 1
    saved = reloaded.queries[0]
    assert saved["name"] == "My Visual Query"
    assert saved["connection_id"] == "conn-1"
    assert saved["id"] == entry["id"]
    restored_state = QueryBuilderState.from_dict(saved["builder_state"])
    assert restored_state.table_names == state.table_names


def test_saved_visual_queries_for_connection_filters(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    state = _sample_state()
    store.add("A", "conn-1", "db", state.to_dict(), {}, "")
    store.add("B", "conn-2", "db", state.to_dict(), {}, "")

    assert [q["name"] for q in store.for_connection("conn-1")] == ["A"]
    assert [q["name"] for q in store.for_connection("conn-2")] == ["B"]


def test_saved_visual_queries_update_and_delete(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    state = _sample_state()
    entry = store.add("Old Name", "conn-1", "db", state.to_dict(), {}, "")

    store.update(entry["id"], name="New Name")
    assert store.get(entry["id"])["name"] == "New Name"

    store.delete(entry["id"])
    assert store.get(entry["id"]) is None
