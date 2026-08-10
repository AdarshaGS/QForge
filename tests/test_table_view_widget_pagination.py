"""Regression test for issue #111: table data present but not shown.

Two bugs in TableViewWidget.load_table_data():
1. Filtered loads never executed the COUNT query, leaving total_rows as
   None -> `None + page_size` crashed with a TypeError.
2. Unfiltered loads trusted MySQL's approximate TABLE_ROWS stat (which can
   read 0 right after inserts) and discarded the real, already-fetched
   DataFrame, showing "No data - 0 rows" despite rows being present.
"""
import os
import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from ui.table_view_widget import TableViewWidget

_app = QApplication.instance() or QApplication([])


class FakeDbService:
    db_type = "mysql"

    def __init__(self, real_rows, filtered_count=None):
        self._real_rows = real_rows
        self._filtered_count = filtered_count

    def execute_query(self, query):
        if "TABLE_ROWS" in query:
            # Stale/approximate count: reports 0 even though rows exist.
            return pd.DataFrame([{"TABLE_ROWS": 0}])
        if "COUNT(*)" in query and "WHERE" in query:
            return pd.DataFrame([{"total": self._filtered_count}])
        if "COUNT(*)" in query:
            return pd.DataFrame([{"total": len(self._real_rows)}])
        return self._real_rows.copy()

    def get_columns(self, table_name):
        return [{"Field": c} for c in self._real_rows.columns]


def _widget(db_service):
    return TableViewWidget(db_service, "permissions")


def test_unfiltered_load_keeps_real_rows_despite_stale_zero_count():
    real_rows = pd.DataFrame({"id": [1, 2, 3], "code": ["A", "B", "C"]})
    w = _widget(FakeDbService(real_rows))

    assert w.data_table.rowCount() == 3
    assert "No data" not in w.limit_label.text()
    assert w.total_rows > 0


def test_filtered_load_sets_total_rows_without_crashing():
    real_rows = pd.DataFrame({"id": [1, 10, 11], "code": ["A", "B", "C"]})
    w = _widget(FakeDbService(real_rows, filtered_count=3))

    w.current_filter = "id > 0"
    w.load_table_data()

    assert w.total_rows == 3
    assert "Error" not in w.limit_label.text()
    assert w.data_table.rowCount() == 3


if __name__ == "__main__":
    test_unfiltered_load_keeps_real_rows_despite_stale_zero_count()
    test_filtered_load_sets_total_rows_without_crashing()
    print("ok")
