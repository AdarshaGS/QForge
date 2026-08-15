"""Persist per-table "pinned" and "favorite" flags for the schema tree's
Organization context-menu group (issue #142).

Keyed the same way as utils/schema_cache.py (connection id + database)
since table names alone collide across different databases/connections.
"""
import json
import os

from utils.logger import get_logger
from utils.paths import app_data_dir

logger = get_logger()

_FILE = os.path.join(app_data_dir(), "table_organization.json")


def _key(connection_id: str, database: str) -> str:
    return f"{connection_id}:{database or ''}"


def _read_all() -> dict:
    if os.path.exists(_FILE):
        with open(_FILE) as f:
            return json.load(f)
    return {}


def _write_all(data: dict):
    os.makedirs(os.path.dirname(_FILE), exist_ok=True)
    with open(_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _entry(data: dict, connection_id: str, database: str) -> dict:
    return data.setdefault(_key(connection_id, database), {"pinned": [], "favorites": []})


def get_pinned(connection_id: str, database: str) -> set:
    if not connection_id:
        return set()
    try:
        return set(_read_all().get(_key(connection_id, database), {}).get("pinned", []))
    except Exception as ex:
        logger.warning(f"Failed to read table organization from {_FILE}: {ex}")
        return set()


def get_favorites(connection_id: str, database: str) -> set:
    if not connection_id:
        return set()
    try:
        return set(_read_all().get(_key(connection_id, database), {}).get("favorites", []))
    except Exception as ex:
        logger.warning(f"Failed to read table organization from {_FILE}: {ex}")
        return set()


def toggle_pinned(connection_id: str, database: str, table_name: str) -> bool:
    """Flip *table_name*'s pinned flag and persist it. Returns the new state."""
    if not connection_id:
        return False
    try:
        data = _read_all()
    except Exception:
        data = {}
    entry = _entry(data, connection_id, database)
    if table_name in entry["pinned"]:
        entry["pinned"].remove(table_name)
        result = False
    else:
        entry["pinned"].append(table_name)
        result = True
    try:
        _write_all(data)
    except Exception as ex:
        logger.warning(f"Failed to save table organization to {_FILE}: {ex}")
    return result


def toggle_favorite(connection_id: str, database: str, table_name: str) -> bool:
    """Flip *table_name*'s favorite flag and persist it. Returns the new state."""
    if not connection_id:
        return False
    try:
        data = _read_all()
    except Exception:
        data = {}
    entry = _entry(data, connection_id, database)
    if table_name in entry["favorites"]:
        entry["favorites"].remove(table_name)
        result = False
    else:
        entry["favorites"].append(table_name)
        result = True
    try:
        _write_all(data)
    except Exception as ex:
        logger.warning(f"Failed to save table organization to {_FILE}: {ex}")
    return result
