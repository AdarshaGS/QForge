"""Regression test for issue #174: a page load that overlaps a detected
system suspend/sleep window must not be recorded as a normal page_load
sample (it would pollute mean/p95 with a number that has nothing to do
with query or render speed) — it should be filtered and counted instead."""
import os
import time

import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from ui.table_view_widget import TableViewWidget
from utils import perf_metrics

_app = QApplication.instance() or QApplication([])


def _pump_until_loaded(w, timeout_ms=5000):
    """load_table_data() now runs its DB work on a background QThread —
    wait for it to finish (via the _loading flag it clears) instead of
    asserting immediately after a call that used to be synchronous."""
    elapsed = 0
    while getattr(w, "_loading", False) and elapsed < timeout_ms:
        QTest.qWait(10)
        elapsed += 10


class _FakeDbService:
    db_type = "mysql"

    def __init__(self, rows):
        self._rows = rows

    def execute_query(self, query):
        if "COUNT(*)" in query:
            return pd.DataFrame([{"total": len(self._rows)}])
        return self._rows.copy()

    def get_columns(self, table_name):
        return [{"Field": c} for c in self._rows.columns]


def setup_function():
    perf_metrics.reset()


def test_page_load_overlapping_suspend_window_is_filtered_not_recorded():
    # Bracket "now" generously — the fake load is near-instantaneous, so a
    # narrow window could miss it by a few milliseconds either side.
    now = time.time()
    perf_metrics._SUSPEND_WINDOWS.append((now - 5, now + 5))

    rows = pd.DataFrame({"id": [1, 2, 3]})
    w = TableViewWidget(_FakeDbService(rows), "t")
    _pump_until_loaded(w)

    assert "page_load" not in perf_metrics.snapshot().get("result_grid", {})
    assert perf_metrics.counter_get("suspend_filtered") == {"page_load": 1}


def test_page_load_without_suspend_window_is_recorded_normally():
    rows = pd.DataFrame({"id": [1, 2, 3]})
    w = TableViewWidget(_FakeDbService(rows), "t")
    _pump_until_loaded(w)

    assert "page_load" in perf_metrics.snapshot().get("result_grid", {})
    assert perf_metrics.counter_get("suspend_filtered") == {}
