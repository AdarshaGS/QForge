"""
ai_prompts
==========
Pure prompt/JSON-schema builders for every AI Assistance capability — no
subprocess or Qt code, so wording iterates independently of
services/ai_client.py's transport. Every builder takes the narrow,
best-effort schema context from services/query_cost.fetch_schema_context()
(never a full-schema dump) plus whatever QForge already has in hand (query
text, an error message, a rule-based Issue list) and returns either a
prompt string, or (prompt, json_schema) when the caller needs a
machine-parseable result.
"""

from __future__ import annotations


# Soft cap on how many tables' full column lists get sent when no specific
# table list narrows things down (build_schema_context_from_cache with
# tables=None) — a NL-to-SQL request or a fresh schema-chat turn has no SQL
# text to parse real table names from, so "which tables are relevant" can't
# be determined client-side the way explain/fix/optimize do via
# query_cost._parse_table_aliases(). Sending every table's columns for a
# huge schema would bloat the prompt; sending none (or only tables whose
# exact name happens to appear in the free-text request) is the "AI is
# guessing the schema" bug this exists to fix. A cap keeps prompt size
# bounded while still covering the common case (tens of tables) in full.
_MAX_CACHE_SCHEMA_TABLES = 60


def build_schema_context_from_cache(column_details: dict | None, foreign_keys: dict | None = None,
                                     tables: list[str] | None = None) -> dict | None:
    """Builds the same {table_lower: {columns, indexed_columns, fk_columns}}
    shape services.query_cost.fetch_schema_context() returns, but from
    already-loaded autocomplete caches (ConnectionPanel's
    _column_details_cache/_foreign_keys_cache — see
    ui/sql_completer.py's set_schema and services/db_service.py's
    get_all_column_details()/get_all_foreign_keys()) instead of a fresh DB
    round-trip. Used by NL-to-SQL and schema chat, which — unlike explain/
    fix/optimize — have no SQL text yet to parse real table references
    from, so every *known* table is included (up to a bounded cap) rather
    than only ones a name-matching heuristic happened to catch.

    *tables*, when given, is used as-is (no cap) — the caller already knows
    exactly which tables matter (e.g. a prior turn's context). When None,
    every table in column_details is included up to _MAX_CACHE_SCHEMA_TABLES."""
    if not column_details:
        return None
    foreign_keys = foreign_keys or {}
    names = tables if tables is not None else sorted(column_details.keys())[:_MAX_CACHE_SCHEMA_TABLES]

    context: dict = {}
    for table in names:
        cols = column_details.get(table)
        if not cols:
            continue
        columns = {}
        indexed_columns = set()
        for c in cols:
            name = c.get("name")
            if not name:
                continue
            columns[str(name).lower()] = str(c.get("type") or "").lower()
            if c.get("key") == "PRI":
                indexed_columns.add(str(name).lower())
        if not columns:
            continue
        fk_columns = {
            str(fk["column"]).lower()
            for fk in (foreign_keys.get(table) or [])
            if fk.get("column")
        }
        context[table.lower()] = {
            "columns": columns,
            "indexed_columns": indexed_columns,
            "fk_columns": fk_columns,
        }
    return context or None


def _format_schema_context(schema_context: dict | None) -> str:
    if not schema_context:
        return "(No schema context available.)"
    lines = []
    for table, detail in schema_context.items():
        columns = detail.get("columns", {}) or {}
        # Include each column's type, not just its name — omitting types
        # here (while still including them in the schema_context dict
        # itself) meant the model never actually saw them and had to guess
        # numeric vs. string vs. date handling for every column.
        cols = ", ".join(
            f"{name}:{typ}" if typ else name
            for name, typ in sorted(columns.items())
        )
        indexed = ", ".join(sorted(detail.get("indexed_columns", set())))
        fks = ", ".join(sorted(detail.get("fk_columns", set())))
        lines.append(f"- {table}(columns: {cols or 'none'})"
                      + (f" [indexed: {indexed}]" if indexed else "")
                      + (f" [foreign keys: {fks}]" if fks else ""))
    return "\n".join(lines)


_NL_TO_SQL_SCHEMA = {
    "type": "object",
    "properties": {
        "sql": {"type": "string"},
        "explanation": {"type": "string"},
        "caveats": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["sql", "explanation"],
}


def build_nl_to_sql_prompt(request: str, dialect: str, schema_context: dict | None,
                            known_tables: list[str] | None = None) -> tuple[str, dict]:
    tables_hint = (", ".join(known_tables) if known_tables else "(unknown)")
    prompt = (
        f"You are a {dialect} SQL assistant embedded in a desktop database "
        f"client. Write a single {dialect} SQL statement for this request:\n\n"
        f"\"{request}\"\n\n"
        f"Known tables in the connected database: {tables_hint}\n\n"
        f"Schema context for tables that look relevant:\n"
        f"{_format_schema_context(schema_context)}\n\n"
        f"Only use tables/columns you have evidence for above — never invent "
        f"a table or column name. If you cannot confidently write the query, "
        f"say so in `explanation` and return your best-effort SQL anyway. "
        f"Return `caveats` as a list of any assumptions you had to make."
    )
    return prompt, _NL_TO_SQL_SCHEMA


def build_explain_prompt(sql: str, dialect: str, schema_context: dict | None) -> str:
    return (
        f"Explain what this {dialect} SQL statement does, in plain language "
        f"a developer who didn't write it could follow. Be concise — a few "
        f"short paragraphs, not a line-by-line commentary.\n\n"
        f"```sql\n{sql}\n```\n\n"
        f"Schema context for tables involved:\n"
        f"{_format_schema_context(schema_context)}"
    )


_FIX_ERROR_SCHEMA = {
    "type": "object",
    "properties": {
        "corrected_sql": {"type": "string"},
        "explanation": {"type": "string"},
    },
    "required": ["corrected_sql", "explanation"],
}


def build_fix_error_prompt(sql: str, error_message: str, dialect: str,
                            schema_context: dict | None) -> tuple[str, dict]:
    prompt = (
        f"This {dialect} SQL statement failed. Diagnose the problem and "
        f"return a corrected version.\n\n"
        f"```sql\n{sql}\n```\n\n"
        f"Database error:\n{error_message}\n\n"
        f"Schema context for tables involved:\n"
        f"{_format_schema_context(schema_context)}\n\n"
        f"`corrected_sql` must be a complete, runnable replacement for the "
        f"statement above — not a diff or partial snippet. `explanation` "
        f"should describe what was wrong in one or two sentences."
    )
    return prompt, _FIX_ERROR_SCHEMA


def build_schema_chat_prompt(question: str, schema_context: dict | None,
                              history: list[tuple[str, str]] | None = None) -> str:
    history = history or []
    transcript = "\n\n".join(
        f"User: {u}\nAssistant: {a}" for u, a in history
    )
    return (
        f"You are answering questions about a database's schema/data model "
        f"inside a desktop SQL client. Be concise and concrete — reference "
        f"real table/column names from the schema context below, and say "
        f"plainly when you don't have enough context to answer.\n\n"
        f"Schema context gathered so far:\n{_format_schema_context(schema_context)}\n\n"
        + (f"Prior conversation:\n{transcript}\n\n" if transcript else "")
        + f"User: {question}"
    )


_OPTIMIZE_SCHEMA = {
    "type": "object",
    "properties": {
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                    "severity": {"type": "string", "enum": ["info", "suggestion", "important"]},
                    "suggested_sql": {"type": ["string", "null"]},
                },
                "required": ["title", "detail", "severity"],
            },
        },
    },
    "required": ["suggestions"],
}


def build_optimize_prompt(sql: str, dialect: str, schema_context: dict | None,
                          existing_issues: list | None = None) -> tuple[str, dict]:
    issues_text = "(none found)"
    if existing_issues:
        issues_text = "\n".join(
            f"- [{i.severity}] {i.code}: {i.message} — {i.suggestion}"
            for i in existing_issues
        )
    prompt = (
        f"Suggest ways to optimize this {dialect} SQL query. A rule-based "
        f"static analyzer already found the issues listed below — build on "
        f"those (elaborate, propose concrete rewrites) rather than "
        f"re-deriving the same findings from scratch. Only add genuinely "
        f"new suggestions beyond what's already listed.\n\n"
        f"```sql\n{sql}\n```\n\n"
        f"Schema context:\n{_format_schema_context(schema_context)}\n\n"
        f"Rule-based findings already surfaced to the user:\n{issues_text}\n\n"
        f"For each suggestion, optionally include a rewritten `suggested_sql` "
        f"only when you're confident it's equivalent to the original query."
    )
    return prompt, _OPTIMIZE_SCHEMA
