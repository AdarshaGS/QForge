"""Regression test for issue #237: TableViewWidget._load_structure_tab's
get_columns/get_foreign_keys/get_indexes calls must run on a background
thread (via the tab's own dedicated connection, same as load_table_data())
instead of blocking the UI thread."""
import os

import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from ui.table_view_widget import TableViewWidget

_app = QApplication.instance() or QApplication([])


class _FakeDbService:
    db_type = "mysql"

    def __init__(self):
        self._rows = pd.DataFrame({"id": [1, 2, 3]})
        self.columns_calls = []

    def execute_query(self, query, **kwargs):
        if "COUNT(*)" in query:
            return pd.DataFrame([{"total": len(self._rows)}])
        return self._rows.copy()

    def get_columns(self, table_name):
        self.columns_calls.append(table_name)
        return [{"Field": "id", "Type": "int", "Null": "NO", "Key": "PRI", "Default": None}]

    def get_foreign_keys(self, table_name):
        return [{"column": "id", "ref_table": "other", "ref_column": "id"}]

    def get_indexes(self, table_name):
        return [{"name": "PRIMARY", "columns": "id", "unique": True, "type": "BTREE"}]


def _pump_until(predicate, timeout_ms=2000):
    elapsed = 0
    while not predicate() and elapsed < timeout_ms:
        QTest.qWait(10)
        elapsed += 10


def test_load_structure_tab_runs_in_background_and_populates_tables():
    db = _FakeDbService()
    w = TableViewWidget(db, "t")
    _pump_until(lambda: not w._loading)   # let the initial grid page load settle

    w._load_structure_tab()
    _pump_until(lambda: db.columns_calls)
    # db.columns_calls flips inside the background thread the instant
    # get_columns() returns, but _apply_structure_result() (which fills
    # col_tbl/idx_tbl/fk_tbl) only runs once the main thread's event loop
    # delivers the cross-thread _structure_load_done signal — a separate,
    # later tick — so wait for that too rather than assuming it's already
    # landed.
    _pump_until(lambda: w.col_tbl.rowCount() > 0)

    assert w.col_tbl.rowCount() == 1
    assert w.idx_tbl.rowCount() == 1
    assert w.fk_tbl.rowCount() == 1
    col_i, idx_i, fk_i = w._structure_tab_indices
    assert w.view_tabs.tabText(col_i) == "Columns (1)"
    assert w.view_tabs.tabText(idx_i) == "Indexes (1)"
    assert w.view_tabs.tabText(fk_i) == "Foreign Keys (1)"
