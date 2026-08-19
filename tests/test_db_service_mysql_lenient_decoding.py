"""Regression tests for issue #150 — a non-UTF-8-clean byte in a MySQL
string column must not abort the entire page fetch.

pymysql has no public hook for this (see the comment on
_ensure_lenient_mysql_decoding in services/db_service.py for why `conv`/
decoders doesn't work): the actual strict `data.decode(encoding)` call is
hardcoded inside the private MySQLResult._read_row_from_packet. These
tests exercise that exact function directly with a real invalid-UTF-8
byte sequence (0xd3 alone — the exact example from the issue), rather than
trying to coerce a live MySQL server into storing invalid bytes in a
column it validates on write (it does, defensively, in every mode tried).
"""
import pytest

pytest.importorskip("pymysql")
import pymysql.connections as pymysql_connections

from services.db_service import _ensure_lenient_mysql_decoding, _lenient_read_row_from_packet


class _FakePacket:
    """Stands in for pymysql's MysqlPacket — read_length_coded_string()
    is the only method _read_row_from_packet calls on it."""
    def __init__(self, values):
        self._values = list(values)

    def read_length_coded_string(self):
        if not self._values:
            raise IndexError("no more columns")
        return self._values.pop(0)


class _FakeResult:
    """Stands in for MySQLResult — only .converters is read."""
    def __init__(self, converters):
        self.converters = converters


# 0xd3 alone is an invalid UTF-8 continuation byte — the exact byte from
# the issue's reproduction ("'utf-8' codec can't decode byte 0xd3...").
_BAD_BYTES = b"\xd3"


def test_original_pymysql_behavior_raises_on_bad_bytes():
    """Sanity check that this is a real pymysql limitation, not an assumption."""
    result = _FakeResult(converters=[("utf-8", None)])
    packet = _FakePacket([_BAD_BYTES])
    with pytest.raises(UnicodeDecodeError):
        pymysql_connections.MySQLResult._read_row_from_packet(result, packet)


def test_patched_decoder_replaces_bad_bytes_instead_of_raising():
    result = _FakeResult(converters=[("utf-8", None)])
    packet = _FakePacket([_BAD_BYTES])

    row = _lenient_read_row_from_packet(result, packet)

    assert row == ("�",)  # U+FFFD REPLACEMENT CHARACTER


def test_patched_decoder_leaves_clean_rows_unaffected():
    result = _FakeResult(converters=[("utf-8", None), ("utf-8", None)])
    packet = _FakePacket(["ok".encode(), "héllo".encode()])

    row = _lenient_read_row_from_packet(result, packet)

    assert row == ("ok", "héllo")


def test_ensure_lenient_mysql_decoding_patches_the_real_pymysql_class():
    _ensure_lenient_mysql_decoding()
    assert pymysql_connections.MySQLResult._read_row_from_packet is _lenient_read_row_from_packet

    # And the patched class method itself now survives what used to raise.
    result = _FakeResult(converters=[("utf-8", None)])
    packet = _FakePacket([_BAD_BYTES])
    row = pymysql_connections.MySQLResult._read_row_from_packet(result, packet)
    assert row == ("�",)
