"""
query_cost
==========
Dialect-aware query cost estimation (pre-run, plan-only) and query
profiling (post-run, from EXPLAIN ANALYZE) for MySQL and PostgreSQL — all
issued through DbService, so it behaves exactly like every other query the
app runs (same guard, same connection handling).

The Issue/SEVERITY_SCORE/analyze_sql_text/analyze_explain_rows/
score_issues rule engine here is the original MySQL-only implementation
from query_analyzer.py (the standalone offline CLI at the repo root),
lifted so the CLI, the SQL editor's status-bar cost badge, and the
Analyze Query dialog all share one implementation instead of three
divergent copies. query_analyzer.py imports from this module rather than
defining its own copies.
"""

from __future__ import annotations

import json
import re
import textwrap
from dataclasses import dataclass, field

from utils.logger import get_logger

logger = get_logger()

SEVERITY_SCORE = {"CRITICAL": 100, "HIGH": 50, "MEDIUM": 20, "LOW": 5, "INFO": 1}

# Short, human labels for the worst issue found — used to build the
# status-bar badge text ("Cost: 62 · full scan") without repeating a
# whole Issue message inline.
_CODE_LABELS = {
    "FULL_TABLE_SCAN": "full scan",
    "SEQ_SCAN": "sequential scan",
    "NO_POSSIBLE_KEYS": "no index",
    "DEPENDENT_SUBQUERY": "dependent subquery",
    "FILESORT": "filesort",
    "SORT_SPILL": "sort spilled to disk",
    "TEMP_TABLE": "temp table",
    "LOW_SELECTIVITY": "low selectivity",
    "HIGH_ROWS_EXAMINED": "high rows scanned",
    "DUPLICATE_WHEN": "duplicate CASE branch",
    "LIKE_WITHOUT_WILDCARD": "LIKE without wildcard",
    "FUNCTION_ON_COLUMN": "function on column",
    "ALWAYS_TRUE_CONDITION": "always-true condition",
    "SELECT_STAR": "select *",
    "MANY_CORRELATED_SUBQUERIES": "many correlated subqueries",
}


@dataclass
class Issue:
    severity: str          # CRITICAL | HIGH | MEDIUM | LOW | INFO
    code: str               # short machine-readable tag
    message: str            # human-readable explanation
    suggestion: str         # what to do about it


@dataclass
class CostEstimate:
    """Pre-run, plan-only estimate — never executes the user's query."""
    dialect: str
    score: int = 0
    label: str = "Looks efficient"
    issues: list = field(default_factory=list)      # list[Issue]
    plan_rows: list = field(default_factory=list)     # raw per-dialect plan rows
    error: str = ""
    # The dialect's OWN planner cost/row estimate — distinct from `score`
    # (QForge's derived severity score) so the UI can never present the two
    # as the same number. None when it couldn't be read — the UI must show
    # "N/A", never a fabricated value.
    native_cost: float | None = None
    estimated_rows: int | None = None
    plan_tree: object = None                         # ProfileNode | None


@dataclass
class ProfileNode:
    node_type: str
    table: str = ""
    rows_estimated: int = 0
    rows_actual: int = 0
    time_ms: float = 0.0
    loops: int = 1
    cost: float = 0.0
    extra: str = ""
    children: list = field(default_factory=list)      # list[ProfileNode]


@dataclass
class QueryProfile:
    """Post-run profile — EXPLAIN ANALYZE, so this DOES execute the query."""
    dialect: str
    total_time_ms: float = 0.0
    root: object = None                                # ProfileNode | None
    issues: list = field(default_factory=list)         # list[Issue]
    supported: bool = True   # False for a dialect with no profiling equivalent
    error: str = ""


def _int(val) -> int:
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return 0


# ===========================================================================
# Static SQL-text rules — dialect independent
# ===========================================================================

def analyze_sql_text(sql: str) -> list:
    """Rule-based SQL text analysis independent of EXPLAIN."""
    issues: list = []
    upper = sql.upper()

    when_vals = re.findall(r"WHEN\s+'([^']+)'", sql, re.IGNORECASE)
    seen: set = set()
    dups: set = set()
    for v in when_vals:
        if v in seen:
            dups.add(v)
        seen.add(v)
    if dups:
        issues.append(Issue(
            severity="LOW",
            code="DUPLICATE_WHEN",
            message=f"Duplicate WHEN branch values found: {', '.join(sorted(dups))}.",
            suggestion="Remove the duplicate WHEN branches; only the first match is used.",
        ))

    like_exact = re.findall(r"LIKE\s+'([^%_]+)'", sql, re.IGNORECASE)
    if like_exact:
        examples = like_exact[:3]
        issues.append(Issue(
            severity="LOW",
            code="LIKE_WITHOUT_WILDCARD",
            message=f"LIKE used for exact string match (no % or _): "
                    f"{', '.join(repr(e) for e in examples)}{' ...' if len(like_exact) > 3 else ''}.",
            suggestion="Replace LIKE 'exact string' with = 'exact string' to allow index use.",
        ))

    func_patterns = [
        (r"WHERE.*?YEAR\s*\(", "YEAR()"),
        (r"WHERE.*?MONTH\s*\(", "MONTH()"),
        (r"WHERE.*?DATE\s*\(", "DATE()"),
        (r"WHERE.*?LOWER\s*\(", "LOWER()"),
        (r"WHERE.*?UPPER\s*\(", "UPPER()"),
    ]
    for pat, name in func_patterns:
        if re.search(pat, sql, re.IGNORECASE | re.DOTALL):
            issues.append(Issue(
                severity="MEDIUM",
                code="FUNCTION_ON_COLUMN",
                message=f"{name} applied to a column inside WHERE — prevents index use.",
                suggestion="Rewrite to compare against a computed constant range instead "
                           "of applying a function to the column.",
            ))
            break

    if re.search(r"NOT\s+LIKE\s+'.+?'\s+OR\s+.+?NOT\s+LIKE", sql, re.IGNORECASE | re.DOTALL):
        issues.append(Issue(
            severity="HIGH",
            code="ALWAYS_TRUE_CONDITION",
            message="Pattern `NOT LIKE 'X' OR ... NOT LIKE 'Y'` is logically always TRUE "
                    "because no single value can equal both X and Y simultaneously.",
            suggestion="Replace with `column NOT IN ('X', 'Y')` to express the intended logic.",
        ))

    if re.search(r"SELECT\s+\*", upper):
        issues.append(Issue(
            severity="LOW",
            code="SELECT_STAR",
            message="SELECT * fetches all columns, including unused ones.",
            suggestion="List only the columns you need to reduce I/O and network traffic.",
        ))

    corr_count = upper.count("DEPENDENT") + len(re.findall(
        r"SELECT\b.+?FROM\b.+?WHERE\b.+?=\s*\w+\.\w+",
        sql, re.IGNORECASE | re.DOTALL
    ))
    if corr_count > 3:
        issues.append(Issue(
            severity="HIGH",
            code="MANY_CORRELATED_SUBQUERIES",
            message=f"Query contains {corr_count} apparent correlated subqueries in the SELECT list.",
            suggestion="Consolidate correlated subqueries into a single pre-aggregated CTE "
                       "joined back to the main query (one scan instead of N scans).",
        ))

    return issues


# ===========================================================================
# MySQL — classic tabular EXPLAIN (pre-run)
# ===========================================================================

def analyze_explain_rows(rows: list, sql: str) -> list:
    """MySQL classic EXPLAIN — one issue-detection pass per row."""
    issues: list = []
    total_rows_examined = 0

    for row in rows:
        select_type = str(row.get("select_type", "")).upper()
        tbl         = row.get("table", "?")
        typ         = str(row.get("type", "")).lower()
        possible    = row.get("possible_keys")
        used_key    = row.get("key")
        extra       = str(row.get("Extra") or "").lower()
        est_rows    = _int(row.get("rows", 0))
        filtered    = float(row.get("filtered") or 100)
        total_rows_examined += est_rows

        if typ == "all":
            issues.append(Issue(
                severity="CRITICAL",
                code="FULL_TABLE_SCAN",
                message=f"Table `{tbl}` uses a full scan ({est_rows:,} estimated rows). "
                        f"Type=ALL, key={used_key}.",
                suggestion=(
                    f"Add an index on the column(s) used in the WHERE / JOIN condition "
                    f"for `{tbl}`."
                ),
            ))
        elif typ not in ("eq_ref", "ref", "range", "index", "const", "system") \
                and possible is None and tbl != "<derived>":
            issues.append(Issue(
                severity="HIGH",
                code="NO_POSSIBLE_KEYS",
                message=f"Table `{tbl}` has no possible indexes (type={typ}, "
                        f"possible_keys=NULL, rows≈{est_rows:,}).",
                suggestion=f"Inspect the JOIN / WHERE predicates touching `{tbl}` "
                           f"and create a covering index.",
            ))

        if "DEPENDENT" in select_type:
            issues.append(Issue(
                severity="HIGH",
                code="DEPENDENT_SUBQUERY",
                message=f"Select #{row.get('id')} on `{tbl}` is a DEPENDENT_SUBQUERY — "
                        f"runs once per outer row (~{est_rows:,} rows each pass).",
                suggestion="Refactor into a JOIN, LEFT JOIN, or a WITH (CTE) that is "
                           "executed once and then joined back to the main query.",
            ))

        if "filesort" in extra:
            issues.append(Issue(
                severity="MEDIUM",
                code="FILESORT",
                message=f"Table `{tbl}` requires a filesort (ORDER BY cannot use an index).",
                suggestion="Add a composite index that covers the ORDER BY columns "
                           f"(and optionally the WHERE columns) for `{tbl}`.",
            ))

        if "temporary" in extra:
            issues.append(Issue(
                severity="MEDIUM",
                code="TEMP_TABLE",
                message=f"Query creates a temporary table (table=`{tbl}`, select_type={select_type}).",
                suggestion="Rewrite GROUP BY / DISTINCT to avoid temp tables, or ensure "
                           "the GROUP BY columns are indexed.",
            ))

        if est_rows > 10_000 and filtered < 20:
            issues.append(Issue(
                severity="MEDIUM",
                code="LOW_SELECTIVITY",
                message=f"Table `{tbl}`: {est_rows:,} rows estimated, only {filtered:.1f}% "
                        f"pass the filter — {int(est_rows * filtered / 100):,} rows survive.",
                suggestion=f"A more selective index on `{tbl}` can reduce rows examined.",
            ))

    if total_rows_examined > 500_000:
        issues.append(Issue(
            severity="HIGH",
            code="HIGH_ROWS_EXAMINED",
            message=f"Total estimated rows examined across all tables: {total_rows_examined:,}.",
            suggestion="Reduce driving table size with a better index on the primary "
                       "filter column, or use CTEs to pre-filter data.",
        ))

    issues += analyze_sql_text(sql)
    return issues


# ===========================================================================
# MySQL — EXPLAIN ANALYZE tree (post-run)
# ===========================================================================

_MYSQL_NODE_RE = re.compile(
    r'^(?P<indent>\s*)-> (?P<desc>.+?)'
    r'(?:\s*\(cost=(?P<cost>[\d.]+) rows=(?P<est_rows>[\d.]+)\))?'
    r'(?:\s*\(actual time=(?P<t0>[\d.]+)\.\.(?P<t1>[\d.]+) rows=(?P<act_rows>[\d.]+) loops=(?P<loops>[\d.]+)\))?'
    r'\s*$'
)
_MYSQL_INDENT_WIDTH = 4


def _mysql_table_from_desc(desc: str) -> str:
    m = re.search(r"\bon\s+(?:`([^`]+)`|(\w+))\b", desc)
    if m:
        return m.group(1) or m.group(2)
    return ""


def parse_mysql_analyze_tree(tree_text: str) -> "ProfileNode | None":
    """Parse MySQL's indented `-> node (cost=..) (actual time=..)` tree
    (EXPLAIN ANALYZE / EXPLAIN FORMAT=TREE) into a ProfileNode tree."""
    root = None
    stack: list[tuple[int, ProfileNode]] = []   # (level, node)

    for line in tree_text.splitlines():
        if not line.strip():
            continue
        m = _MYSQL_NODE_RE.match(line)
        if not m:
            continue
        level = len(m.group("indent")) // _MYSQL_INDENT_WIDTH
        desc = m.group("desc").strip()
        node = ProfileNode(
            node_type=desc,
            table=_mysql_table_from_desc(desc),
            rows_estimated=_int(m.group("est_rows")),
            rows_actual=_int(m.group("act_rows")),
            time_ms=float(m.group("t1")) if m.group("t1") else 0.0,
            loops=_int(m.group("loops")) or 1,
            cost=float(m.group("cost")) if m.group("cost") else 0.0,
        )

        while stack and stack[-1][0] >= level:
            stack.pop()
        if stack:
            stack[-1][1].children.append(node)
        else:
            root = node
        stack.append((level, node))

    return root


def _walk_mysql_profile(node: "ProfileNode", issues: list) -> None:
    desc_lower = node.node_type.lower()
    if "table scan" in desc_lower or "full scan" in desc_lower:
        issues.append(Issue(
            severity="CRITICAL",
            code="FULL_TABLE_SCAN",
            message=f"Full scan of `{node.table or '?'}` — {node.rows_actual:,} actual "
                    f"rows over {node.loops} loop(s).",
            suggestion=f"Add an index on the column(s) filtering `{node.table or 'this table'}`.",
        ))
    if node.rows_estimated and node.rows_actual > node.rows_estimated * 10 and node.rows_actual > 1000:
        issues.append(Issue(
            severity="LOW",
            code="ESTIMATE_MISMATCH",
            message=f"Planner estimated {node.rows_estimated:,} rows for "
                    f"`{node.table or node.node_type}` but actually produced "
                    f"{node.rows_actual:,} — statistics may be stale.",
            suggestion=f"Run `ANALYZE TABLE {node.table}`." if node.table else
                       "Refresh table statistics.",
        ))
    for child in node.children:
        _walk_mysql_profile(child, issues)


def analyze_mysql_profile(root: "ProfileNode | None", sql: str) -> list:
    issues: list = []
    if root:
        _walk_mysql_profile(root, issues)
    issues += analyze_sql_text(sql)
    return issues


# ===========================================================================
# PostgreSQL — EXPLAIN (FORMAT JSON) / EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
# ===========================================================================

_PG_BIG_ROWS = 10_000


def _pg_node_to_profile(node: dict) -> "ProfileNode":
    p = ProfileNode(
        node_type=node.get("Node Type", "?"),
        table=node.get("Relation Name", "") or node.get("Alias", ""),
        rows_estimated=_int(node.get("Plan Rows", 0)),
        rows_actual=_int(node.get("Actual Rows", 0)),
        time_ms=float(node.get("Actual Total Time", 0.0) or 0.0),
        loops=_int(node.get("Actual Loops", 1)) or 1,
        cost=float(node.get("Total Cost", 0.0) or 0.0),
        extra=node.get("Sort Method", "") or "",
    )
    for child in node.get("Plans", []) or []:
        p.children.append(_pg_node_to_profile(child))
    return p


def _walk_postgres_plan(node: dict, issues: list, has_actuals: bool) -> None:
    node_type = node.get("Node Type", "")
    tbl = node.get("Relation Name", "") or node.get("Alias", "?")
    plan_rows = _int(node.get("Plan Rows", 0))
    actual_rows = _int(node.get("Actual Rows", 0)) if has_actuals else plan_rows

    if node_type == "Seq Scan" and (actual_rows if has_actuals else plan_rows) > _PG_BIG_ROWS:
        issues.append(Issue(
            severity="CRITICAL" if (actual_rows if has_actuals else plan_rows) > 100_000 else "HIGH",
            code="SEQ_SCAN",
            message=f"Sequential scan on `{tbl}` "
                    f"(~{(actual_rows if has_actuals else plan_rows):,} rows).",
            suggestion=f"Add an index on the column(s) used to filter or join `{tbl}`.",
        ))

    sort_method = node.get("Sort Method", "")
    if sort_method and "external" in sort_method.lower():
        issues.append(Issue(
            severity="MEDIUM",
            code="SORT_SPILL",
            message=f"Sort spilled to disk ({sort_method}) — not enough work_mem to sort "
                    f"in memory.",
            suggestion="Increase `work_mem` for this session, or add an index that "
                       "avoids the sort entirely.",
        ))

    if has_actuals and plan_rows and actual_rows > plan_rows * 10 and actual_rows > 1000:
        issues.append(Issue(
            severity="LOW",
            code="ESTIMATE_MISMATCH",
            message=f"Planner estimated {plan_rows:,} rows for `{tbl}` but actually "
                    f"produced {actual_rows:,} — statistics may be stale.",
            suggestion=f"Run `ANALYZE {tbl}`." if tbl != "?" else "Refresh table statistics.",
        ))

    if node_type == "Nested Loop" and has_actuals:
        loops = _int(node.get("Actual Loops", 1)) or 1
        if loops > 1000:
            issues.append(Issue(
                severity="HIGH",
                code="DEPENDENT_SUBQUERY",
                message=f"Nested loop executed {loops:,} times — the inner side runs "
                        f"once per outer row.",
                suggestion="Consider a hash or merge join by adding an index on the "
                           "join column, or rewriting as a CTE.",
            ))

    for child in node.get("Plans", []) or []:
        _walk_postgres_plan(child, issues, has_actuals)


def analyze_postgres_plan(plan_root: dict, sql: str, has_actuals: bool = False) -> list:
    issues: list = []
    if plan_root:
        _walk_postgres_plan(plan_root, issues, has_actuals)
    issues += analyze_sql_text(sql)
    return issues


# ===========================================================================
# Scoring / labeling
# ===========================================================================

def score_issues(issues: list) -> int:
    return sum(SEVERITY_SCORE[i.severity] for i in issues)


def label_for(issues: list) -> str:
    if not issues:
        return "Looks efficient"
    worst = max(issues, key=lambda i: SEVERITY_SCORE[i.severity])
    return _CODE_LABELS.get(worst.code, worst.severity.title())


_RISK_FROM_SEVERITY = {
    "CRITICAL": "Critical", "HIGH": "High", "MEDIUM": "Medium",
    "LOW": "Low", "INFO": "Low",
}


def risk_level_for(issues: list) -> str:
    """Low / Medium / High / Critical — driven by the worst severity the
    rule engine actually found (not a raw score cutoff), so it stays
    consistent with the Issues list rather than a second, disconnected
    classification."""
    if not issues:
        return "Low"
    worst = max(issues, key=lambda i: SEVERITY_SCORE[i.severity])
    return _RISK_FROM_SEVERITY[worst.severity]


# ===========================================================================
# Dialect EXPLAIN runners — take a live DbService, never mutate data except
# where noted (EXPLAIN ANALYZE / ANALYZE-format execute the query for real).
# ===========================================================================

def _clean(sql: str) -> str:
    return sql.rstrip().rstrip(";").rstrip()


def estimate_cost(db_service, sql: str) -> CostEstimate:
    """Plan-only pre-run estimate. Never executes *sql* itself."""
    dialect = getattr(db_service, "db_type", "") or ""
    clean = _clean(sql)

    try:
        if dialect == "mysql":
            df = db_service.execute_query(f"EXPLAIN {clean}")
            rows = df.to_dict("records")
            issues = analyze_explain_rows(rows, sql)

            # Best-effort: EXPLAIN FORMAT=TREE (MySQL 8.0.16+) gives the same
            # node tree as EXPLAIN ANALYZE minus the actual-time figures —
            # still plan-only, still never executes the query — so it's the
            # one source for MySQL's own cumulative query_cost and a real
            # execution-plan tree without waiting for a post-run profile.
            # Older servers raise here; that's fine, we just fall back to
            # tableless cost/tree (None) rather than failing the estimate.
            plan_tree = None
            try:
                tree_df = db_service.execute_query(f"EXPLAIN FORMAT=TREE {clean}")
                tree_text = "\n".join(str(v) for v in tree_df.iloc[:, 0].tolist())
                plan_tree = parse_mysql_analyze_tree(tree_text)
            except Exception as tree_ex:
                logger.debug(f"query_cost: EXPLAIN FORMAT=TREE unavailable: {tree_ex}")

            native_cost = plan_tree.cost if plan_tree and plan_tree.cost else None
            estimated_rows = (
                plan_tree.rows_estimated if plan_tree and plan_tree.rows_estimated
                else (_int(rows[0].get("rows")) if rows else None)
            )
            return CostEstimate(
                dialect, score_issues(issues), label_for(issues), issues, rows,
                native_cost=native_cost, estimated_rows=estimated_rows, plan_tree=plan_tree,
            )

        elif dialect == "postgresql":
            df = db_service.execute_query(f"EXPLAIN (FORMAT JSON) {clean}")
            raw = df.iloc[0, 0]
            plan_list = raw if isinstance(raw, list) else json.loads(raw)
            plan_root = plan_list[0]["Plan"]
            issues = analyze_postgres_plan(plan_root, sql, has_actuals=False)
            native_cost = float(plan_root["Total Cost"]) if "Total Cost" in plan_root else None
            estimated_rows = _int(plan_root.get("Plan Rows")) if "Plan Rows" in plan_root else None
            return CostEstimate(
                dialect, score_issues(issues), label_for(issues), issues, plan_list,
                native_cost=native_cost, estimated_rows=estimated_rows,
                plan_tree=_pg_node_to_profile(plan_root),
            )

        else:
            return CostEstimate(dialect, error=f"Unsupported dialect: {dialect}")

    except Exception as ex:
        return CostEstimate(dialect, error=str(ex))


def build_profile(db_service, sql: str) -> QueryProfile:
    """Post-run profile. Executes *sql* for real via EXPLAIN ANALYZE /
    EXPLAIN (ANALYZE, ...) — never call this automatically on every run."""
    dialect = getattr(db_service, "db_type", "") or ""
    clean = _clean(sql)

    try:
        if dialect == "mysql":
            df = db_service.execute_query(f"EXPLAIN ANALYZE {clean}")
            tree_text = "\n".join(str(v) for v in df.iloc[:, 0].tolist())
            root = parse_mysql_analyze_tree(tree_text)
            issues = analyze_mysql_profile(root, sql)
            total_ms = root.time_ms if root else 0.0
            return QueryProfile(dialect, total_ms, root, issues)

        elif dialect == "postgresql":
            df = db_service.execute_query(
                f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {clean}")
            raw = df.iloc[0, 0]
            plan_list = raw if isinstance(raw, list) else json.loads(raw)
            plan_root_raw = plan_list[0]["Plan"]
            root = _pg_node_to_profile(plan_root_raw)
            issues = analyze_postgres_plan(plan_root_raw, sql, has_actuals=True)
            total_ms = float(plan_list[0].get("Execution Time", root.time_ms) or root.time_ms)
            return QueryProfile(dialect, total_ms, root, issues)

        else:
            return QueryProfile(dialect, supported=False, error=f"Unsupported dialect: {dialect}")

    except Exception as ex:
        return QueryProfile(dialect, error=str(ex))


# ===========================================================================
# Report rendering helpers — shared by query_analyzer.py's HTML report and
# any future text-based rendering. Pure formatting, no DB/UI dependency.
# ===========================================================================

def render_issue_text(issue: Issue, width: int = 70) -> list:
    lines = [f"[{issue.severity}]  {issue.code}"]
    lines += [f"    {p}" for p in textwrap.wrap(issue.message, width)]
    lines.append("  → Fix:")
    lines += [f"    {p}" for p in textwrap.wrap(issue.suggestion, width - 2)]
    return lines
