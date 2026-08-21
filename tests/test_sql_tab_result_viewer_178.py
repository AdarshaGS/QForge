"""Tests for issue #178's SqlTab-level pieces: the empty-state
illustration for a successful 0-row query, the result-row icon toolbar
(download/filter wiring), and the bottom status bar (connection state,
dialect, live cursor position, query time/row count)."""
import os

import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from ui.sql_tab import SqlTab

_app = QApplication.instance() or QApplication([])


def _shown_tab():
    tab = SqlTab()
    tab.resize(900, 600)
    tab.show()
    QTest.qWaitForWindowExposed(tab)
    _app.processEvents()
    return tab


# ─── Empty state ──────────────────────────────────────────────────────────────

def test_zero_row_select_with_columns_shows_empty_state_not_blank_grid():
    tab = _shown_tab()
    tab.load_dataframe(pd.DataFrame(columns=["id", "name"]))
    assert tab._empty_state.isVisible()
    assert not tab.result_table.isVisible()
    tab.hide()


def test_nonzero_rows_hides_empty_state_and_shows_grid():
    tab = _shown_tab()
    tab.load_dataframe(pd.DataFrame(columns=["id", "name"]))  # 0 rows first
    assert tab._empty_state.isVisible()

    tab.load_dataframe(pd.DataFrame({"id": [1], "name": ["a"]}))
    assert not tab._empty_state.isVisible()
    assert tab.result_table.isVisible()
    tab.hide()


def test_write_statement_with_no_columns_shows_neither_grid_nor_empty_state():
    tab = _shown_tab()
    tab.load_dataframe(pd.DataFrame())  # no columns — e.g. an INSERT/UPDATE
    assert not tab._empty_state.isVisible()
    assert not tab.result_table.isVisible()
    tab.hide()


# ─── Icon toolbar wiring ──────────────────────────────────────────────────────

def test_download_icon_triggers_export_data(monkeypatch):
    calls = []
    monkeypatch.setattr(SqlTab, "export_data", lambda self: calls.append("export"))
    tab = _shown_tab()
    QTest.mouseClick(tab._download_icon_btn, Qt.LeftButton)
    assert calls == ["export"]
    tab.hide()


def test_filter_icon_triggers_toggle_filter(monkeypatch):
    calls = []
    monkeypatch.setattr(SqlTab, "toggle_filter", lambda self: calls.append("filter"))
    tab = _shown_tab()
    QTest.mouseClick(tab._filter_icon_btn, Qt.LeftButton)
    assert calls == ["filter"]
    tab.hide()


def test_result_actions_bar_shown_on_success_hidden_on_error():
    tab = _shown_tab()
    assert not tab._result_actions_bar.isVisible()

    tab.update_status(3, 0.01)
    assert tab._result_actions_bar.isVisible()

    tab.show_error("ERROR: boom")
    assert not tab._result_actions_bar.isVisible()
    tab.hide()


# ─── Bottom status bar ────────────────────────────────────────────────────────

def test_set_connection_state_updates_readiness_label():
    tab = _shown_tab()
    tab.set_connection_state("running")
    assert "Running" in tab._readiness_lbl.text()

    tab.set_connection_state("disconnected")
    assert "Disconnected" in tab._readiness_lbl.text()

    tab.set_connection_state("idle")
    assert "Ready" in tab._readiness_lbl.text()
    tab.hide()


def test_set_dialect_updates_dialect_label():
    tab = _shown_tab()
    tab.set_dialect("PostgreSQL")
    assert tab._dialect_lbl.text() == "PostgreSQL"
    tab.hide()


def test_cursor_position_label_tracks_the_editor_live():
    tab = _shown_tab()
    tab.editor.setPlainText("SELECT 1\nFROM orders\nWHERE id = 1")
    cursor = tab.editor.textCursor()
    cursor.movePosition(QTextCursor.Start)
    cursor.movePosition(QTextCursor.Down, n=2)
    cursor.movePosition(QTextCursor.Right, n=3)
    tab.editor.setTextCursor(cursor)
    _app.processEvents()
    assert tab._cursor_pos_lbl.text() == "Ln 3, Col 4"
    tab.hide()


def test_update_status_sets_query_time_and_rows_labels():
    tab = _shown_tab()
    tab.update_status(42, 0.123)
    assert tab._query_time_lbl.text() == "Query time: 123 ms"
    assert tab._rows_status_lbl.text() == "Rows: 42"
    tab.hide()


def test_update_status_no_longer_repeats_rows_and_time_above_the_grid():
    """Rows/time now live only in the bottom status bar — showing them a
    second time in status_label was redundant."""
    tab = _shown_tab()
    tab.update_status(96, 0.003)
    assert not tab.status_label.isVisible()
    assert tab.status_label.toPlainText() != "✓  96 rows • 3 ms"
    tab.hide()


def test_update_status_still_warns_on_truncated_results():
    """The one thing status_label is still for: a warning the bottom bar
    has no room to show."""
    tab = _shown_tab()
    tab.update_status(500, 0.02, truncated=True)
    assert tab.status_label.isVisible()
    assert "truncated" in tab.status_label.toPlainText().lower()
    tab.hide()


def test_show_error_sets_rows_to_zero_and_query_time_from_elapsed():
    tab = _shown_tab()
    tab.show_error("ERROR: boom", elapsed=0.05)
    assert tab._query_time_lbl.text() == "Query time: 50 ms"
    assert tab._rows_status_lbl.text() == "Rows: 0"
    tab.hide()
