"""Tests for Ctrl/Cmd-click go-to-definition (issue #206) — resolving a
table, alias, or alias.column reference under the cursor to the table its
structure view should jump to."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from ui.sql_tab import SqlTab

_app = QApplication.instance() or QApplication([])


def _tab_with_schema(query: str) -> SqlTab:
    tab = SqlTab()
    tab.editor.setPlainText(query)
    tab.completer.set_schema(
        tables=["users", "orders"],
        columns_dict={"users": ["id", "name"], "orders": ["id", "user_id"]},
    )
    tab.completer._aliases = {"u": "users", "o": "orders"}
    return tab


def test_dot_notation_column_resolves_to_owning_table():
    tab = _tab_with_schema("SELECT u.name FROM users u")
    pos = tab.editor.toPlainText().index("u.name") + len("u.")
    word, table = tab._resolve_word_at(pos)
    assert (word, table) == ("name", "users")
    assert tab._resolve_definition_target(word, table) == "users"


def test_bare_table_name_resolves_to_itself():
    tab = _tab_with_schema("SELECT * FROM users")
    pos = tab.editor.toPlainText().index("users")
    word, table = tab._resolve_word_at(pos)
    assert (word, table) == ("users", None)
    assert tab._resolve_definition_target(word, table) == "users"


def test_alias_resolves_to_its_table():
    tab = _tab_with_schema("SELECT * FROM users u JOIN orders o ON u.id = o.user_id")
    text = tab.editor.toPlainText()
    pos = text.index(" o ") + 1
    word, table = tab._resolve_word_at(pos)
    assert (word, table) == ("o", None)
    assert tab._resolve_definition_target(word, table) == "orders"


def test_unknown_column_on_known_table_does_not_resolve():
    tab = _tab_with_schema("SELECT u.bogus FROM users u")
    pos = tab.editor.toPlainText().index("u.bogus") + len("u.")
    word, table = tab._resolve_word_at(pos)
    assert table == "users"
    assert tab._resolve_definition_target(word, table) is None


def test_go_to_definition_routes_to_show_structure():
    tab = _tab_with_schema("SELECT * FROM users")
    calls = []
    tab._on_result_show_structure = lambda name: calls.append(name)
    pos = tab.editor.toPlainText().index("users")
    tab._go_to_definition(pos)
    assert calls == ["users"]


def test_ctrl_click_signal_reaches_go_to_definition():
    """CodeEditor.identifier_clicked (emitted by CodeEditor's own
    mousePressEvent on a Ctrl/Cmd-click) is wired to SqlTab._go_to_definition."""
    tab = _tab_with_schema("SELECT * FROM users")
    calls = []
    tab._on_result_show_structure = lambda name: calls.append(name)
    pos = tab.editor.toPlainText().index("users")
    tab.editor.identifier_clicked.emit(pos)
    assert calls == ["users"]
