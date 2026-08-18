"""Regression test for issue #149 — session restore left focus on the last
restored tab instead of tab 1."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QTabWidget
from PySide6.QtTest import QTest

from ui.connection_panel import ConnectionPanel
from ui.sql_tab import SqlTab

_app = QApplication.instance() or QApplication([])


class _PanelStub:
    """Just enough of ConnectionPanel for focus_first_tab() to operate on —
    avoids constructing a real panel (db_service, query history, ...)."""
    def __init__(self, tabs):
        self.tabs = tabs


def _panel_with_tabs(n):
    tabs = QTabWidget()
    sql_tabs = []
    for i in range(n):
        tab = SqlTab()
        tabs.addTab(tab, f"Tab {i + 1}")
        # Mirrors add_new_tab(): each restored tab makes itself current,
        # so after the loop the *last* one is left active — the bug.
        tabs.setCurrentWidget(tab)
        sql_tabs.append(tab)
    return _PanelStub(tabs), sql_tabs


def test_focus_first_tab_makes_tab_zero_current_after_restore_loop():
    panel, sql_tabs = _panel_with_tabs(8)
    assert panel.tabs.currentIndex() == 7  # last-restored tab, pre-fix behavior

    ConnectionPanel.focus_first_tab(panel)

    assert panel.tabs.currentIndex() == 0
    # Drain the deferred QTimer.singleShot(0, ...) this call scheduled
    # while its target widgets are still alive — otherwise it fires during
    # a later test's qWait() against already-garbage-collected tabs.
    QTest.qWait(10)


def test_focus_first_tab_focuses_tab_zeros_editor_not_the_last_ones():
    panel, sql_tabs = _panel_with_tabs(3)
    panel.tabs.show()
    QTest.qWaitForWindowExposed(panel.tabs)

    ConnectionPanel.focus_first_tab(panel)
    QTest.qWait(10)  # let the deferred QTimer.singleShot(0, ...) fire

    assert sql_tabs[0].editor.hasFocus()
    assert not sql_tabs[2].editor.hasFocus()
    panel.tabs.hide()


def test_focus_first_tab_is_a_noop_on_an_empty_panel():
    panel, _ = _panel_with_tabs(0)
    ConnectionPanel.focus_first_tab(panel)  # must not raise
    assert panel.tabs.count() == 0
