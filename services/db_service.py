import pymysql
import pandas as pd
import sqlite3
from utils.logger import get_logger

logger = get_logger()


class DbService:

    def __init__(self):
        self.connection = None
        self.connection_name = None
        self.db_type = None  # 'mysql', 'postgresql', 'sqlite'
        self.ssh_tunnel = None  # SSH tunnel object
        self._config = None   # stored for auto-reconnect

    def connect(self, config):
        """Connect to database based on type"""
        
        db_type = config.get("type", "mysql").lower()
        self.db_type = db_type
        
        logger.info(f"Connecting to {db_type} database: {config['name']}")
        
        if db_type == "mysql":
            self._connect_mysql(config)
        elif db_type == "postgresql":
            self._connect_postgresql(config)
        elif db_type == "sqlite":
            self._connect_sqlite(config)
        else:
            raise Exception(f"Unsupported database type: {db_type}")
        
        self.connection_name = config["name"]
        self._config = config   # save for reconnect
        logger.info(f"Successfully connected to {db_type} database")
        return self.connection
    
    def _connect_mysql(self, config):
        """Connect to MySQL database"""
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
            "autocommit": True
        }
        
        # Only add database if provided
        if config.get("database"):
            connect_params["database"] = config["database"]
        
        self.connection = pymysql.connect(**connect_params)
    
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
        except ImportError:
            raise Exception("psycopg2 not installed. Run: pip install psycopg2-binary")
    
    def _connect_sqlite(self, config):
        """Connect to SQLite database"""
        db_path = config.get("database", config.get("path", ""))
        if not db_path:
            raise Exception("SQLite database path is required")
        
        self.connection = sqlite3.connect(db_path)
        self.connection.row_factory = sqlite3.Row
    
    def _setup_ssh_tunnel(self, ssh_config, db_host, db_port):
        """Setup SSH tunnel and return local host/port"""
        try:
            from sshtunnel import SSHTunnelForwarder
            import os
            
            # Fix paramiko DSSKey issue (DSS keys deprecated in paramiko 3.0+)
            # Monkey-patch to prevent sshtunnel from trying to use DSSKey
            try:
                import paramiko
                if not hasattr(paramiko, 'DSSKey'):
                    # Create dummy DSSKey class to prevent errors
                    paramiko.DSSKey = None
            except:
                pass
            
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
        keywords = (
            'lost connection', 'server has gone away', 'broken pipe',
            'connection reset', 'connection closed', 'interface error',
            'server closed', 'operationalerror', 'not connected',
            'connection refused', 'timed out',
        )
        return any(k in msg for k in keywords)

    def _reconnect(self):
        """Re-establish the connection using the stored config."""
        if not self._config:
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
        self.connect(self._config)
        logger.info("Reconnect successful")

    def is_connected(self):

        try:

            if self.connection:
                self.connection.ping(reconnect=True)
                return True

            return False

        except Exception:
            return False

    def execute_query(self, query):
        """Execute a SELECT query and return results as DataFrame"""
        
        if not self.connection:
            raise Exception("No active database connection")

        try:
            return self._execute_query_raw(query)
        except Exception as ex:
            if self._is_connection_error(ex):
                logger.warning(f"Connection lost during query, reconnecting... ({ex})")
                self._reconnect()
                return self._execute_query_raw(query)
            raise

    def _execute_query_raw(self, query):
        """Internal: run SELECT without reconnect logic."""
        if self.db_type == "mysql":
            cursor = self.connection.cursor()
            cursor.execute(query)
            rows = cursor.fetchall()
            cursor.close()
            return pd.DataFrame(rows)
        
        elif self.db_type == "postgresql":
            import psycopg2.extras
            cursor = self.connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cursor.execute(query)
            rows = cursor.fetchall()
            cursor.close()
            return pd.DataFrame(rows)
        
        elif self.db_type == "sqlite":
            cursor = self.connection.cursor()
            cursor.execute(query)
            rows = cursor.fetchall()
            cursor.close()
            data = [dict(row) for row in rows]
            return pd.DataFrame(data)
        
        else:
            raise Exception(f"Unsupported database type: {self.db_type}")

    def execute_update(self, query):

        if not self.connection:
            raise Exception("No active database connection")

        try:
            return self._execute_update_raw(query)
        except Exception as ex:
            if self._is_connection_error(ex):
                logger.warning(f"Connection lost during update, reconnecting... ({ex})")
                self._reconnect()
                return self._execute_update_raw(query)
            logger.error(f"Update execution error: {str(ex)}")
            raise

    def _execute_update_raw(self, query):
        """Internal: run DML without reconnect logic."""
        cursor = self.connection.cursor()
        cursor.execute(query)
        affected_rows = cursor.rowcount
        cursor.close()
        self.connection.commit()
        return affected_rows

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
            cursor.execute(f"SHOW COLUMNS FROM `{table_name}`")
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
            cursor.execute(f"PRAGMA table_info({table_name})")
            result = cursor.fetchall()
            # Convert to dict format
            return [{"Field": row[1], "Type": row[2], "Null": "YES" if not row[3] else "NO", 
                    "Default": row[4]} for row in result]
        
        else:
            return []

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
                    cursor.execute(f"PRAGMA table_info({tbl})")
                    result[tbl] = [r[1] for r in cursor.fetchall()]
                return result

        except Exception:
            pass
        return {}

    def get_databases(self):

        cursor = self.connection.cursor()

        cursor.execute("SHOW DATABASES")

        result = cursor.fetchall()

        databases = []

        for row in result:
            databases.append(list(row.values())[0])

        databases.sort()

        return databases

    def describe_table(self, table_name):

        cursor = self.connection.cursor()

        cursor.execute(
            f"DESCRIBE `{table_name}`"
        )

        result = cursor.fetchall()

        return pd.DataFrame(result)

    def get_connection_name(self):

        return self.connection_name