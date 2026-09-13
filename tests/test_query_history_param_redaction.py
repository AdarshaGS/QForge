"""Issue #289: query history must never persist a resolved {{param}} value.

_prompt_params() substitutes {{name}} placeholders with whatever the user
typed into the parameter dialog — which can be secret-shaped (a password,
API key, token). The resolved query used to be the only copy kept on the
tab (`tab._last_query`), and both _on_query_done and _on_query_multi_done
handed that straight to query_history.add_query(), so the secret value
was written to query_history.json in the clear and later readable from
any panel/connection that opens history.

_run_query_in_tab now also keeps the as-typed template (placeholders
intact) on `tab._last_query_template`, and the history-writing paths use
that instead."""
import os
from types import SimpleNamespace

import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from ui.connection_panel import ConnectionPanel
from ui.sql_tab import SqlTab

_app = QApplication.instance() or QApplication([])


class _HistoryStub:
    def __init__(self):
        self.added = []

    def add_query(self, query, connection_name, *args, **kwargs):
        self.added.append(query)
        return "entry-1"


# ─── _run_query_in_tab keeps the unresolved template on the tab ───────────


class _RunPanelStub(QObject):
    """_guard_write returns True (unlike test_run_all_statements.py's
    stub) so execution proceeds far enough to record _last_query /
    _last_query_template; the fake config then makes DbService.connect()
    fail fast on a missing "host" key (no network, no thread) which
    _run_query_in_tab already catches and turns into show_error(). Must be
    a real QObject: _run_query_in_tab parents a QTimer to `self`."""

    def __init__(self):
        super().__init__()
        self._connecting = False
        self.config = {"name": "test-conn", "type": "mysql"}

    _run_query_in_tab = ConnectionPanel._run_query_in_tab
    _restore_run_btn = staticmethod(ConnectionPanel._restore_run_btn)

    def _prompt_params(self, query):
        return query.replace("{{token}}", "hunter2")

    def _guard_write(self, sql, extra_reason=None):
        return True

    def _emit_health(self, state):
        pass


def test_last_query_template_keeps_placeholder_resolved_query_has_secret():
    tab = SqlTab()
    tab.editor.setPlainText("SELECT * FROM t WHERE token = '{{token}}'")
    panel = _RunPanelStub()

    ConnectionPanel._run_query_in_tab(panel, tab)

    assert tab._last_query_template == "SELECT * FROM t WHERE token = '{{token}}'"
    assert tab._last_query == "SELECT * FROM t WHERE token = 'hunter2'"


# ─── History-writing paths use the template, not the resolved query ──────


class _QueryDonePanelStub:
    def __init__(self, tab=None):
        self.config = {"name": "test-conn"}
        self.query_history = _HistoryStub()
        self._sidebar_stack = SimpleNamespace(currentIndex=lambda: 0)
        # currentWidget() returning the same tab that's passed to
        # _on_query_done skips the "other tab finished" toast branch,
        # which this stub doesn't otherwise support.
        self.tabs = SimpleNamespace(currentWidget=lambda: tab)

    _on_query_done = ConnectionPanel._on_query_done
    _extract_table_name = staticmethod(ConnectionPanel._extract_table_name)

    def _restore_run_btn(self, tab):
        pass

    def _wire_result_fk(self, tab, table_name):
        pass

    def _emit_health(self, state):
        pass

    def _finalize_query_connection(self, tab):
        pass


def test_on_query_done_writes_template_not_resolved_secret_to_history():
    tab = SqlTab()
    tab._last_query = "SELECT * FROM t WHERE token = 'hunter2'"
    tab._last_query_template = "SELECT * FROM t WHERE token = '{{token}}'"
    panel = _QueryDonePanelStub(tab)

    ConnectionPanel._on_query_done(panel, tab, pd.DataFrame({"n": [1]}), 0.01)

    assert panel.query_history.added == ["SELECT * FROM t WHERE token = '{{token}}'"]
    assert "hunter2" not in panel.query_history.added[0]


class _MultiDonePanelStub:
    def __init__(self):
        self.config = {"name": "test-conn"}
        self.query_history = _HistoryStub()
        self._sidebar_stack = SimpleNamespace(currentIndex=lambda: 0)

    _on_query_multi_done = ConnectionPanel._on_query_multi_done
    _restore_run_btn = staticmethod(ConnectionPanel._restore_run_btn)
    _extract_table_name = staticmethod(ConnectionPanel._extract_table_name)

    def _emit_health(self, state):
        pass

    def _finalize_query_connection(self, tab):
        pass


def test_multi_done_writes_template_not_resolved_secret_to_history():
    tab = SqlTab()
    tab._last_query = "SELECT * FROM t WHERE token = 'hunter2'; SELECT 1;"
    tab._last_query_template = "SELECT * FROM t WHERE token = '{{token}}'; SELECT 1;"
    panel = _MultiDonePanelStub()

    results = [("SELECT * FROM t WHERE token = 'hunter2';", pd.DataFrame({"n": [1]}), None),
               ("SELECT 1;", pd.DataFrame({"n": [1]}), None)]
    ConnectionPanel._on_query_multi_done(panel, tab, results, 0.02)

    assert panel.query_history.added == ["SELECT * FROM t WHERE token = '{{token}}'; SELECT 1;"]
    assert "hunter2" not in panel.query_history.added[0]


def test_history_writers_fall_back_to_last_query_when_no_template_set():
    # override_query path (Begin/Commit/Rollback buttons) never goes through
    # _prompt_params, so _last_query_template is never set — must not KeyError.
    tab = SqlTab()
    tab._last_query = "COMMIT"
    panel = _QueryDonePanelStub(tab)

    ConnectionPanel._on_query_done(panel, tab, pd.DataFrame(), 0.01)

    assert panel.query_history.added == ["COMMIT"]
