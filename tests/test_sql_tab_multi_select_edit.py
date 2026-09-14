"""Regression tests for Cmd+D multi-select (repeated presses select every
occurrence of a word so it can be edited everywhere at once, like
VSCode's Cmd+D) — added so a column name repeated many times in a big
query can be renamed in one pass instead of one at a time."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ui.sql_tab import SqlTab

_app = QApplication.instance() or QApplication([])


def _ctrl_d(tab):
    QTest.keyClick(tab.editor, Qt.Key_D, Qt.ControlModifier)
    _app.processEvents()


def _select_word(tab, word: str):
    text = tab.editor.toPlainText()
    idx = text.index(word)
    cursor = tab.editor.textCursor()
    cursor.setPosition(idx)
    cursor.setPosition(idx + len(word), cursor.MoveMode.KeepAnchor)
    tab.editor.setTextCursor(cursor)
    _app.processEvents()


def test_ctrl_d_adds_one_region_per_press():
    tab = SqlTab()
    tab.show()
    tab.editor.setFocus()
    tab.editor.setPlainText("SELECT id, id, id FROM orders WHERE id = 1")
    _select_word(tab, "id")

    _ctrl_d(tab)  # region 1 (the already-selected word)
    assert len(tab._multi_regions) == 1
    _ctrl_d(tab)  # region 2
    _ctrl_d(tab)  # region 3
    _ctrl_d(tab)  # region 4 (last occurrence)

    assert len(tab._multi_regions) == 4
    text = tab.editor.toPlainText()
    for start, end in tab._multi_regions:
        assert text[start:end] == "id"
    tab.hide()


def test_typing_after_multi_select_edits_every_occurrence_at_once():
    tab = SqlTab()
    tab.show()
    tab.editor.setFocus()
    tab.editor.setPlainText("SELECT id, id, id FROM orders WHERE id = 1")
    _select_word(tab, "id")

    for _ in range(4):
        _ctrl_d(tab)
    assert len(tab._multi_regions) == 4

    QTest.keyClicks(tab.editor, "oid")
    _app.processEvents()

    assert tab.editor.toPlainText() == "SELECT oid, oid, oid FROM orders WHERE oid = 1"
    tab.hide()


def test_backspace_with_active_multi_selection_removes_whole_word_everywhere():
    # Same as a normal single-cursor Backspace with an active selection:
    # it deletes the whole selection, not one character.
    tab = SqlTab()
    tab.show()
    tab.editor.setFocus()
    tab.editor.setPlainText("SELECT abc, abc, abc FROM t")
    _select_word(tab, "abc")

    for _ in range(3):
        _ctrl_d(tab)
    assert len(tab._multi_regions) == 3

    QTest.keyClick(tab.editor, Qt.Key_Backspace)
    _app.processEvents()

    assert tab.editor.toPlainText() == "SELECT , ,  FROM t"
    tab.hide()


def test_backspace_after_typing_removes_one_character_everywhere():
    tab = SqlTab()
    tab.show()
    tab.editor.setFocus()
    tab.editor.setPlainText("SELECT abc, abc, abc FROM t")
    _select_word(tab, "abc")

    for _ in range(3):
        _ctrl_d(tab)
    assert len(tab._multi_regions) == 3

    QTest.keyClicks(tab.editor, "xyz")
    _app.processEvents()
    assert tab.editor.toPlainText() == "SELECT xyz, xyz, xyz FROM t"

    QTest.keyClick(tab.editor, Qt.Key_Backspace)
    _app.processEvents()

    assert tab.editor.toPlainText() == "SELECT xy, xy, xy FROM t"
    tab.hide()


def test_escape_ends_multi_select_and_typing_resumes_single_cursor():
    tab = SqlTab()
    tab.show()
    tab.editor.setFocus()
    tab.editor.setPlainText("SELECT id, id FROM t")
    _select_word(tab, "id")
    _ctrl_d(tab)
    _ctrl_d(tab)
    assert len(tab._multi_regions) == 2

    QTest.keyClick(tab.editor, Qt.Key_Escape)
    _app.processEvents()
    assert tab._multi_regions == []

    # Only the single (last) site is still affected by typing — Escape
    # truly ended multi-select rather than leaving it silently mirroring
    # to the first "id" too.
    QTest.keyClicks(tab.editor, "X")
    _app.processEvents()
    assert tab.editor.toPlainText().count("id") == 1
    assert "X" in tab.editor.toPlainText()
    tab.hide()


def test_arrow_key_exits_multi_select():
    tab = SqlTab()
    tab.show()
    tab.editor.setFocus()
    tab.editor.setPlainText("SELECT id, id FROM t")
    _select_word(tab, "id")
    _ctrl_d(tab)
    _ctrl_d(tab)
    assert len(tab._multi_regions) == 2

    QTest.keyClick(tab.editor, Qt.Key_Right)
    _app.processEvents()
    assert tab._multi_regions == []
    tab.hide()


def test_accepting_autocomplete_suggestion_updates_every_multi_select_site():
    # Bug: accepting a suggestion from the popup (Return) went through
    # SqlCompleter._insert, which only ever touches the single real
    # QTextCursor — every other Cmd+D region was silently left both
    # un-updated and stale.
    tab = SqlTab()
    tab.completer.set_schema(["customers"], {"customers": ["id", "name"]})
    tab.show()
    tab.editor.setFocus()
    tab.editor.setPlainText("SELECT * FROM cust, cust, cust")
    _select_word(tab, "cust")
    for _ in range(3):
        _ctrl_d(tab)
    assert len(tab._multi_regions) == 3

    tab.completer.update(force=True)
    _app.processEvents()
    assert tab.completer.popup_visible
    assert tab.completer.peek_current_completion() == "customers"

    QTest.keyClick(tab.editor, Qt.Key_Return)
    _app.processEvents()

    assert tab.editor.toPlainText() == "SELECT * FROM customers, customers, customers"
    tab.hide()


def test_accepting_suggestion_after_typing_replaces_the_whole_typed_word():
    # Bug: after typing several characters (each keystroke collapses a
    # region to a bare caret at its own site — see
    # _apply_multi_region_edit), accepting a completion inserted it right
    # at that caret without replacing what was typed before it, gluing
    # them together instead of replacing the whole word — e.g. typing
    # "floanapplicationrefer" then accepting "f_loan_application_reference"
    # produced "floanapplicationreferf_loan_application_reference".
    tab = SqlTab()
    tab.completer.set_schema(
        ["f_loan_application_reference"], {"f_loan_application_reference": ["id"]})
    tab.show()
    tab.editor.setFocus()
    tab.editor.setPlainText("SELECT * FROM t, t, t")
    _select_word(tab, "t,")
    cursor = tab.editor.textCursor()
    idx = tab.editor.toPlainText().index("t,")
    cursor.setPosition(idx)
    cursor.setPosition(idx + 1, cursor.MoveMode.KeepAnchor)
    tab.editor.setTextCursor(cursor)
    _app.processEvents()

    for _ in range(3):
        _ctrl_d(tab)
    assert len(tab._multi_regions) == 3

    QTest.keyClicks(tab.editor, "floanapplicationrefer")
    _app.processEvents()
    assert tab.completer.peek_current_completion() == "f_loan_application_reference"

    QTest.keyClick(tab.editor, Qt.Key_Return)
    _app.processEvents()

    expected = ("SELECT * FROM f_loan_application_reference, "
                "f_loan_application_reference, f_loan_application_reference")
    assert tab.editor.toPlainText() == expected
    tab.hide()


def test_mouse_click_ends_multi_select():
    # Bug: the event filter was installed only on tab.editor, but
    # QPlainTextEdit (a QAbstractScrollArea) delivers mouse events to its
    # *viewport* widget instead — a real click never reached the "click
    # ends multi-select" check, so it silently stayed active forever.
    tab = SqlTab()
    tab.show()
    tab.editor.setFocus()
    tab.editor.setPlainText("SELECT id, id FROM t")
    _select_word(tab, "id")
    _ctrl_d(tab)
    _ctrl_d(tab)
    assert len(tab._multi_regions) == 2

    QTest.mouseClick(tab.editor.viewport(), Qt.LeftButton)
    _app.processEvents()
    assert tab._multi_regions == []

    QTest.keyClicks(tab.editor, "X")
    _app.processEvents()
    assert tab.editor.toPlainText().count("id") == 2
    assert "X" in tab.editor.toPlainText()
    tab.hide()


def test_undo_after_multi_select_typing_reverts_every_site_at_once():
    tab = SqlTab()
    tab.show()
    tab.editor.setFocus()
    tab.editor.setPlainText("SELECT id, id, id FROM orders WHERE id = 1")
    _select_word(tab, "id")
    for _ in range(4):
        _ctrl_d(tab)
    assert len(tab._multi_regions) == 4

    QTest.keyClicks(tab.editor, "oid")
    _app.processEvents()
    assert tab.editor.toPlainText() == "SELECT oid, oid, oid FROM orders WHERE oid = 1"

    QTest.keyClick(tab.editor, Qt.Key_Z, Qt.ControlModifier)
    _app.processEvents()

    # One undo reverts the last keystroke at every site simultaneously —
    # not just the primary cursor's site.
    assert tab.editor.toPlainText() == "SELECT oi, oi, oi FROM orders WHERE oi = 1"
    tab.hide()


def test_undo_after_multi_select_completion_reverts_every_site_at_once():
    tab = SqlTab()
    tab.completer.set_schema(["customers"], {"customers": ["id", "name"]})
    tab.show()
    tab.editor.setFocus()
    tab.editor.setPlainText("SELECT * FROM cust, cust, cust")
    _select_word(tab, "cust")
    for _ in range(3):
        _ctrl_d(tab)
    assert len(tab._multi_regions) == 3

    tab.completer.update(force=True)
    _app.processEvents()
    QTest.keyClick(tab.editor, Qt.Key_Return)
    _app.processEvents()
    assert tab.editor.toPlainText() == "SELECT * FROM customers, customers, customers"

    QTest.keyClick(tab.editor, Qt.Key_Z, Qt.ControlModifier)
    _app.processEvents()

    assert tab.editor.toPlainText() == "SELECT * FROM cust, cust, cust"
    tab.hide()
