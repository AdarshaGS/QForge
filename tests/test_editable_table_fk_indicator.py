"""Regression test for issue #261: FK columns had no visible indicator in
the data/result grid header, so the existing right-click "Go to
ref_table.column" navigation (see navigate_fk) was undiscoverable."""
import os

import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from ui.editable_table import EditableTableWidget

_app = QApplication.instance() or QApplication([])


def _table_with_fk():
    df = pd.DataFrame({"id": [1], "org_id": [10], "name": ["acme"]})
    table = EditableTableWidget()
    table.load_data(df, table_name="users")
    table.set_fk_map([
        {"column": "org_id", "ref_table": "organisation", "ref_column": "id"},
    ])
    return table


def test_fk_column_reports_fk_info_by_logical_index():
    table = _table_with_fk()

    fk = table._fk_info_for_column(1)  # org_id
    assert fk is not None
    assert fk["ref_table"] == "organisation"
    assert fk["ref_column"] == "id"


def test_non_fk_columns_report_no_fk_info():
    table = _table_with_fk()

    assert table._fk_info_for_column(0) is None   # id
    assert table._fk_info_for_column(2) is None   # name


def test_empty_fk_map_is_the_default():
    df = pd.DataFrame({"org_id": [10]})
    table = EditableTableWidget()
    table.load_data(df, table_name="users")

    assert table._fk_info_for_column(0) is None


def test_set_fk_map_repaints_header_without_error():
    table = _table_with_fk()
    # Just exercising the paint path shouldn't raise even without a shown window.
    table.horizontalHeader().viewport().repaint()


def test_fk_cell_underlying_text_is_unaffected_by_the_nav_arrow():
    """The inline "→" (issue #261) is paint-only — item.text(), which
    copy/paste, dirty-diffing, and get_changes() all read, must stay the
    real cell value with no arrow appended."""
    table = _table_with_fk()

    assert table.item(0, 1).text() == "10"


def _press(table, pos):
    event = QMouseEvent(
        QEvent.MouseButtonPress, pos, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier
    )
    table.mousePressEvent(event)


def test_click_on_fk_arrow_zone_navigates_to_ref_table():
    table = _table_with_fk()
    received = []
    table.navigate_fk.connect(lambda *args: received.append(args))

    rect = table.visualRect(table.model().index(0, 1))  # org_id cell
    _press(table, QPoint(rect.right() - 2, rect.center().y()))

    assert received == [("organisation", "id", "10")]


def test_click_elsewhere_in_fk_cell_does_not_navigate():
    """Only the reserved arrow zone at the cell's right edge triggers
    navigation — clicking the value text itself just selects, like any
    other cell."""
    table = _table_with_fk()
    received = []
    table.navigate_fk.connect(lambda *args: received.append(args))

    rect = table.visualRect(table.model().index(0, 1))  # org_id cell
    _press(table, QPoint(rect.left() + 2, rect.center().y()))

    assert received == []
    assert table.currentItem() is table.item(0, 1)  # normal selection still happens


def test_click_on_non_fk_cell_arrow_zone_does_not_navigate():
    table = _table_with_fk()
    received = []
    table.navigate_fk.connect(lambda *args: received.append(args))

    rect = table.visualRect(table.model().index(0, 0))  # id column, no FK
    _press(table, QPoint(rect.right() - 2, rect.center().y()))

    assert received == []
