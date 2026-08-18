"""Tests for DbService.get_generated_columns()/content_select_list() (issue
#160): generated columns can't appear in an INSERT column list, so content
exports need to know which columns to leave out."""
import pytest

from services.db_service import DbService


@pytest.fixture
def db(tmp_path):
    service = DbService()
    service.connect({"type": "sqlite", "name": "test", "database": str(tmp_path / "t.db")})
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
