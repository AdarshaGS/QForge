"""Regression tests for issue #278: a MySQL BIT(n) column (the common way
to store a boolean flag — `enabled BIT(1)`, etc.) comes back from pymysql
as raw bytes, since pymysql has no built-in converter for FIELD_TYPE.BIT.
Left alone, that got displayed/exported the same way a genuine BLOB column
is — hex text ("00"/"01" instead of a plain "0"/"1").

No live database needed: a fake cursor/connection stands in, matching this
suite's existing DbService-unit-test pattern (test_db_service_metadata_cache.py,
test_clone_database_274.py).
"""
import pandas as pd
import pymysql

from services.db_service import DbService


class _FakeCursor:
    def __init__(self, description, rows):
        self.description = description
        self._rows = rows
        self.rowcount = len(rows)

    def execute(self, query):
        pass

    def fetchall(self):
        return self._rows

    def fetchmany(self, n):
        rows, self._rows = self._rows[:n], self._rows[n:]
        return rows

    def close(self):
        pass


class _FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def _db_with(description, rows):
    db = DbService()
    db.db_type = "mysql"
    db._config = {"type": "mysql", "name": "test", "database": "shop", "id": "conn-1"}
    db.connection = _FakeConnection(_FakeCursor(description, rows))
    db.read_only = False
    db.in_transaction = False
    return db


def _desc(name, type_code):
    return (name, type_code, None, None, None, None, None)


def test_bit_column_decoded_to_plain_int_not_hex_bytes():
    description = [
        _desc("email", pymysql.FIELD_TYPE.VAR_STRING),
        _desc("enabled", pymysql.FIELD_TYPE.BIT),
    ]
    rows = [
        {"email": "a@example.com", "enabled": b"\x01"},
        {"email": "b@example.com", "enabled": b"\x00"},
    ]
    db = _db_with(description, rows)

    df = db.execute_query("SELECT email, enabled FROM users")

    assert list(df["enabled"]) == [1, 0]
    assert str(df["enabled"].iloc[0]) == "1"  # not "01"


def test_non_bit_bytes_column_is_left_alone():
    """A genuine BLOB column must not be touched — only FIELD_TYPE.BIT is."""
    description = [_desc("encryption_key", pymysql.FIELD_TYPE.BLOB)]
    rows = [{"encryption_key": b"\x01\x02"}]
    db = _db_with(description, rows)

    df = db.execute_query("SELECT encryption_key FROM secrets")

    assert df["encryption_key"].iloc[0] == b"\x01\x02"


def test_null_bit_value_stays_null():
    description = [_desc("enabled", pymysql.FIELD_TYPE.BIT)]
    rows = [{"enabled": None}]
    db = _db_with(description, rows)

    df = db.execute_query("SELECT enabled FROM users")

    # issue #279: BIT columns are now cast to pandas' nullable Int64
    # dtype (same as other MySQL integer columns), so NULL surfaces as
    # pd.NA rather than plain None — still recognized as NULL by every
    # pd.isna()-based check elsewhere in the app.
    assert pd.isna(df["enabled"].iloc[0])


def test_no_bit_columns_is_a_no_op():
    description = [_desc("id", pymysql.FIELD_TYPE.LONG)]
    rows = [{"id": 42}]
    db = _db_with(description, rows)

    df = db.execute_query("SELECT id FROM users")

    assert list(df["id"]) == [42]
