"""Small persisted key/value store for app-wide user preferences (issue
#251) — QForge had no settings/preferences infrastructure before this.
Follows the same flat-JSON-under-app_data_dir() pattern already used for
other local state (utils/onboarding.py, services/table_organization.py)
rather than introducing QSettings or a new storage format. Deliberately
generic (get/set by string key) so the next preference — e.g. the SQL
results grid's own page size, ui/sql_tab.py:118 — doesn't need a new file.
"""
import json
import os

from utils.logger import get_logger
from utils.paths import app_data_dir

logger = get_logger()

_FILE = os.path.join(app_data_dir(), "preferences.json")


def _read() -> dict:
    try:
        if os.path.exists(_FILE):
            with open(_FILE) as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
    except Exception as ex:
        logger.warning(f"Failed to load preferences from {_FILE}: {ex}")
    return {}


def get(key: str, default=None):
    return _read().get(key, default)


def set(key: str, value):
    data = _read()
    data[key] = value
    try:
        os.makedirs(os.path.dirname(_FILE), exist_ok=True)
        with open(_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as ex:
        logger.warning(f"Failed to save preferences to {_FILE}: {ex}")
