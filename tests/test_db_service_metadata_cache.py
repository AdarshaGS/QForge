"""Regression tests for issue #256's per-connection metadata cache.

get_columns()/get_foreign_keys()/get_primary_keys()/get_indexes() each used
to do a fresh round-trip on every call, even for a table just queried a
moment ago. No live database needed here — the underlying _fetch_* methods
are monkeypatched with call counters so these tests exercise only the
caching layer (_cached_metadata / clear_metadata_cache), not the SQL.
"""
from services.db_service import DbService


def _db():
    db = DbService()
    db.db_type = "mysql"
    db._config = {"type": "mysql", "name": "test", "database": "shop", "id": "conn-1"}
    db.connection = object()  # truthy — enough for the code paths under test
    return db


def _counting(monkeypatch, db, attr_name, return_value):
    calls = {"n": 0}

    def fake(table_name):
        calls["n"] += 1
        return return_value

    monkeypatch.setattr(db, attr_name, fake)
    return calls


def test_get_columns_hits_db_once_for_repeated_calls(monkeypatch):
    db = _db()
    calls = _counting(monkeypatch, db, "_fetch_columns", [{"Field": "id"}])

    assert db.get_columns("users") == [{"Field": "id"}]
    assert db.get_columns("users") == [{"Field": "id"}]
    assert calls["n"] == 1


def test_get_foreign_keys_and_get_primary_keys_and_get_indexes_are_cached(monkeypatch):
    db = _db()
    fk_calls = _counting(monkeypatch, db, "_fetch_foreign_keys", [{"column": "x"}])
    pk_calls = _counting(monkeypatch, db, "_fetch_primary_keys", ["id"])
    idx_calls = _counting(monkeypatch, db, "_fetch_indexes", [{"name": "PRIMARY"}])

    for _ in range(3):
        db.get_foreign_keys("orders")
        db.get_primary_keys("orders")
        db.get_indexes("orders")

    assert fk_calls["n"] == 1
    assert pk_calls["n"] == 1
    assert idx_calls["n"] == 1


def test_different_tables_are_cached_independently(monkeypatch):
    db = _db()
    calls = _counting(monkeypatch, db, "_fetch_columns", [])

    db.get_columns("users")
    db.get_columns("orders")
    db.get_columns("users")

    assert calls["n"] == 2  # one per distinct table, not per call


def test_clear_metadata_cache_forces_a_refetch(monkeypatch):
    db = _db()
    calls = _counting(monkeypatch, db, "_fetch_columns", [])

    db.get_columns("users")
    db.clear_metadata_cache()
    db.get_columns("users")

    assert calls["n"] == 2


def test_select_db_success_clears_the_cache(monkeypatch):
    db = _db()

    class _LiveConnection:
        def select_db(self, database):
            pass

    db.connection = _LiveConnection()
    db._metadata_cache[("columns", "users")] = ["stale"]

    db.select_db("new_db")

    assert db._metadata_cache == {}


def test_set_schema_clears_the_cache(monkeypatch):
    db = _db()
    db.db_type = "postgresql"

    class _Cursor:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql): pass

    class _Connection:
        def cursor(self): return _Cursor()

    db.connection = _Connection()
    db._metadata_cache[("columns", "users")] = ["stale"]

    db.set_schema("reporting")

    assert db._metadata_cache == {}


def test_ddl_statement_clears_the_cache(monkeypatch):
    db = _db()
    monkeypatch.setattr("services.db_service.schema_cache.invalidate", lambda *a: None)
    db._metadata_cache[("columns", "users")] = ["stale"]

    db._invalidate_schema_cache_if_ddl("ALTER TABLE users ADD COLUMN nickname TEXT")

    assert db._metadata_cache == {}


def test_non_ddl_statement_does_not_clear_the_cache(monkeypatch):
    db = _db()
    monkeypatch.setattr("services.db_service.schema_cache.invalidate", lambda *a: None)
    db._metadata_cache[("columns", "users")] = ["cached"]

    db._invalidate_schema_cache_if_ddl("UPDATE users SET nickname = 'x' WHERE id = 1")

    assert db._metadata_cache == {("columns", "users"): ["cached"]}


def test_connect_clears_stale_cache_from_a_prior_connection(monkeypatch):
    db = _db()
    db._metadata_cache[("columns", "users")] = ["stale"]
    monkeypatch.setattr(db, "_connect_mysql", lambda config: setattr(db, "connection", object()))

    db.connect({"type": "mysql", "name": "test2", "database": "other"})

    assert db._metadata_cache == {}


if __name__ == "__main__":
    # Minimal stand-in supporting both object attrs and "module.path" strings,
    # mirroring pytest's monkeypatch just enough for this file's own tests.
    class MonkeyPatch:
        def __init__(self):
            self._undo = []

        def setattr(self, target, name, value):
            if isinstance(target, str):
                import importlib
                module_name, attr = target.rsplit(".", 1)
                mod = importlib.import_module(module_name)
                setattr(mod, attr, value)
            else:
                setattr(target, name, value)

    mp = MonkeyPatch()
    test_get_columns_hits_db_once_for_repeated_calls(mp)
    test_get_foreign_keys_and_get_primary_keys_and_get_indexes_are_cached(mp)
    test_different_tables_are_cached_independently(mp)
    test_clear_metadata_cache_forces_a_refetch(mp)
    test_select_db_success_clears_the_cache(mp)
    test_set_schema_clears_the_cache(mp)
    test_ddl_statement_clears_the_cache(mp)
    test_non_ddl_statement_does_not_clear_the_cache(mp)
    test_connect_clears_stale_cache_from_a_prior_connection(mp)
    print("ok")
