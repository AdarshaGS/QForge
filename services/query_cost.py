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
    "TYPE_MISMATCH_PREDICATE": "type-mismatched predicate",
    "UNINDEXED_JOIN_KEY": "unindexed join key",
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
    # MySQL classic-EXPLAIN detail, attached best-effort by matching this
    # node's table against the tabular EXPLAIN rows (see
    # _annotate_tree_from_explain_rows) — "" / None when EXPLAIN didn't
    # report a value, never fabricated. Not populated for PostgreSQL,
    # whose plan JSON has no equivalent columns.
    access_type: str = ""
    possible_keys: str = ""
    key: str = ""
    filtered: float | None = None


@dataclass
class QueryProfile:
    """Post-run profile — EXPLAIN ANALYZE, so this DOES execute the query."""
    dialect: str
    total_time_ms: float = 0.0
    root: object = None                                # ProfileNode | None
    issues: list = field(default_factory=list)         # list[Issue]
    supported: bool = True   # False for a dialect with no profiling equivalent
    error: str = ""


def estimate_to_dict(estimate: "CostEstimate | None") -> dict | None:
    """Serialize a CostEstimate for persistence in query history — score,
    label, issues (severity/code/message/suggestion), and the dialect's
    own cost/row estimate. Deliberately excludes plan_rows/plan_tree: the
    raw EXPLAIN payload, meaningfully larger per query and cheap to
    regenerate on demand (it's a plan-only EXPLAIN) via a live re-check,
    so there's no reason to carry it in every stored history entry. None
    for a missing or errored estimate — nothing worth persisting."""
    if estimate is None or estimate.error:
        return None
    return {
        "dialect": estimate.dialect,
        "score": estimate.score,
        "label": estimate.label,
        "native_cost": estimate.native_cost,
        "estimated_rows": estimate.estimated_rows,
        "issues": [
            {"severity": i.severity, "code": i.code, "message": i.message, "suggestion": i.suggestion}
            for i in estimate.issues
        ],
    }


def estimate_from_dict(data: "dict | None") -> "CostEstimate | None":
    """Reconstruct a plan-less CostEstimate from estimate_to_dict() output
    — enough to render the Performance Summary / issues list a history
    entry captured at execution time, without a plan_tree (never stored)
    or a live DB round trip. None for missing/empty data."""
    if not data:
        return None
    issues = [Issue(**i) for i in data.get("issues", [])]
    return CostEstimate(
        dialect=data.get("dialect", ""),
        score=data.get("score", 0),
        label=data.get("label", ""),
        issues=issues,
        native_cost=data.get("native_cost"),
        estimated_rows=data.get("estimated_rows"),
    )


def _node_to_dict(node: "ProfileNode") -> dict:
    return {
        "node_type": node.node_type, "table": node.table,
        "rows_estimated": node.rows_estimated, "rows_actual": node.rows_actual,
        "time_ms": node.time_ms, "loops": node.loops, "cost": node.cost,
        "extra": node.extra, "access_type": node.access_type,
        "possible_keys": node.possible_keys, "key": node.key, "filtered": node.filtered,
        "children": [_node_to_dict(c) for c in node.children],
    }


def _node_from_dict(data: dict) -> "ProfileNode":
    return ProfileNode(
        node_type=data.get("node_type", ""), table=data.get("table", ""),
        rows_estimated=data.get("rows_estimated", 0), rows_actual=data.get("rows_actual", 0),
        time_ms=data.get("time_ms", 0.0), loops=data.get("loops") or 1,
        cost=data.get("cost", 0.0), extra=data.get("extra", ""),
        access_type=data.get("access_type", ""), possible_keys=data.get("possible_keys", ""),
        key=data.get("key", ""), filtered=data.get("filtered"),
        children=[_node_from_dict(c) for c in data.get("children", [])],
    )


def profile_to_dict(profile: "QueryProfile | None") -> dict | None:
    """Serialize a QueryProfile for persistence in query history —
    total time, issues, and the full plan tree with actual timings, so a
    stored profile renders identically to a freshly-run one. Meaningfully
    larger than an Estimate's stored form (the plan tree can have dozens
    of nodes), but a profile is only ever stored when the user explicitly
    ran one — never automatically for every query — so that's an
    intentional, opt-in cost, not a per-query default. None for a
    missing, errored, or unsupported-dialect profile."""
    if profile is None or profile.error or not profile.supported:
        return None
    return {
        "dialect": profile.dialect,
        "total_time_ms": profile.total_time_ms,
        "issues": [
            {"severity": i.severity, "code": i.code, "message": i.message, "suggestion": i.suggestion}
            for i in profile.issues
        ],
        "root": _node_to_dict(profile.root) if profile.root else None,
    }


def profile_from_dict(data: "dict | None") -> "QueryProfile | None":
    """Reconstruct a QueryProfile from profile_to_dict() output. None for
    missing/empty data."""
    if not data:
        return None
    issues = [Issue(**i) for i in data.get("issues", [])]
    root = _node_from_dict(data["root"]) if data.get("root") else None
    return QueryProfile(
        dialect=data.get("dialect", ""),
        total_time_ms=data.get("total_time_ms", 0.0),
        root=root, issues=issues, supported=True,
    )


def _int(val) -> int:
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return 0


def _is_missing(val) -> bool:
    """True for SQL NULL either way it can arrive here — `None` from a
    DB-API cursor, or NaN from a pandas DataFrame built over the same
    result (NaN/NaT are the only values in Python that don't equal
    themselves, which is what makes this check work without importing
    pandas here)."""
    return val is None or val != val


# ===========================================================================
# Table-size-aware severity for scan-type issues — the core fix behind
# "a full scan is not automatically a serious problem": a full scan is
# judged by how much work it actually does (rows) and whether the query
# even had a condition an index could have served, not by the access type
# alone.
# ===========================================================================

_TINY_TABLE_ROWS = 1_000        # scanning this many rows is inherently cheap
_MEDIUM_TABLE_ROWS = 50_000
_LARGE_TABLE_ROWS = 1_000_000


def _rows_tier(est_rows: int) -> str:
    if est_rows <= _TINY_TABLE_ROWS:
        return "tiny"
    if est_rows <= _MEDIUM_TABLE_ROWS:
        return "medium"
    if est_rows <= _LARGE_TABLE_ROWS:
        return "large"
    return "huge"


def _has_predicate_on_table(sql: str, table: str) -> bool:
    """Best-effort static check: does the query reference `table` (its
    name or, more commonly, its alias — EXPLAIN's `table` column reports
    whichever the query used) inside a WHERE clause or a JOIN's ON
    condition? This only gates whether an index recommendation is even
    plausible — a false negative just skips a recommendation, it can
    never manufacture one that isn't backed by EXPLAIN evidence too.

    Deliberately conservative: no WHERE and no JOIN anywhere in the query
    means there is nothing that could be filtering any table, full stop.
    """
    if not table or table.startswith("<"):
        return False

    upper_sql = sql.upper()
    has_where = "WHERE" in upper_sql
    has_join = "JOIN" in upper_sql
    if not has_where and not has_join:
        return False

    if has_where and not has_join:
        # Single-table query: a WHERE condition applies to this table even
        # when written without a table qualifier (e.g. "WHERE status = 'x'"
        # rather than "WHERE t.status = 'x'"), which is the common case
        # when there's only one table to be ambiguous about.
        return True

    flat_sql = sql.replace("`", " ").replace('"', " ")
    where_m = re.search(
        r"\bWHERE\b(.*?)(?:\bGROUP\s+BY\b|\bORDER\s+BY\b|\bLIMIT\b|\bHAVING\b|$)",
        flat_sql, re.IGNORECASE | re.DOTALL,
    )
    where_clause = where_m.group(1) if where_m else ""
    on_clauses = re.findall(
        r"\bON\b(.*?)(?=\bJOIN\b|\bWHERE\b|\bGROUP\s+BY\b|\bORDER\s+BY\b|\bLIMIT\b|$)",
        flat_sql, re.IGNORECASE | re.DOTALL,
    )
    haystack = where_clause + " " + " ".join(on_clauses)
    return bool(re.search(rf"\b{re.escape(table)}\b", haystack, re.IGNORECASE))


def _scan_verdict(est_rows: int, has_predicate: bool, has_index_candidate: bool) -> tuple:
    """Decide (severity, recommend_index) for a full/inefficient table
    scan given the actual evidence available from EXPLAIN — table size,
    whether the query has a condition on this table at all, and whether
    MySQL itself found a candidate index for that condition.

    Returns (severity, recommend_index, reason) where `reason` is a short
    clause explaining the verdict for the Issue message."""
    tier = _rows_tier(est_rows)

    if tier == "tiny":
        return ("INFO", False,
                "the table is small enough that a full scan is efficient — "
                "no index would meaningfully help")

    if not has_predicate:
        # Nothing to index against: every row must be read regardless.
        sev = "LOW" if tier in ("medium", "large") else "MEDIUM"
        return (sev, False,
                "the query has no WHERE/JOIN condition on this table, so "
                "reading every row is the only possible plan")

    if has_index_candidate:
        # MySQL considered an index for this predicate and still chose a
        # scan — usually correct (the filter isn't selective enough to be
        # worth an index lookup). Only worth a closer look on huge tables.
        sev = "MEDIUM" if tier == "huge" else "LOW"
        return (sev, False,
                "MySQL considered an existing index for this condition and "
                "still chose a full scan — often correct when most rows "
                "match the filter; if this table has grown recently, "
                "stale statistics are also worth ruling out")

    # Real predicate, EXPLAIN reports no candidate index at all: this is
    # the one case an index recommendation is actually justified.
    sev = {"medium": "MEDIUM", "large": "HIGH", "huge": "CRITICAL"}[tier]
    return (sev, True,
            "no index covers the WHERE/JOIN condition on this table "
            "(possible_keys is empty)")


# ===========================================================================
# Schema-aware checks — optional. Every function below degrades to "find
# nothing" (never raises, never fabricates) when schema is unavailable, so
# a caller with no db_service (the standalone query_analyzer.py CLI) or a
# connection that fails to introspect keeps today's schema-blind behavior.
# ===========================================================================

_ALIAS_STOPWORDS = {
    "ON", "WHERE", "INNER", "LEFT", "RIGHT", "OUTER", "FULL", "CROSS",
    "JOIN", "USING", "GROUP", "ORDER", "HAVING", "LIMIT", "SET", "VALUES",
    "AS", "NATURAL", "STRAIGHT_JOIN", "LATERAL",
}

_TABLE_REF_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+(\w+)(?:\s+(?:AS\s+)?(\w+))?", re.IGNORECASE)


def _parse_table_aliases(sql: str) -> dict:
    """Best-effort {alias_or_table_lower: real_table_name} map built from
    FROM/JOIN clauses (a table with no alias maps to itself). Conservative
    — a shape this regex doesn't recognize is simply absent from the map,
    never guessed."""
    aliases: dict = {}
    flat = sql.replace("`", " ").replace('"', " ")
    for m in _TABLE_REF_RE.finditer(flat):
        table, alias = m.group(1), m.group(2)
        aliases[table.lower()] = table
        if alias and alias.upper() not in _ALIAS_STOPWORDS:
            aliases[alias.lower()] = table
    return aliases


def fetch_schema_context(db_service, tables) -> "dict | None":
    """Best-effort {table_lower: {"columns": {col_lower: type_str},
    "indexed_columns": {col_lower, ...}, "fk_columns": {col_lower, ...}}}
    for *tables* (real table names, not aliases) via db_service's cached
    schema calls (get_columns/get_indexes/get_foreign_keys — issue #256's
    per-connection cache, so repeated calls across rows/queries are cheap).

    None if db_service is falsy or nothing could be fetched (e.g. every
    name is a CTE/derived-table alias that SHOW COLUMNS rejects) — callers
    must treat that the same as "no schema available", never raise."""
    if not db_service or not tables:
        return None
    context: dict = {}
    for table in tables:
        if not table or str(table).startswith("<"):
            continue
        try:
            columns = {}
            for c in db_service.get_columns(table) or []:
                name = c.get("Field") if hasattr(c, "get") else None
                typ = c.get("Type") if hasattr(c, "get") else None
                if name:
                    columns[str(name).lower()] = str(typ or "").lower()
            if not columns:
                continue  # not a real, introspectable table — skip silently

            indexed_columns = set()
            for idx in db_service.get_indexes(table) or []:
                for col in str(idx.get("columns", "")).split(","):
                    col = col.strip().lower()
                    if col:
                        indexed_columns.add(col)

            fk_columns = {
                str(fk["column"]).lower()
                for fk in (db_service.get_foreign_keys(table) or [])
                if fk.get("column")
            }

            context[table.lower()] = {
                "columns": columns,
                "indexed_columns": indexed_columns,
                "fk_columns": fk_columns,
            }
        except Exception as ex:
            logger.debug(f"query_cost: schema fetch failed for table {table!r}: {ex}")
    return context or None


_NUMERIC_TYPE_RE = re.compile(
    r"^(?:tiny|small|medium|big)?int|^decimal|^numeric|^float|^double|^real|"
    r"^bit|^serial|^bigserial|^smallserial|^money", re.IGNORECASE)
_STRING_TYPE_RE = re.compile(
    r"^(?:var|n)?char|^text|^enum|^set|^uuid|^json", re.IGNORECASE)


def _column_type_category(type_str: str) -> str:
    """"numeric" / "string" / "" (date/bool/unknown — never flagged) from a
    dialect's own column-type string (MySQL's COLUMN_TYPE / SHOW COLUMNS
    "Type", or Postgres's information_schema.data_type)."""
    t = (type_str or "").strip().lower()
    if _NUMERIC_TYPE_RE.match(t):
        return "numeric"
    if _STRING_TYPE_RE.match(t):
        return "string"
    return ""


def _looks_numeric(literal: str) -> bool:
    return bool(re.match(r"^-?\d+(\.\d+)?$", literal.strip()))


_WHERE_LITERAL_RE = re.compile(
    r"\b(?:(\w+)\.)?(\w+)\s*(?:=|<>|!=|<=|>=|<|>)\s*"
    r"(?:'([^']*)'|(-?\d+(?:\.\d+)?))"
)


def _check_type_mismatches(sql: str, schema: dict, alias_map: dict) -> list:
    """Flag a WHERE/ON predicate comparing a column against a literal of
    the wrong broad type — a VARCHAR column against a bare numeric literal
    forces MySQL/Postgres to cast the *column* on every row, silently
    defeating any index on it; the reverse (numeric column vs. a
    non-numeric-looking string) is very likely a bug either way."""
    issues: list = []
    flat = sql.replace("`", " ").replace('"', " ")
    table_values = set(alias_map.values())
    only_table = next(iter(table_values)) if len(table_values) == 1 else None
    seen: set = set()

    for m in _WHERE_LITERAL_RE.finditer(flat):
        alias, col, str_lit, num_lit = m.groups()
        table = alias_map.get(alias.lower()) if alias else only_table
        if not table:
            continue
        table_schema = schema.get(table.lower())
        if not table_schema:
            continue
        col_type = table_schema["columns"].get(col.lower())
        if not col_type:
            continue
        category = _column_type_category(col_type)
        if not category:
            continue

        mismatch = (
            (category == "numeric" and str_lit is not None and not _looks_numeric(str_lit))
            or (category == "string" and num_lit is not None)
        )
        if not mismatch:
            continue

        key = (table.lower(), col.lower())
        if key in seen:
            continue
        seen.add(key)
        literal_repr = f"'{str_lit}'" if str_lit is not None else num_lit
        issues.append(Issue(
            severity="MEDIUM",
            code="TYPE_MISMATCH_PREDICATE",
            message=f"`{table}`.`{col}` is {col_type.upper()} but is compared against "
                    f"{literal_repr} — a mismatched literal type that can force an "
                    f"implicit cast of the column, silently defeating any index on it.",
            suggestion=f"Compare `{col}` against a same-typed literal (quote it if "
                       f"`{col}` is a string column, or drop the quotes if it's "
                       f"numeric) so an index on `{col}` can be used.",
        ))
    return issues


_ON_CLAUSE_RE = re.compile(
    r"\bON\b(.*?)(?=\bJOIN\b|\bWHERE\b|\bGROUP\s+BY\b|\bORDER\s+BY\b|\bLIMIT\b|$)",
    re.IGNORECASE | re.DOTALL,
)
_COL_EQ_COL_RE = re.compile(r"(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)")


def _check_unindexed_join_keys(sql: str, schema: dict, alias_map: dict) -> list:
    """Flag a JOIN ON column that is neither indexed nor a declared foreign
    key on its table — every join against it forces a scan of that table's
    matching rows rather than an index lookup."""
    issues: list = []
    flat = sql.replace("`", " ").replace('"', " ")
    seen: set = set()

    for on_m in _ON_CLAUSE_RE.finditer(flat):
        for m in _COL_EQ_COL_RE.finditer(on_m.group(1)):
            a1, c1, a2, c2 = m.groups()
            for alias, col in ((a1, c1), (a2, c2)):
                table = alias_map.get(alias.lower())
                if not table:
                    continue
                table_schema = schema.get(table.lower())
                if not table_schema:
                    continue
                col_lower = col.lower()
                if col_lower not in table_schema["columns"]:
                    continue  # not a real column on this table — don't guess
                if (col_lower in table_schema["indexed_columns"]
                        or col_lower in table_schema["fk_columns"]):
                    continue
                key = (table.lower(), col_lower)
                if key in seen:
                    continue
                seen.add(key)
                issues.append(Issue(
                    severity="HIGH",
                    code="UNINDEXED_JOIN_KEY",
                    message=f"JOIN key `{table}`.`{col}` has no index and is not a "
                            f"declared foreign key — every join against it forces a "
                            f"scan of `{table}` rather than an index lookup.",
                    suggestion=f"Add an index on `{table}`(`{col}`), or declare the "
                               f"foreign key relationship if one exists.",
                ))
    return issues


def _candidate_columns_for_table(sql: str, table: str, schema: dict, alias_map: dict) -> list:
    """Real column name(s) from schema referenced in a WHERE/JOIN predicate
    against *table* (which may itself be an alias) — used to name the
    actual column in an index recommendation instead of a generic 'the
    column(s)' phrase. [] if schema doesn't know this table or no
    predicate column resolves, never guessed."""
    real_table = alias_map.get(table.lower(), table)
    table_schema = schema.get(real_table.lower())
    if not table_schema:
        return []

    flat = sql.replace("`", " ").replace('"', " ")
    cols: set = set()
    for ref_name, tbl in alias_map.items():
        if tbl.lower() != real_table.lower():
            continue
        for m in re.finditer(rf"\b{re.escape(ref_name)}\.(\w+)\b", flat, re.IGNORECASE):
            col = m.group(1).lower()
            if col in table_schema["columns"]:
                cols.add(col)

    if len(set(alias_map.values())) == 1:
        where_m = re.search(
            r"\bWHERE\b(.*?)(?:\bGROUP\s+BY\b|\bORDER\s+BY\b|\bLIMIT\b|\bHAVING\b|$)",
            flat, re.IGNORECASE | re.DOTALL,
        )
        if where_m:
            for m in re.finditer(
                    r"\b(\w+)\s*(?:=|<>|!=|<=|>=|<|>|LIKE\b|IN\b)",
                    where_m.group(1), re.IGNORECASE):
                col = m.group(1).lower()
                if col in table_schema["columns"]:
                    cols.add(col)

    return sorted(cols)


def _tables_from_mysql_rows(rows: list, alias_map: dict) -> set:
    out: set = set()
    for r in rows:
        t = str(r.get("table", "") or "")
        if t and not t.startswith("<"):
            out.add(alias_map.get(t.lower(), t))
    return out


def _tables_from_pg_plan(node: dict) -> set:
    out: set = set()
    rel = node.get("Relation Name")
    if rel:
        out.add(rel)
    for child in node.get("Plans", []) or []:
        out |= _tables_from_pg_plan(child)
    return out


def _tables_from_profile_tree(node) -> set:
    out: set = set()
    if node is None:
        return out
    if node.table and not str(node.table).startswith("<"):
        out.add(node.table)
    for child in node.children:
        out |= _tables_from_profile_tree(child)
    return out


def _index_suggestion(sql: str, table: str, schema: "dict | None", alias_map: dict, verb: str = "Add an index on") -> str:
    """The FULL_TABLE_SCAN / NO_POSSIBLE_KEYS / SEQ_SCAN suggestion text —
    names the real candidate column(s) when schema resolved them, else
    falls back to today's generic wording."""
    cols = _candidate_columns_for_table(sql, table, schema, alias_map) if schema else []
    if cols:
        col_list = ", ".join(f"`{c}`" for c in cols)
        return (f"{verb} {col_list} for `{table}` — EXPLAIN reports no usable index "
                f"(possible_keys=NULL) for that condition.")
    return (f"{verb} the column(s) used in the WHERE / JOIN condition for `{table}` "
            f"— EXPLAIN reports no usable index (possible_keys=NULL) for that condition.")


# ===========================================================================
# Static SQL-text rules — dialect independent
# ===========================================================================

def analyze_sql_text(sql: str, schema: "dict | None" = None) -> list:
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

    if schema:
        try:
            alias_map = _parse_table_aliases(sql)
            issues += _check_type_mismatches(sql, schema, alias_map)
            issues += _check_unindexed_join_keys(sql, schema, alias_map)
        except Exception as ex:
            logger.debug(f"query_cost: schema-aware text checks failed: {ex}")

    return issues


# ===========================================================================
# MySQL — classic tabular EXPLAIN (pre-run)
# ===========================================================================

def analyze_explain_rows(rows: list, sql: str, schema: "dict | None" = None) -> list:
    """MySQL classic EXPLAIN — one issue-detection pass per row."""
    issues: list = []
    total_rows_examined = 0
    alias_map = _parse_table_aliases(sql) if schema else {}

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

        has_index_candidate = not _is_missing(possible)

        if typ == "all":
            has_predicate = _has_predicate_on_table(sql, str(tbl))
            severity, recommend_index, reason = _scan_verdict(
                est_rows, has_predicate, has_index_candidate)
            issues.append(Issue(
                severity=severity,
                code="FULL_TABLE_SCAN",
                message=f"Table `{tbl}` is read with a full scan (type=ALL, "
                        f"~{est_rows:,} estimated rows) — {reason}.",
                suggestion=(
                    _index_suggestion(sql, str(tbl), schema, alias_map)
                    if recommend_index else ""
                ),
            ))
        elif typ not in ("eq_ref", "ref", "range", "index", "const", "system") \
                and not has_index_candidate and not str(tbl).startswith("<"):
            has_predicate = _has_predicate_on_table(sql, str(tbl))
            tier = _rows_tier(est_rows)
            if tier == "tiny":
                severity, recommend_index = "INFO", False
            elif not has_predicate:
                severity, recommend_index = "LOW", False
            else:
                severity = {"medium": "MEDIUM", "large": "HIGH", "huge": "CRITICAL"}[tier]
                recommend_index = True
            cols = _candidate_columns_for_table(sql, str(tbl), schema, alias_map) if schema and recommend_index else []
            issues.append(Issue(
                severity=severity,
                code="NO_POSSIBLE_KEYS",
                message=f"Table `{tbl}` has no candidate index for its access "
                        f"path (type={typ}, possible_keys=NULL, "
                        f"~{est_rows:,} estimated rows).",
                suggestion=(
                    (f"Create a covering index on {', '.join(f'`{c}`' for c in cols)} "
                     f"for `{tbl}`." if cols else
                     f"Inspect the JOIN / WHERE predicates touching `{tbl}` "
                     f"and create a covering index.")
                    if recommend_index else ""
                ),
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
            small = _rows_tier(est_rows) == "tiny"
            issues.append(Issue(
                severity="LOW" if small else "MEDIUM",
                code="FILESORT",
                message=f"Table `{tbl}` requires a filesort (ORDER BY cannot use an "
                        f"index) over ~{est_rows:,} estimated rows"
                        + (" — cheap at this size." if small else "."),
                suggestion=(
                    "" if small else
                    "Add a composite index that covers the ORDER BY columns "
                    f"(and optionally the WHERE columns) for `{tbl}`."
                ),
            ))

        if "temporary" in extra:
            small = _rows_tier(est_rows) == "tiny"
            issues.append(Issue(
                severity="LOW" if small else "MEDIUM",
                code="TEMP_TABLE",
                message=f"Query creates a temporary table (table=`{tbl}`, "
                        f"select_type={select_type}, ~{est_rows:,} estimated rows)"
                        + (" — cheap at this size." if small else "."),
                suggestion=(
                    "" if small else
                    "Rewrite GROUP BY / DISTINCT to avoid temp tables, or ensure "
                    "the GROUP BY columns are indexed."
                ),
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

    issues += analyze_sql_text(sql, schema)
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


def _annotate_tree_from_explain_rows(node: "ProfileNode | None", rows: list) -> None:
    """Attach access_type/possible_keys/key/filtered from the classic
    tabular EXPLAIN onto the matching node(s) of a parsed tree, by table
    name — the tree (EXPLAIN FORMAT=TREE / EXPLAIN ANALYZE) carries the
    plan shape and costs but not these columns, and the classic EXPLAIN
    carries these columns but not the tree shape. Best-effort: a table
    name that doesn't match any row is simply left unannotated, never
    guessed."""
    if node is None or not rows:
        return
    by_table = {str(r.get("table", "")).lower(): r for r in rows if r.get("table")}

    def walk(n: "ProfileNode"):
        row = by_table.get(n.table.lower()) if n.table else None
        if row is not None:
            n.access_type = str(row.get("type") or "")
            pk = row.get("possible_keys")
            n.possible_keys = "" if _is_missing(pk) else str(pk)
            key = row.get("key")
            n.key = "" if _is_missing(key) else str(key)
            filt = row.get("filtered")
            n.filtered = None if _is_missing(filt) else float(filt)
        for child in n.children:
            walk(child)

    walk(node)


def _walk_mysql_profile(node: "ProfileNode", issues: list, sql: str,
                         schema: "dict | None" = None, alias_map: "dict | None" = None) -> None:
    alias_map = alias_map or {}
    desc_lower = node.node_type.lower()
    if "table scan" in desc_lower or "full scan" in desc_lower:
        # Same table-size/predicate/candidate-index evidence as the
        # plan-only path (analyze_explain_rows) — a full scan found by
        # EXPLAIN ANALYZE is judged by the same rules as one found by
        # EXPLAIN, using the *actual* row count now that the query really
        # ran. `possible_keys` here comes from build_profile()'s best-
        # effort classic-EXPLAIN annotation (_annotate_tree_from_explain_
        # rows); unannotated (e.g. that lookup failed) reads as "no
        # candidate" rather than crashing.
        actual_rows = node.rows_actual or node.rows_estimated
        has_predicate = _has_predicate_on_table(sql, node.table)
        has_index_candidate = bool(node.possible_keys)
        severity, recommend_index, reason = _scan_verdict(
            actual_rows, has_predicate, has_index_candidate)
        issues.append(Issue(
            severity=severity,
            code="FULL_TABLE_SCAN",
            message=f"Full scan of `{node.table or '?'}` — {node.rows_actual:,} actual "
                    f"rows over {node.loops} loop(s); {reason}.",
            suggestion=(
                _index_suggestion(sql, node.table or "?", schema, alias_map, verb="Add an index on")
                if recommend_index and node.table else
                (f"Add an index on the column(s) filtering this table — EXPLAIN reports "
                 f"no usable index (possible_keys=NULL) for that condition."
                 if recommend_index else "")
            ),
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
        _walk_mysql_profile(child, issues, sql, schema, alias_map)


def analyze_mysql_profile(root: "ProfileNode | None", sql: str, schema: "dict | None" = None) -> list:
    issues: list = []
    alias_map = _parse_table_aliases(sql) if schema else {}
    if root:
        _walk_mysql_profile(root, issues, sql, schema, alias_map)
    issues += analyze_sql_text(sql, schema)
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


def _walk_postgres_plan(node: dict, issues: list, has_actuals: bool,
                         sql: str = "", schema: "dict | None" = None,
                         alias_map: "dict | None" = None) -> None:
    alias_map = alias_map or {}
    node_type = node.get("Node Type", "")
    tbl = node.get("Relation Name", "") or node.get("Alias", "?")
    real_tbl = node.get("Relation Name") or alias_map.get(str(tbl).lower(), tbl)
    plan_rows = _int(node.get("Plan Rows", 0))
    actual_rows = _int(node.get("Actual Rows", 0)) if has_actuals else plan_rows

    if node_type == "Seq Scan" and (actual_rows if has_actuals else plan_rows) > _PG_BIG_ROWS:
        issues.append(Issue(
            severity="CRITICAL" if (actual_rows if has_actuals else plan_rows) > 100_000 else "HIGH",
            code="SEQ_SCAN",
            message=f"Sequential scan on `{tbl}` "
                    f"(~{(actual_rows if has_actuals else plan_rows):,} rows).",
            suggestion=(
                _index_suggestion(sql, real_tbl, schema, alias_map)
                if schema else f"Add an index on the column(s) used to filter or join `{tbl}`."
            ),
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
        _walk_postgres_plan(child, issues, has_actuals, sql, schema, alias_map)


def analyze_postgres_plan(plan_root: dict, sql: str, has_actuals: bool = False,
                           schema: "dict | None" = None) -> list:
    issues: list = []
    alias_map = _parse_table_aliases(sql) if schema else {}
    if plan_root:
        _walk_postgres_plan(plan_root, issues, has_actuals, sql, schema, alias_map)
    issues += analyze_sql_text(sql, schema)
    return issues


# ===========================================================================
# Scoring / labeling
# ===========================================================================

def score_issues(issues: list) -> int:
    """QForge Risk Score, normalized to 0-100. Severity weights are summed
    and capped rather than left unbounded, so a query with several
    low-severity issues never reads as "worse" than the cap implies, and a
    single CRITICAL issue (already the cap) always reads as maximum risk."""
    if not issues:
        return 0
    return min(100, sum(SEVERITY_SCORE[i.severity] for i in issues))


def label_for(issues: list) -> str:
    if not issues:
        return "Looks efficient"
    worst = max(issues, key=lambda i: SEVERITY_SCORE[i.severity])
    return _CODE_LABELS.get(worst.code, worst.severity.title())


def risk_level_for(issues: list) -> str:
    """Low / Medium / High / Critical — derived from the same normalized
    0-100 QForge Risk Score shown next to it, so the risk chip and the
    score badge always agree instead of tracking two independent
    classifications. A single issue's own severity still maps to the band
    its SEVERITY_SCORE weight lands in (e.g. one MEDIUM issue → Medium)."""
    score = score_issues(issues)
    if score >= 70:
        return "Critical"
    if score >= 45:
        return "High"
    if score >= 20:
        return "Medium"
    return "Low"


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
            alias_map = _parse_table_aliases(sql)
            schema = fetch_schema_context(db_service, _tables_from_mysql_rows(rows, alias_map))
            issues = analyze_explain_rows(rows, sql, schema)

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

            if plan_tree:
                _annotate_tree_from_explain_rows(plan_tree, rows)

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
            schema = fetch_schema_context(db_service, _tables_from_pg_plan(plan_root))
            issues = analyze_postgres_plan(plan_root, sql, has_actuals=False, schema=schema)
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

            # Best-effort: also pull the classic tabular EXPLAIN so the
            # tree can be annotated with access type / possible keys / key
            # / filtered (see _annotate_tree_from_explain_rows). This is a
            # second plan-only EXPLAIN, not a second execution — the query
            # has already run for real via EXPLAIN ANALYZE above.
            if root:
                try:
                    classic_df = db_service.execute_query(f"EXPLAIN {clean}")
                    _annotate_tree_from_explain_rows(root, classic_df.to_dict("records"))
                except Exception as classic_ex:
                    logger.debug(f"query_cost: classic EXPLAIN unavailable for profile annotation: {classic_ex}")

            alias_map = _parse_table_aliases(sql)
            profile_tables = {alias_map.get(t.lower(), t) for t in _tables_from_profile_tree(root)}
            schema = fetch_schema_context(db_service, profile_tables)
            issues = analyze_mysql_profile(root, sql, schema)
            total_ms = root.time_ms if root else 0.0
            return QueryProfile(dialect, total_ms, root, issues)

        elif dialect == "postgresql":
            df = db_service.execute_query(
                f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {clean}")
            raw = df.iloc[0, 0]
            plan_list = raw if isinstance(raw, list) else json.loads(raw)
            plan_root_raw = plan_list[0]["Plan"]
            root = _pg_node_to_profile(plan_root_raw)
            schema = fetch_schema_context(db_service, _tables_from_pg_plan(plan_root_raw))
            issues = analyze_postgres_plan(plan_root_raw, sql, has_actuals=True, schema=schema)
            total_ms = float(plan_list[0].get("Execution Time", root.time_ms) or root.time_ms)
            return QueryProfile(dialect, total_ms, root, issues)

        else:
            return QueryProfile(dialect, supported=False, error=f"Unsupported dialect: {dialect}")

    except Exception as ex:
        return QueryProfile(dialect, error=str(ex))


# ===========================================================================
# Plan comparison — diffing two plan-only CostEstimates (e.g. Compare
# Queries' Query 1 vs Query 2). QForge never assumes which side is
# "optimized"; the diff below is symmetric and just reports what changed.
# ===========================================================================

@dataclass
class PlanDelta:
    """One table's before/after verdict from diff_plan_trees()."""
    table: str
    status: str              # "regression" | "improvement" | "changed" | "added" | "removed"
    access_before: str = ""
    access_after: str = ""
    severity_before: str = ""   # worst Issue severity naming this table in estimate 1, "" if none
    severity_after: str = ""    # ... in estimate 2
    reason: str = ""


_ISSUE_TABLE_RE = re.compile(r"`([^`]+)`")


def _flatten_tree_by_table(root) -> dict:
    """{table_lower: ProfileNode} for every node in *root* that names a
    real table — nodes with no table (aggregates, sorts, a join node with
    no single table of its own) are skipped rather than guessed at. The
    first node seen for a given table wins if it somehow appears twice."""
    out: dict = {}

    def walk(node):
        if node.table and not str(node.table).startswith("<"):
            out.setdefault(node.table.lower(), node)
        for child in node.children:
            walk(child)

    if root:
        walk(root)
    return out


def _worst_severity_by_table(issues: list) -> dict:
    """{table_lower: worst severity string} from a CostEstimate's issues —
    matches the first backtick-quoted identifier in each issue's message
    against a table name. Works for both dialects' Issue messages, which
    consistently backtick-quote the table somewhere in the text (MySQL:
    "Table `t` ...", PostgreSQL: "Sequential scan on `t` ..."); an issue
    with no backtick-quoted name (e.g. SELECT_STAR, a Postgres nested-loop
    warning) is query-wide, not table-scoped, and is correctly skipped
    here rather than force-attached to a table."""
    out: dict = {}
    for issue in issues or []:
        m = _ISSUE_TABLE_RE.search(issue.message)
        if not m:
            continue
        tbl = m.group(1).lower()
        cur = out.get(tbl)
        if cur is None or SEVERITY_SCORE[issue.severity] > SEVERITY_SCORE[cur]:
            out[tbl] = issue.severity
    return out


def diff_plan_trees(estimate1: CostEstimate, estimate2: CostEstimate) -> list:
    """Best-effort per-table diff between two plan-only estimates. Matches
    tables by name across both plans — a rewrite that renames a table via
    a new alias won't match; that's a known limitation of name-based
    matching, not a guessed result — and flags only the tables whose
    access path or worst issue severity actually changed. An unchanged
    table is omitted entirely, so the result is a change list, not a full
    re-statement of every table in the query.

    Returns [] when either estimate has no plan_tree (e.g. EXPLAIN
    FORMAT=TREE unavailable on an older MySQL server, or the estimate
    itself errored) rather than fabricating a comparison from partial
    data — callers should fall back to whatever raw EXPLAIN display they
    already have in that case."""
    tree1 = estimate1.plan_tree if estimate1 else None
    tree2 = estimate2.plan_tree if estimate2 else None
    if not tree1 or not tree2:
        return []

    nodes1, nodes2 = _flatten_tree_by_table(tree1), _flatten_tree_by_table(tree2)
    sev1, sev2 = _worst_severity_by_table(estimate1.issues), _worst_severity_by_table(estimate2.issues)

    deltas = []
    for table in sorted(set(nodes1) | set(nodes2)):
        n1, n2 = nodes1.get(table), nodes2.get(table)

        if n1 and not n2:
            deltas.append(PlanDelta(
                table, "removed", access_before=n1.access_type or "",
                severity_before=sev1.get(table, ""),
                reason="no longer referenced in Query 2's plan",
            ))
            continue
        if n2 and not n1:
            deltas.append(PlanDelta(
                table, "added", access_after=n2.access_type or "",
                severity_after=sev2.get(table, ""),
                reason="newly referenced in Query 2's plan",
            ))
            continue

        s1, s2 = sev1.get(table, ""), sev2.get(table, "")
        w1, w2 = SEVERITY_SCORE.get(s1, 0), SEVERITY_SCORE.get(s2, 0)
        a1, a2 = n1.access_type or "", n2.access_type or ""
        access_changed = a1 != a2

        if w2 > w1:
            status = "regression"
            reason = (f"access changed {a1 or '?'} → {a2 or '?'}, now worse: "
                      f"{s1 or 'no issue'} → {s2}") if access_changed else \
                     f"same access path, but now flagged {s2} (was {s1 or 'no issue'})"
        elif w1 > w2:
            status = "improvement"
            reason = (f"access changed {a1 or '?'} → {a2 or '?'}, now better: "
                      f"{s1} → {s2 or 'no issue'}") if access_changed else \
                     f"same access path, issue resolved: {s1} → {s2 or 'no issue'}"
        elif access_changed:
            status = "changed"
            reason = f"access changed {a1 or '?'} → {a2 or '?'}, severity unchanged ({s1 or 'no issue'})"
        else:
            continue  # unchanged — not worth listing

        deltas.append(PlanDelta(table, status, a1, a2, s1, s2, reason))

    return deltas


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
