"""Regression tests for issue #285's statement-boundary edge cases in
SqlTab.get_query_at_cursor() (plain Run's cursor-scoping logic).

sqlparse.parse() correctly keeps a `;` inside a string literal or comment
from splitting a statement (already covered by issue #166), and attaches
leading whitespace/comments to the statement that follows them — both
verified here as already-correct behavior, not new fixes.

The one real bug: sqlparse.parse() does NOT attribute *trailing*
whitespace after the final statement to any statement's span. A cursor
sitting there (a trailing blank line, or Ctrl+End) fell through every
statement's range, returned None, and made get_query() fall back to
running the WHOLE script — surprising and different from every other
statement-at-cursor case, and different from DataGrip/DBeaver's
convention of treating that position as belonging to the last statement.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from ui.sql_tab import SqlTab

_app = QApplication.instance() or QApplication([])


def _tab_with_cursor_at(text: str, pos: int) -> SqlTab:
    tab = SqlTab()
    tab.editor.setPlainText(text)
    cursor = tab.editor.textCursor()
    cursor.setPosition(pos)
    tab.editor.setTextCursor(cursor)
    return tab


def test_cursor_in_trailing_whitespace_after_final_statement_runs_last_statement_only():
    text = "SELECT 1;\nUPDATE t SET x=1;\n\n   "
    tab = _tab_with_cursor_at(text, len(text))  # Ctrl+End

    assert tab.get_query() == "UPDATE t SET x=1;"


def test_cursor_at_very_end_with_no_trailing_whitespace_runs_last_statement():
    text = "SELECT 1;\nUPDATE t SET x=1"
    tab = _tab_with_cursor_at(text, len(text))

    assert tab.get_query() == "UPDATE t SET x=1"


def test_cursor_in_trailing_newline_only_after_single_statement_runs_that_statement():
    text = "SELECT 1;\n\n\n"
    tab = _tab_with_cursor_at(text, len(text))

    assert tab.get_query() == "SELECT 1;"


def test_semicolon_inside_string_literal_does_not_split_the_statement():
    text = "UPDATE a SET x=1;\nSELECT * FROM t WHERE msg = 'a;b';\nUPDATE b SET x=2;"
    cursor_pos = text.index("a;b") + 2  # right after the embedded ; inside the quotes
    tab = _tab_with_cursor_at(text, cursor_pos)

    result = tab.get_query()
    assert "UPDATE a" not in result
    assert "UPDATE b" not in result
    assert "SELECT * FROM t" in result
    assert "'a;b'" in result


def test_cursor_in_leading_comment_runs_the_statement_it_precedes():
    text = "SELECT 1;\n-- a note about the next statement\nUPDATE t SET x=1;"
    cursor_pos = text.index("a note")
    tab = _tab_with_cursor_at(text, cursor_pos)

    result = tab.get_query()
    assert "SELECT 1" not in result
    assert "UPDATE t SET x=1" in result
