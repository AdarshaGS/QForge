from utils import schema_cache


def test_save_then_load_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(schema_cache, "_FILE", str(tmp_path / "schema_cache.json"))
    snapshot = {"tables": ["widgets"], "columns": {"widgets": ["id"]},
                "views": [], "functions": [], "server_version": "8.0",
                "dbs": ["shop"]}

    schema_cache.save("conn-1", "shop", snapshot)

    cached = schema_cache.load("conn-1", "shop")
    assert {k: v for k, v in cached.items() if k != "_cached_at"} == snapshot
    assert "_cached_at" in cached


def test_load_misses_return_none(tmp_path, monkeypatch):
    monkeypatch.setattr(schema_cache, "_FILE", str(tmp_path / "schema_cache.json"))

    assert schema_cache.load("conn-1", "shop") is None


def test_different_database_same_connection_is_a_separate_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(schema_cache, "_FILE", str(tmp_path / "schema_cache.json"))
    schema_cache.save("conn-1", "shop", {"tables": ["widgets"]})
    schema_cache.save("conn-1", "warehouse", {"tables": ["pallets"]})

    assert schema_cache.load("conn-1", "shop")["tables"] == ["widgets"]
    assert schema_cache.load("conn-1", "warehouse")["tables"] == ["pallets"]


def test_save_never_persists_keys_outside_the_structural_whitelist(tmp_path, monkeypatch):
    monkeypatch.setattr(schema_cache, "_FILE", str(tmp_path / "schema_cache.json"))
    snapshot = {"tables": ["widgets"], "password": "hunter2", "rows": [(1, "x")]}

    schema_cache.save("conn-1", "shop", snapshot)

    cached = schema_cache.load("conn-1", "shop")
    assert "password" not in cached
    assert "rows" not in cached


def test_save_and_load_without_connection_id_are_no_ops(tmp_path, monkeypatch):
    cache_file = tmp_path / "schema_cache.json"
    monkeypatch.setattr(schema_cache, "_FILE", str(cache_file))

    schema_cache.save("", "shop", {"tables": ["widgets"]})

    assert not cache_file.exists()
    assert schema_cache.load("", "shop") is None


def test_corrupt_cache_file_is_swallowed_not_raised(tmp_path, monkeypatch):
    cache_file = tmp_path / "schema_cache.json"
    cache_file.write_text("not json")
    monkeypatch.setattr(schema_cache, "_FILE", str(cache_file))

    assert schema_cache.load("conn-1", "shop") is None
    schema_cache.save("conn-1", "shop", {"tables": []})  # must not raise, self-heals the file
    assert schema_cache.load("conn-1", "shop")["tables"] == []


def test_is_stale_uses_freshness_window(monkeypatch):
    monkeypatch.setattr(schema_cache, "FRESHNESS_SECONDS", 100)
    now = 1_000_000.0
    monkeypatch.setattr(schema_cache.time, "time", lambda: now)

    assert schema_cache.is_stale({"_cached_at": now - 50}) is False
    assert schema_cache.is_stale({"_cached_at": now - 200}) is True
    assert schema_cache.is_stale({}) is True  # no timestamp at all


def test_invalidate_removes_only_the_targeted_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(schema_cache, "_FILE", str(tmp_path / "schema_cache.json"))
    schema_cache.save("conn-1", "shop", {"tables": ["widgets"]})
    schema_cache.save("conn-1", "warehouse", {"tables": ["pallets"]})

    schema_cache.invalidate("conn-1", "shop")

    assert schema_cache.load("conn-1", "shop") is None
    assert schema_cache.load("conn-1", "warehouse")["tables"] == ["pallets"]


def test_invalidate_missing_entry_and_missing_id_are_no_ops(tmp_path, monkeypatch):
    cache_file = tmp_path / "schema_cache.json"
    monkeypatch.setattr(schema_cache, "_FILE", str(cache_file))

    schema_cache.invalidate("conn-1", "shop")  # no file yet — must not raise
    schema_cache.invalidate("", "shop")        # no id — must not raise
