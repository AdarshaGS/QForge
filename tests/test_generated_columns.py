"""Tests for DbService.get_generated_columns()/content_select_list() (issue
#160): generated columns can't appear in an INSERT column list, so content
exports need to know which columns to leave out.

Live-Postgres integration (skipped if no local server, mirrors
tests/test_db_service_postgresql.py's fixture)."""
import uuid

import psycopg2
import pytest

from services.db_service import DbService

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


@pytest.fixture
def db():
    name = f"qforge_test_{uuid.uuid4().hex[:12]}"
    admin = psycopg2.connect(connect_timeout=5, **_ADMIN_PARAMS)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    admin.close()

    service = DbService()
    service.connect({
        "type": "postgresql", "name": "test", "host": _PG_HOST, "port": _PG_PORT,
        "user": _PG_USER, "password": _PG_PASSWORD, "database": name,
    })
    service.execute_update(
        "CREATE TABLE items ("
        "id INTEGER PRIMARY KEY, "
        "price REAL, "
        "qty REAL, "
        "total REAL GENERATED ALWAYS AS (price * qty) STORED"
        ")"
    )
    service.execute_update("INSERT INTO items (id, price, qty) VALUES (1, 10.0, 2.0)")

    yield service

    service.disconnect()
    admin = psycopg2.connect(connect_timeout=5, **_ADMIN_PARAMS)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()", (name,))
        cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
    admin.close()


def test_get_generated_columns_finds_stored_generated_column(db):
    assert db.get_generated_columns("items") == ["total"]


def test_get_generated_columns_empty_for_table_without_one(db):
    db.execute_update("CREATE TABLE plain (id INTEGER, name TEXT)")
    assert db.get_generated_columns("plain") == []


def test_content_select_list_excludes_generated_column(db):
    select_list = db.content_select_list("items")
    assert "total" not in select_list
    assert "price" in select_list and "qty" in select_list and "id" in select_list


def test_content_select_list_is_star_without_generated_columns(db):
    db.execute_update("CREATE TABLE plain (id INTEGER, name TEXT)")
    assert db.content_select_list("plain") == "*"


def test_content_select_list_query_is_insertable(db):
    df = db.execute_query(f"SELECT {db.content_select_list('items')} FROM items")  # nosec B608
    assert list(df.columns) == ["id", "price", "qty"]
    assert df.iloc[0]["price"] == 10.0
