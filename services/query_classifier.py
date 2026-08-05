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
from sqlparse.sql import Where

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
    "INSERT", "UPDATE", "DELETE",
    "CREATE", "DROP", "ALTER", "TRUNCATE", "RENAME", "GRANT", "REVOKE",
}
DESTRUCTIVE_DDL_KINDS = {"DROP", "TRUNCATE"}
WHERE_APPLICABLE_KINDS = {"UPDATE", "DELETE"}

# sqlparse's get_type() reports UNKNOWN for these; the real keyword has to
# come from the first non-comment token instead (verified against the
# installed sqlparse version — see tests/test_query_classifier.py).
_UNKNOWN_TYPE_FALLBACKS = {"GRANT", "REVOKE", "RENAME"}


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


def is_dangerous(classification: Classification) -> bool:
    """True if this statement should trigger the dangerous-query
    confirmation (ai/load-context.md's "at minimum flag" list, minus the
    "mass writes" case — that needs a row count, which only the caller
    building the statement has, e.g. CSV import's DataFrame length)."""
    return bool(classification.reasons)
