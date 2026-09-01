"""BLOB columns display as uppercase hex (matching TablePlus/DBeaver),
not Python's str(bytes) repr — and, critically, the dirty-state check
(_recompute_cell_dirty_state) must compare against that same hex text.
Comparing against the old repr format would make every BLOB cell look
"modified" immediately on load, reintroducing the bug fixed in
test_editable_table_update_sql_safety.py (an untouched BLOB column getting
resent in every UPDATE's SET clause)."""
import os

import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from ui.editable_table import EditableTableWidget, _cell_display_text

_app = QApplication.instance() or QApplication([])

_KEY = b"\x04\x8f\x89sA\x1eh\x8c"


def test_cell_display_text_hex_encodes_bytes():
    assert _cell_display_text(_KEY) == _KEY.hex().upper() == "048F8973411E688C"
    assert _cell_display_text(5) == "5"
    assert _cell_display_text("abc") == "abc"


def test_loading_a_blob_column_leaves_it_unmodified():
    df = pd.DataFrame({"id": [1], "tenant_key": [_KEY]})
    table = EditableTableWidget()
    table.load_data(df, table_name="tenants")
    table.set_primary_key_columns(["id"])

    assert table.item(0, 1).text() == _KEY.hex().upper()
    assert not table.has_changes()
    assert table.get_changes()["updates"] == []


def test_set_cell_default_restores_hex_text_not_python_repr():
    df = pd.DataFrame({"id": [1], "tenant_key": [_KEY]})
    table = EditableTableWidget()
    table.load_data(df, table_name="tenants")
    table.set_primary_key_columns(["id"])

    table.setCurrentCell(0, 1)
    table.item(0, 1).setText("garbage")
    table.set_cell_default()

    assert table.item(0, 1).text() == _KEY.hex().upper()
    assert not table.has_changes()
