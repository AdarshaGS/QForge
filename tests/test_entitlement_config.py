from services.entitlement_config import parse_remote_override


def test_full_valid_payload_passes_through():
    raw = {
        "free_limits": {"max_connections": 3, "saved_queries": None},
        "pro_limits": {"max_connections": None},
        "pro_only_features": ["schema_compare"],
        "pricing_url": "https://example.com/pricing",
        "price_label": "$9/month",
        "pro_benefits": ["Thing one", "Thing two"],
    }
    cleaned = parse_remote_override(raw)
    assert cleaned == raw


def test_partial_payload_only_returns_valid_keys():
    cleaned = parse_remote_override({"price_label": "$99/year"})
    assert cleaned == {"price_label": "$99/year"}


def test_non_dict_input_returns_empty():
    assert parse_remote_override(None) == {}
    assert parse_remote_override([1, 2, 3]) == {}
    assert parse_remote_override("nope") == {}


def test_unknown_limit_keys_are_dropped():
    cleaned = parse_remote_override({"free_limits": {"max_connections": 3, "made_up_key": 1}})
    assert cleaned == {"free_limits": {"max_connections": 3}}


def test_wrong_type_limit_values_are_dropped():
    cleaned = parse_remote_override({
        "free_limits": {"max_connections": "five", "saved_queries": -1, "query_history": 20}
    })
    assert cleaned == {"free_limits": {"query_history": 20}}


def test_bool_is_not_accepted_as_a_limit_value():
    # isinstance(True, int) is True in Python — must be explicitly excluded.
    cleaned = parse_remote_override({"free_limits": {"max_connections": True}})
    assert cleaned == {}


def test_unknown_feature_names_are_dropped_but_known_ones_kept():
    cleaned = parse_remote_override({"pro_only_features": ["schema_compare", "made_up_feature"]})
    assert cleaned == {"pro_only_features": ["schema_compare"]}


def test_explicit_empty_feature_list_is_preserved():
    # An intentional "nothing is Pro-only anymore" must not be confused
    # with "not provided".
    cleaned = parse_remote_override({"pro_only_features": []})
    assert cleaned == {"pro_only_features": []}


def test_garbage_feature_list_with_nothing_valid_is_dropped():
    cleaned = parse_remote_override({"pro_only_features": ["nonsense", 42, None]})
    assert cleaned == {}


def test_non_https_pricing_url_is_dropped():
    cleaned = parse_remote_override({"pricing_url": "http://insecure.example.com"})
    assert cleaned == {}


def test_blank_price_label_is_dropped():
    cleaned = parse_remote_override({"price_label": "   "})
    assert cleaned == {}


def test_pro_benefits_must_be_non_empty_list_of_strings():
    assert parse_remote_override({"pro_benefits": []}) == {}
    assert parse_remote_override({"pro_benefits": ["ok", 5]}) == {}
    assert parse_remote_override({"pro_benefits": ["ok", "also ok"]}) == {"pro_benefits": ["ok", "also ok"]}


def test_unknown_top_level_keys_are_ignored():
    cleaned = parse_remote_override({"_comment": "docs", "price_label": "$1"})
    assert cleaned == {"price_label": "$1"}
