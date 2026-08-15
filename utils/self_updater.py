"""Downloads, verifies, and installs a QForge update DMG in place over the
running .app, then hands off to a relaunch — the in-app update path for
macOS (issue #38).

Only usable when running from an installed .app bundle (`sys.frozen`);
running from source has no bundle to replace, so main.py falls back to
opening the release page in a browser in that case."""

import hashlib
import os
import plistlib
import shutil
import subprocess  # nosec B404
import sys
import tempfile
import urllib.request

from PySide6.QtCore import QThread, Signal

from utils.logger import get_logger
from utils.update_signing import verify_signature
from utils.updater import GITHUB_USER, GITHUB_REPO

logger = get_logger()

_CHUNK = 256 * 1024
_USER_AGENT = "QForge-Updater"


def running_app_bundle_path():
    """Path to the .app bundle currently running, or None if not frozen."""
    if not getattr(sys, "frozen", False):
        return None
    exe_dir = os.path.dirname(sys.executable)
    app_path = os.path.abspath(os.path.join(exe_dir, "..", ".."))
    return app_path if app_path.endswith(".app") else None


class UpdateInstaller(QThread):
    """Downloads the release DMG, verifies it against the published
    SHA256SUMS.txt, swaps it into place over the running .app bundle, and
    reports progress/outcome via signals."""

    progress = Signal(int)          # 0-100
    failed = Signal(str)
    ready_to_restart = Signal(str)  # path of the newly installed .app

    def __init__(self, tag: str, dmg_url: str, parent=None):
        super().__init__(parent)
        self.tag = tag
        self.dmg_url = dmg_url

    def run(self):
        try:
            app_path = running_app_bundle_path()
            if not app_path:
                raise RuntimeError("Not running from an installed app bundle.")

            dmg_path = self._download(self.dmg_url)
            self._verify(dmg_path)
            new_app_path = self._extract_app(dmg_path)
            self._replace(app_path, new_app_path)
            self.progress.emit(100)
            self.ready_to_restart.emit(app_path)
        except Exception as ex:
            logger.error(f"Self-update failed: {ex}")
            self.failed.emit(str(ex))

    def _download(self, url: str) -> str:
        # Unlike sums_url/sig_url below (fixed https://github.com/... with
        # only a path segment interpolated), this url is the GitHub API's
        # browser_download_url field verbatim — attacker-influenceable if
        # the release channel itself is compromised (issue #113's threat
        # model). Reject anything but plain https before ever calling
        # urlopen, so a malicious file:// or unexpected scheme can't be
        # used to read a local file or reach an unintended target.
        if not url.startswith("https://"):
            raise RuntimeError(f"Refusing to fetch update from a non-https URL: {url}")
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        fd, tmp_path = tempfile.mkstemp(suffix=".dmg")
        self._sha256 = hashlib.sha256()
        with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310
            total = int(resp.headers.get("Content-Length", 0))
            written = 0
            with os.fdopen(fd, "wb") as f:
                while True:
                    chunk = resp.read(_CHUNK)
                    if not chunk:
                        break
                    f.write(chunk)
                    self._sha256.update(chunk)
                    written += len(chunk)
                    if total:
                        self.progress.emit(min(99, int(written * 100 / total)))
        return tmp_path

    def _verify(self, dmg_path: str):
        sums_url = (
            f"https://github.com/{GITHUB_USER}/{GITHUB_REPO}/releases/"
            f"download/{self.tag}/SHA256SUMS.txt"
        )
        req = urllib.request.Request(sums_url, headers={"User-Agent": _USER_AGENT})
        try:
            # sums_url is a fixed https://github.com/... literal above with
            # only a path segment (self.tag) interpolated — scheme can't be
            # attacker-influenced the way _download()'s url can.
            with urllib.request.urlopen(req, timeout=15) as resp:  # nosec B310
                sums_bytes = resp.read()
        except Exception as ex:
            os.remove(dmg_path)
            raise RuntimeError(f"Could not fetch checksums for verification: {ex}")

        sig_url = sums_url + ".sig"
        req = urllib.request.Request(sig_url, headers={"User-Agent": _USER_AGENT})
        try:
            # sig_url is sums_url (see above) + a fixed ".sig" suffix.
            with urllib.request.urlopen(req, timeout=15) as resp:  # nosec B310
                signature_b64 = resp.read().decode().strip()
        except Exception as ex:
            os.remove(dmg_path)
            raise RuntimeError(f"Could not fetch checksum signature for verification: {ex}")

        # Requiring a valid signature here, not just a matching checksum, is
        # the actual point of issue #113: without it, an attacker able to
        # publish to (or intercept) this GitHub release could replace the
        # DMG and SHA256SUMS.txt together and this check alone wouldn't
        # notice — see utils/update_signing.py.
        if not verify_signature(sums_bytes, signature_b64):
            os.remove(dmg_path)
            raise RuntimeError(
                "Checksum signature verification failed — refusing to "
                "trust this update."
            )

        sums_text = sums_bytes.decode()
        expected = next(
            (line.split()[0].lower() for line in sums_text.splitlines()
             if line.strip().endswith(".dmg")),
            None,
        )
        if not expected:
            os.remove(dmg_path)
            raise RuntimeError("Could not find the .dmg checksum in SHA256SUMS.txt")

        actual = self._sha256.hexdigest().lower()
        if actual != expected:
            os.remove(dmg_path)
            raise RuntimeError("Downloaded update failed checksum verification.")

    def _extract_app(self, dmg_path: str) -> str:
        # dmg_path is our own tempfile.mkstemp() path; list-form args, no
        # shell — nothing here is attacker-controlled string concatenation.
        out = subprocess.run(
            ["hdiutil", "attach", dmg_path, "-nobrowse", "-readonly", "-plist"],
            capture_output=True, check=True,
        )  # nosec B603 B607
        info = plistlib.loads(out.stdout)
        mount_point = next(
            e["mount-point"] for e in info["system-entities"] if e.get("mount-point")
        )
        try:
            apps = [f for f in os.listdir(mount_point) if f.endswith(".app")]
            if not apps:
                raise RuntimeError("No .app found inside the downloaded update.")
            # Copy out of the (read-only, about-to-be-detached) mounted
            # volume before we lose access to it.
            staged = os.path.join(tempfile.mkdtemp(), apps[0])
            shutil.copytree(os.path.join(mount_point, apps[0]), staged, symlinks=True)
            return staged
        finally:
            # mount_point came from hdiutil's own plist output above, not
            # from any external input.
            subprocess.run(["hdiutil", "detach", mount_point, "-quiet"], check=False)  # nosec B603 B607
            os.remove(dmg_path)

    def _replace(self, app_path: str, new_app_path: str):
        # ponytail: crude immediate-delete backup rather than deferring
        # cleanup to next launch — fine since shutil.move below either
        # fully succeeds or we restore the backup right here.
        backup = app_path + ".old"
        shutil.rmtree(backup, ignore_errors=True)
        os.rename(app_path, backup)
        try:
            shutil.move(new_app_path, app_path)
        except Exception:
            shutil.rmtree(app_path, ignore_errors=True)
            os.rename(backup, app_path)
            raise
        shutil.rmtree(backup, ignore_errors=True)
        # Best-effort: a downloaded-by-python file has no quarantine
        # attribute to begin with, but clear it in case macOS added one
        # anyway, so relaunch doesn't hit a Gatekeeper prompt.
        # app_path is the running bundle's own path (running_app_bundle_path,
        # derived from sys.executable) — not externally supplied.
        subprocess.run(["xattr", "-dr", "com.apple.quarantine", app_path], check=False)  # nosec B603 B607


def relaunch(app_path: str):
    """Launch the freshly-installed app and quit this process."""
    subprocess.Popen(["open", "-n", app_path])  # nosec B603 B607
    sys.exit(0)
