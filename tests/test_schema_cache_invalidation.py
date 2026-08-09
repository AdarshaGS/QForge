"""Issue #72: a successful schema-changing statement must drop the on-disk
schema cache entry for its connection/database, regardless of which code
path ran it — DbService.execute_update() is the single chokepoint every
write (raw SQL, the Create/Alter Table dialogs, CSV import, ...) funnels
through."""
from services.db_service import DbService
from utils import schema_cache


def _connected_db(tmp_path, monkeypatch, conn_id="conn-1"):
    monkeypatch.setattr(schema_cache, "_FILE", str(tmp_path / "schema_cache.json"))
    config = {"type": "sqlite", "name": "test", "id": conn_id,
              "database": str(tmp_path / "test.db")}
    db = DbService()
    db.connect(config)
    schema_cache.save(conn_id, config["database"], {"tables": ["placeholder"]})
    return db, config


def test_create_table_invalidates_cache(tmp_path, monkeypatch):
    db, config = _connected_db(tmp_path, monkeypatch)
    db.execute_update("CREATE TABLE widgets (id INTEGER PRIMARY KEY)")
    assert schema_cache.load(config["id"], config["database"]) is None


def test_alter_table_invalidates_cache(tmp_path, monkeypatch):
    db, config = _connected_db(tmp_path, monkeypatch)
    db.execute_update("CREATE TABLE widgets (id INTEGER PRIMARY KEY)")
    schema_cache.save(config["id"], config["database"], {"tables": ["widgets"]})

    db.execute_update("ALTER TABLE widgets ADD COLUMN name TEXT")

    assert schema_cache.load(config["id"], config["database"]) is None


def test_drop_table_invalidates_cache(tmp_path, monkeypatch):
    db, config = _connected_db(tmp_path, monkeypatch)
    db.execute_update("CREATE TABLE widgets (id INTEGER PRIMARY KEY)")
    schema_cache.save(config["id"], config["database"], {"tables": ["widgets"]})

    db.execute_update("DROP TABLE widgets")

    assert schema_cache.load(config["id"], config["database"]) is None


def test_row_level_writes_do_not_invalidate_cache(tmp_path, monkeypatch):
    db, config = _connected_db(tmp_path, monkeypatch)
    db.execute_update("CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT)")
    schema_cache.save(config["id"], config["database"], {"tables": ["widgets"]})

    db.execute_update("INSERT INTO widgets (id, name) VALUES (1, 'a')")
    db.execute_update("UPDATE widgets SET name = 'b' WHERE id = 1")
    db.execute_update("DELETE FROM widgets WHERE id = 1")

    assert schema_cache.load(config["id"], config["database"])["tables"] == ["widgets"]


def test_connection_without_id_never_touches_cache_file(tmp_path, monkeypatch):
    cache_file = tmp_path / "schema_cache.json"
    monkeypatch.setattr(schema_cache, "_FILE", str(cache_file))
    db = DbService()
    db.connect({"type": "sqlite", "name": "test", "database": str(tmp_path / "test.db")})

    db.execute_update("CREATE TABLE widgets (id INTEGER PRIMARY KEY)")

    assert not cache_file.exists()
