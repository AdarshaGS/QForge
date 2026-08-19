"""Regression test for issue #152 — pressing Esc while the autocomplete
popup was visible didn't close it.

Root cause: SqlTab.esc_shortcut is a bare QShortcut("Esc") with the
default WindowShortcut context. Qt's shortcut dispatch intercepts Escape
via a ShortcutOverride event sent to the focus widget *before* a real
KeyPress is ever delivered — since self.editor never claimed that
ShortcutOverride, the shortcut always fired hide_filter() (which only
hides the table-view filter panel) and eventFilter's own KeyPress-based
Key_Escape handling for the completer popup never ran.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ui.sql_tab import SqlTab

_app = QApplication.instance() or QApplication([])


def test_escape_hides_popup_when_visible():
    tab = SqlTab()
    tab.completer.set_schema(["users"], {"users": ["id", "name"]})
    tab.show()
    tab.editor.setFocus()
    _app.processEvents()

    QTest.keyClicks(tab.editor, "SHOW PROCESSLIS")
    _app.processEvents()
    assert tab.completer.popup_visible

    QTest.keyClick(tab.editor, Qt.Key_Escape)
    _app.processEvents()

    assert not tab.completer.popup_visible
    # Escape only dismisses the popup — it must not touch the typed text.
    assert tab.editor.toPlainText() == "SHOW PROCESSLIS"
    tab.hide()


def test_escape_still_hides_filter_panel_when_popup_not_visible():
    """The pre-existing esc_shortcut -> hide_filter() behavior (for the
    table-view data filter panel) must be unaffected when there's no
    popup to dismiss."""
    tab = SqlTab()
    tab.show()
    tab.editor.setFocus()
    _app.processEvents()
    assert not tab.completer.popup_visible

    calls = []
    original = tab.hide_filter
    tab.hide_filter = lambda: (calls.append(1), original())[-1]

    QTest.keyClick(tab.editor, Qt.Key_Escape)
    _app.processEvents()

    assert calls == [1]
    tab.hide()
