import time
import pymysql
import pandas as pd
import sqlite3
from utils.logger import get_logger
from utils import schema_cache
from utils import perf_metrics
from utils.df_export import _quote_identifier
from services import query_classifier

logger = get_logger()


def _dsskey_stub_class(paramiko_module):
    """A stand-in for paramiko.DSSKey that raises SSHException instead of
    being bare None.

    paramiko 3.0+ removed DSS/DSA key support entirely, but the pinned
    sshtunnel==0.4.0 still references `paramiko.DSSKey` unconditionally
    inside its private-key fallback loop (RSA -> DSS -> ECDSA -> Ed25519),
    which tries each key type in turn and catches `paramiko.SSHException`
    to move on to the next one. Patching `paramiko.DSSKey = None` breaks
    that fallback: if the *first* type tried (RSA) fails to parse a given
    key file, the loop falls through to the None stand-in and crashes with
    "'NoneType' object has no attribute 'from_private_key_file'" instead of
    trying ECDSA/Ed25519 next — exactly what happened for .pem files that
    aren't plain legacy RSA PEM (GitHub issue #14: a plain `id_rsa` key
    connected fine, a `.pem` key crashed with that exact error). Raising
    SSHException here instead preserves the loop's intended fallback."""
    class _RemovedDSSKey:
        @staticmethod
        def from_private_key_file(*args, **kwargs):
            raise paramiko_module.SSHException(
                "DSS/DSA keys are not supported (removed in paramiko 3.0+)"
            )
    return _RemovedDSSKey


def _ensure_dsskey_stub():
    """Idempotently patch paramiko.DSSKey with the stub above if this
    paramiko version removed DSSKey entirely."""
    import paramiko
    if not hasattr(paramiko, 'DSSKey'):
        paramiko.DSSKey = _dsskey_stub_class(paramiko)


def _lenient_read_row_from_packet(self, packet):
    """Drop-in replacement for pymysql.connections.MySQLResult's own
    row-decoder (copied from pymysql/connections.py, MIT licensed), with
    one change: a column that fails strict UTF-8 decoding falls back to
    errors='replace' instead of raising, so one bad byte in one cell shows
    up as U+FFFD instead of aborting the entire page fetch (issue #150)."""
    row = []
    for encoding, converter in self.converters:
        try:
            data = packet.read_length_coded_string()
        except IndexError:
            break
        if data is not None:
            if encoding is not None:
                try:
                    data = data.decode(encoding)
                except UnicodeDecodeError:
                    data = data.decode(encoding, errors="replace")
            if converter is not None:
                data = converter(data)
        row.append(data)
    return tuple(row)


def _ensure_lenient_mysql_decoding():
    """Idempotently patch pymysql to survive non-UTF-8-clean string columns
    (issue #150) instead of failing the whole page fetch on one bad byte.

    pymysql has no public hook for this: the `conv`/decoders connection
    param only supplies a converter that runs *after* string decoding
    already succeeded (services/db_service.py's own read of
    pymysql/connections.py's MySQLResult._get_descriptions confirms this —
    TEXT/VARCHAR columns get converter=None). The actual `data.decode(encoding)`
    call is hardcoded, strict, inside the private
    MySQLResult._read_row_from_packet. Patching that one method (rather
    than subclassing Connection/MySQLResult) covers both of pymysql's
    internal call sites that construct MySQLResult, since they all share
    this one class method.

    Guarded so a future pymysql release that reshapes this internal doesn't
    crash QForge on import — it just silently loses the lenient-decode
    behavior and pymysql's original strict behavior returns."""
    try:
        import pymysql.connections as _pymysql_connections
        if not hasattr(_pymysql_connections.MySQLResult, '_read_row_from_packet'):
            return
        _pymysql_connections.MySQLResult._read_row_from_packet = _lenient_read_row_from_packet
    except Exception as ex:
        logger.warning(f"Could not patch pymysql for lenient decoding: {ex}")


def _lenient_parse_field_descriptor(self, encoding):
    """Drop-in replacement for pymysql.connections.FieldDescriptorPacket's
    own column-metadata parser (copied from pymysql/connections.py, MIT
    licensed), with one change: a table/column identifier that fails strict
    decoding under the connection's negotiated charset falls back to
    errors='replace' instead of raising. This is the same failure mode as
    issue #150 (a legacy-charset identifier isn't clean UTF-8) but in a
    different pymysql method — #150 only patched row *values*
    (_read_row_from_packet); this one covers column/table *names*, which
    are parsed before a single row is read, so it previously aborted the
    query outright with no data shown at all."""
    def _decode(data):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            return data.decode(encoding, errors="replace")

    self.catalog = self.read_length_coded_string()
    self.db = self.read_length_coded_string()
    self.table_name = _decode(self.read_length_coded_string())
    self.org_table = _decode(self.read_length_coded_string())
    self.name = _decode(self.read_length_coded_string())
    self.org_name = _decode(self.read_length_coded_string())
    (
        self.charsetnr,
        self.length,
        self.type_code,
        self.flags,
        self.scale,
    ) = self.read_struct("<xHIBHBxx")


def _ensure_lenient_mysql_field_decoding():
    """Idempotently patch pymysql to survive non-UTF-8-clean column/table
    identifiers — see _lenient_parse_field_descriptor. Guarded the same way
    as _ensure_lenient_mysql_decoding, for the same reason."""
    try:
        import pymysql.connections as _pymysql_connections
        if not hasattr(_pymysql_connections.FieldDescriptorPacket, '_parse_field_descriptor'):
            return
        _pymysql_connections.FieldDescriptorPacket._parse_field_descriptor = _lenient_parse_field_descriptor
    except Exception as ex:
        logger.warning(f"Could not patch pymysql for lenient field decoding: {ex}")


class ReadOnlyViolation(Exception):
    """Raised when a write statement is attempted on a read-only connection.
    This is the backstop guard — always active regardless of which UI entry
    point called in, so it can't be bypassed by a new/forgotten call site."""
    pass


class TransactionError(Exception):
    """Raised on transaction-state misuse (double BEGIN, COMMIT/ROLLBACK
    with nothing open, or a connection lost while a transaction was open).
    Kept distinct from a raw driver error so the UI can show a clear
    message instead of a database-specific one."""
    pass


class DbService:

    def __init__(self):
        self.connection = None
        self.connection_name = None
        self.db_type = None  # 'mysql', 'postgresql', 'sqlite'
        self.ssh_tunnel = None  # SSH tunnel object
        self.read_only = False
        self.in_transaction = False
        self._config = None   # stored for auto-reconnect

    def _q(self, identifier: str) -> str:
        """Quote *identifier* (table/column/index name) for this
        connection's dialect, escaping any embedded quote char the same way
        utils/df_export._quote_identifier does for exported SQL (issue
        #114) — table/column names ultimately come from the connected
        database's own schema metadata, which a crafted or compromised
        database can put anything into. SQLite accepts the same
        double-quoted identifier syntax as PostgreSQL."""
        return _quote_identifier(identifier, "mysql" if self.db_type == "mysql" else "postgresql")

    def _guard(self, sql: str):
        """Raise ReadOnlyViolation if *sql* contains a write statement and
        this connection is read-only. No-op otherwise. Classifies the
        actual SQL text (not which public method was called), since
        execute_query()/execute_update() will both run any statement."""
        if not self.read_only:
            return
        for stmt in query_classifier.split_statements(sql):
            c = query_classifier.classify(stmt)
            if c.kind in query_classifier.READ_ONLY_BLOCKED_KINDS:
                raise ReadOnlyViolation(
                    f"This connection is read-only — blocked: {stmt[:200]}"
                )

    def begin_transaction(self):
        """Start a manual transaction, turning off this connection's
        per-statement autocommit until commit_transaction()/
        rollback_transaction() is called. Dialect-specific: MySQL and
        PostgreSQL drivers autocommit by default (see connect()), so
        starting a manual transaction means explicitly disabling that;
        SQLite already opens an implicit transaction on the first write
        under its default isolation level, so an explicit BEGIN just makes
        that transaction start immediately (covering leading SELECTs too)
        instead of on the first DML statement."""
        if not self.connection:
            raise TransactionError("No active database connection.")
        if self.in_transaction:
            raise TransactionError("A transaction is already open on this connection.")

        if self.db_type == "mysql":
            self.connection.autocommit(False)
            with self.connection.cursor() as cur:
                cur.execute("BEGIN")
        elif self.db_type == "postgresql":
            self.connection.autocommit = False
        elif self.db_type == "sqlite":
            self.connection.execute("BEGIN")
        else:
            raise TransactionError(f"Transactions are not supported for {self.db_type}")

        self.in_transaction = True

    def commit_transaction(self):
        """Commit the open manual transaction and restore autocommit."""
        if not self.in_transaction:
            raise TransactionError("No open transaction to commit.")
        self.connection.commit()
        self._restore_autocommit()
        self.in_transaction = False

    def rollback_transaction(self):
        """Roll back the open manual transaction and restore autocommit."""
        if not self.in_transaction:
            raise TransactionError("No open transaction to roll back.")
        self.connection.rollback()
        self._restore_autocommit()
        self.in_transaction = False

    def _restore_autocommit(self):
        """Undo the autocommit=False set by begin_transaction() for
        drivers that need it explicitly re-enabled (MySQL/PostgreSQL).
        SQLite has no persistent autocommit flag to restore — its default
        isolation level already re-opens an implicit transaction on the
        next write and commits it via _execute_query_raw as before."""
        if self.db_type == "mysql":
            self.connection.autocommit(True)
        elif self.db_type == "postgresql":
            self.connection.autocommit = True

    def connect(self, config):
        """Connect to database based on type"""

        db_type = config.get("type", "mysql").lower()
        self.db_type = db_type
        self.read_only = bool(config.get("read_only"))
        self.in_transaction = False

        logger.info(f"Connecting to {db_type} database: {config['name']}")

        _connect_t0 = time.perf_counter()
        if db_type == "mysql":
            self._connect_mysql(config)
        elif db_type == "postgresql":
            self._connect_postgresql(config)
        elif db_type == "sqlite":
            self._connect_sqlite(config)
        else:
            raise Exception(f"Unsupported database type: {db_type}")
        perf_metrics.record("database", "db_connect", (time.perf_counter() - _connect_t0) * 1000)

        self.connection_name = config["name"]
        self._config = config   # save for reconnect
        logger.info(f"Successfully connected to {db_type} database")
        return self.connection
    
    def _connect_mysql(self, config):
        """Connect to MySQL database"""
        _ensure_lenient_mysql_decoding()
        _ensure_lenient_mysql_field_decoding()
        # Check if SSH tunnel is needed
        ssh_tunnel_config = config.get("ssh_tunnel", {"enabled": False})
        
        if ssh_tunnel_config.get("enabled", False):
            host, port = self._setup_ssh_tunnel(ssh_tunnel_config, config["host"], config["port"])
        else:
            host = config["host"]
            port = config["port"]
        
        # Database is optional - can connect without specifying one
        connect_params = {
            "host": host,
            "port": port,
            "user": config["user"],
            "password": config["password"],
            "cursorclass": pymysql.cursors.DictCursor,
            "autocommit": True,
            # Prevent pymysql from timing out long-running queries.
            # read_timeout / write_timeout are the client-side socket limits.
            # 3600 = 1 hour — matches what TablePlus and DBeaver use.
            "read_timeout": 3600,
            "write_timeout": 3600,
            "connect_timeout": 30,
        }
        
        # Only add database if provided
        if config.get("database"):
            connect_params["database"] = config["database"]
        
        self.connection = pymysql.connect(**connect_params)

        # Push session-level timeouts on the server side so net_read_timeout
        # (default 30 s) and wait_timeout don't kill long queries.
        try:
            with self.connection.cursor() as cur:
                cur.execute(
                    "SET SESSION net_read_timeout=3600, "
                    "net_write_timeout=3600, "
                    "wait_timeout=28800, "
                    "interactive_timeout=28800"
                )
        except Exception as ex:
            logger.debug(f"Non-fatal: failed to set session timeouts: {ex}")

        if config.get("read_only"):
            try:
                with self.connection.cursor() as cur:
                    cur.execute("SET SESSION TRANSACTION READ ONLY")
            except Exception as ex:
                logger.warning(f"Failed to set MySQL session read-only: {ex}")

    def _connect_postgresql(self, config):
        """Connect to PostgreSQL database"""
        try:
            import psycopg2
            import psycopg2.extras
            
            # Check if SSH tunnel is needed
            ssh_tunnel_config = config.get("ssh_tunnel", {"enabled": False})
            
            if ssh_tunnel_config.get("enabled", False):
                host, port = self._setup_ssh_tunnel(ssh_tunnel_config, config["host"], config["port"])
            else:
                host = config["host"]
                port = config["port"]
            
            # Database is optional for PostgreSQL too
            connect_params = {
                "host": host,
                "port": port,
                "user": config["user"],
                "password": config["password"]
            }
            
            # Only add database if provided
            if config.get("database"):
                connect_params["database"] = config["database"]
            
            self.connection = psycopg2.connect(**connect_params)
            self.connection.autocommit = True

            if config.get("read_only"):
                try:
                    self.connection.set_session(readonly=True)
                except Exception as ex:
                    logger.warning(f"Failed to set PostgreSQL session read-only: {ex}")
        except ImportError:
            raise Exception("psycopg2 not installed. Run: pip install psycopg2-binary")

    def _connect_sqlite(self, config):
        """Connect to SQLite database"""
        db_path = config.get("database", config.get("path", ""))
        if not db_path:
            raise Exception("SQLite database path is required")

        # check_same_thread=False: DbService connections are routinely handed
        # to a background QThread (_QueryWorker, _ExportWorker) after being
        # opened on the main thread, and an export's producer thread hops
        # once more — a single DbService is still only ever touched by one
        # thread at a time, just not always the thread that opened it.
        self.connection = sqlite3.connect(db_path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row

        if config.get("read_only"):
            try:
                self.connection.execute("PRAGMA query_only = ON")
            except Exception as ex:
                logger.warning(f"Failed to set SQLite query_only pragma: {ex}")
    
    def _setup_ssh_tunnel(self, ssh_config, db_host, db_port):
        """Setup SSH tunnel and return local host/port"""
        try:
            from sshtunnel import SSHTunnelForwarder
            import os

            try:
                _ensure_dsskey_stub()
            except ImportError as ex:
                logger.debug(f"paramiko DSSKey patch skipped: {ex}")

            ssh_host = ssh_config.get("host")
            ssh_port = ssh_config.get("port", 22)
            ssh_user = ssh_config.get("user")
            ssh_password = ssh_config.get("password", "")
            ssh_key_path = ssh_config.get("key_path", "")
            use_key = ssh_config.get("use_key", False)
            
            logger.info(f"Setting up SSH tunnel: use_key={use_key}, key_path={ssh_key_path}")
            
            # Use key or password authentication
            if use_key and ssh_key_path and ssh_key_path.strip():
                # Expand user path if needed (~/.ssh/id_rsa -> /Users/username/.ssh/id_rsa)
                ssh_key_path = os.path.expanduser(ssh_key_path.strip())
                
                # Verify key file exists
                if not os.path.exists(ssh_key_path):
                    raise Exception(f"SSH key file not found: {ssh_key_path}")
                
                logger.info(f"Using SSH key authentication with: {ssh_key_path}")
                
                self.ssh_tunnel = SSHTunnelForwarder(
                    (ssh_host, ssh_port),
                    ssh_username=ssh_user,
                    ssh_private_key=ssh_key_path,
                    remote_bind_address=(db_host, db_port)
                )
            elif ssh_password:
                logger.info(f"Using SSH password authentication")
                self.ssh_tunnel = SSHTunnelForwarder(
                    (ssh_host, ssh_port),
                    ssh_username=ssh_user,
                    ssh_password=ssh_password,
                    remote_bind_address=(db_host, db_port)
                )
            else:
                raise Exception("SSH tunnel requires either password or private key")
            
            self.ssh_tunnel.start()
            logger.info(f"SSH tunnel established to {ssh_host}:{ssh_port} -> localhost:{self.ssh_tunnel.local_bind_port}")
            
            # Return localhost and local bind port
            return '127.0.0.1', self.ssh_tunnel.local_bind_port
            
        except ImportError:
            raise Exception("sshtunnel not installed. Run: pip install sshtunnel")
        except Exception as ex:
            logger.error(f"SSH tunnel setup failed: {str(ex)}")
            raise Exception(f"SSH tunnel failed: {str(ex)}")

    def disconnect(self):

        if self.connection:
            logger.info(f"Disconnecting from database: {self.connection_name}")
            self.connection.close()

        self.connection = None
        self.in_transaction = False

        # Close SSH tunnel if active
        if self.ssh_tunnel:
            try:
                self.ssh_tunnel.stop()
                logger.info("SSH tunnel closed")
            except Exception as ex:
                logger.error(f"Error closing SSH tunnel: {str(ex)}")
            self.ssh_tunnel = None

    def _is_connection_error(self, ex):
        """Return True if the exception looks like a dropped/lost connection."""
        msg = str(ex).lower()
        # pymysql's signature for "this connection object is already
        # closed" — an empty-message, zero-code error, not a phrase.
        if msg in ("(0, '')", "0", ""):
            return True
        keywords = (
            'lost connection', 'server has gone away', 'broken pipe',
            'connection reset', 'connection closed', 'interface error',
            'server closed', 'operationalerror', 'not connected',
            'connection refused', 'timed out',
        )
        return any(k in msg for k in keywords)

    def kill_current_query(self):
        """Best-effort: kill the running query on the server side.

        MySQL  — opens a second connection and sends KILL QUERY <thread_id>.
        Others — no-op (the cancel flag in the worker thread is sufficient).
        """
        if self.db_type != "mysql" or not self.connection:
            return
        try:
            import pymysql
            thread_id = self.connection.thread_id()
            # Open a short-lived kill connection using the same config
            kc = pymysql.connect(
                host=self._config.get("host", "127.0.0.1"),
                port=int(self._config.get("port", 3306)),
                user=self._config.get("user", ""),
                password=self._config.get("password", ""),
                database=self._config.get("database", ""),
                connect_timeout=3,
            )
            with kc.cursor() as cur:
                cur.execute(f"KILL QUERY {thread_id}")
            kc.close()
            logger.info(f"Sent KILL QUERY {thread_id}")
        except Exception as ex:
            logger.warning(f"kill_current_query failed (non-fatal): {ex}")

    def _reconnect(self, config=None):
        """Re-establish the connection using *config*, or the previously
        stored config if not given (the automatic-retry-after-a-dropped-
        connection path, where nothing about the profile has changed)."""
        config = config or self._config
        if not config:
            raise Exception("No connection config stored — cannot reconnect")
        logger.info(f"Attempting reconnect to {self.connection_name}...")
        # Close cleanly first
        try:
            if self.connection:
                self.connection.close()
        except Exception:
            pass
        self.connection = None
        # Re-open SSH tunnel if needed
        if self.ssh_tunnel:
            try:
                self.ssh_tunnel.stop()
            except Exception:
                pass
            self.ssh_tunnel = None
        self.connect(config)
        logger.info("Reconnect successful")

    def select_db(self, database: str):
        """Point the live connection at *database* (MySQL only — Postgres/
        SQLite connections are bound to one database for their lifetime).
        Callers use this instead of `self.connection.select_db(...)`
        directly because that bypasses the reconnect-on-drop retry
        execute_query() gets for free — a connection that went stale (idle
        timeout, dropped tunnel) would otherwise fail select_db() outright
        and leave the switch permanently stuck."""
        if self.connection:
            try:
                self.connection.select_db(database)
                return
            except Exception as ex:
                if not self._is_connection_error(ex):
                    raise
        self._reconnect(dict(self._config, database=database))

    def is_connected(self):
        """Check if connected by opening a *separate* short-lived connection.
        Never touches self.connection so it cannot corrupt a running query's
        packet sequence."""
        if not self._config or not self.connection:
            return False
        try:
            if self.db_type == "mysql":
                import pymysql as _pm
                kc = _pm.connect(
                    host=self._config.get("host", "127.0.0.1"),
                    port=int(self._config.get("port", 3306)),
                    user=self._config.get("user", ""),
                    password=self._config.get("password", ""),
                    connect_timeout=5,
                )
                kc.close()
                return True
            elif self.db_type == "postgresql":
                import psycopg2 as _pg
                kc = _pg.connect(
                    host=self._config.get("host", "127.0.0.1"),
                    port=int(self._config.get("port", 5432)),
                    user=self._config.get("user", ""),
                    password=self._config.get("password", ""),
                    connect_timeout=5,
                )
                kc.close()
                return True
            else:
                # SQLite is always "connected" if we have a connection object
                return self.connection is not None
        except Exception:
            return False

    def _transaction_kind(self, query: str) -> str | None:
        """Return 'BEGIN'/'COMMIT'/'ROLLBACK' if *query* is exactly one
        transaction-control statement (however the user wrote it — typed
        directly in the editor or via the Begin/Commit/Rollback buttons),
        else None. Keeping this the single detection point means
        in_transaction stays accurate regardless of entry point."""
        stmts = query_classifier.split_statements(query)
        if len(stmts) != 1:
            return None
        kind = query_classifier.classify(stmts[0]).kind
        return kind if kind in query_classifier.TRANSACTION_KINDS else None

    def _run_transaction_kind(self, kind: str):
        """Execute a detected transaction-control statement and return a
        status DataFrame, matching the existing DML-feedback shape used by
        _execute_query_raw's 'Query OK, rows affected' rows."""
        if kind == "BEGIN":
            self.begin_transaction()
            status = "Started"
        elif kind == "COMMIT":
            self.commit_transaction()
            status = "Committed"
        else:
            self.rollback_transaction()
            status = "Rolled back"
        return pd.DataFrame([{"Transaction": status}])

    def execute_query(self, query, max_rows=None):
        """Execute a SELECT query and return results as DataFrame.

        max_rows: if given, stop fetching after this many rows (via
        cursor.fetchmany, so the driver itself never pulls more than that
        off the wire for an unbounded ad-hoc SELECT). The returned
        DataFrame carries df.attrs['truncated'] = True when more rows
        existed than were fetched. None (default) fetches everything, for
        callers like exports that need the full result."""

        if not self.connection:
            raise Exception("No active database connection")

        self._guard(query)

        tx_kind = self._transaction_kind(query)
        if tx_kind:
            return self._run_transaction_kind(tx_kind)

        try:
            return self._execute_query_raw(query, max_rows)
        except Exception as ex:
            if self._is_connection_error(ex):
                if self.in_transaction:
                    self.in_transaction = False
                    self._reconnect()
                    raise TransactionError(
                        "Connection was lost while a transaction was open — "
                        "it was rolled back. Reconnected; please retry."
                    ) from ex
                logger.warning(f"Connection lost during query, reconnecting... ({ex})")
                self._reconnect()
                return self._execute_query_raw(query, max_rows)
            raise

    def execute_multi_query(self, script: str, max_rows=None) -> list[tuple[str, object]]:
        """Split *script* into statements, execute each. Returns list of
        (label, DataFrame|int|Exception) tuples, one per statement: a
        SELECT produces its result DataFrame, a write produces the
        affected-row count (int, possibly 0), and a statement that raised
        produces the Exception — the caller (ConnectionPanel.
        _on_query_multi_done) uses this to show every statement's own
        outcome as its own result tab, not just the SELECTs.

        Routes each statement by its real classification (not a first-
        keyword guess), so a write hidden behind a leading comment or
        wrapped in a `WITH` CTE is routed to execute_update() — and
        therefore still passes through self._guard() — instead of silently
        being treated as a read.
        """
        results: list[tuple[str, object]] = []
        stmts = query_classifier.split_statements(script)
        for stmt in stmts:
            label = stmt[:40].replace("\n", " ").strip() + ("…" if len(stmt) > 40 else "")
            if query_classifier.classify(stmt).is_write:
                try:
                    affected = self.execute_update(stmt)
                    results.append((label, affected))
                except Exception as ex:
                    results.append((label, ex))
            else:
                try:
                    df = self.execute_query(stmt, max_rows=max_rows)
                    results.append((label, df))
                except Exception as ex:
                    results.append((label, ex))
        return results

    def get_foreign_keys(self, table_name: str) -> list[dict]:
        """Return FK definitions for *table_name*.
        Each dict has keys: column, ref_table, ref_column.
        """
        try:
            if self.db_type == "mysql":
                cursor = self.connection.cursor()
                cursor.execute("""
                    SELECT COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
                    FROM information_schema.KEY_COLUMN_USAGE
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME = %s
                      AND REFERENCED_TABLE_NAME IS NOT NULL
                """, (table_name,))
                rows = cursor.fetchall()
                cursor.close()
                return [{"column": list(r.values())[0],
                         "ref_table": list(r.values())[1],
                         "ref_column": list(r.values())[2]} for r in rows]
            elif self.db_type == "postgresql":
                cursor = self.connection.cursor()
                cursor.execute("""
                    SELECT kcu.column_name,
                           ccu.table_name  AS ref_table,
                           ccu.column_name AS ref_column
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                         ON tc.constraint_name = kcu.constraint_name
                    JOIN information_schema.constraint_column_usage ccu
                         ON ccu.constraint_name = tc.constraint_name
                    WHERE tc.constraint_type = 'FOREIGN KEY'
                      AND tc.table_name = %s
                """, (table_name,))
                rows = cursor.fetchall()
                cursor.close()
                return [{"column": r[0], "ref_table": r[1], "ref_column": r[2]} for r in rows]
            elif self.db_type == "sqlite":
                cursor = self.connection.cursor()
                cursor.execute(f"PRAGMA foreign_key_list({self._q(table_name)})")
                rows = cursor.fetchall()
                cursor.close()
                return [{"column": r[3], "ref_table": r[2], "ref_column": r[4]} for r in rows]
        except Exception:
            pass
        return []

    def get_primary_keys(self, table_name: str) -> list[str]:
        """Return the primary-key column name(s) for *table_name*, in key
        order. Empty list if the table has no primary key or on failure."""
        try:
            if self.db_type == "mysql":
                cursor = self.connection.cursor()
                cursor.execute(f"SHOW KEYS FROM {self._q(table_name)} WHERE Key_name = 'PRIMARY'")
                rows = cursor.fetchall()
                cursor.close()
                rows.sort(key=lambda r: r["Seq_in_index"])
                return [r["Column_name"] for r in rows]
            elif self.db_type == "postgresql":
                cursor = self.connection.cursor()
                cursor.execute("""
                    SELECT a.attname
                    FROM pg_index i
                    JOIN pg_attribute a
                         ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                    WHERE i.indrelid = %s::regclass AND i.indisprimary
                    ORDER BY array_position(i.indkey, a.attnum)
                """, (table_name,))
                rows = cursor.fetchall()
                cursor.close()
                return [r[0] for r in rows]
            elif self.db_type == "sqlite":
                cursor = self.connection.cursor()
                cursor.execute(f"PRAGMA table_info({self._q(table_name)})")
                rows = cursor.fetchall()
                cursor.close()
                pk_rows = sorted((r for r in rows if r[5] > 0), key=lambda r: r[5])
                return [r[1] for r in pk_rows]
        except Exception:
            pass
        return []

    def get_estimated_row_count(self, table_name: str) -> int | None:
        """Fast, stats-based row-count estimate — reads catalog metadata
        instead of scanning the table, so it stays instant on tables too
        big for a real COUNT(*) to be worth blocking on. None if this
        dialect has no such stat (caller should fall back to COUNT(*))."""
        try:
            if self.db_type == "mysql":
                cursor = self.connection.cursor()
                cursor.execute("""
                    SELECT TABLE_ROWS FROM information_schema.TABLES
                    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
                """, (table_name,))
                row = cursor.fetchone()
                cursor.close()
                if row is not None:
                    val = row["TABLE_ROWS"] if isinstance(row, dict) else row[0]
                    return int(val) if val is not None else None
            elif self.db_type == "postgresql":
                cursor = self.connection.cursor()
                cursor.execute("SELECT reltuples::bigint FROM pg_class WHERE oid = %s::regclass", (table_name,))
                row = cursor.fetchone()
                cursor.close()
                if row is not None and row[0] is not None:
                    return max(0, int(row[0]))
        except Exception:
            pass
        return None

    def get_generated_columns(self, table_name: str) -> list[str]:
        """Column names that are computed (`GENERATED ALWAYS AS`/STORED or
        VIRTUAL) and therefore can't appear in an INSERT column list (issue
        #160). Empty list — not an error — on any dialect/version that
        doesn't expose this, meaning "believed to have none"."""
        try:
            if self.db_type == "mysql":
                cursor = self.connection.cursor()
                cursor.execute(f"SHOW COLUMNS FROM {self._q(table_name)}")
                rows = cursor.fetchall()
                cursor.close()
                return [r["Field"] for r in rows if "GENERATED" in (r.get("Extra") or "").upper()]
            elif self.db_type == "postgresql":
                cursor = self.connection.cursor()
                cursor.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = %s AND is_generated = 'ALWAYS'",
                    (table_name,),
                )
                rows = cursor.fetchall()
                cursor.close()
                return [r[0] for r in rows]
            elif self.db_type == "sqlite":
                # PRAGMA table_xinfo adds a `hidden` column PRAGMA table_info
                # lacks: 2 = VIRTUAL generated, 3 = STORED generated.
                cursor = self.connection.cursor()
                cursor.execute(f"PRAGMA table_xinfo({self._q(table_name)})")
                rows = cursor.fetchall()
                cursor.close()
                return [r[1] for r in rows if r[6] in (2, 3)]
        except Exception:
            pass
        return []

    def content_select_list(self, table_name: str) -> str:
        """Column list for a content-export `SELECT`, with generated
        columns excluded (issue #160) — they can't appear in the INSERT
        statements built from that data anyway, so there's no point
        exporting them. `"*"` when the table has none (or the dialect can't
        tell)."""
        generated = set(self.get_generated_columns(table_name))
        if not generated:
            return "*"
        cols = [self._q(c["Field"]) for c in self.get_columns(table_name) if c["Field"] not in generated]
        return ", ".join(cols) if cols else "*"

    def _fetch_rows(self, cursor, max_rows):
        """fetchall(), or fetchmany(max_rows) with one extra row peeked to
        detect truncation without pulling the whole result set first."""
        if max_rows is None:
            return cursor.fetchall(), False
        rows = cursor.fetchmany(max_rows + 1)
        return rows[:max_rows], len(rows) > max_rows

    def stream_table_rows(self, table_name: str, chunk_size: int = 2000):
        """Yield (columns, rows) chunks for `SELECT * FROM table_name`, read
        via cursor.fetchmany() instead of execute_query()'s fetchall() —
        exports use this so memory and time-to-first-byte don't scale with
        table size (issue #158). `rows` is a list of plain tuples in column
        order regardless of dialect (mysql's cursor yields dicts, sqlite
        yields sqlite3.Row, postgres yields tuples already).

        Uses this DbService's own connection directly, with no reconnect
        logic — callers exporting in the background should pass a dedicated
        DbService instance (as `_run_query_in_tab` already does for ad-hoc
        queries) rather than sharing the interactive connection used for
        schema browsing.
        """
        if not self.connection:
            raise Exception("No active database connection")
        cursor = self.connection.cursor()
        try:
            cursor.execute(f"SELECT * FROM {self._q(table_name)}")  # nosec B608 -- identifier quoted/escaped via self._q()
            columns = [d[0] for d in cursor.description]
            while True:
                rows = cursor.fetchmany(chunk_size)
                if not rows:
                    break
                yield columns, [tuple(r.values()) if isinstance(r, dict) else tuple(r) for r in rows]
        finally:
            cursor.close()

    def _execute_query_raw(self, query, max_rows=None):
        """Internal: run a SQL statement without reconnect logic.
        For statements that return a result set (SELECT/SHOW/EXPLAIN/DESCRIBE),
        returns a DataFrame.  For DML (UPDATE/INSERT/DELETE/…) returns an empty
        DataFrame with an '_affected_rows' column so the UI can show the count."""
        if self.db_type == "mysql":
            cursor = self.connection.cursor()
            cursor.execute(query)
            if cursor.description:
                cols = [d[0] for d in cursor.description]
                rows, truncated = self._fetch_rows(cursor, max_rows)
                cursor.close()
                df = pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)
                df.attrs["truncated"] = truncated
                return df
            else:
                affected = cursor.rowcount
                cursor.close()
                return pd.DataFrame([{"Query OK, rows affected": affected}])

        elif self.db_type == "postgresql":
            import psycopg2.extras
            cursor = self.connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cursor.execute(query)
            if cursor.description:
                cols = [d[0] for d in cursor.description]
                rows, truncated = self._fetch_rows(cursor, max_rows)
                cursor.close()
                df = pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)
                df.attrs["truncated"] = truncated
                return df
            else:
                affected = cursor.rowcount
                cursor.close()
                return pd.DataFrame([{"Query OK, rows affected": affected}])

        elif self.db_type == "sqlite":
            cursor = self.connection.cursor()
            cursor.execute(query)
            if cursor.description:
                cols = [d[0] for d in cursor.description]
                rows, truncated = self._fetch_rows(cursor, max_rows)
                cursor.close()
                data = [dict(row) for row in rows]
                df = pd.DataFrame(data, columns=cols) if data else pd.DataFrame(columns=cols)
                df.attrs["truncated"] = truncated
                return df
            else:
                affected = cursor.rowcount
                if not self.in_transaction:
                    self.connection.commit()
                cursor.close()
                return pd.DataFrame([{"Query OK, rows affected": affected}])

        else:
            raise Exception(f"Unsupported database type: {self.db_type}")

    def execute_update(self, query):

        if not self.connection:
            raise Exception("No active database connection")

        self._guard(query)

        try:
            result = self._execute_update_raw(query)
        except Exception as ex:
            if self._is_connection_error(ex):
                if self.in_transaction:
                    self.in_transaction = False
                    self._reconnect()
                    raise TransactionError(
                        "Connection was lost while a transaction was open — "
                        "it was rolled back. Reconnected; please retry."
                    ) from ex
                logger.warning(f"Connection lost during update, reconnecting... ({ex})")
                self._reconnect()
                result = self._execute_update_raw(query)
            else:
                logger.error(f"Update execution error: {str(ex)}")
                raise

        self._invalidate_schema_cache_if_ddl(query)
        return result

    def _invalidate_schema_cache_if_ddl(self, query: str):
        """Issue #72: every write in the app funnels through here, so this
        is the single place that can catch a successful schema-changing
        statement (raw SQL, the Create/Alter Table dialogs, etc.) and drop
        the now-stale on-disk schema cache entry."""
        if not self._config:
            return
        try:
            stmts = query_classifier.split_statements(query)
            changed = any(
                query_classifier.classify(s).kind in query_classifier.SCHEMA_CHANGING_KINDS
                for s in stmts
            )
            if changed:
                schema_cache.invalidate(
                    self._config.get("id", ""), self._config.get("database", ""))
        except Exception as ex:
            logger.debug(f"Schema-cache invalidation check failed: {ex}")

    def _execute_update_raw(self, query):
        """Internal: run DML without reconnect logic. The explicit commit()
        is a no-op for MySQL/PostgreSQL under normal autocommit=True but is
        what actually persists SQLite writes — skipped while a manual
        transaction is open so a script's own COMMIT/ROLLBACK decides when
        these statements take effect instead of each one committing
        immediately, which would end the transaction after the first write."""
        cursor = self.connection.cursor()
        cursor.execute(query)
        affected_rows = cursor.rowcount
        cursor.close()
        if not self.in_transaction:
            self.connection.commit()
        return affected_rows

    def execute_batch(self, query, rows, batch_size=500, on_batch=None, should_cancel=None):
        """Run `query` via executemany, in batches, committing once at the end.

        on_batch(start, batch_len) is called after each batch (for progress UI).
        should_cancel() is polled before each batch; returning True stops early.
        Returns (inserted, errors) — errors is the count of batches that raised.
        """
        if not self.connection:
            raise Exception("No active database connection")

        self._guard(query)

        inserted = 0
        errors = 0
        cursor = self.connection.cursor()
        try:
            for start in range(0, len(rows), batch_size):
                if should_cancel and should_cancel():
                    break
                chunk = rows[start:start + batch_size]
                try:
                    cursor.executemany(query, chunk)
                    inserted += len(chunk)
                except Exception as ex:
                    errors += 1
                    logger.error(f"Batch execute error: {ex}")
                if on_batch:
                    on_batch(start, len(chunk))
            self.connection.commit()
        finally:
            cursor.close()
        return inserted, errors

    def get_server_version(self) -> str:
        """Return a short version string like 'MySQL 8.0.41' or 'PostgreSQL 15.3'."""
        try:
            if self.db_type == "mysql":
                cursor = self.connection.cursor()
                cursor.execute("SELECT VERSION()")
                row = cursor.fetchone()
                cursor.close()
                ver = list(row.values())[0] if isinstance(row, dict) else row[0]
                return f"MySQL {ver}"
            elif self.db_type == "postgresql":
                cursor = self.connection.cursor()
                cursor.execute("SHOW server_version")
                row = cursor.fetchone()
                cursor.close()
                return f"PostgreSQL {row[0]}"
            elif self.db_type == "sqlite":
                import sqlite3 as _sq
                return f"SQLite {_sq.sqlite_version}"
        except Exception:
            pass
        return self.db_type.title() if self.db_type else ""

    def get_tables(self):
        """Get list of tables based on database type"""
        
        if self.db_type == "mysql":
            cursor = self.connection.cursor()
            cursor.execute("SHOW TABLES")
            result = cursor.fetchall()
            tables = [list(row.values())[0] for row in result]
        
        elif self.db_type == "postgresql":
            cursor = self.connection.cursor()
            cursor.execute("""
                SELECT tablename FROM pg_tables 
                WHERE schemaname = 'public'
                ORDER BY tablename
            """)
            result = cursor.fetchall()
            tables = [row[0] for row in result]
        
        elif self.db_type == "sqlite":
            cursor = self.connection.cursor()
            cursor.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
            """)
            result = cursor.fetchall()
            tables = [row[0] for row in result]
        
        else:
            tables = []
        
        tables.sort()
        return tables
    
    def get_views(self):
        """Get list of views based on database type"""
        
        if self.db_type == "mysql":
            cursor = self.connection.cursor()
            cursor.execute("SHOW FULL TABLES WHERE Table_type = 'VIEW'")
            result = cursor.fetchall()
            views = [list(row.values())[0] for row in result]
        
        elif self.db_type == "postgresql":
            cursor = self.connection.cursor()
            cursor.execute("""
                SELECT viewname FROM pg_views 
                WHERE schemaname = 'public'
                ORDER BY viewname
            """)
            result = cursor.fetchall()
            views = [row[0] for row in result]
        
        elif self.db_type == "sqlite":
            cursor = self.connection.cursor()
            cursor.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='view'
                ORDER BY name
            """)
            result = cursor.fetchall()
            views = [row[0] for row in result]
        
        else:
            views = []
        
        views.sort()
        return views
    
    def get_functions(self):
        """Get list of functions/procedures based on database type"""
        
        if self.db_type == "mysql":
            cursor = self.connection.cursor()
            # Get both functions and procedures
            cursor.execute("SHOW FUNCTION STATUS WHERE Db = DATABASE()")
            functions = [row['Name'] for row in cursor.fetchall()]
            cursor.execute("SHOW PROCEDURE STATUS WHERE Db = DATABASE()")
            procedures = [row['Name'] for row in cursor.fetchall()]
            items = functions + procedures
        
        elif self.db_type == "postgresql":
            cursor = self.connection.cursor()
            cursor.execute("""
                SELECT routine_name FROM information_schema.routines 
                WHERE routine_schema = 'public'
                ORDER BY routine_name
            """)
            result = cursor.fetchall()
            items = [row[0] for row in result]
        
        elif self.db_type == "sqlite":
            # SQLite doesn't support stored procedures/functions
            items = []
        
        else:
            items = []
        
        items.sort() if items else None
        return items

    def get_columns(self, table_name):
        """Get columns for a table based on database type"""
        
        if self.db_type == "mysql":
            cursor = self.connection.cursor()
            cursor.execute(f"SHOW COLUMNS FROM {self._q(table_name)}")
            return cursor.fetchall()
        
        elif self.db_type == "postgresql":
            cursor = self.connection.cursor()
            cursor.execute("""
                SELECT column_name as "Field", data_type as "Type",
                       is_nullable as "Null", column_default as "Default"
                FROM information_schema.columns
                WHERE table_name = %s
                ORDER BY ordinal_position
            """, (table_name,))
            result = cursor.fetchall()
            # Convert to dict format similar to MySQL
            return [{"Field": row[0], "Type": row[1], "Null": row[2], "Default": row[3]} 
                    for row in result]
        
        elif self.db_type == "sqlite":
            cursor = self.connection.cursor()
            cursor.execute(f"PRAGMA table_info({self._q(table_name)})")
            result = cursor.fetchall()
            # Convert to dict format
            return [{"Field": row[1], "Type": row[2], "Null": "YES" if not row[3] else "NO",
                    "Default": row[4]} for row in result]
        
        else:
            return []

    def get_table_ddl(self, table_name: str) -> str:
        """Return this table's CREATE TABLE statement, followed by its
        indexes and foreign keys as separate statements — export
        "Structure" means all three, not just the bare CREATE TABLE.

        MySQL's SHOW CREATE TABLE already inlines indexes/FKs, so it needs
        nothing extra. SQLite stores each object's exact original DDL in
        sqlite_master — CREATE TABLE inlines its own FKs, so only indexes
        (skipping the implicit ones backing inline PK/UNIQUE, which have no
        stored sql text) need appending. PostgreSQL has no single built-in
        equivalent, so the table is reconstructed from information_schema
        columns + the primary key, then indexes (excluding the PK's own
        backing index) and FKs are appended — a reasonable approximation,
        not a full pg_dump (no comments/non-FK constraints)."""
        cursor = self.connection.cursor()
        if self.db_type == "mysql":
            cursor.execute(f"SHOW CREATE TABLE {self._q(table_name)}")
            row = cursor.fetchone()
            return list(row.values())[1] + ";" if row else ""

        elif self.db_type == "sqlite":
            cursor.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            )
            row = cursor.fetchone()
            if not row or not row[0]:
                return ""
            statements = [row[0] + ";"]
            cursor.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
                (table_name,),
            )
            statements.extend(r[0] + ";" for r in cursor.fetchall())
            return "\n".join(statements)

        elif self.db_type == "postgresql":
            cursor.execute(
                "SELECT a.attname FROM pg_index i "
                "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
                "WHERE i.indrelid = %s::regclass AND i.indisprimary",
                (table_name,),
            )
            pk_cols = [r[0] for r in cursor.fetchall()]
            col_defs = []
            for c in self.get_columns(table_name):
                line = f'{self._q(c["Field"])} {c["Type"]}'
                if c["Null"] == "NO":
                    line += " NOT NULL"
                if c["Default"] is not None:
                    line += f' DEFAULT {c["Default"]}'
                col_defs.append(line)
            if pk_cols:
                col_defs.append(f'PRIMARY KEY ({", ".join(self._q(c) for c in pk_cols)})')
            statements = [f'CREATE TABLE {self._q(table_name)} (\n  ' + ",\n  ".join(col_defs) + "\n);"]

            cursor.execute(
                "SELECT conname FROM pg_constraint WHERE conrelid = %s::regclass AND contype = 'p'",
                (table_name,),
            )
            pk_row = cursor.fetchone()
            pk_index_name = pk_row[0] if pk_row else None
            cursor.execute(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE schemaname = current_schema() AND tablename = %s",
                (table_name,),
            )
            statements.extend(
                indexdef + ";" for indexname, indexdef in cursor.fetchall()
                if indexname != pk_index_name
            )

            for fk in self.get_foreign_keys(table_name):
                statements.append(
                    f'ALTER TABLE {self._q(table_name)} ADD FOREIGN KEY ({self._q(fk["column"])}) '
                    f'REFERENCES {self._q(fk["ref_table"])} ({self._q(fk["ref_column"])});'
                )
            return "\n".join(statements)

        return ""

    def get_all_columns(self) -> dict:
        """Return {table_name: [col_name, ...]} for all tables in one query.
        Used to populate autocomplete — much faster than N individual SHOW COLUMNS calls."""
        try:
            if self.db_type == "mysql":
                cursor = self.connection.cursor()
                cursor.execute("""
                    SELECT TABLE_NAME, COLUMN_NAME
                    FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE()
                    ORDER BY TABLE_NAME, ORDINAL_POSITION
                """)
                rows = cursor.fetchall()
                result: dict = {}
                for row in rows:
                    tbl = list(row.values())[0]
                    col = list(row.values())[1]
                    result.setdefault(tbl, []).append(col)
                return result

            elif self.db_type == "postgresql":
                cursor = self.connection.cursor()
                cursor.execute("""
                    SELECT table_name, column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                    ORDER BY table_name, ordinal_position
                """)
                rows = cursor.fetchall()
                result = {}
                for row in rows:
                    result.setdefault(row[0], []).append(row[1])
                return result

            elif self.db_type == "sqlite":
                cursor = self.connection.cursor()
                cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'"
                )
                tables = [r[0] for r in cursor.fetchall()]
                result = {}
                for tbl in tables:
                    cursor.execute(f"PRAGMA table_info({self._q(tbl)})")
                    result[tbl] = [r[1] for r in cursor.fetchall()]
                return result

        except Exception:
            pass
        return {}

    def get_all_column_details(self) -> dict:
        """Return {table_name: [{name, type, nullable, default, key}, ...]}
        for every table in one round-trip — the type/PK-aware sibling of
        get_all_columns(), used to enrich SQL-editor autocomplete without
        paying an N+1 SHOW COLUMNS/PRAGMA cost per table. `key` is "PRI" for
        primary-key columns, "" otherwise (matching MySQL's own COLUMN_KEY
        vocabulary, reused across dialects for a single downstream shape)."""
        try:
            if self.db_type == "mysql":
                cursor = self.connection.cursor()
                cursor.execute("""
                    SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE,
                           COLUMN_DEFAULT, COLUMN_KEY
                    FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE()
                    ORDER BY TABLE_NAME, ORDINAL_POSITION
                """)
                rows = cursor.fetchall()
                result: dict = {}
                for r in rows:
                    r = dict(r)
                    result.setdefault(r["TABLE_NAME"], []).append({
                        "name": r["COLUMN_NAME"],
                        "type": r["COLUMN_TYPE"],
                        "nullable": r["IS_NULLABLE"] == "YES",
                        "default": r["COLUMN_DEFAULT"],
                        "key": "PRI" if r["COLUMN_KEY"] == "PRI" else "",
                    })
                return result

            elif self.db_type == "postgresql":
                cursor = self.connection.cursor()
                cursor.execute("""
                    SELECT c.table_name, c.column_name, c.data_type,
                           c.is_nullable, c.column_default,
                           CASE WHEN pk.column_name IS NOT NULL THEN 'PRI' ELSE '' END
                    FROM information_schema.columns c
                    LEFT JOIN (
                        SELECT kcu.table_name, kcu.column_name
                        FROM information_schema.table_constraints tc
                        JOIN information_schema.key_column_usage kcu
                             ON tc.constraint_name = kcu.constraint_name
                        WHERE tc.constraint_type = 'PRIMARY KEY'
                    ) pk ON pk.table_name = c.table_name AND pk.column_name = c.column_name
                    WHERE c.table_schema = 'public'
                    ORDER BY c.table_name, c.ordinal_position
                """)
                rows = cursor.fetchall()
                result = {}
                for r in rows:
                    result.setdefault(r[0], []).append({
                        "name": r[1], "type": r[2], "nullable": r[3] == "YES",
                        "default": r[4], "key": r[5],
                    })
                return result

            elif self.db_type == "sqlite":
                cursor = self.connection.cursor()
                cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'"
                )
                tables = [r[0] for r in cursor.fetchall()]
                result = {}
                for tbl in tables:
                    cursor.execute(f"PRAGMA table_info({self._q(tbl)})")
                    result[tbl] = [{
                        "name": r[1], "type": r[2], "nullable": not r[3],
                        "default": r[4], "key": "PRI" if r[5] > 0 else "",
                    } for r in cursor.fetchall()]
                return result

        except Exception:
            pass
        return {}

    def get_all_foreign_keys(self) -> dict:
        """Return {table_name: [{column, ref_table, ref_column}, ...]} for
        every table in one round-trip — the bulk sibling of
        get_foreign_keys(table_name), used to power FK-aware JOIN completion
        without an N+1 query per table."""
        try:
            if self.db_type == "mysql":
                cursor = self.connection.cursor()
                cursor.execute("""
                    SELECT TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
                    FROM information_schema.KEY_COLUMN_USAGE
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND REFERENCED_TABLE_NAME IS NOT NULL
                """)
                rows = cursor.fetchall()
                result: dict = {}
                for r in rows:
                    r = dict(r)
                    result.setdefault(r["TABLE_NAME"], []).append({
                        "column": r["COLUMN_NAME"],
                        "ref_table": r["REFERENCED_TABLE_NAME"],
                        "ref_column": r["REFERENCED_COLUMN_NAME"],
                    })
                return result

            elif self.db_type == "postgresql":
                cursor = self.connection.cursor()
                cursor.execute("""
                    SELECT tc.table_name, kcu.column_name,
                           ccu.table_name  AS ref_table,
                           ccu.column_name AS ref_column
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                         ON tc.constraint_name = kcu.constraint_name
                    JOIN information_schema.constraint_column_usage ccu
                         ON ccu.constraint_name = tc.constraint_name
                    WHERE tc.constraint_type = 'FOREIGN KEY'
                """)
                rows = cursor.fetchall()
                result = {}
                for r in rows:
                    result.setdefault(r[0], []).append(
                        {"column": r[1], "ref_table": r[2], "ref_column": r[3]})
                return result

            elif self.db_type == "sqlite":
                cursor = self.connection.cursor()
                cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'"
                )
                tables = [r[0] for r in cursor.fetchall()]
                result = {}
                for tbl in tables:
                    cursor.execute(f"PRAGMA foreign_key_list({self._q(tbl)})")
                    fks = [{"column": r[3], "ref_table": r[2], "ref_column": r[4]}
                           for r in cursor.fetchall()]
                    if fks:
                        result[tbl] = fks
                return result

        except Exception:
            pass
        return {}

    # System schemas that aren't a user database, hidden from any picker
    # that lists sibling databases on a MySQL host.
    _MYSQL_SYSTEM_DBS = ("information_schema", "mysql", "performance_schema", "sys")

    def get_databases(self) -> list:
        """Other databases reachable on this already-open connection's host
        — used by database pickers (Schema Compare / Data Compare, issue
        feedback: a saved connection profile is host-level and one host can
        hold several databases, so comparing by profile alone isn't enough).
        Empty for sqlite, where the connection *is* a single database file,
        so there's nothing to list."""
        if self.db_type == "mysql":
            cursor = self.connection.cursor()
            cursor.execute("SHOW DATABASES")
            names = [list(row.values())[0] for row in cursor.fetchall()]
            return sorted(n for n in names if n not in self._MYSQL_SYSTEM_DBS)
        if self.db_type == "postgresql":
            df = self.execute_query("SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY datname")
            return df["datname"].tolist()
        return []

    def describe_table(self, table_name):

        cursor = self.connection.cursor()

        cursor.execute(
            f"DESCRIBE {self._q(table_name)}"
        )

        result = cursor.fetchall()

        return pd.DataFrame(result)

    def get_connection_name(self):

        return self.connection_name

    def get_indexes(self, table_name: str) -> list[dict]:
        """Return index definitions for *table_name*.
        Each dict has: name, columns, unique, type.
        """
        try:
            if self.db_type == "mysql":
                cursor = self.connection.cursor()
                cursor.execute(f"SHOW INDEX FROM {self._q(table_name)}")
                rows = cursor.fetchall()
                cursor.close()
                # Group columns by index name
                indexes: dict[str, dict] = {}
                for r in rows:
                    r = dict(r)
                    name = r.get("Key_name", "")
                    if name not in indexes:
                        indexes[name] = {
                            "name": name,
                            "columns": [],
                            "unique": r.get("Non_unique", 1) == 0,
                            "type": r.get("Index_type", "BTREE"),
                        }
                    indexes[name]["columns"].append(r.get("Column_name", ""))
                for idx in indexes.values():
                    idx["columns"] = ", ".join(idx["columns"])
                return list(indexes.values())
            elif self.db_type == "postgresql":
                cursor = self.connection.cursor()
                cursor.execute("""
                    SELECT i.relname AS index_name,
                           ix.indisunique AS is_unique,
                           am.amname AS index_type,
                           array_to_string(array_agg(a.attname ORDER BY k.pos), ', ') AS columns
                    FROM pg_class t
                    JOIN pg_index ix ON t.oid = ix.indrelid
                    JOIN pg_class i ON i.oid = ix.indexrelid
                    JOIN pg_am am ON i.relam = am.oid
                    JOIN pg_attribute a ON a.attrelid = t.oid
                    JOIN unnest(ix.indkey) WITH ORDINALITY AS k(attnum, pos)
                         ON a.attnum = k.attnum
                    WHERE t.relname = %s AND t.relkind = 'r'
                    GROUP BY i.relname, ix.indisunique, am.amname
                    ORDER BY i.relname
                """, (table_name,))
                rows = cursor.fetchall()
                cursor.close()
                return [{"name": r[0], "unique": r[1], "type": r[2], "columns": r[3]} for r in rows]
            elif self.db_type == "sqlite":
                cursor = self.connection.cursor()
                cursor.execute(f"PRAGMA index_list({self._q(table_name)})")
                idx_list = cursor.fetchall()
                indexes = []
                for row in idx_list:
                    row = dict(row) if hasattr(row, 'keys') else row
                    idx_name = row[1] if isinstance(row, (list, tuple)) else row.get("name", "")
                    unique = bool(row[2] if isinstance(row, (list, tuple)) else row.get("unique", 0))
                    cursor.execute(f"PRAGMA index_info({self._q(idx_name)})")
                    info_rows = [dict(r) if hasattr(r, 'keys') else r for r in cursor.fetchall()]
                    cols = ", ".join(str(r[2] if isinstance(r, (list, tuple)) else r.get("name", ""))
                                    for r in info_rows)
                    indexes.append({"name": idx_name, "columns": cols, "unique": unique, "type": "BTREE"})
                cursor.close()
                return indexes
        except Exception:
            pass
        return []