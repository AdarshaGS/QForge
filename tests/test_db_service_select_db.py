"""Regression test: DbService.select_db() must self-heal a dead connection.

Switching the active database previously called `self.connection.select_db()`
directly, bypassing the reconnect-on-drop retry execute_query() gets for
free — a connection that went stale (idle timeout, dropped SSH tunnel) failed
select_db() outright with e.g. "(0, '')" and left the switch permanently
stuck (GitHub report: "not able to switch from one database to another").
"""
from services.db_service import DbService


class _DeadConnection:
    def select_db(self, database):
        raise Exception("(0, '')")


class _LiveConnection:
    def __init__(self):
        self.selected = None

    def select_db(self, database):
        self.selected = database


def _db_with_dead_connection():
    db = DbService()
    db.db_type = "mysql"
    db._config = {"type": "mysql", "name": "test", "host": "h", "port": 3306,
                   "user": "u", "password": "p", "database": "old_db"}
    db.connection = _DeadConnection()
    return db


def test_select_db_reconnects_when_connection_is_dead(monkeypatch):
    db = _db_with_dead_connection()
    reconnected_with = {}

    def fake_connect(config):
        reconnected_with["config"] = config
        db.connection = _LiveConnection()

    monkeypatch.setattr(db, "connect", fake_connect)

    db.select_db("new_db")

    assert reconnected_with["config"]["database"] == "new_db"
    assert isinstance(db.connection, _LiveConnection)


def test_select_db_reuses_live_connection_without_reconnecting(monkeypatch):
    db = DbService()
    db.db_type = "mysql"
    db._config = {"type": "mysql", "name": "test"}
    live = _LiveConnection()
    db.connection = live

    def fail_if_called(config):
        raise AssertionError("should not reconnect a healthy connection")

    monkeypatch.setattr(db, "connect", fail_if_called)

    db.select_db("new_db")

    assert live.selected == "new_db"


def test_select_db_propagates_non_connection_errors(monkeypatch):
    db = DbService()
    db.db_type = "mysql"
    db._config = {"type": "mysql", "name": "test"}

    class _UnknownDbConnection:
        def select_db(self, database):
            raise Exception("Unknown database 'new_db'")

    db.connection = _UnknownDbConnection()

    def fail_if_called(config):
        raise AssertionError("should not reconnect on a real SQL error")

    monkeypatch.setattr(db, "connect", fail_if_called)

    try:
        db.select_db("new_db")
        assert False, "expected the original exception to propagate"
    except Exception as ex:
        assert "Unknown database" in str(ex)


if __name__ == "__main__":
    import types

    class _MonkeyPatch:
        def setattr(self, obj, name, value):
            setattr(obj, name, value)

    mp = _MonkeyPatch()
    test_select_db_reconnects_when_connection_is_dead(mp)
    test_select_db_reuses_live_connection_without_reconnecting(mp)
    test_select_db_propagates_non_connection_errors(mp)
    print("ok")
