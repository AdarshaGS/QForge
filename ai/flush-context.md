# QForge — AI Flush Context

Leave an accurate, **concise** handoff here when work pauses — a few bullets
per item, not exhaustive prose. This file tracks *current state*, not
history: full root-cause writeups and verification detail belong in the
relevant GitHub issue's comments (posted as each fix lands) and in commit
messages once committed — don't duplicate them here. Replace stale sections
outright rather than appending to them. Do not record passwords, tokens,
hostnames, customer data, or unredacted SQL.

## Current state (as of 2026-08-13)

**Branch:** `master`, HEAD `ccd2352` (pushed, tagged `v1.1.2` —
https://github.com/AdarshaGS/QForge/releases/tag/v1.1.2).

**Uncommitted in the working tree** — six separable pieces of work, not
yet committed or pushed:
- **Relicensing** off MIT → proprietary EULA (`LICENSE`, `pyproject.toml`,
  `README.md`). Template only — not lawyer-reviewed; no technical
  enforcement (license-key check) exists yet.
- **#124** — real undo/redo history in the result grid (`ui/editable_table.py`).
- **#70** — hardened read-only mode: closed a `REPLACE`/`LOAD DATA`/`CALL`
  bypass in the classifier and a table-view-grid bypass of `_guard_write`.
- **#125** — freeze/pin leading columns in the result grid, via a second
  `QTableView` sharing the main widget's model (no data duplication).
- **#62 + #63/#64/#65** — read-only ER diagram (`services/erd_model.py`,
  `ui/erd_dialog.py`, "🗺 ER Diagram" button in `connection_panel.py`).
  `QGraphicsView` canvas, dependency-free flow layout (no networkx
  available), pan/zoom/fit/select/filter/refresh, opens tables via existing
  `open_table_view`/`_show_table_structure`. New `DbService.get_primary_keys()`.
- **Schema sidebar redesign** (`ui/connection_panel.py`, no GitHub issue —
  requested directly from a reference screenshot, not filed). Replaced the
  always-nested Tables/Views/Functions tree with a flat, single-category
  list: three clickable category rows (All Tables/Views/Functions) with
  live counts, selected one highlighted; the search box filters within
  whichever category is active. `filter_tables`/`_on_item_clicked`/
  `_show_context_menu` all reworked to key off `self._active_category` +
  membership in that category's item map instead of a parent-folder check.
  Deliberately skipped, per explicit user answer: Indexes/Relationships as
  new browsable categories, and a per-table "hide from sidebar" toggle.
  Caught by an actual offscreen `.grab()` render (not just the structural
  checks, which only inspect the stylesheet string): the active-category
  blue highlight silently didn't paint at all — plain `QWidget` ignores a
  stylesheet `background` without `setAttribute(Qt.WA_StyledBackground,
  True)`. Fixed in `_ClickableRow.__init__`; re-rendered to confirm.

All six: full test suite green (84/84), README updated. Implementation
comment posted on the relevant GitHub issue(s) — except the sidebar
redesign, which has none to comment on. On-screen verification: user
confirmed done manually for everything up through #70. **#125, #62/63/64/65,
and the sidebar redesign are still offscreen-only** (30/30, 19/19, and 22/22
scripted checks respectively) — no on-screen pass yet, and #62/63/64/65 also
untested against real MySQL/PostgreSQL (SQLite only so far).

## Open threads not yet started

- **Production-safety Slices 5–6** (`ai/load-context.md`): Slice 5
  (per-profile timeout/max-rows) partially done — cancellation works,
  timeout is hardcoded (3600s) and not per-profile or user-facing; no
  max-row-limit feature exists. Slice 6 (audit trail) has nothing built.
- **#54, #75** — ON HOLD by user request, investigated with a plan at
  `/Users/adarsh/.claude/plans/tingly-wandering-bumblebee.md`, no code.
- **#61, #79, #66/#67** (Schema Compare, multi-week build) — real,
  unstarted P0 items.
- **#78 follow-ups** (connections/databases in the quick-search palette,
  indexes/FKs needing a batched `DbService` method) — open, unrelated to
  the current track.
- **VAPT security issues #113–120** (due 2026-08-31) and the Mac-only
  launch plan (`LAUNCH_PLAN.md`) — see GitHub milestones #10–15 for the
  full pre-launch/licensing timeline (target 2026-09-28).

## Exact next step

1. **#125, #62/63/64/65, and the schema sidebar redesign all need a real
   on-screen pass** (see #125/#62-65's GitHub comments for exact
   verification checklists) — freeze-columns header interaction, the ER
   diagram's pan/zoom/click/filter/open-table flow plus a real
   MySQL/PostgreSQL schema, and the sidebar's category-row styling/click
   behavior against a real large schema.
2. Decide commit strategy for the six uncommitted pieces above (separate
   vs. combined commits).
3. Then: milestone #9 is fully done in code (only #54/#75 remain, both on
   hold); ERD epic #62 is also done in code. Next could be Slice 5/6, the
   VAPT track, or a P0/P2 item like #61/#79/#66/#67. User's call.

## Milestone status

**#9 ("Aug 3rd week 2026")**: every non-held item done in code — #44, #41,
#57, #39, #122, #123, #124, #125 (all four #76 sub-issues built; column
hide/show folds into the held #54). Only **#54** and **#75** remain, both
intentionally on hold.

**#62 ERD epic (P2 — Database intelligence)**: #63/#64/#65 all done in code
as one slice — see "Uncommitted in the working tree" above.

## Where the detail lives

Root-cause writeups, exact code changes, and verification steps for past
fixes are in each issue's GitHub comments and in commit messages. `git log`
and `gh issue view <n> --comments` are the source of truth for history —
this file is not.
