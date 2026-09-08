"""Regression tests for issue #274 (Clone Database).

No live database needed — execute_update() and the MySQL information_schema
cursor are monkeypatched to record what SQL DbService.clone_database() would
run, so these tests exercise only the per-engine statement sequence and
identifier quoting, not a real connection.
"""
from services.db_service import DbService


def _mysql_db(monkeypatch, tables):
    db = DbService()
    db.db_type = "mysql"
    db._config = {"type": "mysql", "name": "test", "database": "shop", "id": "conn-1"}

    executed = []
    monkeypatch.setattr(db, "execute_update", lambda sql: executed.append(sql))

    class _FakeCursor:
        def execute(self, query, params=None):
            pass

        def fetchall(self):
            return [{"TABLE_NAME": t} for t in tables]

        def close(self):
            pass

    class _FakeConnection:
        def cursor(self):
            return _FakeCursor()

    db.connection = _FakeConnection()
    return db, executed


def _postgres_db(monkeypatch):
    db = DbService()
    db.db_type = "postgresql"
    db._config = {"type": "postgresql", "name": "test", "database": "postgres", "id": "conn-1"}
    db.connection = object()

    executed = []
    monkeypatch.setattr(db, "execute_update", lambda sql: executed.append(sql))
    return db, executed


def test_mysql_clone_creates_db_then_like_and_insert_per_table(monkeypatch):
    db, executed = _mysql_db(monkeypatch, ["users", "orders"])

    db.clone_database("shop", "shop_copy")

    assert executed[0] == "CREATE DATABASE `shop_copy`"
    assert executed[1] == "CREATE TABLE `shop_copy`.`users` LIKE `shop`.`users`"
    assert executed[2] == "INSERT INTO `shop_copy`.`users` SELECT * FROM `shop`.`users`"
    assert executed[3] == "CREATE TABLE `shop_copy`.`orders` LIKE `shop`.`orders`"
    assert executed[4] == "INSERT INTO `shop_copy`.`orders` SELECT * FROM `shop`.`orders`"
    assert len(executed) == 5


def test_mysql_clone_of_empty_database_just_creates_it(monkeypatch):
    db, executed = _mysql_db(monkeypatch, [])

    db.clone_database("empty_db", "empty_db_copy")

    assert executed == ["CREATE DATABASE `empty_db_copy`"]


def test_postgres_clone_is_a_single_create_database_with_template(monkeypatch):
    db, executed = _postgres_db(monkeypatch)

    db.clone_database("shop", "shop_copy")

    assert executed == ['CREATE DATABASE "shop_copy" WITH TEMPLATE "shop"']


def test_unsupported_db_type_raises(monkeypatch):
    db = DbService()
    db.db_type = "sqlite"
    db.connection = object()

    try:
        db.clone_database("a", "b")
        assert False, "expected an exception"
    except Exception as ex:
        assert "not supported" in str(ex)
