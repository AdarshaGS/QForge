"""Regression test: a raw BLOB column (bytes, left undecoded on purpose —
it's genuinely binary, e.g. a stored encryption key) crashed the entire
grid load. pandas' Series/DataFrame.astype(str) casts via numpy's string
dtype, which tries to UTF-8-decode bytes and raises on the first non-UTF-8
byte — unlike Python's own str(), which just reprs it. Two call sites hit
this: _set_compact_column_widths() (column sizing, runs on every load) and
_apply_all_filters() (the column filter row)."""
import os

import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from ui.editable_table import EditableTableWidget

_app = QApplication.instance() or QApplication([])

# A real encryption key's raw bytes are essentially guaranteed to be
# invalid UTF-8 somewhere — this one fails at position 0, like the
# original bug report.
_BINARY_KEY = b"\xeb\xcf\x1b\xdaQ\xe8\x04\x85"


def _table_with_blob_row():
    df = pd.DataFrame({"id": [1], "name": ["acme"], "tenant_key": [_BINARY_KEY]})
    table = EditableTableWidget()
    table.load_data(df, table_name="tenants")
    return table


def test_load_data_does_not_crash_on_a_raw_blob_column():
    table = _table_with_blob_row()
    assert table.rowCount() == 1
    # Hex, matching TablePlus/DBeaver — not Python's str(bytes) repr.
    assert table.item(0, 2).text() == _BINARY_KEY.hex().upper()


def test_column_filter_does_not_crash_on_a_raw_blob_column():
    table = _table_with_blob_row()
    table.column_filters[1] = "acme"  # filter on the unrelated "name" column
    table._apply_all_filters()
    assert len(table.filtered_data) == 1
