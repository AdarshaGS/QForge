# QForge — AI Flush Context

Use this file to leave an accurate handoff when work on QForge pauses or ends.
It is deliberately a living record: replace the template sections with concrete
facts from the current task. Do not record passwords, access tokens, private
hostnames, customer data, or unredacted sensitive SQL.

## Current handoff

**Status:** Working directly on `master` (HEAD `533cd29`). No feature branch
open for this session's work; changes below are uncommitted in the working
tree.

**Last updated:** 2026-08-11.

## Uncommitted right now (working tree)

`git status --short`:
```
 M README.md                      (Slice 4, issue #39, #122, AND #123 docs — this session)
 M ai/flush-context.md            (this handoff update)
 M main.py                        (Slice 4 — this session)
 M services/db_service.py         (Slice 4 — this session)
 M services/query_classifier.py   (Slice 4 — this session)
 M services/schema_snapshot.py    (issue #121 fix — this session)
 M tests/test_db_service_sqlite.py    (Slice 4 — this session)
 M tests/test_query_classifier.py     (Slice 4 — this session)
 M tests/test_schema_snapshot.py      (issue #121 fix — this session)
 M ui/connection_dialog.py        (issue #41 fix — this session)
 M ui/connection_panel.py         (Slice 4, issue #57, AND issue #39 — this session)
 M ui/editable_table.py           (issue #122 AND #123 — this session)
 M ui/sql_tab.py                  (Slice 4 AND issue #112 fix — this session)
 M ui/query_verifier_dialog.py    (issue #44 fix — this session)
 M ui/table_view_widget.py        (issue #126 fix — this session)
?? ui/export_scope_dialog.py      (issue #39 fix — this session, new file)
 M utils/updater.py               (unrelated, pre-existing — version bump
                                    1.1.0→1.1.2 — not touched this session)
?? LAUNCH_PLAN.md                 (pricing/investment/Apple-timeline notes
                                    for the Mac-only individual-sale launch —
                                    not yet committed, ask before committing)
```

- **Production-safety Slice 4 — Transaction controls** (`ai/load-context.md`).
  Slices 1–3 (environment classification, read-only mode, dangerous-query
  guard) were already done and committed in `15ec91b` before this session —
  confirmed by reading the actual code, not just this file's history. Slice 4
  had nothing built: `autocommit=True` was hardcoded for MySQL/PostgreSQL,
  no BEGIN/COMMIT/ROLLBACK was exposed anywhere, and SQLite silently
  committed after every non-SELECT statement.

  **Key architectural finding that shaped the design:** every "Run" click in
  `ui/connection_panel.py:_run_query_in_tab` opens a brand-new disposable
  `DbService()`, executes, and closes it in `_QueryWorker.run()`'s `finally`
  block — explicitly commented "prevents shared-connection races." A
  transaction cannot span separate Run clicks under that model as-is. Asked
  the user to choose between (a) script-scoped transactions only (BEGIN...
  COMMIT as one script, zero changes to connection lifecycle) or (b) an
  interactive session (Begin/Commit/Rollback buttons, connection persists
  across clicks). **User chose (b), interactive session.**

  - `services/db_service.py`: new `TransactionError`; `self.in_transaction`
    state (reset in `connect()`/`disconnect()`); `begin_transaction()` /
    `commit_transaction()` / `rollback_transaction()` (dialect-specific —
    MySQL toggles `connection.autocommit(False/True)` + `BEGIN`; PostgreSQL
    toggles `connection.autocommit`; SQLite issues an explicit `BEGIN`).
    `execute_query()` now detects a lone BEGIN/COMMIT/ROLLBACK (however the
    user wrote it — typed directly or via the new buttons, both go through
    this one path) and routes it through the transaction methods instead of
    sending it as ordinary SQL, so `in_transaction` can't desync from
    reality. Fixed two pre-existing unconditional-commit bugs that would
    otherwise have silently ended a manual transaction after its first
    statement: `_execute_query_raw`'s SQLite branch and `_execute_update_raw`
    (all three dialects) now both check `if not self.in_transaction` before
    committing. If the connection drops while a transaction is open, the
    code no longer silently reconnects-and-retries the statement outside the
    transaction the user thinks is still active — it raises a clear
    `TransactionError` instead.
  - `services/query_classifier.py`: `BEGIN` added to the existing
    `_UNKNOWN_TYPE_FALLBACKS` set (sqlparse types it `UNKNOWN`); `START
    TRANSACTION` (sqlparse types it `START`) folded into `BEGIN`. New
    `TRANSACTION_KINDS = {"BEGIN", "COMMIT", "ROLLBACK"}` constant.
  - `ui/connection_panel.py`: `_run_query_in_tab` gained an `override_query`
    param (used by the new buttons) so typed SQL and button clicks share one
    dispatch/guard/connection path — no alternate entry point skips
    `_guard_write`. Before creating a connection it now checks
    `tab._tx_db_service`; if set, reuses it instead of opening a new
    disposable one. `_QueryWorker`'s `finally` only disconnects `if not
    self._db.in_transaction`. New `_finalize_query_connection` (called from
    all four query-completion handlers) decides whether the tab keeps its
    connection alive and refreshes the indicator; deliberately does **not**
    auto-rollback on error/cancel while a transaction is open — the
    transaction stays open so the user explicitly decides, per
    load-context.md's "avoid silently committing/losing changes." New
    `has_open_transactions()`, `_close_tab` now warns before closing a tab
    with an open transaction (rolls back on confirm).
  - `main.py`: `_close_connection_at` warns before closing a connection with
    any open transaction. Found and fixed a real bypass while wiring this in
    — `_smart_close` (Cmd+W) called `panel.tabs.removeTab()` directly instead
    of going through `ConnectionPanel._close_tab`, which would have skipped
    the new warning entirely for the keyboard-shortcut close path. Now
    routes through `_close_tab`.
  - `ui/sql_tab.py`: new toolbar controls — `tx_status_lbl`, `begin_tx_btn`,
    `commit_tx_btn`, `rollback_tx_btn` — and `set_transaction_state(active)`.
    Transactions are **tab-scoped**, not connection-scoped: each tab already
    got its own disposable connection per run, so giving a tab its own
    persistent transaction connection was the natural fit with no new
    cross-tab coordination needed.
  - `README.md`: new "Control a transaction" subsection under Core workflows.
  - **Verification:** `QT_QPA_PLATFORM=offscreen ./venv/bin/python3 -m
    pytest tests/` → 70 passed (59 pre-existing + 11 new: 9 in
    `test_db_service_sqlite.py` covering begin/commit/rollback, the SQLite
    no-auto-commit-inside-a-transaction regression, double-begin/commit-
    without-begin/rollback-without-begin all raising `TransactionError`,
    default-autocommit-unchanged, and raw-typed BEGIN/COMMIT/ROLLBACK
    through `execute_query()`; 2 in `test_query_classifier.py` for
    BEGIN/BEGIN TRANSACTION/START TRANSACTION/COMMIT/ROLLBACK
    classification). All modified modules also import cleanly under
    `QT_QPA_PLATFORM=offscreen`.
    Also ran a throwaway offscreen script (not committed, scratch dir) that
    instantiated a real `SqlTab` and `ConnectionPanel` and exercised
    `set_transaction_state`, `_finalize_query_connection` (both the
    BEGIN-keeps-connection-alive and COMMIT-releases-it paths), and the
    `_close_tab` open-transaction guard (both decline and confirm) — all
    assertions passed. This covers the new UI-wiring logic itself; it does
    **not** exercise the real async `QThread` worker path, since that isn't
    practically synchronous-testable this way.
    **Not verified**: a real on-screen interactive pass (click Begin, run a
    statement, see results, Commit/Rollback, watch the tab-bar `⏳` marker
    and button states update live; the close-connection confirmation
    dialog; MySQL and PostgreSQL — no live server available in this
    environment, and no existing test fixture covers them). Same
    offscreen-vs-real-focus limitation noted for prior UI work in this
    file's git history.

- **Issue #121 — schema tree stays empty on connect (MySQL, no database
  pre-selected).** User-reported from a live screenshot (MySQL localhost
  connection, sidebar "Tables" empty with no error). Root cause:
  `services/schema_snapshot.py:fetch_schema_snapshot()` called
  `db.get_tables()`/`db.get_all_columns()` *before* the "no database
  selected → fall back to the first available db, `select_db()`" logic ran
  — so that first fetch always came back empty (MySQL raises "No database
  selected" with nothing chosen yet), and nothing ever re-fetched after the
  fallback db was selected. `ui/connection_panel.py:_on_schema_loaded`
  applies the `switched_db` result to `self.config`/the live connection but
  renders the tree from the already-empty `tables`, and nothing re-triggers
  `load_schema()` afterward — the tree only ever populated once the user
  manually used the database switcher (which does select_db *before*
  re-fetching). PostgreSQL is unaffected — libpq always resolves to a real
  default database on connect.
  - Fix: reordered `fetch_schema_snapshot()` so the MySQL fallback
    select-db step runs before the tables/columns fetch. Single-file fix,
    no UI changes needed once the snapshot itself is correct.
  - Added `test_fetch_schema_snapshot_mysql_with_no_database_selected_still_lists_tables`
    to `tests/test_schema_snapshot.py`, using a fake `DbService`
    (`_FakeMysqlDbService`) that reproduces MySQL's real "No database
    selected" failure mode, since no live MySQL server is available in this
    environment. Full suite green (71/71 → still 71 after this fix, was
    already counted above).
  - Filed as GitHub issue #121 with the root-cause writeup and fix status.
  - **Not verified**: against a real MySQL server (same "no live server
    here" limitation as Slice 4's MySQL/PostgreSQL paths above) — the fake
    is a faithful model of the documented failure mode but isn't a
    substitute for hitting an actual server with no database pre-selected.

- **Issue #112 — "Replace option is not present in SQL query editor."**
  User-reported with a screenshot: Find bar open with matches, no Replace
  row visible anywhere. The Replace UI (`_replace_row` in `ui/sql_tab.py`)
  and its Replace/Replace All logic (`_replace_current`/`_replace_all`)
  already existed and worked — the only way to reveal it was `Ctrl+H`
  (`find_shortcut` → `_toggle_find_replace`), and **on macOS Cmd+H is the
  system "Hide Application" shortcut**, intercepted by the OS before Qt
  ever sees the key event. Pressing it just hid the whole window —
  indistinguishable, from the user's side, from Replace not existing.
  `Ctrl+F` (find-only) was the only thing anyone could ever actually reach.
  - Fix (`ui/sql_tab.py`): rebound the shortcut to `Ctrl+Alt+F` (Cmd+Option+F
    on macOS — the Xcode/Sublime/VS Code convention, not OS-shadowed).
    **Also**, and probably the more load-bearing half of the fix: added a
    visible `⇄ Replace` toggle button directly in the Find bar next to the
    Aa/\b/.* toggles, so Replace is discoverable and reachable by clicking
    — no shortcut knowledge needed at all. Both entry points now go through
    one `_set_replace_row_visible()` helper so the toggle button's checked
    state always matches reality regardless of how the row was opened or
    closed (shortcut, button, or Esc via `_hide_find_bar`).
  - Also added the previously-undocumented `Ctrl+F`/`Ctrl+Alt+F` shortcuts
    to the README shortcuts table.
  - Updated issue #112's title/body on GitHub with the root cause and fix
    write-up; left open pending the on-screen check below (not closed).
  - **Verification:** offscreen script (scratch dir, not committed)
    confirming: Find-only never shows Replace; the rebound shortcut opens
    both and syncs the toggle button; the toggle button alone opens/closes
    Replace; and the shortcut is bound to `Ctrl+Alt+F`, confirmed *not*
    `Ctrl+H`. Full suite green (71/71, unaffected — no existing test
    touches `ui/sql_tab.py`). **Not verified**: a real on-screen click
    through on macOS (open Replace via the new button, via the new
    shortcut, actually replace text) — same offscreen-vs-real-focus/real-OS
    limitation as everything else UI in this file.
  - Follow-up from user screenshot after the above: the "Find:"/"Replace:"
    labels weren't the same width, so the Find and Replace input boxes
    didn't line up vertically. Fixed by giving both labels a shared
    `setFixedWidth(52)`. Verified with an offscreen `QWidget.grab()`
    render (not committed, scratch dir) confirming both inputs now start
    at the same x-coordinate (122px in the test render) and "Replace:"
    isn't clipped at that width.

- **GitHub milestone work — "Aug 3rd week 2026" (#9), issue #44.** User
  asked to work a milestone one issue at a time; before starting, sized all
  7 open issues in #9 by reading the actual code behind each (not just the
  issue text) — several turned out to be already partly or fully built
  (#39 database export, #41 group autocomplete) while others are genuine
  multi-feature epics (#75 filtering/sorting has 3 overlapping dialog
  classes to reconcile; #76 "5.3 Data UX" bundles ~10 sub-features, several
  already done, freeze-columns/undo-redo genuinely hard — recommended
  splitting it into sub-issues before starting). User picked #44 to start.

  **Issue #44 — Query Verifier parameter panel.** The issue's own stated
  hypothesis (refresh wired to Enter/keypress instead of document changes)
  was wrong — `_refresh_params` was already correctly connected to both
  editors' `textChanged` via a debounce timer. Reproduced the real bug
  directly by driving `QueryVerifierDialog` in an offscreen Qt session
  (paste into Original → params show correctly; paste a *different* query
  containing the *same* param names into Optimised → panel goes silently
  empty, with a `RuntimeError: ... QLineEdit already deleted` printed to
  the log but never surfaced to the UI).
  - Root cause (`ui/query_verifier_dialog.py:_refresh_params`):
    `existing = dict(self._param_inputs)` captured **widget references**;
    `QFormLayout.removeRow()` (called right after, to clear the form)
    **deletes** those widgets in Qt (unlike `takeRow()`); the code then
    read `existing[name].text()` on an already-deleted widget while
    rebuilding — a dangling-C++-object access, raised by shiboken as
    `RuntimeError`. That exception aborted the rest of the method,
    including the final `_param_box.setVisible(...)` call, leaving the
    panel empty/stale. Only triggers when a param name repeats across
    refreshes (the "restore previous value" branch) — explaining the
    seemingly random "Enter fixes it" pattern: a retry that doesn't hit
    the same collision (e.g. because the crash already cleared
    `_param_inputs`) just works.
  - Fix: capture `{name: inp.text() ...}` (plain values) *before* the
    `removeRow()` teardown, not widget references read afterward.
  - Verified via the same offscreen repro script (scratch dir, not
    committed) confirming the traceback is gone and params now stay in
    sync across a full paste → paste-again sequence. Full suite green
    (71/71, no existing test touches this dialog).
  - Updated issue #44's title/body on GitHub with the corrected root
    cause; left open pending the on-screen check below.
  - **Not verified**: a real on-screen check that no traceback appears in
    the console during normal paste/edit use of the Query Verifier.

  **Issue #41 — group field autocomplete.** Turned out to already be an
  editable `QComboBox` populated from every saved connection's group
  (`ui/connection_dialog.py:_populate_group_combo`), not plain free text as
  the issue assumed — so "pick existing or type new" already worked. The
  real gap: Qt auto-installs a default completer on any editable combo, but
  its default mode (`InlineCompletion`) just silently completes to the
  single closest alphabetical match — no visible popup of matches, no
  substring filtering, so it didn't behave like the requested "filter the
  list as you type."
  - Fix: replaced the default completer with an explicit `QCompleter`
    bound to the combo's own model (so `_populate_group_combo()` stays the
    only place group names need to be kept in sync) in
    `QCompleter.PopupCompletion` mode with `Qt.MatchContains`, case
    insensitive.
  - Verified offscreen (scratch dir, not committed) against the issue's
    own example — typing `MFI P` surfaces `MFI Production` in the
    completion model — plus substring/case-insensitive matching and that
    typing an unmatched new name still works untouched. Full suite green
    (71/71, no existing test touches this dialog).
  - Updated issue #41 on GitHub with the root cause and fix; left open
    pending the on-screen check below.
  - **Not verified**: a real on-screen check (Connection Manager → New
    Connection → type into Group, confirm the popup actually appears and
    filters — offscreen mode can't render/interact with a real completer
    popup).

  **Issue #57 — schema-loading progress indicator.** The panel wasn't
  fully blank as the issue described — `load_schema()` already added a
  static `"Loading…"` row (and a `"⏱ Cached schema (stale) — refreshing…"`
  one for background stale-cache refreshes) — but both were inert text,
  nothing proving the app was still working, which is exactly the "looks
  frozen" complaint on a large/slow schema. Failure (`_on_schema_error`)
  was a dead-end `⚠ {msg}` row with no retry path except hunting for the
  toolbar's "↺ Schema" button.
  - Fix (`ui/connection_panel.py`): both loading rows now tick a live
    elapsed-time counter via a `QTimer` (`⏳ Loading schema… 2.3s`),
    mirroring the existing Run-button ticker pattern already used
    elsewhere in this file. The row upgrades to show a table count the
    moment `_on_schema_tables_ready` fires (issue #16's fast partial
    callback): `⏳ 1,245 table(s) found — loading details… 3.1s`.
    `_on_schema_error` now adds a `↺  Click to retry` row wired through
    the existing `itemClicked` handler to call `load_schema()` — same
    action as the toolbar button, just directly reachable from the error
    itself. New helpers: `_start_schema_loading_indicator`,
    `_tick_schema_loading`, `_stop_schema_loading_indicator`.
  - Deliberately did **not** add "disable schema interactions while
    loading" (one of the issue's "additional improvements") — the
    stale-cache path intentionally keeps the *old* tree usable during a
    silent background refresh (issues #71/#72); disabling it would undo
    that existing, deliberate design.
  - Verified offscreen against a real SQLite connection (scratch dir, not
    committed): ticker starts immediately on construction; the table-count
    text format checked deterministically (a local SQLite fetch is too
    fast to reliably catch mid-tick against the real background thread);
    full fetch correctly stops the ticker and lands on the real table
    tree; `_on_schema_error` triggered directly confirms the retry row
    appears and clicking it calls `load_schema()`. Full suite green
    (71/71, no existing test touches this panel).
  - Updated issue #57 on GitHub with the fix; left open pending the
    on-screen check below.
  - **Not verified**: a real on-screen check against an actual large/slow
    remote schema — does the ticker genuinely read as "alive," is the
    retry row noticeable on a real failure (e.g. killing the network
    mid-connect).

  **Issue #39 — database export scope (structure/data/tables).** The core
  complaint (export tied to an open query result) was already fixed in an
  earlier session — `export_database()`/`_export_table()` exist with
  explicit `# issue #39` comments, reachable from File → Export Database…
  and right-click → Export Table…. What was missing: no way to narrow a
  whole-database export to structure-only/data-only, or to specific
  tables — it always did every table's structure + data.
  - Fix: new `ui/export_scope_dialog.py` (`ExportScopeDialog`) — table
    checklist (skipped for a single-table export) + a Structure only/Data
    only/Structure + Data choice. Both entry points open it first.
    Structure reuses the existing `get_table_ddl()`; data reuses the
    existing `_to_sql_inserts()`. Single-table "Data only" is untouched —
    still the original multi-format `export_dataframe()` path (CSV/JSON/
    Excel/SQL); Structure only/Structure + Data write a single `.sql` file
    instead, since DDL doesn't fit the other formats.
  - Deliberately deferred (documented in the issue, not silently dropped):
    CSV/JSON/Excel for a *whole-database* export (needs a real
    packaging/format decision — one file per table or a zip — bigger than
    this slice), and a separate "Export Schema…" action (redundant with
    the new "Structure only" mode).
  - Verified offscreen against a real SQLite connection (scratch dir, not
    committed): dialog table-selection/content-mode behavior; structure-
    only output has DDL and no INSERTs; data-only the reverse; both has
    both; single-table "Data only" still produces a file via the
    unmodified `export_dataframe()` path. Full suite green (71/71).
    (One iteration of this offscreen script hung — `export_dataframe()`'s
    `QMessageBox.information()` blocks forever with no user to click OK
    under `QT_QPA_PLATFORM=offscreen`; fixed by monkeypatching
    `QMessageBox.information/warning/critical` to no-ops before exercising
    that path. Worth remembering for any future offscreen script that
    exercises a real success/error dialog, not just this one.)
  - README updated — the whole-database/single-table export entry points
    weren't documented there at all before this, only query-result export
    was.
  - Updated issue #39 on GitHub with the fix; left open pending the
    on-screen check below.
  - **Not verified**: a real on-screen check (File → Export Database…,
    right-click → Export Table…, confirm the dialog appears and each
    content mode produces the expected file).

  **Issues #54 and #75 — put ON HOLD by the user before implementation.**
  Both were fully investigated and a plan written (still saved at
  `/Users/adarsh/.claude/plans/tingly-wandering-bumblebee.md`) but the user
  said to hold both and move on — no code was written for either. Key
  findings worth keeping even though nothing shipped: #54 ("column
  selector") describes a UI that doesn't exist anywhere in this codebase —
  it's a build task, not polish, and overlaps with #76's "Column hide/show".
  #75 ("filtering/sorting") looked like three competing filter-dialog
  systems needing reconciling, but two of the three
  (`AdvancedFilterDialog`, `DataFilterDialog`) are **never instantiated
  anywhere**, and the third (`ColumnFilterDialog`) is instantiated inside a
  method with zero callers — all dead code from the initial commit. The
  only live filter mechanism is `sql_tab.py`'s Quick Filter bar
  (`toggle_filter`/`add_filter_row`/`apply_all_filters`), which needs
  AND/OR combining and date-range support added, not any reconciling.

  **Issue #76 ("5.3 Data UX") — user approved splitting into sub-issues,
  working through them one at a time.** 5 of the 10 bundled items already
  existed (copy cell/row/selected-rows/as-JSON/as-SQL, inline editing).
  Created 4 new GitHub issues for the rest: **#122** (column reorder, done
  below), **#123** (export selection), **#124** (undo/redo), **#125**
  (freeze columns — flagged as needing its own scoping pass, no model/view
  split exists on this widget to hang the standard Qt frozen-pane technique
  on). Column hide/show folds into the held #54 rather than becoming a 6th
  sub-issue. Comment posted on #76 linking all four. Confirmed
  `EditableTableWidget` (`ui/editable_table.py`) backs *both* the
  table-data view and SQL query results — same widget, so each fix in this
  breakdown only needs building once.

  **Issue #122 — column reorder.** `hdr.setSectionsMovable(True)` is the
  one-line Qt feature enable; the real work was auditing every place in
  `ui/editable_table.py` that iterated columns assuming logical index order
  matched left-to-right visual order (true before this change, not after).
  Found and fixed three real bugs the feature would otherwise have
  introduced:
  - `_selected_rows_data()` (backs every `copy_rows_as()` format) — was
    `range(columnCount())` (logical order); now
    `[hdr.logicalIndex(v) for v in range(columnCount())]` (visual order).
  - `copy_to_clipboard()` — was sorting selected cells by logical column
    index; now sorts by `hdr.visualIndex`.
  - `paste_from_clipboard()` — was walking logical indices outward from
    the clicked cell; now walks visual positions and maps each back to a
    logical index via `hdr.logicalIndex(visualPos)`, so pasted values land
    in the visually-adjacent columns, not the logically-adjacent ones.

  `sectionClicked` (used for sort) already emits logical index regardless
  of visual position, so sorting needed no change. Column-width sizing
  (`_set_compact_column_widths`) and single-column operations
  (`copy_column_values`, delete/NULL/default) also needed no change — they
  already resolve a specific known logical column rather than iterating in
  an order that could drift from what's displayed.
  - Verified offscreen (scratch dir, not committed): loads a 3-column
    DataFrame, simulates a drag via `header.moveSection()`, confirms
    `_selected_rows_data()`/`copy_to_clipboard()` reflect the new visual
    order and a paste lands in the correct visually-adjacent columns. Full
    suite green (71/71, no existing test touches this widget).
  - README updated. Issue #122 updated with the fix; left open pending the
    on-screen check below.
  - **Not verified**: a real on-screen drag-and-drop of a column header —
    offscreen mode can simulate the *resulting* header state via
    `moveSection()` but not the drag gesture itself. Also not
    cross-checked against #54's held plan, since #54 hasn't been built yet
    — when it is, its column-visibility state (`_hidden_columns`) should
    be checked against reorder for the same kind of index-vs-visual-order
    issue this fix just addressed.

  **Issue #123 — export selection.** Confirmed the bug exactly as
  suspected during #76's triage: right-click → "Export result..." calls
  `export_selected()` (`editable_table.py:981`), which despite the name
  exports the entire `filtered_data`/`original_data`, never touching
  `self.selectedItems()`.
  - Fix: new `export_selected_rows()` + a new "Export Selected Rows..."
    context-menu entry next to the existing one (kept unchanged — both are
    useful). Builds the export DataFrame via `base_df.iloc[selected_rows]`
    on the same typed `filtered_data`/`original_data` the existing action
    already uses — not the grid's stringified cell text (unlike
    `copy_rows_as()`'s approach) — so numeric/date columns keep real types
    in JSON/Excel output, and reuses the existing `export_dataframe()`
    helper.
  - Verified offscreen (scratch dir, not committed; reused the
    `QMessageBox.information/warning/critical` no-op monkeypatch lesson
    from #39's verification since `export_dataframe()` shows a real modal
    at the end): no-selection shows an info message rather than erroring;
    selecting 2 of 3 rows exports exactly those 2; the existing
    "export everything" action still exports all 3 unchanged (regression
    check it wasn't accidentally narrowed); dtypes preserved via `.iloc`.
    Full suite green (71/71).
  - README updated. Issue #123 updated with the fix; left open pending the
    on-screen check below.
  - **Not verified**: a real on-screen check (select rows, right-click →
    Export Selected Rows..., confirm only those rows land in the file).

  **Issue #126 — table-data view crash on filtered pagination (new, found
  from a user-supplied app log, not from milestone #9).** User pasted a
  real log: successful `"Loaded page 1 (25 rows) from flyway_schema_history"`
  lines, then repeated `ERROR - Failed to load table data: unsupported
  operand type(s) for +: 'NoneType' and 'int'` — asked if it was related to
  #54 (it wasn't; #54 is still unimplemented/on hold).
  - Root cause: `ui/table_view_widget.py:load_table_data()`'s
    `if self.current_filter:` branch built a `COUNT(*)` query but **never
    executed it and never set `self.total_rows`** — the `else` (unfiltered)
    branch does the real query + fallback-on-failure; the filtered branch
    was just missing that entirely. Once any filter is applied,
    `self.total_rows` stays `None`, and
    `total_pages = (self.total_rows + self.page_size - 1) // self.page_size`
    a few lines later crashes on every subsequent load — matches the log
    exactly (loads succeeded before a filter was applied, failed
    repeatedly after).
  - Fix: actually run the count query in the filtered branch, same
    try/except-with-large-number-fallback shape the unfiltered branch
    already uses.
  - Verified offscreen against a real SQLite table (scratch dir, not
    committed): unfiltered load still sets `total_rows` correctly
    (regression check); applying a filter and reloading now correctly
    counts and paginates instead of leaving `total_rows` `None`; repeated
    filtered loads (matching the log's repeated-failure pattern) stay
    correct. Full suite green (71/71, no existing test touches this
    widget).
  - Filed as GitHub issue #126 with the root-cause writeup and fix status
    (this one didn't have an existing issue — filed fresh, not from the
    milestone).
  - **Not verified**: a real on-screen check (apply a filter on a
    table-data view, page through it, confirm no error).

## Exact next step

1. Recommended before calling Slice 4 done: a real on-screen pass against a
   SQLite connection (Begin → Run an UPDATE → Run a SELECT to see the
   pending value → Rollback → confirm reverted; repeat ending in Commit →
   confirm persisted after reconnect; confirm button states and the `⏳`
   tab marker track reality; try closing a tab/connection mid-transaction
   and confirm the warning appears and Cancel actually cancels). Then the
   same against a real MySQL and PostgreSQL server if available — untested
   here, dialect-specific code paths (`autocommit(False)` / `.autocommit =
   False`) are unverified against a live driver.
1a. Also verify issue #121's fix on that same real MySQL pass: connect a
   profile with no database configured and confirm the schema tree
   populates immediately, no manual database-switcher step needed.
1b. Also verify issue #112's fix on-screen: open a query tab, click the new
   `⇄ Replace` button in the Find bar and confirm the Replace row appears;
   separately confirm `Ctrl+Alt+F`/Cmd+Option+F opens it too; run an actual
   replace and Replace All. (Cmd+H itself still hides the app window as
   normal macOS behavior, unrelated to this fix — QForge's own shortcut just
   no longer collides with it.)
2. Decide what to do with `LAUNCH_PLAN.md` (untracked) and this file's own
   diff — both currently uncommitted, ask before committing per the usual
   convention. `utils/updater.py`'s version bump is unrelated and pre-dates
   this session — leave it alone unless asked.
3. Production-safety Slices 5–6 per `ai/load-context.md`: Slice 5 (query
   limits/timeout/cancellation) is partially done — cancellation already
   works, server/client timeouts exist but are hardcoded (3600s) and not
   per-profile or user-facing, and there's no max-row-limit feature at all.
   Slice 6 (audit trail) has nothing built — no audit logging exists
   anywhere in the codebase yet.
4. Issue #78 (quick search palette) was committed last session
   (`533cd29`); its own deferred follow-ups (connections/databases in the
   palette, indexes/foreign keys needing a new batched DbService method)
   are unrelated to Slice 4 and still open if picked up later.
5. Separate track from an earlier session, not code: VAPT security issues
   (#113-120, due 2026-08-31) and the Mac-only launch plan
   (`LAUNCH_PLAN.md`) — see GitHub milestones #10-15 for the full
   pre-launch/licensing timeline against a targeted 2026-09-28 launch.
6. **Active milestone track: "Aug 3rd week 2026" (#9), one issue at a
   time, pausing for the user's go-ahead after each.** #44, #41, #57, #39,
   #122, and now #123 (both sub-issues of #76) all done — verify all six
   on-screen (no console traceback while pasting/editing in the Query
   Verifier; Group field popup actually appears and filters;
   schema-loading ticker/retry row against a real slow connection; Export
   Database…/Export Table… scope dialog and each content mode; a real
   column drag-to-reorder, then copy/paste/export still matching what's
   displayed; select some rows → Export Selected Rows… only exports those),
   then pick the next one.
   - **#54 and #75: ON HOLD** — investigated and planned (plan file still
     at `/Users/adarsh/.claude/plans/tingly-wandering-bumblebee.md`), user
     said hold both, no code written.
   - **#76 was split into sub-issues** (5 of its 10 items already existed;
     see the #76 writeup above for the full audit): **#122 and #123 done**
     (column reorder; export selection). Still open: **#124** (undo/redo
     history — medium), **#125** (freeze columns — large, flagged as
     needing its own scoping pass; recommended doing this one last).
     Column hide/show folds into the held #54.
   - Recommended order for what's left: #124 → #125, with #54/#75 resumed
     whenever the user un-holds them.
