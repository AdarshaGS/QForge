"""Tests for WHERE/HAVING predicate awareness: type-aware operator
suggestions right after a column, static enum/boolean value suggestions
right after an operator, and dropping a column from the suggestion list
once it's already used in an earlier predicate in the same clause."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QPlainTextEdit

from ui.sql_completer import SqlCompleter, SuggestionItem

_app = QApplication.instance() or QApplication([])

_COLUMN_DETAILS = {
    "orders": [
        {"name": "id", "type": "int", "nullable": False, "default": None, "key": "PRI"},
        {"name": "total", "type": "decimal(10,2)", "nullable": True, "default": None, "key": ""},
        {"name": "status", "type": "enum('active','pending','done')",
         "nullable": False, "default": None, "key": ""},
        {"name": "is_active", "type": "tinyint(1)", "nullable": False, "default": None, "key": ""},
        {"name": "created_at", "type": "datetime", "nullable": True, "default": None, "key": ""},
        {"name": "name", "type": "varchar(255)", "nullable": True, "default": None, "key": ""},
    ],
}


def _make_completer(query: str) -> SqlCompleter:
    editor = QPlainTextEdit()
    editor.setPlainText(query)
    completer = SqlCompleter(editor)
    completer.set_schema(
        tables=["orders"],
        columns_dict={"orders": [c["name"] for c in _COLUMN_DETAILS["orders"]]},
        column_details=_COLUMN_DETAILS,
    )
    completer._aliases = completer._extract_aliases(query)
    return completer


def _find(items, text):
    return next((i for i in items if i.text == text), None)


def _op_score(items, text):
    hit = _find(items, text)
    return hit.score if hit else None


def test_numeric_column_ranks_range_operators_above_like():
    # The type-aware operators are additive on top of the pre-existing
    # generic operator/keyword list (never suppressed) — the point is
    # that they rank far above it (score 1200 vs 900/650), not that the
    # generic ones disappear.
    query = "SELECT * FROM orders WHERE total "
    completer = _make_completer(query)
    items = completer._build_suggestions("", "AFTER_WHERE", query, len(query))
    for op in ("=", "!=", "<", "<=", ">", ">=", "BETWEEN", "IN", "NOT IN",
               "IS NULL", "IS NOT NULL"):  # total is nullable
        assert _op_score(items, op) == 1200, op
    assert _op_score(items, "LIKE") < 1200


def test_string_column_ranks_like_above_between():
    query = "SELECT * FROM orders WHERE name "
    completer = _make_completer(query)
    items = completer._build_suggestions("", "AFTER_WHERE", query, len(query))
    for op in ("LIKE", "ILIKE", "NOT LIKE"):
        assert _op_score(items, op) == 1200, op
    assert _op_score(items, "BETWEEN") < 1200


def test_non_nullable_column_does_not_rank_is_null_as_type_aware():
    query = "SELECT * FROM orders WHERE id "
    completer = _make_completer(query)
    items = completer._build_suggestions("", "AFTER_WHERE", query, len(query))
    assert _op_score(items, "IS NULL") < 1200
    assert _op_score(items, "IS NOT NULL") < 1200


def test_boolean_column_ranks_is_operators_above_like_and_between():
    query = "SELECT * FROM orders WHERE is_active "
    completer = _make_completer(query)
    items = completer._build_suggestions("", "AFTER_WHERE", query, len(query))
    for op in ("=", "!=", "IS", "IS NOT"):
        assert _op_score(items, op) == 1200, op
    assert _op_score(items, "LIKE") < 1200
    assert _op_score(items, "BETWEEN") < 1200


def test_enum_column_suggests_its_literal_values_after_equals():
    query = "SELECT * FROM orders WHERE status = "
    completer = _make_completer(query)
    items = completer._build_suggestions("", "AFTER_WHERE", query, len(query))
    values = [i.text for i in items if i.kind == SuggestionItem.VALUE]
    assert sorted(values) == ["'active'", "'done'", "'pending'"]


def test_tinyint1_column_suggests_zero_and_one_after_equals():
    query = "SELECT * FROM orders WHERE is_active = "
    completer = _make_completer(query)
    items = completer._build_suggestions("", "AFTER_WHERE", query, len(query))
    values = {i.text for i in items if i.kind == SuggestionItem.VALUE}
    assert values == {"0", "1"}


def test_non_enum_column_has_no_value_suggestions():
    query = "SELECT * FROM orders WHERE name = "
    completer = _make_completer(query)
    items = completer._build_suggestions("", "AFTER_WHERE", query, len(query))
    assert [i for i in items if i.kind == SuggestionItem.VALUE] == []


def test_column_already_used_in_predicate_is_dropped_from_list():
    query = "SELECT * FROM orders WHERE total > 5 AND "
    completer = _make_completer(query)
    items = completer._build_suggestions("", "AFTER_WHERE", query, len(query))
    cols = {i.text for i in items if i.kind == SuggestionItem.COLUMN}
    assert "total" not in cols
    assert "status" in cols and "id" in cols


def test_columns_not_yet_predicated_are_unaffected():
    query = "SELECT * FROM orders WHERE "
    completer = _make_completer(query)
    items = completer._build_suggestions("", "AFTER_WHERE", query, len(query))
    cols = {i.text for i in items if i.kind == SuggestionItem.COLUMN}
    assert {"total", "status", "id", "name"} <= cols
