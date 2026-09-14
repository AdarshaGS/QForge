"""Regression test for issue #295: DbService.kill_current_query() was a
complete no-op for PostgreSQL — a lock-blocked query's Cancel button only
stopped the UI (the worker thread's cancel flag) from waiting on it, never
actually interrupted the query server-side, so the connection stayed
blocked underneath.

psycopg2's Connection.cancel() sends a real cancel request (libpq
PQcancel) over its own short-lived socket, using the connection's own
cancellation key — it's specifically designed to be called from another
thread while the connection is stuck inside a blocking query, which is
exactly the "genuinely blocked on a lock" scenario #295 describes.
"""
from services.db_service import DbService


def test_kill_current_query_calls_connection_cancel_for_postgres():
    db = DbService()
    db.db_type = "postgresql"

    calls = []
    db.connection = type("C", (), {"cancel": lambda self: calls.append("cancel")})()

    db.kill_current_query()

    assert calls == ["cancel"]


def test_kill_current_query_swallows_a_postgres_cancel_failure():
    db = DbService()
    db.db_type = "postgresql"

    def _raise():
        raise Exception("connection already closed")
    db.connection = type("C", (), {"cancel": lambda self: _raise()})()

    db.kill_current_query()  # must not raise


def test_kill_current_query_is_a_noop_without_a_live_connection():
    db = DbService()
    db.db_type = "postgresql"
    db.connection = None

    db.kill_current_query()  # must not raise, must not touch anything


def test_kill_current_query_still_dispatches_to_mysql_kill_query(monkeypatch):
    db = DbService()
    db.db_type = "mysql"
    db._config = {"host": "127.0.0.1", "port": 3306, "user": "u", "password": "p", "database": "d"}

    executed = []

    class _Cursor:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql): executed.append(sql)

    class _KillConn:
        def cursor(self): return _Cursor()
        def close(self): pass

    import pymysql
    monkeypatch.setattr(pymysql, "connect", lambda **kwargs: _KillConn())

    db.connection = type("C", (), {"thread_id": lambda self: 42})()
    db.kill_current_query()

    assert executed == ["KILL QUERY 42"]


def test_kill_current_query_is_a_noop_for_unsupported_dialects():
    db = DbService()
    db.db_type = "sqlite"
    calls = []
    db.connection = type("C", (), {"cancel": lambda self: calls.append("cancel")})()

    db.kill_current_query()

    assert calls == []
