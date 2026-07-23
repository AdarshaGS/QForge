# QForge — AI Flush Context

Use this file to leave an accurate handoff when work on QForge pauses or ends.
It is deliberately a living record: replace the template sections with concrete
facts from the current task. Do not record passwords, access tokens, private
hostnames, customer data, or unredacted sensitive SQL.

## Current handoff

**Status:** The working tree has a large batch of *uncommitted* changes that are
unrelated to the production-safety initiative below — see "In-flight
uncommitted work" first, since it's what actually needs review/finishing.
The write-entry-point inventory from the previous handoff is preserved below
for when safety-slice work resumes, but **no safety-slice code has been
written yet** — the previous note's claim that Slice 2 was ready to implement
was aspirational (a plan existed in an external tool path,
`~/.gemini/antigravity-ide/brain/.../implementation_plan.md`, not in this
repo), not code in this tree.

**Last updated:** 2026-07-23.

**Goal:** Add production safety controls to QForge in small, safe slices (see
`ai/load-context.md`).

**Next recommended slice:** Still Slice 1/2 as `ai/load-context.md` describes
(environment classification, then read-only mode + dangerous-query guard via
a `QueryClassifier`). Nothing in the current uncommitted work overlaps with
or blocks this — start it once the in-flight work below is committed or
otherwise resolved, to avoid tangling an unrelated large diff with a new
safety feature.

## In-flight uncommitted work (as of 2026-07-23, not yet committed)

Unrelated to the safety initiative; appears to be finished feature/refactor
work sitting in the working tree. Verify and commit (or otherwise resolve)
before starting new work, so the safety slice lands as a clean diff.

- **Credential migration to OS keychain** — `utils/credential_store.py` (new)
  wraps `keyring`; `ui/connection_dialog.py` now assigns each connection a
  stable `id`, resolves passwords through the keychain, and writes
  `connections.json` with `password` fields blanked out. `query_analyzer.py`
  (the CLI companion script) was updated to fall back to the keychain via
  `_resolve_password()` when a profile has no plaintext password. Legacy
  plaintext passwords already on disk are migrated to the keychain on first
  load of the connection dialog.
- **Schema-snapshot concurrency fix** — `services/schema_snapshot.py` (new)
  fixes a real bug: sharing one DB-API connection across threads corrupted
  result sets (MySQL version string leaking into the Tables list, duplicated
  Views). It opens its own throwaway `DbService` connection per snapshot and
  is meant to be called from a background thread. `main.py` and
  `ui/connection_panel.py` were updated so the server version now arrives via
  `panel.label_changed` (from the schema-load result) instead of a separate
  racy fetch on the connect path.
- **Shared DataFrame export helper** — `utils/df_export.py` (new), CSV/JSON/
  Excel/SQL-insert export, consolidated out of `ui/table_view_widget.py` /
  `ui/sql_tab.py` (hence those files shrinking in the diff).
- **`ui/table_view_widget.py`** was substantially reworked; `.bak` backup file
  deleted (`ui/table_view_widget.py.bak`).
- **Logging cleanup** — `services/query_history.py` now logs load/save
  failures via `utils/logger` instead of silent `except: pass` / `print()`.
- **Dev tooling added**: `pyproject.toml` (ruff config), `requirements-dev.txt`
  (pytest), `.github/workflows/tests.yml` (CI running `pytest tests/`), and
  new tests: `test_db_service_sqlite.py`, `test_df_export.py`,
  `test_query_verifier.py`, `test_schema_snapshot.py`.
- **`requirements.txt`** gained `keyring>=25.0.0`.
- Not yet verified in this environment: this session's Python (`python3` /
  Homebrew 3.14) doesn't have `pytest` installed, so the new test suite has
  not actually been run here. Confirm it passes (with the right interpreter/
  venv) before committing.
- Note: `services/query_verifier.py` (existing file, one-line diff casting
  `limit` to `int`) is a query-optimization A/B comparator (original vs.
  optimized query result diffing) — despite the similar name, it is *not*
  related to the dangerous-query classifier/guard described below. Don't
  conflate the two when starting Slice 2/3.

## Known implementation context

- QForge is a PySide6 desktop database client for MySQL, PostgreSQL, and SQLite.
- Connection metadata is managed by `ui/connection_dialog.py`.
- SQL is dispatched from `ui/connection_panel.py`; low-level execution is in `services/db_service.py`.
- Complete inventory of write entry points below (Editor execution, Multi-script, Table Grid commits, Create/Alter table dialogs, CSV import) — carried over from the previous handoff; re-verify line numbers before relying on them, since the table-view/panel refactor above may have shifted them.
- Connection passwords are now stored via `utils/credential_store.py` (OS keychain), not `utils/credential_manager.py` as the previous handoff said — `connections.json` no longer holds plaintext passwords for non-SQLite connections. Never copy passwords into profile JSON or logs.
- The worktree may contain user edits unrelated to this feature. Preserve them.

## Write Entry Point Inventory

| Entry Point | Location | Mechanism | Operation Type |
| --- | --- | --- | --- |
| **SQL Editor Single Query** | `ui/connection_panel.py:77` | `QueryWorker` -> `DbService.execute_query()` | Ad-hoc DML/DDL/DQL |
| **SQL Editor Multi-Script** | `ui/connection_panel.py:70` | `QueryWorker` -> `DbService.execute_multi_query()` | Multi-statement DML/DDL |
| **Direct Update Helper** | `services/db_service.py:449` | `DbService.execute_update()` | DML / DDL |
| **Result Grid Edits (Save)** | `ui/table_view_widget.py:774` | `TableViewWidget.commit_changes()` -> `execute_update()` | Inline UPDATE, INSERT, DELETE |
| **Result Grid Edits (Panel)** | `ui/connection_panel.py:742` | `ConnectionPanel._execute_commit_sql()` -> `execute_update()` | Inline UPDATE, INSERT, DELETE |
| **Create Table Dialog** | `ui/connection_panel.py:1144` | `StructureEditorDialog` -> `execute_update()` | DDL (`CREATE TABLE`) |
| **Alter Table Dialog** | `ui/connection_panel.py:1285` | `AlterTableDialog` -> `execute_update()` | DDL (`ALTER TABLE`) |
| **CSV Data Import** | `ui/connection_panel.py:1364` | `_import_csv_into_table()` -> `cursor.executemany()` | Batch `INSERT` |

## Exact next step

1. Review and commit (or otherwise resolve) the in-flight uncommitted work
   listed above — run the test suite with a Python environment that has
   `pytest` installed first.
2. Then start the production-safety work from a clean tree: implement
   environment classification (Slice 1) or, if that's judged already
   sufficient/skippable, go straight to `services/query_classifier.py` plus
   profile read-only toggles and safety guards in `services/db_service.py`
   and `ui/connection_panel.py` (Slice 2/3), per `ai/load-context.md`.
