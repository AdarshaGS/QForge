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
        query_cost.Issue("MEDIUM", "X", "m", "s"),
        query_cost.Issue("LOW", "Y", "m", "s"),
    ]
    assert query_cost.score_issues(issues) == 25


def test_score_issues_caps_at_100():
    issues = [
        query_cost.Issue("CRITICAL", "X", "m", "s"),
        query_cost.Issue("LOW", "Y", "m", "s"),
    ]
    assert query_cost.score_issues(issues) == 100


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
# MySQL — full-scan severity must be contextual, not "type=ALL == CRITICAL"
# ===========================================================================

def _full_scan_row(table="t", rows=38, possible_keys=None, filtered=100):
    return [{
        "id": 1, "select_type": "SIMPLE", "table": table, "type": "ALL",
        "possible_keys": possible_keys, "key": None, "Extra": "", "rows": rows,
        "filtered": filtered,
    }]


def test_full_scan_of_small_table_is_low_severity_with_no_index_recommendation():
    rows = _full_scan_row("audit_logs", rows=38)
    issues = query_cost.analyze_explain_rows(rows, "SELECT * FROM audit_logs;")
    scan = next(i for i in issues if i.code == "FULL_TABLE_SCAN")
    assert scan.severity in ("INFO", "LOW")
    assert scan.suggestion == ""
    assert query_cost.risk_level_for(issues) == "Low"


def test_full_scan_no_where_clause_never_recommends_an_index_regardless_of_size():
    # Reading the whole table is the only possible plan when there's
    # nothing to filter on — an index can't help, even on a huge table.
    rows = _full_scan_row("events", rows=5_000_000)
    issues = query_cost.analyze_explain_rows(rows, "SELECT * FROM events;")
    scan = next(i for i in issues if i.code == "FULL_TABLE_SCAN")
    assert scan.suggestion == ""


def test_full_scan_large_table_with_selective_filter_and_no_index_is_flagged_and_actionable():
    rows = _full_scan_row("events", rows=250_000, possible_keys=None, filtered=2.5)
    issues = query_cost.analyze_explain_rows(rows, "SELECT * FROM events WHERE status = 'pending';")
    scan = next(i for i in issues if i.code == "FULL_TABLE_SCAN")
    assert scan.severity in ("HIGH", "CRITICAL")
    assert scan.suggestion != ""
    assert "index" in scan.suggestion.lower()
    assert query_cost.risk_level_for(issues) in ("High", "Critical")


def test_full_scan_with_existing_index_candidate_not_recommended_again():
    # possible_keys is populated (MySQL considered an index) even though
    # type=ALL — recommending "add an index" here would be wrong since one
    # already exists as a candidate.
    rows = _full_scan_row("orders", rows=20_000, possible_keys="idx_status", filtered=80)
    issues = query_cost.analyze_explain_rows(
        rows, "SELECT * FROM orders WHERE status != 'archived';")
    scan = next(i for i in issues if i.code == "FULL_TABLE_SCAN")
    assert scan.suggestion == ""


def test_full_scan_join_with_missing_index_on_joined_table_is_flagged():
    rows = [
        {"id": 1, "select_type": "SIMPLE", "table": "o", "type": "ref",
         "possible_keys": "idx_customer", "key": "idx_customer", "Extra": "", "rows": 3,
         "filtered": 100},
        {"id": 1, "select_type": "SIMPLE", "table": "c", "type": "ALL",
         "possible_keys": None, "key": None, "Extra": "Using where", "rows": 500_000,
         "filtered": 10},
    ]
    sql = ("SELECT * FROM orders o JOIN customers c ON o.customer_id = c.id "
           "WHERE c.region = 'EU';")
    issues = query_cost.analyze_explain_rows(rows, sql)
    scan = next(i for i in issues if i.code == "FULL_TABLE_SCAN")
    assert scan.severity in ("HIGH", "CRITICAL")
    assert "index" in scan.suggestion.lower()


def test_filesort_and_temp_table_downgraded_on_tiny_result_sets():
    rows = [{
        "id": 1, "select_type": "SIMPLE", "table": "tags", "type": "ALL",
        "possible_keys": None, "key": None, "Extra": "Using filesort", "rows": 50,
        "filtered": 100,
    }]
    issues = query_cost.analyze_explain_rows(rows, "SELECT * FROM tags ORDER BY name;")
    filesort = next(i for i in issues if i.code == "FILESORT")
    assert filesort.severity in ("INFO", "LOW")
    assert filesort.suggestion == ""


def test_profile_full_scan_of_small_table_is_low_severity_not_critical():
    # Run Profile (EXPLAIN ANALYZE) path — must apply the same contextual
    # verdict as the plan-only path (Estimate Cost), not the old blanket
    # "any table/full scan node = CRITICAL" rule.
    tree_text = ("-> Table scan on audit_logs  (cost=4.05 rows=38) "
                 "(actual time=0.010..0.020 rows=38 loops=1)\n")
    root = query_cost.parse_mysql_analyze_tree(tree_text)
    classic_rows = [{
        "id": 1, "select_type": "SIMPLE", "table": "audit_logs", "type": "ALL",
        "possible_keys": None, "key": None, "Extra": "", "rows": 38, "filtered": 100,
    }]
    query_cost._annotate_tree_from_explain_rows(root, classic_rows)
    issues = query_cost.analyze_mysql_profile(root, "SELECT * FROM audit_logs")
    scan = next(i for i in issues if i.code == "FULL_TABLE_SCAN")
    assert scan.severity in ("INFO", "LOW")
    assert scan.suggestion == ""
    assert query_cost.risk_level_for(issues) == "Low"


def test_score_issues_is_never_larger_than_100():
    issues = [
        query_cost.Issue("CRITICAL", "A", "m", "s"),
        query_cost.Issue("CRITICAL", "B", "m", "s"),
        query_cost.Issue("HIGH", "C", "m", "s"),
    ]
    assert query_cost.score_issues(issues) == 100


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
# Schema-aware checks (issue #250) — schema is a plain dict here (the same
# shape fetch_schema_context() builds from a live db_service), so these
# stay pure unit tests for both dialects without a live DB fixture.
# ===========================================================================

def _schema(table, columns, indexed=(), fks=()):
    return {table.lower(): {
        "columns": {c.lower(): t for c, t in columns.items()},
        "indexed_columns": {c.lower() for c in indexed},
        "fk_columns": {c.lower() for c in fks},
    }}


def test_full_table_scan_suggestion_names_real_column_when_schema_available():
    rows = _full_scan_row("events", rows=250_000, possible_keys=None, filtered=2.5)
    schema = _schema("events", {"id": "int", "status": "varchar(20)"})
    issues = query_cost.analyze_explain_rows(
        rows, "SELECT * FROM events WHERE status = 'pending';", schema=schema)
    scan = next(i for i in issues if i.code == "FULL_TABLE_SCAN")
    assert "`status`" in scan.suggestion
    assert "column(s) used in the WHERE" not in scan.suggestion


def test_type_mismatch_predicate_flagged_for_string_column_vs_numeric_literal():
    schema = _schema("orders", {"id": "int", "customer_code": "varchar(20)"})
    issues = query_cost.analyze_sql_text(
        "SELECT * FROM orders WHERE customer_code = 12345", schema=schema)
    assert any(i.code == "TYPE_MISMATCH_PREDICATE" for i in issues)


def test_type_mismatch_predicate_not_flagged_for_matching_types():
    schema = _schema("orders", {"id": "int", "customer_code": "varchar(20)"})
    issues = query_cost.analyze_sql_text(
        "SELECT * FROM orders WHERE customer_code = 'ABC123'", schema=schema)
    assert not any(i.code == "TYPE_MISMATCH_PREDICATE" for i in issues)


def test_unindexed_join_key_flagged_when_not_indexed_or_fk():
    schema = {
        **_schema("orders", {"id": "int", "customer_id": "int"}, indexed=["id"]),
        **_schema("customers", {"id": "int"}, indexed=["id"]),
    }
    sql = "SELECT * FROM orders o JOIN customers c ON o.customer_id = c.id"
    issues = query_cost.analyze_sql_text(sql, schema=schema)
    join_issues = [i for i in issues if i.code == "UNINDEXED_JOIN_KEY"]
    assert any("customer_id" in i.message for i in join_issues)
    assert not any("`customers`.`id`" in i.message for i in join_issues)


def test_postgres_seq_scan_suggestion_names_real_column_when_schema_available():
    plan = {
        "Node Type": "Seq Scan", "Relation Name": "orders",
        "Plan Rows": 50000, "Actual Rows": 52000, "Actual Loops": 1,
        "Actual Total Time": 12.3, "Plans": [],
    }
    schema = _schema("orders", {"id": "integer", "status": "text"})
    issues = query_cost.analyze_postgres_plan(
        plan, "SELECT * FROM orders WHERE status = 'open'", has_actuals=True, schema=schema)
    scan = next(i for i in issues if i.code == "SEQ_SCAN")
    assert "`status`" in scan.suggestion


# ===========================================================================
# Plan comparison — diff_plan_trees() (#181: multi-operator Plan
# Comparison in Compare Queries). Pure unit tests against hand-built
# ProfileNode trees + Issue lists — no DB needed, dialect-agnostic by
# design (the function only looks at ProfileNode/Issue shapes, not the
# dialect that produced them).
# ===========================================================================

def _node(table, access_type="", children=None):
    return query_cost.ProfileNode(
        node_type=f"scan {table}", table=table, access_type=access_type,
        children=children or [],
    )


def _estimate(tree, issues=None):
    return query_cost.CostEstimate(
        dialect="mysql", issues=issues or [], plan_tree=tree,
    )


def test_diff_plan_trees_empty_when_no_plan_tree():
    est_no_tree = query_cost.CostEstimate(dialect="mysql", plan_tree=None)
    est_with_tree = _estimate(_node("orders"))
    assert query_cost.diff_plan_trees(est_no_tree, est_with_tree) == []
    assert query_cost.diff_plan_trees(est_with_tree, est_no_tree) == []


def test_diff_plan_trees_unchanged_table_is_omitted():
    est1 = _estimate(_node("orders", "ref"))
    est2 = _estimate(_node("orders", "ref"))
    assert query_cost.diff_plan_trees(est1, est2) == []


def test_diff_plan_trees_flags_regression_when_severity_worsens():
    issue = query_cost.Issue("CRITICAL", "FULL_TABLE_SCAN", "Table `orders` is read with a full scan.", "")
    est1 = _estimate(_node("orders", "ref"))
    est2 = _estimate(_node("orders", "ALL"), issues=[issue])

    deltas = query_cost.diff_plan_trees(est1, est2)
    assert len(deltas) == 1
    assert deltas[0].table == "orders"
    assert deltas[0].status == "regression"
    assert deltas[0].access_before == "ref"
    assert deltas[0].access_after == "ALL"


def test_diff_plan_trees_flags_improvement_when_severity_resolves():
    issue = query_cost.Issue("CRITICAL", "FULL_TABLE_SCAN", "Table `orders` is read with a full scan.", "")
    est1 = _estimate(_node("orders", "ALL"), issues=[issue])
    est2 = _estimate(_node("orders", "ref"))

    deltas = query_cost.diff_plan_trees(est1, est2)
    assert len(deltas) == 1
    assert deltas[0].status == "improvement"


def test_diff_plan_trees_flags_neutral_change_when_severity_unchanged():
    est1 = _estimate(_node("orders", "ref"))
    est2 = _estimate(_node("orders", "eq_ref"))

    deltas = query_cost.diff_plan_trees(est1, est2)
    assert len(deltas) == 1
    assert deltas[0].status == "changed"


def test_diff_plan_trees_flags_added_and_removed_tables():
    est1 = _estimate(_node("orders", "ref", children=[_node("users", "eq_ref")]))
    est2 = _estimate(_node("orders", "ref"))

    deltas = query_cost.diff_plan_trees(est1, est2)
    assert len(deltas) == 1
    assert deltas[0].table == "users"
    assert deltas[0].status == "removed"

    deltas_reversed = query_cost.diff_plan_trees(est2, est1)
    assert len(deltas_reversed) == 1
    assert deltas_reversed[0].status == "added"


def test_diff_plan_trees_matches_postgres_style_issue_messages():
    # PostgreSQL's issue messages don't use MySQL's "Table `t`" prefix
    # (e.g. "Sequential scan on `orders` ...") — diff_plan_trees must still
    # match the table via the backtick-quoted name, not a fixed prefix.
    issue = query_cost.Issue("HIGH", "SEQ_SCAN", "Sequential scan on `orders` (~50,000 rows).", "")
    est1 = _estimate(_node("orders", "Index Scan"))
    est2 = _estimate(_node("orders", "Seq Scan"), issues=[issue])

    deltas = query_cost.diff_plan_trees(est1, est2)
    assert len(deltas) == 1
    assert deltas[0].table == "orders"
    assert deltas[0].status == "regression"


def test_diff_plan_trees_query_wide_issue_without_table_is_not_attached():
    # SELECT_STAR (and Postgres's Nested-Loop-count warning) name no
    # table at all — must not be force-matched to an unrelated table.
    issue = query_cost.Issue("LOW", "SELECT_STAR", "SELECT * fetches all columns, including unused ones.", "")
    est1 = _estimate(_node("orders", "ref"))
    est2 = _estimate(_node("orders", "ref"), issues=[issue])

    assert query_cost.diff_plan_trees(est1, est2) == []


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
