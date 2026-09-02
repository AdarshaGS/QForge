"""Tests for the schema-compare diff model (issue #68) — pure data, no UI.

Live-Postgres integration (skipped if no local server, mirrors
tests/test_db_service_postgresql.py's fixture). Each test gets its own
throwaway source/target database pair, cleaned up afterward."""
import uuid

import psycopg2
import pytest

from services.db_service import DbService
from services.schema_diff import build_schema_diff

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


pytestmark = pytest.mark.skipif(
    not _postgres_available(),
    reason="No local Postgres reachable as qforge_test@localhost:5432 — see "
           "tests/test_db_service_postgresql.py's module docstring for setup.",
)


def _create_db(name: str) -> None:
    admin = psycopg2.connect(connect_timeout=5, **_ADMIN_PARAMS)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    admin.close()


def _drop_db(name: str) -> None:
    admin = psycopg2.connect(connect_timeout=5, **_ADMIN_PARAMS)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()", (name,))
        cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
    admin.close()


def _config(name: str) -> dict:
    return {
        "type": "postgresql", "name": name, "host": _PG_HOST, "port": _PG_PORT,
        "user": _PG_USER, "password": _PG_PASSWORD, "database": name,
    }


@pytest.fixture
def db_pair():
    """Yields (source_config, target_config) for two fresh throwaway
    databases, dropped again after the test."""
    source_name = f"qforge_test_src_{uuid.uuid4().hex[:10]}"
    target_name = f"qforge_test_tgt_{uuid.uuid4().hex[:10]}"
    _create_db(source_name)
    _create_db(target_name)
    yield _config(source_name), _config(target_name)
    _drop_db(source_name)
    _drop_db(target_name)


def _run(config, *statements):
    db = DbService()
    db.connect(config)
    for stmt in statements:
        db.execute_update(stmt)
    db.disconnect()


def test_added_and_removed_tables(db_pair):
    source, target = db_pair
    _run(source, "CREATE TABLE only_in_source (id INTEGER PRIMARY KEY)")
    _run(target, "CREATE TABLE only_in_target (id INTEGER PRIMARY KEY)")

    diff = build_schema_diff(source, target)

    assert diff.tables_added == ["only_in_target"]
    assert diff.tables_removed == ["only_in_source"]
    assert diff.tables_modified == []
    assert diff.tables_unchanged == 0


def test_identical_tables_are_unchanged(db_pair):
    source, target = db_pair
    ddl = "CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
    _run(source, ddl)
    _run(target, ddl)

    diff = build_schema_diff(source, target)

    assert diff.tables_added == []
    assert diff.tables_removed == []
    assert diff.tables_modified == []
    assert diff.tables_unchanged == 1


def test_added_removed_and_modified_columns(db_pair):
    source, target = db_pair
    _run(source, """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            legacy_flag INTEGER,
            email TEXT
        )
    """)
    _run(target, """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            email TEXT NOT NULL,
            signup_at TEXT
        )
    """)

    diff = build_schema_diff(source, target)

    assert diff.tables_added == []
    assert diff.tables_removed == []
    assert len(diff.tables_modified) == 1
    table_diff = diff.tables_modified[0]
    assert table_diff.name == "users"

    by_name = {c.name: c for c in table_diff.columns}
    assert by_name["legacy_flag"].change == "removed"
    assert by_name["signup_at"].change == "added"
    assert by_name["email"].change == "modified"
    assert "nullable" in by_name["email"].field_changes
    assert by_name["email"].field_changes["nullable"] == (True, False)


def test_index_added_removed_and_modified(db_pair):
    source, target = db_pair
    _run(source, "CREATE TABLE items (id INTEGER PRIMARY KEY, sku TEXT)",
         "CREATE INDEX idx_only_source ON items (sku)",
         "CREATE UNIQUE INDEX idx_sku ON items (sku)")
    _run(target, "CREATE TABLE items (id INTEGER PRIMARY KEY, sku TEXT)",
         "CREATE INDEX idx_only_target ON items (sku)",
         "CREATE INDEX idx_sku ON items (sku)")

    diff = build_schema_diff(source, target)

    assert len(diff.tables_modified) == 1
    by_name = {i.name: i for i in diff.tables_modified[0].indexes}
    assert by_name["idx_only_source"].change == "removed"
    assert by_name["idx_only_target"].change == "added"
    assert by_name["idx_sku"].change == "modified"
    assert by_name["idx_sku"].field_changes["unique"] == (True, False)


def test_foreign_key_added_and_removed(db_pair):
    source, target = db_pair
    _run(source,
         "CREATE TABLE authors (id INTEGER PRIMARY KEY)",
         "CREATE TABLE books (id INTEGER PRIMARY KEY, author_id INTEGER)")
    _run(target,
         "CREATE TABLE authors (id INTEGER PRIMARY KEY)",
         """CREATE TABLE books (
                id INTEGER PRIMARY KEY,
                author_id INTEGER,
                FOREIGN KEY (author_id) REFERENCES authors(id)
            )""")

    diff = build_schema_diff(source, target)

    table_diff = next(t for t in diff.tables_modified if t.name == "books")
    assert len(table_diff.foreign_keys) == 1
    fk_diff = table_diff.foreign_keys[0]
    assert fk_diff.change == "added"
    assert fk_diff.label == "author_id → authors.id"


def test_table_names_filter_limits_comparison(db_pair):
    source, target = db_pair
    _run(source, "CREATE TABLE a (id INTEGER PRIMARY KEY)",
         "CREATE TABLE b (id INTEGER PRIMARY KEY)")
    _run(target, "CREATE TABLE a (id INTEGER PRIMARY KEY, extra TEXT)")

    diff = build_schema_diff(source, target, table_names=["a"])

    assert diff.tables_removed == []
    assert len(diff.tables_modified) == 1
    assert diff.tables_modified[0].name == "a"


def test_missing_table_metadata_does_not_crash(db_pair):
    source, target = db_pair
    _run(source, "CREATE TABLE ok (id INTEGER PRIMARY KEY)")
    _run(target, "CREATE TABLE ok (id INTEGER PRIMARY KEY)")

    diff = build_schema_diff(source, target, table_names=["ok", "does_not_exist"])

    # Postgres's information_schema queries return an empty result rather
    # than raising for a nonexistent table, so "does_not_exist" comes back
    # identically empty on both sides and counts as unchanged too — the
    # point of this test is just that a name with no real metadata doesn't
    # crash the comparison.
    assert diff.tables_unchanged == 2
    assert diff.tables_added == []
    assert diff.tables_removed == []
