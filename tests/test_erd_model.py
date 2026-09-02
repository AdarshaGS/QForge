"""Tests for the ERD metadata graph model (issue #63) — pure data, no UI.

Live-Postgres integration (skipped if no local server, mirrors
tests/test_db_service_postgresql.py's fixture)."""
import uuid

import psycopg2
import pytest

from services.db_service import DbService
from services.erd_model import build_erd_graph, fetch_table_indexes

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
def config():
    name = f"qforge_test_{uuid.uuid4().hex[:12]}"
    admin = psycopg2.connect(connect_timeout=5, **_ADMIN_PARAMS)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    admin.close()

    yield {
        "type": "postgresql", "name": "erd_test", "host": _PG_HOST, "port": _PG_PORT,
        "user": _PG_USER, "password": _PG_PASSWORD, "database": name,
    }

    admin = psycopg2.connect(connect_timeout=5, **_ADMIN_PARAMS)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()", (name,))
        cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
    admin.close()


def test_build_erd_graph_represents_tables_columns_pk_fk(config):
    setup = DbService()
    setup.connect(config)
    setup.execute_update("""
        CREATE TABLE authors (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL
        )
    """)
    setup.execute_update("""
        CREATE TABLE books (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            author_id INTEGER,
            FOREIGN KEY (author_id) REFERENCES authors(id)
        )
    """)
    setup.disconnect()

    graph = build_erd_graph(config)

    assert set(graph.tables) == {"authors", "books"}

    authors = graph.tables["authors"]
    id_col = next(c for c in authors.columns if c.name == "id")
    name_col = next(c for c in authors.columns if c.name == "name")
    assert id_col.is_primary_key
    assert not name_col.is_primary_key

    books = graph.tables["books"]
    author_id_col = next(c for c in books.columns if c.name == "author_id")
    assert author_id_col.is_foreign_key
    assert not author_id_col.is_primary_key

    assert len(graph.relationships) == 1
    rel = graph.relationships[0]
    assert rel.source_table == "books"
    assert rel.source_column == "author_id"
    assert rel.target_table == "authors"
    assert rel.target_column == "id"


def test_build_erd_graph_drops_relationship_to_a_table_outside_the_set(config):
    """A FK pointing at a table that wasn't included (or doesn't exist)
    must not crash generation and must not appear as a relationship."""
    setup = DbService()
    setup.connect(config)
    setup.execute_update("CREATE TABLE customers (id INTEGER PRIMARY KEY)")
    setup.execute_update("""
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            customer_id INTEGER,
            FOREIGN KEY (customer_id) REFERENCES customers(id)
        )
    """)
    setup.disconnect()

    graph = build_erd_graph(config, table_names=["orders"])

    assert set(graph.tables) == {"orders"}
    assert graph.relationships == []


def test_build_erd_graph_on_empty_database_returns_empty_graph(config):
    setup = DbService()
    setup.connect(config)
    setup.disconnect()

    graph = build_erd_graph(config)

    assert graph.tables == {}
    assert graph.relationships == []


def test_build_erd_graph_respects_table_names_filter(config):
    setup = DbService()
    setup.connect(config)
    setup.execute_update("CREATE TABLE a (id INTEGER PRIMARY KEY)")
    setup.execute_update("CREATE TABLE b (id INTEGER PRIMARY KEY)")
    setup.disconnect()

    graph = build_erd_graph(config, table_names=["a"])

    assert set(graph.tables) == {"a"}


def test_build_erd_graph_marks_one_to_one_when_fk_column_is_unique(config):
    setup = DbService()
    setup.connect(config)
    setup.execute_update("""
        CREATE TABLE authors (
            id INTEGER PRIMARY KEY,
            name TEXT
        )
    """)
    setup.execute_update("""
        CREATE TABLE profiles (
            id INTEGER PRIMARY KEY,
            author_id INTEGER UNIQUE,
            bio TEXT,
            FOREIGN KEY (author_id) REFERENCES authors(id)
        )
    """)
    setup.execute_update("""
        CREATE TABLE books (
            id INTEGER PRIMARY KEY,
            title TEXT,
            author_id INTEGER,
            FOREIGN KEY (author_id) REFERENCES authors(id)
        )
    """)
    setup.disconnect()

    graph = build_erd_graph(config)

    rel_by_source = {rel.source_table: rel for rel in graph.relationships}
    assert rel_by_source["profiles"].is_one_to_one is True
    assert rel_by_source["books"].is_one_to_one is False


def test_fetch_table_indexes_returns_indexes_for_a_table(config):
    setup = DbService()
    setup.connect(config)
    setup.execute_update("""
        CREATE TABLE widgets (
            id INTEGER PRIMARY KEY,
            sku TEXT UNIQUE,
            name TEXT
        )
    """)
    setup.disconnect()

    indexes = fetch_table_indexes(config, "widgets")

    assert any(idx["unique"] and idx["columns"] == "sku" for idx in indexes)


def test_get_primary_keys_composite(config):
    db = DbService()
    db.connect(config)
    db.execute_update("""
        CREATE TABLE composite_pk (
            a INTEGER,
            b INTEGER,
            c TEXT,
            PRIMARY KEY (a, b)
        )
    """)
    try:
        assert db.get_primary_keys("composite_pk") == ["a", "b"]
        db.execute_update("CREATE TABLE no_pk (x TEXT)")
        assert db.get_primary_keys("no_pk") == []
    finally:
        db.disconnect()
