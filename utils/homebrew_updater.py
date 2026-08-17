"""Runs `brew upgrade --cask qforge` on a worker thread for a Homebrew-managed
install (issue #79) — the command is a fixed argv list (cask name is the
module constant, never user input), so there's no shell/injection surface."""

import subprocess

from PySide6.QtCore import QThread, Signal

from utils.install_source import CASK_NAME
from utils.logger import get_logger

logger = get_logger()


class HomebrewUpdateInstaller(QThread):
    done = Signal()
    failed = Signal(str)

    def __init__(self, brew_path: str, parent=None):
        super().__init__(parent)
        self.brew_path = brew_path

    def run(self):
        try:
            result = subprocess.run(
                [self.brew_path, "upgrade", "--cask", CASK_NAME],
                capture_output=True, text=True, timeout=300,
            )
        except Exception as ex:
            logger.error(f"Homebrew upgrade failed to run: {ex}")
            self.failed.emit(str(ex))
            return

        if result.returncode != 0:
            message = (result.stderr or result.stdout or "brew upgrade failed").strip()
            logger.error(f"Homebrew upgrade failed: {message}")
            self.failed.emit(message)
            return

        self.done.emit()
