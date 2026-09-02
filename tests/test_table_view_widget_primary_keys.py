"""TableViewWidget.load_table_data() must pass the table's real primary
key(s) to data_table (EditableTableWidget.set_primary_key_columns()) so
saved edits key off the actual PK instead of assuming column 0 — see
get_changes()'s fix. get_primary_keys() is fetched once and cached, not
re-queried on every page turn."""
import os
import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from ui.table_view_widget import TableViewWidget

_app = QApplication.instance() or QApplication([])


def _pump_until_loaded(w, timeout_ms=5000):
    """load_table_data() now runs its DB work on a background QThread —
    wait for it to finish (via the _loading flag it clears) instead of
    asserting immediately after a call that used to be synchronous."""
    elapsed = 0
    while getattr(w, "_loading", False) and elapsed < timeout_ms:
        QTest.qWait(10)
        elapsed += 10


class FakeDbService:
    db_type = "mysql"

    def __init__(self, rows, primary_keys):
        self._rows = rows
        self._primary_keys = primary_keys
        self.get_primary_keys_calls = 0

    def execute_query(self, query):
        if "COUNT(*)" in query:
            return pd.DataFrame([{"total": len(self._rows)}])
        return self._rows.copy()

    def get_columns(self, table_name):
        return [{"Field": c} for c in self._rows.columns]

    def get_primary_keys(self, table_name):
        self.get_primary_keys_calls += 1
        return list(self._primary_keys)


def test_declared_primary_key_is_applied_to_data_table():
    db = FakeDbService(pd.DataFrame({"dept": ["eng"], "id": [1]}), primary_keys=["id"])
    w = TableViewWidget(db, "employees")
    _pump_until_loaded(w)
    assert w.data_table.primary_key_columns == ["id"]


def test_no_primary_key_leaves_data_table_with_empty_list():
    db = FakeDbService(pd.DataFrame({"a": [1]}), primary_keys=[])
    w = TableViewWidget(db, "no_pk_table")
    _pump_until_loaded(w)
    assert w.data_table.primary_key_columns == []


def test_primary_keys_fetched_once_and_reused_across_page_loads():
    db = FakeDbService(pd.DataFrame({"id": [1]}), primary_keys=["id"])
    w = TableViewWidget(db, "employees")
    _pump_until_loaded(w)
    assert db.get_primary_keys_calls == 1

    w.load_table_data()   # simulate a page turn / refresh
    _pump_until_loaded(w)
    assert db.get_primary_keys_calls == 1
    assert w.data_table.primary_key_columns == ["id"]
