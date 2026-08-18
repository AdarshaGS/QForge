"""Tests for DbService.stream_table_rows() (issue #158): reads a table via
cursor.fetchmany() chunks instead of execute_query()'s fetchall(), so a
table export never has to materialize the whole table in memory first."""
import pytest

from services.db_service import DbService


@pytest.fixture
def db(tmp_path):
    service = DbService()
    service.connect({"type": "sqlite", "name": "test", "database": str(tmp_path / "t.db")})
    service.execute_update("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
    for i in range(1, 11):
        service.execute_update(f"INSERT INTO users (id, name) VALUES ({i}, 'user{i}')")  # nosec B608
    yield service
    service.disconnect()


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
