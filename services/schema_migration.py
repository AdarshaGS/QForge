"""Generates a reviewable SQL migration script from a SchemaDiff (issue #69):
DDL that brings the target schema in line with the source schema.

Never executes anything against either database — it only ever reads (a
throwaway connection to *source_config* for CREATE TABLE DDL of tables
missing from target) and returns text for the caller to display, let the
user copy/export, and run themselves wherever they choose.
"""
from services.db_service import DbService
from services.schema_diff import SchemaDiff

_NUMERIC_TYPE_HINTS = ("int", "decimal", "float", "double", "bit", "numeric", "real")
_DEFAULT_KEYWORDS = {"CURRENT_TIMESTAMP", "NULL", "NOW()"}


def generate_migration_sql(diff: SchemaDiff, source_config: dict, target_config: dict,
                            table_name: str = None) -> str:
    """DDL, as one string, that transforms *target* toward *source*.

    table_name: if given, scope the script to just that table instead of
    every difference in *diff*."""
    if table_name is not None:
        diff = _diff_for_table(diff, table_name)
    db_type = (target_config.get("type") or "mysql").lower()
    sections = []

    if diff.tables_removed:
        db = DbService()
        db.connect(source_config)
        try:
            for name in diff.tables_removed:
                try:
                    ddl = db.get_table_ddl(name)
                except Exception:
                    ddl = ""
                sections.append(ddl or f"-- Could not reconstruct CREATE TABLE for {_quote(name, db_type)}; add it manually.")
        finally:
            db.disconnect()

    for name in diff.tables_added:
        sections.append(f"DROP TABLE {_quote(name, db_type)};")

    for table_diff in diff.tables_modified:
        stmts = _alter_table_sql(table_diff, db_type)
        if stmts:
            sections.append(f"-- {table_diff.name}\n" + "\n".join(stmts))

    if not sections:
        return "-- No differences to migrate."
    return "\n\n".join(sections)


def _diff_for_table(diff: SchemaDiff, table_name: str) -> SchemaDiff:
    """A SchemaDiff containing only *table_name*'s change, whichever bucket
    it's in — lets generate_migration_sql() stay a single code path for
    both "whole diff" and "one table" instead of branching internally."""
    scoped = SchemaDiff()
    if table_name in diff.tables_added:
        scoped.tables_added = [table_name]
    elif table_name in diff.tables_removed:
        scoped.tables_removed = [table_name]
    else:
        scoped.tables_modified = [t for t in diff.tables_modified if t.name == table_name]
    return scoped


def _quote(ident: str, db_type: str) -> str:
    return f"`{ident}`" if db_type == "mysql" else f'"{ident}"'


def _default_clause(info) -> str:
    if info.default is None:
        return ""
    val = str(info.default)
    is_numeric_type = any(hint in (info.data_type or "").lower() for hint in _NUMERIC_TYPE_HINTS)
    if val.upper() in _DEFAULT_KEYWORDS or is_numeric_type:
        return f" DEFAULT {val}"
    return f" DEFAULT '{val.replace(chr(39), chr(39) * 2)}'"


def _column_def_sql(info) -> str:
    return f"{info.data_type or 'TEXT'} {'NULL' if info.nullable else 'NOT NULL'}{_default_clause(info)}"


def _alter_table_sql(table_diff, db_type: str) -> list:
    t = _quote(table_diff.name, db_type)
    stmts = []

    for col in table_diff.columns:
        c = _quote(col.name, db_type)
        if col.change == "removed":  # present in source only -> add to target
            stmts.append(f"ALTER TABLE {t} ADD COLUMN {c} {_column_def_sql(col.source)};")
        elif col.change == "added":  # present in target only -> drop from target
            stmts.append(f"ALTER TABLE {t} DROP COLUMN {c};")
        elif db_type == "sqlite":
            # ponytail: SQLite's ALTER TABLE can't change a column's type,
            # nullability, or default — flagging for manual review instead
            # of emitting DDL that would fail. Upgrade path: rebuild-and-copy
            # (CREATE new table, INSERT...SELECT, DROP old, RENAME) if this
            # comes up often enough to be worth automating.
            stmts.append(f"-- {table_diff.name}.{col.name}: column changes aren't supported by "
                          f"SQLite's ALTER TABLE; recreate the table manually.")
        elif db_type == "mysql":
            stmts.append(f"ALTER TABLE {t} MODIFY COLUMN {c} {_column_def_sql(col.source)};")
        else:  # postgresql — type/null each need their own ALTER COLUMN clause
            stmts.append(f"ALTER TABLE {t} ALTER COLUMN {c} TYPE {col.source.data_type};")
            stmts.append(f"ALTER TABLE {t} ALTER COLUMN {c} "
                          f"{'DROP NOT NULL' if col.source.nullable else 'SET NOT NULL'};")

    for idx in table_diff.indexes:
        stmts.extend(_index_sql(table_diff.name, idx, db_type))

    if table_diff.foreign_keys:
        # ponytail: get_foreign_keys() doesn't carry constraint names (sqlite's
        # PRAGMA doesn't even have one), so a changed FK can't be DROP'd by
        # name. Upgrade path: track constraint names for mysql/postgres if
        # generating FK DDL becomes a real ask.
        stmts.append(f"-- {table_diff.name}: {len(table_diff.foreign_keys)} foreign key change(s) "
                      f"require manual review (constraint names aren't tracked).")

    return stmts


def _index_sql(table_name: str, idx, db_type: str) -> list:
    t = _quote(table_name, db_type)
    name = idx.name
    is_pk = name.upper() == "PRIMARY" and db_type == "mysql"

    def _create(info):
        cols = info.get("columns", "")
        if is_pk:
            return [f"ALTER TABLE {t} ADD PRIMARY KEY ({cols});"]
        unique = "UNIQUE " if info.get("unique") else ""
        if db_type == "mysql":
            return [f"ALTER TABLE {t} ADD {unique}INDEX {_quote(name, db_type)} ({cols});"]
        return [f"CREATE {unique}INDEX {_quote(name, db_type)} ON {t} ({cols});"]

    def _drop():
        if is_pk:
            return [f"ALTER TABLE {t} DROP PRIMARY KEY;"]
        if db_type == "mysql":
            return [f"ALTER TABLE {t} DROP INDEX {_quote(name, db_type)};"]
        return [f"DROP INDEX {_quote(name, db_type)};"]

    if idx.change == "removed":  # present in source only -> add to target
        return _create(idx.source)
    if idx.change == "added":  # present in target only -> drop from target
        return _drop()
    return _drop() + _create(idx.source)  # modified -> drop + recreate from source's definition
