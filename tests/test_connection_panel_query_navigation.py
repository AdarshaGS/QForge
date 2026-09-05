"""Regression tests for issue #175 — opening a saved query (or history
entry) while a table Data/Structure view was active silently wrote into a
background SQL tab instead of bringing it to front, and doing so always
snapped the sidebar back to Schema even when the user was browsing
Queries/History."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QListWidgetItem, QTabWidget, QWidget

from ui.connection_panel import ConnectionPanel
from ui.sql_tab import SqlTab

_app = QApplication.instance() or QApplication([])


class _PanelStub:
    """Just enough of ConnectionPanel for _active_sql_tab()/_use_saved_query()/
    _use_history_item() to operate on, plus a _switch_sidebar() spy so tests
    can assert it's never called by these methods (issue #175)."""

    def __init__(self, tabs):
        self.tabs = tabs
        self.sidebar_switches = []

    _active_sql_tab = ConnectionPanel._active_sql_tab

    def _switch_sidebar(self, index):
        self.sidebar_switches.append(index)


def _panel_with_table_view_active_and_sql_tab_in_background():
    """Mirrors the bug repro: a table Data/Structure view (any non-SqlTab
    widget) is the current tab, with a real SqlTab sitting elsewhere."""
    tabs = QTabWidget()
    sql_tab = SqlTab()
    tabs.addTab(sql_tab, "Query 1")
    table_view = QWidget()
    tabs.addTab(table_view, "orders — Data")
    tabs.setCurrentWidget(table_view)
    return _PanelStub(tabs), sql_tab, table_view


def test_active_sql_tab_brings_a_background_sql_tab_to_front():
    panel, sql_tab, table_view = _panel_with_table_view_active_and_sql_tab_in_background()
    assert panel.tabs.currentWidget() is table_view  # pre-fix: Data view active

    result = ConnectionPanel._active_sql_tab(panel)

    assert result is sql_tab
    assert panel.tabs.currentWidget() is sql_tab


def test_use_saved_query_opens_into_view_when_data_view_is_active():
    panel, sql_tab, table_view = _panel_with_table_view_active_and_sql_tab_in_background()

    ConnectionPanel._use_saved_query(panel, {"query": "SELECT * FROM orders"})

    assert panel.tabs.currentWidget() is sql_tab
    assert sql_tab.get_query() == "SELECT * FROM orders"


def test_use_saved_query_does_not_force_sidebar_back_to_schema():
    panel, sql_tab, table_view = _panel_with_table_view_active_and_sql_tab_in_background()

    ConnectionPanel._use_saved_query(panel, {"query": "SELECT 1"})

    assert panel.sidebar_switches == []


def test_use_history_item_does_not_force_sidebar_back_to_schema():
    panel, sql_tab, table_view = _panel_with_table_view_active_and_sql_tab_in_background()
    item = QListWidgetItem("SELECT 1")
    item.setData(Qt.UserRole, {"query": "SELECT 1"})

    ConnectionPanel._use_history_item(panel, item)

    assert panel.tabs.currentWidget() is sql_tab
    assert sql_tab.get_query() == "SELECT 1"
    assert panel.sidebar_switches == []


def test_use_saved_query_reuses_the_already_active_sql_tab_without_switching():
    tabs = QTabWidget()
    sql_tab = SqlTab()
    tabs.addTab(sql_tab, "Query 1")
    tabs.setCurrentWidget(sql_tab)
    panel = _PanelStub(tabs)

    ConnectionPanel._use_saved_query(panel, {"query": "SELECT 2"})

    assert panel.tabs.currentWidget() is sql_tab
    assert sql_tab.get_query() == "SELECT 2"
