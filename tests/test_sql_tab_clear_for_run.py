"""Regression test for issue #260: re-running a query in the same tab left
the previous result grid or error card on screen for the duration of the
new run, since _run_query_in_tab never cleared the tab's prior output
before dispatching the new worker."""
import os

import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from ui.sql_tab import SqlTab

_app = QApplication.instance() or QApplication([])


def test_clear_for_run_hides_previous_successful_result():
    tab = SqlTab()
    tab.load_dataframe(pd.DataFrame({"id": [1, 2]}), table_name="users")
    assert not tab.result_table.isHidden()
    assert tab.result_table.rowCount() == 2

    tab.clear_for_run()

    assert tab.result_table.rowCount() == 0
    assert tab.result_table.columnCount() == 0
    assert tab.result_table.isHidden()
    assert tab._pagination_bar.isHidden()


def test_clear_for_run_hides_previous_error_card():
    tab = SqlTab()
    tab.show_error("syntax error at or near \"SELCT\"")
    assert not tab._error_card_scroll.isHidden()

    tab.clear_for_run()

    assert tab._error_card_scroll.isHidden()
    assert tab.result_table.isHidden()
