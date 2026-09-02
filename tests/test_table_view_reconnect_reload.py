"""Regression tests for issue #176 — a TableViewWidget created before the
connection was actually live (session restore racing the optimistic
background connect) hit "No active database connection" once and never
retried, even after the real connect succeeded a moment later."""
import os

import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QTabWidget
from PySide6.QtTest import QTest

from ui.connection_panel import ConnectionPanel
from ui.table_view_widget import TableViewWidget

_app = QApplication.instance() or QApplication([])


def _pump_until_loaded(w, timeout_ms=5000):
    """load_table_data() now runs its DB work on a background QThread —
    wait for it to finish (via the _loading flag it clears) instead of
    asserting immediately after a call that used to be synchronous."""
    elapsed = 0
    while getattr(w, "_loading", False) and elapsed < timeout_ms:
        QTest.qWait(10)
        elapsed += 10


class _FlakyDbService:
    """Fails every execute_query() call until connected=True — simulates
    a TableViewWidget built while db_service.connection is still None."""

    def __init__(self, connected: bool = False):
        self.connected = connected
        self.calls = 0

    def execute_query(self, query, max_rows=None):
        self.calls += 1
        if not self.connected:
            raise Exception("No active database connection")
        if "COUNT" in query.upper():
            return pd.DataFrame({"total": [1]})
        return pd.DataFrame({"id": [1]})

    def get_columns(self, table_name):
        return [{"Field": "id"}]

    def get_foreign_keys(self, table_name):
        return []


def test_tab_created_before_connect_starts_in_error_state():
    db = _FlakyDbService(connected=False)
    tv = TableViewWidget(db, "orders")
    _pump_until_loaded(tv)

    assert tv._load_failed is True
    assert "Error" in tv.limit_label.text()


def test_reload_if_errored_retries_and_recovers_once_connected():
    db = _FlakyDbService(connected=False)
    tv = TableViewWidget(db, "orders")
    _pump_until_loaded(tv)
    assert tv._load_failed is True

    db.connected = True  # the background connect just finished
    tv.reload_if_errored()
    _pump_until_loaded(tv)

    assert tv._load_failed is False
    assert "Showing" in tv.limit_label.text()


def test_reload_if_errored_is_a_noop_for_an_already_loaded_tab():
    db = _FlakyDbService(connected=True)
    tv = TableViewWidget(db, "orders")
    _pump_until_loaded(tv)
    assert tv._load_failed is False
    calls_after_initial_load = db.calls

    tv.reload_if_errored()
    _pump_until_loaded(tv)

    assert db.calls == calls_after_initial_load  # no extra query fired


def test_connection_panel_reloads_only_the_errored_tabs():
    class _PanelStub:
        def __init__(self, tabs):
            self.tabs = tabs

        _reload_errored_table_tabs = ConnectionPanel._reload_errored_table_tabs

    tabs = QTabWidget()

    healthy_db = _FlakyDbService(connected=True)
    healthy_tab = TableViewWidget(healthy_db, "customers")
    _pump_until_loaded(healthy_tab)
    tabs.addTab(healthy_tab, "customers")

    errored_db = _FlakyDbService(connected=False)
    errored_tab = TableViewWidget(errored_db, "orders")
    _pump_until_loaded(errored_tab)
    tabs.addTab(errored_tab, "orders")
    assert errored_tab._load_failed is True

    panel = _PanelStub(tabs)
    errored_db.connected = True  # reconnect completed
    panel._reload_errored_table_tabs()
    _pump_until_loaded(errored_tab)

    assert errored_tab._load_failed is False
    assert "Showing" in errored_tab.limit_label.text()
    # The already-healthy tab was left alone, not reloaded again.
    assert healthy_db.calls == 2  # its one original count+data load
