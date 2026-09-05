"""Persist per-table data-grid layout (issue #182): column order, column
widths, and pinned/frozen column count.

Keyed the same way as utils/schema_cache.py and services/table_organization.py
(connection id + database), plus the table name — table names alone collide
across different databases/connections.
"""
import json
import os

from utils.logger import get_logger
from utils.paths import app_data_dir

logger = get_logger()

_FILE = os.path.join(app_data_dir(), "grid_layout.json")


def _key(connection_id: str, database: str, table_name: str) -> str:
    return f"{connection_id}:{database or ''}:{table_name}"


def _read_all() -> dict:
    if os.path.exists(_FILE):
        with open(_FILE) as f:
            return json.load(f)
    return {}


def _write_all(data: dict):
    os.makedirs(os.path.dirname(_FILE), exist_ok=True)
    with open(_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_layout(connection_id: str, database: str, table_name: str) -> dict:
    """Returns {"order": [col names], "widths": {col name: px}, "frozen": int},
    or {} if nothing's been saved for this table yet."""
    if not connection_id or not table_name:
        return {}
    try:
        return _read_all().get(_key(connection_id, database, table_name), {})
    except Exception as ex:
        logger.warning(f"Failed to read grid layout from {_FILE}: {ex}")
        return {}


def save_layout(connection_id: str, database: str, table_name: str, layout: dict):
    """Overwrite the saved layout for this table with *layout*
    (as returned by EditableTableWidget.get_layout_state())."""
    if not connection_id or not table_name:
        return
    try:
        data = _read_all()
    except Exception:
        data = {}
    data[_key(connection_id, database, table_name)] = layout
    try:
        _write_all(data)
    except Exception as ex:
        logger.warning(f"Failed to save grid layout to {_FILE}: {ex}")
