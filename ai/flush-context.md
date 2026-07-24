# QForge — AI Flush Context

Use this file to leave an accurate handoff when work on QForge pauses or ends.
It is deliberately a living record: replace the template sections with concrete
facts from the current task. Do not record passwords, access tokens, private
hostnames, customer data, or unredacted sensitive SQL.

## Current handoff

**Status:** On branch `Qforge_2` (working tree otherwise clean at session
start except an untracked `logo.png`). Production-safety Slices 1, 2, and 3
from `ai/load-context.md` are implemented and verified this session.
**Nothing described below is committed yet** — all of it is sitting in the
working tree, uncommitted, ready for review.

**Last updated:** 2026-07-24.

## App icon / branding (uncommitted)

`logo.png` (repo root, provided by the user) is now wired up as the app's
icon everywhere:
- `main.py`: `app.setWindowIcon(QIcon(_asset_path("logo.png")))`, where
  `_asset_path()` resolves correctly both running from source and inside a
  frozen PyInstaller bundle (`sys._MEIPASS`).
- `QForge.spec`: `logo.png` added to `datas` (bundled for the runtime QIcon
  load above); macOS `BUNDLE()` icon set to `assets/icon.icns`; Windows
  `EXE()` icon set to `assets/icon.ico`.
- `assets/icon.icns` / `assets/icon.ico` were generated from `logo.png`
  (padded to a transparent square, multi-resolution) via a throwaway Pillow
  install in a temp venv — not a new project dependency.
- Verified via a real `./build.sh` run: `Info.plist`'s `CFBundleIconFile`
  correctly references `icon.icns`, the built `.app` launches cleanly
  offscreen, and unfrozen `python3 main.py` also resolves a real (non-null)
  icon.
- **`logo.png` must not be deleted** — it's the live source both `main.py`
  and `QForge.spec` read from, not just the source the `.icns`/`.ico` were
  derived from once.

## Slice 1: Environment classification & visible status (done, verified)

Every connection profile now has an explicit environment tier —
`unclassified`/`local`/`development`/`staging`/`production` — never
inferred from hostname, with `unclassified` as the safe default for
profiles that predate this field.

- New `utils/environment.py`: the tier list, `COMBO_LABELS`/`BADGE_LABELS`,
  and `normalize()` (the single fail-safe choke point every reader goes
  through — a missing/corrupt value always becomes `unclassified`, never
  guessed).
- `ui/theme_manager.py`: 30 new `D_ENV_*`/`L_ENV_*` tokens (bg/text/border
  × 5 tiers × 2 themes) plus `ThemeManager.env_colors(env, is_dark)`.
  Development deliberately got its own new blue-gray tone (not a reuse of
  Local's green or Staging's amber) — a user decision, not a guess.
- `ai/ui-design.md`: environment table extended with Development,
  Unclassified, and a light-theme column (previously dark-only, 3 tiers).
- `ui/connection_dialog.py`: Environment `QComboBox` in the connection
  form; legacy profiles backfilled to `unclassified` via the existing
  `_resolve_credentials()` resave mechanism (same pattern already used for
  backfilling missing `id`s); connection tree shows a colored tier suffix
  (omitted for `unclassified`, so old profiles look unchanged in the list).
- `ui/connection_panel.py`: a persistent colored workspace badge above the
  query tabs (always visible, including "ENVIRONMENT NOT SET"); `label`
  property embeds the tier text, which propagates automatically into the
  tab bar and window title (both already re-read `panel.label` fresh, so
  no `main.py` changes were needed).
- `README.md`: new "Classify a connection's environment" section.
- Tests: `tests/test_environment.py` (7 tests, all passing).

## Slice 2 + 3: Read-only mode & dangerous-query guard (done, verified)

Confirmed with the user before implementing:
- The new per-profile **Read-only** checkbox auto-checks itself
  (one-directional — never auto-unchecks) when Environment is set to
  Staging or Production; stays independently editable otherwise. Legacy
  profiles backfill `read_only: false` (never retroactively locks an
  existing writable connection).
- The dangerous-query confirmation dialog fires for **Staging and
  Production** (not Production-only). Local/Development/Unclassified see
  no new dialog — today's pre-existing plain confirms (create table, alter
  table, CSV import, grid-commit) are untouched for them.
- The strongest confirmation (typed input required) is gated on
  **destructive DDL — `DROP`/`TRUNCATE` specifically** — and requires
  typing the **connection's name** (not a fixed phrase).

**Architecture — two layers, one shared classifier:**

- New `services/query_classifier.py` (pure Python, no Qt/DB dependency):
  `split_statements()` (single source of truth — replaced three independent
  splitting implementations that used to exist: `_QueryWorker`'s
  `sqlparse.split` for UI routing only, `execute_multi_query`'s
  `sqlparse.split` for actual execution, and `show_alter_table_editor`'s
  naive `";\n".split()`), `classify()` (uses `sqlparse.parse().get_type()`
  with a `token_first(skip_cm=True)` fallback for `GRANT`/`REVOKE`/`RENAME`,
  which `get_type()` reports as `UNKNOWN`; WHERE-clause detection via
  `sqlparse.sql.Where` token-walking, not string search — correctly ignores
  a commented-out fake `WHERE`), and `is_dangerous()`.
- **Layer 1 — `services/db_service.py` (backstop, cannot be bypassed):**
  `connect()` stores `self.read_only`; native read-only session settings
  added per engine (MySQL `SET SESSION TRANSACTION READ ONLY`, PostgreSQL
  `connection.set_session(readonly=True)`, SQLite `PRAGMA query_only = ON`)
  — all three re-apply for free on reconnect since `_reconnect()` funnels
  through the same `connect()`. New `_guard()` method + new
  `ReadOnlyViolation` exception, called from `execute_query()`,
  `execute_update()`, and `execute_batch()` — classifies the *actual SQL
  text* rather than trusting which method the caller used (verified
  `execute_query`/`_execute_query_raw` will run ANY statement, not just
  SELECTs). `execute_multi_query()` now routes each split statement by real
  classification instead of a naive first-keyword guess, closing the
  "hidden write in a multi-statement script" gap.
- **Layer 2 — `ui/connection_panel.py` (UX: explain, confirm, cancel):**
  new `_guard_write(sql, extra_reason=None)` wired into all 5 traced write
  entry points (`_run_query_in_tab`, `_execute_commit_sql`,
  `show_structure_editor`, `show_alter_table_editor` — whose execution loop
  was also switched from naive `";\n".split()` to
  `query_classifier.split_statements` — and `_import_csv_into_table`, which
  additionally treats an import ≥`MASS_WRITE_ROW_THRESHOLD` (5,000) rows as
  a flagged "mass write" on Staging/Production even though a plain `INSERT`
  alone isn't otherwise flagged). New `ui/query_guard_dialog.py`
  (`show_read_only_blocked`, `show_dangerous_confirmation` — the latter
  gates its Confirm button on typing the connection name when
  `require_typed_name` is set). Environment badge extended to append
  "🔒 READ-ONLY" when applicable.
- `ui/connection_dialog.py`: `read_only_check` `QCheckBox` (mirrors the
  existing `ssh_enabled_check` declare/connect/addRow pattern exactly),
  auto-default wiring on `environment_input.currentIndexChanged`, wired
  into `get_form_data()`/`clear_form()`/`load_selected_connection()`,
  legacy backfill in `_resolve_credentials()`, and a `🔒` tree-row marker.

**Verified this session:**
- `py_compile` on every touched/new file.
- Full suite: **40/40 passed** (18 pre-session + 7 Slice-1 + 15
  query-classifier tests).
- `services/db_service.py` guard, offscreen, against a real SQLite file:
  SELECT still works under read-only; a plain `UPDATE` raises
  `ReadOnlyViolation`; a `DELETE` hidden as the second statement in a
  multi-statement script is still caught (not just the first statement);
  `PRAGMA query_only` is actually set on the connection; the underlying DB
  file is provably unchanged afterward; a non-read-only connection on a
  second DB file still writes normally (no false-positive blocking).
- `ConnectionPanel._guard_write`, offscreen, with the dialog functions
  monkeypatched to record calls instead of blocking on `exec()`: read-only
  blocks writes regardless of environment (including Local); Local with a
  `DROP TABLE` shows no dialog at all; Production `UPDATE` without `WHERE`
  shows the standard confirmation (no typed-name gate); Production/Staging
  `DROP`/`TRUNCATE` both require the typed-name gate; Production `UPDATE
  ... WHERE ...` (safe) shows no dialog; a user declining the confirmation
  correctly returns `False` to the caller.
- `ui/connection_dialog.py`, offscreen: a legacy connection (no
  `read_only` key) backfills to `False` and persists that to disk; a
  stored `read_only: True` profile shows the `🔒` tree marker, a legacy one
  doesn't; `clear_form()` defaults to unchecked; selecting
  Production/Staging auto-checks Read-only; switching *away* from those
  tiers does **not** auto-uncheck it; `get_form_data()` includes both new
  fields; **critically**, a stored `environment: production, read_only:
  false` profile (a deliberate choice to keep a Production connection
  writable) survives `load_selected_connection()` intact — the transient
  auto-default side effect from `setCurrentText()` firing
  `_on_environment_changed` does not clobber the real stored value, because
  the explicit `read_only_check.setChecked(...)` line runs after it.

**Known, accepted limitations (not fixed this slice, consistent with
`ai/load-context.md`'s "small, independently testable slices" framing):**
- No audit trail yet (Slice 6) — blocked/confirmed/declined actions aren't
  persisted anywhere, only shown in the moment.
- No query-limit/timeout/cancellation changes (Slice 5) — unrelated to this
  slice's scope.
- No transaction controls (Slice 4) — MySQL/PostgreSQL still autocommit;
  read-only is enforced via session-level `SET`/`PRAGMA`, not a wrapping
  transaction.
- `ConnectionPanel` reads `environment`/`read_only` once at construction
  (same pre-existing limitation Slice 1 already noted for `environment`
  alone) — editing a profile's Read-only/Environment while it's already
  open in a tab doesn't affect that open tab until reopened.
- The Connection Manager dialog (`ui/connection_dialog.py`) itself is
  unconditionally dark-styled (no light/dark toggle exists there at all,
  pre-existing) — the new checkbox/tree-marker follow that same
  convention, nothing new introduced.

## Exact next step

1. Review the uncommitted diff (`git status --short` currently shows
   `QForge.spec`, `README.md`, `ai/ui-design.md`, `main.py`,
   `services/db_service.py`, `ui/connection_dialog.py`,
   `ui/connection_panel.py`, `ui/theme_manager.py` modified, plus new
   `assets/`, `logo.png`, `services/query_classifier.py`,
   `tests/test_environment.py`, `tests/test_query_classifier.py`,
   `ui/query_guard_dialog.py`, `utils/environment.py`) and commit when
   ready — the user pushes manually.
2. Manual UI verification checklist from the approved plan
   (`~/.claude/plans/lets-keep-them-pending-parsed-cerf.md`, "Tests" section
   of the Slice 2+3 plan) still needs a real click-through pass against a
   live MySQL/PostgreSQL server — this session's verification was
   thorough at the logic/offscreen level but used SQLite for the live-DB
   guard test and monkeypatched dialogs for the UI-decision test, not a
   real interactive click-through of the typed-name dialog or a real
   MySQL/PostgreSQL `SET SESSION`/`set_session(readonly=True)` round-trip.
3. Remaining production-safety roadmap per `ai/load-context.md`: Slice 4
   (transaction controls), Slice 5 (query limits/timeout/cancellation),
   Slice 6 (audit trail) — none started.
4. Separately, the broader product roadmap
   (`~/.claude/plans/what-are-the-features-memoized-bumblebee.md`) still has
   Tier 2 mostly open (path unification via `utils/paths.py` — the actual
   blocker for Windows/Linux users finding a sane data directory — and
   extending `.github/workflows/tests.yml` into a real OS matrix with
   PySide6/`pytest-qt`); only the CI packaging item is done and CI-proven.
