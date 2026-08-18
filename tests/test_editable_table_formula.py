"""Tests for the cell-formula evaluator (issue #161): replaced a raw
eval() with an AST-whitelisted arithmetic-only evaluator, since
{"__builtins__": {}} was never a real sandbox (object introspection still
reaches arbitrary classes without needing the builtins dict) and the
formula can carry database-sourced content via bulk column edit's
{value} substitution."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from ui.editable_table import EditableTableWidget, _safe_eval_arithmetic

_app = QApplication.instance() or QApplication([])


def test_safe_eval_arithmetic_computes_basic_expressions():
    assert _safe_eval_arithmetic("2+3*4") == 14
    assert _safe_eval_arithmetic("(2+3)*4") == 20
    assert _safe_eval_arithmetic("-5+2") == -3


def test_safe_eval_arithmetic_rejects_call_expressions():
    with pytest.raises(ValueError):
        _safe_eval_arithmetic("__import__('os').system('id')+0")


def test_safe_eval_arithmetic_rejects_attribute_access():
    with pytest.raises(ValueError):
        _safe_eval_arithmetic("().__class__+0")


def test_evaluate_formula_string_runs_arithmetic_through_safe_evaluator():
    table = EditableTableWidget()
    assert table.evaluate_formula_string("=2+3*4") == "14"


def test_evaluate_formula_string_blocks_injected_call_via_value_placeholder():
    """Mirrors the bulk-column-edit path: {value} substitution can splice
    database-sourced content into the formula before it's evaluated."""
    table = EditableTableWidget()
    malicious_db_value = "__import__('os').system('id')-0"
    result = table.evaluate_formula_string(f"={malicious_db_value}", context_value=None)
    assert result.startswith("#ERROR")
