# QForge — AI Flush Context

Leave an accurate, **concise** handoff here when work pauses — a few bullets
per item, not exhaustive prose. This file tracks *current state*, not
history: full root-cause writeups and verification detail belong in the
relevant GitHub issue's comments (posted as each fix lands) and in commit
messages once committed — don't duplicate them here. Replace stale sections
outright rather than appending to them. Do not record passwords, tokens,
hostnames, customer data, or unredacted SQL.

## Current state (as of 2026-09-06)

**Branch:** `master`. **Issue #250 is committed, pushed, and closed**
(commit `3ca2c31`, comment posted with implementation summary). The repo's
remote also moved to `AdarshaGS/QForge` (capitalized) during this session —
`git push` still worked via the old URL (GitHub redirects), but update the
remote if that stops working: `git remote set-url origin
https://github.com/AdarshaGS/QForge.git`.

**Issue #237 (background the remaining blocking DB calls in
ConnectionPanel) is now fully implemented and uncommitted** — all three of
the issue's priority tiers, not just the highest one; see below. `git
status` shows `ui/connection_panel.py` as `MM` (both staged and unstaged
changes) — something staged a mid-flight snapshot of this file earlier in
the session (likely alongside the #250 commit); `git diff HEAD --
ui/connection_panel.py` (staged+unstaged combined) is the authoritative
view of the real diff, not `git diff` alone.

**Pre-existing unrelated modifications still uncommitted in the working
tree** (not touched by this or any #237/#250 work, do not attribute to
either): `README.md`, `entitlements-config.json`,
`services/entitlement_config.py`, `tests/test_entitlements.py`.
`graphify-out/*` is no longer tracked by git as of this session (upstream
untracked it as a regenerable cache in a commit pulled down mid-session) —
it will still appear on disk as ignored/untracked files, that's expected.

### #237 work (uncommitted — all 3 priority tiers):
- `ui/connection_panel.py` — new shared helper `_run_bg_db(fn, on_done,
  on_error=None, config=None)`: opens a fresh *dedicated* `DbService`,
  calls `fn(dedicated_db)` on a `threading.Thread`, delivers the result/
  error back on the main thread via a generic op_id-keyed Signal pair
  (`_bg_op_done`/`_bg_op_error` → `_on_bg_op_done`/`_on_bg_op_error`
  dispatch to whichever `on_done`/`on_error` callback was registered for
  that op_id) — never touches `self.db_service`. Same dedicated-connection
  + thread + Qt-signal-bridge shape as the pre-existing
  `_spawn_schema_fetch`/`_connect_in_background`/`SchemaCompareDialog`
  pattern, genericized instead of one bespoke Signal per call site.
  - **Highest priority** (real freeze risk): `_copy_insert_script`,
    `_export_table_data_only`, `_export_table_with_column_selection` (all
    three now run their full-table SELECT via `_run_bg_db`, busy-cursor
    while in flight); CSV import's batched INSERT loop (own bespoke
    signals — `_csv_import_progress`/`_csv_import_write_done`/
    `_csv_import_write_error` — since it needs live progress + a
    `threading.Event` cancel flag, which `_run_bg_db`'s simple
    request/response shape doesn't cover); `_truncate_table`/
    `_delete_table` (DDL only — `_delete_table`'s pre-drop Impact
    Analysis dependency check is left synchronous, cheap metadata).
  - **Moderate**: `_switch_database`'s non-MySQL (Postgres) reconnect path
    — this one **does** touch `self.db_service` (switching databases *is*
    changing what it points to), so it follows `_connect_in_background`'s
    existing precedent instead: gated by `self._connecting` so nothing
    else touches `self.db_service` until `_on_db_switch_done` fires. The
    MySQL `select_db` path stays synchronous by design (already
    lightweight, per the pre-existing comment). Mock data generator's
    batched INSERT + sequence-bump loop also now runs via `_run_bg_db`.
  - **Lowest priority / cheap-but-blocking** (done for consistency, per
    the issue's own framing): `_load_databases` (now takes an optional
    `on_done(ok)` callback instead of returning a bool synchronously —
    `refresh_databases`/`create_database`/`drop_database` updated to the
    callback shape; `drop_database`'s picker split into a new
    `_show_drop_database_picker()` so the fetch can complete first when
    the list isn't already cached), `show_structure_editor`,
    `show_alter_table_editor` (columns-fetch-then-dialog restructured as
    a continuation — dialog only opens once the fetch callback fires),
    `_new_table`, `_clone_table`, `create_database`, `drop_database`.
- `ui/table_view_widget.py` — `_load_structure_tab` (Columns/Indexes/
  Foreign Keys sub-tab) now fetches via the tab's own pre-existing
  `_get_worker_db()` dedicated connection on a `threading.Thread`, same
  pattern as `load_table_data()`; split into `_load_structure_tab()`
  (worker dispatch) + `_apply_structure_result()` (fills the three tables)
  + `_on_structure_load_failed()` (warning dialog) via two new Signals.
- New tests: `tests/test_connection_panel_bg_db_237.py` (`_run_bg_db`
  itself — success + error paths, asserts the shared/stand-in
  `self.db_service` is never touched and the dedicated one is always
  disconnected) and `tests/test_table_view_widget_structure_tab_237.py`
  (`_load_structure_tab` populates the three tables via the background
  path). Both use the existing `QTest.qWait`-pump-until-flag-clears
  convention (see `test_table_view_widget_suspend_guard.py`).
- **Not modified, deliberately**: `_export_table_as_sql` — uses the same
  `_write_table_export`-based full-table read as the three "highest
  priority" methods above but isn't named in issue #237's checklist; worth
  a follow-up if the same freeze risk matters there too.
- Verified via `python3 -m py_compile` on both files + the two new test
  files, plus a careful line-by-line read of the full `git diff HEAD`
  (not just `git diff`, since part of the file was already staged — see
  above) for each of the ~15 converted call sites. **Not run through
  pytest or a real running window this session** (standing instruction) —
  no live MySQL/Postgres available in this environment either, so the
  dedicated-connection wiring is unverified against a real server.

### #250 work that was committed (`3ca2c31`):
- `services/query_cost.py` — `analyze_sql_text`/`analyze_explain_rows`/
  `analyze_mysql_profile`/`analyze_postgres_plan` now take an optional
  `schema` dict (built by new `fetch_schema_context(db_service, tables)`,
  called from `estimate_cost`/`build_profile`). Best-effort throughout —
  `schema=None` (no db_service, or introspection fails) reproduces prior
  output exactly.
  - FULL_TABLE_SCAN / NO_POSSIBLE_KEYS / SEQ_SCAN suggestions now name the
    real candidate column(s) via `_candidate_columns_for_table` /
    `_index_suggestion` instead of a generic "the column(s)" phrase, when
    schema resolves one.
  - Two new Issue codes: `TYPE_MISMATCH_PREDICATE` (WHERE/ON literal
    compared against a column of the wrong broad type — e.g. VARCHAR vs.
    a bare numeric literal) and `UNINDEXED_JOIN_KEY` (a JOIN ON column
    that's neither indexed nor a declared FK).
  - Table/alias resolution is a new best-effort regex helper,
    `_parse_table_aliases` (FROM/JOIN clauses only, conservative).
  - `query_analyzer.py` (the standalone CLI) and `services/query_verifier.py`
    were deliberately **not** modified — the CLI stays schema-blind by
    design (matches issue #250's acceptance criteria), and the verifier
    already gets schema-aware plans for free via `estimate_cost()`.
- `tests/test_query_cost.py` — 5 new unit tests (plain-dict `schema`
  fixtures, no live DB) covering: real-column-name suggestion fill (MySQL
  FULL_TABLE_SCAN + Postgres SEQ_SCAN), `TYPE_MISMATCH_PREDICATE` (positive
  + negative), `UNINDEXED_JOIN_KEY`.
  - Caught and fixed one real regex bug while smoke-testing by hand (not
    pytest, per standing instruction): a trailing `\b` right after a
    symbol operator (`=`, `<`, …) in the bare-column WHERE-clause regex
    inside `_candidate_columns_for_table` never matches, since neither
    side of `=`→space is a word-boundary transition — silently made every
    single-table candidate-column list empty. Fixed by dropping the
    trailing `\b` for symbol operators (kept for `LIKE`/`IN`).

Verified via `python3 -m py_compile` + ad hoc script execution (not
pytest, per standing instruction) exercising all four new code paths
directly. Not exercised through the UI (Analyze Query dialog / SQL editor
status-bar badge) this session.

**Earlier this session — `f3d2b3d` (#236, #263):** Impact Analysis
(`services/dependency_analyzer.py`, `ui/dependency_dialog.py` — FK/view/
function/procedure/trigger dependents for a table or column, schema-tree
context menu, automatic pre-DROP warnings, Pro-gated) and the schema-tree
loading progress bar (`ui/connection_panel.py`, replacing the old
ticking-text tree row). Both closed (#263 was already closed on GitHub
before this session found the code).

**Earlier this session — three commits, split out of one heavily entangled
uncommitted tree via hash-object/update-index surgical staging:**
1. `4d8bbc2` — grid/table-view polish cluster: **#182** (NULL-vs-empty
   rendering, type-aware alignment, fill-down, column layout persistence;
   #182 itself is a P2 tracking issue, left **open**, commented only),
   **#251** (rows-per-page, closed), **#252** (column visibility, closed),
   **#253** (scrollbar fix, was already closed), **#259** (Raw SQL
   filter-row entry, was already closed), **#262** (progress bar + Refresh
   button, was already closed).
2. `601ae3c` — **#260** (closed): clears the previous result grid/error
   card the moment a new run starts.
3. `986c309` — **#261** (closed): FK column header hover tooltip + a
   clickable inline nav arrow on FK cell values. A 🔗 header icon
   originally built for this was removed per user feedback before
   committing.

Verification for all of the above: `py_compile` clean, manual diff review
confirming each staged blob was isolated correctly. **None of this
session's work has been run via pytest or exercised in a real, non-offscreen
window** — that's the standing gap across every commit above.

## Open threads not yet started

- **#182** is a P2 tracking issue, not a single fix — stays open. Re-read
  it before assuming the grid is "done."
- The Query Analyzer/#181 and #249 code landed uncommitted-review — if bugs
  turn up there, they predate this session's involvement; nobody
  specifically vetted that slice.
- #247, #248 — still unstarted, carried over from 2026-09-03.
- SQLite-removal audit (`aad6ecd`), remaining VAPT tickets
  (#113–118, #139–141), production-safety roadmap in
  `ai/load-context.md` — unchanged, not rechecked this session.
- `_export_table_as_sql` (connection_panel.py) has the same unbounded
  full-table-read shape as issue #237's "highest priority" tier but isn't
  named in that issue — flagged above, not fixed, not filed as its own
  issue yet.

## Exact next step

1. `git status --short` first — confirm nothing unexpected changed, and
   don't stage the pre-existing unrelated files (README, entitlements,
   test_entitlements) into a #237 commit. `ui/connection_panel.py` shows
   `MM` — use `git diff HEAD -- ui/connection_panel.py` (not plain `git
   diff`) to see the real, complete diff before committing it.
2. **#237 is implemented (all 3 tiers) but uncommitted and unverified
   against a real server** — no live MySQL/Postgres in this environment.
   Next: run `tests/test_connection_panel_bg_db_237.py` and
   `tests/test_table_view_widget_structure_tab_237.py` plus the full
   suite, then manually exercise each of the ~15 converted call sites
   against a real connection (large-table export/copy/truncate/drop, CSV
   import with cancel, switching a Postgres connection's database,
   generating mock data, create/drop database, new/alter table/clone
   table) before considering this done. Only then commit + comment/close
   on the GitHub issue.
3. **#250 is done: committed as `3ca2c31`, pushed to origin/master, GitHub
   comment posted, issue closed.** Only gap left: nothing in it has been
   run through pytest or the actual Analyze Query dialog UI — worth doing
   before relying on it further, but not blocking.
4. Note for next session: origin's remote URL case changed
   (`AdarshaGS/qforge` → `AdarshaGS/QForge`); GitHub is currently
   redirecting old pushes/fetches through fine, but if that ever breaks,
   `git remote set-url origin https://github.com/AdarshaGS/QForge.git`.
5. If bugs surface in the Query Analyzer/#181 or #249 code, treat it as
   pre-existing/unreviewed rather than assuming this session vetted it —
   it was committed as-is on explicit request, not independently checked.

## Where the detail lives

Root-cause writeups, exact code changes, and verification steps for past
fixes are in each issue's GitHub comments and in commit messages. `git log`
and `gh issue view <n> --comments` are the source of truth for history —
this file is not.
