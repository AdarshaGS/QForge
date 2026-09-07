"""Background update checker — hits GitHub releases API, emits a signal when
a newer version is available.  No extra dependencies (uses stdlib urllib)."""

import json
import urllib.request
import urllib.error

from PySide6.QtCore import QThread, Signal

# ── App identity ──────────────────────────────────────────────────────────────
GITHUB_USER = "AdarshaGS"
# Deliberately NOT the "QForge" source repo — that's private (source stays
# confidential), and this URL (plus self_updater.py's, which imports these
# same two constants) is hit unauthenticated by every installed copy of the
# app. Releases are published to this separate public repo instead — see
# .github/workflows/build-release.yml's "release" job.
GITHUB_REPO = "QForge-releases"
APP_VERSION  = "1.5.0"        # bump this, commit, then tag as v<APP_VERSION> to release


def _vtuple(tag: str) -> tuple:
    """'v1.2.3' or '1.2.3' → (1, 2, 3)"""
    return tuple(int(x) for x in tag.lstrip("v").split(".") if x.isdigit())


class UpdateChecker(QThread):
    """Runs a single HTTP request on a worker thread; never blocks the UI."""

    # emitted on main thread when a newer release is found
    # (tag_name e.g. "v1.2.0", html_url, macOS .dmg asset download URL or "")
    update_available = Signal(str, str, str)

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
            # url above is a fixed https://api.github.com/... literal built
            # only from the hardcoded GITHUB_USER/GITHUB_REPO constants —
            # nothing attacker- or user-influenced feeds the scheme/host.
            with urllib.request.urlopen(req, timeout=8) as resp:  # nosec B310
                data = json.loads(resp.read())

            tag      = data.get("tag_name", "").strip()
            html_url = data.get("html_url", "")
            dmg_url  = next(
                (a.get("browser_download_url", "") for a in data.get("assets", [])
                 if a.get("name", "").endswith(".dmg")),
                "",
            )

            if tag and _vtuple(tag) > _vtuple(APP_VERSION):
                self.update_available.emit(tag, html_url, dmg_url)

        except Exception:
            pass   # silently ignore — no network, rate-limit, etc.
