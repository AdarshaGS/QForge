"""Regression test for issue #286: verify BEGIN/COMMIT/ROLLBACK detection
when mixed with other statements in one Run All script.

_transaction_kind() only recognizes a query as transaction control when it
is exactly one statement — the concern raised in #286 was that Run All
("BEGIN; SELECT ...; COMMIT;") sends the whole script to execute_multi_query
as one string, which would never take the transaction-control path.

That's not what happens, though: execute_multi_query() splits the script
first (query_classifier.split_statements) and dispatches each already-split
statement individually through execute_query()/execute_update() — so
_transaction_kind() only ever sees one statement at a time and detects
BEGIN/COMMIT/ROLLBACK correctly regardless of what else is in the script.
This test locks that behavior in so a future refactor of the dispatch loop
can't silently regress it back into the bug #286 described.
"""
import pandas as pd

from services.db_service import DbService


def _make_service():
    svc = DbService()
    svc.connection = object()  # truthy, so execute_query's guard clause passes
    svc.db_type = "mysql"
    svc.read_only = False
    svc.in_transaction = False

    calls = []
    svc.begin_transaction = lambda: (calls.append("begin"), setattr(svc, "in_transaction", True))
    svc.commit_transaction = lambda: (calls.append("commit"), setattr(svc, "in_transaction", False))
    svc.rollback_transaction = lambda: (calls.append("rollback"), setattr(svc, "in_transaction", False))
    svc._execute_query_raw = lambda query, max_rows=None: (
        calls.append(f"raw:{query}") or pd.DataFrame({"x": [1]})
    )
    svc._execute_update_raw = lambda query: (calls.append(f"raw_update:{query}") or 1)
    return svc, calls


def test_begin_commit_mixed_with_a_select_take_the_transaction_control_path():
    svc, calls = _make_service()

    results = svc.execute_multi_query("BEGIN; SELECT 1; COMMIT;")

    assert "begin" in calls
    assert "commit" in calls
    # BEGIN/COMMIT must never reach the driver as raw SQL — they're handled
    # by begin_transaction()/commit_transaction(), not _execute_query_raw.
    assert not any(c.startswith("raw:BEGIN") or c.startswith("raw:COMMIT") for c in calls)
    assert svc.in_transaction is False  # ended in a clean, committed state

    labels_and_status = [
        (label, df.iloc[0].to_dict()) for label, df, _ in results if isinstance(df, pd.DataFrame)
    ]
    assert ("BEGIN;", {"Transaction": "Started"}) in labels_and_status
    assert ("COMMIT;", {"Transaction": "Committed"}) in labels_and_status


def test_in_transaction_stays_accurate_across_the_whole_script():
    svc, calls = _make_service()

    svc.execute_multi_query("BEGIN; UPDATE a SET x=1; SELECT 1; ROLLBACK;")

    assert "begin" in calls
    assert "rollback" in calls
    assert svc.in_transaction is False


def test_a_script_with_begin_but_no_matching_commit_leaves_transaction_open():
    svc, calls = _make_service()

    svc.execute_multi_query("BEGIN; SELECT 1;")

    assert "begin" in calls
    assert "commit" not in calls
    assert svc.in_transaction is True
