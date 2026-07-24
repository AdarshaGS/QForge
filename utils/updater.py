"""Background update checker — hits GitHub releases API, emits a signal when
a newer version is available.  No extra dependencies (uses stdlib urllib)."""

import json
import urllib.request
import urllib.error

from PySide6.QtCore import QThread, Signal

# ── App identity ──────────────────────────────────────────────────────────────
GITHUB_USER = "AdarshaGS"
GITHUB_REPO = "QForge"
APP_VERSION  = "1.0.6"        # bump this, commit, then tag as v<APP_VERSION> to release


def _vtuple(tag: str) -> tuple:
    """'v1.2.3' or '1.2.3' → (1, 2, 3)"""
    return tuple(int(x) for x in tag.lstrip("v").split(".") if x.isdigit())


class UpdateChecker(QThread):
    """Runs a single HTTP request on a worker thread; never blocks the UI."""

    # emitted on main thread when a newer release is found
    update_available = Signal(str, str)   # (tag_name e.g. "v1.2.0", html_url)

    def run(self):
        try:
            url = (
                f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}"
                "/releases/latest"
            )
            req = urllib.request.Request(
                url,
                headers={"User-Agent": f"QForge/{APP_VERSION}"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read())

            tag      = data.get("tag_name", "").strip()
            html_url = data.get("html_url", "")

            if tag and _vtuple(tag) > _vtuple(APP_VERSION):
                self.update_available.emit(tag, html_url)

        except Exception:
            pass   # silently ignore — no network, rate-limit, etc.
