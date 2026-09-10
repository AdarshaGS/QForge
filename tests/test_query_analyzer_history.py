"""Regression test for issue #339: running Cost or Profile from the
"Analyze Query" dialog against the current tab's query (no pre-existing
history entry — history_entry_id is None) must create a new Query History
entry, not silently discard the result. Re-running Estimate/Profile again
in the same dialog session must update that same entry rather than
creating a second one."""
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from services import query_cost
from ui.query_analyzer_dialog import _CostProfileTab

_app = QApplication.instance() or QApplication([])


class _HistoryStub:
    def __init__(self):
        self.added = []
        self.updated = []
        self._next_id = 1

    def add_query(self, query, connection_name, **fields):
        entry_id = f"entry-{self._next_id}"
        self._next_id += 1
        self.added.append((query, connection_name, fields))
        return entry_id

    def update_entry(self, entry_id, **fields):
        self.updated.append((entry_id, fields))
        return True


def _make_tab(history_entry_id=None):
    history = _HistoryStub()
    tab = _CostProfileTab(
        SimpleNamespace(db_type="mysql"), initial_query="SELECT 1",
        query_history=history, history_entry_id=history_entry_id,
        connection_name="Local",
    )
    return tab, history


def test_estimate_creates_a_history_entry_when_none_exists():
    tab, history = _make_tab()

    result = query_cost.CostEstimate(dialect="mysql", score=5, label="Fine")
    tab._on_estimate_done(result)

    assert len(history.added) == 1
    query, connection_name, fields = history.added[0]
    assert query == "SELECT 1"
    assert connection_name == "Local"
    assert fields["cost_score"] == 5
    assert fields["cost_label"] == "Fine"
    assert tab._history_entry_id == "entry-1"
    assert history.updated == []


def test_profile_after_estimate_updates_the_same_entry():
    tab, history = _make_tab()
    tab._on_estimate_done(query_cost.CostEstimate(dialect="mysql", score=5, label="Fine"))

    profile = query_cost.QueryProfile(dialect="mysql", total_time_ms=42.0)
    tab._on_profile_done(profile)

    assert len(history.added) == 1  # no second row created
    assert len(history.updated) == 1
    entry_id, fields = history.updated[0]
    assert entry_id == "entry-1"
    assert fields["execution_time"] == pytest.approx(0.042)


def test_run_updates_existing_entry_when_opened_from_history(monkeypatch):
    tab, history = _make_tab(history_entry_id="existing-42")

    tab._on_estimate_done(query_cost.CostEstimate(dialect="mysql", score=1, label="Fine"))

    assert history.added == []
    assert len(history.updated) == 1
    assert history.updated[0][0] == "existing-42"


def test_failed_estimate_does_not_touch_history():
    tab, history = _make_tab()

    tab._on_estimate_done(query_cost.CostEstimate(dialect="mysql", error="boom"))

    assert history.added == []
    assert history.updated == []
    assert tab._history_entry_id is None
