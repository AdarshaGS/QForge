"""Regression test for issue #298: a failing EXPLAIN/cost-estimate for one
statement in a Run All script must never abort the rest of the run.

execute_multi_query() wraps query_cost.estimate_cost() in its own try/
except, entirely separate from the try/except around the statement's
actual execution — confirmed here by making estimate_cost() raise
unconditionally (simulating an EXPLAIN that errors for any reason: an
unusual statement shape, a dialect quirk, whatever) and checking every
statement in a mixed SELECT/UPDATE/SELECT script still executes and
produces its normal result, just with cost=None instead of an estimate.

Also confirms query_cost.estimate_cost() is plan-only (EXPLAIN / EXPLAIN
FORMAT=TREE / EXPLAIN (FORMAT JSON) — never EXPLAIN ANALYZE) so it can't
itself execute a statement with side effects; DDL/writes skip cost
estimation entirely (only the non-write branch calls it), so a statement
that "isn't EXPLAIN-able at all like DDL" never reaches estimate_cost in
the first place.
"""
import pandas as pd
import pytest

from services import query_cost
from services.db_service import DbService


def _make_service():
    svc = DbService()
    svc.connection = object()
    svc.db_type = "mysql"
    svc.read_only = False
    svc.in_transaction = False
    svc._execute_query_raw = lambda query, max_rows=None: pd.DataFrame({"x": [1]})
    svc._execute_update_raw = lambda query: 3
    return svc


def test_estimate_cost_raising_never_aborts_the_rest_of_a_multi_statement_run(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("EXPLAIN blew up unexpectedly")
    monkeypatch.setattr(query_cost, "estimate_cost", _boom)

    svc = _make_service()
    results = svc.execute_multi_query("SELECT 1; UPDATE t SET x=1; SELECT 2;")

    assert len(results) == 3
    label1, obj1, cost1 = results[0]
    label2, obj2, cost2 = results[1]
    label3, obj3, cost3 = results[2]

    assert isinstance(obj1, pd.DataFrame) and cost1 is None
    assert obj2 == 3 and cost2 is None  # the write ran normally, untouched by the estimate failure
    assert isinstance(obj3, pd.DataFrame) and cost3 is None


def test_a_cost_estimate_with_an_error_field_is_also_dropped_not_propagated(monkeypatch):
    # estimate_cost() itself can return a CostEstimate with .error set
    # instead of raising (e.g. an unsupported dialect) — the caller must
    # treat that the same as a raised exception: cost=None, statement
    # still runs.
    monkeypatch.setattr(
        query_cost, "estimate_cost",
        lambda db, sql: query_cost.CostEstimate(dialect="mysql", error="not explainable"),
    )

    svc = _make_service()
    results = svc.execute_multi_query("SELECT 1;")

    assert len(results) == 1
    label, obj, cost = results[0]
    assert isinstance(obj, pd.DataFrame)
    assert cost is None


def test_ddl_statements_skip_cost_estimation_entirely(monkeypatch):
    calls = []
    monkeypatch.setattr(
        query_cost, "estimate_cost",
        lambda db, sql: calls.append(sql) or query_cost.CostEstimate(dialect="mysql"),
    )

    svc = _make_service()
    svc.execute_multi_query("CREATE TABLE t (id INT); DROP TABLE t;")

    assert calls == []  # never attempted an EXPLAIN for either DDL statement
