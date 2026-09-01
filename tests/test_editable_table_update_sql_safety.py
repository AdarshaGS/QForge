"""Regression tests: editing one column in a row that also has an untouched
BLOB column broke the UPDATE two ways at once.

1. get_changes() rebuilt the SET clause from *every* column in the row,
   including untouched ones — so a raw bytes value (e.g. a stored
   encryption key, never decoded since it's genuinely binary) got resent
   as its Python str(bytes) repr, a string full of backslashes. MySQL
   syntax error, and the actually-edited columns were never saved.
2. Even for plain text, the quoting helper only escaped the quote
   character, not backslash — MySQL treats backslash as an escape char
   inside '...' by default, so e.g. saving 'C:\\Users\\test' silently
   stored 'C:Users\\test' with no error at all.
"""
import os

import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from ui.editable_table import EditableTableWidget, _sql_string_literal

_app = QApplication.instance() or QApplication([])


def test_sql_string_literal_escapes_backslash_and_quote():
    assert _sql_string_literal(r"C:\Users\test") == r"'C:\\Users\\test'"
    assert _sql_string_literal("O'Brien") == "'O''Brien'"


def test_update_only_includes_actually_edited_columns():
    df = pd.DataFrame({
        "id": [1],
        "oltp_id": [4],
        "tenant_key": [b"\xeb\xcf\x1b\xdaQ\xe8\x04\x85"],
    })
    table = EditableTableWidget()
    table.load_data(df, table_name="tenants")
    table.set_primary_key_columns(["id"])

    table.item(0, 1).setText("99")  # edit oltp_id only

    sql = table.get_changes()["updates"][0]
    assert sql == "UPDATE tenants SET oltp_id = '99' WHERE id = '1';"
    assert "tenant_key" not in sql


def test_update_escapes_backslashes_in_an_edited_value():
    df = pd.DataFrame({"id": [1], "path": ["old"]})
    table = EditableTableWidget()
    table.load_data(df, table_name="files")
    table.set_primary_key_columns(["id"])

    table.item(0, 1).setText(r"C:\Users\test")

    sql = table.get_changes()["updates"][0]
    assert r"path = 'C:\\Users\\test'" in sql
