"""Tests for issue #183's incremental-polish acceptance criteria:
CTE names completable as a table (with inferred output columns where
possible), and derived-table aliases (`FROM (SELECT ...) AS x`) resolving
for `x.column` completion — plus a large-schema latency spot-check."""
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QPlainTextEdit

from ui.sql_completer import SqlCompleter, SuggestionItem

_app = QApplication.instance() or QApplication([])


def _make_completer(query: str) -> SqlCompleter:
    """A real SqlCompleter over a throwaway editor, schema pre-loaded with
    orders/users, with CTE/derived-table/alias state refreshed from
    *query* exactly like update() does on every keystroke."""
    editor = QPlainTextEdit()
    editor.setPlainText(query)
    completer = SqlCompleter(editor)
    completer.set_schema(
        tables=["orders", "users"],
        columns_dict={
            "orders": ["id", "user_id", "total", "created_at"],
            "users":  ["id", "email"],
        },
    )
    completer._ctes = completer._extract_ctes(query)
    completer._derived_columns = completer._extract_derived_tables(query)
    completer._aliases = completer._extract_aliases(query)
    for alias in completer._derived_columns:
        completer._aliases[alias.lower()] = alias
    return completer


def _find(items, text):
    return next((i for i in items if i.text == text), None)


# ── CTE names completable as a table ────────────────────────────────────

def test_cte_name_is_suggested_after_from():
    query = ("WITH recent_orders AS (SELECT id, total FROM orders) "
              "SELECT * FROM rec")
    completer = _make_completer(query)
    items = completer._build_suggestions("rec", "AFTER_FROM", query, len(query))
    match = _find(items, "recent_orders")
    assert match is not None
    assert match.kind == SuggestionItem.TABLE
    assert match.extra.get("badge") == "CTE"


def test_cte_columns_are_inferred_from_simple_select_list():
    query = "WITH recent_orders AS (SELECT id, total FROM orders) SELECT * FROM x"
    completer = _make_completer(query)
    assert completer._ctes["recent_orders"] == ["id", "total"]


def test_cte_column_completes_after_referencing_the_cte():
    query = "WITH recent_orders AS (SELECT id, total FROM orders) SELECT to FROM recent_orders"
    completer = _make_completer(query)
    pos = query.index("to") + 2
    items = completer._build_suggestions("to", "AFTER_SELECT", query, pos)
    assert _find(items, "total") is not None


def test_cte_dot_notation_resolves_columns():
    query = "WITH ro AS (SELECT id, total FROM orders) SELECT ro.t FROM ro"
    completer = _make_completer(query)
    items = completer._dot_suggestions("ro", "t")
    assert [i.text for i in items] == ["ro.total"]


def test_cte_with_explicit_column_list_uses_those_names():
    query = "WITH ro (a, b) AS (SELECT id, total FROM orders) SELECT ro.a FROM ro"
    completer = _make_completer(query)
    assert completer._ctes["ro"] == ["a", "b"]


def test_cte_with_select_star_has_no_fabricated_columns():
    # SELECT * gives no confidently-named columns — the CTE must still be
    # completable as a table, just with an empty (not guessed) column list.
    query = "WITH all_orders AS (SELECT * FROM orders) SELECT * FROM all_o"
    completer = _make_completer(query)
    assert completer._ctes == {"all_orders": []}
    items = completer._build_suggestions("all_o", "AFTER_FROM", query, len(query))
    assert _find(items, "all_orders") is not None


def test_multiple_ctes_keep_independent_column_sets():
    query = ("WITH cte1 AS (SELECT id, total FROM orders), "
              "cte2 AS (SELECT id, user_id FROM orders) "
              "SELECT * FROM cte1 JOIN cte2 ON cte1.id = cte2.id")
    completer = _make_completer(query)
    assert completer._ctes["cte1"] == ["id", "total"]
    assert completer._ctes["cte2"] == ["id", "user_id"]
    assert completer._tables_in_query(query)[-2:] == ["cte1", "cte2"]


def test_with_recursive_is_still_recognized():
    query = "WITH RECURSIVE cte1 AS (SELECT id FROM orders) SELECT * FROM cte1"
    completer = _make_completer(query)
    assert "cte1" in completer._ctes


def test_group_by_with_rollup_is_not_mistaken_for_a_cte():
    query = "SELECT user_id, SUM(total) FROM orders GROUP BY user_id WITH ROLLUP"
    completer = _make_completer(query)
    assert completer._ctes == {}


def test_no_with_clause_yields_no_ctes():
    completer = _make_completer("SELECT * FROM orders")
    assert completer._ctes == {}


# ── Derived-table aliases ───────────────────────────────────────────────

def test_derived_table_alias_resolves_inferred_columns():
    query = "SELECT x.tot FROM (SELECT id, total AS tot FROM orders) AS x"
    completer = _make_completer(query)
    assert completer._derived_columns["x"] == ["id", "tot"]
    items = completer._dot_suggestions("x", "tot")
    assert [i.text for i in items] == ["x.tot"]


def test_derived_table_alias_without_as_keyword_still_resolves():
    query = "SELECT y.id FROM (SELECT id FROM users) y"
    completer = _make_completer(query)
    assert completer._derived_columns == {"y": ["id"]}


def test_derived_table_column_completes_via_dot_notation_query():
    query = "SELECT x. FROM (SELECT id, total AS tot FROM orders) AS x"
    completer = _make_completer(query)
    pos = query.index("x.") + 2
    prefix = completer._current_prefix(query, pos)
    assert prefix == "x."
    obj, partial = prefix.rsplit(".", 1)
    items = completer._dot_suggestions(obj, partial)
    assert sorted(i.text for i in items) == ["x.id", "x.tot"]


def test_scalar_subquery_in_where_is_not_treated_as_derived_table():
    query = "SELECT * FROM orders WHERE user_id IN (SELECT id FROM users)"
    completer = _make_completer(query)
    assert completer._derived_columns == {}


# ── Large-schema responsiveness (acceptance criterion #3) ──────────────

def test_suggestion_latency_stays_low_with_thousands_of_tables():
    n = 5000
    tables = [f"table_{i:05d}" for i in range(n)]
    columns = {t: [f"col_{j}" for j in range(15)] for t in tables}

    editor = QPlainTextEdit()
    completer = SqlCompleter(editor)
    completer.set_schema(tables=tables, columns_dict=columns)

    query = "SELECT * FROM tb2"
    completer._ctes = completer._extract_ctes(query)
    completer._derived_columns = completer._extract_derived_tables(query)
    completer._aliases = completer._extract_aliases(query)

    # Worst case: a short prefix that forces the fuzzy-match fallback
    # across every one of n tables (nothing starts-with/contains "tb2"
    # cleanly other than by fuzzy character-order matching).
    t0 = time.perf_counter()
    items = completer._build_suggestions("tb2", "AFTER_FROM", query, len(query))
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert items
    assert elapsed_ms < 50, f"suggestion build took {elapsed_ms:.1f}ms for {n} tables"
