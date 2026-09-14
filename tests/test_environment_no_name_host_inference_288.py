"""Regression tests for issue #288: verify prod/staging environment
detection doesn't mislabel connections based on name/host pattern-matching.

The issue speculated ("presumably") that environment classification works
by pattern-matching a connection's name or host string, and asked to
confirm it doesn't mislabel an ambiguous one (a personal dev DB named
"prod-clone", a genuine production host without "prod" in its name, an
internal hostname).

Reading utils/environment.py and every call site: there is no name/host
heuristic anywhere. A connection's environment is a fully explicit field
— a QComboBox in the connection dialog (ui/connection_dialog.py) that
defaults to UNCLASSIFIED and is only ever set by the user's own
selection. environment.normalize() does nothing but coerce whatever
string is already stored into one of the five known tiers (or
UNCLASSIFIED if it's missing/invalid/malformed) — it never looks at
"name", "host", or any other connection field. So the exact scenarios
the issue names can't happen: a "prod-clone"-named connection that the
user left at the default shows as Unclassified, not auto-detected as
dev/local; a genuine production host without "prod" in its name shows
whatever tier the user actually picked for it, including Production if
that's what they chose — the badge always matches the stored field, and
the name/host string never overrides it in either direction.

No production code change needed; these tests lock the no-heuristic
behavior in.
"""
from utils import environment


def test_normalize_ignores_name_and_only_reads_the_stored_environment_value():
    # normalize() takes the environment value directly — it has no access
    # to a connection dict's name/host at all, so it structurally cannot
    # infer anything from them. This exercises the exact ambiguous names
    # from the issue's own test-focus list.
    ambiguous_names_and_stored_values = [
        ("prod-clone", None),                       # personal dev DB, left unclassified
        ("prod-clone", environment.DEVELOPMENT),     # personal dev DB, explicitly marked dev
        ("Warehouse", environment.PRODUCTION),       # genuine prod host, no "prod" in the name
        ("internal-db-01", environment.PRODUCTION),  # internal-sounding hostname, actually prod
    ]
    for _name, stored_value in ambiguous_names_and_stored_values:
        assert environment.normalize(stored_value) == (stored_value or environment.UNCLASSIFIED)


def test_a_connection_dict_with_a_misleading_name_is_classified_by_its_stored_field_only():
    """Simulates what ConnectionPanel/main.py actually do: pull
    conn.get("environment") and normalize it — the name/host fields exist
    on the same dict but are never consulted."""
    connections = [
        {"name": "prod-clone", "host": "localhost", "environment": None},
        {"name": "prod-clone", "host": "localhost", "environment": "development"},
        {"name": "Warehouse", "host": "10.0.4.12", "environment": "production"},
        {"name": "internal-db-01", "host": "reports.internal", "environment": "production"},
        {"name": "Analytics", "host": "prod-db.internal", "environment": None},  # prod-shaped host, unset
    ]
    expected = [
        environment.UNCLASSIFIED,
        environment.DEVELOPMENT,
        environment.PRODUCTION,
        environment.PRODUCTION,
        environment.UNCLASSIFIED,  # the host string ("prod-db.internal") is never consulted
    ]
    actual = [environment.normalize(c.get("environment")) for c in connections]
    assert actual == expected


def test_new_connections_default_to_unclassified_not_inferred_from_anything():
    assert environment.DEFAULT_ENVIRONMENT == environment.UNCLASSIFIED
    # A brand-new connection dict (as ConnectionDialog.get_connection_data()
    # would build it before the user touches the Environment dropdown) has
    # no environment-inferring fields at all — normalize() must still land
    # on UNCLASSIFIED regardless of what a hypothetical name/host said.
    fresh_conn = {"name": "prod-primary", "host": "db.prod.internal"}
    assert environment.normalize(fresh_conn.get("environment")) == environment.UNCLASSIFIED
