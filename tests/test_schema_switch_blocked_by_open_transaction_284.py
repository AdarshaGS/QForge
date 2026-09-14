"""Regression test for issue #284: switching the Postgres schema pill used
to silently leave a tab's open transaction bound to the OLD schema.

_switch_schema() mutates the panel's shared self.config["schema"] and
repoints self.db_service's search_path — but a tab with an open manual
transaction runs on its own dedicated DbService (_run_query_in_tab reuses
it across runs while tab._tx_db_service is set, per _finalize_query_
connection), whose connection's search_path was fixed when the
transaction began. Switching the schema pill didn't touch that
connection at all, so the UI would show the new schema while that tab's
in-flight transaction kept running against the old one.

Fix: _switch_schema() now blocks (with a warning) when
has_open_transactions() is true, instead of proceeding silently.
"""
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QMessageBox, QTabWidget

from ui.connection_panel import ConnectionPanel
from ui.sql_tab import SqlTab

_app = QApplication.instance() or QApplication([])


class _SchemaSwitchPanelStub:
    has_open_transactions = ConnectionPanel.has_open_transactions
    _switch_schema = ConnectionPanel._switch_schema

    def __init__(self, schema="public"):
        self.config = {"schema": schema}
        self._connecting = False
        self.tabs = QTabWidget()
        self.db_service = SimpleNamespace(connection=None, set_schema=lambda s: None)
        self.schema_fetch_calls = []

    def _clear_schema_state(self):
        pass

    def _start_schema_loading_indicator(self, label=None):
        pass

    def _update_pill_label(self):
        pass

    def _spawn_schema_fetch(self, conf):
        self.schema_fetch_calls.append(conf)


def test_schema_switch_blocked_when_a_tab_has_an_open_transaction(monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a) or QMessageBox.Ok)

    panel = _SchemaSwitchPanelStub(schema="public")
    tx_tab = SqlTab()
    tx_tab._tx_db_service = object()  # simulates an open transaction on this tab
    panel.tabs.addTab(tx_tab, "Query 1")

    panel._switch_schema("reporting")

    assert len(warnings) == 1
    assert panel.config["schema"] == "public"  # unchanged
    assert panel.schema_fetch_calls == []  # switch never proceeded


def test_schema_switch_proceeds_when_no_tab_has_an_open_transaction(monkeypatch):
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: pytest.fail("should not warn"))

    panel = _SchemaSwitchPanelStub(schema="public")
    idle_tab = SqlTab()
    panel.tabs.addTab(idle_tab, "Query 1")

    panel._switch_schema("reporting")

    assert panel.config["schema"] == "reporting"
    assert len(panel.schema_fetch_calls) == 1
