"""Persist pinned/favourite SQL tabs across sessions.

Format: { connection_name: [ {name, query}, ... ] }
"""
import json
import os

_FILE = os.path.join(
    os.path.expanduser("~"), "Library", "Application Support", "QForge", "pinned_tabs.json"
)


def load() -> dict:
    try:
        if os.path.exists(_FILE):
            with open(_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save(data: dict):
    try:
        os.makedirs(os.path.dirname(_FILE), exist_ok=True)
        with open(_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass
