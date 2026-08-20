"""Regression tests for issue #147 — the SQL error/status banner clipped
long messages at a fixed 150px with no visible way to see the rest. A
long single line with no literal '\\n' word-wraps across several visual
lines that the old height formula (counting only literal newlines)
never accounted for."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from ui.sql_tab import SqlTab

_app = QApplication.instance() or QApplication([])


def _shown_tab():
    tab = SqlTab()
    tab.resize(900, 600)
    tab.show()
    QTest.qWaitForWindowExposed(tab)
    _app.processEvents()
    return tab


def test_short_error_does_not_grow_to_the_cap():
    tab = _shown_tab()
    tab.show_error("ERROR: syntax error near X")
    _app.processEvents()
    assert tab.status_label.height() < tab._STATUS_MAX_HEIGHT
    tab.hide()


def test_long_wrapping_single_line_error_grows_past_the_old_36px_estimate():
    """Pre-fix, a message with zero literal '\\n's was always sized as if
    it were exactly one line (lines=1 -> 1*20+16=36px), regardless of how
    many visual lines it actually wrapped into."""
    tab = _shown_tab()
    long_single_line = (
        "ERROR 1064 (42000): You have an error in your SQL syntax; check "
        "the manual that corresponds to your MySQL server version for the "
        "right syntax to use near 'GROUP BY customer_id HAVING COUNT(*) > 5' "
        "at line 3"
    )
    assert "\n" not in long_single_line
    tab.show_error(long_single_line)
    _app.processEvents()
    assert tab.status_label.height() > 36
    tab.hide()


def test_very_long_error_is_capped_and_the_rest_is_reachable_by_scrolling():
    tab = _shown_tab()
    huge = "\n".join(f"ERROR line {i}: something went wrong in detail" for i in range(40))
    tab.show_error(huge)
    _app.processEvents()
    assert tab.status_label.height() == tab._STATUS_MAX_HEIGHT
    scrollbar = tab.status_label.verticalScrollBar()
    assert scrollbar.isVisible()
    assert scrollbar.maximum() > 0
    tab.hide()
