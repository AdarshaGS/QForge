"""Shared CSV/JSON/Excel/SQL export helper for pandas DataFrames."""
import csv
import re

import pandas as pd

_FILTERS = "CSV Files (*.csv);;JSON Files (*.json);;Excel Files (*.xlsx);;SQL Insert (*.sql)"


def _xml_escape(text: str) -> str:
    # xml.sax.saxutils.escape is bandit-blacklisted as an XML-parsing risk
    # even though it (like this) only ever escapes outgoing text — a plain
    # replace avoids the false positive without pulling in defusedxml.
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

_AUTO_INCREMENT_RE = re.compile(r"\s*AUTO_INCREMENT=\d+", re.IGNORECASE)
_GENERATED_RE = re.compile(r"\bGENERATED\s+ALWAYS\s+AS\s*\(", re.IGNORECASE)


def strip_auto_increment_value(ddl: str) -> str:
    """Remove the current-counter `AUTO_INCREMENT=N` clause MySQL embeds in
    `SHOW CREATE TABLE` output (issue #160), so re-importing the dump
    starts the counter fresh instead of resuming from the source table's
    last value. A no-op on dialects whose DDL doesn't contain this clause
    (Postgres has no such text to strip)."""
    return _AUTO_INCREMENT_RE.sub("", ddl)


def strip_generated_column_clauses(ddl: str) -> str:
    """Remove `GENERATED ALWAYS AS (...) STORED|VIRTUAL` clauses from a
    `CREATE TABLE` statement (issue #160). Tracks paren depth rather than
    using a naive non-greedy regex, so a nested expression like
    `GENERATED ALWAYS AS ((a + (b * c))) STORED` isn't cut short at the
    first `)`."""
    out = []
    i = 0
    for m in _GENERATED_RE.finditer(ddl):
        out.append(ddl[i:m.start()])
        depth = 1
        j = m.end()
        while j < len(ddl) and depth > 0:
            if ddl[j] == "(":
                depth += 1
            elif ddl[j] == ")":
                depth -= 1
            j += 1
        tail = re.match(r"\s*(STORED|VIRTUAL)\b", ddl[j:], re.IGNORECASE)
        if tail:
            j += tail.end()
        i = j
    out.append(ddl[i:])
    result = "".join(out)
    # Removing a clause can leave a stray space before the next `,`/`)`.
    result = re.sub(r"[ \t]+,", ",", result)
    result = re.sub(r"[ \t]+\)", ")", result)
    return result


def _cell_str(val, blob_as_hex: bool = True) -> str:
    if val is None:
        return ""
    if isinstance(val, (bytes, bytearray)):
        return val.hex() if blob_as_hex else val.decode("utf-8", errors="replace")
    return str(val)


_FORMULA_LEAD_CHARS = ("=", "+", "-", "@")


def _csv_formula_guard(value: str) -> str:
    """Neutralize spreadsheet-formula injection (issue #115): a cell whose
    text begins with =, +, -, or @ is read as a formula by Excel/Numbers/
    Sheets when a CSV QForge exported is later opened there — e.g. a DB
    value of `=cmd(...)` would execute on open. A leading apostrophe forces
    literal-text interpretation there (shown only in the formula bar, not
    the cell itself) — the standard OWASP-documented mitigation. Applied to
    every cell regardless of the source column's type, since by the time a
    value reaches here it's already display text; the one side effect is a
    negative number importing back as text rather than numeric, which is
    the accepted trade-off for closing the code-execution vector."""
    return "'" + value if value[:1] in _FORMULA_LEAD_CHARS else value


class CsvRowStreamWriter:
    """Incrementally writes CSV rows to an open text file handle (issue
    #159), header first. Column values that are `bytes`/`bytearray` are
    hex-encoded (or best-effort decoded) via the same `blob_as_hex` toggle
    the SQL export uses. Cells are also passed through _csv_formula_guard
    (issue #115)."""

    def __init__(self, fh, columns, blob_as_hex: bool = True):
        self._blob_as_hex = blob_as_hex
        self._writer = csv.writer(fh)
        self._writer.writerow(columns)

    def write_rows(self, rows):
        for row in rows:
            self._writer.writerow(
                [_csv_formula_guard(_cell_str(v, self._blob_as_hex)) for v in row])

    def close(self):
        pass


class XmlRowStreamWriter:
    """Incrementally writes `<row><col>value</col>...</row>` elements to an
    open text file handle, wrapped in a `<table name="...">` root (issue
    #159). Column names are used verbatim as element tags — callers should
    expect trouble only for identifiers that aren't valid XML names, which
    SQL databases rarely allow anyway."""

    def __init__(self, fh, columns, table_name: str, blob_as_hex: bool = True):
        self._fh = fh
        self._columns = columns
        self._blob_as_hex = blob_as_hex
        fh.write(f'<?xml version="1.0" encoding="UTF-8"?>\n<table name="{_xml_escape(table_name)}">\n')

    def write_rows(self, rows):
        for row in rows:
            cells = "".join(
                f"<{col}>{_xml_escape(_cell_str(v, self._blob_as_hex))}</{col}>"
                for col, v in zip(self._columns, row)
            )
            self._fh.write(f"  <row>{cells}</row>\n")

    def close(self):
        self._fh.write("</table>\n")


def _quote_identifier(name: str, dialect: str = "mysql") -> str:
    # Doubling an embedded quote char is the standard escape for both
    # dialects — matches _sql_value_literal's handling of embedded '.
    # Without it, a table/column name containing a backtick/quote breaks
    # out of the identifier context in the generated SQL (issue #162).
    if dialect == "mysql":
        return f"`{name.replace(chr(96), chr(96) * 2)}`"
    return f'"{name.replace(chr(34), chr(34) * 2)}"'


def _sql_value_literal(val, dialect: str = "mysql", blob_as_hex: bool = True) -> str:
    """Checks None/str first — the two overwhelmingly common cases in a
    streamed export, where values are plain values from a DB cursor and
    never real pandas NaN/NaT — before falling through to pd.isna() for
    the remaining, rarer types (numeric NaN, NaT, Decimal, etc.) it's
    still needed for on the DataFrame-sourced call path (_copy_insert_
    script). Reordering only skips pd.isna() calls it would've returned
    False from anyway (pd.isna() never returns True for a str), so this
    is a pure speed win, not a behavior change."""
    if val is None:
        return "NULL"
    if isinstance(val, (bytes, bytearray)):
        if not blob_as_hex:
            return "NULL"
        hexstr = val.hex()
        return f"E'\\\\x{hexstr}'" if dialect == "postgresql" else f"X'{hexstr}'"
    if isinstance(val, str):
        escaped = val.replace("'", "''")
        # MySQL (not Postgres, under the modern standard_conforming_strings
        # default) treats backslash as an escape character inside '...' —
        # an unescaped one silently eats the next character instead of
        # erroring: re-importing 'C:\Users\test' would store 'C:Users\test'
        # with no error at all.
        if dialect == "mysql":
            escaped = escaped.replace("\\", "\\\\")
        return f"'{escaped}'"
    if pd.isna(val):
        return "NULL"
    return str(val)


def drop_table_statement(table_name: str, dialect: str = "mysql") -> str:
    return f"DROP TABLE IF EXISTS {_quote_identifier(table_name, dialect)};"


class SqlInsertStreamWriter:
    """Incrementally writes `INSERT INTO ... VALUES (...);` statements to an
    open file handle as row chunks arrive, instead of needing the whole
    table's rows in memory at once (issue #158). Batches multiple rows into
    one statement when `batch_kib` is set (issue #157); `batch_kib=None`
    writes one INSERT per row. Call `close()` once after the last
    `write_rows()` to flush any partial batch."""

    def __init__(self, fh, columns, table_name: str, dialect: str = "mysql",
                 batch_kib: float | None = None, blob_as_hex: bool = True):
        self._fh = fh
        self._dialect = dialect
        self._blob_as_hex = blob_as_hex
        self._limit_bytes = (batch_kib * 1024) if batch_kib else None
        cols = ", ".join(_quote_identifier(c, dialect) for c in columns)
        table = _quote_identifier(table_name, dialect)
        self._prefix = f"INSERT INTO {table} ({cols}) VALUES "  # nosec B608
        self._prefix_size = len(self._prefix.encode("utf-8"))
        self._batch: list[str] = []
        self._batch_size = self._prefix_size

    def write_rows(self, rows):
        for row in rows:
            v = "(" + ", ".join(_sql_value_literal(x, self._dialect, self._blob_as_hex) for x in row) + ")"
            if self._limit_bytes is None:
                self._fh.write(f"{self._prefix}{v};\n")
                continue
            v_size = len(v.encode("utf-8")) + 2  # ", " separator
            if self._batch and self._batch_size + v_size > self._limit_bytes:
                self._flush_batch()
            self._batch.append(v)
            self._batch_size += v_size

    def _flush_batch(self):
        if self._batch:
            self._fh.write(self._prefix + ",\n  ".join(self._batch) + ";\n")
            self._batch = []
            self._batch_size = self._prefix_size

    def close(self):
        self._flush_batch()


def _to_sql_inserts(df, table_name: str, dialect: str = "mysql",
                     batch_kib: float | None = None, blob_as_hex: bool = True) -> str:
    import io
    buf = io.StringIO()
    writer = SqlInsertStreamWriter(buf, list(df.columns), table_name, dialect, batch_kib, blob_as_hex)
    writer.write_rows(df.itertuples(index=False, name=None))
    writer.close()
    return buf.getvalue().rstrip("\n")


def _guarded_for_spreadsheet(df: pd.DataFrame) -> pd.DataFrame:
    """Copy of *df* with every string cell passed through
    _csv_formula_guard (issue #115), for the CSV/XLSX export paths below.
    Checks each value's actual type rather than the column's dtype —
    pandas' string dtype varies by version/backend (plain `object`, the
    newer dedicated `str` dtype, or pandas' nullable `string[...]`), so a
    dtype-based column filter would silently stop guarding on some pandas
    versions. Non-string values (numbers, dates, None/NaN) pass through
    untouched and keep their native pandas serialization."""
    return df.map(lambda v: _csv_formula_guard(v) if isinstance(v, str) else v)


def export_dataframe(parent, df, default_name: str, table_name: str = "table"):
    """Prompt for a save file and export `df` as CSV/JSON/Excel/SQL.

    Shows an information/error dialog on the given `parent` widget.
    """
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    if df is None or df.empty:
        QMessageBox.information(parent, "Export", "No data to export.")
        return

    file_name, _ = QFileDialog.getSaveFileName(parent, "Export Data", default_name, _FILTERS)
    if not file_name:
        return

    try:
        if file_name.endswith(".json"):
            df.to_json(file_name, orient="records", indent=2, force_ascii=False)
        elif file_name.endswith(".xlsx"):
            _guarded_for_spreadsheet(df).to_excel(file_name, index=False, engine="openpyxl")
        elif file_name.endswith(".sql"):
            with open(file_name, "w", encoding="utf-8") as fh:
                fh.write(_to_sql_inserts(df, table_name))
        else:
            _guarded_for_spreadsheet(df).to_csv(file_name, index=False)
        QMessageBox.information(parent, "Export", f"Exported {len(df)} rows to:\n{file_name}")
    except Exception as ex:
        QMessageBox.critical(parent, "Export Error", str(ex))
