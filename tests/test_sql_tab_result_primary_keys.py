"""SqlTab's editable query-result grid must also key UPDATE/DELETE off the
real primary key, not column 0 — same underlying get_changes() fix as the
Data tab, wired from the schema metadata autocomplete already has (no
extra fetch) via SqlCompleter.primary_key_columns()."""
import os

import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from ui.sql_tab import SqlTab

_app = QApplication.instance() or QApplication([])


def _tab_with_schema() -> SqlTab:
    tab = SqlTab()
    tab.completer.set_schema(
        tables=["employees"],
        columns_dict={"employees": ["dept", "id", "name"]},
        column_details={
            "employees": [
                {"name": "dept", "type": "varchar(50)", "nullable": False, "default": None, "key": ""},
                {"name": "id",   "type": "int",          "nullable": False, "default": None, "key": "PRI"},
                {"name": "name", "type": "varchar(100)",  "nullable": True,  "default": None, "key": ""},
            ],
        },
    )
    return tab


def test_result_grid_gets_real_primary_key_on_load():
    tab = _tab_with_schema()
    df = pd.DataFrame({"dept": ["eng"], "id": [1], "name": ["Alice"]})
    tab.load_dataframe(df, table_name="employees")
    tab._result_view_df = df
    tab._refresh_result_view()

    assert tab.result_table.primary_key_columns == ["id"]


def test_result_grid_update_sql_keys_off_primary_key_not_column_zero():
    tab = _tab_with_schema()
    df = pd.DataFrame({"dept": ["eng", "eng"], "id": [1, 2], "name": ["Alice", "Bob"]})
    tab.load_dataframe(df, table_name="employees")
    tab._result_view_df = df
    tab._refresh_result_view()

    tab.result_table.item(0, 2).setText("Alicia")

    sql = tab.result_table.get_changes()["updates"][0]
    assert "WHERE id = '1'" in sql


def test_no_table_name_leaves_primary_keys_empty():
    tab = _tab_with_schema()
    df = pd.DataFrame({"dept": ["eng"], "id": [1]})
    tab.load_dataframe(df, table_name=None)
    tab._result_view_df = df
    tab._refresh_result_view()

    assert tab.result_table.primary_key_columns == []
