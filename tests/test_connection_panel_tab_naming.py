"""Opening or saving a saved query should name its tab after the query,
not leave it as the generic "Tab N" — a saved query's whole point is to
be findable by name, and that's most useful right on the tab itself."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QInputDialog, QTabWidget

from services import saved_queries as saved_queries_module
from services.saved_queries import SavedQueries
from ui.connection_panel import ConnectionPanel
from ui.query_library_dialog import QueryLibraryDialog
from ui.sql_tab import SqlTab

_app = QApplication.instance() or QApplication([])


def _store(tmp_path, monkeypatch):
    monkeypatch.setattr(saved_queries_module, "_FILE", str(tmp_path / "saved_queries.json"))
    return SavedQueries()


class _PanelStub:
    def __init__(self, tabs, saved_queries=None):
        self.tabs = tabs
        self.saved_queries = saved_queries

    _active_sql_tab = ConnectionPanel._active_sql_tab
    _save_current_query = ConnectionPanel._save_current_query

    def _switch_sidebar(self, index):
        pass


def _panel_with_one_sql_tab(saved_queries=None):
    tabs = QTabWidget()
    sql_tab = SqlTab()
    tabs.addTab(sql_tab, "Tab 1")
    tabs.setCurrentWidget(sql_tab)
    return _PanelStub(tabs, saved_queries), sql_tab


def test_use_saved_query_renames_tab_to_query_name():
    panel, sql_tab = _panel_with_one_sql_tab()

    ConnectionPanel._use_saved_query(panel, {"name": "Top Customers", "query": "SELECT 1"})

    assert panel.tabs.tabText(panel.tabs.indexOf(sql_tab)) == "Top Customers"


def test_use_saved_query_leaves_tab_name_alone_when_entry_has_no_name():
    panel, sql_tab = _panel_with_one_sql_tab()

    ConnectionPanel._use_saved_query(panel, {"name": "", "query": "SELECT 1"})

    assert panel.tabs.tabText(panel.tabs.indexOf(sql_tab)) == "Tab 1"


def test_save_current_query_renames_the_tab_to_the_name_just_given(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    panel, sql_tab = _panel_with_one_sql_tab(saved_queries=store)
    sql_tab.editor.setPlainText("SELECT * FROM orders")

    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Order Report", True)))

    ConnectionPanel._save_current_query(panel)

    assert panel.tabs.tabText(panel.tabs.indexOf(sql_tab)) == "Order Report"
    assert store.queries[0]["name"] == "Order Report"


def test_query_library_dialog_exposes_selected_entrys_name(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    store.add("Monthly Revenue", "SELECT SUM(total) FROM invoices")

    dialog = QueryLibraryDialog(store)
    dialog.use_selected_query()

    assert dialog.get_selected_name() == "Monthly Revenue"
    assert dialog.get_selected_query() == "SELECT SUM(total) FROM invoices"
