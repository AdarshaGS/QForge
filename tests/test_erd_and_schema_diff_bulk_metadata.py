"""Regression-guard for issue #257: ER Diagram and Schema Compare must
gather column/PK/FK metadata via DbService's bulk, single-round-trip calls
(get_all_column_details/get_all_foreign_keys) rather than a get_columns()/
get_primary_keys()/get_foreign_keys() loop per table — the N+1 pattern that
made opening either view slow on anything but a tiny schema (see
services/erd_model.py and services/schema_diff.py). No live database
needed: a fake DbService counts how many times each method is called.
"""
from unittest.mock import patch

import services.erd_model as erd_model
import services.schema_diff as schema_diff


class _FakeDb:
    def __init__(self):
        self.calls = []

    def connect(self, config):
        pass

    def disconnect(self):
        pass

    def get_tables(self):
        self.calls.append("get_tables")
        return ["users", "orders"]

    def get_all_column_details(self):
        self.calls.append("get_all_column_details")
        return {
            "users": [{"name": "id", "type": "int", "nullable": False,
                       "default": None, "key": "PRI"}],
            "orders": [{"name": "id", "type": "int", "nullable": False,
                        "default": None, "key": "PRI"},
                       {"name": "user_id", "type": "int", "nullable": True,
                        "default": None, "key": ""}],
        }

    def get_all_foreign_keys(self):
        self.calls.append("get_all_foreign_keys")
        return {"orders": [{"column": "user_id", "ref_table": "users", "ref_column": "id"}]}

    def get_indexes(self, table_name):
        self.calls.append(f"get_indexes:{table_name}")
        return [{"name": "PRIMARY", "columns": "id", "unique": True, "type": "BTREE"}]

    # Would only be hit by a regression back to the old per-table loop.
    def get_columns(self, table_name):
        self.calls.append(f"get_columns:{table_name}")
        return []

    def get_primary_keys(self, table_name):
        self.calls.append(f"get_primary_keys:{table_name}")
        return []

    def get_foreign_keys(self, table_name):
        self.calls.append(f"get_foreign_keys:{table_name}")
        return []


def test_build_erd_graph_uses_bulk_metadata_not_a_per_table_loop():
    fake = _FakeDb()
    with patch.object(erd_model, "DbService", return_value=fake):
        graph = erd_model.build_erd_graph({"id": "c1", "database": "db1"})

    assert fake.calls == [
        "get_tables", "get_all_column_details", "get_all_foreign_keys",
        "get_indexes:orders",  # cardinality — only for the one FK-source table
    ]
    assert set(graph.tables) == {"users", "orders"}
    assert len(graph.relationships) == 1


def test_schema_diff_fetch_side_uses_bulk_metadata_not_a_per_table_loop():
    fake = _FakeDb()
    with patch.object(schema_diff, "DbService", return_value=fake):
        result = schema_diff._fetch_side({"id": "c1", "database": "db1"})

    assert fake.calls == [
        "get_tables", "get_all_column_details", "get_all_foreign_keys",
        "get_indexes:users", "get_indexes:orders",  # no bulk equivalent — stays per-table
    ]
    assert set(result) == {"users", "orders"}
    columns, indexes, fks = result["orders"]
    assert columns["user_id"].is_primary_key is False
    assert fks == [{"column": "user_id", "ref_table": "users", "ref_column": "id"}]


def test_build_schema_diff_runs_both_sides_with_bulk_metadata():
    fake_source = _FakeDb()
    fake_target = _FakeDb()
    dbs = iter([fake_source, fake_target])
    with patch.object(schema_diff, "DbService", side_effect=lambda: next(dbs)):
        diff = schema_diff.build_schema_diff({"id": "src"}, {"id": "tgt"})

    assert fake_source.calls[:3] == ["get_tables", "get_all_column_details", "get_all_foreign_keys"]
    assert fake_target.calls[:3] == ["get_tables", "get_all_column_details", "get_all_foreign_keys"]
    # Identical schemas both sides -> nothing modified/added/removed.
    assert diff.tables_added == []
    assert diff.tables_removed == []
    assert diff.tables_modified == []


if __name__ == "__main__":
    test_build_erd_graph_uses_bulk_metadata_not_a_per_table_loop()
    test_schema_diff_fetch_side_uses_bulk_metadata_not_a_per_table_loop()
    test_build_schema_diff_runs_both_sides_with_bulk_metadata()
    print("ok")
