"""Tests for schema/query impact analysis (issue #236) — pure-logic unit
tests against a fake DbService stub, plus live-Postgres integration
mirroring tests/test_erd_model.py's fixture."""
import uuid

import psycopg2
import pytest

from services.db_service import DbService
from services.dependency_analyzer import (
    find_column_dependents, find_database_dependents, find_table_dependents,
)


class _FakeDb:
    """Stands in for DbService — dependency_analyzer only ever calls
    db_type, get_tables(), get_all_foreign_keys(), get_view_definitions(),
    get_function_definitions(), get_procedure_definitions(),
    get_trigger_definitions()."""

    def __init__(self, db_type="postgresql", tables=None, fks=None, views=None,
                 functions=None, procedures=None, triggers=None):
        self.db_type = db_type
        self._tables = tables or []
        self._fks = fks or {}
        self._views = views or {}
        self._functions = functions or {}
        self._procedures = procedures or {}
        self._triggers = triggers or {}

    def get_tables(self):
        return self._tables

    def get_all_foreign_keys(self):
        return self._fks

    def get_view_definitions(self):
        return self._views

    def get_function_definitions(self):
        return self._functions

    def get_procedure_definitions(self):
        return self._procedures

    def get_trigger_definitions(self):
        return self._triggers


def test_find_table_dependents_finds_incoming_foreign_key():
    db = _FakeDb(fks={
        "orders": [{"column": "customer_id", "ref_table": "customers", "ref_column": "id",
                    "constraint": "orders_customer_id_fk"}],
    })

    report = find_table_dependents(db, "customers")

    assert report.supported
    assert len(report.foreign_keys) == 1
    ref = report.foreign_keys[0]
    assert ref.name == "orders"
    assert ref.column == "customer_id"
    assert ref.ref_column == "id"
    assert ref.detail == "orders.customer_id → customers.id"
    assert report.views == report.functions == report.procedures == report.triggers == []


def test_find_column_dependents_filters_by_ref_column():
    db = _FakeDb(fks={
        "orders": [
            {"column": "customer_id", "ref_table": "customers", "ref_column": "id"},
            {"column": "referred_by", "ref_table": "customers", "ref_column": "email"},
        ],
    })

    report = find_column_dependents(db, "customers", "id")

    assert len(report.foreign_keys) == 1
    assert report.foreign_keys[0].name == "orders"


def test_find_table_dependents_matches_view_function_procedure_and_trigger():
    db = _FakeDb(
        views={"active_customers": "SELECT * FROM customers WHERE active = true"},
        functions={"recalc_customer_total": "UPDATE customers SET total = 0"},
        procedures={"purge_customers": "DELETE FROM customers WHERE inactive"},
        triggers={"customers_audit": "ON customers INSERT INTO audit_log VALUES (NEW.id)"},
    )

    report = find_table_dependents(db, "customers")

    assert [v.name for v in report.views] == ["active_customers"]
    assert [f.name for f in report.functions] == ["recalc_customer_total"]
    assert [p.name for p in report.procedures] == ["purge_customers"]
    assert [t.name for t in report.triggers] == ["customers_audit"]
    # The definition text is carried on the ref for the dialog's on-demand
    # "View Definition" popup — no second lookup needed.
    assert report.functions[0].detail == "UPDATE customers SET total = 0"


def test_find_table_dependents_ignores_substring_match():
    """'customers' must not match a view referencing 'old_customers' —
    whole-word matching, not substring."""
    db = _FakeDb(views={"old_report": "SELECT * FROM old_customers"})

    report = find_table_dependents(db, "customers")

    assert report.views == []


def test_find_column_dependents_requires_both_table_and_column_in_definition():
    db = _FakeDb(views={
        "v1": "SELECT id, email FROM customers",   # mentions table, not "phone"
        "v2": "SELECT id, phone FROM customers",   # mentions both
    })

    report = find_column_dependents(db, "customers", "phone")

    assert [v.name for v in report.views] == ["v2"]


def test_find_table_dependents_unsupported_db_type_returns_empty_no_crash():
    db = _FakeDb(db_type="sqlite", fks={"orders": [
        {"column": "customer_id", "ref_table": "customers", "ref_column": "id"},
    ]})

    report = find_table_dependents(db, "customers")

    assert report.supported is False
    assert report.is_empty()
    assert report.total() == 0


def test_report_is_empty_and_total():
    db = _FakeDb()
    report = find_table_dependents(db, "customers")
    assert report.is_empty()
    assert report.total() == 0
    assert report.target_label == "customers"


def test_find_database_dependents_returns_one_report_per_table():
    db = _FakeDb(
        tables=["customers", "orders", "lonely"],
        fks={"orders": [{"column": "customer_id", "ref_table": "customers", "ref_column": "id"}]},
        views={"active_customers": "SELECT * FROM customers"},
    )

    reports = find_database_dependents(db)

    by_table = {r.table: r for r in reports}
    assert set(by_table) == {"customers", "orders", "lonely"}
    assert [fk.name for fk in by_table["customers"].foreign_keys] == ["orders"]
    assert [v.name for v in by_table["customers"].views] == ["active_customers"]
    assert by_table["orders"].is_empty()
    assert by_table["lonely"].is_empty()
    # Every report is table-scoped, not column-scoped.
    assert all(r.column is None for r in reports)


def test_find_database_dependents_unsupported_db_type_returns_empty_list_no_crash():
    db = _FakeDb(db_type="sqlite", tables=["customers"])

    assert find_database_dependents(db) == []


def test_find_database_dependents_empty_schema_returns_empty_list():
    db = _FakeDb(tables=[])

    assert find_database_dependents(db) == []


# ── Live-Postgres integration ────────────────────────────────────────────

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


_live_pg = pytest.mark.skipif(
    not _postgres_available(),
    reason="No local Postgres reachable as qforge_test@localhost:5432 — see "
           "tests/test_db_service_postgresql.py's module docstring for setup.",
)


@pytest.fixture
def db():
    name = f"qforge_test_{uuid.uuid4().hex[:12]}"
    admin = psycopg2.connect(connect_timeout=5, **_ADMIN_PARAMS)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    admin.close()

    config = {
        "type": "postgresql", "name": "dep_test", "host": _PG_HOST, "port": _PG_PORT,
        "user": _PG_USER, "password": _PG_PASSWORD, "database": name,
    }
    service = DbService()
    service.connect(config)

    yield service

    service.disconnect()
    admin = psycopg2.connect(connect_timeout=5, **_ADMIN_PARAMS)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()", (name,))
        cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
    admin.close()


@_live_pg
def test_live_postgres_finds_fk_view_function_procedure_and_trigger(db):
    db.execute_update("""
        CREATE TABLE customers (
            id INTEGER PRIMARY KEY,
            email TEXT
        )
    """)
    db.execute_update("""
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            customer_id INTEGER REFERENCES customers(id)
        )
    """)
    db.execute_update("""
        CREATE TABLE audit_log (
            id INTEGER PRIMARY KEY,
            customer_id INTEGER
        )
    """)
    db.execute_update("CREATE VIEW active_customers AS SELECT * FROM customers")
    db.execute_update("""
        CREATE FUNCTION recalc_customer_total(cust_id INTEGER) RETURNS void AS $$
        BEGIN
            UPDATE customers SET id = cust_id WHERE id = cust_id;
        END;
        $$ LANGUAGE plpgsql
    """)
    db.execute_update("""
        CREATE PROCEDURE purge_customer(cust_id INTEGER) AS $$
        BEGIN
            DELETE FROM customers WHERE id = cust_id;
        END;
        $$ LANGUAGE plpgsql
    """)
    db.execute_update("""
        CREATE FUNCTION log_customer_change() RETURNS trigger AS $$
        BEGIN
            INSERT INTO audit_log (customer_id) VALUES (NEW.id);
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    db.execute_update("""
        CREATE TRIGGER customers_audit AFTER INSERT ON customers
        FOR EACH ROW EXECUTE FUNCTION log_customer_change()
    """)

    report = find_table_dependents(db, "customers")

    assert report.supported
    assert [fk.name for fk in report.foreign_keys] == ["orders"]
    assert "active_customers" in [v.name for v in report.views]
    assert "recalc_customer_total" in [f.name for f in report.functions]
    assert "purge_customer" in [p.name for p in report.procedures]
    assert "customers_audit" in [t.name for t in report.triggers]
    # log_customer_change() is a trigger *function*, not called directly in
    # a way this heuristic text-search would catch here (its body mentions
    # audit_log, not customers) — the trigger itself is what's attributed
    # to "customers", via its "ON customers" clause.
    assert "log_customer_change" not in [f.name for f in report.functions]


@_live_pg
def test_live_postgres_no_dependents_for_unreferenced_table(db):
    db.execute_update("CREATE TABLE lonely (id INTEGER PRIMARY KEY)")

    report = find_table_dependents(db, "lonely")

    assert report.supported
    assert report.is_empty()


@_live_pg
def test_live_postgres_database_scan_matches_per_table_lookup(db):
    """find_database_dependents()'s shared-bulk-fetch path must agree with
    find_table_dependents()'s per-call path — same catalog, same matches,
    just fetched once instead of N times."""
    db.execute_update("CREATE TABLE customers (id INTEGER PRIMARY KEY)")
    db.execute_update("""
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            customer_id INTEGER REFERENCES customers(id)
        )
    """)
    db.execute_update("CREATE TABLE lonely (id INTEGER PRIMARY KEY)")
    db.execute_update("CREATE VIEW active_customers AS SELECT * FROM customers")

    reports = find_database_dependents(db)
    by_table = {r.table: r for r in reports}

    assert set(by_table) >= {"customers", "orders", "lonely"}
    assert [fk.name for fk in by_table["customers"].foreign_keys] == ["orders"]
    assert [v.name for v in by_table["customers"].views] == ["active_customers"]
    assert by_table["lonely"].is_empty()

    # Cross-check against the single-table path for the referenced table.
    single = find_table_dependents(db, "customers")
    assert [fk.name for fk in single.foreign_keys] == [fk.name for fk in by_table["customers"].foreign_keys]
    assert [v.name for v in single.views] == [v.name for v in by_table["customers"].views]


@_live_pg
def test_live_postgres_column_scoped_lookup(db):
    db.execute_update("""
        CREATE TABLE customers (
            id INTEGER PRIMARY KEY,
            email TEXT UNIQUE
        )
    """)
    db.execute_update("""
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY,
            customer_email TEXT REFERENCES customers(email)
        )
    """)

    by_id = find_column_dependents(db, "customers", "id")
    by_email = find_column_dependents(db, "customers", "email")

    assert by_id.is_empty()
    assert [fk.name for fk in by_email.foreign_keys] == ["orders"]
