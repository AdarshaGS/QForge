"""Tests for the schema-compare migration SQL generator (issue #69)."""
from services.db_service import DbService
from services.schema_diff import ColumnDiff, ColumnInfo, IndexDiff, SchemaDiff, TableDiff, build_schema_diff
from services.schema_migration import _alter_table_sql, _column_def_sql, _index_sql, generate_migration_sql


def _make_sqlite_config(tmp_path, name):
    return {"type": "sqlite", "name": name, "database": str(tmp_path / f"{name}.db")}


def _run(config, *statements):
    db = DbService()
    db.connect(config)
    for stmt in statements:
        db.execute_update(stmt)
    db.disconnect()


def test_no_changes_returns_placeholder():
    assert generate_migration_sql(SchemaDiff(), {"type": "mysql"}, {"type": "mysql"}) == "-- No differences to migrate."


def test_table_name_scopes_to_a_single_table():
    diff = SchemaDiff(
        tables_added=["extra_in_target"],
        tables_removed=["missing_from_target"],
        tables_modified=[TableDiff(name="users", columns=[
            ColumnDiff(name="email", change="added", target=_col("email")),
        ])],
    )

    sql = generate_migration_sql(diff, {"type": "mysql"}, {"type": "mysql"}, table_name="users")

    assert "users" in sql
    assert "extra_in_target" not in sql
    assert "missing_from_target" not in sql


def test_table_name_for_added_table_only_drops_that_table():
    diff = SchemaDiff(tables_added=["a", "b"])

    sql = generate_migration_sql(diff, {"type": "mysql"}, {"type": "mysql"}, table_name="b")

    assert sql == "DROP TABLE `b`;"


def test_table_removed_from_target_becomes_create_table(tmp_path):
    source = _make_sqlite_config(tmp_path, "source")
    target = _make_sqlite_config(tmp_path, "target")
    _run(source, "CREATE TABLE only_in_source (id INTEGER PRIMARY KEY)")
    _run(target, "CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")

    diff = build_schema_diff(source, target)
    sql = generate_migration_sql(diff, source, target)

    assert "CREATE TABLE" in sql
    assert "only_in_source" in sql


def test_table_added_to_target_becomes_drop_table(tmp_path):
    source = _make_sqlite_config(tmp_path, "source")
    target = _make_sqlite_config(tmp_path, "target")
    _run(source, "CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
    _run(target, "CREATE TABLE only_in_target (id INTEGER PRIMARY KEY)")

    diff = build_schema_diff(source, target)
    sql = generate_migration_sql(diff, source, target)

    assert 'DROP TABLE "only_in_target";' in sql


def _col(name, data_type="varchar(255)", nullable=True, default=None):
    return ColumnInfo(name=name, data_type=data_type, nullable=nullable, default=default)


def test_alter_table_add_and_drop_column_mysql():
    table_diff = TableDiff(name="users", columns=[
        ColumnDiff(name="legacy_flag", change="removed", source=_col("legacy_flag", "tinyint(1)")),
        ColumnDiff(name="signup_at", change="added", target=_col("signup_at")),
    ])

    stmts = _alter_table_sql(table_diff, "mysql")

    assert "ALTER TABLE `users` ADD COLUMN `legacy_flag` tinyint(1) NULL;" in stmts
    assert "ALTER TABLE `users` DROP COLUMN `signup_at`;" in stmts


def test_alter_table_modified_column_mysql_uses_source_definition():
    table_diff = TableDiff(name="users", columns=[
        ColumnDiff(name="email", change="modified",
                   source=_col("email", "varchar(255)", nullable=False),
                   target=_col("email", "varchar(255)", nullable=True)),
    ])

    stmts = _alter_table_sql(table_diff, "mysql")

    assert stmts == ["ALTER TABLE `users` MODIFY COLUMN `email` varchar(255) NOT NULL;"]


def test_alter_table_modified_column_sqlite_flags_for_manual_review():
    table_diff = TableDiff(name="users", columns=[
        ColumnDiff(name="email", change="modified",
                   source=_col("email", nullable=False), target=_col("email", nullable=True)),
    ])

    stmts = _alter_table_sql(table_diff, "sqlite")

    assert len(stmts) == 1
    assert stmts[0].startswith("--")
    assert "manually" in stmts[0]


def test_foreign_key_changes_flagged_not_generated():
    from services.schema_diff import ForeignKeyDiff
    table_diff = TableDiff(name="books", foreign_keys=[ForeignKeyDiff(label="author_id → authors.id", change="added")])

    stmts = _alter_table_sql(table_diff, "mysql")

    assert len(stmts) == 1
    assert stmts[0].startswith("--")
    assert "manual review" in stmts[0]


def test_default_value_quoting_heuristic():
    numeric = _col("retries", "int", default="0")
    text = _col("status", "varchar(20)", default="pending")
    keyword = _col("created_at", "timestamp", default="CURRENT_TIMESTAMP")

    assert _column_def_sql(numeric).endswith("DEFAULT 0")
    assert _column_def_sql(text).endswith("DEFAULT 'pending'")
    assert _column_def_sql(keyword).endswith("DEFAULT CURRENT_TIMESTAMP")


def test_default_value_injection_on_numeric_type_is_quoted_not_raw():
    """Issue #114: a crafted/compromised source database could report an
    'int'-typed column with a non-numeric default. Older code trusted the
    column's declared *type* to decide whether to emit DEFAULT unquoted —
    the fix validates the *value* itself instead, so injection-shaped text
    lands inside a quoted (and escaped) string literal rather than as raw
    SQL."""
    malicious = _col("retries", "int", default="0); DROP TABLE users; --")
    assert _column_def_sql(malicious).endswith("DEFAULT '0); DROP TABLE users; --'")


def test_default_value_with_embedded_quote_is_escaped():
    malicious = _col("bio", "varchar(50)", default="o'brien")
    assert _column_def_sql(malicious).endswith("DEFAULT 'o''brien'")


def test_index_primary_key_special_cased_for_mysql():
    idx = IndexDiff(name="PRIMARY", change="removed", source={"columns": "id", "unique": True, "type": "BTREE"})

    stmts = _index_sql("users", idx, "mysql")

    assert stmts == ["ALTER TABLE `users` ADD PRIMARY KEY (`id`);"]


def test_index_add_and_drop_for_postgres():
    added = IndexDiff(name="idx_extra", change="added", target={"columns": "sku", "unique": False})
    removed = IndexDiff(name="idx_sku", change="removed", source={"columns": "sku", "unique": True})

    assert _index_sql("items", added, "postgresql") == ['DROP INDEX "idx_extra";']
    assert _index_sql("items", removed, "postgresql") == ['CREATE UNIQUE INDEX "idx_sku" ON "items" ("sku");']


def test_index_column_list_with_malicious_column_name_is_quoted_per_column():
    """Issue #114: DbService.get_indexes()'s 'columns' field is a raw,
    unquoted, comma-joined string (it doubles as display text elsewhere) —
    _index_sql must quote each column individually when turning it into
    DDL, not interpolate the joined string as-is."""
    idx = IndexDiff(
        name="idx_evil", change="removed",
        source={"columns": 'sku, name"); DROP TABLE items; --', "unique": False},
    )
    stmts = _index_sql("items", idx, "postgresql")
    assert stmts == [
        'CREATE INDEX "idx_evil" ON "items" ("sku", "name""); DROP TABLE items; --");'
    ]
