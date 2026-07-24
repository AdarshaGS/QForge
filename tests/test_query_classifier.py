# Regression note: classification correctness for GRANT/REVOKE/RENAME and
# WITH-CTE-wrapped writes depends on sqlparse's token behavior, which is only
# verified here against the installed version (requirements.txt pins
# sqlparse>=0.4.4 with no upper bound). If these tests start failing after a
# sqlparse upgrade, that's exactly what they're here to catch.

from services.query_classifier import classify, is_dangerous, split_statements


def test_split_statements_splits_on_semicolons():
    stmts = split_statements("SELECT 1; SELECT 2;")
    assert stmts == ["SELECT 1;", "SELECT 2;"]


def test_split_statements_strips_blank_entries():
    assert split_statements("  ;  SELECT 1;  ;") == ["SELECT 1;"]


def test_classify_plain_statement_kinds():
    cases = {
        "SELECT * FROM users": "SELECT",
        "INSERT INTO users (id) VALUES (1)": "INSERT",
        "UPDATE users SET x=1 WHERE id=1": "UPDATE",
        "DELETE FROM users WHERE id=1": "DELETE",
        "CREATE TABLE x (id INT)": "CREATE",
        "DROP TABLE users": "DROP",
        "ALTER TABLE x ADD COLUMN y INT": "ALTER",
        "TRUNCATE users": "TRUNCATE",
    }
    for sql, expected_kind in cases.items():
        assert classify(sql).kind == expected_kind, sql


def test_classify_falls_back_for_types_get_type_reports_unknown():
    assert classify("GRANT ALL ON db.* TO 'u'@'%'").kind == "GRANT"
    assert classify("REVOKE ALL ON db.* FROM 'u'@'%'").kind == "REVOKE"
    assert classify("RENAME TABLE a TO b").kind == "RENAME"


def test_select_and_reads_are_not_writes():
    for sql in ("SELECT * FROM users", "SHOW TABLES", "EXPLAIN SELECT 1"):
        c = classify(sql)
        assert c.is_write is False, sql


def test_writes_are_flagged_as_writes():
    for sql in (
        "INSERT INTO users (id) VALUES (1)",
        "UPDATE users SET x=1 WHERE id=1",
        "DELETE FROM users WHERE id=1",
        "CREATE TABLE x (id INT)",
        "DROP TABLE users",
        "ALTER TABLE x ADD COLUMN y INT",
        "TRUNCATE users",
        "RENAME TABLE a TO b",
        "GRANT ALL ON db.* TO 'u'@'%'",
        "REVOKE ALL ON db.* FROM 'u'@'%'",
    ):
        assert classify(sql).is_write is True, sql


def test_only_drop_and_truncate_are_destructive_ddl():
    assert classify("DROP TABLE users").is_destructive_ddl is True
    assert classify("TRUNCATE users").is_destructive_ddl is True
    assert classify("ALTER TABLE x ADD COLUMN y INT").is_destructive_ddl is False
    assert classify("CREATE TABLE x (id INT)").is_destructive_ddl is False


def test_delete_and_update_without_where_are_flagged_dangerous():
    c = classify("DELETE FROM users")
    assert c.has_where is False
    assert is_dangerous(c)

    c = classify("UPDATE users SET x=1")
    assert c.has_where is False
    assert is_dangerous(c)


def test_delete_and_update_with_where_are_not_flagged():
    c = classify("DELETE FROM users WHERE id=1")
    assert c.has_where is True
    assert not is_dangerous(c)

    c = classify("UPDATE users SET x=1 WHERE id=1")
    assert c.has_where is True
    assert not is_dangerous(c)


def test_commented_out_where_does_not_count():
    c = classify("DELETE FROM users -- WHERE id=1")
    assert c.has_where is False
    assert is_dangerous(c)


def test_where_not_applicable_to_select_or_insert():
    assert classify("SELECT * FROM users").has_where is None
    assert classify("INSERT INTO users (id) VALUES (1)").has_where is None


def test_with_cte_wrapped_writes_are_classified_as_writes():
    sql = (
        "WITH cte AS (SELECT id FROM users) "
        "DELETE FROM users WHERE id IN (SELECT id FROM cte)"
    )
    c = classify(sql)
    assert c.kind == "DELETE"
    assert c.is_write is True
    assert c.has_where is True


def test_quoted_identifier_named_delete_is_not_confused_with_keyword():
    c = classify("CREATE TABLE `DELETE` (id INT)")
    assert c.kind == "CREATE"


def test_ddl_kinds_are_flagged_dangerous():
    for sql in (
        "DROP TABLE users",
        "TRUNCATE users",
        "ALTER TABLE x ADD COLUMN y INT",
        "CREATE TABLE x (id INT)",
        "RENAME TABLE a TO b",
        "GRANT ALL ON db.* TO 'u'@'%'",
        "REVOKE ALL ON db.* FROM 'u'@'%'",
    ):
        assert is_dangerous(classify(sql)), sql


def test_plain_single_row_insert_is_not_flagged_dangerous():
    # Only "mass" inserts should be flagged, and that requires a row count
    # the classifier doesn't have — callers (e.g. CSV import) add that
    # themselves. A single-row INSERT alone is not in the "at minimum flag"
    # list.
    assert not is_dangerous(classify("INSERT INTO users (id) VALUES (1)"))
