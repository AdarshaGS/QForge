# QForge — AI Load Context

Read this file before planning or changing QForge, especially work related to
production-database safety. It describes the project, current constraints, and
the intended safety direction. It is context, not a request to implement every
item below.

## Project at a glance

QForge is a PySide6 desktop SQL client for MySQL, PostgreSQL, and SQLite. Its
main capabilities include connection profiles (including SSH tunnels), a schema
browser, SQL editor and completion, tabbed results, table-data editing,
imports/exports, SQL snippets, query history, query comparison, and a macOS
app build path.

The user wants QForge to become safe and pleasant to use against real staging
and production databases, without making the normal local-development workflow
frustrating.

## Important code map

| Area | Primary files | Responsibility |
| --- | --- | --- |
| Application shell | `main.py` | Top-level windows, menus, connections, shortcuts, session restoration |
| Connection profiles | `ui/connection_dialog.py` | Create, group, save, test, and select profiles |
| Connection workspace | `ui/connection_panel.py` | Schema browser, tabs, query dispatch, background workers, table actions |
| SQL editor and results | `ui/sql_tab.py` | Editor, results, editing, import/export, filters, query controls |
| Database access | `services/db_service.py` | Drivers, SSH tunnels, execution, reconnects, schema metadata |
| Query history | `services/query_history.py` | JSON-backed executed-query history |
| Credentials | `utils/credential_manager.py` | Keyring or encrypted credential fallback |
| Logs | `utils/logger.py` | Application logging |

## Current behaviour to preserve

- Supports MySQL, PostgreSQL, and SQLite.
- A query can be selected text, the statement at the cursor, or editor text.
- Multi-statement SQL is split with `sqlparse` and each statement is run.
- Result editing can generate data-changing statements; users can commit or
  revert tracked changes.
- Users may import data into a table and create or alter tables from the UI.
- Existing profiles should keep working without requiring a migration unless
  the code explicitly includes a safe migration path.
- Passwords are handled separately from connection metadata. Do not add
  passwords to logs, query history, exported workspace files, or UI messages.

## Production safety: recommended scope

Implement in small, independently testable slices. The following is the
recommended order, not a mandate to build everything at once.

### 1. Environment classification and visible status

Add an explicit profile-level environment such as `local`, `development`,
`staging`, or `production`; do not infer it only from a hostname. Preserve a
safe default for older profiles (for example, `development` or `unclassified`)
and let the user choose production intentionally.

Show the active environment clearly in the connection tab and workspace. Use a
strong, accessible visual treatment for production, but do not rely on colour
alone. Ensure the environment travels with the profile but contains no secrets.

### 2. Read-only mode

Support a connection/profile mode that blocks writes inside QForge. It should
cover direct SQL, multi-statement scripts, result-grid commits, CSV imports,
and schema creation/alteration. Ideally use database-native read-only session
settings where each engine supports them, plus a client-side guard for clear
feedback and consistent UI behaviour.

Never describe client-side statement parsing as a security boundary: database
permissions remain the real enforcement mechanism.

### 3. Dangerous-query guard

Classify SQL statements before sending them to the database. At minimum flag:

- `DELETE` or `UPDATE` without a meaningful `WHERE` clause;
- `DROP`, `TRUNCATE`, `ALTER`, `CREATE`, `RENAME`, and `GRANT`/`REVOKE`;
- mass writes, where a reliable row-count estimate is available;
- DDL and writes hidden in multi-statement scripts.

For production, require a confirmation dialog that shows the exact statement,
target environment, and why it was flagged. Strong confirmations may require
typing the connection name or a deliberate phrase for destructive DDL. Provide
an explicit user preference only when its risk and scope are unambiguous.

Do not attempt to parse SQL with a simple string prefix. Account for comments,
whitespace, CTEs, quoted identifiers, and multiple statements. Use a parser or
a conservative classifier; when uncertain, choose the safer path and explain
why.

### 4. Transaction controls

Expose the active autocommit state and make transactions intentional. For
supported databases, allow users to begin, commit, and roll back a transaction
from the current connection or query session. Make transaction state highly
visible and avoid silently committing a group of changes.

Before changing existing execution behaviour, understand that current MySQL
and PostgreSQL connections use autocommit and SQLite commits after non-result
statements. A design must preserve today’s normal workflow or provide a clear,
tested migration.

### 5. Query limits, timeout, and cancellation

Provide per-profile defaults for query timeout and maximum returned rows, with
an intentional way to override them. Do not rewrite a query with `LIMIT` if it
would change semantics, and distinguish result limits from server-side execution
timeouts. Retain cancellation, while documenting that driver/database support
varies.

### 6. Audit trail

Record safety-relevant actions locally: timestamp, profile name, environment,
statement classification, confirmation outcome, duration, affected-row count
when available, and error/success status. Never store credentials. Consider a
redaction policy before persisting full SQL because SQL may contain personal or
confidential data.

## Non-negotiable design principles

1. Database permissions are the final authority; client safeguards reduce
   accidents but must not be presented as access control.
2. Protect production by default while keeping local development quick.
3. Do not bypass guards through alternate entry points such as imports, bulk
   commits, schema dialogs, or multi-statement execution.
4. Explain why an action is blocked or requires confirmation, and offer a
   clear cancel path.
5. Keep confirmation UI precise: show the database/profile/environment and
   the exact action being approved.
6. Store no secrets in logs, history, telemetry, or new settings files.
7. Avoid breaking existing user connection files and workflows.

## Before changing code

0. If `graphify-out/graph.json` exists, orient with `graphify query "<topic>"`,
   `graphify path "<A>" "<B>"`, or `graphify explain "<concept>"` before
   grepping/reading whole files — these return a scoped subgraph with
   `source_location` citations. Treat a citation as "as of the last commit,"
   not ground truth for anything already edited this session.
1. Read the relevant files in the code map and trace every write entry point.
2. Check the working tree with `git status --short`; unrelated edits belong to
   the user and must be preserved.
3. Write a short implementation plan naming affected entry points and how each
   guard will apply.
4. Decide which database dialects are supported in this slice and make any
   limitations visible in the UI and documentation.
5. Update `ai/flush-context.md` before handoff or when the work pauses.

## Definition of done for a safety slice

- Every in-scope write path is guarded consistently.
- The behaviour is covered by focused tests where practical, plus a documented
  manual verification path for UI flows.
- Existing connection profiles remain readable.
- Errors and cancellation do not accidentally run or commit work.
- README or in-app help documents user-visible safety behaviour.
- `ai/flush-context.md` records the implementation, decisions, verification,
  outstanding risks, and exact next step.
