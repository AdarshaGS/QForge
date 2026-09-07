"""Regression test for issue #220: _is_connection_error must recognize
psycopg2's actual "connection already closed" message, not just the
literal substring "connection closed" it previously required.
"""
from services.db_service import DbService


def test_is_connection_error_matches_psycopg2_already_closed():
    db = DbService()
    assert db._is_connection_error(Exception("connection already closed")) is True
