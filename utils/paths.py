"""Single source of truth for QForge's per-user application data directory
(issue #32) — was previously hardcoded to the macOS path in five separate
files, breaking Windows/Linux.
"""
import os
import sys
from pathlib import Path


def app_data_dir() -> Path:
    """OS-appropriate directory for QForge's persisted app data
    (connections, snippets, session state, etc.):

      macOS:   ~/Library/Application Support/QForge
      Windows: %APPDATA%\\QForge
      Linux:   ~/.config/QForge (or $XDG_CONFIG_HOME/QForge)
    """
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "QForge"


def bundled_asset_path(name: str) -> str:
    """Resolve a bundled asset (e.g. "logo.png", "assets/logo_mark.svg")
    both when running from source and when frozen by PyInstaller, which
    extracts/collects data files next to `sys._MEIPASS`. Was duplicated as
    main.py's module-local _asset_path() before ui/welcome_screen.py also
    needed it.
    """
    base = getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)
    return str(Path(base) / name)
