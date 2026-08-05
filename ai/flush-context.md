# QForge — AI Flush Context

Use this file to leave an accurate handoff when work on QForge pauses or ends.
It is deliberately a living record: replace the template sections with concrete
facts from the current task. Do not record passwords, access tokens, private
hostnames, customer data, or unredacted sensitive SQL.

## Current handoff

**Status:** On branch `fix/github-issues-batch` (single shared branch, one
commit per GitHub issue, per standing user instruction — do not create a
new branch or commit without being asked). Production-safety Slices 1-3,
the app icon/branding work, and issues #14/#15(partial)/#6/#9/#22/#17/#13/#24
described in older revisions of this file are all committed (see `git log`
— this file no longer restates them in detail).

**Last updated:** 2026-07-27.

## Uncommitted right now (working tree)

`git status --short`:
```
 M ui/editable_table.py   (issue #29)
 M ui/sql_tab.py           (issue #2)
 M ui/theme_manager.py     (issue #28)
?? .claude/                (graphify bootstrap — see below)
?? CLAUDE.md               (graphify bootstrap — see below)
```

- **Issue #2** — Cmd+F opened the generic Find bar even when focus was on
  the SQL result grid, instead of Quick Filter. `ui/sql_tab.py`
  `_toggle_find_bar()` now checks `self.result_table.hasFocus()` first and
  calls `self.toggle_filter()` instead when true. Verified offscreen by
  stubbing `hasFocus()` (real Qt focus transfer is unreliable under
  `QT_QPA_PLATFORM=offscreen`).
- **Issue #28** — Light theme schema-tree text (table names) used
  `L_TEXT2` (`#687080`, muted) instead of `L_TEXT` (`#20242C`, near-black).
  One-line swap in `ui/theme_manager.py`'s light `QTreeWidget` block.
  Deliberately left the dark theme and the header-row label alone (not
  reported, different elements).
- **Issue #29** — Result grid stretched to fill the editor width even for
  2-column queries, leaving dead space after the last column.
  `ui/editable_table.py`'s `_set_compact_column_widths()` now also calls
  `setMaximumWidth()` sized to the actual column-width sum (+ row header +
  frame + scrollbar), relying on the existing `Expanding` size policy to
  fill-then-cap. **Caveat:** verified the width computation itself
  (narrow→254px, wide→8774px, no-columns→uncapped) but could NOT reliably
  verify the live "still fills the viewport for wide results" half in this
  sandbox — `QT_QPA_PLATFORM=offscreen` doesn't propagate size hints
  correctly (same limitation hit testing issue #2's focus transfer). A
  real on-screen check (narrow query vs. wide query) is recommended before
  calling this done.
- **`.claude/` / `CLAUDE.md`** — not my edits; appeared automatically when
  the graphify skill bootstrapped itself (see below). `.claude/settings.json`
  is graphify's shared PreToolUse hook config (safe to commit if wanted);
  `.claude/settings.local.json` is a local permissions allowlist
  (conventionally NOT committed). Left untracked for the user's own call.

Issue #15 (new SQL tab jumping to a different macOS Space in fullscreen)
remains **explicitly deferred** — user said "forget about this issue, i
will check in future" after root-causing it to `EditableTableWidget`
(QTableWidget) inside a `QSplitter` in native fullscreen; a `QTableView`
experiment strongly suggested a fix but needs a full rewrite of
`EditableTableWidget`, out of scope unless the user revisits it.

## graphify codegraph (set up this session, live now)

A codegraph now exists at `graphify-out/graph.json` (gitignored, derivable)
for the ticket-to-fix workflow: 998 nodes / 1,764 edges / 70 labeled
communities, built from `main.py`, `query_analyzer.py`, `ui/`, `services/`,
`utils/`, `tests/`, `ai/load-context.md`, `ai/ui-design.md` (deliberately
excludes `ai/flush-context.md` — this file — since it's single-session/
replaced-each-time, not stable content worth graph-indexing).

- `graphify hook install` (post-commit + post-checkout) is installed and
  fires automatically on every commit — AST-only re-extraction, no LLM
  cost, confirmed firing on this session's commits.
- `graphify claude install` already ran (via the skill's own bootstrap,
  not manually) — wrote the `## graphify` section in the new project-root
  `CLAUDE.md` above.
- `ai/load-context.md`'s "before changing code" checklist now has a step 0
  pointing at `graphify query/path/explain` before grepping.
- **Real, mixed results this session** (see conversation, not restated
  here): ~10-fold less useful than the abstract 233x benchmark suggested
  for narrow, single-file bugs (issue #13: 42,837 vs 47,661 tokens, only
  ~10% less, because free-text queries needed several reformulations
  before matching the graph's vocabulary). Works best for broad
  "where does X live" orientation, not for Qt event-wiring bugs
  (`QShortcut`/`keyPressEvent`) which don't produce distinctive graph
  symbols — issues #2, #24, #29 all fell back to grep after 1-3
  unproductive `graphify query` attempts, per the documented fallback rule.
- `graphify benchmark` and `graphify explain "dangerous-query guard"` /
  `"read only mode"` both confirmed working (doc→code `rationale_for`
  edges resolve correctly to `query_classifier.classify()`,
  `db_service._guard()`, `connection_panel._guard_write()`).

## Exact next step

1. Review and commit the three uncommitted fixes above (issues #2, #28,
   #29) — `git status --short` / `git diff` first; user commits/pushes
   manually unless they explicitly ask otherwise (they have both ways this
   session: sometimes asked for an explicit commit, sometimes not — always
   ask or wait to be told, don't assume).
2. Do the real on-screen check flagged for issue #29 (narrow vs. wide
   query result grid) before considering it fully verified.
3. Decide whether to commit `.claude/settings.json` + `CLAUDE.md` (the
   graphify integration) — `.claude/settings.local.json` should stay
   untracked regardless.
4. Remaining original backlog issues, not yet touched: **#10** (light mode
   contrast — may partially overlap with #28, check what's left), **#11**
   (zooming issue in SQL editor), **#12** (error display issue in SQL
   editor).
5. Still-open, older items: the `HOMEBREW_TAP_TOKEN` secret lacks push
   permission to `homebrew-qforge` (403 in CI, flagged earlier this
   session's history — needs the user to regenerate/rescope the PAT);
   production-safety Slices 4-6 (transaction controls, query limits/
   timeout/cancellation, audit trail) per `ai/load-context.md` — none
   started.
