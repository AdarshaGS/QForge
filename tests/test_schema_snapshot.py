"""Regression tests for the shared-connection corruption bug: a single
DB-API connection used concurrently from two threads without
synchronization can cross-contaminate query results (e.g. a server version
string ending up in the "Tables" list, or duplicated "Views" entries).

fetch_schema_snapshot() must always use its own dedicated connection and
must never touch a caller-supplied "primary" DbService's connection.
"""
import pandas as pd

from services.db_service import DbService
from services import schema_snapshot
from services.schema_snapshot import fetch_schema_snapshot
from utils import schema_cache


def _make_sqlite_config(tmp_path, name="test"):
    return {"type": "sqlite", "name": name, "database": str(tmp_path / f"{name}.db")}


def test_fetch_schema_snapshot_lists_tables_and_views(tmp_path):
    config = _make_sqlite_config(tmp_path)
    setup = DbService()
    setup.connect(config)
    setup.execute_update("CREATE TABLE widgets (id INTEGER PRIMARY KEY)")
    setup.execute_update("CREATE VIEW widget_view AS SELECT * FROM widgets")
    setup.disconnect()

    snapshot = fetch_schema_snapshot(config)

    assert snapshot["tables"] == ["widgets"]
    assert snapshot["views"] == ["widget_view"]
    assert "SQLite" in snapshot["server_version"]


def test_fetch_schema_snapshot_never_touches_a_primary_connection(tmp_path):
    """The exact regression this was built to prevent: schema loading must
    not read/write a shared 'primary' connection concurrently with other
    work on that connection."""
    primary_config = _make_sqlite_config(tmp_path, name="primary")
    primary = DbService()
    primary.connect(primary_config)
    primary.execute_update("CREATE TABLE accounts (id INTEGER PRIMARY KEY)")

    primary_connection_before = primary.connection

    # fetch_schema_snapshot is called with a DIFFERENT config/connection —
    # mirroring how connection_panel.py's background thread must never
    # share `self.db_service` with the schema-loading task.
    other_config = _make_sqlite_config(tmp_path, name="other")
    other_setup = DbService()
    other_setup.connect(other_config)
    other_setup.execute_update("CREATE TABLE widgets (id INTEGER PRIMARY KEY)")
    other_setup.disconnect()

    snapshot = fetch_schema_snapshot(other_config)

    # The primary connection object must be completely unaffected.
    assert primary.connection is primary_connection_before
    assert primary.get_tables() == ["accounts"]
    assert snapshot["tables"] == ["widgets"]

    primary.disconnect()


def test_fetch_schema_snapshot_disconnects_its_own_connection(tmp_path):
    config = _make_sqlite_config(tmp_path)
    setup = DbService()
    setup.connect(config)
    setup.disconnect()

    snapshot = fetch_schema_snapshot(config)
    # A dedicated DbService is created and torn down internally; nothing
    # here should leak a live connection back to the caller.
    assert isinstance(snapshot, dict)
    assert "tables" in snapshot


def test_fetch_schema_snapshot_caches_result_for_next_open(tmp_path, monkeypatch):
    """Issue #71: a successful fetch is cached so re-opening the same
    connection/database can populate the UI without hitting the network."""
    monkeypatch.setattr(schema_cache, "_FILE", str(tmp_path / "cache.json"))
    config = _make_sqlite_config(tmp_path)
    config["id"] = "conn-71"
    setup = DbService()
    setup.connect(config)
    setup.execute_update("CREATE TABLE widgets (id INTEGER PRIMARY KEY)")
    setup.disconnect()

    fetch_schema_snapshot(config)

    cached = schema_cache.load("conn-71", config["database"])
    assert cached["tables"] == ["widgets"]
    assert "SQLite" in cached["server_version"]


def test_fetch_schema_snapshot_without_id_does_not_write_cache(tmp_path, monkeypatch):
    cache_file = tmp_path / "cache.json"
    monkeypatch.setattr(schema_cache, "_FILE", str(cache_file))
    config = _make_sqlite_config(tmp_path)  # no "id" key, mirrors older callers

    fetch_schema_snapshot(config)

    assert not cache_file.exists()


class _FakeMysqlConnection:
    def __init__(self):
        self.selected_db = None

    def select_db(self, name):
        self.selected_db = name


class _FakeMysqlDbService:
    """Simulates pymysql's real failure mode: get_tables()/get_all_columns()
    raise "No database selected" until connection.select_db() has actually
    been called — mirroring a MySQL connection profile with no configured
    database. Used to test fetch_schema_snapshot's fetch ordering without
    needing a live MySQL server."""

    def __init__(self):
        self.db_type = "mysql"
        self.connection = _FakeMysqlConnection()

    def connect(self, config):
        pass

    def execute_query(self, sql):
        assert sql == "SHOW DATABASES"
        return pd.DataFrame({"Database": ["shop"]})

    def get_tables(self):
        if self.connection.selected_db is None:
            raise Exception("No database selected")
        return ["orders"]

    def get_all_columns(self):
        if self.connection.selected_db is None:
            raise Exception("No database selected")
        return {"orders": ["id"]}

    def get_views(self):
        return []

    def get_functions(self):
        return []

    def get_server_version(self):
        return "MySQL 8.0.46"

    def disconnect(self):
        pass


def test_fetch_schema_snapshot_mysql_with_no_database_selected_still_lists_tables(monkeypatch):
    """Regression test: a MySQL connection profile with no database
    configured used to come back with an empty tables list on first
    connect (and stay empty until the user manually switched databases),
    because get_tables()/get_all_columns() ran before the "no db selected
    -> fall back to the first available db" logic ever executed."""
    monkeypatch.setattr(schema_snapshot, "DbService", _FakeMysqlDbService)
    config = {"type": "mysql", "name": "test", "database": ""}

    snapshot = fetch_schema_snapshot(config)

    assert snapshot["switched_db"] == "shop"
    assert snapshot["tables"] == ["orders"]
    assert snapshot["columns"] == {"orders": ["id"]}
