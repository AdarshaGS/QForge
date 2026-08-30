# Data Compare — design note (issue #202)

Row-level data diff across two connections, sitting alongside Schema Compare
(`services/schema_diff.py` / `ui/schema_compare_dialog.py`, structural diff)
and Query Verifier (`services/query_verifier.py`, row diff but single
connection). Data Compare is the missing combination: row-level, cross
connection. Part of milestone #25 / parent issue #197.

## Comparison modes (in scope for v1)

Two modes, one engine (`services/data_diff.py`) underneath:

- **Table mode** — pick a table on the source connection and a table on the
  target connection (names need not match). Resolves internally to
  `SELECT * FROM <table>` on each side.
- **Query mode** — type an arbitrary SELECT per side, mirroring how
  `QueryVerifier` already compares two queries, but here each query runs
  against its own connection instead of a shared one.

Both modes feed the same diff engine, since the engine only ever sees "a SQL
string per side" — table mode is just a convenience that fills that string in
for the user.

## Key-matching strategy

Row identity is never positional — two independently ordered result sets
from different databases will not line up row-for-row (this is the specific
trap `QueryVerifier`'s single-connection design avoided by only ever
comparing queries against one already-consistent connection). Key column(s)
are **optional**, with two matching modes depending on whether they're given:

- **Key given** (one or more columns, comma-separated for composite keys):
  rows are matched by those column(s) only. A match with any other column
  differing is reported as "modified" with old→new per changed cell. Table
  mode pre-fills the key field from the source table's primary key
  (`db.get_primary_keys(table)`) when one exists, but the user can override
  it (e.g. a natural/business key instead of a surrogate PK, or when the two
  tables' PK columns don't share a name). Query mode has no reliable default
  — an arbitrary SELECT has no schema metadata to draw a PK from.
- **No key given** (the field left blank): falls back to whole-row
  matching — every common column together stands in as the key, so two rows
  only compare equal when *every* value matches. This is still not
  positional: it's a multiset comparison (`collections.Counter` over each
  side's stringified rows), the same symmetric-diff idea
  `QueryVerifier.verify` already uses via `value_counts`, so ordering never
  affects the result and exact duplicate rows are still counted correctly
  (3 copies of a row in source vs. 2 in target reports 1 extra removed, not
  a wholesale collapse). There is no "modified" bucket in this mode — by
  construction, two rows sharing a whole-row key cannot differ — so the
  fallback only ever reports added/removed/unchanged.

## Large-table scope

Two independent full-table loads is the failure mode to design out.

- Each side's SQL is fetched via `DbService.execute_query(sql,
  max_rows=N)` (`services/db_service.py:565`), which caps rows pulled off
  the wire (`cursor.fetchmany`) rather than the driver fetching everything.
- Default cap: **50,000 rows per side**. An explicit "Full table (no
  limit)" checkbox opts into fetching everything, with a confirmation
  warning before running.
- Rows are fetched `ORDER BY <key columns>` (ascending) before the cap is
  applied, so a capped run takes a deterministic slice — the rows with the
  lowest key values — rather than whatever order the database happens to
  return. This is the same reasoning `QueryVerifier._maybe_limit` uses when
  wrapping a query in a `LIMIT` subquery.
- **Known limitation**: when either side is truncated, rows past the cap on
  that side are simply absent from the comparison — a genuine mismatch that
  only exists beyond the cap will not be reported. The UI surfaces this via
  a truncation flag per side rather than pretending the diff is exhaustive.
- Diff result lists (added/removed/modified) are capped at 500 entries each
  for on-screen display, the same way `QueryVerifier.verify` caps
  `col_diff_rows` at 50 (`services/query_verifier.py:227`) — the reported
  *counts* are always exact; only the rendered list is capped.

## Gating

Pro-only, consistent with Schema Compare and Query Verifier (`Feature.
DATA_COMPARE` in `services/entitlements.py` — see DC.4 / issue #205).
