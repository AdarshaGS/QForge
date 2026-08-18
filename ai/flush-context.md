# QForge — AI Flush Context

Leave an accurate, **concise** handoff here when work pauses — a few bullets
per item, not exhaustive prose. This file tracks *current state*, not
history: full root-cause writeups and verification detail belong in the
relevant GitHub issue's comments (posted as each fix lands) and in commit
messages once committed — don't duplicate them here. Replace stale sections
outright rather than appending to them. Do not record passwords, tokens,
hostnames, customer data, or unredacted SQL.

## Current state (as of 2026-08-19)

**Branch:** `master`. `git status` is the source of truth for exactly which
files are dirty — this section is a summary, not a substitute.

**Security VAPT #161–163 (this session)** — closed. Full hacker-style pass
turned up three real findings, all fixed, tested, and commented+closed on
GitHub: sandbox-escapable `eval()` in cell formulas (`ui/editable_table.py`,
replaced with an AST-whitelisted `_safe_eval_arithmetic`, reachable via
DB-sourced content through bulk-column-edit's `{value}` substitution);
unescaped identifiers in exported `.sql` (`utils/df_export.py`
`_quote_identifier` now doubles embedded backtick/quote); Zip Slip via
unsanitized table names as zip entry paths (`ui/connection_panel.py`, new
`_safe_zip_entry_name`). 10 new regression tests added; full suite is 323
passing. **Not committed** — user chose not to commit yet this session.
Found but not filed: `stream_table_rows()` (`services/db_service.py:715`)
builds `SELECT * FROM {table_name}` unquoted across the file — not itself
exploitable (breaks the query before reaching anything unsafe) but worth
its own ticket if pursued; overlaps existing **#114** ("Audit
Identifier/Value Escaping") scope.

**Licensing (Milestone #12, #81–#88)** — all done and, unusually, partly
**committed**: the user has been approving narrow single-file commits
rather than lifting the hold wholesale. Landed on `master`: `c7a9e3e`
(`pricing_url` → the deployed `qforge-licensing` pricing page, closing
#145) and `31e7b68` (rotated the license-verification public key to match
the keypair Railway's signing key actually holds). Still uncommitted: rest
of the licensing-integration layer (`main.py`,
`services/entitlement_config.py`, `services/licensing_client.py`,
entitlement/license test files). **Not yet verified**: a real activation
against production with an actually-issued signed license (needs the
private key, kept outside the repo).

**Export dialog** (#156–#160, closed prior session) — per-table S/C/D
grid, format tabs, streaming backend, indexes/FKs in Structure export
(SQLite/Postgres), perf fixes (`_sql_value_literal` reorder,
`gzip` compresslevel=1). None of this committed yet.

**Postgres/SQLite test coverage** — `_is_connection_error` doesn't match
psycopg2's "connection already closed" message (only the realistic
server-killed-session case). Found, not fixed.

## Open threads not yet started

- Commit strategy for everything uncommitted (security fixes, export
  perf/UI work, rest of licensing-integration layer) — user's call each
  time; narrow per-file/per-epic commits have been the pattern.
- A full activation against production with a real signed license (#88
  follow-up) — needs the private signing key.
- Pick a payment provider for `qforge-licensing`'s `/checkout`; wire
  `/webhook/purchase`; flip `QFORGE_CHECKOUT_MODE` to `"payment"`.
- **#144** — decide "Advanced schema tools" Pro-gate scope.
- **#114, #115–118, #139, #140** — remaining open VAPT milestone (#10)
  tickets, untouched this session.
- **Production-safety Slices 5–6** (`ai/load-context.md`) — unchanged.
- **#54, #75** — still on hold. **#61, #79, #66/#67** — still need a
  status recheck.
- The `_is_connection_error` gap (Postgres) — undecided whether worth
  fixing.

## Exact next step

1. User's call on committing the accumulated uncommitted work (security
   fixes + export + licensing) — ask before assuming scope.
2. A full activation against production with a real signed license.
3. Pick a payment provider for `qforge-licensing`'s `/checkout`.

## Milestone status

**#12**: #81–#88 done and verified; #144 still open. #145 closed.
**Export epic (#156–#160)**: closed prior session. **Security VAPT (#10)**:
#161–163 closed this session; #113–118, #139, #140 still open.

## Where the detail lives

Root-cause writeups, exact code changes, and verification steps for past
fixes are in each issue's GitHub comments and in commit messages. `git log`
and `gh issue view <n> --comments` are the source of truth for history —
this file is not.
