"""Persist per-connection ER diagram layouts (table positions + collapsed
state) across sessions. Same load/save-json pattern as utils/col_widths.py.

Format: { "<connection_id>:<database>": {table_name: {"x": float, "y":
float, "collapsed": bool}} }.
"""
import json
import os

from utils.logger import get_logger
from utils.paths import app_data_dir

logger = get_logger()

_FILE = os.path.join(app_data_dir(), "erd_layout.json")


def _key(connection_id: str, database: str) -> str:
    return f"{connection_id}:{database or ''}"


def _read_all() -> dict:
    try:
        if os.path.exists(_FILE):
            with open(_FILE) as f:
                return json.load(f)
    except Exception as ex:
        logger.warning(f"Failed to load ERD layout from {_FILE}: {ex}")
    return {}


def load(connection_id: str, database: str) -> dict | None:
    if not connection_id:
        return None
    return _read_all().get(_key(connection_id, database))


def save(connection_id: str, database: str, layout: dict):
    if not connection_id:
        return
    try:
        data = _read_all()
        data[_key(connection_id, database)] = layout
        os.makedirs(os.path.dirname(_FILE), exist_ok=True)
        with open(_FILE, "w") as f:
            json.dump(data, f)
    except Exception as ex:
        logger.warning(f"Failed to save ERD layout to {_FILE}: {ex}")


def clear(connection_id: str, database: str):
    if not connection_id:
        return
    try:
        data = _read_all()
        if data.pop(_key(connection_id, database), None) is not None:
            os.makedirs(os.path.dirname(_FILE), exist_ok=True)
            with open(_FILE, "w") as f:
                json.dump(data, f)
    except Exception as ex:
        logger.warning(f"Failed to clear ERD layout in {_FILE}: {ex}")
