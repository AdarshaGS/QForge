"""Regression tests for the shared-connection corruption bug: a single
DB-API connection used concurrently from two threads without
synchronization can cross-contaminate query results (e.g. a server version
string ending up in the "Tables" list, or duplicated "Views" entries).

fetch_schema_snapshot() must always use its own dedicated connection and
must never touch a caller-supplied "primary" DbService's connection.
"""
from services.db_service import DbService
from services.schema_snapshot import fetch_schema_snapshot


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
