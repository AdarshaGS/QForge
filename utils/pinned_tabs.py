"""Persist pinned/favourite SQL tabs across sessions.

Format: { connection_name: [ {name, query}, ... ] }
"""
import json
import os

from utils.logger import get_logger

logger = get_logger()

_FILE = os.path.join(
    os.path.expanduser("~"), "Library", "Application Support", "QForge", "pinned_tabs.json"
)


def load() -> dict:
    try:
        if os.path.exists(_FILE):
            with open(_FILE) as f:
                return json.load(f)
    except Exception as ex:
        logger.warning(f"Failed to load pinned tabs from {_FILE}: {ex}")
    return {}


def save(data: dict):
    try:
        os.makedirs(os.path.dirname(_FILE), exist_ok=True)
        with open(_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as ex:
        logger.warning(f"Failed to save pinned tabs to {_FILE}: {ex}")
