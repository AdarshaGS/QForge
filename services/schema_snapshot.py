"""Fetch a read-only snapshot of a database's schema.

Uses its own dedicated, throwaway DbService connection rather than a
caller-supplied shared one. This is deliberate: sharing one DB-API
connection across threads without synchronization corrupts result sets
(interleaved queries can cross-contaminate each other's rows — the cause of
a real bug where a MySQL version string appeared in the "Tables" list and
"Views" showed duplicated entries). Call this from a background thread and
apply the result to your live connection/state on the main thread.
"""
from services.db_service import DbService
from utils import schema_cache
from utils.logger import get_logger

logger = get_logger()

_MYSQL_SYSTEM_DBS = {"information_schema", "mysql", "performance_schema", "sys"}


def fetch_schema_snapshot(config: dict, on_tables_ready=None) -> dict:
    """Connect using `config`, gather schema metadata, then disconnect.

    Returns a dict with keys: dbs, tables, columns, column_details,
    foreign_keys, views, functions, server_version, and (mysql only, when the
    configured database doesn't exist) switched_db — the database actually
    selected instead.

    On success, also persists the structural subset of this result via
    utils.schema_cache (issue #71), keyed by config["id"] + database, so the
    next time this connection/database is opened the caller can populate the
    UI from disk before this (network) fetch completes.

    Tables, columns, column_details and foreign_keys — everything the SQL
    editor's autocomplete needs (issue #16) — are fetched first, each in one
    bulk round-trip (no N+1 per table), and handed to
    `on_tables_ready(tables, columns, column_details, foreign_keys)`
    immediately, before the remaining (autocomplete-irrelevant) dbs/views/
    functions/server_version round-trips run. On a high-latency connection
    those extra round-trips previously delayed autocomplete for no reason.
    """
    db = DbService()
    db.connect(config)
    try:
        result = {}
        db_type = db.db_type

        # MySQL alone allows connecting with no database selected (issue:
        # schema tree stayed empty on first connect). Resolve that — and
        # list every other db for the switcher UI — *before* fetching
        # tables/columns below, so that fetch actually has a database to
        # query against instead of failing/returning empty and never being
        # retried. PostgreSQL always lands in a real database via libpq's
        # own defaults, so it needs no equivalent fallback here.
        try:
            if db_type == "mysql":
                df = db.execute_query("SHOW DATABASES")
                dbs = [d for d in df.iloc[:, 0].tolist() if d not in _MYSQL_SYSTEM_DBS]
                result["dbs"] = dbs
                cur_db = config.get("database", "")
                if cur_db not in dbs and dbs:
                    db.connection.select_db(dbs[0])
                    result["switched_db"] = dbs[0]
            elif db_type == "postgresql":
                df = db.execute_query(
                    "SELECT datname FROM pg_database WHERE datistemplate = false")
                result["dbs"] = df["datname"].tolist()
            else:
                result["dbs"] = []
        except Exception as ex:
            logger.debug(f"Failed to list databases: {ex}")
            result["dbs"] = []

        try:
            result["tables"] = db.get_tables()
        except Exception as ex:
            logger.debug(f"Failed to list tables: {ex}")
            result["tables"] = []
        try:
            result["columns"] = db.get_all_columns()
        except Exception as ex:
            logger.debug(f"Failed to list columns: {ex}")
            result["columns"] = {}
        try:
            result["column_details"] = db.get_all_column_details()
        except Exception as ex:
            logger.debug(f"Failed to list column details: {ex}")
            result["column_details"] = {}
        try:
            result["foreign_keys"] = db.get_all_foreign_keys()
        except Exception as ex:
            logger.debug(f"Failed to list foreign keys: {ex}")
            result["foreign_keys"] = {}

        if on_tables_ready:
            try:
                on_tables_ready(result["tables"], result["columns"],
                                 result["column_details"], result["foreign_keys"])
            except Exception as ex:
                logger.debug(f"schema_snapshot on_tables_ready callback failed: {ex}")

        try:
            result["views"] = db.get_views()
        except Exception as ex:
            logger.debug(f"Failed to list views: {ex}")
            result["views"] = []
        try:
            result["functions"] = db.get_functions()
        except Exception as ex:
            logger.debug(f"Failed to list functions: {ex}")
            result["functions"] = []
        try:
            result["server_version"] = db.get_server_version()
        except Exception as ex:
            logger.debug(f"Failed to get server version: {ex}")
            result["server_version"] = ""

        db_key = result.get("switched_db", config.get("database", ""))
        schema_cache.save(config.get("id", ""), db_key, result)

        return result
    finally:
        db.disconnect()
