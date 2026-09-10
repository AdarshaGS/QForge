"""Regression test: loading a page containing a nullable-Int64 column with
at least one NULL crashed EditableTableWidget.load_data() with
"TypeError: Invalid value '' for dtype 'Int64'" inside
_set_compact_column_widths(), which sampled the column via
`.fillna('').map(str)` — filling a pandas nullable-integer array with the
string '' raises, since '' isn't a valid Int64 value. Nullable Int64
columns became routine after issue #279 (MySQL integer columns with any
NULL are cast to Int64 instead of promoting to float64), so any table page
with a nullable int column and a NULL row hit this on every load.
"""
import os

import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from ui.editable_table import EditableTableWidget

_app = QApplication.instance() or QApplication([])


def test_loading_a_nullable_int_column_with_a_null_does_not_crash():
    df = pd.DataFrame({"id": pd.array([1, 2, None], dtype="Int64"), "name": ["a", "b", "c"]})
    table = EditableTableWidget()
    table.load_data(df, table_name="widgets")  # must not raise
    assert table.item(2, 0).text() in ("", "None")
