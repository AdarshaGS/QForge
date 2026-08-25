"""Tests for EditableTableWidget.get_changes()'s WHERE-clause column
selection. get_changes() used to hardcode "column 0 is the primary key"
for both UPDATE and DELETE WHERE clauses — on any table where that
convention didn't hold (PK not first, composite PK, no PK at all, or an
edited query-result grid whose first column isn't a key), a saved edit
could silently affect zero rows (looks like "nothing happened") or more
than one (wrong rows changed), depending on whether column 0's values
happened to be unique. Fixed by using the table's real primary-key
column(s) when known (set_primary_key_columns()), falling back to
matching on every column — never a single assumed column — when not."""
import os

import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from ui.editable_table import EditableTableWidget

_app = QApplication.instance() or QApplication([])


def _edit_cell(table: EditableTableWidget, row: int, col: int, new_text: str):
    table.item(row, col).setText(new_text)


def test_update_where_clause_uses_declared_primary_key_not_column_zero():
    """Primary key is `id` (column 1, not column 0) — the WHERE clause
    must key off id, not the non-unique `dept` column that happens to sit
    first."""
    df = pd.DataFrame({"dept": ["eng", "eng"], "id": [1, 2], "name": ["Alice", "Bob"]})
    table = EditableTableWidget()
    table.load_data(df, table_name="employees")
    table.set_primary_key_columns(["id"])

    _edit_cell(table, 0, 2, "Alicia")

    changes = table.get_changes()
    assert len(changes["updates"]) == 1
    sql = changes["updates"][0]
    assert "WHERE id = '1'" in sql
    assert "dept" not in sql.split("WHERE")[1]


def test_update_where_clause_falls_back_to_all_columns_when_pk_unknown():
    """No primary_key_columns set (e.g. table has no PK, or the caller
    never resolved one) — every column goes into WHERE rather than
    guessing column 0, so the match stays as specific as the data allows."""
    df = pd.DataFrame({"a": [1], "b": [2], "c": [3]})
    table = EditableTableWidget()
    table.load_data(df, table_name="no_pk_table")
    # primary_key_columns left at its post-load_data default: []

    _edit_cell(table, 0, 2, "99")

    changes = table.get_changes()
    where = changes["updates"][0].split("WHERE")[1]
    assert "a = '1'" in where
    assert "b = '2'" in where
    assert "c = '3'" in where


def test_composite_primary_key_uses_all_key_columns():
    df = pd.DataFrame({"org_id": [1], "user_id": [2], "role": ["member"]})
    table = EditableTableWidget()
    table.load_data(df, table_name="membership")
    table.set_primary_key_columns(["org_id", "user_id"])

    _edit_cell(table, 0, 2, "admin")

    where = table.get_changes()["updates"][0].split("WHERE")[1]
    assert "org_id = '1'" in where
    assert "user_id = '2'" in where
    assert "role" not in where


def test_editing_the_primary_key_column_itself_matches_on_old_value():
    """The WHERE clause must use the row's original id, not the new one
    just typed, or it would match nothing (the old row no longer has that
    id) and the edit would silently no-op."""
    df = pd.DataFrame({"id": [1], "name": ["Alice"]})
    table = EditableTableWidget()
    table.load_data(df, table_name="users")
    table.set_primary_key_columns(["id"])

    _edit_cell(table, 0, 0, "100")

    sql = table.get_changes()["updates"][0]
    assert "SET id = '100'" in sql
    assert "WHERE id = '1'" in sql


def test_delete_where_clause_uses_primary_key_not_column_zero():
    df = pd.DataFrame({"dept": ["eng"], "id": [1], "name": ["Alice"]})
    table = EditableTableWidget()
    table.load_data(df, table_name="employees")
    table.set_primary_key_columns(["id"])

    table.deleted_rows.add(0)

    sql = table.get_changes()["deletes"][0]
    assert sql == "DELETE FROM employees WHERE id = '1';"


def test_pk_column_missing_from_grid_falls_back_to_all_columns():
    """The declared PK isn't among this result set's columns (e.g. a
    hand-written SELECT that left it out) — falling back to every column
    present is safer than a WHERE clause referencing a column that
    doesn't exist in the query result."""
    df = pd.DataFrame({"name": ["Alice"], "dept": ["eng"]})
    table = EditableTableWidget()
    table.load_data(df, table_name="employees")
    table.set_primary_key_columns(["id"])   # not a column in this result set

    _edit_cell(table, 0, 1, "sales")

    where = table.get_changes()["updates"][0].split("WHERE")[1]
    assert "name = 'Alice'" in where
    assert "dept = 'eng'" in where


def test_where_clause_escapes_single_quotes_in_original_value():
    """The WHERE clause's original-value literal previously wasn't
    escaped (only the new SET value was) — a key value containing an
    apostrophe would break the generated SQL's syntax."""
    df = pd.DataFrame({"id": ["o'brien"], "name": ["Pat"]})
    table = EditableTableWidget()
    table.load_data(df, table_name="users")
    table.set_primary_key_columns(["id"])

    _edit_cell(table, 0, 1, "Patrick")

    sql = table.get_changes()["updates"][0]
    assert "id = 'o''brien'" in sql


def test_new_load_resets_primary_key_columns():
    """A fresh load_data() (different table/query) must not keep a stale
    PK from whatever was loaded before it."""
    table = EditableTableWidget()
    table.load_data(pd.DataFrame({"id": [1]}), table_name="t1")
    table.set_primary_key_columns(["id"])

    table.load_data(pd.DataFrame({"x": [1]}), table_name="t2")
    assert table.primary_key_columns == []
