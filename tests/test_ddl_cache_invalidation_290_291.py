"""Regression tests for issues #290/#291.

#290: DbService._invalidate_schema_cache_if_ddl() was only ever called
from execute_update() — but the single-statement Run path (plain Run,
Ctrl+Return — the common way to run one ad-hoc DDL statement) always
calls execute_query() instead, even for a write/DDL statement
(_execute_query_raw handles both reads and writes generically). That
meant typing one ALTER TABLE and hitting plain Run left the on-disk
schema cache AND that connection's own in-memory metadata cache
completely un-invalidated — Run All was the only path that happened to
invalidate anything, because it explicitly routes writes through
execute_update().

#291: even with DDL cache invalidation firing, it only ever clears the
metadata cache of whichever DbService instance actually ran the
statement. Each SQL tab runs on its own dedicated DbService, separate
from the panel's shared self.db_service (schema browser/structure
editor/FK-aware features) — so a DDL run from a tab never told that
other, shared instance its cache was stale.
"""
import os

import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from services.db_service import DbService
from ui.connection_panel import ConnectionPanel

_app = QApplication.instance() or QApplication([])


def _db(id_="conn-1"):
    db = DbService()
    db.db_type = "mysql"
    db.connection = object()
    db.read_only = False
    db.in_transaction = False
    db._config = {"type": "mysql", "name": "test", "database": "shop", "id": id_}
    return db


def test_execute_query_invalidates_cache_for_a_single_ddl_statement(monkeypatch):
    invalidated = []
    monkeypatch.setattr(
        "services.db_service.schema_cache.invalidate",
        lambda conn_id, database: invalidated.append((conn_id, database)),
    )
    db = _db()
    db._metadata_cache[("columns", "users")] = ["stale"]
    db._execute_query_raw = lambda q, max_rows=None: pd.DataFrame(
        [{"Query OK, rows affected": 0}]
    )

    db.execute_query("ALTER TABLE users ADD COLUMN nickname TEXT")

    assert invalidated == [("conn-1", "shop")]
    assert db._metadata_cache == {}


def test_execute_query_does_not_invalidate_cache_for_a_plain_select(monkeypatch):
    invalidated = []
    monkeypatch.setattr(
        "services.db_service.schema_cache.invalidate",
        lambda *a: invalidated.append(a),
    )
    db = _db()
    db._metadata_cache[("columns", "users")] = ["cached"]
    db._execute_query_raw = lambda q, max_rows=None: pd.DataFrame({"id": [1]})

    db.execute_query("SELECT * FROM users")

    assert invalidated == []
    assert db._metadata_cache == {("columns", "users"): ["cached"]}


def test_execute_query_invalidation_does_not_run_on_a_failed_statement(monkeypatch):
    invalidated = []
    monkeypatch.setattr(
        "services.db_service.schema_cache.invalidate",
        lambda *a: invalidated.append(a),
    )
    db = _db()
    db._metadata_cache[("columns", "users")] = ["cached"]

    def _raise(*a, **k):
        raise ValueError("syntax error")
    db._execute_query_raw = _raise
    db._is_connection_error = lambda ex: False

    with pytest.raises(ValueError):
        db.execute_query("ALTER TABLE users ADD COLUMN nickname TEXT")

    assert invalidated == []
    assert db._metadata_cache == {("columns", "users"): ["cached"]}


# ─── ConnectionPanel: the panel's shared db_service also gets cleared ─────


class _SharedDbStub:
    def __init__(self):
        self.clear_calls = 0

    def clear_metadata_cache(self):
        self.clear_calls += 1


class _PanelStub:
    _invalidate_shared_metadata_cache_if_ddl = ConnectionPanel._invalidate_shared_metadata_cache_if_ddl

    def __init__(self):
        self.db_service = _SharedDbStub()


def test_shared_cache_is_cleared_when_a_ddl_statement_ran_in_a_tab():
    panel = _PanelStub()
    panel._invalidate_shared_metadata_cache_if_ddl("ALTER TABLE users ADD COLUMN nickname TEXT")
    assert panel.db_service.clear_calls == 1


def test_shared_cache_is_not_cleared_for_a_non_ddl_query():
    panel = _PanelStub()
    panel._invalidate_shared_metadata_cache_if_ddl("SELECT * FROM users")
    assert panel.db_service.clear_calls == 0


def test_shared_cache_is_cleared_when_any_statement_in_a_script_is_ddl():
    panel = _PanelStub()
    panel._invalidate_shared_metadata_cache_if_ddl(
        "SELECT 1; ALTER TABLE users ADD COLUMN nickname TEXT; SELECT 2;"
    )
    assert panel.db_service.clear_calls == 1


def test_shared_cache_helper_is_a_noop_without_a_db_service():
    class _NoDbPanel:
        _invalidate_shared_metadata_cache_if_ddl = ConnectionPanel._invalidate_shared_metadata_cache_if_ddl
        db_service = None

    _NoDbPanel()._invalidate_shared_metadata_cache_if_ddl("ALTER TABLE x ADD COLUMN y INT")  # must not raise
