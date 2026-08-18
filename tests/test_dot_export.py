"""Tests for the Dot schema/FK diagram export (issue #159)."""
from utils.dot_export import build_dot_graph


class _FakeDb:
    def __init__(self, columns: dict, fks: dict):
        self._columns = columns
        self._fks = fks

    def get_columns(self, table_name):
        return [{"Field": c} for c in self._columns.get(table_name, [])]

    def get_foreign_keys(self, table_name):
        return self._fks.get(table_name, [])


def test_build_dot_graph_includes_every_selected_table_as_a_node():
    db = _FakeDb({"users": ["id", "name"], "orders": ["id", "user_id"]}, {})
    dot = build_dot_graph(db, ["users", "orders"])
    assert dot.startswith("digraph schema {")
    assert dot.rstrip().endswith("}")
    assert '"users"' in dot and '"orders"' in dot
    assert "id" in dot and "name" in dot and "user_id" in dot


def test_build_dot_graph_draws_edge_for_foreign_key_within_selection():
    db = _FakeDb(
        {"users": ["id"], "orders": ["id", "user_id"]},
        {"orders": [{"column": "user_id", "ref_table": "users", "ref_column": "id"}]},
    )
    dot = build_dot_graph(db, ["users", "orders"])
    assert '"orders" -> "users"' in dot


def test_build_dot_graph_skips_fk_pointing_outside_the_selection():
    db = _FakeDb(
        {"orders": ["id", "user_id"]},
        {"orders": [{"column": "user_id", "ref_table": "users", "ref_column": "id"}]},
    )
    dot = build_dot_graph(db, ["orders"])  # "users" not selected
    assert "->" not in dot


def test_build_dot_graph_empty_table_list():
    db = _FakeDb({}, {})
    dot = build_dot_graph(db, [])
    assert dot == "digraph schema {\n  rankdir=LR;\n  node [shape=record];\n}"
