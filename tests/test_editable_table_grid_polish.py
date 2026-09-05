"""Tests for the data-grid polish work in issue #182: NULL-vs-empty-string
rendering, type-aware alignment, fill-down, and column layout persistence.
"""
import os

import numpy as np
import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from ui.editable_table import EditableTableWidget, _IS_NULL_ROLE

_app = QApplication.instance() or QApplication([])


# ── NULL vs empty-string ─────────────────────────────────────────────────

def test_null_cell_is_flagged_but_empty_string_is_not():
    df = pd.DataFrame({"note": [None, ""]})
    table = EditableTableWidget()
    table.load_data(df, table_name="t")

    assert table.item(0, 0).data(_IS_NULL_ROLE) is True
    assert not table.item(1, 0).data(_IS_NULL_ROLE)
    # Both still display as "" — copy/paste and get_changes() are untouched.
    assert table.item(0, 0).text() == ""
    assert table.item(1, 0).text() == ""


def test_set_cell_null_flags_the_cell():
    df = pd.DataFrame({"note": ["hello"]})
    table = EditableTableWidget()
    table.load_data(df, table_name="t")

    table.setCurrentCell(0, 0)
    table.set_cell_null()

    assert table.item(0, 0).text() == ""
    assert table.item(0, 0).data(_IS_NULL_ROLE) is True


def test_set_cell_default_clears_null_flag():
    df = pd.DataFrame({"note": [None]})
    table = EditableTableWidget()
    table.load_data(df, table_name="t")

    table.setCurrentCell(0, 0)
    table.set_cell_default()  # original was None, so this stays blank

    table.item(0, 0).setText("restored")
    assert not table.item(0, 0).data(_IS_NULL_ROLE)


# ── Type-aware alignment ─────────────────────────────────────────────────

def test_numeric_column_right_aligned_text_column_left_aligned():
    df = pd.DataFrame({"amount": [1, 2], "name": ["a", "b"]})
    table = EditableTableWidget()
    table.load_data(df, table_name="t")

    amount_align = table.item(0, 0).textAlignment()
    name_align = table.item(0, 1).textAlignment()
    assert amount_align & Qt.AlignRight
    assert name_align & Qt.AlignLeft


def test_float_column_with_nan_still_right_aligned():
    df = pd.DataFrame({"amount": [1.5, np.nan]})
    table = EditableTableWidget()
    table.load_data(df, table_name="t")

    assert table.item(0, 0).textAlignment() & Qt.AlignRight


# ── Fill-down ─────────────────────────────────────────────────────────────

def test_fill_down_copies_top_row_value_to_rest_of_selection():
    df = pd.DataFrame({"status": ["active", "", ""]})
    table = EditableTableWidget()
    table.load_data(df, table_name="t")

    from PySide6.QtWidgets import QTableWidgetSelectionRange
    table.setRangeSelected(QTableWidgetSelectionRange(0, 0, 2, 0), True)
    table.fill_down()

    assert table.item(0, 0).text() == "active"
    assert table.item(1, 0).text() == "active"
    assert table.item(2, 0).text() == "active"


def test_fill_down_is_one_undo_step():
    df = pd.DataFrame({"status": ["active", "", ""]})
    table = EditableTableWidget()
    table.load_data(df, table_name="t")

    from PySide6.QtWidgets import QTableWidgetSelectionRange
    table.setRangeSelected(QTableWidgetSelectionRange(0, 0, 2, 0), True)
    table.fill_down()
    assert len(table._undo_stack) == 1

    table.undo()
    assert table.item(1, 0).text() == ""
    assert table.item(2, 0).text() == ""


def test_fill_down_single_row_selection_is_a_noop():
    df = pd.DataFrame({"status": ["active"]})
    table = EditableTableWidget()
    table.load_data(df, table_name="t")

    from PySide6.QtWidgets import QTableWidgetSelectionRange
    table.setRangeSelected(QTableWidgetSelectionRange(0, 0, 0, 0), True)
    table.fill_down()

    assert table._undo_stack == []


# ── Column layout persistence ──────────────────────────────────────────────

def test_layout_state_round_trips_widths_order_and_frozen_count():
    df = pd.DataFrame({"a": [1], "b": [2], "c": [3]})
    table = EditableTableWidget()
    table.load_data(df, table_name="t")

    hdr = table.horizontalHeader()
    hdr.resizeSection(0, 222)
    hdr.moveSection(0, 2)  # move column "a" to the last visual slot
    table.set_frozen_columns(1)

    state = table.get_layout_state()
    assert state["frozen"] == 1
    assert state["widths"]["a"] == 222
    assert state["order"][-1] == "a"

    # Reload the same table (as a fresh page/sort/filter refresh would) and
    # restore — order/width/frozen count should come back.
    table.load_data(df.copy(), table_name="t")
    assert table._frozen_col_count == 0
    table.apply_layout_state(state)

    hdr = table.horizontalHeader()
    name_to_logical = {
        table.horizontalHeaderItem(c).text(): c for c in range(table._real_col_count)
    }
    assert hdr.visualIndex(name_to_logical["a"]) == 2
    assert hdr.sectionSize(name_to_logical["a"]) == 222
    assert table._frozen_col_count == 1


def test_layout_state_skips_columns_no_longer_present():
    table = EditableTableWidget()
    table.load_data(pd.DataFrame({"a": [1], "b": [2]}), table_name="t")

    state = {"order": ["b", "a", "gone"], "widths": {"gone": 999}, "frozen": 0}
    table.apply_layout_state(state)  # must not raise

    name_to_logical = {
        table.horizontalHeaderItem(c).text(): c for c in range(table._real_col_count)
    }
    hdr = table.horizontalHeader()
    assert hdr.visualIndex(name_to_logical["b"]) == 0
    assert hdr.visualIndex(name_to_logical["a"]) == 1
