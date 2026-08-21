"""Network calls to the qforge-licensing activation service — the only
place `services/license_manager.py` touches the network. Mirrors
utils/entitlement_fetcher.py's stdlib-only urllib approach (no new
dependency) and its silent-failure contract: any network problem here
degrades to a clear "network_error" reason, never a raised exception.

Only /activate and /deactivate are called from here — day-to-day edition
checks (LicenseManager.load()/current_edition()) stay purely local, per
qforge-licensing's ARCHITECTURE.md §7/§8.
"""
import json
import urllib.error
import urllib.request

from utils.updater import APP_VERSION

# Base URL of the deployed qforge-licensing service.
LICENSING_SERVICE_URL = "https://qforge-licensing-production.up.railway.app"

_TIMEOUT_SECONDS = 8


def _post(path: str, payload: dict) -> dict | None:
    """POST JSON to the licensing service, return the parsed JSON
    response, or None on any network/parse failure. Never raises."""
    try:
        req = urllib.request.Request(
            f"{LICENSING_SERVICE_URL}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": f"QForge/{APP_VERSION}"},
            method="POST",
        )
        # LICENSING_SERVICE_URL is a fixed https:// literal above —
        # nothing attacker- or user-influenced feeds the scheme/host.
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:  # nosec B310
            return json.loads(resp.read())
    except Exception:
        return None


def activate_online(license_key: str, installation_id: str) -> dict:
    """{"ok": True, "receipt": ..., "device_limit": ..., "seats_used": ...}
    on success, or {"ok": False, "reason": "device_limit_reached" |
    "invalid_license" | "network_error", ...} — the same shape
    qforge-licensing's POST /activate returns, with "network_error" added
    locally for anything that never got a response at all."""
    result = _post("/activate", {"license_key": license_key, "installation_id": installation_id})
    if result is None:
        return {"ok": False, "reason": "network_error"}
    return result


def validate_online(license_key: str, installation_id: str) -> dict:
    """{"ok": True, "status": "active", "device_limit": ..., "seats_used": ...}
    on a still-live seat, {"ok": False, "status": "revoked" | "suspended" |
    "expired" | "active", "reason": ...} on a server-confirmed bad state, or
    {"ok": False, "reason": "invalid_license"} — the same shape
    qforge-licensing's POST /validate returns, with "network_error" added
    locally for anything that never got a response at all. Doesn't register
    a new device or consume a seat — see services/license_manager.py's
    revalidate_online()."""
    result = _post("/validate", {"license_key": license_key, "installation_id": installation_id})
    if result is None:
        return {"ok": False, "reason": "network_error"}
    return result


def deactivate_online(license_key: str, installation_id: str) -> None:
    """Best-effort — mirrors POST /deactivate's own "best-effort cleanup,
    not a security boundary" design. Failures are swallowed; the caller
    (LicenseManager.deactivate()) always removes the local license file
    regardless of what happens here, so a user is never stuck Pro-locked
    to a device just because they're offline."""
    _post("/deactivate", {"license_key": license_key, "installation_id": installation_id})
