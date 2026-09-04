"""Tests for EditableTableWidget's "search any column" quick filter,
shared by the table data view (ui/table_view_widget.py) and the SQL
result editor (ui/sql_tab.py) since both use this same widget class."""
import os

import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from ui.editable_table import EditableTableWidget

_app = QApplication.instance() or QApplication([])


def _table_with(rows):
    table = EditableTableWidget()
    table.load_data(pd.DataFrame(rows))
    return table


def _visible_rows(table):
    return table.filtered_data.reset_index(drop=True)


def test_quick_filter_matches_any_column():
    table = _table_with([
        {"name": "orders", "note": ""},
        {"name": "users", "note": "orders reference this"},
        {"name": "products", "note": "unrelated"},
    ])

    table.set_quick_filter("orders")

    visible = _visible_rows(table)
    assert set(visible["name"]) == {"orders", "users"}


def test_quick_filter_is_case_insensitive():
    table = _table_with([{"name": "Orders"}, {"name": "products"}])
    table.set_quick_filter("ORDERS")
    assert list(_visible_rows(table)["name"]) == ["Orders"]


def test_quick_filter_empty_shows_everything():
    table = _table_with([{"name": "orders"}, {"name": "users"}])
    table.set_quick_filter("orders")
    table.set_quick_filter("")
    assert len(_visible_rows(table)) == 2


def test_quick_filter_ands_with_column_filters():
    table = _table_with([
        {"name": "orders", "status": "open"},
        {"name": "orders_archive", "status": "closed"},
    ])

    table.apply_column_filter(1, "open")   # status contains "open"
    table.set_quick_filter("orders")       # any column contains "orders"

    visible = _visible_rows(table)
    assert list(visible["name"]) == ["orders"]


def test_quick_filter_resets_on_reload():
    table = _table_with([{"name": "orders"}])
    table.set_quick_filter("orders")
    assert table.quick_filter_text == "orders"

    table.load_data(pd.DataFrame([{"name": "users"}]))

    assert table.quick_filter_text == ""
    assert len(_visible_rows(table)) == 1


def test_get_filter_status_reports_quick_filter():
    table = _table_with([{"name": "orders"}])
    assert table.get_filter_status() == ""

    table.set_quick_filter("orders")
    assert 'search: "orders"' in table.get_filter_status()

    table.apply_column_filter(0, "ord")
    status = table.get_filter_status()
    assert "column filter" in status
    assert "search:" in status
