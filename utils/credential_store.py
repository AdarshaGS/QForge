"""OS keychain-backed credential storage for QForge database/SSH passwords.

Uses the `keyring` library (Keychain on macOS) so passwords never need to be
written to connections.json in plaintext. Each stored secret is keyed by a
connection's stable `id` plus a `kind` ("db" or "ssh").
"""
import subprocess
import sys

import keyring
from keyring.errors import PasswordDeleteError

from utils.logger import get_logger

logger = get_logger()

_SERVICE = "QForge"


def _account(connection_id: str, kind: str) -> str:
    return f"{connection_id}:{kind}"


def _force_delete_stale_item(connection_id: str, kind: str) -> None:
    """Best-effort removal of a keychain item via the `security` CLI.

    macOS ties ownership of a keychain item to the code signature of the
    app that created it. QForge is ad-hoc signed (build.sh), so every
    rebuild gets a different signature — a password an older build stored
    can no longer be overwritten by a newer one, and keyring's own
    delete-then-add (keyring/backends/macOS/api.py) fails at the delete
    step with an error it doesn't recognize as "not found", so it isn't
    suppressed (commonly surfaces as "(-25244, 'Unknown Error')" —
    errSecInvalidOwnerEdit). Shelling out to `security` instead of using
    the same Security-framework call keyring made gives macOS a chance to
    resolve the conflict (e.g. via an interactive re-authorization prompt)
    rather than failing the same way again. The `security` CLI only exists
    on macOS — this ownership conflict is a macOS-only Keychain quirk, so
    Windows/Linux (Credential Manager / Secret Service via keyring) never
    need this recovery path (issue #34)."""
    if sys.platform != "darwin":
        return
    try:
        subprocess.run(
            ["security", "delete-generic-password", "-s", _SERVICE,
             "-a", _account(connection_id, kind)],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass


def set_password(connection_id: str, kind: str, password: str) -> bool:
    """Returns True on success. Callers must not discard a plaintext copy of
    `password` on failure — the keychain is the only place it's persisted."""
    try:
        keyring.set_password(_SERVICE, _account(connection_id, kind), password)
        return True
    except Exception as ex:
        logger.warning(f"Keychain: failed to store {kind} password for {connection_id}: {ex}")

    # Retry once after clearing out a possibly stale, ownership-conflicted
    # item — see _force_delete_stale_item.
    _force_delete_stale_item(connection_id, kind)
    try:
        keyring.set_password(_SERVICE, _account(connection_id, kind), password)
        logger.info(f"Keychain: stored {kind} password for {connection_id} after clearing a stale entry")
        return True
    except Exception as ex2:
        logger.warning(f"Keychain: retry also failed for {kind} password for {connection_id}: {ex2}")
        return False


def get_password(connection_id: str, kind: str) -> str:
    if not connection_id:
        return ""
    try:
        return keyring.get_password(_SERVICE, _account(connection_id, kind)) or ""
    except Exception as ex:
        logger.warning(f"Keychain: failed to retrieve {kind} password for {connection_id}: {ex}")
        return ""


def delete_password(connection_id: str, kind: str) -> None:
    if not connection_id:
        return
    try:
        keyring.delete_password(_SERVICE, _account(connection_id, kind))
    except PasswordDeleteError:
        pass  # nothing stored for this account — fine
    except Exception as ex:
        logger.warning(f"Keychain: failed to delete {kind} password for {connection_id}: {ex}")
