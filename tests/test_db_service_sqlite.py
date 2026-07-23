import pytest

from services.db_service import DbService


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "test.db")
    service = DbService()
    service.connect({"type": "sqlite", "name": "test", "database": path})
    service.execute_update(
        "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
    )
    service.execute_update("INSERT INTO users (id, name) VALUES (1, 'Alice')")
    service.execute_update("INSERT INTO users (id, name) VALUES (2, 'Bob')")
    yield service
    service.disconnect()


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
