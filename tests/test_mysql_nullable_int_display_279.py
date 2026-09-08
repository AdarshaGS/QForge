"""Regression tests for issue #279: a nullable MySQL integer column (e.g. a
BIGINT foreign key with at least one NULL row on the current page) showed
every value with a spurious ".0" — "1.0"/"2.0" instead of "1"/"2" — because
building a pandas DataFrame from row dicts promotes an int column with any
None in it to float64 (plain Python int has no NaN representation).

No live database needed: a fake cursor/connection stands in, matching this
suite's existing DbService-unit-test pattern.
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


def test_nullable_bigint_column_shows_plain_ints_not_dot_zero():
    # Mirrors the reported m_appuser.staff_id column: BIGINT FK, one row's
    # value is NULL, the rest are plain ints.
    description = [
        _desc("id", pymysql.FIELD_TYPE.LONGLONG),
        _desc("staff_id", pymysql.FIELD_TYPE.LONGLONG),
    ]
    rows = [
        {"id": 1, "staff_id": 1},
        {"id": 2, "staff_id": 2},
        {"id": 3, "staff_id": 4},
        {"id": 4, "staff_id": 2},
        {"id": 5, "staff_id": None},
    ]
    db = _db_with(description, rows)

    df = db.execute_query("SELECT id, staff_id FROM m_appuser")

    assert [str(v) for v in df["staff_id"][:4]] == ["1", "2", "4", "2"]
    assert pd.isna(df["staff_id"].iloc[4])
    assert str(df["id"].iloc[0]) == "1"


def test_decimal_column_is_untouched():
    """A genuine DECIMAL/NEWDECIMAL column must keep showing decimal
    places — only integer FIELD_TYPEs get the Int64 treatment."""
    import decimal
    description = [_desc("amount", pymysql.FIELD_TYPE.NEWDECIMAL)]
    rows = [{"amount": decimal.Decimal("1.50")}, {"amount": None}]
    db = _db_with(description, rows)

    df = db.execute_query("SELECT amount FROM payments")

    assert str(df["amount"].iloc[0]) == "1.50"


def test_float_column_is_untouched():
    description = [_desc("score", pymysql.FIELD_TYPE.DOUBLE)]
    rows = [{"score": 1.5}, {"score": None}]
    db = _db_with(description, rows)

    df = db.execute_query("SELECT score FROM results")

    assert df["score"].iloc[0] == 1.5


def test_non_nullable_int_column_unaffected_in_practice():
    """No NULLs at all — pandas would already infer int64 here, this just
    confirms the Int64 cast doesn't change the displayed value."""
    description = [_desc("id", pymysql.FIELD_TYPE.LONG)]
    rows = [{"id": 1}, {"id": 2}, {"id": 3}]
    db = _db_with(description, rows)

    df = db.execute_query("SELECT id FROM t")

    assert [str(v) for v in df["id"]] == ["1", "2", "3"]
