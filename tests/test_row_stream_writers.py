"""Tests for the CSV/XML row writers (issue #159)."""
import io

from utils.df_export import CsvRowStreamWriter, XmlRowStreamWriter


def test_csv_writer_writes_header_then_rows():
    buf = io.StringIO()
    w = CsvRowStreamWriter(buf, ["id", "name"])
    w.write_rows([(1, "Alice"), (2, "Bob")])
    w.close()
    assert buf.getvalue() == "id,name\r\n1,Alice\r\n2,Bob\r\n"


def test_csv_writer_none_becomes_empty_cell():
    buf = io.StringIO()
    w = CsvRowStreamWriter(buf, ["id", "name"])
    w.write_rows([(1, None)])
    assert buf.getvalue() == "id,name\r\n1,\r\n"


def test_csv_writer_hex_encodes_blobs_by_default():
    buf = io.StringIO()
    w = CsvRowStreamWriter(buf, ["id", "data"])
    w.write_rows([(1, b"\x01\xff")])
    assert "01ff" in buf.getvalue()


def test_csv_writer_decodes_blobs_when_hex_disabled():
    buf = io.StringIO()
    w = CsvRowStreamWriter(buf, ["id", "data"], blob_as_hex=False)
    w.write_rows([(1, b"hello")])
    assert "hello" in buf.getvalue()


def test_xml_writer_wraps_rows_in_table_root():
    buf = io.StringIO()
    w = XmlRowStreamWriter(buf, ["id", "name"], "users")
    w.write_rows([(1, "Alice")])
    w.close()
    text = buf.getvalue()
    assert text.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<table name="users">\n')
    assert "<row><id>1</id><name>Alice</name></row>" in text
    assert text.rstrip().endswith("</table>")


def test_xml_writer_escapes_special_characters():
    buf = io.StringIO()
    w = XmlRowStreamWriter(buf, ["name"], "t")
    w.write_rows([("A & B <C>",)])
    w.close()
    assert "A &amp; B &lt;C&gt;" in buf.getvalue()
