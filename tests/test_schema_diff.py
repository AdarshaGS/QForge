"""Tests for the schema-compare diff model (issue #68) — pure data, no UI."""
from services.db_service import DbService
from services.schema_diff import build_schema_diff


def _make_sqlite_config(tmp_path, name):
    return {"type": "sqlite", "name": name, "database": str(tmp_path / f"{name}.db")}


def _run(config, *statements):
    db = DbService()
    db.connect(config)
    for stmt in statements:
        db.execute_update(stmt)
    db.disconnect()


def test_added_and_removed_tables(tmp_path):
    source = _make_sqlite_config(tmp_path, "source")
    target = _make_sqlite_config(tmp_path, "target")
    _run(source, "CREATE TABLE only_in_source (id INTEGER PRIMARY KEY)")
    _run(target, "CREATE TABLE only_in_target (id INTEGER PRIMARY KEY)")

    diff = build_schema_diff(source, target)

    assert diff.tables_added == ["only_in_target"]
    assert diff.tables_removed == ["only_in_source"]
    assert diff.tables_modified == []
    assert diff.tables_unchanged == 0


def test_identical_tables_are_unchanged(tmp_path):
    source = _make_sqlite_config(tmp_path, "source")
    target = _make_sqlite_config(tmp_path, "target")
    ddl = "CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
    _run(source, ddl)
    _run(target, ddl)

    diff = build_schema_diff(source, target)

    assert diff.tables_added == []
    assert diff.tables_removed == []
    assert diff.tables_modified == []
    assert diff.tables_unchanged == 1


def test_added_removed_and_modified_columns(tmp_path):
    source = _make_sqlite_config(tmp_path, "source")
    target = _make_sqlite_config(tmp_path, "target")
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


def test_index_added_removed_and_modified(tmp_path):
    source = _make_sqlite_config(tmp_path, "source")
    target = _make_sqlite_config(tmp_path, "target")
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


def test_foreign_key_added_and_removed(tmp_path):
    source = _make_sqlite_config(tmp_path, "source")
    target = _make_sqlite_config(tmp_path, "target")
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


def test_table_names_filter_limits_comparison(tmp_path):
    source = _make_sqlite_config(tmp_path, "source")
    target = _make_sqlite_config(tmp_path, "target")
    _run(source, "CREATE TABLE a (id INTEGER PRIMARY KEY)",
         "CREATE TABLE b (id INTEGER PRIMARY KEY)")
    _run(target, "CREATE TABLE a (id INTEGER PRIMARY KEY, extra TEXT)")

    diff = build_schema_diff(source, target, table_names=["a"])

    assert diff.tables_removed == []
    assert len(diff.tables_modified) == 1
    assert diff.tables_modified[0].name == "a"


def test_missing_table_metadata_does_not_crash(tmp_path):
    source = _make_sqlite_config(tmp_path, "source")
    target = _make_sqlite_config(tmp_path, "target")
    _run(source, "CREATE TABLE ok (id INTEGER PRIMARY KEY)")
    _run(target, "CREATE TABLE ok (id INTEGER PRIMARY KEY)")

    diff = build_schema_diff(source, target, table_names=["ok", "does_not_exist"])

    # sqlite's PRAGMA calls return an empty result rather than raising for a
    # nonexistent table, so "does_not_exist" comes back identically empty on
    # both sides and counts as unchanged too — the point of this test is
    # just that a name with no real metadata doesn't crash the comparison.
    assert diff.tables_unchanged == 2
    assert diff.tables_added == []
    assert diff.tables_removed == []
