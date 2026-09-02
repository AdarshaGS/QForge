"""Tests for services/query_cost.py.

MySQL coverage is pure unit tests against the rule engine (synthetic
EXPLAIN rows / EXPLAIN ANALYZE tree text) — no test file in this repo
already establishes a live-MySQL-server fixture convention (unlike
Postgres below), and standing one up is out of scope for this module's own
tests. Postgres additionally gets a live-DbService integration test
through estimate_cost()/build_profile() itself, since it already has a
working fixture pattern to reuse (tests/test_db_service_postgresql.py).
"""
import uuid

import psycopg2
import pytest

from services import query_cost
from services.db_service import DbService

# ===========================================================================
# Dialect-independent rule engine
# ===========================================================================

def test_analyze_sql_text_flags_select_star():
    issues = query_cost.analyze_sql_text("SELECT * FROM orders")
    assert any(i.code == "SELECT_STAR" for i in issues)


def test_score_issues_sums_severity_weights():
    issues = [
        query_cost.Issue("CRITICAL", "X", "m", "s"),
        query_cost.Issue("LOW", "Y", "m", "s"),
    ]
    assert query_cost.score_issues(issues) == 105


def test_label_for_picks_worst_issue():
    issues = [
        query_cost.Issue("LOW", "SELECT_STAR", "m", "s"),
        query_cost.Issue("CRITICAL", "FULL_TABLE_SCAN", "m", "s"),
    ]
    assert query_cost.label_for(issues) == "full scan"


def test_label_for_no_issues():
    assert query_cost.label_for([]) == "Looks efficient"


def test_risk_level_for_no_issues_is_low():
    assert query_cost.risk_level_for([]) == "Low"


def test_risk_level_for_tracks_worst_severity():
    issues = [
        query_cost.Issue("LOW", "SELECT_STAR", "m", "s"),
        query_cost.Issue("CRITICAL", "FULL_TABLE_SCAN", "m", "s"),
        query_cost.Issue("MEDIUM", "FILESORT", "m", "s"),
    ]
    assert query_cost.risk_level_for(issues) == "Critical"
    assert query_cost.risk_level_for(issues[:1]) == "Low"
    assert query_cost.risk_level_for(issues[2:3]) == "Medium"


# ===========================================================================
# MySQL — classic tabular EXPLAIN
# ===========================================================================

def test_mysql_full_table_scan_detected():
    rows = [{
        "id": 1, "select_type": "SIMPLE", "table": "orders", "type": "ALL",
        "possible_keys": None, "key": None, "Extra": "", "rows": 50000,
        "filtered": 100,
    }]
    issues = query_cost.analyze_explain_rows(rows, "SELECT * FROM orders")
    codes = {i.code for i in issues}
    assert "FULL_TABLE_SCAN" in codes
    assert "SELECT_STAR" in codes


def test_mysql_indexed_lookup_has_no_scan_issue():
    rows = [{
        "id": 1, "select_type": "SIMPLE", "table": "orders", "type": "const",
        "possible_keys": "PRIMARY", "key": "PRIMARY", "Extra": "", "rows": 1,
        "filtered": 100,
    }]
    issues = query_cost.analyze_explain_rows(rows, "SELECT id FROM orders WHERE id = 1")
    assert not any(i.code == "FULL_TABLE_SCAN" for i in issues)


# ===========================================================================
# MySQL — EXPLAIN ANALYZE tree
# ===========================================================================

_MYSQL_TREE = (
    "-> Nested loop inner join  (cost=1.25 rows=1) "
    "(actual time=0.042..0.045 rows=1 loops=1)\n"
    "    -> Table scan on t1  (cost=0.35 rows=100) "
    "(actual time=0.020..0.022 rows=100 loops=1)\n"
    "    -> Single-row index lookup on t2 using PRIMARY (id=t1.id)  "
    "(cost=0.25 rows=1) (actual time=0.010..0.011 rows=1 loops=100)\n"
)


def test_parse_mysql_analyze_tree_builds_nested_structure():
    root = query_cost.parse_mysql_analyze_tree(_MYSQL_TREE)
    assert root.node_type.startswith("Nested loop")
    assert len(root.children) == 2
    assert root.children[0].table == "t1"
    assert root.children[0].rows_actual == 100
    assert root.children[1].loops == 100


def test_analyze_mysql_profile_flags_table_scan_node():
    root = query_cost.parse_mysql_analyze_tree(_MYSQL_TREE)
    issues = query_cost.analyze_mysql_profile(root, "SELECT * FROM t1 JOIN t2 ON t1.id = t2.id")
    assert any(i.code == "FULL_TABLE_SCAN" for i in issues)


def test_parse_mysql_analyze_tree_captures_cumulative_root_cost():
    # The outermost node's own cost= figure is MySQL's total query cost —
    # this is what estimate_cost() surfaces as native_cost, so the parser
    # must actually capture it, not just rows/time/loops.
    root = query_cost.parse_mysql_analyze_tree(_MYSQL_TREE)
    assert root.cost > 0


# ===========================================================================
# PostgreSQL — JSON plan tree
# ===========================================================================

def test_postgres_seq_scan_on_big_table_flagged():
    plan = {
        "Node Type": "Seq Scan", "Relation Name": "orders",
        "Plan Rows": 50000, "Actual Rows": 52000, "Actual Loops": 1,
        "Actual Total Time": 12.3, "Plans": [],
    }
    issues = query_cost.analyze_postgres_plan(plan, "SELECT * FROM orders", has_actuals=True)
    assert any(i.code == "SEQ_SCAN" for i in issues)


def test_postgres_sort_spill_flagged():
    plan = {
        "Node Type": "Sort", "Sort Method": "external merge",
        "Plan Rows": 1000, "Plans": [],
    }
    issues = query_cost.analyze_postgres_plan(plan, "SELECT 1", has_actuals=False)
    assert any(i.code == "SORT_SPILL" for i in issues)


def test_postgres_estimate_mismatch_flagged_on_actuals():
    plan = {
        "Node Type": "Index Scan", "Relation Name": "orders",
        "Plan Rows": 100, "Actual Rows": 12450, "Actual Loops": 1,
        "Plans": [],
    }
    issues = query_cost.analyze_postgres_plan(plan, "SELECT * FROM orders WHERE x = 1", has_actuals=True)
    assert any(i.code == "ESTIMATE_MISMATCH" for i in issues)


def test_postgres_estimate_mismatch_not_flagged_without_actuals():
    # Plan-only (no ANALYZE) — Actual Rows isn't real data, so the
    # estimate-vs-actual comparison must not fire on it.
    plan = {
        "Node Type": "Index Scan", "Relation Name": "orders",
        "Plan Rows": 100, "Plans": [],
    }
    issues = query_cost.analyze_postgres_plan(plan, "SELECT * FROM orders WHERE x = 1", has_actuals=False)
    assert not any(i.code == "ESTIMATE_MISMATCH" for i in issues)


def test_postgres_index_scan_not_flagged():
    plan = {
        "Node Type": "Index Scan", "Relation Name": "orders",
        "Plan Rows": 1, "Plans": [],
    }
    issues = query_cost.analyze_postgres_plan(plan, "SELECT * FROM orders WHERE id = 1", has_actuals=False)
    assert not any(i.code == "SEQ_SCAN" for i in issues)


# ===========================================================================
# PostgreSQL — live integration (skipped if no local server, mirrors
# tests/test_db_service_postgresql.py's fixture)
# ===========================================================================

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


@pytest.mark.skipif(
    not _postgres_available(),
    reason="No local Postgres reachable as qforge_test@localhost:5432 — see "
           "tests/test_db_service_postgresql.py's module docstring for setup.",
)
class TestPostgresLive:
    @pytest.fixture
    def pg_db(self):
        name = f"qforge_test_{uuid.uuid4().hex[:12]}"
        admin = psycopg2.connect(connect_timeout=5, **_ADMIN_PARAMS)
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{name}"')
        admin.close()

        service = DbService()
        service.connect({
            "type": "postgresql", "name": "test", "host": _PG_HOST, "port": _PG_PORT,
            "user": _PG_USER, "password": _PG_PASSWORD, "database": name,
        })
        service.execute_update("CREATE TABLE orders (id INTEGER PRIMARY KEY, status TEXT)")
        # Bulk insert well past query_cost._PG_BIG_ROWS (10,000) — a seq
        # scan on a handful of rows is correctly *not* flagged (Postgres's
        # planner often prefers one for tiny tables regardless of indexes),
        # so the SEQ_SCAN assertion below needs a genuinely large table.
        service.execute_update(
            "INSERT INTO orders (id, status) "
            "SELECT i, 'open' FROM generate_series(1, 15000) AS i")
        # Without this, the planner's row estimate for a never-analyzed
        # table is wildly low (reltuples defaults to 0), so Plan Rows
        # stays under the SEQ_SCAN threshold regardless of real table size.
        service.execute_update("ANALYZE orders")

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

    def test_estimate_cost_flags_seq_scan(self, pg_db):
        result = query_cost.estimate_cost(pg_db, "SELECT * FROM orders WHERE status = 'open'")
        assert not result.error
        assert any(i.code == "SEQ_SCAN" for i in result.issues)

    def test_estimate_cost_primary_key_lookup_is_clean(self, pg_db):
        result = query_cost.estimate_cost(pg_db, "SELECT id FROM orders WHERE id = 1")
        assert not result.error
        assert not any(i.code == "SEQ_SCAN" for i in result.issues)

    def test_build_profile_returns_actual_rows(self, pg_db):
        result = query_cost.build_profile(pg_db, "SELECT * FROM orders WHERE id = 1")
        assert result.supported
        assert not result.error
        assert result.root is not None
        assert result.total_time_ms >= 0

    def test_estimate_cost_exposes_native_planner_cost_and_tree(self, pg_db):
        # Postgres's own "Total Cost" / "Plan Rows" — distinct from
        # result.score, which is QForge's own derived severity score.
        result = query_cost.estimate_cost(pg_db, "SELECT * FROM orders WHERE id = 1")
        assert not result.error
        assert result.native_cost is not None and result.native_cost >= 0
        assert result.estimated_rows is not None
        assert result.plan_tree is not None
