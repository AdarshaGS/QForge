"""Tests for CodeEditor's matching-bracket highlight (issue #208) — the
class docstring claimed this feature existed with no actual
implementation behind it; this covers the real one."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from ui.code_editor import CodeEditor

_app = QApplication.instance() or QApplication([])


def _selections_at(text: str, cursor_pos: int):
    editor = CodeEditor()
    editor.setPlainText(text)
    c = editor.textCursor()
    c.setPosition(cursor_pos)
    editor.setTextCursor(c)
    return editor.own_extra_selections()


def _bracket_spans(selections):
    """(start, end) pairs for every non-full-width (i.e. bracket) selection."""
    return sorted(
        (s.cursor.selectionStart(), s.cursor.selectionEnd())
        for s in selections
        if s.cursor.selectionStart() != s.cursor.selectionEnd()
    )


def test_matched_pair_highlights_both_brackets():
    text = "SELECT * FROM foo(a, b)"
    pos = text.index("(") + 1   # cursor right after '('
    spans = _bracket_spans(_selections_at(text, pos))
    assert spans == [(text.index("("), text.index("(") + 1),
                      (text.index(")"), text.index(")") + 1)]


def test_unmatched_bracket_flags_itself_only():
    text = "SELECT (a, b"
    pos = text.index("(") + 1
    spans = _bracket_spans(_selections_at(text, pos))
    assert spans == [(text.index("("), text.index("(") + 1)]


def test_bracket_inside_string_literal_is_ignored():
    text = "SELECT 'a(b' , (c)"
    pos = text.index("(c)") + 1
    spans = _bracket_spans(_selections_at(text, pos))
    assert spans == [(text.index("(c)"), text.index("(c)") + 1),
                      (text.index(")"), text.index(")") + 1)]


def test_cursor_away_from_any_bracket_has_no_bracket_selection():
    text = "SELECT * FROM foo(a, b)"
    spans = _bracket_spans(_selections_at(text, 0))
    assert spans == []


def test_own_extra_selections_includes_current_line_highlight():
    """CodeEditor's line-highlight selection (full-width, no visible
    text span) must still be present alongside any bracket selections —
    sql_tab.py's _apply_extra_selections() layers on top of this list."""
    editor = CodeEditor()
    editor.setPlainText("SELECT 1")
    selections = editor.own_extra_selections()
    assert any(s.cursor.selectionStart() == s.cursor.selectionEnd()
               for s in selections)
