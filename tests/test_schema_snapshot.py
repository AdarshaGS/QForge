"""Regression tests for the shared-connection corruption bug: a single
DB-API connection used concurrently from two threads without
synchronization can cross-contaminate query results (e.g. a server version
string ending up in the "Tables" list, or duplicated "Views" entries).

fetch_schema_snapshot() must always use its own dedicated connection and
must never touch a caller-supplied "primary" DbService's connection.

The Postgres-backed tests are skipped if no local server is reachable —
mirrors tests/test_db_service_postgresql.py's fixture.
"""
import uuid

import pandas as pd
import psycopg2
import pytest

from services.db_service import DbService
from services import schema_snapshot
from services.schema_snapshot import fetch_schema_snapshot
from utils import schema_cache

_PG_HOST = "localhost"
_PG_PORT = 5432
_PG_USER = "qforge_test"
_PG_PASSWORD = "qforge_test_pw"
_ADMIN_PARAMS = dict(host=_PG_HOST, port=_PG_PORT, user=_PG_USER, password=_PG_PASSWORD, database="postgres")


def _postgres_available() -> bool:
    try:
        conn = psycopg2.connect(connect_timeout=2, **_ADMIN_PARAMS)
        conn.close()
        return True
    except Exception:
        return False


@pytest.fixture
def make_db():
    """Factory fixture: make_db("label") -> connect() config for a fresh
    throwaway database, named uniquely so multiple calls in one test don't
    collide. All databases it created are dropped at teardown.

    Only the tests that actually request this fixture need a local
    Postgres server (skipped here, not module-wide, so the pure-mock MySQL
    test at the bottom of this file keeps running unconditionally)."""
    if not _postgres_available():
        pytest.skip(
            "No local Postgres reachable as qforge_test@localhost:5432 — see "
            "tests/test_db_service_postgresql.py's module docstring for setup."
        )
    created: list[str] = []

    def _make(label: str = "test") -> dict:
        name = f"qforge_test_{label}_{uuid.uuid4().hex[:10]}"
        admin = psycopg2.connect(connect_timeout=5, **_ADMIN_PARAMS)
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{name}"')
        admin.close()
        created.append(name)
        return {
            "type": "postgresql", "name": label, "host": _PG_HOST, "port": _PG_PORT,
            "user": _PG_USER, "password": _PG_PASSWORD, "database": name,
        }

    yield _make

    for name in created:
        admin = psycopg2.connect(connect_timeout=5, **_ADMIN_PARAMS)
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()", (name,))
            cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
        admin.close()


def test_fetch_schema_snapshot_lists_tables_and_views(make_db):
    config = make_db()
    setup = DbService()
    setup.connect(config)
    setup.execute_update("CREATE TABLE widgets (id INTEGER PRIMARY KEY)")
    setup.execute_update("CREATE VIEW widget_view AS SELECT * FROM widgets")
    setup.disconnect()

    snapshot = fetch_schema_snapshot(config)

    assert snapshot["tables"] == ["widgets"]
    assert snapshot["views"] == ["widget_view"]
    assert "PostgreSQL" in snapshot["server_version"]


def test_fetch_schema_snapshot_never_touches_a_primary_connection(make_db):
    """The exact regression this was built to prevent: schema loading must
    not read/write a shared 'primary' connection concurrently with other
    work on that connection."""
    primary_config = make_db("primary")
    primary = DbService()
    primary.connect(primary_config)
    primary.execute_update("CREATE TABLE accounts (id INTEGER PRIMARY KEY)")

    primary_connection_before = primary.connection

    # fetch_schema_snapshot is called with a DIFFERENT config/connection —
    # mirroring how connection_panel.py's background thread must never
    # share `self.db_service` with the schema-loading task.
    other_config = make_db("other")
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


def test_fetch_schema_snapshot_disconnects_its_own_connection(make_db):
    config = make_db()
    setup = DbService()
    setup.connect(config)
    setup.disconnect()

    snapshot = fetch_schema_snapshot(config)
    # A dedicated DbService is created and torn down internally; nothing
    # here should leak a live connection back to the caller.
    assert isinstance(snapshot, dict)
    assert "tables" in snapshot


def test_fetch_schema_snapshot_caches_result_for_next_open(make_db, tmp_path, monkeypatch):
    """Issue #71: a successful fetch is cached so re-opening the same
    connection/database can populate the UI without hitting the network."""
    monkeypatch.setattr(schema_cache, "_FILE", str(tmp_path / "cache.json"))
    config = make_db()
    config["id"] = "conn-71"
    setup = DbService()
    setup.connect(config)
    setup.execute_update("CREATE TABLE widgets (id INTEGER PRIMARY KEY)")
    setup.disconnect()

    fetch_schema_snapshot(config)

    cached = schema_cache.load("conn-71", config["database"])
    assert cached["tables"] == ["widgets"]
    assert "PostgreSQL" in cached["server_version"]


def test_fetch_schema_snapshot_without_id_does_not_write_cache(make_db, tmp_path, monkeypatch):
    cache_file = tmp_path / "cache.json"
    monkeypatch.setattr(schema_cache, "_FILE", str(cache_file))
    config = make_db()  # no "id" key, mirrors older callers

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
