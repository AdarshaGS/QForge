"""Regression tests for issue #147 (the SQL error banner clipped long
messages with no way to see the rest) and its follow-on, issue #178 (the
error banner was replaced by a structured card: title / message /
location / snippet / hint — still must never silently clip text)."""
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


def test_short_error_does_not_grow_the_plain_status_label():
    tab = _shown_tab()
    tab.show_error("ERROR: syntax error near X")
    _app.processEvents()
    assert tab.status_label.height() < tab._STATUS_MAX_HEIGHT
    tab.hide()


def test_long_wrapping_single_line_error_is_shown_in_full_not_truncated():
    """The old plain-text banner sized itself by counting literal '\\n's,
    so a long single line with none wrapped across several visual lines
    it never accounted for, clipping the rest. The error card's message
    label word-wraps in a normal (uncapped) Qt layout instead, so the
    full string is always present regardless of how many lines it wraps
    into — there's no manual height estimate left to get wrong."""
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
    assert tab._error_card_scroll.isVisible()
    assert tab._error_message_lbl.text() == long_single_line
    tab.hide()


def test_very_long_error_stays_reachable_via_the_card_scroll_area():
    tab = _shown_tab()
    huge = "\n".join(f"ERROR line {i}: something went wrong in detail" for i in range(80))
    tab.show_error(huge)
    _app.processEvents()
    assert tab._error_card_scroll.isVisible()
    # Full text present in the label regardless of length...
    assert tab._error_message_lbl.text() == huge
    # ...and if it doesn't fit the pane, the scroll area (not the label
    # itself) is what makes the rest reachable — the same guarantee #147
    # established, just relocated to the new widget.
    content_taller_than_viewport = (
        tab._error_card.sizeHint().height() > tab._error_card_scroll.viewport().height()
    )
    if content_taller_than_viewport:
        assert tab._error_card_scroll.verticalScrollBar().maximum() > 0
    tab.hide()


def test_error_with_hint_populates_message_location_and_details_sections():
    tab = _shown_tab()
    msg = (
        '(1064, "You have an error in your SQL syntax; check the manual '
        "that corresponds to your MySQL server version for the right "
        "syntax to use near 'fghjk' at line 1\")"
    )
    tab.show_error(msg, query="SHOW fghjk", elapsed=0.005)
    _app.processEvents()

    assert tab._error_card_scroll.isVisible()
    assert "Syntax Error" in tab._error_title_lbl.text()
    assert tab._error_message_lbl.text() == msg
    assert "0.005" in tab._error_elapsed_lbl.text()
    assert tab._error_location_section.isVisible()
    assert tab._error_location_lbl.text() == "Line 1, Column 6"  # "fghjk" in "SHOW fghjk"
    assert "fghjk" in tab._error_snippet.toPlainText()
    assert tab._error_details_section.isVisible()
    assert "missing commas" in tab._error_details_lbl.text().lower()
    tab.hide()


def test_show_error_hides_the_normal_status_label_and_empty_state():
    tab = _shown_tab()
    tab.show_error("ERROR: syntax error near X")
    _app.processEvents()
    assert not tab.status_label.isVisible()
    assert not tab._empty_state.isVisible()
    assert not tab._result_actions_bar.isVisible()
    tab.hide()
