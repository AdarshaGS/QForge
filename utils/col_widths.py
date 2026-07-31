"""Persist per-table column widths across sessions."""
import json
import os

from utils.logger import get_logger

logger = get_logger()

_FILE = os.path.join(
    os.path.expanduser("~"), "Library", "Application Support", "QForge", "col_widths.json"
)


def load() -> dict:
    try:
        if os.path.exists(_FILE):
            with open(_FILE) as f:
                return json.load(f)
    except Exception as ex:
        logger.warning(f"Failed to load column widths from {_FILE}: {ex}")
    return {}


def save(data: dict):
    try:
        os.makedirs(os.path.dirname(_FILE), exist_ok=True)
        with open(_FILE, "w") as f:
            json.dump(data, f)
    except Exception as ex:
        logger.warning(f"Failed to save column widths to {_FILE}: {ex}")
