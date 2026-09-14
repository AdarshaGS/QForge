"""Regression tests for issue #293: DbService.bump_sequence_for_column()
used to swallow every failure and return None either way, so a mock-data
generation run into a Postgres table with an explicit PK, followed by a
setval() failure (permissions, an unusual sequence name shape, ...), gave
no indication that a later auto-assigned insert into that table could
collide with the value just written.

bump_sequence_for_column() now returns True/False (still never raises —
it stays best-effort and must not block the generation flow), and
ConnectionPanel._mock_data_insert_message() surfaces any failures in the
result dialog's message instead of a silent "Success".
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from services.db_service import DbService
from ui.connection_panel import ConnectionPanel

_app = QApplication.instance() or QApplication([])


def _pg_db():
    db = DbService()
    db.db_type = "postgresql"
    return db


def test_bump_sequence_returns_true_for_mysql_without_touching_the_connection():
    db = DbService()
    db.db_type = "mysql"
    db.connection = None  # would blow up if bump_sequence_for_column tried to use it

    assert db.bump_sequence_for_column("orders", "id") is True


def test_bump_sequence_returns_true_on_postgres_success():
    db = _pg_db()

    class _Cursor:
        def execute(self, sql, params):
            pass
        def close(self):
            pass

    db.connection = type("C", (), {"cursor": lambda self: _Cursor()})()

    assert db.bump_sequence_for_column("orders", "id") is True


def test_bump_sequence_returns_false_and_does_not_raise_on_postgres_failure():
    db = _pg_db()

    class _Cursor:
        def execute(self, sql, params):
            raise Exception("permission denied for sequence orders_id_seq")
        def close(self):
            pass

    db.connection = type("C", (), {"cursor": lambda self: _Cursor()})()

    assert db.bump_sequence_for_column("orders", "id") is False  # no exception propagates


def test_success_message_has_no_warning_when_nothing_failed():
    message = ConnectionPanel._mock_data_insert_message("orders", [])
    assert message == "Mock data inserted into orders."
    assert "Warning" not in message


def test_success_message_warns_about_a_single_failed_bump():
    message = ConnectionPanel._mock_data_insert_message("orders", [("orders", "id")])
    assert "Mock data inserted into orders." in message
    assert "orders.id" in message
    assert "Warning" in message
    assert "collide" in message


def test_success_message_warns_about_multiple_failed_bumps():
    message = ConnectionPanel._mock_data_insert_message(
        "orders", [("orders", "id"), ("order_items", "id")]
    )
    assert "orders.id" in message
    assert "order_items.id" in message
    assert "those tables" in message  # plural phrasing for 2+ failures
