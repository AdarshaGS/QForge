"""A random, persistent ID for this QForge installation — not a hardware
fingerprint. Used only to let the licensing service count how many
installations a license key is active on (device-limit enforcement); see
qforge-licensing's ARCHITECTURE.md §4 for why installation ID rather than
hardware ID was chosen.
"""
import os
import uuid

from utils.paths import app_data_dir

_INSTALLATION_ID_FILE = os.path.join(app_data_dir(), "installation_id.txt")


def get_installation_id() -> str:
    """Reads the persisted ID, generating and persisting one on first
    call. Stable for the lifetime of this install (survives app restarts,
    resets only if the file is deleted)."""
    try:
        with open(_INSTALLATION_ID_FILE) as f:
            existing = f.read().strip()
        if existing:
            return existing
    except OSError:
        pass

    new_id = uuid.uuid4().hex
    os.makedirs(os.path.dirname(_INSTALLATION_ID_FILE), exist_ok=True)
    with open(_INSTALLATION_ID_FILE, "w") as f:
        f.write(new_id)
    return new_id
