"""Tests for the "Raw SQL" pseudo-column in the filter row's column dropdown.

Replaces the old standalone "search all columns" box (previously its own
QLineEdit, tests/test_quick_filter_search_box_wiring.py) with an entry in
the same column dropdown used by every other filter condition — so there is
one filter mechanism, not two. Picking "Raw SQL" as the column and typing a
value searches every column for that value instead of one specific column:

- table_view_widget.py queries the DB directly, so it builds a real SQL
  WHERE fragment: (col1 LIKE '%v%' OR col2 LIKE '%v%' OR ...).
- sql_tab.py's results can come from arbitrary user SQL (joins,
  aggregates, ...) with no live table to re-query, so it does the same
  cross-column matching client-side against the already-loaded DataFrame,
  the same substring-matching mechanism the old quick_search box used.
"""
import os

import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit
from PySide6.QtTest import QTest

from ui.table_view_widget import TableViewWidget

_app = QApplication.instance() or QApplication([])


class FakeDbService:
    db_type = "mysql"

    def execute_query(self, query):
        if "COUNT(*)" in query:
            return pd.DataFrame([{"total": 1}])
        return pd.DataFrame({"id": [1], "name": ["orders widget"], "status": ["active"]})

    def get_columns(self, table_name):
        return [{"Field": "id"}, {"Field": "name"}, {"Field": "status"}]


def _pump_until_loaded(w, timeout_ms=5000):
    elapsed = 0
    while getattr(w, "_loading", False) and elapsed < timeout_ms:
        QTest.qWait(10)
        elapsed += 10


def _first_row_widgets(filter_rows_layout):
    row = filter_rows_layout.itemAt(0).widget()
    return (
        row.findChild(QComboBox, "column_combo"),
        row.findChild(QComboBox, "operator_combo"),
        row.findChild(QLineEdit, "value_input"),
    )


# ─── table_view_widget.py ───────────────────────────────────────────────────


def test_no_standalone_quick_search_box_remains():
    w = TableViewWidget(FakeDbService(), "widgets")
    assert not hasattr(w, "quick_search")


def test_raw_sql_is_the_first_column_dropdown_entry():
    w = TableViewWidget(FakeDbService(), "widgets")
    _pump_until_loaded(w)
    col_combo, _, _ = _first_row_widgets(w.filter_rows_layout)
    assert col_combo.itemText(0) == "Raw SQL"
    assert set(col_combo.itemText(i) for i in range(1, col_combo.count())) == {
        "id", "name", "status"
    }


def test_selecting_raw_sql_disables_the_operator_dropdown():
    w = TableViewWidget(FakeDbService(), "widgets")
    _pump_until_loaded(w)
    col_combo, op_combo, _ = _first_row_widgets(w.filter_rows_layout)

    col_combo.setCurrentText("id")
    assert op_combo.isEnabled()

    col_combo.setCurrentText("Raw SQL")
    assert not op_combo.isEnabled()


def test_raw_sql_filter_builds_an_or_clause_across_every_column():
    w = TableViewWidget(FakeDbService(), "widgets")
    _pump_until_loaded(w)
    col_combo, _, value_input = _first_row_widgets(w.filter_rows_layout)

    col_combo.setCurrentText("Raw SQL")
    value_input.setText("orders")
    w.apply_all_filters()

    assert w.current_filter == (
        "(id LIKE '%orders%' OR name LIKE '%orders%' OR status LIKE '%orders%')"
    )


def test_raw_sql_filter_escapes_single_quotes():
    w = TableViewWidget(FakeDbService(), "widgets")
    _pump_until_loaded(w)
    col_combo, _, value_input = _first_row_widgets(w.filter_rows_layout)

    col_combo.setCurrentText("Raw SQL")
    value_input.setText("o'brien")
    w.apply_all_filters()

    assert "o''brien" in w.current_filter
    assert "o'brien" not in w.current_filter.replace("o''brien", "")


def test_raw_sql_filter_with_empty_value_is_skipped():
    w = TableViewWidget(FakeDbService(), "widgets")
    _pump_until_loaded(w)
    col_combo, _, value_input = _first_row_widgets(w.filter_rows_layout)

    col_combo.setCurrentText("Raw SQL")
    value_input.setText("")
    w.apply_all_filters()

    assert w.current_filter == ""


# ─── sql_tab.py ──────────────────────────────────────────────────────────────


def test_sql_tab_has_no_standalone_quick_search_box():
    from ui.sql_tab import SqlTab

    tab = SqlTab()
    assert not hasattr(tab, "quick_search")


def test_sql_tab_raw_sql_is_the_first_column_dropdown_entry():
    from ui.sql_tab import SqlTab

    tab = SqlTab()
    col_combo, _, _ = _first_row_widgets(tab.filter_rows_layout)
    assert col_combo.itemText(0) == "Raw SQL"


def test_sql_tab_raw_sql_filter_matches_across_every_column():
    from ui.sql_tab import SqlTab

    tab = SqlTab()
    df = pd.DataFrame({
        "id": [1, 2, 3],
        "name": ["orders widget", "gadget", "widget"],
        "status": ["active", "orders-pending", "active"],
    })
    tab.original_df = df.copy()
    tab.current_df = df.copy()
    tab._result_view_df = df.copy()

    col_combo, _, value_input = _first_row_widgets(tab.filter_rows_layout)
    col_combo.setCurrentText("Raw SQL")
    value_input.setText("orders")
    tab.apply_all_filters()

    assert list(tab._result_view_df["id"]) == [1, 2]


def test_sql_tab_raw_sql_filter_is_case_insensitive():
    from ui.sql_tab import SqlTab

    tab = SqlTab()
    df = pd.DataFrame({"id": [1, 2], "name": ["ORDERS", "gadget"]})
    tab.original_df = df.copy()
    tab.current_df = df.copy()
    tab._result_view_df = df.copy()

    col_combo, _, value_input = _first_row_widgets(tab.filter_rows_layout)
    col_combo.setCurrentText("Raw SQL")
    value_input.setText("orders")
    tab.apply_all_filters()

    assert list(tab._result_view_df["id"]) == [1]
