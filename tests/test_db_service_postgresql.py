"""Integration tests for services/db_service.py's PostgreSQL path.

Runs against a real local Postgres ("real engine, not mocks", same
approach the rest of this test suite's live-DB fixtures use) — connects
as the `qforge_test` role
against a fresh, uniquely-named database per test (see the `pg_database`
fixture) so tests can't see each other's state. A fresh *database* rather
than a fresh schema, specifically because DbService hardcodes
`schemaname = 'public'` in its introspection queries (get_tables,
get_views, get_all_columns) — a schema-only isolation strategy would be
silently invisible to those methods.

Local setup this file expects (one-time):
    createuser -s qforge_test --pwprompt   # password: qforge_test_pw
    (or: psql postgres -c "CREATE ROLE qforge_test WITH LOGIN SUPERUSER PASSWORD 'qforge_test_pw';")

The whole module is skipped automatically if that role/server isn't
reachable, so `pytest` stays green on any machine without a local Postgres.
"""
import uuid

import psycopg2
import pytest

from services.db_service import DbService, ReadOnlyViolation, TransactionError

_PG_HOST = "localhost"
_PG_PORT = 5432
_PG_USER = "qforge_test"
_PG_PASSWORD = "qforge_test_pw"
_ADMIN_PARAMS = dict(host=_PG_HOST, port=_PG_PORT, user=_PG_USER, password=_PG_PASSWORD, database="postgres")


def _postgres_available() -> bool:
    try:
        conn = psycopg2.connect(connect_timeout=2, **_ADMIN_PARAMS)
        conn.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _postgres_available(),
    reason=(
        "No local Postgres reachable as qforge_test@localhost:5432 — "
        "see this file's module docstring for one-time setup."
    ),
)


@pytest.fixture
def pg_database():
    """A fresh, uniquely-named database — created before the test, dropped
    after, so tests can't see each other's state."""
    name = f"qforge_test_{uuid.uuid4().hex[:12]}"
    admin = psycopg2.connect(connect_timeout=5, **_ADMIN_PARAMS)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    admin.close()

    yield name

    admin = psycopg2.connect(connect_timeout=5, **_ADMIN_PARAMS)
    admin.autocommit = True
    with admin.cursor() as cur:
        # Postgres refuses to drop a database with active connections —
        # terminate any left open by a test that forgot to disconnect().
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()", (name,),
        )
        cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
    admin.close()


def _config(pg_database, name="test", read_only=False):
    return {
        "type": "postgresql", "name": name, "host": _PG_HOST, "port": _PG_PORT,
        "user": _PG_USER, "password": _PG_PASSWORD, "database": pg_database,
        "read_only": read_only,
    }


@pytest.fixture
def db(pg_database):
    service = DbService()
    service.connect(_config(pg_database))
    service.execute_update("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
    service.execute_update("INSERT INTO users (id, name) VALUES (1, 'Alice')")
    service.execute_update("INSERT INTO users (id, name) VALUES (2, 'Bob')")
    yield service
    service.disconnect()


def _second_connection(pg_database):
    """A fully independent DbService to the same database — proves
    durability from a change made on `db`, not just in-process state."""
    other = DbService()
    other.connect(_config(pg_database, name="test2"))
    return other


def _admin_cursor(pg_database):
    conn = psycopg2.connect(
        host=_PG_HOST, port=_PG_PORT, user=_PG_USER, password=_PG_PASSWORD, database=pg_database,
    )
    conn.autocommit = True
    return conn, conn.cursor()


# ─── Basic CRUD ─────────────────────────────────────────────────────────


def test_connect_sets_db_type(db):
    assert db.db_type == "postgresql"
    assert db.get_connection_name() == "test"


def test_get_tables_lists_created_table(db):
    assert db.get_tables() == ["users"]


def test_get_tables_empty_database_returns_empty_list(pg_database):
    service = DbService()
    service.connect(_config(pg_database))
    try:
        assert service.get_tables() == []
    finally:
        service.disconnect()


def test_execute_query_returns_rows(db):
    df = db.execute_query("SELECT * FROM users ORDER BY id")
    assert list(df["name"]) == ["Alice", "Bob"]


def test_execute_query_empty_result_set_has_correct_columns(db):
    df = db.execute_query("SELECT * FROM users WHERE id = 999")
    assert list(df.columns) == ["id", "name"]
    assert len(df) == 0


def test_execute_update_reports_affected_rows(db):
    affected = db.execute_update("UPDATE users SET name = 'Carol' WHERE id = 1")
    assert affected == 1
    df = db.execute_query("SELECT name FROM users WHERE id = 1")
    assert df.iloc[0]["name"] == "Carol"


def test_execute_batch_inserts_all_rows_and_commits_once(db):
    rows = [(i, f"user{i}") for i in range(3, 13)]
    inserted, errors = db.execute_batch(
        "INSERT INTO users (id, name) VALUES (%s, %s)", rows, batch_size=4
    )
    assert inserted == 10
    assert errors == 0
    df = db.execute_query("SELECT COUNT(*) as total FROM users")
    assert int(df.iloc[0]["total"]) == 12


def test_execute_batch_counts_failing_batches_without_aborting(db):
    rows = [(1, "dup"), (10, "ok")]  # id=1 already exists -> PK violation
    inserted, errors = db.execute_batch(
        "INSERT INTO users (id, name) VALUES (%s, %s)", rows, batch_size=1
    )
    assert errors == 1
    assert inserted == 1


def test_get_columns_reports_field_names(db):
    cols = db.get_columns("users")
    assert [c["Field"] for c in cols] == ["id", "name"]


def test_get_all_columns_maps_table_to_columns(db):
    mapping = db.get_all_columns()
    assert "users" in mapping
    assert mapping["users"] == ["id", "name"]


# ─── NULLs and data types ───────────────────────────────────────────────


def test_null_values_come_back_as_none(db):
    db.execute_update("CREATE TABLE t_null (id INTEGER PRIMARY KEY, note TEXT)")
    db.execute_update("INSERT INTO t_null (id, note) VALUES (1, NULL)")
    df = db.execute_query("SELECT note FROM t_null WHERE id = 1")
    assert df.iloc[0]["note"] is None


def test_various_postgres_types_round_trip(db):
    db.execute_update(
        "CREATE TABLE t_types (id INTEGER PRIMARY KEY, flag BOOLEAN, "
        "amount NUMERIC(10,2), created TIMESTAMP, tags TEXT[])"
    )
    db.execute_update(
        "INSERT INTO t_types (id, flag, amount, created, tags) VALUES "
        "(1, TRUE, 19.99, '2026-01-15 10:30:00', ARRAY['a', 'b'])"
    )
    df = db.execute_query("SELECT * FROM t_types WHERE id = 1")
    row = df.iloc[0]
    assert bool(row["flag"]) is True
    assert float(row["amount"]) == 19.99
    assert row["tags"] == ["a", "b"]


# ─── Schema introspection ───────────────────────────────────────────────


def test_get_primary_keys_single_column(db):
    assert db.get_primary_keys("users") == ["id"]


def test_get_primary_keys_composite(db):
    db.execute_update(
        "CREATE TABLE membership (org_id INTEGER, user_id INTEGER, "
        "PRIMARY KEY (org_id, user_id))"
    )
    assert db.get_primary_keys("membership") == ["org_id", "user_id"]


def test_get_primary_keys_empty_when_no_pk(db):
    db.execute_update("CREATE TABLE no_pk (note TEXT)")
    assert db.get_primary_keys("no_pk") == []


def test_get_foreign_keys(db):
    db.execute_update(
        "CREATE TABLE orders (id INTEGER PRIMARY KEY, "
        "user_id INTEGER REFERENCES users(id))"
    )
    fks = db.get_foreign_keys("orders")
    assert fks == [{"column": "user_id", "ref_table": "users", "ref_column": "id"}]


def test_get_foreign_keys_empty_when_none(db):
    assert db.get_foreign_keys("users") == []


def test_get_indexes_reports_unique_and_non_unique(db):
    db.execute_update("CREATE UNIQUE INDEX idx_users_name ON users(name)")
    db.execute_update("CREATE TABLE logs (id INTEGER PRIMARY KEY, user_id INTEGER)")
    db.execute_update("CREATE INDEX idx_logs_user ON logs(user_id)")

    user_indexes = {i["name"]: i for i in db.get_indexes("users")}
    assert user_indexes["idx_users_name"]["unique"] is True

    log_indexes = {i["name"]: i for i in db.get_indexes("logs")}
    assert log_indexes["idx_logs_user"]["unique"] is False


def test_get_table_ddl_includes_columns_and_primary_key(db):
    ddl = db.get_table_ddl("users")
    assert "CREATE TABLE" in ddl
    assert "id" in ddl and "name" in ddl
    assert "PRIMARY KEY" in ddl


def test_get_table_ddl_includes_secondary_index_but_not_the_pk_index(db):
    db.execute_update("CREATE INDEX idx_users_name ON users(name)")
    ddl = db.get_table_ddl("users")
    assert "idx_users_name" in ddl
    assert "CREATE INDEX" in ddl
    # The PK's own implicit index must not be re-emitted as a second
    # CREATE statement — it's already covered by the inline PRIMARY KEY.
    assert ddl.count("users_pkey") == 0


def test_get_table_ddl_includes_foreign_keys(db):
    db.execute_update(
        "CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id))"
    )
    ddl = db.get_table_ddl("orders")
    assert "FOREIGN KEY" in ddl
    assert '"user_id"' in ddl
    assert '"users"' in ddl and '"id"' in ddl


def test_get_views(db):
    db.execute_update("CREATE VIEW active_users AS SELECT * FROM users")
    assert db.get_views() == ["active_users"]


def test_get_views_empty_when_none(db):
    assert db.get_views() == []


def test_get_functions_lists_created_function(db):
    db.execute_update(
        "CREATE FUNCTION add_one(n INTEGER) RETURNS INTEGER AS "
        "'SELECT n + 1' LANGUAGE SQL"
    )
    assert "add_one" in db.get_functions()


def test_get_server_version_reports_postgresql(db):
    version = db.get_server_version()
    assert version.startswith("PostgreSQL")


# ─── Multi-statement scripts ────────────────────────────────────────────


def test_execute_multi_query_runs_all_statements(db):
    script = "SELECT * FROM users ORDER BY id; UPDATE users SET name = 'Zed' WHERE id = 1;"
    results = db.execute_multi_query(script)
    assert len(results) == 2
    label1, df1, cost1 = results[0]
    label2, affected, cost2 = results[1]
    assert list(df1["name"]) == ["Alice", "Bob"]
    assert affected == 1  # UPDATE affected 1 row, no result set
    assert db.execute_query("SELECT name FROM users WHERE id = 1").iloc[0]["name"] == "Zed"
    assert cost1 is not None and cost1.error == ""  # SELECT gets a plan-only estimate
    assert cost2 is None  # writes never get one


def test_execute_multi_query_continues_after_a_statement_error(db):
    script = "SELECT * FROM does_not_exist; SELECT * FROM users ORDER BY id;"
    results = db.execute_multi_query(script)
    assert len(results) == 2
    _, first_result, first_cost = results[0]
    _, second_result, second_cost = results[1]
    assert isinstance(first_result, Exception)
    assert first_cost is None  # the SELECT itself failed — nothing to estimate
    assert list(second_result["name"]) == ["Alice", "Bob"]
    assert second_cost is not None and second_cost.error == ""


# ─── Transactions ────────────────────────────────────────────────────────


def test_default_autocommit_behavior_unchanged(db, pg_database):
    db.execute_update("UPDATE users SET name = 'Carol' WHERE id = 1")
    other = _second_connection(pg_database)
    try:
        df = other.execute_query("SELECT name FROM users WHERE id = 1")
        assert df.iloc[0]["name"] == "Carol"
    finally:
        other.disconnect()


def test_begin_transaction_disables_driver_autocommit(db):
    assert db.connection.autocommit is True
    db.begin_transaction()
    assert db.connection.autocommit is False
    db.commit_transaction()
    assert db.connection.autocommit is True


def test_begin_commit_persists_change(db, pg_database):
    db.begin_transaction()
    assert db.in_transaction
    db.execute_update("UPDATE users SET name = 'Carol' WHERE id = 1")
    db.commit_transaction()
    assert not db.in_transaction

    other = _second_connection(pg_database)
    try:
        df = other.execute_query("SELECT name FROM users WHERE id = 1")
        assert df.iloc[0]["name"] == "Carol"
    finally:
        other.disconnect()


def test_begin_rollback_discards_change(db, pg_database):
    db.begin_transaction()
    db.execute_update("UPDATE users SET name = 'Carol' WHERE id = 1")
    db.rollback_transaction()
    assert not db.in_transaction

    other = _second_connection(pg_database)
    try:
        df = other.execute_query("SELECT name FROM users WHERE id = 1")
        assert df.iloc[0]["name"] == "Alice"
    finally:
        other.disconnect()


def test_uncommitted_change_is_invisible_to_another_connection(db, pg_database):
    """With a real second connection over the network, a manual
    transaction's writes must stay invisible until commit — proving
    isolation, not just that commit() was called."""
    db.begin_transaction()
    db.execute_update("UPDATE users SET name = 'Carol' WHERE id = 1")

    other = _second_connection(pg_database)
    try:
        df = other.execute_query("SELECT name FROM users WHERE id = 1")
        assert df.iloc[0]["name"] == "Alice"  # not yet visible
    finally:
        other.disconnect()

    db.commit_transaction()


def test_double_begin_raises(db):
    db.begin_transaction()
    with pytest.raises(TransactionError):
        db.begin_transaction()


def test_commit_without_begin_raises(db):
    with pytest.raises(TransactionError):
        db.commit_transaction()


def test_rollback_without_begin_raises(db):
    with pytest.raises(TransactionError):
        db.rollback_transaction()


def test_raw_typed_begin_commit_via_execute_query(db, pg_database):
    db.execute_query("BEGIN")
    assert db.in_transaction
    db.execute_update("UPDATE users SET name = 'Carol' WHERE id = 1")
    db.execute_query("COMMIT")
    assert not db.in_transaction

    other = _second_connection(pg_database)
    try:
        df = other.execute_query("SELECT name FROM users WHERE id = 1")
        assert df.iloc[0]["name"] == "Carol"
    finally:
        other.disconnect()


def test_raw_typed_rollback_via_execute_query(db, pg_database):
    db.execute_query("BEGIN")
    db.execute_update("UPDATE users SET name = 'Carol' WHERE id = 1")
    db.execute_query("ROLLBACK")
    assert not db.in_transaction

    other = _second_connection(pg_database)
    try:
        df = other.execute_query("SELECT name FROM users WHERE id = 1")
        assert df.iloc[0]["name"] == "Alice"
    finally:
        other.disconnect()


# ─── Read-only guard ─────────────────────────────────────────────────────


def test_read_only_blocks_truncate_and_call(db):
    db.execute_update("CREATE FUNCTION noop() RETURNS VOID AS '' LANGUAGE SQL")
    db.read_only = True
    with pytest.raises(ReadOnlyViolation):
        db.execute_query("TRUNCATE TABLE users")
    with pytest.raises(ReadOnlyViolation):
        db.execute_query("CALL noop()")
    # Statements never reached the driver — data is untouched.
    df = db.execute_query("SELECT COUNT(*) as total FROM users")
    assert int(df.iloc[0]["total"]) == 2


def test_read_only_still_allows_reads(db):
    db.read_only = True
    df = db.execute_query("SELECT * FROM users ORDER BY id")
    assert list(df["name"]) == ["Alice", "Bob"]


def test_read_only_off_by_default_writes_still_work(db):
    assert db.read_only is False
    affected = db.execute_update("UPDATE users SET name = 'Carol' WHERE id = 1")
    assert affected == 1


def test_read_only_session_is_set_on_the_connection(pg_database):
    """Defense-in-depth beyond the client-side guard: connect() itself
    should ask the driver to mark the session read-only server-side."""
    service = DbService()
    service.connect(_config(pg_database, read_only=True))
    try:
        assert service.connection.readonly is True
    finally:
        service.disconnect()


# ─── Connection lifecycle / reconnect ────────────────────────────────────


def test_is_connected_true_when_connected(db):
    assert db.is_connected() is True


def test_is_connected_false_after_disconnect(db):
    db.disconnect()
    assert db.is_connected() is False


def test_execute_query_without_connection_raises():
    service = DbService()
    with pytest.raises(Exception):
        service.execute_query("SELECT 1")


def test_syntax_error_propagates_as_an_exception(db):
    with pytest.raises(Exception):
        db.execute_query("SELECT * FORM users")  # deliberate typo


def test_reconnects_after_server_terminates_the_connection(db):
    """Simulates a real dropped connection (not just closing it locally)
    by having the server kill this session's backend — the shape
    _is_connection_error/_reconnect are actually designed to catch."""
    admin_conn, admin_cur = _admin_cursor(db._config["database"])
    try:
        admin_cur.execute("SELECT pg_backend_pid()")
        this_pid = db.execute_query("SELECT pg_backend_pid() as pid").iloc[0]["pid"]
        admin_cur.execute("SELECT pg_terminate_backend(%s)", (int(this_pid),))
    finally:
        admin_conn.close()

    # The next query transparently reconnects and still succeeds.
    df = db.execute_query("SELECT * FROM users ORDER BY id")
    assert list(df["name"]) == ["Alice", "Bob"]


def test_reconnect_mid_transaction_raises_transaction_error(db):
    admin_conn, admin_cur = _admin_cursor(db._config["database"])
    try:
        db.begin_transaction()
        db.execute_update("UPDATE users SET name = 'Carol' WHERE id = 1")

        this_pid = db.connection.get_backend_pid()
        admin_cur.execute("SELECT pg_terminate_backend(%s)", (this_pid,))
    finally:
        admin_conn.close()

    with pytest.raises(TransactionError):
        db.execute_query("SELECT * FROM users")
    assert db.in_transaction is False

    # Reconnected cleanly — the uncommitted change is gone, and the
    # connection is usable again.
    df = db.execute_query("SELECT name FROM users WHERE id = 1")
    assert df.iloc[0]["name"] == "Alice"
