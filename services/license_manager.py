"""services/license_manager.py — local license persistence + validation.

Owns app_data_dir()/license.json, the one place a stored license key is
read, verified, and turned into an edition string.

Signature validation is always local and offline (a key signed by
scripts/issue_license.py or qforge-licensing's admin API). activate()
additionally calls out to the qforge-licensing service (services/
licensing_client.py) to enforce the license's device limit — the one
place in the app that touches the network for licensing. load() and
current_edition() stay purely local, so every app launch after the first
activation is fully offline; see qforge-licensing's ARCHITECTURE.md §7/§8.

Fails closed to "free" on any invalid/missing/expired/tampered/unreachable
state — this is the sole gate between "user typed something in a box" and
the app granting Pro capabilities, so every failure path returns the same
safe default rather than raising.
"""
import base64
import json
import os
from datetime import date

from services import licensing_client
from utils.installation_id import get_installation_id
from utils.license_signing import verify_signature
from utils.logger import get_logger
from utils.paths import app_data_dir

logger = get_logger()

_LICENSE_FILE = os.path.join(app_data_dir(), "license.json")
_SEPARATOR = "."


def canonical_payload_bytes(payload: dict) -> bytes:
    """Deterministic JSON encoding — the issuer signs this exact byte
    sequence, so verification must reproduce it exactly (sorted keys, no
    incidental whitespace)."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def build_key_string(payload: dict, signature_b64: str) -> str:
    """Assemble the pasteable license key: base64url(payload).signature.
    Lives here (not in scripts/issue_license.py) so the issuer and the
    verifier below can never drift out of format sync."""
    payload_b64 = base64.urlsafe_b64encode(canonical_payload_bytes(payload)).decode("ascii")
    return f"{payload_b64}{_SEPARATOR}{signature_b64}"


def _decode_key_string(key_string: str):
    """(payload_dict, signature_b64), or None if malformed. Never raises."""
    try:
        payload_b64, signature_b64 = key_string.strip().split(_SEPARATOR, 1)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        if not isinstance(payload, dict):
            return None
        return payload, signature_b64
    except Exception:
        return None


def _validate_key_string(key_string: str):
    """(payload, "") on a valid, current, correctly-signed Pro key;
    (None, reason) otherwise. Never raises."""
    decoded = _decode_key_string(key_string)
    if decoded is None:
        return None, "That doesn't look like a valid license key."
    payload, signature_b64 = decoded

    if not verify_signature(canonical_payload_bytes(payload), signature_b64):
        return None, "License signature is invalid — the key may be corrupted or tampered with."

    if payload.get("edition") != "pro":
        return None, "License payload is malformed (unrecognized edition)."

    expires_at = payload.get("expires_at")
    if expires_at:
        try:
            if date.fromisoformat(expires_at) < date.today():
                return None, f"This license expired on {expires_at}."
        except ValueError:
            return None, "License payload is malformed (bad expiry date)."

    return payload, ""


class LicenseManager:
    def _load_raw(self) -> dict | None:
        """The stored file's raw {"key", "receipt"} dict, unvalidated —
        or None if missing/unreadable. Used where the key *string* itself
        is needed (deactivate()); load() below validates it into a
        payload for edition checks."""
        if not os.path.exists(_LICENSE_FILE):
            return None
        try:
            with open(_LICENSE_FILE) as f:
                return json.load(f)
        except Exception as ex:
            logger.warning(f"Failed to read license file: {ex}")
            return None

    def load(self) -> dict | None:
        """Validated license payload, or None — covers "no license file",
        "unreadable file", "bad signature", "tampered payload", and
        "expired", all folding to the same Free-by-default result."""
        stored = self._load_raw()
        if stored is None:
            return None
        payload, _ = _validate_key_string(stored.get("key", ""))
        return payload

    def activate(self, key_string: str) -> tuple[bool, str]:
        """Validate locally before ever touching the network or the
        stored file — a bad key never reaches either. Once the signature
        checks out, registers the activation with the licensing service
        to enforce the license's device limit. Returns (True, "") on
        success, (False, reason) on failure — including a
        network/device-limit failure, both of which fail closed (no Pro
        granted)."""
        key_string = key_string.strip()
        payload, reason = _validate_key_string(key_string)
        if payload is None:
            return False, reason

        result = licensing_client.activate_online(key_string, get_installation_id())
        if not result.get("ok"):
            server_reason = result.get("reason")
            if server_reason == "device_limit_reached":
                limit = result.get("device_limit")
                return False, (
                    f"This license is already active on {limit} device(s) — "
                    "deactivate one first, or use a license with a higher device limit."
                )
            if server_reason == "network_error":
                return False, "Couldn't reach the license server. Check your connection and try again."
            return False, "This license key was not recognized by the license server."

        try:
            os.makedirs(os.path.dirname(_LICENSE_FILE), exist_ok=True)
            with open(_LICENSE_FILE, "w") as f:
                json.dump({"key": key_string, "receipt": result.get("receipt")}, f, indent=2)
        except OSError as ex:
            logger.error(f"Failed to write license file: {ex}")
            return False, f"Could not save the license: {ex}"
        return True, ""

    def deactivate(self):
        """Best-effort notifies the licensing service (freeing this
        installation's seat) but always removes the local file regardless
        of network outcome — a user must always be able to drop back to
        Free locally, online or not, matching /deactivate's own
        best-effort design on the server side."""
        stored = self._load_raw()
        if stored:
            licensing_client.deactivate_online(stored.get("key", ""), get_installation_id())
        try:
            if os.path.exists(_LICENSE_FILE):
                os.remove(_LICENSE_FILE)
        except OSError as ex:
            logger.warning(f"Failed to remove license file: {ex}")

    def current_edition(self) -> str:
        """"pro" or "free" — a plain string, not the Edition enum: keeps
        this module independent of services.entitlements so nothing here
        needs to import the model that in turn depends on this class."""
        payload = self.load()
        return "pro" if payload else "free"


license_manager = LicenseManager()
