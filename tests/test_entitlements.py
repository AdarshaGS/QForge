from services import entitlements as entitlements_module
from services.entitlements import Edition, Entitlements, Feature, Limit


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(entitlements_module, "_CACHE_FILE", str(tmp_path / "cache.json"))
    return Entitlements()


def _as_free(monkeypatch):
    monkeypatch.setattr(entitlements_module.license_manager, "current_edition", lambda: "free")


def _as_pro(monkeypatch):
    monkeypatch.setattr(entitlements_module.license_manager, "current_edition", lambda: "pro")


def test_free_bundled_defaults(tmp_path, monkeypatch):
    _as_free(monkeypatch)
    ent = _fresh(tmp_path, monkeypatch)

    assert ent.edition() is Edition.FREE
    assert ent.limit(Limit.MAX_CONNECTIONS) == 5
    assert ent.limit(Limit.MAX_QUERY_TABS) == 5
    assert ent.limit(Limit.SAVED_QUERIES) is None  # unlimited on Free too
    assert ent.limit(Limit.QUERY_HISTORY) == 20
    assert ent.limit(Limit.ER_DIAGRAM_TABLES) == 10
    assert ent.is_enabled(Feature.SCHEMA_COMPARE) is False
    assert ent.is_enabled(Feature.ADVANCED_ERD) is False


def test_pro_bundled_defaults(tmp_path, monkeypatch):
    _as_pro(monkeypatch)
    ent = _fresh(tmp_path, monkeypatch)

    assert ent.edition() is Edition.PRO
    assert ent.limit(Limit.MAX_CONNECTIONS) is None
    assert ent.limit(Limit.MAX_QUERY_TABS) is None
    assert ent.limit(Limit.SAVED_QUERIES) is None
    assert ent.limit(Limit.ER_DIAGRAM_TABLES) is None
    # Storage sanity cap, not a monetization limit — stays capped on Pro too.
    assert ent.limit(Limit.QUERY_HISTORY) == 100
    assert ent.is_enabled(Feature.SCHEMA_COMPARE) is True
    assert ent.is_enabled(Feature.ADVANCED_ERD) is True


def test_apply_remote_config_overrides_only_provided_keys(tmp_path, monkeypatch):
    _as_free(monkeypatch)
    ent = _fresh(tmp_path, monkeypatch)

    ent.apply_remote_config({"free_limits": {"max_connections": 2}, "price_label": "$99/year"})

    assert ent.limit(Limit.MAX_CONNECTIONS) == 2
    assert ent.price_label() == "$99/year"
    # Untouched values keep their bundled defaults.
    assert ent.limit(Limit.SAVED_QUERIES) is None
    assert ent.pricing_url() == "https://qforge-licensing-production.up.railway.app/#pricing"


def test_apply_remote_config_persists_across_a_fresh_instance(tmp_path, monkeypatch):
    _as_free(monkeypatch)
    ent = _fresh(tmp_path, monkeypatch)
    ent.apply_remote_config({"free_limits": {"max_connections": 2}})

    reloaded = _fresh(tmp_path, monkeypatch)
    assert reloaded.limit(Limit.MAX_CONNECTIONS) == 2


def test_apply_remote_config_with_malformed_payload_is_a_noop(tmp_path, monkeypatch):
    _as_free(monkeypatch)
    ent = _fresh(tmp_path, monkeypatch)

    ent.apply_remote_config({"free_limits": "not-a-dict", "pro_only_features": "nope"})

    assert ent.limit(Limit.MAX_CONNECTIONS) == 5
    assert ent.is_enabled(Feature.SCHEMA_COMPARE) is False


def test_apply_remote_config_can_explicitly_free_a_pro_only_feature(tmp_path, monkeypatch):
    _as_free(monkeypatch)
    ent = _fresh(tmp_path, monkeypatch)

    ent.apply_remote_config({"pro_only_features": []})

    assert ent.is_enabled(Feature.SCHEMA_COMPARE) is True
    assert ent.is_enabled(Feature.ADVANCED_ERD) is True
