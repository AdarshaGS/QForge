"""Tests for the Refresh/Filter/Columns toolbar row docked into the
Data/Structure tab bar's corner (Ctrl+F filter toggle, issue #252's Manage
Columns, and issue #262's Refresh button — no general overflow menu).

test_creating_and_discarding_many_instances_does_not_crash is a regression
guard for a real segfault: the toolbar's container widget was originally a
bare local passed to QTabWidget.setCornerWidget() with no persistent Python
reference — PySide's ownership tracking doesn't reliably recognize that
call as a reparent, so the container (and the buttons parented to it) got
garbage-collected once init_ui() returned, corrupting Qt's C++-side state
and crashing the next time the event loop touched it. Only reproduced
through a real constructed widget across many instances/GC cycles, not a
single one — this loops enough to make the old bug fail reliably.
"""
import gc
import os

import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from ui.table_view_widget import TableViewWidget

_app = QApplication.instance() or QApplication([])


class FakeDbService:
    db_type = "mysql"

    def execute_query(self, query):
        if "COUNT(*)" in query:
            return pd.DataFrame([{"total": 3}])
        return pd.DataFrame({"id": [1, 2, 3]})

    def get_columns(self, table_name):
        return [{"Field": "id"}]


def _pump_until_loaded(w, timeout_ms=5000):
    elapsed = 0
    while getattr(w, "_loading", False) and elapsed < timeout_ms:
        QTest.qWait(10)
        elapsed += 10


def test_toolbar_buttons_exist_and_are_wired():
    w = TableViewWidget(FakeDbService(), "widgets")
    _pump_until_loaded(w)
    assert w.filter_toggle_btn.text() == "▽ Filter"
    assert w.columns_btn.text() == "▦ Columns"
    assert "Filter" in w.filter_toggle_btn.toolTip()
    assert "columns" in w.columns_btn.toolTip().lower()
    assert w._view_tab_tools.parent() is w.view_tabs


def test_filter_button_click_toggles_filter_visible_and_checked_state():
    w = TableViewWidget(FakeDbService(), "widgets")
    _pump_until_loaded(w)
    assert not w.filter_visible
    assert not w.filter_toggle_btn.isChecked()

    w.filter_toggle_btn.click()
    assert w.filter_visible
    assert w.filter_toggle_btn.isChecked()

    w.filter_toggle_btn.click()
    assert not w.filter_visible
    assert not w.filter_toggle_btn.isChecked()


def test_ctrl_f_and_esc_keep_the_button_in_sync_too():
    """toggle_filter()/hide_filter() can also be triggered by keyboard
    shortcuts, not just this button — the button's checked look must
    follow along regardless of which path toggled it."""
    w = TableViewWidget(FakeDbService(), "widgets")
    _pump_until_loaded(w)

    w.toggle_filter()  # Ctrl+F path
    assert w.filter_toggle_btn.isChecked()

    w.hide_filter()  # Esc path
    assert not w.filter_toggle_btn.isChecked()


def test_columns_button_opens_manage_columns(monkeypatch):
    w = TableViewWidget(FakeDbService(), "widgets")
    _pump_until_loaded(w)

    called = []
    monkeypatch.setattr(w.data_table, "manage_columns", lambda: called.append(True))
    w.columns_btn.click()
    assert called == [True]


def test_no_overflow_button_added():
    """Only Refresh + Filter + Columns live here (issue #262 added the
    Refresh button; no general overflow menu)."""
    w = TableViewWidget(FakeDbService(), "widgets")
    _pump_until_loaded(w)
    assert w._view_tab_tools.layout().count() == 3


def test_creating_and_discarding_many_instances_does_not_crash():
    for _ in range(20):
        w = TableViewWidget(FakeDbService(), "widgets")
        _pump_until_loaded(w)
        w.filter_toggle_btn.click()
        w.deleteLater()
        del w
        gc.collect()
        QTest.qWait(5)
    # Reaching here without a segfault is the actual assertion.
