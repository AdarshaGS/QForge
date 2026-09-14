"""Regression tests for issue #292: mock data generation into a parent+child
table pair (build_dependency_chain) ran every INSERT statement with no
explicit transaction — each one auto-committed as it ran. A failure
partway through the children (an FK/constraint violation, a cancelled
connection, ...) left the parent rows permanently committed with only
some of their children present, with no way to tell from the reported
result whether the DB now had orphaned data.

ConnectionPanel._insert_mock_data_atomically() now wraps the whole
statement list (and the post-insert sequence bumps) in one transaction:
either everything commits together, or a rollback undoes all of it.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from services import query_classifier
from ui.connection_panel import ConnectionPanel

_app = QApplication.instance() or QApplication([])


class _FakeDb:
    """Records begin/commit/rollback calls and every statement executed,
    so tests can assert transactional behavior without a real database."""

    def __init__(self, fail_on_statement_containing=None, bump_results=None):
        self.calls = []
        self._fail_on = fail_on_statement_containing
        self._bump_results = bump_results or {}

    def begin_transaction(self):
        self.calls.append("begin")

    def commit_transaction(self):
        self.calls.append("commit")

    def rollback_transaction(self):
        self.calls.append("rollback")

    def execute_update(self, stmt):
        self.calls.append(("execute", stmt))
        if self._fail_on and self._fail_on in stmt:
            raise Exception(f"constraint violation: {stmt}")

    def bump_sequence_for_column(self, table, column):
        self.calls.append(("bump", table, column))
        return self._bump_results.get((table, column), True)


def test_all_statements_commit_together_on_success():
    db = _FakeDb()
    sql = "INSERT INTO parents (id) VALUES (1); INSERT INTO children (parent_id) VALUES (1);"

    result = ConnectionPanel._insert_mock_data_atomically(db, sql, [("parents", "id")])

    assert result == []  # no failed bumps
    assert db.calls[0] == "begin"
    assert db.calls[-1] == "commit"
    assert "rollback" not in db.calls
    executed = [c[1] for c in db.calls if isinstance(c, tuple) and c[0] == "execute"]
    assert len(executed) == 2


def test_a_failure_partway_through_children_rolls_back_everything():
    # Parent statement succeeds, child statement fails — without the
    # transaction wrapper this would leave the parent row committed with
    # no matching child (the exact FK-inconsistency #292 is about).
    db = _FakeDb(fail_on_statement_containing="children")
    sql = "INSERT INTO parents (id) VALUES (1); INSERT INTO children (parent_id) VALUES (1);"

    try:
        ConnectionPanel._insert_mock_data_atomically(db, sql, [("parents", "id")])
        assert False, "expected the child statement's exception to propagate"
    except Exception as ex:
        assert "constraint violation" in str(ex)

    assert db.calls[0] == "begin"
    assert db.calls[-1] == "rollback"
    assert "commit" not in db.calls
    # The parent statement DID run against the connection, but since it's
    # all inside one transaction that then rolled back, nothing from it
    # is actually committed to the database.
    executed = [c[1] for c in db.calls if isinstance(c, tuple) and c[0] == "execute"]
    assert any("parents" in s for s in executed)


def test_sequence_bump_never_runs_after_a_failed_statement():
    db = _FakeDb(fail_on_statement_containing="children")
    sql = "INSERT INTO parents (id) VALUES (1); INSERT INTO children (parent_id) VALUES (1);"

    try:
        ConnectionPanel._insert_mock_data_atomically(db, sql, [("parents", "id")])
    except Exception:
        pass

    assert not any(isinstance(c, tuple) and c[0] == "bump" for c in db.calls)


def test_failed_sequence_bumps_are_returned_but_still_commit():
    # A sequence-bump failure (issue #293) is best-effort and must not
    # abort or roll back an otherwise-successful insert.
    db = _FakeDb(bump_results={("parents", "id"): False})
    sql = "INSERT INTO parents (id) VALUES (1);"

    result = ConnectionPanel._insert_mock_data_atomically(db, sql, [("parents", "id")])

    assert result == [("parents", "id")]
    assert db.calls[-1] == "commit"
    assert "rollback" not in db.calls


def test_split_statements_used_matches_query_classifier():
    # Sanity check that the helper really does split on ';' the same way
    # the rest of the app does, rather than a naive split.
    db = _FakeDb()
    sql = "INSERT INTO t (a) VALUES ('x;y'); INSERT INTO t (a) VALUES ('z');"

    ConnectionPanel._insert_mock_data_atomically(db, sql, [])

    executed = [c[1] for c in db.calls if isinstance(c, tuple) and c[0] == "execute"]
    assert executed == query_classifier.split_statements(sql)
