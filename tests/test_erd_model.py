"""Tests for the ERD metadata graph model (issue #63) — pure data, no UI."""
from services.db_service import DbService
from services.erd_model import build_erd_graph, fetch_table_indexes


def _make_sqlite_config(tmp_path, name="erd_test"):
    return {"type": "sqlite", "name": name, "database": str(tmp_path / f"{name}.db")}


def test_build_erd_graph_represents_tables_columns_pk_fk(tmp_path):
    config = _make_sqlite_config(tmp_path)
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


def test_build_erd_graph_drops_relationship_to_a_table_outside_the_set(tmp_path):
    """A FK pointing at a table that wasn't included (or doesn't exist)
    must not crash generation and must not appear as a relationship."""
    config = _make_sqlite_config(tmp_path)
    setup = DbService()
    setup.connect(config)
    setup.execute_update("""
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            customer_id INTEGER,
            FOREIGN KEY (customer_id) REFERENCES customers(id)
        )
    """)
    setup.disconnect()

    graph = build_erd_graph(config)

    assert set(graph.tables) == {"orders"}
    assert graph.relationships == []


def test_build_erd_graph_on_empty_database_returns_empty_graph(tmp_path):
    config = _make_sqlite_config(tmp_path)
    setup = DbService()
    setup.connect(config)
    setup.disconnect()

    graph = build_erd_graph(config)

    assert graph.tables == {}
    assert graph.relationships == []


def test_build_erd_graph_respects_table_names_filter(tmp_path):
    config = _make_sqlite_config(tmp_path)
    setup = DbService()
    setup.connect(config)
    setup.execute_update("CREATE TABLE a (id INTEGER PRIMARY KEY)")
    setup.execute_update("CREATE TABLE b (id INTEGER PRIMARY KEY)")
    setup.disconnect()

    graph = build_erd_graph(config, table_names=["a"])

    assert set(graph.tables) == {"a"}


def test_build_erd_graph_marks_one_to_one_when_fk_column_is_unique(tmp_path):
    config = _make_sqlite_config(tmp_path)
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


def test_fetch_table_indexes_returns_indexes_for_a_table(tmp_path):
    config = _make_sqlite_config(tmp_path)
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


def test_get_primary_keys_sqlite(tmp_path):
    config = _make_sqlite_config(tmp_path)
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
