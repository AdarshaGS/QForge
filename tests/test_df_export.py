import pandas as pd

from utils.df_export import _sql_value_literal, _to_sql_inserts, drop_table_statement


def test_sql_value_literal_null_for_none_and_nan():
    assert _sql_value_literal(None) == "NULL"
    assert _sql_value_literal(float("nan")) == "NULL"


def test_sql_value_literal_quotes_and_escapes_strings():
    assert _sql_value_literal("hello") == "'hello'"
    assert _sql_value_literal("O'Brien") == "'O''Brien'"


def test_sql_value_literal_leaves_numbers_unquoted():
    assert _sql_value_literal(42) == "42"
    assert _sql_value_literal(3.14) == "3.14"


def test_sql_value_literal_hex_encodes_blobs_per_dialect():
    assert _sql_value_literal(b"\x01\xff", dialect="mysql") == "X'01ff'"
    assert _sql_value_literal(b"\x01\xff", dialect="sqlite") == "X'01ff'"
    assert _sql_value_literal(b"\x01\xff", dialect="postgresql") == "E'\\\\x01ff'"


def test_sql_value_literal_blob_as_hex_false_falls_back_to_null():
    assert _sql_value_literal(b"\x01\xff", blob_as_hex=False) == "NULL"


def test_to_sql_inserts_quotes_identifiers_per_dialect():
    df = pd.DataFrame([{"id": 1}])
    assert _to_sql_inserts(df, "users", dialect="mysql") == "INSERT INTO `users` (`id`) VALUES (1);"
    assert _to_sql_inserts(df, "users", dialect="postgresql") == 'INSERT INTO "users" ("id") VALUES (1);'
    assert _to_sql_inserts(df, "users", dialect="sqlite") == 'INSERT INTO "users" ("id") VALUES (1);'


def test_to_sql_inserts_builds_one_statement_per_row():
    """Regression: batch_kib=None (the default) must reproduce today's
    exact one-row-per-statement output, unaffected by the batching feature."""
    df = pd.DataFrame([
        {"id": 1, "name": "Alice"},
        {"id": 2, "name": None},
    ])
    sql = _to_sql_inserts(df, "users")
    lines = sql.splitlines()
    assert len(lines) == 2
    assert lines[0] == "INSERT INTO `users` (`id`, `name`) VALUES (1, 'Alice');"
    assert lines[1] == "INSERT INTO `users` (`id`, `name`) VALUES (2, NULL);"


def test_to_sql_inserts_batches_multiple_rows_per_statement_by_kib():
    df = pd.DataFrame([{"id": i} for i in range(5)])
    sql = _to_sql_inserts(df, "t", batch_kib=0.02)  # tiny cap forces multiple batches
    assert sql.count("INSERT INTO") > 1
    # every row value still appears exactly once across the batched statements
    for i in range(5):
        assert sql.count(f"({i})") == 1


def test_to_sql_inserts_single_large_batch_when_kib_is_generous():
    df = pd.DataFrame([{"id": i} for i in range(5)])
    sql = _to_sql_inserts(df, "t", batch_kib=1024)
    assert sql.count("INSERT INTO") == 1
    assert sql.strip().endswith(";")


def test_drop_table_statement_quotes_per_dialect():
    assert drop_table_statement("users", "mysql") == "DROP TABLE IF EXISTS `users`;"
    assert drop_table_statement("users", "postgresql") == 'DROP TABLE IF EXISTS "users";'
