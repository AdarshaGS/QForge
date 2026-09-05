"""Tests for services/preferences.py (issue #251)."""
from services import preferences


def test_get_missing_key_returns_default(tmp_path, monkeypatch):
    monkeypatch.setattr(preferences, "_FILE", str(tmp_path / "preferences.json"))
    assert preferences.get("nope", "fallback") == "fallback"
    assert preferences.get("nope") is None


def test_set_then_get_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(preferences, "_FILE", str(tmp_path / "preferences.json"))
    preferences.set("table_page_size", 500)
    assert preferences.get("table_page_size") == 500


def test_set_persists_across_separate_reads(tmp_path, monkeypatch):
    """Simulates separate app sessions: each get()/set() call re-reads the
    file rather than caching in memory."""
    file_path = str(tmp_path / "preferences.json")
    monkeypatch.setattr(preferences, "_FILE", file_path)
    preferences.set("a", 1)
    monkeypatch.setattr(preferences, "_FILE", file_path)  # "restart"
    assert preferences.get("a") == 1


def test_set_preserves_other_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(preferences, "_FILE", str(tmp_path / "preferences.json"))
    preferences.set("a", 1)
    preferences.set("b", 2)
    assert preferences.get("a") == 1
    assert preferences.get("b") == 2


def test_corrupt_file_is_swallowed_not_raised(tmp_path, monkeypatch):
    bad_file = tmp_path / "preferences.json"
    bad_file.write_text("{not json")
    monkeypatch.setattr(preferences, "_FILE", str(bad_file))
    assert preferences.get("anything", "fallback") == "fallback"
