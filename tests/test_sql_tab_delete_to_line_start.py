"""Regression tests for Cmd+Backspace (⌘⌫) in the SQL editor: it used to
always wipe the entire current line regardless of cursor position. It
should instead delete from the cursor back to the start of the line —
same as the native "delete to line start" behaviour — falling back to a
normal backspace (merging with the previous line) when the cursor is
already at column 0."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ui.sql_tab import SqlTab

_app = QApplication.instance() or QApplication([])


def _press_cmd_backspace(tab):
    QTest.keyClick(tab.editor, Qt.Key_Backspace, Qt.ControlModifier)
    _app.processEvents()


def test_cmd_backspace_mid_line_deletes_only_up_to_cursor():
    tab = SqlTab()
    tab.show()
    tab.editor.setFocus()
    tab.editor.setPlainText("SELECT * FROM customers\nWHERE id = 1")
    cursor = tab.editor.textCursor()
    cursor.setPosition(len("SELECT * FROM cust"))
    tab.editor.setTextCursor(cursor)
    _app.processEvents()

    _press_cmd_backspace(tab)

    assert tab.editor.toPlainText() == "omers\nWHERE id = 1"
    tab.hide()


def test_cmd_backspace_at_end_of_line_removes_whole_line_content():
    tab = SqlTab()
    tab.show()
    tab.editor.setFocus()
    tab.editor.setPlainText("SELECT * FROM customers\nWHERE id = 1")
    cursor = tab.editor.textCursor()
    cursor.setPosition(len("SELECT * FROM customers"))
    tab.editor.setTextCursor(cursor)
    _app.processEvents()

    _press_cmd_backspace(tab)

    # The whole line's text is gone but its (now-empty) line remains,
    # rather than being merged into the next line.
    assert tab.editor.toPlainText() == "\nWHERE id = 1"
    tab.hide()


def test_cmd_backspace_at_start_of_line_merges_with_previous_line():
    tab = SqlTab()
    tab.show()
    tab.editor.setFocus()
    tab.editor.setPlainText("SELECT * FROM customers\nWHERE id = 1")
    cursor = tab.editor.textCursor()
    cursor.setPosition(len("SELECT * FROM customers") + 1)  # start of line 2
    tab.editor.setTextCursor(cursor)
    _app.processEvents()

    _press_cmd_backspace(tab)

    assert tab.editor.toPlainText() == "SELECT * FROM customersWHERE id = 1"
    tab.hide()
