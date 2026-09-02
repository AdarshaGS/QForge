"""Tests for DbService.stream_table_rows() (issue #158): reads a table via
cursor.fetchmany() chunks instead of execute_query()'s fetchall(), so a
table export never has to materialize the whole table in memory first.

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
    service.execute_update("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
    for i in range(1, 11):
        service.execute_update(f"INSERT INTO users (id, name) VALUES ({i}, 'user{i}')")  # nosec B608

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


def test_stream_table_rows_yields_all_rows_across_chunks(db):
    columns = None
    all_rows = []
    for cols, rows in db.stream_table_rows("users", chunk_size=3):
        columns = cols
        all_rows.extend(rows)
    assert columns == ["id", "name"]
    assert len(all_rows) == 10
    assert all_rows[0] == (1, "user1")
    assert all_rows[-1] == (10, "user10")


def test_stream_table_rows_respects_chunk_size(db):
    chunks = list(db.stream_table_rows("users", chunk_size=4))
    assert [len(rows) for _, rows in chunks] == [4, 4, 2]


def test_stream_table_rows_empty_table_yields_nothing(db):
    db.execute_update("CREATE TABLE empty_t (id INTEGER)")
    assert list(db.stream_table_rows("empty_t")) == []


def test_stream_table_rows_requires_connection():
    db = DbService()
    with pytest.raises(Exception):
        list(db.stream_table_rows("users"))
