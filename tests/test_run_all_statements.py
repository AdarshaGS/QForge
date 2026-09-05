"""Regression tests for Run's statement-scoping behavior.

History: Run originally scoped to the statement at the cursor. A user with
a 10-statement UPDATE script got confused when only one row changed,
because plain Run silently resolved to just the cursor's statement — so
Run's no-selection default was changed to "run everything." That in turn
broke the opposite, equally valid case: a SELECT followed by an UPDATE,
cursor on the UPDATE, Run firing both instead of just the one the user was
looking at (issue reported 2026-09-03).

Resolution: plain Run (no selection) is cursor-scoped again — matching
DataGrip/DBeaver/TablePlus convention, see SqlTab.get_query() — and
"run everything" becomes its own explicit action (Ctrl+Shift+Return /
Database menu "Run All Statements" / ConnectionPanel.run_all_statements(),
threaded through _run_query_in_tab's `run_all=True`) instead of a silent
fallback, so neither case is ambiguous anymore.

Follows the existing _PanelStub pattern (see
tests/test_connection_panel_query_navigation.py) rather than constructing a
second real ConnectionPanel() — an earlier test that did so crashed the
suite with a shiboken "object already deleted" lifecycle error (see commit
b539861)."""
import os
from types import SimpleNamespace

import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from ui.connection_panel import ConnectionPanel
from ui.sql_tab import SqlTab

_app = QApplication.instance() or QApplication([])

_SCRIPT = "UPDATE a SET x=1;\nUPDATE b SET x=2;\nUPDATE c SET x=3;"


class _PanelStub:
    """Just enough of ConnectionPanel for _run_query_in_tab()'s query-
    resolution step to run. _guard_write is stubbed to record what it was
    called with and return False, short-circuiting before any DB
    connection/threading — that's the only side effect these tests need to
    observe."""

    def __init__(self):
        self._connecting = False
        self.guard_calls: list[str] = []

    _run_query_in_tab = ConnectionPanel._run_query_in_tab

    def _prompt_params(self, query):
        return query  # no {{params}} in these fixtures — pass through untouched

    def _guard_write(self, sql, extra_reason=None):
        self.guard_calls.append(sql)
        return False


def _tab_with_cursor_in_first_statement():
    tab = SqlTab()
    tab.editor.setPlainText(_SCRIPT)
    cursor = tab.editor.textCursor()
    cursor.setPosition(0)
    tab.editor.setTextCursor(cursor)
    return tab


def test_run_with_no_selection_sends_only_the_cursors_statement():
    tab = _tab_with_cursor_in_first_statement()
    panel = _PanelStub()

    ConnectionPanel._run_query_in_tab(panel, tab)

    assert len(panel.guard_calls) == 1
    sent = panel.guard_calls[0]
    assert "UPDATE a" in sent
    assert "UPDATE b" not in sent
    assert "UPDATE c" not in sent


def test_run_with_cursor_on_the_second_of_two_statements_sends_only_that_one():
    # The reported case: a SELECT followed by an UPDATE, cursor on the
    # UPDATE — plain Run must fire only the UPDATE, not both.
    tab = SqlTab()
    tab.editor.setPlainText("SELECT *\nFROM organisations;\n\n"
                             "UPDATE audit_logs\nSET organisation_id = 2\nWHERE id = 3;")
    cursor = tab.editor.textCursor()
    cursor.setPosition(tab.editor.toPlainText().index("UPDATE") + 3)
    tab.editor.setTextCursor(cursor)

    panel = _PanelStub()
    ConnectionPanel._run_query_in_tab(panel, tab)

    assert len(panel.guard_calls) == 1
    sent = panel.guard_calls[0]
    assert "UPDATE audit_logs" in sent
    assert "SELECT" not in sent


def test_run_all_sends_every_statement_regardless_of_cursor():
    tab = _tab_with_cursor_in_first_statement()
    panel = _PanelStub()

    ConnectionPanel._run_query_in_tab(panel, tab, run_all=True)

    assert len(panel.guard_calls) == 1
    sent = panel.guard_calls[0]
    assert "UPDATE a" in sent
    assert "UPDATE b" in sent
    assert "UPDATE c" in sent


def test_run_with_an_active_selection_sends_only_the_selection():
    tab = SqlTab()
    tab.editor.setPlainText(_SCRIPT)
    cursor = tab.editor.textCursor()
    cursor.setPosition(0)
    cursor.setPosition(len("UPDATE a SET x=1;"), cursor.MoveMode.KeepAnchor)
    tab.editor.setTextCursor(cursor)

    panel = _PanelStub()
    ConnectionPanel._run_query_in_tab(panel, tab)

    assert len(panel.guard_calls) == 1
    sent = panel.guard_calls[0]
    assert "UPDATE a" in sent
    assert "UPDATE b" not in sent
    assert "UPDATE c" not in sent


def test_a_single_statement_still_runs_normally_with_no_selection():
    tab = SqlTab()
    tab.editor.setPlainText("SELECT 1")
    panel = _PanelStub()

    ConnectionPanel._run_query_in_tab(panel, tab)

    assert panel.guard_calls == ["SELECT 1"]


# ─── Result display: every statement gets its own "Query N" tab ───────────


class _MultiDonePanelStub:
    """Just enough of ConnectionPanel for _on_query_multi_done() to run
    against a real SqlTab. _restore_run_btn/_extract_table_name are real
    @staticmethods on ConnectionPanel — wrapped in staticmethod() again so
    aliasing them here doesn't turn them into (incorrectly) self-binding
    instance methods."""

    def __init__(self):
        self.config = {"name": "test-conn"}
        self.query_history = SimpleNamespace(add_query=lambda *a, **k: None)
        self._sidebar_stack = SimpleNamespace(currentIndex=lambda: 0)

    _on_query_multi_done = ConnectionPanel._on_query_multi_done
    _restore_run_btn = staticmethod(ConnectionPanel._restore_run_btn)
    _extract_table_name = staticmethod(ConnectionPanel._extract_table_name)

    def _emit_health(self, state):
        pass

    def _finalize_query_connection(self, tab):
        pass


def test_multi_done_shows_a_tab_per_write_statement_not_just_selects():
    tab = SqlTab()
    tab._last_query = "UPDATE a SET x=1; UPDATE b SET x=2;"
    panel = _MultiDonePanelStub()

    results = [("UPDATE a SET x=1;", 3, None), ("UPDATE b SET x=2;", 5, None)]
    ConnectionPanel._on_query_multi_done(panel, tab, results, 0.01)

    assert tab._multi_result_bar.count() == 2
    assert tab._multi_results[0][1].iloc[0]["result"] == "3 row(s) affected"
    assert tab._multi_results[1][1].iloc[0]["result"] == "5 row(s) affected"


def test_multi_done_mixes_selects_writes_and_errors_in_their_own_tabs():
    tab = SqlTab()
    tab._last_query = "SELECT 1; UPDATE a SET x=1; SELECT * FROM missing;"
    panel = _MultiDonePanelStub()

    df = pd.DataFrame({"n": [1]})
    err = ValueError("no such table: missing")
    results = [("SELECT 1;", df, None), ("UPDATE a SET x=1;", 2, None), ("SELECT * FROM missing;", err, None)]
    ConnectionPanel._on_query_multi_done(panel, tab, results, 0.02)

    assert tab._multi_result_bar.count() == 3
    assert tab._multi_results[0][1] is df
    assert tab._multi_results[1][1].iloc[0]["result"] == "2 row(s) affected"
    assert tab._multi_results[2][1] is err
