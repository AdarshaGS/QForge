import pandas as pd

from utils.df_export import _sql_value_literal, _to_sql_inserts


def test_sql_value_literal_null_for_none_and_nan():
    assert _sql_value_literal(None) == "NULL"
    assert _sql_value_literal(float("nan")) == "NULL"


def test_sql_value_literal_quotes_and_escapes_strings():
    assert _sql_value_literal("hello") == "'hello'"
    assert _sql_value_literal("O'Brien") == "'O''Brien'"


def test_sql_value_literal_leaves_numbers_unquoted():
    assert _sql_value_literal(42) == "42"
    assert _sql_value_literal(3.14) == "3.14"


def test_to_sql_inserts_builds_one_statement_per_row():
    df = pd.DataFrame([
        {"id": 1, "name": "Alice"},
        {"id": 2, "name": None},
    ])
    sql = _to_sql_inserts(df, "users")
    lines = sql.splitlines()
    assert len(lines) == 2
    assert lines[0] == "INSERT INTO `users` (`id`, `name`) VALUES (1, 'Alice');"
    assert lines[1] == "INSERT INTO `users` (`id`, `name`) VALUES (2, NULL);"
