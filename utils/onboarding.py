"""Persist one-time first-run UI state across sessions (issue #164).

Currently just the connection dialog's annotated empty-state hint: shown
whenever there are zero saved connections, dismissible, and — once
dismissed — stays dismissed even if the user later deletes every
connection again. Deliberately a single flat flag file rather than
folding this into connections.json — this is UI chrome state, not
connection data, and should survive independently of it.
"""
import json
import os

from utils.logger import get_logger
from utils.paths import app_data_dir

logger = get_logger()

_FILE = os.path.join(app_data_dir(), "onboarding.json")


def _read() -> dict:
    try:
        if os.path.exists(_FILE):
            with open(_FILE) as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
    except Exception as ex:
        logger.warning(f"Failed to load onboarding state from {_FILE}: {ex}")
    return {}


def is_connection_hint_dismissed() -> bool:
    return bool(_read().get("connection_hint_dismissed"))


def dismiss_connection_hint() -> None:
    data = _read()
    data["connection_hint_dismissed"] = True
    try:
        os.makedirs(os.path.dirname(_FILE), exist_ok=True)
        with open(_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as ex:
        logger.warning(f"Failed to save onboarding state to {_FILE}: {ex}")
