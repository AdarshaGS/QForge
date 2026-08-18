"""Tests for the auto-increment-value and generated-column DDL text
transforms (issue #160)."""
from utils.df_export import strip_auto_increment_value, strip_generated_column_clauses


def test_strip_auto_increment_value_removes_clause():
    ddl = "CREATE TABLE `t` (`id` int) ENGINE=InnoDB AUTO_INCREMENT=42 DEFAULT CHARSET=utf8;"
    result = strip_auto_increment_value(ddl)
    assert "AUTO_INCREMENT" not in result
    assert "ENGINE=InnoDB" in result and "DEFAULT CHARSET=utf8" in result


def test_strip_auto_increment_value_is_noop_without_clause():
    ddl = 'CREATE TABLE "t" (id integer);'
    assert strip_auto_increment_value(ddl) == ddl


def test_strip_generated_column_clauses_removes_simple_expression():
    ddl = "CREATE TABLE t (a int, b int GENERATED ALWAYS AS (a + 1) STORED);"
    result = strip_generated_column_clauses(ddl)
    assert "GENERATED" not in result
    assert result == "CREATE TABLE t (a int, b int);"


def test_strip_generated_column_clauses_handles_nested_parens():
    ddl = "CREATE TABLE t (a int, b int, c int GENERATED ALWAYS AS ((a + (b * 2))) VIRTUAL, d int);"
    result = strip_generated_column_clauses(ddl)
    assert "GENERATED" not in result
    assert result == "CREATE TABLE t (a int, b int, c int, d int);"


def test_strip_generated_column_clauses_handles_multiple_columns():
    ddl = ("CREATE TABLE t (a int GENERATED ALWAYS AS (1) STORED, "
           "b int GENERATED ALWAYS AS (2) STORED);")
    result = strip_generated_column_clauses(ddl)
    assert result.count("GENERATED") == 0
    assert result == "CREATE TABLE t (a int, b int);"


def test_strip_generated_column_clauses_is_noop_without_clause():
    ddl = "CREATE TABLE t (a int);"
    assert strip_generated_column_clauses(ddl) == ddl
