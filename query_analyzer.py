#!/usr/bin/env python3
"""
query_analyzer.py
=================
Connects to a MySQL database from connections.json, runs EXPLAIN + EXPLAIN ANALYZE
on every .sql file in a queries/ folder, scores each query for performance issues,
writes an HTML report, and writes optimized .sql files to optimized_queries/.

Usage:
    python query_analyzer.py                         # interactive connection picker
    python query_analyzer.py --conn "Local CIM"      # specify connection by name
    python query_analyzer.py --conn "Local CIM"
        --queries ./queries
        --out ./optimized_queries
        --db MY_DATABASE
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import textwrap
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Optional SSH tunnel — same logic as db_service.py
# ---------------------------------------------------------------------------
try:
    from sshtunnel import SSHTunnelForwarder
    HAS_SSH = True
except ImportError:
    HAS_SSH = False

try:
    import pymysql
    import pymysql.cursors
    HAS_MYSQL = True
except ImportError:
    HAS_MYSQL = False


# ===========================================================================
# Data structures
# ===========================================================================

@dataclass
class Issue:
    severity: str          # CRITICAL | HIGH | MEDIUM | LOW | INFO
    code: str              # short machine-readable tag
    message: str           # human-readable explanation
    suggestion: str        # what to do about it


SEVERITY_SCORE = {"CRITICAL": 100, "HIGH": 50, "MEDIUM": 20, "LOW": 5, "INFO": 1}


@dataclass
class QueryResult:
    name: str
    sql: str
    explain_rows: list[dict]        = field(default_factory=list)
    analyze_tree: str               = field(default_factory=str)
    issues: list[Issue]             = field(default_factory=list)
    score: int                      = 0
    actual_time_ms: float           = 0.0
    error: str                      = ""
    optimized_sql: str              = ""


# ===========================================================================
# Connection helpers (mirrors db_service.py, standalone)
# ===========================================================================

def load_connections(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _resolve_password(section: dict, connection_id: str, kind: str) -> str:
    """Return section["password"] if set, else fall back to the OS keychain
    (for connections saved by the QForge GUI, which no longer persists
    passwords to connections.json in plaintext)."""
    pw = section.get("password", "")
    if pw or not connection_id:
        return pw
    from utils.credential_store import get_password
    return get_password(connection_id, kind)


def pick_connection(connections: list[dict], name: str | None) -> dict:
    if name:
        for c in connections:
            if c["name"].lower() == name.lower():
                return c
        raise SystemExit(f"Connection '{name}' not found in connections.json")

    print("\nAvailable connections:")
    for i, c in enumerate(connections):
        print(f"  [{i}] {c['name']}  ({c.get('type','mysql').upper()}  {c.get('host','')}:{c.get('port','')})")
    idx = int(input("\nSelect connection index: ").strip())
    return connections[idx]


def open_connection(config: dict, db_override: str | None = None):
    """Return a live pymysql connection (with SSH tunnel if configured)."""
    if not HAS_MYSQL:
        raise SystemExit("pymysql is not installed. Run: pip install pymysql")

    ssh_tunnel = None
    host = config["host"]
    port = int(config.get("port", 3306))

    ssh_cfg = config.get("ssh_tunnel", {})
    if ssh_cfg.get("enabled", False):
        if not HAS_SSH:
            raise SystemExit("sshtunnel is not installed. Run: pip install sshtunnel")
        ssh_key = os.path.expanduser(ssh_cfg.get("key_path", ""))
        kwargs: dict[str, Any] = dict(
            ssh_username=ssh_cfg["user"],
            remote_bind_address=(host, port),
        )
        if ssh_cfg.get("use_key") and ssh_key:
            kwargs["ssh_private_key"] = ssh_key
        else:
            kwargs["ssh_password"] = _resolve_password(ssh_cfg, config.get("id", ""), "ssh")

        ssh_tunnel = SSHTunnelForwarder(
            (ssh_cfg["host"], int(ssh_cfg.get("port", 22))), **kwargs
        )
        ssh_tunnel.start()
        host, port = "127.0.0.1", ssh_tunnel.local_bind_port
        print(f"  SSH tunnel: localhost:{port} → {config['host']}:{config['port']}")

    database = db_override or config.get("database") or ""
    params: dict[str, Any] = dict(
        host=host,
        port=port,
        user=config["user"],
        password=_resolve_password(config, config.get("id", ""), "db"),
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
        read_timeout=300,
        write_timeout=300,
        connect_timeout=30,
    )
    if database:
        params["database"] = database

    conn = pymysql.connect(**params)
    # bump session timeouts
    with conn.cursor() as cur:
        cur.execute(
            "SET SESSION net_read_timeout=300, net_write_timeout=300, "
            "wait_timeout=28800, interactive_timeout=28800"
        )
    return conn, ssh_tunnel


# ===========================================================================
# EXPLAIN helpers
# ===========================================================================

def run_explain(conn, sql: str) -> list[dict]:
    """Run classical EXPLAIN and return rows as list of dicts."""
    with conn.cursor() as cur:
        cur.execute(f"EXPLAIN {sql}")
        return cur.fetchall()


def run_explain_analyze(conn, sql: str) -> tuple[str, float]:
    """
    Run EXPLAIN ANALYZE (MySQL 8.0.18+) and return the tree string +
    total actual time in ms.  Falls back to empty string on older servers.
    """
    try:
        with conn.cursor() as cur:
            cur.execute(f"EXPLAIN ANALYZE {sql}")
            rows = cur.fetchall()
            # MySQL returns a single column 'EXPLAIN' with the whole tree
            tree = ""
            for row in rows:
                val = list(row.values())[0]
                tree += str(val) + "\n"

            # Extract total actual time from last top-level node
            # Pattern: "actual time=X..Y" – take the last Y value
            times = re.findall(r"actual time=[\d.e+-]+\.\.([\d.e+-]+)", tree)
            total_ms = float(times[-1]) if times else 0.0
            return tree, total_ms
    except Exception:
        return "", 0.0


# ===========================================================================
# Issue detection
# ===========================================================================

def _int(val) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0


def analyze_explain_rows(rows: list[dict], sql: str) -> list[Issue]:
    issues: list[Issue] = []
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

        # ---- Full table scan ------------------------------------------------
        if typ == "all":
            issues.append(Issue(
                severity="CRITICAL",
                code="FULL_TABLE_SCAN",
                message=f"Table `{tbl}` uses a full scan ({est_rows:,} estimated rows). "
                        f"Type=ALL, key={used_key}.",
                suggestion=(
                    f"Add an index on the column(s) used in the WHERE / JOIN condition "
                    f"for `{tbl}`.  If this is the driving table filtered by a status "
                    f"column, e.g. `ALTER TABLE {tbl} ADD INDEX idx_status (loan_status_id);`"
                ),
            ))

        # ---- No index available ---------------------------------------------
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

        # ---- Correlated / dependent subquery --------------------------------
        if "DEPENDENT" in select_type:
            issues.append(Issue(
                severity="HIGH",
                code="DEPENDENT_SUBQUERY",
                message=f"Select #{row.get('id')} on `{tbl}` is a DEPENDENT_SUBQUERY — "
                        f"runs once per outer row (~{est_rows:,} rows each pass).",
                suggestion="Refactor into a JOIN, LEFT JOIN, or a WITH (CTE) that is "
                           "executed once and then joined back to the main query.",
            ))

        # ---- Filesort -------------------------------------------------------
        if "filesort" in extra:
            issues.append(Issue(
                severity="MEDIUM",
                code="FILESORT",
                message=f"Table `{tbl}` requires a filesort (ORDER BY cannot use an index).",
                suggestion="Add a composite index that covers the ORDER BY columns "
                           "(and optionally the WHERE columns) for `{tbl}`.",
            ))

        # ---- Temporary table ------------------------------------------------
        if "temporary" in extra:
            issues.append(Issue(
                severity="MEDIUM",
                code="TEMP_TABLE",
                message=f"Query creates a temporary table (table=`{tbl}`, select_type={select_type}).",
                suggestion="Rewrite GROUP BY / DISTINCT to avoid temp tables, or ensure "
                           "the GROUP BY columns are indexed.",
            ))

        # ---- Low filtered % with many rows ----------------------------------
        if est_rows > 10_000 and filtered < 20:
            issues.append(Issue(
                severity="MEDIUM",
                code="LOW_SELECTIVITY",
                message=f"Table `{tbl}`: {est_rows:,} rows estimated, only {filtered:.1f}% "
                        f"pass the filter — {int(est_rows * filtered / 100):,} rows survive.",
                suggestion=f"A more selective index on `{tbl}` can reduce rows examined.",
            ))

    # ---- Total rows examined ------------------------------------------------
    if total_rows_examined > 500_000:
        issues.append(Issue(
            severity="HIGH",
            code="HIGH_ROWS_EXAMINED",
            message=f"Total estimated rows examined across all tables: {total_rows_examined:,}.",
            suggestion="Reduce driving table size with a better index on the primary "
                       "filter column, or use CTEs to pre-filter data.",
        ))

    # ---- Static SQL analysis ------------------------------------------------
    issues += analyze_sql_text(sql)

    return issues


def analyze_sql_text(sql: str) -> list[Issue]:
    """Rule-based SQL text analysis independent of EXPLAIN."""
    issues: list[Issue] = []
    upper = sql.upper()

    # --- Duplicate WHEN branches in CASE -------------------------------------
    when_vals = re.findall(r"WHEN\s+'([^']+)'", sql, re.IGNORECASE)
    seen: set[str] = set()
    dups: set[str] = set()
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

    # --- LIKE without wildcards (should be =) --------------------------------
    like_exact = re.findall(r"LIKE\s+'([^%_]+)'", sql, re.IGNORECASE)
    if like_exact:
        examples = like_exact[:3]
        issues.append(Issue(
            severity="LOW",
            code="LIKE_WITHOUT_WILDCARD",
            message=f"LIKE used for exact string match (no % or _): "
                    f"{', '.join(repr(e) for e in examples)}{' ...' if len(like_exact)>3 else ''}.",
            suggestion="Replace LIKE 'exact string' with = 'exact string' to allow index use.",
        ))

    # --- Function on a column in WHERE (index-killer) -----------------------
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
                suggestion=f"Rewrite to compare against a computed constant: e.g. "
                           f"`duedate >= '2026-06-01' AND duedate < '2026-07-01'` "
                           f"instead of `MONTH(duedate) = MONTH(CURDATE())`.",
            ))
            break  # report once

    # --- OR NOT LIKE / NOT LIKE x OR NOT LIKE y (always true) ---------------
    if re.search(r"NOT\s+LIKE\s+'.+?'\s+OR\s+.+?NOT\s+LIKE", sql, re.IGNORECASE | re.DOTALL):
        issues.append(Issue(
            severity="HIGH",
            code="ALWAYS_TRUE_CONDITION",
            message="Pattern `NOT LIKE 'X' OR ... NOT LIKE 'Y'` is logically always TRUE "
                    "because no single value can equal both X and Y simultaneously.",
            suggestion="Replace with `column NOT IN ('X', 'Y')` to express the intended logic.",
        ))

    # --- SELECT * ------------------------------------------------------------
    if re.search(r"SELECT\s+\*", upper):
        issues.append(Issue(
            severity="LOW",
            code="SELECT_STAR",
            message="SELECT * fetches all columns, including unused ones.",
            suggestion="List only the columns you need to reduce I/O and network traffic.",
        ))

    # --- Missing LIMIT on large correlated subqueries -----------------------
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
# Score a query
# ===========================================================================

def score_issues(issues: list[Issue]) -> int:
    return sum(SEVERITY_SCORE[i.severity] for i in issues)


# ===========================================================================
# Optimized SQL generator
# ===========================================================================

INDENT = "    "


def generate_optimized_sql(result: QueryResult) -> str:
    """
    Produce an annotated SQL file with:
    1. A header comment listing every detected issue + suggestion.
    2. Concrete index recommendations as ALTER TABLE statements.
    3. The original SQL preserved (not auto-rewritten — safe for production).
    4. Placeholder CTEs where correlated subqueries were detected.
    """
    lines: list[str] = []

    lines.append("-- " + "=" * 72)
    lines.append(f"-- QUERY ANALYZER REPORT  •  {result.name}")
    lines.append(f"-- Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"-- Issue Score: {result.score}  (higher = more problematic)")
    if result.actual_time_ms:
        lines.append(f"-- Actual Execution Time: {result.actual_time_ms:.1f} ms")
    lines.append("-- " + "=" * 72)
    lines.append("")

    if not result.issues:
        lines.append("-- No issues detected.")
    else:
        lines.append(f"-- {len(result.issues)} issue(s) found:")
        lines.append("")
        for i, issue in enumerate(sorted(result.issues,
                                         key=lambda x: SEVERITY_SCORE[x.severity],
                                         reverse=True), 1):
            lines.append(f"-- [{i}] [{issue.severity}]  {issue.code}")
            for part in textwrap.wrap(issue.message, 70):
                lines.append(f"--     {part}")
            lines.append(f"--   → Fix: ")
            for part in textwrap.wrap(issue.suggestion, 68):
                lines.append(f"--     {part}")
            lines.append("")

    # ---- Index recommendations -----------------------------------------------
    index_issues = [i for i in result.issues if i.code == "FULL_TABLE_SCAN"]
    if index_issues:
        lines.append("-- " + "-" * 72)
        lines.append("-- RECOMMENDED INDEXES (review before applying to production):")
        lines.append("-- " + "-" * 72)
        lines.append("")
        seen_tables: set[str] = set()
        for issue in index_issues:
            # extract table name from message
            m = re.search(r"Table `(\w+)`", issue.message)
            tbl = m.group(1) if m else "UNKNOWN_TABLE"
            if tbl not in seen_tables:
                seen_tables.add(tbl)
                lines.append(f"-- ALTER TABLE {tbl} ADD INDEX idx_<column> (<column>);")
        lines.append("")

    # ---- CTE skeleton for correlated subqueries ------------------------------
    cte_issues = [i for i in result.issues
                  if i.code in ("DEPENDENT_SUBQUERY", "MANY_CORRELATED_SUBQUERIES")]
    if cte_issues:
        lines.append("-- " + "-" * 72)
        lines.append("-- SUGGESTED CTE SKELETON (fill in actual query bodies):")
        lines.append("-- " + "-" * 72)
        lines.append("--")
        lines.append("-- WITH")
        lines.append("-- ")
        lines.append("-- agg_1 AS (")
        lines.append("--     SELECT foreign_key_id,")
        lines.append("--            SUM(amount_col)    AS total_amount,")
        lines.append("--            COUNT(*)           AS cnt")
        lines.append("--     FROM   some_large_table")
        lines.append("--     -- place all correlated WHERE filters as conditional aggregates:")
        lines.append("--     -- SUM(CASE WHEN condition THEN value ELSE 0 END) AS conditional_sum")
        lines.append("--     GROUP BY foreign_key_id")
        lines.append("-- ),")
        lines.append("-- ")
        lines.append("-- /* repeat for each logical group of subqueries */")
        lines.append("-- ")
        lines.append("-- SELECT main.*, agg_1.total_amount")
        lines.append("-- FROM   main_table main")
        lines.append("-- LEFT JOIN agg_1 ON agg_1.foreign_key_id = main.id")
        lines.append("-- WHERE  ...;")
        lines.append("")

    # ---- Original SQL --------------------------------------------------------
    lines.append("-- " + "-" * 72)
    lines.append("-- ORIGINAL QUERY (preserved unchanged — apply fixes above manually):")
    lines.append("-- " + "-" * 72)
    lines.append("")
    lines.append(result.sql.rstrip())
    lines.append("")

    return "\n".join(lines)


# ===========================================================================
# HTML report
# ===========================================================================

SEV_COLOR = {
    "CRITICAL": "#c0392b",
    "HIGH":     "#e67e22",
    "MEDIUM":   "#f39c12",
    "LOW":      "#2980b9",
    "INFO":     "#7f8c8d",
}

def html_escape(s: str) -> str:
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;"))


def render_html_report(results: list[QueryResult], conn_name: str, db_name: str) -> str:
    rows_html = ""
    for r in sorted(results, key=lambda x: x.score, reverse=True):
        sev_cells = ""
        for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"):
            cnt = sum(1 for i in r.issues if i.severity == sev)
            col = SEV_COLOR[sev] if cnt else "#ccc"
            sev_cells += (
                f'<td style="color:{col};font-weight:bold;text-align:center">'
                f'{cnt if cnt else "-"}</td>'
            )

        time_str = f"{r.actual_time_ms:.1f} ms" if r.actual_time_ms else "n/a"

        issue_rows = ""
        for issue in sorted(r.issues, key=lambda x: SEVERITY_SCORE[x.severity], reverse=True):
            col = SEV_COLOR[issue.severity]
            issue_rows += f"""
            <tr>
              <td><span style="color:{col};font-weight:bold">{html_escape(issue.severity)}</span></td>
              <td><code>{html_escape(issue.code)}</code></td>
              <td>{html_escape(issue.message)}</td>
              <td style="color:#27ae60">{html_escape(issue.suggestion)}</td>
            </tr>"""

        explain_rows_html = ""
        if r.explain_rows:
            headers = list(r.explain_rows[0].keys())
            explain_rows_html = "<table class='explain'><thead><tr>"
            for h in headers:
                explain_rows_html += f"<th>{html_escape(str(h))}</th>"
            explain_rows_html += "</tr></thead><tbody>"
            for erow in r.explain_rows:
                explain_rows_html += "<tr>"
                for h in headers:
                    val = str(erow.get(h) or "")
                    style = ""
                    if h == "type" and val.lower() == "all":
                        style = ' style="color:#c0392b;font-weight:bold"'
                    elif h == "select_type" and "DEPENDENT" in val.upper():
                        style = ' style="color:#e67e22;font-weight:bold"'
                    explain_rows_html += f"<td{style}>{html_escape(val)}</td>"
                explain_rows_html += "</tr>"
            explain_rows_html += "</tbody></table>"

        analyze_html = ""
        if r.analyze_tree:
            analyze_html = (
                f"<pre class='analyze'>{html_escape(r.analyze_tree[:8000])}"
                f"{'...(truncated)' if len(r.analyze_tree)>8000 else ''}</pre>"
            )

        error_html = (
            f'<p style="color:red"><strong>Error:</strong> {html_escape(r.error)}</p>'
            if r.error else ""
        )

        rows_html += f"""
<div class="query-card" id="q-{html_escape(r.name)}">
  <div class="query-header">
    <span class="qname">{html_escape(r.name)}</span>
    <span class="score" title="Issue score (higher = worse)">{r.score}</span>
    <span class="time">{html_escape(time_str)}</span>
  </div>
  {error_html}
  <table class="sev-summary">
    <thead><tr><th>CRITICAL</th><th>HIGH</th><th>MEDIUM</th><th>LOW</th><th>INFO</th></tr></thead>
    <tbody><tr>{sev_cells}</tr></tbody>
  </table>

  {'<h4>Issues &amp; Recommendations</h4><table class="issues"><thead><tr><th>Sev</th><th>Code</th><th>Message</th><th>Suggestion</th></tr></thead><tbody>' + issue_rows + '</tbody></table>' if r.issues else '<p style="color:#27ae60">✓ No issues detected.</p>'}

  <details>
    <summary>EXPLAIN output ({len(r.explain_rows)} rows)</summary>
    {explain_rows_html}
  </details>

  {'<details><summary>EXPLAIN ANALYZE tree</summary>' + analyze_html + '</details>' if r.analyze_tree else ''}
</div>
"""

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Query Analyzer Report — {html_escape(conn_name)} / {html_escape(db_name)}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         margin: 0; padding: 20px; background: #f5f5f5; color: #222; }}
  h1   {{ font-size: 1.4rem; }}
  .meta {{ color: #666; font-size: .85rem; margin-bottom: 20px; }}
  .query-card {{ background: #fff; border-radius: 8px; padding: 18px 22px;
                 margin-bottom: 18px; box-shadow: 0 1px 4px rgba(0,0,0,.12); }}
  .query-header {{ display: flex; align-items: center; gap: 14px; margin-bottom: 10px; }}
  .qname  {{ font-weight: 700; font-size: 1.05rem; flex: 1; }}
  .score  {{ background: #2c3e50; color: #fff; border-radius: 4px;
             padding: 2px 10px; font-size: .9rem; font-weight: bold; }}
  .time   {{ color: #555; font-size: .85rem; }}
  table   {{ border-collapse: collapse; width: 100%; font-size: .82rem; margin: 8px 0; }}
  th, td  {{ border: 1px solid #ddd; padding: 5px 8px; text-align: left; }}
  th      {{ background: #f0f0f0; }}
  .sev-summary {{ width: auto; margin-bottom: 10px; }}
  .sev-summary th, .sev-summary td {{ padding: 3px 14px; }}
  .issues td {{ vertical-align: top; }}
  .issues td:last-child {{ color: #27ae60; }}
  .explain {{ font-size: .75rem; }}
  pre.analyze {{ background: #1e1e1e; color: #d4d4d4; padding: 12px;
                 border-radius: 6px; font-size: .72rem; overflow-x: auto;
                 white-space: pre-wrap; word-break: break-all; max-height: 400px;
                 overflow-y: auto; }}
  details  {{ margin-top: 8px; }}
  summary  {{ cursor: pointer; color: #2980b9; font-size: .85rem; }}
  code     {{ background: #f0f0f0; padding: 1px 4px; border-radius: 3px; }}
</style>
</head>
<body>
<h1>Query Analyzer Report</h1>
<div class="meta">
  Connection: <strong>{html_escape(conn_name)}</strong> &nbsp;|&nbsp;
  Database: <strong>{html_escape(db_name)}</strong> &nbsp;|&nbsp;
  Generated: {html_escape(timestamp)} &nbsp;|&nbsp;
  Queries analyzed: {len(results)}
</div>
{rows_html}
</body>
</html>"""


# ===========================================================================
# Main pipeline
# ===========================================================================

def list_sql_files(folder: str) -> list[Path]:
    p = Path(folder)
    if not p.exists():
        raise SystemExit(f"Queries folder not found: {folder}")
    files = sorted(p.glob("*.sql"))
    if not files:
        raise SystemExit(f"No .sql files found in {folder}")
    return files


def run_analysis(
    conn,
    sql_files: list[Path],
    out_dir: str,
    db_name: str,
) -> list[QueryResult]:
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    results: list[QueryResult] = []
    total = len(sql_files)

    for idx, sql_file in enumerate(sql_files, 1):
        name = sql_file.stem
        print(f"\n[{idx}/{total}] Analyzing: {name}")
        sql = sql_file.read_text(encoding="utf-8").strip()
        result = QueryResult(name=name, sql=sql)

        # --- Run EXPLAIN ---------------------------------------------------
        try:
            result.explain_rows = run_explain(conn, sql)
            print(f"  EXPLAIN: {len(result.explain_rows)} rows")
        except Exception as exc:
            result.error = str(exc)
            print(f"  EXPLAIN failed: {exc}")
            # Still do text-only analysis
            result.issues = analyze_sql_text(sql)
            result.score = score_issues(result.issues)
            result.optimized_sql = generate_optimized_sql(result)
            (out_path / f"{name}.sql").write_text(result.optimized_sql, encoding="utf-8")
            results.append(result)
            continue

        # --- Run EXPLAIN ANALYZE -------------------------------------------
        result.analyze_tree, result.actual_time_ms = run_explain_analyze(conn, sql)
        if result.actual_time_ms:
            print(f"  EXPLAIN ANALYZE: {result.actual_time_ms:.1f} ms")
        else:
            print(f"  EXPLAIN ANALYZE: not available (MySQL < 8.0.18 or error)")

        # --- Detect issues -------------------------------------------------
        result.issues = analyze_explain_rows(result.explain_rows, sql)
        result.score = score_issues(result.issues)

        crit = sum(1 for i in result.issues if i.severity == "CRITICAL")
        high = sum(1 for i in result.issues if i.severity == "HIGH")
        print(f"  Score: {result.score}  |  CRITICAL={crit}  HIGH={high}  "
              f"Total issues={len(result.issues)}")

        # --- Write optimized file ------------------------------------------
        result.optimized_sql = generate_optimized_sql(result)
        out_file = out_path / f"{name}_optimized.sql"
        out_file.write_text(result.optimized_sql, encoding="utf-8")
        print(f"  Optimized file → {out_file}")

        results.append(result)

    return results


def print_summary(results: list[QueryResult]) -> None:
    print("\n" + "=" * 70)
    print("SUMMARY — ranked by issue score (worst first)")
    print("=" * 70)
    print(f"{'Rank':<5} {'Score':<7} {'Time (ms)':<11} {'CRIT':<6} {'HIGH':<6} {'Name'}")
    print("-" * 70)
    for rank, r in enumerate(
        sorted(results, key=lambda x: x.score, reverse=True), 1
    ):
        crit = sum(1 for i in r.issues if i.severity == "CRITICAL")
        high = sum(1 for i in r.issues if i.severity == "HIGH")
        t    = f"{r.actual_time_ms:.1f}" if r.actual_time_ms else "n/a"
        flag = " ◄ WORST" if rank == 1 else ""
        print(f"{rank:<5} {r.score:<7} {t:<11} {crit:<6} {high:<6} {r.name}{flag}")
    print("=" * 70)


# ===========================================================================
# Entry point
# ===========================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Analyze SQL query files against a live MySQL database."
    )
    parser.add_argument("--conn",    default=None,               help="Connection name from connections.json")
    parser.add_argument("--queries", default="./queries",        help="Folder containing .sql files (default: ./queries)")
    parser.add_argument("--out",     default="./optimized_queries", help="Output folder for optimized .sql files")
    parser.add_argument("--db",      default=None,               help="Override database/schema name")
    parser.add_argument("--report",  default="./query_report.html", help="Path for HTML report (default: ./query_report.html)")
    parser.add_argument("--connections", default="./connections.json", help="Path to connections.json")
    args = parser.parse_args()

    # ---- Load connections ---------------------------------------------------
    if not os.path.exists(args.connections):
        raise SystemExit(f"connections.json not found at: {args.connections}")

    connections = load_connections(args.connections)
    config = pick_connection(connections, args.conn)
    db_name = args.db or config.get("database") or "(no database)"

    print(f"\nConnecting to: {config['name']}  [{config.get('type','mysql').upper()}]"
          f"  {config['host']}:{config.get('port',3306)}")

    conn, ssh_tunnel = open_connection(config, args.db)
    print("Connected.\n")

    # ---- List SQL files ----------------------------------------------------
    sql_files = list_sql_files(args.queries)
    print(f"Found {len(sql_files)} SQL file(s) in {args.queries}:")
    for f in sql_files:
        print(f"  • {f.name}")

    # ---- Run analysis ------------------------------------------------------
    try:
        results = run_analysis(conn, sql_files, args.out, db_name)
    finally:
        conn.close()
        if ssh_tunnel:
            ssh_tunnel.stop()

    # ---- Print summary -----------------------------------------------------
    print_summary(results)

    # ---- Write HTML report -------------------------------------------------
    report_path = Path(args.report)
    html = render_html_report(results, config["name"], db_name)
    report_path.write_text(html, encoding="utf-8")
    print(f"\nHTML report → {report_path.resolve()}")
    print(f"Optimized SQL files → {Path(args.out).resolve()}/")


if __name__ == "__main__":
    main()
