from services import saved_queries as saved_queries_module
from services.saved_queries import SavedQueries


def _store(tmp_path, monkeypatch):
    monkeypatch.setattr(saved_queries_module, "_FILE", str(tmp_path / "saved_queries.json"))
    return SavedQueries()


def test_add_persists_and_reloads(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    store.add("Active Users", "SELECT * FROM users WHERE active = 1;")

    reloaded = _store(tmp_path, monkeypatch)
    assert len(reloaded.queries) == 1
    assert reloaded.queries[0]["name"] == "Active Users"
    assert reloaded.queries[0]["query"] == "SELECT * FROM users WHERE active = 1;"
    assert reloaded.queries[0]["favorite"] is False


def test_add_defaults_untitled_name_when_blank(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    entry = store.add("   ", "SELECT 1;")
    assert entry["name"] == "Untitled Query"


def test_toggle_favorite_flips_and_persists(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    entry = store.add("Top Customers", "SELECT * FROM customers;")

    assert store.toggle_favorite(entry["id"]) is True
    assert store.get(entry["id"])["favorite"] is True

    assert store.toggle_favorite(entry["id"]) is False
    assert store.get(entry["id"])["favorite"] is False


def test_toggle_favorite_unknown_id_is_a_no_op(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    assert store.toggle_favorite("does-not-exist") is False


def test_get_favorites_and_get_saved_partition_the_list(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    a = store.add("A", "SELECT 1;")
    store.add("B", "SELECT 2;")
    store.toggle_favorite(a["id"])

    assert [q["name"] for q in store.get_favorites()] == ["A"]
    assert [q["name"] for q in store.get_saved()] == ["B"]


def test_update_changes_fields_and_persists(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    entry = store.add("Old Name", "SELECT 1;")

    store.update(entry["id"], name="New Name")

    reloaded = _store(tmp_path, monkeypatch)
    assert reloaded.queries[0]["name"] == "New Name"


def test_update_unknown_id_returns_none(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    assert store.update("does-not-exist", name="X") is None


def test_delete_removes_entry(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    entry = store.add("Doomed", "SELECT 1;")
    store.add("Survivor", "SELECT 2;")

    store.delete(entry["id"])

    reloaded = _store(tmp_path, monkeypatch)
    assert [q["name"] for q in reloaded.queries] == ["Survivor"]


def test_search_matches_name_or_query_case_insensitively(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    store.add("Monthly Sales Summary", "SELECT * FROM orders;")
    store.add("Active Users", "SELECT * FROM users WHERE active = 1;")

    by_name = store.search("sales")
    assert [q["name"] for q in by_name] == ["Monthly Sales Summary"]

    by_sql = store.search("USERS")
    assert [q["name"] for q in by_sql] == ["Active Users"]


def test_load_missing_file_starts_empty(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    assert store.queries == []
