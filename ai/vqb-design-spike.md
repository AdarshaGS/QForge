# Visual Query Builder — design note (VQB.1, issue #191)

Net-new drag-and-build query canvas: join tables visually, pick
columns/aggregates, build filters, and sync to SQL live. Part of milestone
#24 / parent issue #190. This note is the deliverable for VQB.1 and unblocks
VQB.2–VQB.6.

## Interaction model: canvas-based

Tables are dragged onto a freeform canvas and joins are drawn between
columns, not a form/stepper flow. `ui/erd_dialog.py` already built most of
the required canvas infrastructure for the ER Diagram feature, and VQB.2
should extend it rather than start over:

- `_TableNodeItem` (`erd_dialog.py:116`) — draggable `QGraphicsRectItem`
  table node with a collapsible column list. VQB's table node reuses this
  shape but adds a per-column checkbox (column picker, VQB.3) and an output
  join handle per row.
- `_RelationshipLineItem` + `_build_curved_path` (`erd_dialog.py:327`,
  `:273`) — curved edges between two column anchors that route around
  obstacles and carry cardinality marks. ERD relationships are inferred
  from FKs and read-only; VQB joins are the inverse — user-drawn,
  editable, and each one carries a join type (INNER/LEFT/RIGHT/FULL) shown
  as a label on the edge instead of a cardinality mark, plus an inline
  editor for the ON condition when it isn't a straight FK equality.
- `_ErdView` / `_MinimapView` (`erd_dialog.py:367`, `:436`) — pan/zoom
  `QGraphicsView` and minimap, reusable as-is.

New in VQB.2: dragging a second FK-linked table onto the canvas
auto-proposes the join (same FK lookup `ErdDialog` already uses to draw
relationships) with an accept/adjust affordance, rather than requiring the
user to draw every edge by hand.

## Panel layout

Single dialog (`ui/query_builder_dialog.py`), `QSplitter`-based, modeled on
`SqlTab`'s vertical splitter (`sql_tab.py:144`):

```
┌─────────────────────────────────────────────────────────────┐
│  Toolbar: [Add Table ▾] [Run] [Save] [Copy SQL]              │
├───────────────────────────────┬─────────────────────────────┤
│                                │  SELECT                     │
│                                │  ☑ orders.id                │
│      Join canvas               │  ☑ customers.name           │
│      (table nodes +            │  ☐ orders.total   Σ SUM ▾   │
│       join edges)              ├─────────────────────────────┤
│                                │  WHERE           [+ AND/OR] │
│                                │  orders.status = 'shipped'  │
│                                │  ├─ AND orders.total > 100  │
│                                ├─────────────────────────────┤
│                                │  GROUP BY / ORDER BY / LIMIT │
├────────────────────────────────┴─────────────────────────────┤
│  SQL preview (read-only, SqlHighlighter)                     │
└───────────────────────────────────────────────────────────────┘
```

- Left: the join canvas (VQB.2), majority of the width — this is the
  primary surface.
- Right: a stacked panel — column/aggregate picker (VQB.3), WHERE/HAVING
  filter builder (VQB.4), then GROUP BY / ORDER BY / LIMIT (VQB.5) — each
  section only shows entries relevant to tables currently on the canvas.
- Bottom: full-width SQL preview strip, `CodeEditor` + `SqlHighlighter`
  (`sql_tab.py:151`, `:156`) in read-only mode.

**Filter builder** (VQB.4) needs AND/OR grouping and nested conditions,
which `AdvancedFilterDialog` (`ui/advanced_filter_dialog.py`) does not
support today — it's a single column/operator/value row. VQB.4 is a new
tree-structured widget (each node is a condition or an AND/OR group),
reusing only `AdvancedFilterDialog`'s operator list and value-quoting
logic (`get_filter_condition`, `advanced_filter_dialog.py:104`).

## SQL sync: one-way, builder → SQL, read-only preview

Per the scope decision for v1:

- The SQL preview pane is **read-only**. Hand-editing SQL and having it
  reconcile back into the visual state is out of scope — it would require
  a SQL parser and a reconciliation layer, and the risk of the parser
  silently misreading an edit (producing a query that doesn't match what's
  displayed) is worse than not offering inline editing at all.
- **SQL → builder round-trip is explicitly out of scope for v1.** There is
  no "open this saved/hand-written query in the builder" path. The builder
  only ever starts from a blank canvas. A user who wants to hand-write SQL
  keeps using `SqlTab` as today; the two surfaces don't cross over yet.
  Revisit as its own milestone item once VQB.1–6 ship, if there's demand.
- Sync direction is builder-state → SQL string, recomputed on every
  edit (table added/removed, join changed, column toggled, filter edited,
  GROUP BY/ORDER BY/LIMIT changed) and rendered into the preview pane
  immediately — no separate "generate" step.
- "Copy SQL" and "Run" both act on the last-generated string; "Run" hands
  it to the same execution path `SqlTab` uses today rather than
  duplicating query-execution logic.

## Persistence & gating (VQB.6, forward-looking)

- Persist the **builder state** (tables, positions, joins, selected
  columns/aggregates, filter tree, group/order/limit), not just the
  generated SQL string — reopening a saved visual query must restore the
  canvas, not just re-run text. Follow `services/saved_queries.py`'s
  JSON-file pattern: a sibling store keyed by id, one JSON blob per saved
  query holding both `builder_state` and the last-generated `sql` (the
  latter kept only for display in list views, never re-parsed).
- Gate behind a new `Feature.VISUAL_QUERY_BUILDER` in
  `services/entitlements.py:42`, consistent with how `SCHEMA_COMPARE`,
  `ADVANCED_ERD`, and `DATA_COMPARE` are gated — added to
  `config.PRO_ONLY_FEATURES`.

## Entry point

New "Visual Query Builder…" action on `db_menu`, next to "ER Diagram"
(`main.py:936`), enabled only when a connection is active — same guard
`_current_panel()` already provides for the other DB-menu actions.
