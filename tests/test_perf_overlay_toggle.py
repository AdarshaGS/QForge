"""Regression guard for the dev performance overlay (issue #43): the
shortcut toggles visibility, and it never renders anything for a normal
user unless explicitly turned on (env var or shortcut)."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QWidget

from ui.perf_overlay import PerfOverlayWidget
from utils import perf_metrics

_app = QApplication.instance() or QApplication([])


class _MainWindowStub(QWidget):
    def __init__(self):
        super().__init__()
        self.resize(1000, 700)
        self._panels = []
        # A hidden parent makes any child's isVisible() False regardless of
        # the child's own show()/hide() state — show it, matching the real
        # MainWindow (main.py's __main__ always calls window.show()).
        self.show()


def setup_function():
    perf_metrics.reset()


def test_overlay_hidden_by_default():
    win = _MainWindowStub()
    overlay = PerfOverlayWidget(win)
    assert not overlay.isVisible()


def test_toggle_shows_then_hides():
    win = _MainWindowStub()
    overlay = PerfOverlayWidget(win)

    overlay.toggle()
    assert overlay.isVisible()

    overlay.toggle()
    assert not overlay.isVisible()


def test_refresh_reflects_recorded_metrics():
    win = _MainWindowStub()
    overlay = PerfOverlayWidget(win)

    perf_metrics.record("database", "db_connect", 42.0)
    overlay.refresh()

    assert "42.0" in overlay._label.text() or "42" in overlay._label.text()


def test_active_connections_counts_only_live_db_service_connections():
    win = _MainWindowStub()

    class _StubPanel:
        def __init__(self, connected):
            self.db_service = type("Svc", (), {"connection": object() if connected else None})()

    win._panels = [_StubPanel(True), _StubPanel(False), _StubPanel(True)]
    overlay = PerfOverlayWidget(win)
    assert overlay._active_connections() == 2


def test_reposition_anchors_top_right():
    win = _MainWindowStub()
    overlay = PerfOverlayWidget(win)
    overlay.toggle()
    overlay.reposition()
    assert overlay.x() + overlay.width() <= win.width()
    assert overlay.x() > win.width() / 2  # anchored right, not left
