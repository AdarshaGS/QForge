"""Tests that the "search any column" quick filter box is present and
wired to EditableTableWidget.set_quick_filter in both places it's
supposed to appear: the table data view and the SQL result editor."""
import os

import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QLineEdit

from ui.table_view_widget import TableViewWidget

_app = QApplication.instance() or QApplication([])


class FakeDbService:
    db_type = "mysql"

    def execute_query(self, query):
        if "COUNT(*)" in query:
            return pd.DataFrame([{"total": 1}])
        return pd.DataFrame({"id": [1]})

    def get_columns(self, table_name):
        return [{"Field": "id"}]


def test_table_view_has_a_quick_search_box_wired_to_the_grid():
    w = TableViewWidget(FakeDbService(), "widgets")

    assert isinstance(w.quick_search, QLineEdit)
    assert "search" in w.quick_search.placeholderText().lower()

    w.quick_search.setText("orders")
    assert w.data_table.quick_filter_text == "orders"


def test_sql_tab_has_a_quick_search_box_wired_to_the_result_grid():
    from ui.sql_tab import SqlTab

    tab = SqlTab()

    assert isinstance(tab.quick_search, QLineEdit)
    assert "search" in tab.quick_search.placeholderText().lower()
    assert tab.quick_search.isHidden()  # no results yet

    tab.quick_search.setText("orders")
    assert tab.result_table.quick_filter_text == "orders"
