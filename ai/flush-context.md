# QForge — AI Flush Context

Use this file to leave an accurate handoff when work on QForge pauses or ends.
It is deliberately a living record: replace the template sections with concrete
facts from the current task. Do not record passwords, access tokens, private
hostnames, customer data, or unredacted sensitive SQL.

## Current handoff

**Status:** Branch situation is now resolved. Working tree is on branch
**`Qforge_1`**, tracking **`origin/1.0.7`**, HEAD at `f0c2c0b` (already
includes the original credential-store/schema-snapshot/table-view fixes as
real committed history — not just staged). The `Qforge` vs
`adding_security_encryption_1` split described below (kept for history) was
reconciled by branching `Qforge_1` off the better base and layering this
session's work on top — verified by direct inspection that every fix from
both lineages is present together (theme restyle, `df_export.py`, the full
`tests/` suite, credential_manager removal, keychain fixes, LICENSE/
SECURITY.md, CI packaging, the table-view crash fix, the View Structure menu
fix). One additional real bug was found and fixed while verifying this
reconciled state — see "Final verification pass" below. **Everything is
staged, not yet committed as of writing this; the user will push once a
commit is made.**

**Last updated:** 2026-07-24.

**Goal:** Add production safety controls to QForge in small, safe slices (see
`ai/load-context.md`). No safety-slice code has been written yet.

## Branch situation (resolved — kept for history)

Earlier this session, discovered via `git reflog` that branch `Qforge` had
been created from commit `3dcc292` — *before* all the table-view/
credential-store/theme fixes that existed on `adding_security_encryption_1`
(tip `5ee0e56` at the time, itself never pushed anywhere). A commit `7421a43`
("Deployment v1.0.6") briefly existed on top of `5ee0e56` containing
near-duplicate LICENSE/SECURITY.md/CI-packaging work to what this session
built independently on `Qforge`, but it was `git reset` away and is now
unreachable from any branch (reflog-only, will eventually be GC'd — not
recovered, not needed since `Qforge_1`'s reconciliation covers the same
ground). User decided to merge/reconcile rather than pick one side; the
result is `Qforge_1`.

## Final verification pass before this handoff (2026-07-24)

Before committing the reconciled state, ran the full verification suite for
the first time:
- `py_compile` on every staged `.py` file — clean.
- **`pytest tests/ -v` — 18/18 passed.** This is the first time this test
  suite has ever actually been executed (previous handoffs all noted it had
  never been run for real, only offscreen Qt smoke scripts).
- Re-ran this session's offscreen smoke tests (construct `ConnectionPanel`,
  `open_table_view` twice, `_show_table_structure`) against the reconciled
  tree — **caught a real, pre-existing bug in the process**: `_on_schema_
  loaded()` in `ui/connection_panel.py` called `tab.set_schema((tables,
  columns))` on any open `TableViewWidget` tab, but that class has never
  defined `set_schema` since a much earlier rewrite (confirmed via
  `git log --all -S"def set_schema" -- ui/table_view_widget.py` — it only
  existed in ancient commits `9d3df25`/`d00a2b4`). This is **not** a
  regression from the branch reconciliation — it was already present in
  committed history at `f0c2c0b`/HEAD. It would crash (`AttributeError`)
  whenever a table-view tab was open and the schema (re)loaded — e.g. on
  refresh (`Ctrl+Shift+R`) or reconnect. Fixed by removing the dead `elif
  isinstance(tab, TableViewWidget): ...` branch entirely — `TableViewWidget`
  is a data grid, not an editor, and never needed schema-based autocomplete;
  the `SqlTab` branch (which does need it) is untouched. Re-verified after
  the fix: same smoke test now processes queued schema-load events with a
  table view open, with no crash, and the full pytest suite still passes
  18/18.

## Table-view crash + missing structure-view fix (2026-07-24, uncommitted)

User reported "table structure view isn't working" then hit a real crash
with a traceback when clicking a table in the schema browser:
`AttributeError: 'PySide6.QtWidgets.QWidget' object has no attribute
'resized'` in `ui/table_view_widget.py`'s `init_ui()`. Root cause: `Qforge`
branch's `ui/table_view_widget.py`/`ui/connection_panel.py` were byte-identical
to commit `3dcc292` — the exact "half-migrated" broken state the *original*
version of this handoff file (before this session) already described (old
"Load More" infinite-scroll code mixed with new pagination code referencing
nonexistent attributes; `w.get_table_name()` calls on a class that doesn't
define that method).

Fixed by bringing the already-verified-working fix from commit `f0c2c0b`
(made earlier this session on `adding_security_encryption_1`) forward onto
`Qforge`, rather than re-debugging from scratch:
- Added `services/schema_snapshot.py` (new file at `f0c2c0b`, the only new
  dependency `connection_panel.py`'s fixed version needs — verified via
  `git diff 3dcc292 f0c2c0b --stat` that no other files changed, and via
  import diffing that no other new dependency was introduced).
- Replaced `ui/table_view_widget.py` and `ui/connection_panel.py` wholesale
  with their `f0c2c0b` versions (confirmed byte-identical to `3dcc292`
  beforehand via `diff`, so this was a clean, non-lossy upgrade — nothing
  branch-specific was clobbered).
- Patched `main.py` to wire `panel.label_changed` (needed by the new
  `connection_panel.py`) — applied by hand on top of the current `main.py`
  (which already had this session's `Ctrl+Shift+E` shortcut fix), rather than
  overwriting the whole file, to avoid losing that fix.
- Verified via an offscreen smoke test against a real SQLite test db: constructed
  `ConnectionPanel`, called `open_table_view()` twice (exercises the
  already-open-tab lookup that used to crash) — no exception.

**Separately found and fixed**: the *actual* "table structure view isn't
working" complaint was a distinct bug, not fixed by the above. The read-only
structure viewer (`_show_table_structure()` — Columns/Indexes/Foreign Keys
tabs) was only ever wired to a Qt signal (`tv.data_table.show_structure`,
defined in `ui/editable_table.py`) that is **never emitted anywhere** — no
button, context-menu item, or shortcut fires it, in this commit or in
`f0c2c0b`. It was dead code before this session even started. Fixed by adding
a direct "🔍 View Structure" entry to the schema-browser right-click menu in
`ui/connection_panel.py`'s `_show_context_menu()`, calling
`_show_table_structure()` directly (it's self-contained, doesn't need an open
tab). Verified via an offscreen test with a `QTimer`-based auto-close: the
dialog opens with the expected 3 tabs.

**Not done**: did not investigate whether `ui/editable_table.py`'s
`show_structure` signal (and its dead-code sibling stub methods
`show_structure_editor`/`show_alter_table_editor` in `table_view_widget.py`,
both explicitly commented "kept for compatibility but not used") should be
removed entirely as cleanup, versus left as-is. Left as-is this session —
purely additive fix, no deletions of dead code.

## Urgent: release mismatch (check this first)

- The public release at `github.com/AdarshaGS/QForge/releases/tag/v1.0.5`
  (published 2026-07-23T11:56Z) and the remote tag `v1.0.5` both point at
  commit `3dcc292` — the commit *before* all of this session's work. Anyone
  downloading "v1.0.5" right now gets the DMG **without** the credential-store
  migration, theme restyle, or (importantly) without the table-view pagination
  crash fix, i.e. it likely still crashes when opening a table view.
- This happened once already earlier in the session (same symptom, same
  commit) and was cleaned up (`gh release delete v1.0.5 --cleanup-tag`), then
  recurred after a second `deploy.sh` run. Exact cause unconfirmed — most
  likely `deploy.sh` was run from a checkout at `3dcc292` (e.g. `master`, or a
  stale branch state) rather than from the branch with the real changes.
  Verify which branch/commit is checked out **before** running `deploy.sh`
  next time.
- There is also a stray duplicate tag `vv1.0.5` (typo, double "v"), also at
  `3dcc292` — harmless but should be deleted (`git push origin
  :refs/tags/vv1.0.5`).
- The actual complete work lives on `origin/1.0.5` at commit `f0c2c0b` ("Add
  credential-store keychain migration, thread-safe schema snapshot, shared
  export helper, and UI/table-view fixes").
- Locally, branch `adding_security_encryption_1` has one more commit on top,
  `5ee0e56` ("v1.0.5: bump APP_VERSION and cask formula") — this looks like
  `deploy.sh`'s own auto-commit step, made locally but **not yet pushed** to
  `origin/1.0.5`. Decide whether to push it or fold/discard it before
  re-releasing.
- `build.sh` has an uncommitted change (not yet committed anywhere): a
  post-build step that prunes unused Qt frameworks (`QtQml`, `QtQuick`,
  `QtPdf`, `QtVirtualKeyboard`, `QtOpenGL`, etc. — verified via `otool -L`
  that nothing in the app links against them) and re-signs the bundle
  afterward. This is what took the DMG from 66M to 57M. Commit this before
  the next real release, or the size win is lost.
- **To actually fix the release, now that Tier 2 packaging automation exists
  (see below):** once this session's work is committed/pushed, delete the bad
  `v1.0.5` release + both tags (`gh release delete v1.0.5 --cleanup-tag`,
  `git push origin :refs/tags/vv1.0.5`), then push a fresh `v1.0.5` tag from
  the corrected commit. `.github/workflows/build-release.yml` will build all
  three platforms from that exact tagged commit on clean CI runners and
  publish the release itself — no local `build.sh`/`deploy.sh` run needed for
  the release artifacts themselves. `deploy.sh` still has a role (Homebrew
  tap push, version-bump commit) but must NOT be run for a tag that's also
  being pushed to trigger CI — see the warning now at the top of `deploy.sh`.

## Branch/PR state

- PR #1 ("Adding security encryption", branch `adding_security_encryption`)
  is CLOSED — superseded by pushing directly to `origin/1.0.5` from a renamed
  local branch `adding_security_encryption_1`. No open PR currently exists for
  this work.
- `m_client.sql` (a ~1MB untracked file containing real customer PII that was
  sitting in the repo root) was deleted at the user's request earlier this
  session. Confirmed gone.

## In-flight work landed this session (previously uncommitted, now in `f0c2c0b`)

- **Credential migration to OS keychain** — `utils/credential_store.py` wraps
  `keyring`; `ui/connection_dialog.py` assigns each connection a stable `id`,
  resolves passwords through the keychain, writes `connections.json` with
  `password` fields blanked out. `query_analyzer.py` falls back to the
  keychain via `_resolve_password()`.
- **Resolved: `credential_manager.py` vs `credential_store.py` duplication**
  (2026-07-23, uncommitted, ready to commit). Deleted `utils/
  credential_manager.py` (the older keyring/encrypted-file module) and its
  two call sites:
  - `services/db_service.py` — removed the `config["name"]`-keyed fallback
    lookup in `connect()`. Dead in practice: every real caller
    (`main.py:260`, `ui/connection_panel.py:531,954`) passes a config whose
    password was already hydrated by `ConnectionDialog._resolve_credentials()`
    via `credential_store`, keyed by connection `id`, not `name`.
  - `ui/connection_dialog.py` — removed the guarded import and the
    `delete_connection()` tail that called `delete_connection_password
    (connection_name)`. **This was a live bug**, not just dead code:
    `connection_name` was never defined in that method, and since
    `cryptography` is installed in the build `venv/` (confirmed), the guard
    (`CREDENTIAL_MANAGER_AVAILABLE`) was `True`, so every connection deletion
    raised `NameError`. The working `credential_store.delete_password(conn_id,
    ...)` calls a few lines above (already correct) are untouched.
  - No data-loss risk: verified against the user's real `~/Library/Application
    Support/QForge/connections.json` (5 profiles) — all already have `id` set
    and `password` blanked, i.e. already fully on the `credential_store`
    scheme. No `credentials.enc`/`.master_key` files exist on disk, so the old
    module never persisted anything real to migrate.
  - Verified: `py_compile` on both edited files, and an offscreen smoke run
    (`QT_QPA_PLATFORM=offscreen`) constructing `ConnectionDialog` against the
    real connections file succeeded. **Not verified**: did not click through
    an actual `delete_connection()` UI action in this pass (offscreen script
    only constructed the dialog); the pytest suite still hasn't been run for
    real either (see below).
  - Not yet committed — sitting in the working tree alongside the unrelated
    `build.sh` Qt-pruning change.
- **Schema-snapshot concurrency fix** — `services/schema_snapshot.py`, fixes
  cross-thread DB connection corruption during schema loading.
- **Shared DataFrame export helper** — `utils/df_export.py`, consolidated out
  of `ui/table_view_widget.py` / `ui/sql_tab.py`.
- **UI theme restyle** — `ui/theme_manager.py` rewritten to the "graphite
  blue" (dark) / "soft paper" (light) palette and component rules in
  `ai/ui-design.md`. Scope was deliberately limited to the shared
  `ThemeManager` stylesheet, not a full hardcoded-color audit of every widget
  file, and explicitly excludes the environment-safety-badge feature (no
  environment field exists on connection profiles yet — that's Slice 1 of the
  safety initiative, not done).
- **Table-view crash fix** — `ui/table_view_widget.py` was left in a
  half-migrated state (old "Load More" infinite-scroll code mixed with new
  page-based pagination that referenced `current_page`/`prev_btn`/`next_btn`/
  `page_label`/`load_table_data`/`filter_visible`, none of which existed).
  Fixed by completing the migration to page-based pagination and removing the
  dead old-system code (`TableDataLoader`, `threadpool`, `accumulated_data`).
  `ui/connection_panel.py` also had 4 calls to a nonexistent
  `w.get_table_name()` — fixed to `w.table_name`.
- **Dev tooling**: `pyproject.toml` (ruff), `requirements-dev.txt` (pytest),
  `.github/workflows/tests.yml` (CI), 4 new test files under `tests/`. **Not
  verified**: the test suite has still never actually been run in this repo —
  fixes above were verified with ad-hoc offscreen smoke scripts
  (`QT_QPA_PLATFORM=offscreen`), not `pytest`. Run the real suite before
  trusting it as regression coverage.
- `requirements.txt` gained `keyring>=25.0.0`.
- `utils/updater.py` `APP_VERSION` is `1.0.5`.
- Note: `services/query_verifier.py` is a query-optimization A/B comparator
  (original vs. optimized query result diffing), unrelated to the
  dangerous-query classifier/guard planned for the safety initiative — don't
  conflate the two names.

## Known implementation context

- QForge is a PySide6 desktop database client for MySQL, PostgreSQL, and SQLite.
- Connection metadata is managed by `ui/connection_dialog.py`.
- SQL is dispatched from `ui/connection_panel.py`; low-level execution is in `services/db_service.py`.
- Passwords: see the credential-store/credential-manager duplication note
  above. Never copy passwords into profile JSON or logs regardless of which
  mechanism wins.
- Building: `build.sh` requires a folder literally named `venv/` (not
  `.venv/`), installs PyInstaller into it, and produces `dist/QForge.app` +
  `QForge.dmg`. `deploy.sh` does *not* build anything — it requires
  `QForge.dmg` to already exist and publishes it (GitHub release + Homebrew
  tap push + a version-bump commit). Always double check the checked-out
  branch/commit before running either script.
- The worktree may contain user edits unrelated to any given task. Preserve them.

## Write Entry Point Inventory

Carried over from the previous handoff — **line numbers are stale** after
this session's `table_view_widget.py`/`connection_panel.py` changes;
re-verify before relying on them.

| Entry Point | Location (approximate) | Mechanism | Operation Type |
| --- | --- | --- | --- |
| **SQL Editor Single Query** | `ui/connection_panel.py` | `QueryWorker` -> `DbService.execute_query()` | Ad-hoc DML/DDL/DQL |
| **SQL Editor Multi-Script** | `ui/connection_panel.py` | `QueryWorker` -> `DbService.execute_multi_query()` | Multi-statement DML/DDL |
| **Direct Update Helper** | `services/db_service.py` | `DbService.execute_update()` | DML / DDL |
| **Result Grid Edits** | `ui/table_view_widget.py` (`commit_changes()`), `ui/connection_panel.py` (`_execute_commit_sql()`) | -> `execute_update()` | Inline UPDATE, INSERT, DELETE |
| **Create Table Dialog** | `ui/connection_panel.py` | `StructureEditorDialog` -> `execute_update()` | DDL (`CREATE TABLE`) |
| **Alter Table Dialog** | `ui/connection_panel.py` | `AlterTableDialog` -> `execute_update()` | DDL (`ALTER TABLE`) |
| **CSV Data Import** | `ui/connection_panel.py` (`_import_csv_into_table()`) | `cursor.executemany()` | Batch `INSERT` |

## Product roadmap (2026-07-23)

At the user's request, wrote a broader "make this a real product" feature
roadmap (not just the safety initiative) to
`~/.claude/plans/what-are-the-features-memoized-bumblebee.md`. Direction
confirmed with the user: aimed at an **open-source public release**, safety
slices in `ai/load-context.md` stay the top-priority tier unchanged, and
cross-platform (Windows/Linux) support is explicitly in scope. The roadmap is
tiered (0 = safety, 1 = license/docs/bug-fix table stakes, 2 = cross-platform,
3 = onboarding/polish, 4 = differentiators, 5 = OSS community process). Read
that file for the full list before proposing new product features, so work
doesn't get re-derived or duplicated.

**Tier 1 items completed this session (uncommitted):**
- Added `LICENSE` (MIT), fixed `README.md`'s license line, added
  `license = "MIT"` to `pyproject.toml`.
- Fixed README inaccuracies: removed the two claims that environment
  classification (local/staging/production) is already shipped (it isn't —
  Slice 1 of the safety roadmap, not started), corrected the log path
  (`logs/` → actual `~/.qforge/logs/`), and corrected the credential-storage
  description (no longer claims an "encrypted local credential file"
  fallback, since that was `credential_manager.py`, deleted this session).
- **Fixed a real data-loss bug** in `ui/connection_dialog.py`'s
  `save_connections()`: it was unconditionally blanking the password field in
  `connections.json` even when the keychain write (`credential_store.
  set_password`) had just failed — meaning a locked/unavailable OS keyring
  could silently destroy the only copy of a password. `credential_store.
  set_password()` now returns `bool`; `save_connections()` keeps the
  plaintext password on disk when the keychain write fails and shows one
  consolidated `QMessageBox.warning` naming the affected connections instead.
  Verified both the failure path (password preserved, warning fires) and the
  success path (password blanked, no warning) via offscreen smoke tests
  against a temp `connections.json`, not the real one.
- Added `SECURITY.md`: private disclosure via `adarshgs1928@gmail.com`,
  latest-release-only support, an in/out-of-scope list tailored to QForge
  (credential handling, malicious import files, SQL injection in
  QForge-generated SQL vs. user-written SQL), and a "known limitations"
  section documenting the keyring-only storage + plaintext-fallback-on-
  keychain-failure behavior above.
- Fixed the `Ctrl+Shift+I` shortcut collision: `main.py`'s global
  "Import Data…" action moved to `Ctrl+Shift+E` (pairs with `Ctrl+E` for
  Export). `ui/sql_tab.py`'s `Ctrl+I`/`Ctrl+Shift+I` beautify/minify pair is
  unchanged. Updated the README shortcut table to match. Verified via
  `py_compile` and a grep confirming no remaining `Ctrl+Shift+I` binding
  outside `sql_tab.py`.
- **All of Tier 1 (LICENSE, README accuracy, credential_manager cleanup,
  keychain-failure data-loss fix, keychain-prompt-storm fix, SECURITY.md,
  shortcut collision) is now done, uncommitted.** Next roadmap step is Tier 2
  (cross-platform) — see the roadmap file above — once this is committed and
  the release mismatch below is resolved.

**Keychain-prompt-storm fix (2026-07-23, uncommitted).** User reported 6-7
macOS Keychain password prompts every time they ran the app. Root cause:
`ConnectionDialog._resolve_credentials()` eagerly called `credential_store.
get_password()` for every saved connection profile (5 in this user's real
`connections.json`) on every single `load_connections()` call — and
`load_connections()` re-runs on nearly every dialog action (init, save,
duplicate, reorder, delete — 7 call sites). Fixed in `ui/connection_dialog.py`:
- `_resolve_credentials()` now only assigns ids and migrates legacy plaintext
  passwords; it no longer fetches existing keychain passwords at all.
- New `_resolve_password()` fetches a single (conn_id, kind) credential
  lazily and caches it in `self._resolved_passwords` for the dialog's
  lifetime — called from `load_selected_connection()` (only when a user
  actually selects/clicks a connection row).
- `save_connections()` reworked (via new `_sync_password()` helper) to only
  write/delete a Keychain entry for a connection this session actually
  resolved or edited (tracked via the same `_resolved_passwords` cache,
  populated for edited/new connections by new `_remember_form_password()`
  called from `save_connection()`). **This was a real correctness fix, not
  just an optimization** — naively leaving `save_connections()` unchanged
  while making resolution lazy would have caused any unrelated save
  (editing/reordering one connection) to delete every *other* connection's
  Keychain-stored password, since their in-memory `password` field would be
  blank (unresolved) rather than intentionally cleared.
- Verified via offscreen smoke tests with a fake in-memory keychain +  call
  counters: load → 0 reads (was 5); selecting one connection → 1 read,
  cached after; an unrelated `save_connections()` call → 0 deletes of other
  connections' entries; editing a password → correctly re-synced;
  `delete_connection()` → still correctly removes its Keychain entry. Also
  confirmed against the user's real `connections.json` (5 profiles):
  constructing `ConnectionDialog` now does 0 keychain reads, selecting one
  row does exactly 1.
- Not fixed (separate, environmental, not code): macOS ties Keychain
  "Always Allow" grants to the calling process's code signature. Running via
  `python3 main.py` in a dev venv, or an ad-hoc-signed PyInstaller build
  whose signature changes each rebuild, can still cause re-prompts across
  separate runs even for the same, already-resolved item. Not addressed this
  session.

## Tier 2 progress: GitHub Actions packaging (2026-07-24, uncommitted)

Implemented the "Packaging" item from Tier 2 of the product roadmap
(`~/.claude/plans/what-are-the-features-memoized-bumblebee.md`): replaced the
hand-run `build.sh`/`deploy.sh` release path with a CI-based one.

- **`QForge.spec` is now a single, checked-in, canonical PyInstaller spec**
  (previously `build.sh` regenerated it from a heredoc every run, gitignored,
  never committed — real risk of local/CI drift if left that way). It reads
  `APP_VERSION` dynamically from `utils/updater.py` at build time (no more
  sed-patching a hardcoded version string) and only builds the macOS
  `BUNDLE()` step when `sys.platform == 'darwin'` — Windows/Linux just use
  the OS-agnostic `COLLECT()` onedir output.
- `.gitignore` still ignores `*.spec` generally (defensive, in case a stray
  spec gets generated elsewhere) but negates it for this one file
  (`!QForge.spec`) so it's tracked. Verified with `git add -n QForge.spec`.
- **`build.sh` simplified**: removed the ~90-line heredoc spec-generation +
  `sed` version-patching block; it now just runs
  `pyinstaller --clean QForge.spec` against the checked-in spec. Everything
  else (Qt-framework pruning, ad-hoc codesign, DMG creation) is unchanged.
  **Verified by actually running `./build.sh` end-to-end on this machine**:
  produced the same 57M DMG as before, `Info.plist`'s `CFBundleShortVersionString`/
  `CFBundleVersion` correctly show `1.0.5` (confirming the dynamic version
  read works), and the built `.app` launches and stays running under
  `QT_QPA_PLATFORM=offscreen` (smoke-tested by backgrounding it, confirming
  it's still alive after 4s, then killing it).
- **New `.github/workflows/build-release.yml`**: matrix build across
  `macos-latest`/`windows-latest`/`ubuntu-latest`, all from the same
  `QForge.spec`. Triggers on pushing a `v*.*.*` tag (full build + a
  `release` job that downloads all 3 artifacts, computes `SHA256SUMS.txt`,
  and runs `gh release create` attaching everything) or via manual
  `workflow_dispatch` (build-only validation on any branch, no release
  created — deliberately did NOT wire this to run on every push/PR, to avoid
  adding unrequested CI cost/noise to normal development).
  - macOS artifact is named exactly `QForge.dmg` (not e.g.
    `QForge-macos.dmg`) — **this matters**: the existing Homebrew cask
    (`qforge.rb`) has a hardcoded download URL expecting that exact filename,
    so this keeps it compatible without needing a cask change.
  - Linux job installs a set of Qt-runtime apt packages (`libegl1`,
    `libxkbcommon0`, `libxcb-*`, etc.) before building, since PySide6 often
    fails to import on a bare Ubuntu runner without them — **this list is
    best-effort/unverified**; I have no Linux machine to test against, so
    the actual first CI run may need adjustment based on real error output.
  - **Verified**: YAML parses correctly (`python -c "import yaml; yaml.safe_load(...)"`),
    matrix/trigger/job structure inspected programmatically. The macOS
    packaging commands (Qt pruning, codesign, hdiutil) are the same ones
    just proven locally via `build.sh`. The Windows (`Compress-Archive`) and
    Linux (`tar`) packaging commands were checked for correct syntax/target
    directory (`dist/QForge/` onedir, confirmed to exist locally alongside
    `dist/QForge.app/` after a macOS build) but **not actually run on
    Windows/Linux** — that only happens on a real CI run. Recommend
    triggering `workflow_dispatch` manually (or pushing a test tag) once
    this is committed, to prove the Windows/Linux legs actually succeed,
    before relying on this for a real release.
  - **Deliberately NOT automated** (documented as a known gap, not
    implemented): the `APP_VERSION`/`qforge.rb` version bump and the
    Homebrew tap (`adarshags/homebrew-qforge`) push that `deploy.sh` used to
    do. Automating a git push back to the default branch from a
    tag-triggered workflow, and pushing to a *separate* tap repo, both need
    infrastructure (a cross-repo PAT secret for the tap; a decision on
    whether CI should ever commit back to `master`) that wasn't invented or
    assumed here. For now: bump `APP_VERSION` and commit that *before*
    tagging, and still run `deploy.sh`'s Homebrew-tap-push step manually
    (its release-creation step will now conflict with CI's if the same tag
    triggers both — see the warning comment added atop `deploy.sh`).
- Added a "Releases (macOS, Windows, Linux)" section to `README.md`
  explaining the new tag-triggered workflow, and fixed a now-stale comment
  in `utils/updater.py` (previously said "keep in sync with QForge.spec /
  deploy.sh" — QForge.spec no longer needs manual syncing).

## Exact next step

1. Commit the current staged state on `Qforge_1` (verified: compiles clean,
   18/18 pytest passing, offscreen smoke tests passing) and push to
   `origin/1.0.7`. The user is pushing manually rather than having the
   assistant push directly.
2. **Push a test tag or use `workflow_dispatch`** to actually exercise
   `.github/workflows/build-release.yml` on real Windows/Linux runners
   before trusting it — the macOS leg is proven locally, Windows/Linux are
   not yet proven on real CI (see Tier 2 section above for exactly what's
   unverified, especially the Linux apt-package list for PySide6).
3. **Fix the release mismatch** (see "Urgent" above) — still describes the
   real, unresolved state as of this writing: the public `v1.0.5` GitHub
   release/tags still point at the old broken `3dcc292`. Re-verify against
   whatever `origin/1.0.7`/`Qforge_1` becomes once pushed.
4. Continue the product roadmap — Tier 2's remaining items (path
   unification via `utils/paths.py`, extending `.github/workflows/tests.yml`
   into a macOS/Windows/Linux matrix + `pytest-qt`) or Tier 3, per
   `~/.claude/plans/what-are-the-features-memoized-bumblebee.md` — or start
   the production-safety work (Slice 1: environment classification, or
   straight to Slice 2/3: `services/query_classifier.py` + read-only guards)
   per `ai/load-context.md`.
