"""Cache database schema metadata locally (issue #71) so a previously
visited connection can populate its schema browser and SQL autocomplete
before any network round-trip, then get silently refreshed in the
background — plus staleness/invalidation on top (issue #72).

Format: { "<connection_id>:<database>": {tables, columns, views, functions,
server_version, dbs, _cached_at} }. Deliberately excludes row data, query
results, and credentials — those never appear in the cached snapshot subset
below. The live database is always the source of truth; this cache only
ever feeds the UI's first paint, never a query-execution decision.
"""
import json
import os
import time

from utils.logger import get_logger
from utils.paths import app_data_dir
from utils import perf_metrics

logger = get_logger()

_FILE = os.path.join(app_data_dir(), "schema_cache.json")

# Structural metadata only — never row data, query results, or credentials.
_CACHED_KEYS = ("tables", "columns", "column_details", "foreign_keys",
                "views", "functions", "server_version", "dbs", "schemas")

# ponytail: fixed default rather than a per-profile setting — add a UI knob
# if users ever ask to tune it. Only affects the "stale" UI hint below; a
# live background refresh already runs on every schema load regardless.
FRESHNESS_SECONDS = 15 * 60


def _key(connection_id: str, database: str) -> str:
    return f"{connection_id}:{database or ''}"


def _read_all() -> dict:
    if os.path.exists(_FILE):
        with open(_FILE) as f:
            return json.load(f)
    return {}


def load(connection_id: str, database: str) -> dict | None:
    if not connection_id:
        return None
    try:
        entry = _read_all().get(_key(connection_id, database))
        perf_metrics.counter_inc("schema_cache", "hit" if entry is not None else "miss")
        return entry
    except Exception as ex:
        logger.warning(f"Failed to load schema cache from {_FILE}: {ex}")
    return None


def is_stale(entry: dict) -> bool:
    return (time.time() - entry.get("_cached_at", 0)) > FRESHNESS_SECONDS


def save(connection_id: str, database: str, snapshot: dict):
    if not connection_id:
        return
    try:
        try:
            data = _read_all()
        except Exception:
            data = {}  # corrupt file — overwrite it rather than fail
        entry = {k: snapshot[k] for k in _CACHED_KEYS if k in snapshot}
        entry["_cached_at"] = time.time()
        data[_key(connection_id, database)] = entry
        os.makedirs(os.path.dirname(_FILE), exist_ok=True)
        with open(_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as ex:
        logger.warning(f"Failed to save schema cache to {_FILE}: {ex}")


def invalidate(connection_id: str, database: str):
    """Drop a cached entry, e.g. right after a schema-changing statement
    (issue #72) succeeds, so the next load shows live data instead of the
    pre-change structure."""
    if not connection_id:
        return
    try:
        data = _read_all()
        if data.pop(_key(connection_id, database), None) is not None:
            with open(_FILE, "w") as f:
                json.dump(data, f, indent=2)
    except Exception as ex:
        logger.warning(f"Failed to invalidate schema cache in {_FILE}: {ex}")
