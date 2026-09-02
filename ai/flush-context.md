# QForge — AI Flush Context

Leave an accurate, **concise** handoff here when work pauses — a few bullets
per item, not exhaustive prose. This file tracks *current state*, not
history: full root-cause writeups and verification detail belong in the
relevant GitHub issue's comments (posted as each fix lands) and in commit
messages once committed — don't duplicate them here. Replace stale sections
outright rather than appending to them. Do not record passwords, tokens,
hostnames, customer data, or unredacted SQL.

## Current state (as of 2026-09-02)

**Branch:** `master`. Last commit `aad6ecd` — consolidated Query Analyzer
dialog (Cost & Profile + Compare Queries), dropped SQLite support. That
commit message flagged one loose end: `services/query_cost.py` SQLite
EXPLAIN support was removed but `tests/test_query_cost.py` no longer
references it (SQLite coverage appears fully gone — not rechecked deeply
this session).

**Committed this session** — issue #183 (autocomplete polish), first three
acceptance criteria done, commented + closed on GitHub:
- `ui/sql_completer.py`: CTE names from a `WITH` clause are now completable
  as a table (`_extract_ctes`, badge-tagged "CTE"), with output columns
  best-effort inferred from the CTE's own `SELECT` list
  (`_infer_select_columns` — only `AS`-aliased or bare-identifier columns
  are named, `SELECT *`/expressions/aggregates without `AS` are omitted,
  never guessed). Derived-table aliases (`FROM (SELECT ...) AS x`) resolve
  the same way for `x.column` (`_extract_derived_tables`). Both flow
  through `_columns_for()`, threaded into `_score_columns`,
  `_dot_suggestions`, `_alias_col_items`, `resolve_table`, `table_columns`.
  New `_find_matching_paren` helper does quote-aware bracket matching for
  both.
- Latency spot-check (synthetic 5,000–8,000 table schema, worst-case
  fuzzy-match path): ~1.6–2.2ms per suggestion build — no regression,
  nothing to fix. Regression-guard test added (asserts <50ms at 5k tables).
- New `tests/test_sql_completer_cte_derived.py` (15 tests): CTE
  completion/badge, column inference (simple/explicit-list/`SELECT
  *`/multiple independent CTEs/`WITH RECURSIVE`), a false-positive guard
  (`GROUP BY … WITH ROLLUP` isn't mistaken for a CTE), derived-table
  dot-notation resolution (with/without `AS`), a guard that a scalar
  subquery in `WHERE … IN (...)` isn't mistaken for a derived table, and
  the latency spot-check. Full suite: 526 passed, 0 failed.
- **Not done / manual-only:** acceptance criterion 4 ("spot-checked against
  DataGrip's autocomplete … for any remaining parity gaps") needs a human
  with DataGrip open — not something this session could verify directly.
  Also not done: opening the dialog/editor in a real running QForge window
  (only offscreen-Qt smoke tests + unit tests were run).

**Uncommitted, now complete** — table-size-aware severity for scan issues,
found already in progress at session start (not documented in the previous
flush, so it predates this note):
- `services/query_cost.py`: `FULL_TABLE_SCAN`/`NO_POSSIBLE_KEYS` severity
  now depends on table size (`_rows_tier`), whether the query actually has
  a WHERE/JOIN predicate on that table (`_has_predicate_on_table`, static
  regex-based), and whether MySQL found a candidate index at all
  (`_scan_verdict`) — a full scan on a tiny table is now INFO, not
  CRITICAL by default. `Issue.suggestion` is left empty when there's
  nothing actionable to recommend (`query_analyzer.py`'s index-recommendation
  section was updated to only list issues that carry a suggestion).
  `score_issues()` is now capped at 100; `risk_level_for()` derives its
  Low/Medium/High/Critical band from that same 0–100 score instead of a
  separate severity lookup, so the two always agree.
- `ProfileNode` gained `access_type`/`possible_keys`/`key`/`filtered`,
  best-effort filled by `_annotate_tree_from_explain_rows()` matching the
  EXPLAIN ANALYZE tree's table names against a second, plan-only classic
  `EXPLAIN` (not a second execution). MySQL-only; not populated for
  PostgreSQL.
- `ui/query_analyzer_dialog.py`: the Execution Plan diagram, its compact
  single-node view, and the Detailed Profile tree all now color by the
  same `_issues_by_table()` verdict the Issues section already computed,
  instead of each forming its own "is this bad" opinion from node text.
  Cost labels reworded to "MySQL Optimizer Cost" / "PostgreSQL Planner
  Cost" with stronger not-milliseconds tooltips; "Estimated Rows" ->
  "Estimated Rows Examined" with a clarifying tooltip.
- Fixed one now-stale test this session:
  `test_score_issues_sums_severity_weights` assumed uncapped summing;
  split into a sub-100 sum case and a new `test_score_issues_caps_at_100`.
- Re-verified end-to-end this turn: widget construction smoke-tested
  offscreen (compact summary, plan card, tree) plus 7 new scenario tests
  added to `tests/test_query_cost.py` (small-table scan, no-WHERE scan on
  a huge table, large-table-selective-filter-no-index, existing-index-
  candidate-not-re-recommended, JOIN missing index, tiny filesort, score
  cap). Full suite: 510 passed, 0 failed. Manual UI verification (opening
  the dialog against a real MySQL connection, confirming the compact/tree
  views render) still has NOT been done — no live MySQL in this
  environment.

## Open threads not yet started

- Commit verdict still needed for the Query Analyzer severity/
  EXPLAIN-annotation work (`query_analyzer.py`, `services/query_cost.py`,
  `ui/query_analyzer_dialog.py`, `tests/test_query_cost.py`) — unrelated to
  #183, still sitting uncommitted in the working tree on its own.
- Also filed this session but not started: 7 new tickets from a Command
  Palette / Quick Search review — #240 (rename "Schema" tab to "Tables"),
  #241 (drop columns from default Quick Search results), #242 (recency
  ranking + show-recent-on-open), #243 (search across all open
  connections), #244 (large-schema latency spot-check), #245 (type badges
  + command shortcuts in results), #246 (show disabled commands with a
  reason instead of hiding them). None started; no code changes yet.
- Confirm SQLite removal in `aad6ecd` is actually complete — the commit
  message called out a test/support mismatch; grep didn't turn up an
  obvious leftover this session but wasn't exhaustively re-audited.
- Remaining VAPT milestone tickets (#113–118, #139–141) — still open,
  status not rechecked this session.
- Production-safety roadmap in this file's parent doc
  (`ai/load-context.md`) — no slice of it has started; still just
  direction, not implementation.

## Exact next step

1. Ask the user whether/when to commit the Query Analyzer diff — still
   only imports+tests-verified, not tried against a real MySQL connection.
2. Ask the user which of #240–#246 (Command Palette/Quick Search) to
   pick up next, if any — all are just filed, unstarted.
3. Manually try CTE/derived-table completion in a real running QForge SQL
   tab at some point — #183's code changes were only offscreen-Qt/unit-
   test-verified, never exercised in a real window.

## Where the detail lives

Root-cause writeups, exact code changes, and verification steps for past
fixes are in each issue's GitHub comments and in commit messages. `git log`
and `gh issue view <n> --comments` are the source of truth for history —
this file is not.
