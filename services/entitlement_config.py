"""services/entitlement_config.py — the single place every Free/Pro tunable
value lives: per-edition limits, which features are Pro-gated, and the
upgrade price/URL/benefits copy.

Two sources feed the effective values at runtime (see services/entitlements.py):
1. The bundled defaults below — always available, fully offline.
2. An optional remote override, fetched once at startup
   (utils/entitlement_fetcher.py) from ENTITLEMENT_CONFIG_URL and cached
   locally for the next offline launch. Editing that hosted JSON file
   changes a limit or the price for every installed copy without shipping
   a new release.

Both paths use the exact same shape (plain string keys, matching Limit/
Feature .value) and the same parse_remote_override() validator below, so
there is exactly one schema to keep in sync, not two.
"""

# Raw JSON file in the QForge repo itself — no separate hosting needed.
# Reuses the same GITHUB_USER/GITHUB_REPO identity utils/updater.py already
# has. Point this at a Gist or another host instead if that's ever preferred;
# it's the only line that needs to change.
ENTITLEMENT_CONFIG_URL = (
    "https://raw.githubusercontent.com/AdarshaGS/QForge/master/entitlements-config.json"
)

# ── Bundled defaults (offline fallback) ─────────────────────────────────────

FREE_LIMITS = {
    "max_connections": 5,
    "max_query_tabs": 5,
    "saved_queries": None,  # unlimited on Free too — not a distinguishing limit
    "query_history": 20,
    "er_diagram_tables": 10,
}

PRO_LIMITS = {
    # None == unlimited.
    "max_connections": None,
    "max_query_tabs": None,
    "saved_queries": None,
    "er_diagram_tables": None,
    # Not a monetization limit — just storage sanity, matches the app's
    # original hardcoded QueryHistory.MAX_HISTORY.
    "query_history": 100,
}

PRO_ONLY_FEATURES = {"schema_compare", "advanced_erd"}

PRICING_URL = "https://github.com/AdarshaGS/QForge"  # placeholder — see GitHub issue #145
PRICE_LABEL = "$49/lifetime"  # placeholder — see GitHub issue #145

PRO_BENEFITS = [
    "Unlimited connections & query tabs",
    "Unlimited saved queries",
    "Full query history",
    "Full ER diagrams",
    "Schema Compare",
    "Priority support",
]

_KNOWN_LIMIT_KEYS = set(FREE_LIMITS)
_KNOWN_FEATURE_KEYS = set(PRO_ONLY_FEATURES)


def _clean_limits_section(value) -> dict:
    if not isinstance(value, dict):
        return {}
    cleaned = {}
    for key, val in value.items():
        if key in _KNOWN_LIMIT_KEYS and (val is None or (isinstance(val, int) and not isinstance(val, bool) and val >= 0)):
            cleaned[key] = val
    return cleaned


def parse_remote_override(raw: dict) -> dict:
    """Validate an untrusted remote JSON payload against the shape of the
    bundled defaults above. Per key: unknown keys are ignored, wrong types
    are ignored, only recognized limit/feature names are accepted.

    Returns a dict containing only the sub-values that passed validation —
    callers merge this over the current effective config key-by-key, so a
    partial or malformed remote file degrades individual values rather than
    wiping everything out. Never raises.
    """
    if not isinstance(raw, dict):
        return {}

    result = {}

    free_limits = _clean_limits_section(raw.get("free_limits"))
    if free_limits:
        result["free_limits"] = free_limits

    pro_limits = _clean_limits_section(raw.get("pro_limits"))
    if pro_limits:
        result["pro_limits"] = pro_limits

    pro_only_features = raw.get("pro_only_features")
    if isinstance(pro_only_features, list):
        cleaned_features = [f for f in pro_only_features if isinstance(f, str) and f in _KNOWN_FEATURE_KEYS]
        # An explicit empty list is meaningful (vendor made everything free) —
        # only skip this key when the source list had nothing valid AND was
        # non-empty (i.e. garbage), to distinguish "explicitly none" from
        # "not provided".
        if cleaned_features or not pro_only_features:
            result["pro_only_features"] = cleaned_features

    pricing_url = raw.get("pricing_url")
    if isinstance(pricing_url, str) and pricing_url.startswith("https://"):
        result["pricing_url"] = pricing_url

    price_label = raw.get("price_label")
    if isinstance(price_label, str) and price_label.strip():
        result["price_label"] = price_label.strip()

    pro_benefits = raw.get("pro_benefits")
    if isinstance(pro_benefits, list) and pro_benefits and all(isinstance(b, str) and b.strip() for b in pro_benefits):
        result["pro_benefits"] = pro_benefits

    return result
