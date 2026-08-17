"""Detects how the running QForge.app was installed (issue #79), so the
update flow can route to the mechanism that actually owns the bundle instead
of always overwriting it in place. A Homebrew-managed install must be
upgraded through `brew upgrade --cask qforge` — self-replacing the bundle
would leave Homebrew's own receipt pointing at a version that's no longer
there, breaking future `brew upgrade`/`brew uninstall`.
"""
import os
import shutil
import subprocess

HOMEBREW = "homebrew"
DIRECT = "direct"
UNKNOWN = "unknown"

CASK_NAME = "qforge"

# GUI apps launched from Finder get launchd's minimal PATH
# (/usr/bin:/bin:/usr/sbin:/sbin), which doesn't include Homebrew's own bin
# dir — so shutil.which("brew") alone misses a real Homebrew install.
_COMMON_BREW_PATHS = ("/opt/homebrew/bin/brew", "/usr/local/bin/brew")


def brew_path():
    """Path to the `brew` executable, or None if it can't be found."""
    found = shutil.which("brew")
    if found:
        return found
    for path in _COMMON_BREW_PATHS:
        if os.access(path, os.X_OK):
            return path
    return None


def detect() -> str:
    """HOMEBREW if `brew` confirms it manages the qforge cask, DIRECT if
    brew is absent or doesn't know about it (a plain DMG install), UNKNOWN
    if brew is present but its answer can't be trusted."""
    brew = brew_path()
    if not brew:
        return DIRECT
    try:
        result = subprocess.run(
            [brew, "list", "--cask", "--versions", CASK_NAME],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return UNKNOWN
    if result.returncode == 0 and result.stdout.strip():
        return HOMEBREW
    return DIRECT
