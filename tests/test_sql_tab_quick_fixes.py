"""Tests for "Did you mean …?" quick-fixes on schema-validation squiggles
(issue #207) — surfacing an action on the unknown-table/unknown-column
detection _update_schema_validation already does."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from ui.sql_tab import SqlTab

_app = QApplication.instance() or QApplication([])


def _validated_tab(query: str, cursor_at_end: bool = False) -> SqlTab:
    tab = SqlTab()
    tab.completer.set_schema(
        tables=["users", "orders"],
        columns_dict={"users": ["id", "email"], "orders": ["id", "user_id"]},
    )
    tab.editor.setPlainText(query)   # setPlainText leaves the cursor at position 0
    tab.completer._aliases = tab.completer._extract_aliases(query)
    if cursor_at_end:
        c = tab.editor.textCursor()
        c.movePosition(c.MoveOperation.End)
        tab.editor.setTextCursor(c)
    tab._update_schema_validation()
    return tab


def test_unknown_table_flagged_with_table_candidates():
    tab = _validated_tab("SELECT * FROM usres")
    assert len(tab._validation_issues) == 1
    issue = tab._validation_issues[0]
    assert issue["text"] == "usres"
    assert set(issue["candidates"]) == {"users", "orders"}


def test_unknown_column_flagged_with_owning_tables_columns():
    tab = _validated_tab("SELECT u.emial FROM users u")
    assert len(tab._validation_issues) == 1
    issue = tab._validation_issues[0]
    assert issue["text"] == "emial"
    assert issue["candidates"] == ["id", "email"]


def test_quick_fix_at_finds_issue_covering_position():
    tab = _validated_tab("SELECT * FROM usres")
    pos = tab.editor.toPlainText().index("usres") + 2
    issue = tab._quick_fix_at(pos)
    assert issue is not None
    assert issue["text"] == "usres"


def test_quick_fix_at_returns_none_outside_any_issue():
    tab = _validated_tab("SELECT * FROM usres")
    issue = tab._quick_fix_at(0)   # "SELECT" — not a flagged span
    assert issue is None


def test_apply_quick_fix_replaces_span_and_revalidates():
    tab = _validated_tab("SELECT * FROM usres")
    issue = tab._validation_issues[0]
    tab._apply_quick_fix(issue["start"], issue["end"], "users")
    assert tab.editor.toPlainText() == "SELECT * FROM users"
    # Fixed — no longer flagged.
    assert tab._validation_issues == []


def test_no_issue_at_cursor_position_itself_still_typing():
    """The identifier the cursor is currently sitting inside is left
    unflagged (still being typed) — same as _update_schema_validation's
    existing behavior for the underline itself."""
    tab = _validated_tab("SELECT * FROM usres", cursor_at_end=True)
    assert tab._validation_issues == []
