"""End-to-end test for issue #252: a hidden column persists via
services/grid_layout.py and is restored on a freshly opened table view.
"""
import os

import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from services import grid_layout
from ui.table_view_widget import TableViewWidget

_app = QApplication.instance() or QApplication([])


def _pump_until_loaded(w, timeout_ms=5000):
    elapsed = 0
    while getattr(w, "_loading", False) and elapsed < timeout_ms:
        QTest.qWait(10)
        elapsed += 10


class FakeDbService:
    db_type = "mysql"

    def execute_query(self, query):
        if "COUNT(*)" in query:
            return pd.DataFrame([{"total": 3}])
        return pd.DataFrame({"id": [1, 2, 3], "name": ["a", "b", "c"], "dept": ["x", "y", "z"]})

    def get_columns(self, table_name):
        return [{"Field": "id"}, {"Field": "name"}, {"Field": "dept"}]


@pytest.fixture(autouse=True)
def _isolated_grid_layout_file(tmp_path, monkeypatch):
    monkeypatch.setattr(grid_layout, "_FILE", str(tmp_path / "grid_layout.json"))


def _widget():
    config = {"id": "conn1", "database": "db1"}
    w = TableViewWidget(FakeDbService(), "widgets", config)
    _pump_until_loaded(w)
    return w


def test_hidden_column_persists_across_a_fresh_table_view():
    w1 = _widget()
    w1.data_table.hide_column("dept")
    QTest.qWait(600)  # debounce timer in EditableTableWidget._schedule_layout_save

    w2 = _widget()
    assert w2.data_table._hidden_columns == {"dept"}
    logical = w2.data_table._logical_index_for_column_name("dept")
    assert w2.data_table.isColumnHidden(logical)
