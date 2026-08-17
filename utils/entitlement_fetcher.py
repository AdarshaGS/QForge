"""Background fetch of the remote entitlement-config override — mirrors
utils/updater.py's UpdateChecker exactly (same stdlib-only HTTP approach,
same silent-failure contract): a single request on a worker thread, never
blocking the UI, and never raising if it fails.

Failure here (no network, rate-limited, 404, malformed JSON) simply means
services.entitlements keeps using its bundled defaults or last-cached
values — this fetch is an enhancement, never a requirement.
"""
import json
import urllib.error
import urllib.request

from PySide6.QtCore import QThread, Signal

from services.entitlement_config import ENTITLEMENT_CONFIG_URL
from utils.updater import APP_VERSION


class EntitlementConfigFetcher(QThread):
    """Runs a single HTTP GET on a worker thread; emits config_loaded only
    on success. Never emits, never raises, on any failure."""

    config_loaded = Signal(dict)

    def run(self):
        try:
            req = urllib.request.Request(
                ENTITLEMENT_CONFIG_URL,
                headers={"User-Agent": f"QForge/{APP_VERSION}"},
            )
            # ENTITLEMENT_CONFIG_URL is a fixed https:// literal from
            # services/entitlement_config.py — nothing attacker- or
            # user-influenced feeds the scheme/host.
            with urllib.request.urlopen(req, timeout=8) as resp:  # nosec B310
                raw = json.loads(resp.read())
            if isinstance(raw, dict):
                self.config_loaded.emit(raw)
        except Exception:
            pass  # silently ignore — no network, rate-limit, malformed JSON, etc.
