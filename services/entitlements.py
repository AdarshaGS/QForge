"""services/entitlements.py — the centralized Free/Pro entitlement model
(issue #82). Every part of the app that needs to know "how many of X am I
allowed" or "is feature Y available" asks this module — nothing else in
the codebase should hardcode a Free/Pro check.

Pure logic, no Qt imports: the UI layer (ui/upgrade_dialog.py) owns what
happens when a gate is hit, keeping this module UI-free like the rest of
services/.

Effective values start from services/entitlement_config.py's bundled
defaults, then a cached remote override (if one was ever successfully
fetched — see utils/entitlement_fetcher.py) is layered on top so a value
change survives an offline relaunch without needing a new fetch.
"""
import json
import os
from enum import Enum

from services import entitlement_config as config
from services.license_manager import license_manager
from utils.logger import get_logger
from utils.paths import app_data_dir

logger = get_logger()

_CACHE_FILE = os.path.join(app_data_dir(), "entitlement_config_cache.json")


class Edition(Enum):
    FREE = "free"
    PRO = "pro"


class Limit(Enum):
    MAX_CONNECTIONS = "max_connections"
    MAX_QUERY_TABS = "max_query_tabs"
    SAVED_QUERIES = "saved_queries"
    QUERY_HISTORY = "query_history"
    ER_DIAGRAM_TABLES = "er_diagram_tables"


class Feature(Enum):
    SCHEMA_COMPARE = "schema_compare"
    ADVANCED_ERD = "advanced_erd"
    DATA_COMPARE = "data_compare"
    IMPACT_ANALYSIS = "impact_analysis"


class Entitlements:
    def __init__(self):
        self._free_limits = dict(config.FREE_LIMITS)
        self._pro_limits = dict(config.PRO_LIMITS)
        self._pro_only_features = set(config.PRO_ONLY_FEATURES)
        self._pricing_url = config.PRICING_URL
        self._price_label = config.PRICE_LABEL
        self._pro_benefits = list(config.PRO_BENEFITS)
        self._load_cached_override()

    # ─── Queries ────────────────────────────────────────────────────────────

    def edition(self) -> Edition:
        return Edition.PRO if license_manager.current_edition() == "pro" else Edition.FREE

    def limit(self, limit: Limit) -> int | None:
        """None means unlimited."""
        if config.ALL_FEATURES_FREE or self.edition() is Edition.PRO:
            table = self._pro_limits
        else:
            table = self._free_limits
        return table.get(limit.value)

    def is_enabled(self, feature: Feature) -> bool:
        if config.ALL_FEATURES_FREE or self.edition() is Edition.PRO:
            return True
        return feature.value not in self._pro_only_features

    def pricing_url(self) -> str:
        return self._pricing_url

    def price_label(self) -> str:
        return self._price_label

    def pro_benefits(self) -> list[str]:
        return list(self._pro_benefits)

    # ─── Remote override ────────────────────────────────────────────────────

    def apply_remote_config(self, raw: dict) -> None:
        """Called once per app run when utils/entitlement_fetcher.py
        successfully downloads ENTITLEMENT_CONFIG_URL. Validates via
        entitlement_config.parse_remote_override, merges per-key over the
        current effective values, and persists the result so the next
        offline launch keeps using it."""
        cleaned = config.parse_remote_override(raw)
        if not cleaned:
            return
        self._merge(cleaned)
        self._persist_cache()

    def _merge(self, cleaned: dict) -> None:
        if "free_limits" in cleaned:
            self._free_limits.update(cleaned["free_limits"])
        if "pro_limits" in cleaned:
            self._pro_limits.update(cleaned["pro_limits"])
        if "pro_only_features" in cleaned:
            self._pro_only_features = set(cleaned["pro_only_features"])
        if "pricing_url" in cleaned:
            self._pricing_url = cleaned["pricing_url"]
        if "price_label" in cleaned:
            self._price_label = cleaned["price_label"]
        if "pro_benefits" in cleaned:
            self._pro_benefits = cleaned["pro_benefits"]

    def _persist_cache(self) -> None:
        snapshot = {
            "free_limits": self._free_limits,
            "pro_limits": self._pro_limits,
            "pro_only_features": sorted(self._pro_only_features),
            "pricing_url": self._pricing_url,
            "price_label": self._price_label,
            "pro_benefits": self._pro_benefits,
        }
        try:
            os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
            with open(_CACHE_FILE, "w") as f:
                json.dump(snapshot, f, indent=2)
        except OSError as ex:
            logger.warning(f"Failed to persist entitlement config cache: {ex}")

    def _load_cached_override(self) -> None:
        if not os.path.exists(_CACHE_FILE):
            return
        try:
            with open(_CACHE_FILE) as f:
                cached = json.load(f)
        except Exception as ex:
            logger.warning(f"Failed to read entitlement config cache: {ex}")
            return
        # Re-validate on load too (defense in depth — the cache file is
        # locally writable and this app may run across major-version
        # upgrades that changed the schema).
        cleaned = config.parse_remote_override(cached)
        if cleaned:
            self._merge(cleaned)


entitlements = Entitlements()
