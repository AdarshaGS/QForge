"""Tests for the richer schema payload SqlCompleter.set_schema() now accepts
(column types/PK/FK, views, user-defined functions) and what it unlocks:
type/key badges on column suggestions, views/routines in autocomplete, and
FK-aware JOIN ON completion."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QPlainTextEdit

from ui.sql_completer import SqlCompleter, SuggestionItem

_app = QApplication.instance() or QApplication([])


def _make_completer(query: str) -> tuple[SqlCompleter, int]:
    """A real SqlCompleter over a throwaway editor, schema pre-loaded with
    users/orders (+ a view and a routine), cursor at the end of *query*."""
    editor = QPlainTextEdit()
    editor.setPlainText(query)
    completer = SqlCompleter(editor)
    completer.set_schema(
        tables=["users", "orders"],
        columns_dict={
            "users":  ["id", "email"],
            "orders": ["id", "user_id", "total"],
        },
        column_details={
            "users": [
                {"name": "id", "type": "int", "nullable": False, "default": None, "key": "PRI"},
                {"name": "email", "type": "varchar(255)", "nullable": False, "default": None, "key": ""},
            ],
            "orders": [
                {"name": "id", "type": "int", "nullable": False, "default": None, "key": "PRI"},
                {"name": "user_id", "type": "int", "nullable": False, "default": None, "key": ""},
                {"name": "total", "type": "decimal(10,2)", "nullable": True, "default": "0.00", "key": ""},
            ],
        },
        foreign_keys={
            "orders": [{"column": "user_id", "ref_table": "users", "ref_column": "id"}],
        },
        views=["active_users"],
        functions=["calc_discount"],
    )
    completer._aliases = completer._extract_aliases(query)
    return completer, len(query)


def _find(items, text):
    return next((i for i in items if i.text == text), None)


def test_column_suggestion_carries_type_badge():
    completer, pos = _make_completer("SELECT em FROM users")
    items = completer._build_suggestions("em", "AFTER_SELECT", "SELECT em FROM users", 8)
    email = _find(items, "email")
    assert email is not None
    assert email.extra.get("badge") == "VARCHAR"


def test_primary_key_column_is_flagged_pri():
    completer, pos = _make_completer("SELECT i FROM users")
    items = completer._build_suggestions("i", "AFTER_SELECT", "SELECT i FROM users", 8)
    col = _find(items, "id")
    assert col.extra.get("key") == "PRI"


def test_foreign_key_column_is_flagged_fk():
    query = "SELECT user_ FROM orders"
    completer, _ = _make_completer(query)
    items = completer._build_suggestions("user_", "AFTER_SELECT", query, 12)
    col = _find(items, "user_id")
    assert col.extra.get("key") == "FK"


def test_view_is_suggested_after_from_with_view_badge():
    query = "SELECT * FROM active"
    completer, _ = _make_completer(query)
    items = completer._build_suggestions("active", "AFTER_FROM", query, len(query))
    view = _find(items, "active_users")
    assert view is not None
    assert view.kind == SuggestionItem.VIEW


def test_user_defined_function_appears_in_function_suggestions():
    query = "SELECT calc"
    completer, _ = _make_completer(query)
    items = completer._build_suggestions("calc", "AFTER_SELECT", query, len(query))
    fn = _find(items, "calc_discount")
    assert fn is not None
    assert fn.kind == SuggestionItem.FUNC


def test_fk_aware_join_on_suggestion_uses_real_fk_relationship():
    query = "SELECT * FROM users JOIN orders o"
    completer, _ = _make_completer(query)
    items = completer._build_suggestions("o", "AFTER_JOIN", query, len(query))
    on_item = _find(items, "ON")
    assert on_item is not None
    assert on_item.extra["body"] == "ON users.id = o.user_id"


def test_fk_join_suggestion_absent_without_matching_relationship():
    # No FK links users to itself — sanity check the helper doesn't fire on
    # unrelated pairs.
    completer, _ = _make_completer("SELECT * FROM users JOIN users u")
    items = completer._fk_join_on_suggestion(
        "", "SELECT * FROM users JOIN users u", len("SELECT * FROM users JOIN users u"))
    assert items == []


def test_public_accessors_resolve_alias_and_column_meta():
    query = "SELECT * FROM users u"
    completer, _ = _make_completer(query)
    completer._aliases = completer._extract_aliases(query)
    assert completer.resolve_table("u") == "users"
    assert completer.alias_target("u") == "users"
    meta = completer.column_meta("users", "id")
    assert meta["key"] == "PRI"
    assert completer.is_view("active_users") is True
    assert completer.known_tables() == {"users", "orders", "active_users"}


def test_primary_key_columns_reads_key_field_from_column_details():
    completer, _ = _make_completer("SELECT * FROM users")
    assert completer.primary_key_columns("users") == ["id"]
    assert completer.primary_key_columns("orders") == ["id"]


def test_primary_key_columns_empty_for_unknown_table():
    completer, _ = _make_completer("SELECT * FROM users")
    assert completer.primary_key_columns("no_such_table") == []


def test_aliased_table_column_suggestion_is_alias_qualified():
    query = "SELECT * FROM users u JOIN orders o ON u.id = o.user_id WHERE i"
    completer, _ = _make_completer(query)
    items = completer._build_suggestions("i", "AFTER_WHERE", query, len(query))
    # Both tables have an "id" column — with aliases in play each should
    # surface distinctly qualified rather than collapsing to one ambiguous
    # bare "id".
    assert _find(items, "u.id") is not None
    assert _find(items, "o.id") is not None
    assert _find(items, "id") is None


def test_unaliased_table_column_suggestion_stays_bare():
    completer, _ = _make_completer("SELECT em FROM users")
    items = completer._build_suggestions("em", "AFTER_SELECT", "SELECT em FROM users", 8)
    assert _find(items, "email") is not None
    assert _find(items, "users.email") is None
