"""Tests for issue #178's ConnectionPanel-level wiring: health_changed now
goes through a single _emit_health() choke point that keeps _last_health
in sync and fans out to every open SqlTab's bottom status bar, and each
new tab is pushed the connection's dialect display name."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QTabWidget

from ui.connection_panel import ConnectionPanel
from ui.sql_tab import SqlTab

_app = QApplication.instance() or QApplication([])


class _FakeHealthSignal:
    """Stands in for the real Signal(str) — .emit() calls the same slot
    ConnectionPanel.__init__ connects it to, without needing a real
    QObject/Signal for this unit test."""

    def __init__(self, panel):
        self._panel = panel

    def emit(self, status):
        self._panel._update_tab_status_bars(status)


class _PanelStub:
    def __init__(self, tabs, config=None):
        self.tabs = tabs
        self.config = config or {"type": "mysql"}
        self.health_changed = _FakeHealthSignal(self)

    _emit_health = ConnectionPanel._emit_health
    _update_tab_status_bars = ConnectionPanel._update_tab_status_bars
    _dialect_display_name = ConnectionPanel._dialect_display_name


def test_emit_health_updates_last_health_and_fans_out_to_open_sql_tabs():
    tabs = QTabWidget()
    tab_a = SqlTab()
    tab_b = SqlTab()
    tabs.addTab(tab_a, "Query 1")
    tabs.addTab(tab_b, "Query 2")
    panel = _PanelStub(tabs)

    panel._emit_health("running")

    assert panel._last_health == "running"
    assert "Running" in tab_a._readiness_lbl.text()
    assert "Running" in tab_b._readiness_lbl.text()


@pytest.mark.parametrize("db_type,expected", [
    ("mysql", "MySQL"),
    ("postgresql", "PostgreSQL"),
    ("sqlite", "SQLite"),
    ("weirddb", "WEIRDDB"),
])
def test_dialect_display_name_maps_known_types_and_falls_back_for_unknown(db_type, expected):
    panel = _PanelStub(QTabWidget(), config={"type": db_type})
    assert panel._dialect_display_name() == expected
