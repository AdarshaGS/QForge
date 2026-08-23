"""Tests for the cross-connection data diff engine (issue #203) — pure data, no UI."""
from services.data_diff import build_data_diff, table_select_sql
from services.db_service import DbService


def _make_sqlite_config(tmp_path, name):
    return {"type": "sqlite", "name": name, "database": str(tmp_path / f"{name}.db")}


def _run(config, *statements):
    db = DbService()
    db.connect(config)
    for stmt in statements:
        db.execute_update(stmt)
    db.disconnect()


def _table_sql(table_name):
    return table_select_sql(table_name, "sqlite")


def test_identical_tables_are_unchanged(tmp_path):
    source = _make_sqlite_config(tmp_path, "source")
    target = _make_sqlite_config(tmp_path, "target")
    ddl = "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)"
    _run(source, ddl, "INSERT INTO users VALUES (1, 'alice'), (2, 'bob')")
    _run(target, ddl, "INSERT INTO users VALUES (1, 'alice'), (2, 'bob')")

    diff = build_data_diff(source, target, _table_sql("users"), _table_sql("users"), ["id"])

    assert diff.rows_added == []
    assert diff.rows_removed == []
    assert diff.rows_modified == []
    assert diff.rows_unchanged == 2


def test_added_and_removed_rows_by_key(tmp_path):
    source = _make_sqlite_config(tmp_path, "source")
    target = _make_sqlite_config(tmp_path, "target")
    ddl = "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)"
    _run(source, ddl, "INSERT INTO users VALUES (1, 'alice'), (2, 'bob')")
    _run(target, ddl, "INSERT INTO users VALUES (2, 'bob'), (3, 'carol')")

    diff = build_data_diff(source, target, _table_sql("users"), _table_sql("users"), ["id"])

    assert diff.rows_removed_total == 1
    assert diff.rows_removed[0].key == "1"
    assert diff.rows_added_total == 1
    assert diff.rows_added[0].key == "3"
    assert diff.rows_unchanged == 1


def test_modified_row_reports_changed_cells(tmp_path):
    source = _make_sqlite_config(tmp_path, "source")
    target = _make_sqlite_config(tmp_path, "target")
    ddl = "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, email TEXT)"
    _run(source, ddl, "INSERT INTO users VALUES (1, 'alice', 'a@x.com')")
    _run(target, ddl, "INSERT INTO users VALUES (1, 'alice', 'alice@x.com')")

    diff = build_data_diff(source, target, _table_sql("users"), _table_sql("users"), ["id"])

    assert diff.rows_modified_total == 1
    row = diff.rows_modified[0]
    assert row.key == "1"
    assert row.field_changes == {"email": ("a@x.com", "alice@x.com")}
    assert "name" not in row.field_changes


def test_composite_key_matches_rows(tmp_path):
    source = _make_sqlite_config(tmp_path, "source")
    target = _make_sqlite_config(tmp_path, "target")
    ddl = "CREATE TABLE items (tenant TEXT, sku TEXT, qty INTEGER)"
    _run(source, ddl, "INSERT INTO items VALUES ('t1', 'a', 5), ('t2', 'a', 9)")
    _run(target, ddl, "INSERT INTO items VALUES ('t1', 'a', 7), ('t2', 'a', 9)")

    diff = build_data_diff(source, target, _table_sql("items"), _table_sql("items"), ["tenant", "sku"])

    assert diff.rows_modified_total == 1
    assert diff.rows_modified[0].key == "t1|a"
    assert diff.rows_unchanged == 1


def test_query_mode_diffs_arbitrary_selects(tmp_path):
    source = _make_sqlite_config(tmp_path, "source")
    target = _make_sqlite_config(tmp_path, "target")
    _run(source, "CREATE TABLE orders (id INTEGER PRIMARY KEY, status TEXT)",
         "INSERT INTO orders VALUES (1, 'open'), (2, 'closed')")
    _run(target, "CREATE TABLE orders (id INTEGER PRIMARY KEY, status TEXT)",
         "INSERT INTO orders VALUES (1, 'closed'), (2, 'closed')")

    diff = build_data_diff(
        source, target,
        "SELECT id, status FROM orders WHERE status = 'open' OR id = 1",
        "SELECT id, status FROM orders WHERE id = 1",
        ["id"],
    )

    assert diff.rows_modified_total == 1
    assert diff.rows_modified[0].field_changes == {"status": ("open", "closed")}


def test_row_limit_caps_and_flags_truncation(tmp_path):
    source = _make_sqlite_config(tmp_path, "source")
    target = _make_sqlite_config(tmp_path, "target")
    ddl = "CREATE TABLE nums (id INTEGER PRIMARY KEY)"
    values = ", ".join(f"({i})" for i in range(1, 11))
    _run(source, ddl, f"INSERT INTO nums VALUES {values}")
    _run(target, ddl, f"INSERT INTO nums VALUES {values}")

    diff = build_data_diff(source, target, _table_sql("nums"), _table_sql("nums"), ["id"], row_limit=5)

    assert diff.truncated_source is True
    assert diff.truncated_target is True
    # Only ids 1-5 were fetched on each side, so all compared rows still match.
    assert diff.rows_unchanged == 5
    assert diff.rows_added == []
    assert diff.rows_removed == []


def test_no_key_falls_back_to_whole_row_matching(tmp_path):
    source = _make_sqlite_config(tmp_path, "source")
    target = _make_sqlite_config(tmp_path, "target")
    ddl = "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)"
    _run(source, ddl, "INSERT INTO users VALUES (1, 'alice'), (2, 'bob')")
    _run(target, ddl, "INSERT INTO users VALUES (2, 'bob'), (3, 'carol')")

    diff = build_data_diff(source, target, _table_sql("users"), _table_sql("users"), key_columns=None)

    assert diff.rows_unchanged == 1
    assert diff.rows_removed_total == 1
    assert diff.rows_added_total == 1
    assert diff.rows_modified == []  # whole-row mode never reports "modified"


def test_no_key_counts_exact_duplicate_rows(tmp_path):
    source = _make_sqlite_config(tmp_path, "source")
    target = _make_sqlite_config(tmp_path, "target")
    ddl = "CREATE TABLE logs (event TEXT)"
    _run(source, ddl, "INSERT INTO logs VALUES ('login'), ('login'), ('login')")
    _run(target, ddl, "INSERT INTO logs VALUES ('login'), ('login')")

    diff = build_data_diff(source, target, _table_sql("logs"), _table_sql("logs"), key_columns=[])

    assert diff.rows_unchanged == 2
    assert diff.rows_removed_total == 1
    assert diff.rows_added_total == 0


def test_no_key_row_differing_in_any_column_counts_as_added_and_removed(tmp_path):
    source = _make_sqlite_config(tmp_path, "source")
    target = _make_sqlite_config(tmp_path, "target")
    ddl = "CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT)"
    _run(source, ddl, "INSERT INTO users VALUES (1, 'a@x.com')")
    _run(target, ddl, "INSERT INTO users VALUES (1, 'alice@x.com')")

    diff = build_data_diff(source, target, _table_sql("users"), _table_sql("users"), key_columns=[])

    assert diff.rows_unchanged == 0
    assert diff.rows_removed_total == 1
    assert diff.rows_added_total == 1
    assert diff.rows_modified == []


def test_missing_key_column_raises(tmp_path):
    source = _make_sqlite_config(tmp_path, "source")
    target = _make_sqlite_config(tmp_path, "target")
    ddl = "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)"
    _run(source, ddl, "INSERT INTO users VALUES (1, 'alice')")
    _run(target, ddl, "INSERT INTO users VALUES (1, 'alice')")

    try:
        build_data_diff(source, target, _table_sql("users"), _table_sql("users"), ["does_not_exist"])
        assert False, "expected ValueError"
    except ValueError as ex:
        assert "does_not_exist" in str(ex)
