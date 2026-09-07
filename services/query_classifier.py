"""Classifies SQL statements for the read-only guard and dangerous-query
guard (ai/load-context.md, Slices 2/3). Built on sqlparse's token tree, not
string-prefix matching, so comments, whitespace, CTEs, quoted identifiers,
and multi-statement scripts are handled correctly. Pure Python — no Qt, no
DB access — so it can be shared by services/db_service.py (the backstop)
and ui/connection_panel.py (the UX layer) without either depending on the
other.
"""

from dataclasses import dataclass, field

import sqlparse
from sqlparse.engine import grouping
from sqlparse.sql import Where, Identifier
from sqlparse.tokens import Keyword, DML

# sqlparse's default 10,000-token grouping-safety cap (issue #30) is too low
# for legitimately large generated SQL (long WHERE...IN chains, reporting
# queries). Measured cost is roughly linear, not the quadratic blowup the
# cap seems to guard against — ~1.6s at 128k tokens, ~8.5s at 512k on a
# 2026 laptop — and every caller of classify()/split_statements() in this
# app (DbService._guard, ConnectionPanel._guard_write, QueryVerifier) runs
# off the main thread, so a slower parse doesn't freeze the UI. Raised
# generously rather than disabled — still a backstop against a genuinely
# pathological paste (e.g. non-SQL content pasted by mistake).
grouping.MAX_GROUPING_TOKENS = 100_000

WRITE_KINDS = {
    "INSERT", "UPDATE", "DELETE", "REPLACE",
    "CREATE", "DROP", "ALTER", "TRUNCATE", "RENAME", "GRANT", "REVOKE",
    "LOAD",
}
DESTRUCTIVE_DDL_KINDS = {"DROP", "TRUNCATE"}
# Statements that change table/column structure — used to invalidate the
# on-disk schema cache (issue #72). TRUNCATE/GRANT/REVOKE are writes but
# don't change what get_tables()/get_columns() would return, so they're
# deliberately excluded.
SCHEMA_CHANGING_KINDS = {"CREATE", "DROP", "ALTER", "RENAME"}
WHERE_APPLICABLE_KINDS = {"UPDATE", "DELETE"}

# sqlparse's get_type() reports UNKNOWN for these; the real keyword has to
# come from the first non-comment token instead (verified against the
# installed sqlparse version — see tests/test_query_classifier.py).
_UNKNOWN_TYPE_FALLBACKS = {
    "GRANT", "REVOKE", "RENAME", "BEGIN", "LOAD", "CALL", "EXEC", "EXECUTE",
}

# Stored procedure/function calls (issue #70). sqlparse can't tell whether
# the called routine mutates data, so these are treated as unsafe under
# read-only mode (READ_ONLY_BLOCKED_KINDS below) without folding them into
# WRITE_KINDS itself — WRITE_KINDS also drives execute_multi_query()'s
# DataFrame-vs-affected-rows dispatch, and a CALL can legitimately return a
# result set that would be silently dropped if routed through
# execute_update() instead of execute_query().
PROCEDURE_CALL_KINDS = {"CALL", "EXEC", "EXECUTE"}

# Everything read-only mode blocks (issue #70) — broader than WRITE_KINDS
# because a stored-procedure call might mutate even though it isn't itself
# classified as a write.
READ_ONLY_BLOCKED_KINDS = WRITE_KINDS | PROCEDURE_CALL_KINDS

# Transaction-control statements (Slice 4, ai/load-context.md). Not writes,
# not dangerous — used by services/db_service.py to route BEGIN/COMMIT/
# ROLLBACK (however the user wrote it: typed directly or via the UI's
# Begin/Commit/Rollback buttons) through its transaction state machine
# instead of sending them as ordinary SQL.
TRANSACTION_KINDS = {"BEGIN", "COMMIT", "ROLLBACK"}


@dataclass
class Classification:
    statement: str
    kind: str
    is_write: bool
    is_destructive_ddl: bool
    has_where: bool | None       # None when not applicable (not UPDATE/DELETE)
    reasons: list = field(default_factory=list)


def split_statements(sql: str) -> list:
    """Single source of truth for splitting a script into statements —
    replaces the three independent sqlparse.split()/naive-split
    implementations that used to exist across the codebase. Drops
    fragments that are empty or contain nothing but a stray semicolon
    (sqlparse.split() otherwise yields a bare ";" as its own statement for
    input like "SELECT 1;;")."""
    stripped = (s.strip() for s in sqlparse.split(sql))
    return [s for s in stripped if s and s.rstrip(";").strip()]


def classify(stmt_text: str) -> Classification:
    parsed = sqlparse.parse(stmt_text)
    if not parsed:
        return Classification(stmt_text, "OTHER", False, False, None, [])

    stmt = parsed[0]
    kind = stmt.get_type()
    if kind == "UNKNOWN":
        first = stmt.token_first(skip_cm=True)
        value = first.value.upper() if first else ""
        kind = value if value in _UNKNOWN_TYPE_FALLBACKS else "OTHER"
    elif kind == "START":
        # sqlparse types "START TRANSACTION" as "START" rather than
        # UNKNOWN — fold it into the same bucket as "BEGIN"/"BEGIN
        # TRANSACTION" so callers only need to check one kind.
        kind = "BEGIN"

    is_write = kind in WRITE_KINDS
    is_destructive_ddl = kind in DESTRUCTIVE_DDL_KINDS

    has_where = None
    reasons = []
    if kind in WHERE_APPLICABLE_KINDS:
        has_where = any(isinstance(tok, Where) for tok in stmt.tokens)
        if not has_where:
            reasons.append(f"{kind} without a WHERE clause — affects all rows")

    if kind in DESTRUCTIVE_DDL_KINDS:
        reasons.append(f"{kind} is a destructive, typically irreversible schema change")
    elif kind == "ALTER":
        reasons.append("ALTER TABLE changes schema structure")
    elif kind == "RENAME":
        reasons.append("RENAME changes schema structure")
    elif kind in ("GRANT", "REVOKE"):
        reasons.append(f"{kind} changes database permissions")
    elif kind == "CREATE":
        reasons.append("CREATE adds new schema structure")

    return Classification(stmt_text, kind, is_write, is_destructive_ddl, has_where, reasons)


def _single_target_table(stmt) -> str | None:
    """Best-effort table name for a single-table UPDATE/DELETE — None
    (caller bails) for anything with a JOIN, since a matched-row count
    for a multi-table statement isn't reducible to one COUNT(*) without
    risking a misleading number (issue #247)."""
    if any(t.ttype is Keyword and "JOIN" in t.value.upper() for t in stmt.flatten()):
        return None

    # Strictly Identifier, not the broader "ttype is None" — an
    # IdentifierList ("UPDATE a, b SET ...") is also ttype None and would
    # otherwise look like one (nonexistent) table.
    tokens = [t for t in stmt.tokens if not t.is_whitespace]
    kind = stmt.get_type()
    if kind == "UPDATE":
        for i, t in enumerate(tokens):
            if t.ttype is DML and t.value.upper() == "UPDATE":
                nxt = tokens[i + 1] if i + 1 < len(tokens) else None
                if isinstance(nxt, Identifier):
                    # Keep any alias ("db.orders o") — the WHERE clause
                    # being reused verbatim may reference it.
                    return str(nxt).strip()
        return None
    if kind == "DELETE":
        seen_from = False
        for t in tokens:
            if t.ttype is Keyword and t.value.upper() == "FROM":
                seen_from = True
                continue
            if seen_from:
                if isinstance(t, Identifier):
                    return str(t).strip()
                return None
        return None
    return None


def build_count_query(classification: Classification) -> str | None:
    """A conservative SELECT COUNT(*) mirroring *classification*'s own
    WHERE clause, for showing an affected-row estimate before a
    dangerous UPDATE/DELETE executes (issue #247). Returns None — never
    a guessed or approximate query — for anything that can't be safely
    reduced to a single-table count: multi-table JOINs, CTEs, or a shape
    _single_target_table() doesn't recognize. Callers must treat None as
    "no estimate available", not an error."""
    if classification.kind not in WHERE_APPLICABLE_KINDS:
        return None
    # WITH ... UPDATE/DELETE (a CTE-qualified statement) can still get
    # classified by its inner DML keyword — bail explicitly rather than
    # risk counting against the wrong table.
    if classification.statement.strip().upper().startswith("WITH"):
        return None

    parsed = sqlparse.parse(classification.statement)
    if not parsed:
        return None
    stmt = parsed[0]
    table = _single_target_table(stmt)
    if not table:
        return None

    where_clause = next((t for t in stmt.tokens if isinstance(t, Where)), None)
    if where_clause is not None:
        return f"SELECT COUNT(*) FROM {table} {str(where_clause).strip()}"
    return f"SELECT COUNT(*) FROM {table}"


def is_dangerous(classification: Classification) -> bool:
    """True if this statement should trigger the dangerous-query
    confirmation (ai/load-context.md's "at minimum flag" list, minus the
    "mass writes" case — that needs a row count, which only the caller
    building the statement has, e.g. CSV import's DataFrame length)."""
    return bool(classification.reasons)
