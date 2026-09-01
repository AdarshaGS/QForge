# QForge — AI Flush Context

Leave an accurate, **concise** handoff here when work pauses — a few bullets
per item, not exhaustive prose. This file tracks *current state*, not
history: full root-cause writeups and verification detail belong in the
relevant GitHub issue's comments (posted as each fix lands) and in commit
messages once committed — don't duplicate them here. Replace stale sections
outright rather than appending to them. Do not record passwords, tokens,
hostnames, customer data, or unredacted SQL.

## Current state (as of 2026-08-30)

**Branch:** `master`, just merged `origin/master` (16 commits, tags
v1.4.0/v1.4.1 — Data Compare, Command Palette, SQL editor go-to-def/quick-
fixes, schema-aware completer, a security-audit pass on identifier
escaping, mock data generator) together with a local-only commit
(`4669554`, offline license grace period) that had never been pushed. One
real conflict, in `main.py` (two independent new imports at the same
line) — resolved by keeping both. `git status` is the source of truth for
exactly which files are dirty — this section is a summary, not a
substitute.

**This session's work** — MySQL BLOB/decode safety, reconciled against an
independently-landed fix for the *same* bug found upstream mid-session:
- Two real MySQL decode bugs (not just cell values — `_read_row_from_packet`,
  already patched pre-session for issue #150 — but also column/table
  *names*, `FieldDescriptorPacket._parse_field_descriptor`, unpatched
  until now) in `services/db_service.py`.
- A raw BLOB column (e.g. a stored encryption key) crashed the whole grid
  load (`pandas.astype(str)` decode-crashes on real bytes) and, separately,
  got resent as garbage text in every UPDATE's `SET` clause even when a
  completely different column was edited — fixed in `ui/editable_table.py`
  by restricting `SET` to `self.modified_cells` and displaying BLOB cells
  as hex (matching TablePlus/DBeaver) via a new `_cell_display_text()`.
  Same backslash-escaping gap (MySQL treats `\` as an escape char in
  `'...'` by default) fixed in the SQL-literal builders here and in
  `utils/df_export.py`.
- **Merge conflict**: origin's `253bcac` independently fixed the same
  "WHERE clause assumed column 0 was the primary key" bug this session
  also fixed, with a different API (`set_primary_key_columns()` +
  `_key_column_indices()`, also wired into `SqlTab`'s query-result grid,
  which this session's fix didn't cover). Kept upstream's PK-lookup API as
  the base; layered this session's `modified_cells` restriction and
  backslash-escaping on top, since upstream's fix had neither. Deleted the
  now-redundant duplicate helpers (`_where_clause_columns`, the plain
  `_sql_literal` static method) that the merge left standing.

**Still uncommitted, pre-dating this session** (unrelated, appeared mid-
prior-session):
- `ui/connection_panel.py` — in-progress fix for #179 ("Open in New Tab"
  throws TypeError): `force_new` param on `open_table_view()`.
  Unfinished/untested as of this note.
- `tests/test_tab_cap.py` — untracked test file for #154 (already
  closed); unclear if still needed or superseded.
- `benchmarks/results/history.jsonl` — modified; a benign byproduct of
  running the local benchmark suite.

## Open threads not yet started

- Verdict needed on committing this session's decode/BLOB-safety fixes,
  and on finishing/testing/committing the pre-existing #179 fix.
- Remaining VAPT milestone tickets (#113–118, #139–141) — still open,
  untouched, status not rechecked this session.
- Whether a real production license activation with an actually-issued
  signed key has happened — still unconfirmed as of the last few flushes.

## Exact next step

1. Run the full test suite post-merge, then ask the user whether to
   commit (a) this session's BLOB/decode fixes and (b) the pre-existing
   #179 fix, separately or together.
2. Otherwise, ask what's next — no other session work is mid-task.

## Where the detail lives

Root-cause writeups, exact code changes, and verification steps for past
fixes are in each issue's GitHub comments and in commit messages. `git log`
and `gh issue view <n> --comments` are the source of truth for history —
this file is not.
