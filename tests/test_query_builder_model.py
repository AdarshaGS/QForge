"""Test for the query-builder join-graph state (VQB.2, issue #192)."""
from services.query_builder_model import Join, QueryBuilderState


def test_query_builder_state_tracks_tables_and_cascades_join_removal():
    state = QueryBuilderState()

    assert state.add_table("orders") is True
    assert state.add_table("orders") is False  # already on canvas
    state.add_table("users")

    join = Join(left_table="orders", left_column="user_id",
                right_table="users", right_column="id", suggested=True)
    state.add_join(join)

    # Duplicate detection is direction-agnostic.
    assert state.has_join_between("orders", "user_id", "users", "id")
    assert state.has_join_between("users", "id", "orders", "user_id")
    assert not state.has_join_between("orders", "id", "users", "id")

    # Removing a table drops it and cascades to any join touching it.
    state.remove_table("users")
    assert state.table_names == ["orders"]
    assert state.joins == []
