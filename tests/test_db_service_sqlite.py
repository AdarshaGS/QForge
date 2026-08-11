import pytest

from services.db_service import DbService, TransactionError


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
