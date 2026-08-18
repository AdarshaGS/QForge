import pytest

from services.db_service import DbService, ReadOnlyViolation, TransactionError


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def db(db_path):
    service = DbService()
    service.connect({"type": "sqlite", "name": "test", "database": db_path})
    service.execute_update(
        "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
    )
    service.execute_update("INSERT INTO users (id, name) VALUES (1, 'Alice')")
    service.execute_update("INSERT INTO users (id, name) VALUES (2, 'Bob')")
    yield service
    service.disconnect()


def _second_connection(db_path):
    """A fully independent DbService to the same file — proves durability
    from a change made on `db`, not just in-process state on that object."""
    other = DbService()
    other.connect({"type": "sqlite", "name": "test2", "database": db_path})
    return other


def test_get_tables_lists_created_table(db):
    assert db.get_tables() == ["users"]


def test_execute_query_returns_rows(db):
    df = db.execute_query("SELECT * FROM users ORDER BY id")
    assert list(df["name"]) == ["Alice", "Bob"]
    assert df.attrs["truncated"] is False


def test_execute_query_max_rows_caps_and_flags_truncation(db):
    df = db.execute_query("SELECT * FROM users ORDER BY id", max_rows=1)
    assert list(df["name"]) == ["Alice"]
    assert df.attrs["truncated"] is True


def test_execute_query_max_rows_not_truncated_when_under_limit(db):
    df = db.execute_query("SELECT * FROM users ORDER BY id", max_rows=10)
    assert list(df["name"]) == ["Alice", "Bob"]
    assert df.attrs["truncated"] is False


def test_execute_update_reports_affected_rows(db):
    affected = db.execute_update("UPDATE users SET name = 'Carol' WHERE id = 1")
    assert affected == 1
    df = db.execute_query("SELECT name FROM users WHERE id = 1")
    assert df.iloc[0]["name"] == "Carol"


def test_execute_batch_inserts_all_rows_and_commits_once(db):
    rows = [(i, f"user{i}") for i in range(3, 13)]
    inserted, errors = db.execute_batch(
        "INSERT INTO users (id, name) VALUES (?, ?)", rows, batch_size=4
    )
    assert inserted == 10
    assert errors == 0
    df = db.execute_query("SELECT COUNT(*) as total FROM users")
    assert int(df.iloc[0]["total"]) == 12


def test_execute_batch_counts_failing_batches_without_aborting(db):
    # id=1 already exists, so this batch violates the PRIMARY KEY constraint
    # and should be counted as an error without stopping the remaining batches.
    rows = [(1, "dup"), (10, "ok")]
    inserted, errors = db.execute_batch(
        "INSERT INTO users (id, name) VALUES (?, ?)", rows, batch_size=1
    )
    assert errors == 1
    assert inserted == 1


def test_get_columns_reports_field_names(db):
    cols = db.get_columns("users")
    assert [c["Field"] for c in cols] == ["id", "name"]


def test_get_all_columns_maps_table_to_columns(db):
    mapping = db.get_all_columns()
    assert "users" in mapping
    assert "name" in mapping["users"]


# ─── Transaction controls (Slice 4, ai/load-context.md) ────────────────────


def test_default_autocommit_behavior_unchanged(db, db_path):
    """No begin_transaction() call — every statement still persists
    immediately, exactly like before this slice existed."""
    db.execute_update("UPDATE users SET name = 'Carol' WHERE id = 1")
    other = _second_connection(db_path)
    try:
        df = other.execute_query("SELECT name FROM users WHERE id = 1")
        assert df.iloc[0]["name"] == "Carol"
    finally:
        other.disconnect()


def test_begin_commit_persists_change(db, db_path):
    db.begin_transaction()
    assert db.in_transaction
    db.execute_update("UPDATE users SET name = 'Carol' WHERE id = 1")
    db.commit_transaction()
    assert not db.in_transaction

    other = _second_connection(db_path)
    try:
        df = other.execute_query("SELECT name FROM users WHERE id = 1")
        assert df.iloc[0]["name"] == "Carol"
    finally:
        other.disconnect()


def test_begin_rollback_discards_change(db, db_path):
    db.begin_transaction()
    db.execute_update("UPDATE users SET name = 'Carol' WHERE id = 1")
    db.rollback_transaction()
    assert not db.in_transaction

    other = _second_connection(db_path)
    try:
        df = other.execute_query("SELECT name FROM users WHERE id = 1")
        assert df.iloc[0]["name"] == "Alice"
    finally:
        other.disconnect()


def test_sqlite_dml_inside_transaction_does_not_auto_commit(db, db_path):
    """Regression test for the bug this slice fixes: a write statement
    inside a manual transaction used to unconditionally commit(), silently
    ending the transaction after the first statement."""
    db.begin_transaction()
    db.execute_update("UPDATE users SET name = 'Carol' WHERE id = 1")

    other = _second_connection(db_path)
    try:
        df = other.execute_query("SELECT name FROM users WHERE id = 1")
        assert df.iloc[0]["name"] == "Alice"  # not yet visible — no auto-commit
    finally:
        other.disconnect()

    db.commit_transaction()
    other2 = _second_connection(db_path)
    try:
        df = other2.execute_query("SELECT name FROM users WHERE id = 1")
        assert df.iloc[0]["name"] == "Carol"  # visible after explicit commit
    finally:
        other2.disconnect()


def test_double_begin_raises(db):
    db.begin_transaction()
    with pytest.raises(TransactionError):
        db.begin_transaction()


def test_commit_without_begin_raises(db):
    with pytest.raises(TransactionError):
        db.commit_transaction()


def test_rollback_without_begin_raises(db):
    with pytest.raises(TransactionError):
        db.rollback_transaction()


def test_raw_typed_begin_commit_via_execute_query(db, db_path):
    """A user can type BEGIN/COMMIT directly in the editor instead of
    clicking the dedicated buttons — execute_query() must route these the
    same way so in_transaction stays accurate regardless of entry point."""
    db.execute_query("BEGIN")
    assert db.in_transaction
    db.execute_update("UPDATE users SET name = 'Carol' WHERE id = 1")
    db.execute_query("COMMIT")
    assert not db.in_transaction

    other = _second_connection(db_path)
    try:
        df = other.execute_query("SELECT name FROM users WHERE id = 1")
        assert df.iloc[0]["name"] == "Carol"
    finally:
        other.disconnect()


def test_raw_typed_rollback_via_execute_query(db, db_path):
    db.execute_query("BEGIN")
    db.execute_update("UPDATE users SET name = 'Carol' WHERE id = 1")
    db.execute_query("ROLLBACK")
    assert not db.in_transaction

    other = _second_connection(db_path)
    try:
        df = other.execute_query("SELECT name FROM users WHERE id = 1")
        assert df.iloc[0]["name"] == "Alice"
    finally:
        other.disconnect()


# ─── Read-only guard coverage (issue #70) ──────────────────────────────────


def test_read_only_blocks_replace_and_load_data(db):
    db.read_only = True
    with pytest.raises(ReadOnlyViolation):
        db.execute_query("REPLACE INTO users (id, name) VALUES (1, 'Mallory')")
    with pytest.raises(ReadOnlyViolation):
        db.execute_query("LOAD DATA INFILE 'x.csv' INTO TABLE users")
    # Statement never reached the driver — data is untouched.
    df = db.execute_query("SELECT name FROM users WHERE id = 1")
    assert df.iloc[0]["name"] == "Alice"


def test_read_only_blocks_procedure_calls_even_though_not_classified_as_writes(db):
    db.read_only = True
    with pytest.raises(ReadOnlyViolation):
        db.execute_query("CALL some_proc(1)")
    with pytest.raises(ReadOnlyViolation):
        db.execute_query("EXEC some_proc")


def test_read_only_still_allows_reads(db):
    db.read_only = True
    df = db.execute_query("SELECT * FROM users ORDER BY id")
    assert list(df["name"]) == ["Alice", "Bob"]


def test_read_only_off_by_default_writes_still_work(db):
    assert db.read_only is False
    affected = db.execute_update("UPDATE users SET name = 'Carol' WHERE id = 1")
    assert affected == 1


def test_read_only_pragma_is_set_on_the_connection(db_path):
    """Defense-in-depth beyond the client-side guard: connect() itself
    should put the SQLite connection into query_only mode server-side —
    a raw write attempted via the driver directly (bypassing _guard) must
    still fail."""
    service = DbService()
    service.connect({"type": "sqlite", "name": "t", "database": db_path, "read_only": True})
    try:
        with pytest.raises(Exception):
            service.connection.execute("INSERT INTO sqlite_master VALUES (1,2,3,4,5)")
    finally:
        service.disconnect()


# ─── NULLs and data types ───────────────────────────────────────────────


def test_null_values_come_back_as_none(db):
    db.execute_update("CREATE TABLE t_null (id INTEGER PRIMARY KEY, note TEXT)")
    db.execute_update("INSERT INTO t_null (id, note) VALUES (1, NULL)")
    df = db.execute_query("SELECT note FROM t_null WHERE id = 1")
    assert df.iloc[0]["note"] is None


def test_various_sqlite_types_round_trip(db):
    db.execute_update(
        "CREATE TABLE t_types (id INTEGER PRIMARY KEY, flag INTEGER, "
        "amount REAL, note TEXT, blob_col BLOB)"
    )
    db.execute_update(
        "INSERT INTO t_types (id, flag, amount, note, blob_col) VALUES "
        "(1, 1, 19.99, 'hi', X'0102')"
    )
    df = db.execute_query("SELECT * FROM t_types WHERE id = 1")
    row = df.iloc[0]
    assert int(row["flag"]) == 1
    assert float(row["amount"]) == 19.99
    assert row["note"] == "hi"
    assert bytes(row["blob_col"]) == b"\x01\x02"


# ─── Schema introspection ───────────────────────────────────────────────


def test_get_primary_keys_single_column(db):
    assert db.get_primary_keys("users") == ["id"]


def test_get_primary_keys_composite(db):
    db.execute_update(
        "CREATE TABLE membership (org_id INTEGER, user_id INTEGER, "
        "PRIMARY KEY (org_id, user_id))"
    )
    assert db.get_primary_keys("membership") == ["org_id", "user_id"]


def test_get_primary_keys_empty_when_no_pk(db):
    db.execute_update("CREATE TABLE no_pk (note TEXT)")
    assert db.get_primary_keys("no_pk") == []


def test_get_foreign_keys(db):
    db.execute_update(
        "CREATE TABLE orders (id INTEGER PRIMARY KEY, "
        "user_id INTEGER REFERENCES users(id))"
    )
    fks = db.get_foreign_keys("orders")
    assert fks == [{"column": "user_id", "ref_table": "users", "ref_column": "id"}]


def test_get_foreign_keys_empty_when_none(db):
    assert db.get_foreign_keys("users") == []


def test_get_indexes_reports_unique_and_non_unique(db):
    db.execute_update("CREATE UNIQUE INDEX idx_users_name ON users(name)")
    db.execute_update("CREATE TABLE logs (id INTEGER PRIMARY KEY, user_id INTEGER)")
    db.execute_update("CREATE INDEX idx_logs_user ON logs(user_id)")

    user_indexes = {i["name"]: i for i in db.get_indexes("users")}
    assert user_indexes["idx_users_name"]["unique"] is True

    log_indexes = {i["name"]: i for i in db.get_indexes("logs")}
    assert log_indexes["idx_logs_user"]["unique"] is False


def test_get_table_ddl_returns_original_create_statement(db):
    ddl = db.get_table_ddl("users")
    assert "CREATE TABLE users" in ddl
    assert "id" in ddl and "name" in ddl


def test_get_table_ddl_unknown_table_returns_empty_string(db):
    assert db.get_table_ddl("does_not_exist") == ""


def test_get_table_ddl_includes_secondary_index(db):
    db.execute_update("CREATE INDEX idx_users_name ON users(name)")
    ddl = db.get_table_ddl("users")
    assert "CREATE TABLE users" in ddl
    assert "CREATE INDEX idx_users_name" in ddl


def test_get_table_ddl_does_not_duplicate_implicit_pk_index(db):
    # A composite/UNIQUE PK creates an internal sqlite_autoindex_* entry
    # with no stored sql text — it must not surface as a bare "None;".
    db.execute_update("CREATE TABLE composite_pk (a INTEGER, b INTEGER, PRIMARY KEY (a, b))")
    ddl = db.get_table_ddl("composite_pk")
    assert "None" not in ddl


def test_get_views(db):
    db.execute_update("CREATE VIEW active_users AS SELECT * FROM users")
    assert db.get_views() == ["active_users"]


def test_get_views_empty_when_none(db):
    assert db.get_views() == []


def test_get_functions_returns_empty_list(db):
    """SQLite has no stored procedures/functions to introspect."""
    assert db.get_functions() == []


def test_get_server_version_reports_sqlite(db):
    assert db.get_server_version().startswith("SQLite")


# ─── Multi-statement scripts ────────────────────────────────────────────


def test_execute_multi_query_runs_all_statements(db):
    script = "SELECT * FROM users ORDER BY id; UPDATE users SET name = 'Zed' WHERE id = 1;"
    results = db.execute_multi_query(script)
    assert len(results) == 2
    _, df1 = results[0]
    _, df2 = results[1]
    assert list(df1["name"]) == ["Alice", "Bob"]
    assert df2 is None  # UPDATE has no result set
    assert db.execute_query("SELECT name FROM users WHERE id = 1").iloc[0]["name"] == "Zed"


def test_execute_multi_query_continues_after_a_statement_error(db):
    script = "SELECT * FROM does_not_exist; SELECT * FROM users ORDER BY id;"
    results = db.execute_multi_query(script)
    assert len(results) == 2
    _, first_result = results[0]
    _, second_result = results[1]
    assert isinstance(first_result, Exception)
    assert list(second_result["name"]) == ["Alice", "Bob"]


# ─── Connection lifecycle ────────────────────────────────────────────────


def test_is_connected_true_when_connected(db):
    assert db.is_connected() is True


def test_is_connected_false_after_disconnect(db):
    db.disconnect()
    assert db.is_connected() is False


def test_execute_query_without_connection_raises():
    service = DbService()
    with pytest.raises(Exception):
        service.execute_query("SELECT 1")


def test_syntax_error_propagates_as_an_exception(db):
    with pytest.raises(Exception):
        db.execute_query("SELECT * FORM users")  # deliberate typo


def test_connect_missing_database_path_raises():
    service = DbService()
    with pytest.raises(Exception):
        service.connect({"type": "sqlite", "name": "t"})
