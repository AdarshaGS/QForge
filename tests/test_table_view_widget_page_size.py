"""Tests for issue #251: table view's rows-per-page is user-configurable
via a toolbar combo box and persists across sessions (services/preferences.py).
"""
import os

import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from services import preferences
from ui.table_view_widget import TableViewWidget, _PAGE_SIZE_PREF_KEY, _DEFAULT_PAGE_SIZE

_app = QApplication.instance() or QApplication([])


def _pump_until_loaded(w, timeout_ms=5000):
    elapsed = 0
    while getattr(w, "_loading", False) and elapsed < timeout_ms:
        QTest.qWait(10)
        elapsed += 10


class FakeDbService:
    db_type = "mysql"

    def __init__(self, real_rows):
        self._real_rows = real_rows
        self.count_queries = 0
        self.last_limit = None

    def execute_query(self, query):
        if "COUNT(*)" in query:
            self.count_queries += 1
            return pd.DataFrame([{"total": len(self._real_rows)}])
        if "LIMIT" in query:
            self.last_limit = int(query.split("LIMIT")[1].split("OFFSET")[0].strip())
        return self._real_rows.copy()

    def get_columns(self, table_name):
        return [{"Field": c} for c in self._real_rows.columns]


@pytest.fixture(autouse=True)
def _isolated_preferences_file(tmp_path, monkeypatch):
    """Every test here would otherwise read/write the real developer
    machine's actual ~/Library/Application Support/QForge/preferences.json
    — point the module at a throwaway file instead."""
    monkeypatch.setattr(preferences, "_FILE", str(tmp_path / "preferences.json"))


def _widget(db_service=None):
    db_service = db_service or FakeDbService(pd.DataFrame({"id": [1, 2, 3]}))
    w = TableViewWidget(db_service, "widgets")
    _pump_until_loaded(w)
    return w, db_service


def test_default_page_size_falls_back_when_nothing_saved():
    w, _ = _widget()
    assert w.page_size == _DEFAULT_PAGE_SIZE
    assert w.page_size_combo.currentText() == str(_DEFAULT_PAGE_SIZE)


def test_saved_preference_becomes_the_default_for_a_new_widget():
    preferences.set(_PAGE_SIZE_PREF_KEY, 500)
    w, _ = _widget()
    assert w.page_size == 500
    assert w.page_size_combo.currentText() == "500"


def test_saved_value_outside_the_preset_list_falls_back_to_default():
    """A value from an older/foreign preset list (or hand-edited file)
    shouldn't leave the combo box showing something it has no entry for."""
    preferences.set(_PAGE_SIZE_PREF_KEY, 37)
    w, _ = _widget()
    assert w.page_size == _DEFAULT_PAGE_SIZE


def test_changing_combo_updates_page_size_persists_and_reloads():
    w, db = _widget()
    assert db.count_queries == 1

    w.page_size_combo.setCurrentText("500")
    _pump_until_loaded(w)

    assert w.page_size == 500
    assert db.last_limit == 500
    assert preferences.get(_PAGE_SIZE_PREF_KEY) == 500
    # Same table/filter — the row count didn't change, only the grouping
    # into pages, so this must not have re-run COUNT(*).
    assert db.count_queries == 1


def test_changing_page_size_resets_to_first_page():
    w, _ = _widget(FakeDbService(pd.DataFrame({"id": range(10)})))
    w.page_size = 2
    w.next_page()
    _pump_until_loaded(w)
    assert w.current_page == 2

    w.page_size_combo.setCurrentText("1000")
    _pump_until_loaded(w)
    assert w.current_page == 1


def test_reselecting_the_same_value_is_a_noop():
    w, db = _widget()
    assert db.count_queries == 1

    w.page_size_combo.setCurrentText(str(w.page_size))
    _pump_until_loaded(w)

    assert db.count_queries == 1  # no reload triggered

def test_new_widget_after_a_change_picks_up_the_new_default():
    w1, _ = _widget()
    w1.page_size_combo.setCurrentText("200")
    _pump_until_loaded(w1)

    w2, _ = _widget()
    assert w2.page_size == 200
