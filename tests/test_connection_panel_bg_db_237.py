"""Regression test for issue #237: one-off background DB operations (quick
copy/export, DDL, CSV import write, etc.) must run against a *dedicated*
DbService opened just for that call — never touching self.db_service, which
other tabs/the schema tree may be using concurrently with no locking
(services/db_service.py:373-377)."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ui.connection_panel import ConnectionPanel

_app = QApplication.instance() or QApplication([])


class _FakeSharedDbService:
    """Stands in for ConnectionPanel.db_service, the shared main connection
    — any recorded call here would mean _run_bg_db touched the wrong one."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def _record(*a, **k):
            self.calls.append(name)
        return _record


class _FakeDedicatedDbService:
    """Stands in for the fresh DbService _run_bg_db() opens per call."""
    instances = []

    def __init__(self):
        self.connected_with = None
        self.disconnected = False
        _FakeDedicatedDbService.instances.append(self)

    def connect(self, config):
        self.connected_with = config

    def disconnect(self):
        self.disconnected = True


class _PanelStub(QObject):
    _bg_op_done = Signal(str, object)
    _bg_op_error = Signal(str, str)

    def __init__(self, config, shared_db):
        super().__init__()
        self.config = config
        self.db_service = shared_db
        self._bg_ops = {}
        self._bg_op_done.connect(self._on_bg_op_done, Qt.QueuedConnection)
        self._bg_op_error.connect(self._on_bg_op_error, Qt.QueuedConnection)

    _run_bg_db = ConnectionPanel._run_bg_db
    _on_bg_op_done = ConnectionPanel._on_bg_op_done
    _on_bg_op_error = ConnectionPanel._on_bg_op_error


def _pump_until(predicate, timeout_ms=2000):
    elapsed = 0
    while not predicate() and elapsed < timeout_ms:
        QTest.qWait(10)
        elapsed += 10


def test_run_bg_db_uses_a_dedicated_connection_never_self_db_service(monkeypatch):
    monkeypatch.setattr("ui.connection_panel.DbService", _FakeDedicatedDbService)
    _FakeDedicatedDbService.instances.clear()

    shared_db = _FakeSharedDbService()
    panel = _PanelStub({"host": "db.example", "database": "app"}, shared_db)

    # ── Success path ────────────────────────────────────────────────────
    results = {}
    panel._run_bg_db(
        lambda db: "worked",
        on_done=lambda r: results.setdefault("done", r),
    )
    _pump_until(lambda: "done" in results)

    assert results["done"] == "worked"
    assert shared_db.calls == []   # never touched the shared connection
    assert len(_FakeDedicatedDbService.instances) == 1
    dedicated = _FakeDedicatedDbService.instances[0]
    assert dedicated.connected_with == panel.config
    assert dedicated.disconnected is True

    # ── Error path — still never touches the shared connection, and the
    # dedicated one is still closed ─────────────────────────────────────
    def _boom(db):
        raise RuntimeError("query failed")

    errors = {}
    panel._run_bg_db(_boom, on_done=lambda r: None,
                      on_error=lambda msg: errors.setdefault("error", msg))
    _pump_until(lambda: "error" in errors)

    assert errors["error"] == "query failed"
    assert shared_db.calls == []
    assert len(_FakeDedicatedDbService.instances) == 2
    assert _FakeDedicatedDbService.instances[1].disconnected is True
