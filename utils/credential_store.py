"""OS keychain-backed credential storage for QForge database/SSH passwords.

Uses the `keyring` library (Keychain on macOS) so passwords never need to be
written to connections.json in plaintext. Each stored secret is keyed by a
connection's stable `id` plus a `kind` ("db" or "ssh").
"""
import keyring
from keyring.errors import PasswordDeleteError

from utils.logger import get_logger

logger = get_logger()

_SERVICE = "QForge"


def _account(connection_id: str, kind: str) -> str:
    return f"{connection_id}:{kind}"


def set_password(connection_id: str, kind: str, password: str) -> None:
    try:
        keyring.set_password(_SERVICE, _account(connection_id, kind), password)
    except Exception as ex:
        logger.warning(f"Keychain: failed to store {kind} password for {connection_id}: {ex}")


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
