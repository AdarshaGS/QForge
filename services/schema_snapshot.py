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
from utils.logger import get_logger

logger = get_logger()

_MYSQL_SYSTEM_DBS = {"information_schema", "mysql", "performance_schema", "sys"}


def fetch_schema_snapshot(config: dict) -> dict:
    """Connect using `config`, gather schema metadata, then disconnect.

    Returns a dict with keys: dbs, tables, columns, views, functions,
    server_version, and (mysql only, when the configured database doesn't
    exist) switched_db — the database actually selected instead.
    """
    db = DbService()
    db.connect(config)
    try:
        result = {}
        db_type = db.db_type

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

        return result
    finally:
        db.disconnect()
