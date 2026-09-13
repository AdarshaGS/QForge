"""Tests for services/ai_prompts.py — pure prompt/schema builder functions."""
from services import ai_prompts
from services.query_cost import Issue

_SCHEMA_CTX = {
    "orders": {
        "columns": {"id": "int", "customer_id": "int", "total": "decimal"},
        "indexed_columns": {"id"},
        "fk_columns": {"customer_id"},
    },
}


def test_build_nl_to_sql_prompt_includes_request_and_schema():
    prompt, schema = ai_prompts.build_nl_to_sql_prompt(
        "show me all orders over 100", "mysql", _SCHEMA_CTX, known_tables=["orders", "customers"],
    )
    assert "show me all orders over 100" in prompt
    assert "orders" in prompt
    assert "customer_id" in prompt
    assert schema["required"] == ["sql", "explanation"]


def test_schema_context_formatting_includes_column_types():
    """Column types must reach the prompt, not just names — a model that
    can't see a column is numeric vs. text vs. a date has to guess how to
    filter/cast/format it."""
    prompt = ai_prompts.build_explain_prompt("SELECT * FROM orders", "mysql", _SCHEMA_CTX)
    assert "total:decimal" in prompt
    assert "id:int" in prompt


def test_build_nl_to_sql_prompt_handles_missing_schema_context():
    prompt, _ = ai_prompts.build_nl_to_sql_prompt("anything", "postgresql", None)
    assert "No schema context available" in prompt


def test_build_explain_prompt_embeds_sql():
    prompt = ai_prompts.build_explain_prompt("SELECT * FROM orders", "mysql", _SCHEMA_CTX)
    assert "SELECT * FROM orders" in prompt
    assert "orders" in prompt


def test_build_fix_error_prompt_includes_error_message():
    prompt, schema = ai_prompts.build_fix_error_prompt(
        "SELECT * FROM ordrs", "Table 'ordrs' doesn't exist", "mysql", _SCHEMA_CTX,
    )
    assert "Table 'ordrs' doesn't exist" in prompt
    assert schema["required"] == ["corrected_sql", "explanation"]


def test_build_schema_chat_prompt_includes_history_and_question():
    prompt = ai_prompts.build_schema_chat_prompt(
        "which columns are indexed?", _SCHEMA_CTX,
        history=[("what tables exist?", "Just orders.")],
    )
    assert "which columns are indexed?" in prompt
    assert "what tables exist?" in prompt
    assert "Just orders." in prompt


def test_build_schema_chat_prompt_with_no_history_omits_transcript_section():
    prompt = ai_prompts.build_schema_chat_prompt("hi", _SCHEMA_CTX, history=None)
    assert "Prior conversation" not in prompt


def test_build_optimize_prompt_includes_existing_issues():
    issues = [Issue("HIGH", "FULL_TABLE_SCAN", "full scan on orders", "add an index")]
    prompt, schema = ai_prompts.build_optimize_prompt(
        "SELECT * FROM orders", "mysql", _SCHEMA_CTX, existing_issues=issues,
    )
    assert "FULL_TABLE_SCAN" in prompt
    assert "add an index" in prompt
    assert schema["required"] == ["suggestions"]


def test_build_optimize_prompt_with_no_issues_says_none_found():
    prompt, _ = ai_prompts.build_optimize_prompt("SELECT 1", "mysql", None, existing_issues=None)
    assert "(none found)" in prompt


_COLUMN_DETAILS = {
    "orders": [
        {"name": "id", "type": "int", "nullable": False, "key": "PRI"},
        {"name": "customer_id", "type": "int", "nullable": False, "key": ""},
        {"name": "total", "type": "decimal(10,2)", "nullable": False, "key": ""},
    ],
    "customers": [
        {"name": "id", "type": "int", "nullable": False, "key": "PRI"},
        {"name": "name", "type": "varchar(100)", "nullable": False, "key": ""},
    ],
}
_FOREIGN_KEYS = {
    "orders": [{"column": "customer_id", "ref_table": "customers", "ref_column": "id",
                "constraint": "fk_orders_customer"}],
}


def test_build_schema_context_from_cache_includes_all_tables_by_default():
    ctx = ai_prompts.build_schema_context_from_cache(_COLUMN_DETAILS, _FOREIGN_KEYS)
    assert set(ctx.keys()) == {"orders", "customers"}
    assert ctx["orders"]["columns"]["total"] == "decimal(10,2)"
    assert ctx["orders"]["indexed_columns"] == {"id"}
    assert ctx["orders"]["fk_columns"] == {"customer_id"}


def test_build_schema_context_from_cache_no_table_name_matching_required():
    """The whole point: a request that never says "orders" or "customers"
    verbatim still gets full column info for both, unlike the old
    substring-match-against-free-text approach."""
    ctx = ai_prompts.build_schema_context_from_cache(_COLUMN_DETAILS, _FOREIGN_KEYS)
    assert "orders" in ctx
    assert "customers" in ctx


def test_build_schema_context_from_cache_respects_explicit_table_list():
    ctx = ai_prompts.build_schema_context_from_cache(_COLUMN_DETAILS, _FOREIGN_KEYS, tables=["orders"])
    assert set(ctx.keys()) == {"orders"}


def test_build_schema_context_from_cache_caps_table_count(monkeypatch):
    monkeypatch.setattr(ai_prompts, "_MAX_CACHE_SCHEMA_TABLES", 1)
    ctx = ai_prompts.build_schema_context_from_cache(_COLUMN_DETAILS, _FOREIGN_KEYS)
    assert len(ctx) == 1


def test_build_schema_context_from_cache_empty_input_returns_none():
    assert ai_prompts.build_schema_context_from_cache(None) is None
    assert ai_prompts.build_schema_context_from_cache({}) is None
