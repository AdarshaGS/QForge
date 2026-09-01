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
        self.count_queries = 0

    def execute_query(self, query):
        if "TABLE_ROWS" in query:
            # Stale/approximate count: reports 0 even though rows exist.
            return pd.DataFrame([{"TABLE_ROWS": 0}])
        if "COUNT(*)" in query and "WHERE" in query:
            self.count_queries += 1
            return pd.DataFrame([{"total": self._filtered_count}])
        if "COUNT(*)" in query:
            self.count_queries += 1
            return pd.DataFrame([{"total": len(self._real_rows)}])
        return self._real_rows.copy()

    def get_columns(self, table_name):
        return [{"Field": c} for c in self._real_rows.columns]


class FakeDbServiceWithEstimate(FakeDbService):
    """Simulates a dialect that exposes a fast, stats-based row-count
    estimate (MySQL/Postgres catalog stats) — the path that keeps opening
    a huge table from blocking on a full-scan COUNT(*)."""

    def __init__(self, real_rows, estimate, filtered_count=None):
        super().__init__(real_rows, filtered_count)
        self._estimate = estimate

    def get_estimated_row_count(self, table_name):
        return self._estimate


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


def test_page_turn_and_sort_do_not_recount():
    real_rows = pd.DataFrame({"id": [1, 2, 3], "code": ["A", "B", "C"]})
    db = FakeDbService(real_rows)
    w = _widget(db)
    w.page_size = 2  # force a second page to exist with only 3 rows

    assert db.count_queries == 1  # initial load

    w.next_page()
    w.on_column_header_clicked(0)  # sort by first column
    assert db.count_queries == 1  # neither re-ran COUNT(*)

    w.apply_all_filters()
    assert db.count_queries == 2  # filter change does recount


def test_unfiltered_open_uses_estimate_never_runs_count():
    """The scenario that used to hang on a huge (hundreds-of-GB) table:
    opening it should never run a real COUNT(*), and an estimate lower
    than reality must still be bumped up (not treated as the true total),
    per the existing "never let a stale stat cap pagination" guard."""
    real_rows = pd.DataFrame({"id": [1, 2, 3], "code": ["A", "B", "C"]})
    db = FakeDbServiceWithEstimate(real_rows, estimate=1)
    w = _widget(db)
    w.page_size = 2

    assert db.count_queries == 0
    assert w.total_rows == 3  # bumped up: offset(0) + full page(2) + 1 more


def test_filtered_load_still_counts_even_with_estimate_available():
    real_rows = pd.DataFrame({"id": [1, 10, 11], "code": ["A", "B", "C"]})
    db = FakeDbServiceWithEstimate(real_rows, estimate=999, filtered_count=3)
    w = _widget(db)

    w.current_filter = "id > 0"
    w.total_rows = None  # what reset_and_load_first_page() does on a real filter apply
    w.load_table_data()

    assert db.count_queries == 1
    assert w.total_rows == 3


if __name__ == "__main__":
    test_unfiltered_load_keeps_real_rows_despite_stale_zero_count()
    test_filtered_load_sets_total_rows_without_crashing()
    test_page_turn_and_sort_do_not_recount()
    test_unfiltered_open_uses_estimate_never_runs_count()
    test_filtered_load_still_counts_even_with_estimate_available()
    print("ok")
