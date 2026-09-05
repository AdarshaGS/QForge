"""Regression tests for issue #253: the grid showed a horizontal scrollbar
even when all real columns already fit the visible width, because filler
columns (see ui/editable_table.py's _EMPTY_PLACEHOLDER_COLUMNS) were always
padded out to a fixed 30 * _COL_WIDTH_DEF budget regardless of the actual
viewport width.
"""
import os

import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QMainWindow

from ui.editable_table import EditableTableWidget

_app = QApplication.instance() or QApplication([])


def _shown_widget(df, width=900, height=400):
    win = QMainWindow()
    t = EditableTableWidget()
    t.load_data(df, table_name="demo")
    win.setCentralWidget(t)
    win.resize(width, height)
    win.show()
    _app.processEvents()
    return win, t


def test_no_scrollbar_when_columns_fit_the_viewport():
    df = pd.DataFrame({"id": [1, 2, 3], "name": ["a", "b", "c"], "dept": ["x", "y", "z"]})
    win, t = _shown_widget(df, width=900)

    assert t.horizontalScrollBar().maximum() == 0
    assert not t.horizontalScrollBar().isVisible()
    # Total column width (real + filler) should exactly match the
    # viewport, not the old fixed filler budget far past it.
    hdr = t.horizontalHeader()
    assert hdr.length() == t.viewport().width()


def test_scrollbar_appears_and_is_scoped_to_real_overflow():
    df = pd.DataFrame({f"col_{i}": [f"a fairly long value {i}"] * 3 for i in range(10)})
    win, t = _shown_widget(df, width=900)

    hdr = t.horizontalHeader()
    real_w = sum(hdr.sectionSize(c) for c in range(t._real_col_count))
    assert real_w > t.viewport().width()  # sanity: this dataset does overflow

    assert t.horizontalScrollBar().isVisible()
    assert t.horizontalScrollBar().maximum() > 0
    # No filler was appended past the real, overflowing columns.
    assert hdr.length() == real_w


def test_resizing_narrower_introduces_scrollbar_live():
    df = pd.DataFrame({"id": [1, 2, 3], "name": ["aaaaaaaaaa", "b", "c"], "dept": ["xxxxxxxxxx", "y", "z"]})
    win, t = _shown_widget(df, width=900)
    assert not t.horizontalScrollBar().isVisible()

    win.resize(200, 400)
    _app.processEvents()
    assert t.horizontalScrollBar().isVisible()
    assert t.horizontalScrollBar().maximum() > 0


def test_resizing_back_wider_removes_scrollbar_live():
    df = pd.DataFrame({"id": [1, 2, 3], "name": ["aaaaaaaaaa", "b", "c"], "dept": ["xxxxxxxxxx", "y", "z"]})
    win, t = _shown_widget(df, width=200)
    assert t.horizontalScrollBar().isVisible()

    win.resize(900, 400)
    _app.processEvents()
    assert not t.horizontalScrollBar().isVisible()
    assert t.horizontalScrollBar().maximum() == 0


def test_widening_a_real_column_past_the_viewport_introduces_scrollbar():
    """Dragging a real column wider (not just a window resize) can also
    cross the fit/overflow boundary. Asserts on maximum() rather than
    isVisible() — the offscreen QPA platform used for headless tests
    doesn't reliably flip a scrollbar's visibility from a single
    resizeSection() the way a real window resize does, but maximum() is
    the authoritative "does this need to scroll" signal either way."""
    df = pd.DataFrame({"id": [1, 2, 3], "name": ["a", "b", "c"]})
    win, t = _shown_widget(df, width=900)
    assert t.horizontalScrollBar().maximum() == 0

    t.horizontalHeader().resizeSection(0, 950)  # wider than the whole viewport alone
    _app.processEvents()
    assert t.horizontalScrollBar().maximum() > 0
    hdr = t.horizontalHeader()
    real_w = sum(hdr.sectionSize(c) for c in range(t._real_col_count))
    assert hdr.length() == real_w  # no filler left once real content overflows


def test_no_serial_number_column_is_introduced():
    df = pd.DataFrame({"id": [1, 2, 3]})
    win, t = _shown_widget(df, width=900)
    assert t.verticalHeader().isHidden()
    assert t._real_col_count == 1


def test_grid_lines_still_shown():
    df = pd.DataFrame({"id": [1, 2, 3]})
    win, t = _shown_widget(df, width=900)
    assert t.showGrid()


def test_empty_filler_range_is_a_noop():
    """_size_filler_columns() can be called before any data has ever been
    loaded (e.g. from an early resizeEvent) — must not raise."""
    t = EditableTableWidget()
    t._size_filler_columns()  # no exception
