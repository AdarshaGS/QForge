"""Issue #72: a successful schema-changing statement must drop the on-disk
schema cache entry for its connection/database, regardless of which code
path ran it — DbService.execute_update() is the single chokepoint every
write (raw SQL, the Create/Alter Table dialogs, CSV import, ...) funnels
through.

Live-Postgres integration (skipped if no local server, mirrors
tests/test_db_service_postgresql.py's fixture)."""
import uuid

import psycopg2
import pytest

from services.db_service import DbService
from utils import schema_cache

_PG_HOST = "localhost"
_PG_PORT = 5432
_PG_USER = "qforge_test"
_PG_PASSWORD = "qforge_test_pw"
_ADMIN_PARAMS = dict(host=_PG_HOST, port=_PG_PORT, user=_PG_USER, password=_PG_PASSWORD, database="postgres")


def _postgres_available() -> bool:
    try:
        conn = psycopg2.connect(connect_timeout=2, **_ADMIN_PARAMS)
        conn.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _postgres_available(),
    reason="No local Postgres reachable as qforge_test@localhost:5432 — see "
           "tests/test_db_service_postgresql.py's module docstring for setup.",
)


def _connected_db(tmp_path, monkeypatch, conn_id="conn-1"):
    monkeypatch.setattr(schema_cache, "_FILE", str(tmp_path / "schema_cache.json"))
    name = f"qforge_test_{uuid.uuid4().hex[:12]}"
    admin = psycopg2.connect(connect_timeout=5, **_ADMIN_PARAMS)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    admin.close()

    config = {
        "type": "postgresql", "name": "test", "id": conn_id, "host": _PG_HOST,
        "port": _PG_PORT, "user": _PG_USER, "password": _PG_PASSWORD, "database": name,
    }
    db = DbService()
    db.connect(config)
    schema_cache.save(conn_id, config["database"], {"tables": ["placeholder"]})
    return db, config


def _drop_db(config):
    admin = psycopg2.connect(connect_timeout=5, **_ADMIN_PARAMS)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()", (config["database"],))
        cur.execute(f'DROP DATABASE IF EXISTS "{config["database"]}"')
    admin.close()


def test_create_table_invalidates_cache(tmp_path, monkeypatch):
    db, config = _connected_db(tmp_path, monkeypatch)
    db.execute_update("CREATE TABLE widgets (id INTEGER PRIMARY KEY)")
    assert schema_cache.load(config["id"], config["database"]) is None
    db.disconnect()
    _drop_db(config)


def test_alter_table_invalidates_cache(tmp_path, monkeypatch):
    db, config = _connected_db(tmp_path, monkeypatch)
    db.execute_update("CREATE TABLE widgets (id INTEGER PRIMARY KEY)")
    schema_cache.save(config["id"], config["database"], {"tables": ["widgets"]})

    db.execute_update("ALTER TABLE widgets ADD COLUMN name TEXT")

    assert schema_cache.load(config["id"], config["database"]) is None
    db.disconnect()
    _drop_db(config)


def test_drop_table_invalidates_cache(tmp_path, monkeypatch):
    db, config = _connected_db(tmp_path, monkeypatch)
    db.execute_update("CREATE TABLE widgets (id INTEGER PRIMARY KEY)")
    schema_cache.save(config["id"], config["database"], {"tables": ["widgets"]})

    db.execute_update("DROP TABLE widgets")

    assert schema_cache.load(config["id"], config["database"]) is None
    db.disconnect()
    _drop_db(config)


def test_row_level_writes_do_not_invalidate_cache(tmp_path, monkeypatch):
    db, config = _connected_db(tmp_path, monkeypatch)
    db.execute_update("CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT)")
    schema_cache.save(config["id"], config["database"], {"tables": ["widgets"]})

    db.execute_update("INSERT INTO widgets (id, name) VALUES (1, 'a')")
    db.execute_update("UPDATE widgets SET name = 'b' WHERE id = 1")
    db.execute_update("DELETE FROM widgets WHERE id = 1")

    assert schema_cache.load(config["id"], config["database"])["tables"] == ["widgets"]
    db.disconnect()
    _drop_db(config)


def test_connection_without_id_never_touches_cache_file(tmp_path, monkeypatch):
    cache_file = tmp_path / "schema_cache.json"
    monkeypatch.setattr(schema_cache, "_FILE", str(cache_file))
    name = f"qforge_test_{uuid.uuid4().hex[:12]}"
    admin = psycopg2.connect(connect_timeout=5, **_ADMIN_PARAMS)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    admin.close()

    config = {
        "type": "postgresql", "name": "test", "host": _PG_HOST, "port": _PG_PORT,
        "user": _PG_USER, "password": _PG_PASSWORD, "database": name,
    }
    db = DbService()
    db.connect(config)

    db.execute_update("CREATE TABLE widgets (id INTEGER PRIMARY KEY)")

    assert not cache_file.exists()
    db.disconnect()
    _drop_db(config)
