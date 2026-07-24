from utils import environment


def test_normalize_accepts_known_values():
    for env in environment.ENVIRONMENTS:
        assert environment.normalize(env) == env


def test_normalize_defaults_unknown_string_to_unclassified():
    assert environment.normalize("nonsense") == environment.UNCLASSIFIED


def test_normalize_defaults_none_to_unclassified():
    assert environment.normalize(None) == environment.UNCLASSIFIED


def test_normalize_defaults_empty_string_to_unclassified():
    assert environment.normalize("") == environment.UNCLASSIFIED


def test_normalize_is_case_and_whitespace_insensitive():
    assert environment.normalize("  Production  ") == environment.PRODUCTION
    assert environment.normalize("STAGING") == environment.STAGING


def test_every_environment_has_combo_and_badge_labels():
    for env in environment.ENVIRONMENTS:
        assert env in environment.COMBO_LABELS
        assert env in environment.BADGE_LABELS


def test_default_environment_is_unclassified():
    assert environment.DEFAULT_ENVIRONMENT == environment.UNCLASSIFIED
