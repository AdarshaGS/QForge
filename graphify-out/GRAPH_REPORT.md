# Graph Report - .  (2026-09-01)

## Corpus Check
- 160 files · ~154,772 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 2977 nodes · 5904 edges · 181 communities (142 shown, 39 thin omitted)
- Extraction: 93% EXTRACTED · 7% INFERRED · 0% AMBIGUOUS · INFERRED: 410 edges (avg confidence: 0.61)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- License Manager Tests
- Query Cost Estimation
- Db Service Sqlite Tests
- Schema Compare Dialog
- Schema Snapshot Tests
- SQL Code Editor
- Query Classifier Tests
- Export Scope Dialog Tests
- SQL Tab Toolbar & Dialogs
- Database Service Core
- Main Window Core
- Connection Panel Core
- Benchmark Harness
- PostgreSQL Integration Tests
- Editable Grid PK Tests
- Table View Streaming Load
- Connection Dialog UI Layout
- Query Cost Tests
- Data Compare Dialog
- Data Diff Engine
- Theme Manager
- Query Verifier
- ER Diagram Dialog Core
- Database Query Execution
- Perf Overlay Toggle Tests
- Upgrade to Pro Dialog
- Mock Data Dialog
- SQL Completer Core Engine
- Mock Data Generator
- Code Editor Find & Replace
- Query History
- Editable Grid Undo/Redo
- Onboarding
- Query Library Dialog
- Perf Metrics Tests
- Table Context Menu Actions
- Saved Queries
- Query Analyzer Compare Tab
- Table Structure Editor
- ER Diagram View & Minimap
- Entitlements Model
- Export Worker Tests
- Quick Search
- Query Analyzer Profile Sections
- Df Export Tests
- ER Diagram Table Boxes
- ER Diagram Model
- Database Switcher
- Connection Panel Tab Management
- Connection Panel Query Lifecycle
- Editable Grid Row Batch Ops
- Editable Grid Frozen Columns
- Row Stream Writers Tests
- Run All Statements Tests
- License Activation Dialog
- Entitlement Config Tests
- Query Analyzer Cost Estimate Tab
- Connection Panel Query Navigation Tests
- Sql Tab Result Viewer 178 Tests
- Connection Dialog Core Actions
- Connection Panel Schema Loading
- Db Service Mysql Lenient Decoding Tests
- Schema Diff Data Model
- Ddl Toggles Tests
- Install Source Tests
- Connection List Load & Migration
- SQL Snippet Manager
- SQL Completer Suggestion Scoring
- Code Editor Input Handling
- Table Structure Panels
- Ui Design
- Self-Update Detection
- Dot Export Tests
- Query History Dialog
- SQL Tab Result Grid Paging
- Launch Plan
- Database & Table Export
- Column Filter Dialog
- SQL Completer Popup Window
- Schema Migration
- Performance Metrics
- Sql Completer Schema Awareness Tests
- Column Selection Dialog
- Dangerous Query Guard
- SQL Tab Multi-Result Display
- Self Updater
- Editable Grid Header & Theming
- Editable Table Formula Tests
- Licensing Client Tests
- Database Management (Create/Drop)
- Editable Grid Composite Undo
- Editable Grid Filtering & Load
- Updater
- ER Diagram Relationship Curves
- Schema Diff Tests
- Schema Migration SQL Generation
- Db Service Select Db Tests
- Sql Completer Multiword Insert Tests
- Connection Dialog Status Feedback
- Connection Panel Tab Actions
- Table View Sort & Save
- Database Transaction Control
- Database Connection Setup
- Table Organization
- Saved Queries Tests
- Table View Reconnect Reload Tests
- Export Worker (DOT/SQL)
- SQL Go-to-Definition
- Connection Panel Status Bar 178 Tests
- Connection Panel Focus Restore Tests
- DataFrame Export
- Table View Widget Suspend Guard Tests
- Connection Dialog Save & Connect
- Saved Queries & History Filtering
- SSH Key Auth Compatibility Shim
- Connection Dialog First Run Hint Tests
- Environment Tests
- Sql Tab Go To Definition Tests
- Sql Tab Quick Fixes Tests
- Table View Widget Primary Keys Tests
- Schema Diff Engine
- PostgreSQL Durability Tests
- Generated Columns Tests
- Sql Tab Status Banner Tests
- Table View Widget Pagination Tests
- Connection Dialog Selection Handling
- Editable Grid Row Export
- Licensing Client
- PostgreSQL Test Fixtures
- Stream Table Rows Tests
- SQL Completer Item Rendering
- Table View Reload & Pagination
- Deploy
- Sql Highlighter
- Editable Grid Copy & Context Menu
- ER Diagram Layout
- Update Install & Relaunch
- Sql Tab Result Primary Keys Tests
- SQL Tab Theming
- Sql Tab Escape Dismisses Popup Tests
- Editable Grid Change SQL Generation
- Query Cost Status Badge
- SQL Result Diff Highlighting
- Memory
- Conftest
- PostgreSQL Reconnect Tests
- Connections File Sanitization
- Editable Grid Cell Detail Popup
- SQL Tab Save Shortcut
- SQL Tab Query Extraction
- Docker Compose
- Build
- Downloadedfile
- Data Compare Design
- Flush Context — Current State
- Flush Context — Milestone Status
- Flush Context — Open Threads
- Flush Context — Where Detail Lives
- Load Context
- Benchmarking
- Miscellaneous
- Add To Project
- Logo
- Pyproject
- README — Compare Schemas
- README — Control a Transaction
- README — Explore an ER Diagram
- README — Read-Only Mode
- Miscellaneous
- Miscellaneous
- Miscellaneous
- Miscellaneous
- Miscellaneous
- Miscellaneous
- Miscellaneous
- Miscellaneous
- Miscellaneous
- Miscellaneous
- Miscellaneous

## God Nodes (most connected - your core abstractions)
1. `ConnectionPanel` - 179 edges
2. `DbService` - 163 edges
3. `SqlTab` - 138 edges
4. `EditableTableWidget` - 86 edges
5. `ConnectionDialog` - 71 edges
6. `TableViewWidget` - 71 edges
7. `MainWindow` - 67 edges
8. `ExportScopeDialog` - 48 edges
9. `SchemaCompareDialog` - 42 edges
10. `ErdDialog` - 40 edges

## Surprising Connections (you probably didn't know these)
- `Downloaded File Placeholder` --semantically_similar_to--> `Local File Placeholder`  [INFERRED] [semantically similar]
  downloadedfile.txt → localfile.txt
- `MainWindow` --uses--> `DbService`  [INFERRED]
  main.py → services/db_service.py
- `MainWindow` --uses--> `Edition`  [INFERRED]
  main.py → services/entitlements.py
- `MainWindow` --uses--> `QueryHistory`  [INFERRED]
  main.py → services/query_history.py
- `MainWindow` --uses--> `SavedQueries`  [INFERRED]
  main.py → services/saved_queries.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Disagreeing Pricing Numbers Across Docs** — launch_plan_personal_license_pricing, product_strategy, readme_free_pro_tiers [INFERRED 0.75]

## Communities (181 total, 39 thin omitted)

### Community 0 - "License Manager Tests"
Cohesion: 0.08
Nodes (54): Ed25519PrivateKey, _issue(), _keygen(), _load_private_key(), main(), build_key_string(), canonical_payload_bytes(), _decode_key_string() (+46 more)

### Community 1 - "Query Cost Estimation"
Cohesion: 0.07
Nodes (59): generate_optimized_sql(), html_escape(), list_sql_files(), load_connections(), main(), open_connection(), pick_connection(), print_summary() (+51 more)

### Community 2 - "Db Service Sqlite Tests"
Cohesion: 0.04
Nodes (17): db(), db_path(), evil_db(), fixture, No begin_transaction() call — every statement still persists immediately,…, Regression test for the bug this slice fixes: a write statement inside a manual…, A user can type BEGIN/COMMIT directly in the editor instead of clicking the…, A fully independent DbService to the same file — proves durability from a… (+9 more)

### Community 3 - "Schema Compare Dialog"
Cohesion: 0.06
Nodes (21): Open the read-only Schema Compare dialog (issue #68), preselecting this…, _diff_is_destructive(), _MigrationReviewDialog, _Placeholder, QColor, QComboBox, QDialog, QFrame (+13 more)

### Community 4 - "Schema Snapshot Tests"
Cohesion: 0.07
Nodes (41): fetch_schema_snapshot(), Fetch a read-only snapshot of a database's schema. Uses its own dedicated,…, Connect using `config`, gather schema metadata, then disconnect. Returns a dict…, _connected_db(), Issue #72: a successful schema-changing statement must drop the on-disk schema…, test_alter_table_invalidates_cache(), test_connection_without_id_never_touches_cache_file(), test_create_table_invalidates_cache() (+33 more)

### Community 5 - "SQL Code Editor"
Cohesion: 0.06
Nodes (26): _asset_path(), Resolve a bundled asset both when running from source and when frozen by…, QPainter, QRect, QSize, QStyleOptionViewItem, _bracket_spans(), Tests for CodeEditor's matching-bracket highlight (issue #208) — the class… (+18 more)

### Community 6 - "Query Classifier Tests"
Cohesion: 0.09
Nodes (44): Classification, classify(), is_dangerous(), Classifies SQL statements for the read-only guard and dangerous-query guard…, True if this statement should trigger the dangerous-query confirmation…, Single source of truth for splitting a script into statements — replaces the…, split_statements(), _col() (+36 more)

### Community 7 - "Export Scope Dialog Tests"
Cohesion: 0.06
Nodes (32): Tests for the per-table Structure/Content/Drop export grid (issue #157),…, test_advanced_options_defaults(), test_auto_increment_and_strip_generated_default_on_mysql(), test_auto_increment_and_strip_generated_disabled_outside_sql_tab(), test_auto_increment_disabled_on_postgresql_and_sqlite(), test_batch_kib_only_set_when_toggle_checked(), test_bulk_select_only_touches_columns_relevant_to_current_format(), test_csv_tab_only_content_is_relevant() (+24 more)

### Community 8 - "SQL Tab Toolbar & Dialogs"
Cohesion: 0.05
Nodes (20): QWidget, Open the snippet management dialog., Called when snippets are changed in the editor dialog., Enable commit/revert buttons when changes are made, Minify/compress SQL query., Show filter dialog for current results, Add filter input boxes to column headers, Update status when filters change (+12 more)

### Community 9 - "Database Service Core"
Cohesion: 0.06
Nodes (23): DbService, Get columns for a table based on database type, Return this table's CREATE TABLE statement, followed by its indexes and foreign…, Return {table_name: [col_name, ...]} for all tables in one query. Used to…, Return {table_name: [{name, type, nullable, default, key}, ...]} for every…, Return {table_name: [{column, ref_table, ref_column}, ...]} for every table in…, Quote *identifier* (table/column/index name) for this connection's dialect,…, Return index definitions for *table_name*. Each dict has: name, columns,… (+15 more)

### Community 10 - "Main Window Core"
Cohesion: 0.08
Nodes (12): MainWindow, Adjust *widget*'s font size relative to its last zoom level. Reading…, Background, best-effort fetch of the remote entitlement override…, Close the connection panel at *index*. No confirmation unless one of its tabs…, Close all connection tabs except the one at *keep_index*., Cmd+W: close current content tab; if that was the last tab for this connection,…, Right-click menu on a connection tab., Top-level window. A QTabBar at the top holds one tab per open database… (+4 more)

### Community 11 - "Connection Panel Core"
Cohesion: 0.06
Nodes (14): ConnectionPanel, Receives worker `cost_ready` signal via bridge — best-effort, never fires for a…, True if any tab in this connection has an open manual transaction — used to…, Read-only popup showing a function/procedure's CREATE statement (issue:…, Build the full (item_type, display_text, payload) list for the command palette:…, Fuzzy-match the active category's item names (tables, views, or functions —…, QSS for the Schema/Queries/History toggle buttons — set directly per-widget…, Return serialisable list of open tabs. (+6 more)

### Community 12 - "Benchmark Harness"
Cohesion: 0.08
Nodes (34): benchmarks(), _make_sqlite_db(), Connection & database switching benchmarks (issue #60). Sqlite-only, same…, benchmarks(), _make_sqlite_db(), Backend benchmarks: DB connection establishment + query execution (sqlite).…, benchmarks(), _construct_one_main_window() (+26 more)

### Community 14 - "Editable Grid PK Tests"
Cohesion: 0.07
Nodes (29): _edit_cell(), Tests for EditableTableWidget.get_changes()'s WHERE-clause column selection.…, The declared PK isn't among this result set's columns (e.g. a hand-written…, The WHERE clause's original-value literal previously wasn't escaped (only the…, A fresh load_data() (different table/query) must not keep a stale PK from…, Primary key is `id` (column 1, not column 0) — the WHERE clause must key off…, No primary_key_columns set (e.g. table has no PK, or the caller never resolved…, The WHERE clause must use the row's original id, not the new one just typed, or… (+21 more)

### Community 15 - "Table View Streaming Load"
Cohesion: 0.05
Nodes (19): Widget that shows a table with streaming data loading, Update loading overlay to cover the table container, Toggle filter visibility with Cmd+F, Hide filter with Esc key, Open a table view; re-focus if already open., Open a blank SQL query tab., Execute the SQL in `tab` using this connection's db_service., Switch to the Structure tab, loading it on first use. (+11 more)

### Community 16 - "Connection Dialog UI Layout"
Cohesion: 0.12
Nodes (14): QHBoxLayout, QLineEdit, QPlainTextEdit, QPushButton, QTextEdit, QWidget, Size *edit* to its own text, not the row's full width: stays at the medium…, QWidget (+6 more)

### Community 17 - "Query Cost Tests"
Cohesion: 0.06
Nodes (5): skipif, fixture, Tests for services/query_cost.py. MySQL coverage is pure unit tests against the…, sqlite_db(), TestPostgresLive

### Community 18 - "Data Compare Dialog"
Cohesion: 0.09
Nodes (10): Open the read-only Data Compare dialog (issue #197/#204), preselecting this…, DataCompareDialog, QComboBox, QDialog, *conn_combo*'s resolved config, with "database" overridden to whatever…, Rebuilds the left tree from self._category_data, honoring the active change-…, A concise one-line Detail-column summary — "3 fields changed" for a modified…, Opens standalone — the caller only supplies which connection id to preselect as… (+2 more)

### Community 19 - "Data Diff Engine"
Cohesion: 0.17
Nodes (30): _append_capped(), build_data_diff(), DataDiff, _diff_by_key(), _diff_whole_rows(), _fetch_side(), _index_by_key(), Build a read-only row-level data diff between two database connections (issue… (+22 more)

### Community 20 - "Theme Manager"
Cohesion: 0.11
Nodes (19): Data Compare — read-only row-level data diff between two saved connections…, Structured error dialog for failed grid edits (issue #143). Replaces…, _load_connection_profiles(), _profile_label(), Schema Compare — read-only structural diff between two saved connections (issue…, Raw (credential-free) connection list for populating the source/ target pickers…, ThemeManager — QForge design system (see ai/ui-design.md for the source of…, Render a 16x16 'x' close-tab icon in *color_hex* to a cached PNG and return its… (+11 more)

### Community 21 - "Query Verifier"
Cohesion: 0.11
Nodes (18): AggRow, ColDiffRow, ExplainRow, QueryVerifier, QueryVerifier ───────────── Runs two SQL queries (original vs optimised) and…, Compare two SQL queries using an existing DbService connection., Run EXPLAIN and return list[ExplainRow]. Non-fatal on any error., One row from EXPLAIN output — key fields only. (+10 more)

### Community 22 - "ER Diagram Dialog Core"
Cohesion: 0.12
Nodes (4): ErdDialog, QDialog, Opens against a connection profile's config dict (same shape passed to…, Select *table_name*'s node (highlighting it and its relationship edges, same as…

### Community 23 - "Database Query Execution"
Cohesion: 0.09
Nodes (16): Exception, Other databases reachable on this already-open connection's host — used by…, Raise ReadOnlyViolation if *sql* contains a write statement and this connection…, Return True if the exception looks like a dropped/lost connection., Re-establish the connection using *config*, or the previously stored config if…, Point the live connection at *database* (MySQL only — Postgres connections are…, Return 'BEGIN'/'COMMIT'/'ROLLBACK' if *query* is exactly one transaction-…, Execute a SELECT query and return results as DataFrame. max_rows: if given,… (+8 more)

### Community 24 - "Perf Overlay Toggle Tests"
Cohesion: 0.12
Nodes (20): _MainWindowStub, QWidget, Regression guard for the dev performance overlay (issue #43): the shortcut…, Regression guard for issue #172: import/export operations had no…, Regression guard for issue #169: without word wrap, a stat line wider than the…, Regression guard for issue #170: CPU utilization was computed for the offline…, test_active_connections_counts_only_live_db_service_connections(), test_label_has_word_wrap_enabled() (+12 more)

### Community 25 - "Upgrade to Pro Dialog"
Cohesion: 0.14
Nodes (19): Enum, Edition, Feature, Limit, services/entitlements.py — the centralized Free/Pro entitlement model (issue…, _QueryWorker, ConnectionPanel ═══════════════ A self-contained widget that owns one database…, Best-effort pre-run cost estimate for the status-bar badge — plan-only (EXPLAIN… (+11 more)

### Community 26 - "Mock Data Dialog"
Cohesion: 0.12
Nodes (15): _col(), _make_dialog(), Dialog-level regressions for the Mock Data Generator (issue #180): switching a…, test_not_null_column_set_to_always_null_warns(), test_preview_grid_never_renders_more_than_the_cap(), test_row_count_is_capped_to_a_safe_maximum(), test_switching_away_from_omit_does_not_override_a_deliberate_exclude(), test_switching_away_from_omit_restores_included_state() (+7 more)

### Community 27 - "SQL Completer Core Engine"
Cohesion: 0.08
Nodes (11): Drives autocomplete for a QTextEdit. Design goals ───────────── • GENERAL…, Replace the snippet cache (called after SnippetManager is updated)., Refresh schema cache (called on connect / db switch).…, Every table and view name the active connection reported., A bare table/view name, or an alias already used in the current query (via…, Column name(s) of `table` marked primary key in the bulk-fetched column_details…, Recompute and display suggestions for the current editor state. force=True →…, Return the token (incl. alias.col dot notation) ending at pos. (+3 more)

### Community 28 - "Mock Data Generator"
Cohesion: 0.13
Nodes (26): date, build_insert_sql(), ColumnSpec, _gen_address(), _gen_boolean(), _gen_date(), _gen_datetime(), _gen_email() (+18 more)

### Community 29 - "Code Editor Find & Replace"
Cohesion: 0.11
Nodes (10): QTextCursor, Merge CodeEditor's own selections (current-line highlight + matching-bracket…, Cmd+F: open Quick Filter when the result grid has focus (issue #2), otherwise…, Ctrl+Alt+F (Cmd+Option+F on macOS): show find+replace bar., The visible 'Replace' toggle button in the find bar (issue #112) — lets Replace…, Single place that shows/hides the replace row and keeps the toggle button's…, Build a regex pattern from the current search text + toggles. Smart-case: if…, Remove all orange match highlights from the editor. (+2 more)

### Community 30 - "Query History"
Cohesion: 0.09
Nodes (13): QueryHistory, Simple query history manager, Load query history from file, Save query history to file, Add a query to history. cost_score/cost_label are the optional pre-run estimate…, Search queries by keyword, _MainWindowStub, QDialog (+5 more)

### Community 31 - "Editable Grid Undo/Redo"
Cohesion: 0.09
Nodes (12): _CellEditCommand, QColor, Apply the correct colour to every cell in *row* based on its state., Legacy helper — delegates to _repaint_row when state is already set., One cell's text changed from old_text to new_text., (at_row, delta) this command's own undo/redo causes to every OTHER row index —…, row was toggled into/out of the pending-deletion set., Track when an item is modified, push an undo step, and paint changed cell + row. (+4 more)

### Community 32 - "Onboarding"
Cohesion: 0.11
Nodes (14): _is_remote_connection(), True for connections where establishing db_service.connect() is slow enough…, saved_queries.py — Persistent store for user-saved SQL queries. Backs the…, Regression guard: after a fresh connection's modal ConnectionDialog closes, the…, Persist per-table column widths across sessions., dismiss_connection_hint(), is_connection_hint_dismissed(), Persist one-time first-run UI state across sessions (issue #164). Currently… (+6 more)

### Community 33 - "Query Library Dialog"
Cohesion: 0.13
Nodes (11): _panel_with_one_sql_tab(), _PanelStub, Opening or saving a saved query should name its tab after the query, not leave…, _store(), test_query_library_dialog_exposes_selected_entrys_name(), test_save_current_query_renames_the_tab_to_the_name_just_given(), test_use_saved_query_leaves_tab_name_alone_when_entry_has_no_name(), test_use_saved_query_renames_tab_to_query_name() (+3 more)

### Community 34 - "Perf Metrics Tests"
Cohesion: 0.11
Nodes (25): Regression guard for issue #172 (timing) and #171 (in-flight gauge): run() must…, test_run_records_perf_metrics_and_clears_active_task(), Regression guard for issue #174 — doesn't wait for the real watchdog thread…, setup_function(), test_counter_inc_and_get(), test_likely_suspended_between_false_with_no_windows(), test_likely_suspended_between_true_when_windows_overlap(), test_record_and_snapshot_basic_stats() (+17 more)

### Community 35 - "Table Context Menu Actions"
Cohesion: 0.10
Nodes (8): Full table/view context menu (issue #142, TablePlus parity). Grouped:…, Open a read-only ER diagram of the current database (issue #62). Builds its own…, Opens the Mock Data Generator (issue #77). Environment/read-only safety gating…, Read a CSV file and INSERT all rows into *table_name*., Export just the rows (context menu's "Export Table Data") without the…, Export structure + data as a single .sql file (context menu's "Export Table as…, StructureEditorDialog already supports a "New Table" mode (table_name=None) —…, Classify *sql* (one statement or a whole script) and show whatever dialog is…

### Community 36 - "Saved Queries"
Cohesion: 0.13
Nodes (9): CRUD + persistence for named, favoritable saved SQL queries., SavedQueries, _MainWindowStub, QDialog, QWidget, Regression test for issue #173: _prompt_new_connection() must accumulate…, First call: accepted but with no connection selected (get_selected_connection()…, _RepromptThenCancelDialog (+1 more)

### Community 37 - "Query Analyzer Compare Tab"
Cohesion: 0.16
Nodes (11): _check_row(), _CompareQueriesTab, _extract_params(), _plan_node_card(), _plan_tree_widget(), QLabel, QWidget, Recreates the plan hierarchy as a small vertical flow of node cards connected… (+3 more)

### Community 38 - "Table Structure Editor"
Cohesion: 0.10
Nodes (12): QDialog, Enable/disable length input based on type, Enable/disable auto increment when primary key is toggled, Add column to the table, Remove selected column, Dialog for creating/editing table structure, Generate CREATE TABLE or ALTER TABLE SQL, Generate CREATE TABLE SQL (+4 more)

### Community 39 - "ER Diagram View & Minimap"
Cohesion: 0.11
Nodes (9): QGraphicsView, QRectF, _ErdView, _InspectorPanel, _MinimapView, QWidget, Wheel-to-zoom; left-drag on empty canvas (or empty space between nodes) pans,…, Small always-fit overview sharing the main scene. Draws the main view's visible… (+1 more)

### Community 40 - "Entitlements Model"
Cohesion: 0.15
Nodes (12): Entitlements, None means unlimited., Called once per app run when utils/entitlement_fetcher.py successfully…, _as_free(), _as_pro(), _fresh(), test_apply_remote_config_can_explicitly_free_a_pro_only_feature(), test_apply_remote_config_overrides_only_provided_keys() (+4 more)

### Community 41 - "Export Worker Tests"
Cohesion: 0.15
Nodes (23): db_path(), db_path_with_generated_column(), _export_db(), fixture, Tests for _ExportWorker (issue #158): the producer/queue pipeline that streams…, Regression for issue #163 (Zip Slip): a table name containing path…, _run(), test_batching_reduces_insert_statement_count() (+15 more)

### Community 42 - "Quick Search"
Cohesion: 0.11
Nodes (13): QKeyEvent, _collect_actions(), Command palette: search and run any app menu action by name (issue #232).…, {key: QAction} for every enabled, non-separator, leaf action under *menu_bar* —…, show_command_palette(), QDialog, QuickSearchDialog, Handle arrow key navigation from search input (+5 more)

### Community 43 - "Query Analyzer Profile Sections"
Cohesion: 0.21
Nodes (13): _count_plan_nodes(), _divider(), _issue_card(), _na(), _plan_depth(), QFrame, QueryAnalyzerDialog ──────────────────── Consolidated query-analysis dialog —…, Render a possibly-unavailable metric — never fabricate a number when the… (+5 more)

### Community 44 - "Df Export Tests"
Cohesion: 0.14
Nodes (17): Regression for issue #162: an embedded backtick/quote in a table/column name…, Regression: batch_kib=None (the default) must reproduce today's exact one-row-…, test_quote_identifier_escapes_embedded_quote_char_per_dialect(), test_sql_value_literal_blob_as_hex_false_falls_back_to_null(), test_sql_value_literal_hex_encodes_blobs_per_dialect(), test_sql_value_literal_leaves_numbers_unquoted(), test_sql_value_literal_null_for_none_and_nan(), test_sql_value_literal_quotes_and_escapes_strings() (+9 more)

### Community 45 - "ER Diagram Table Boxes"
Cohesion: 0.12
Nodes (8): QGraphicsRectItem, QGraphicsSimpleTextItem, _CollapseToggle, _header_color(), QColor, Small header glyph that collapses/expands its parent table node, intercepting…, One table box: header + one row per column. Draggable (ItemIsMovable) and…, _TableNodeItem

### Community 46 - "ER Diagram Model"
Cohesion: 0.18
Nodes (20): build_erd_graph(), ErdColumn, ErdGraph, ErdRelationship, ErdTable, fetch_table_indexes(), _is_unique_single_column(), Build a read-only ER-diagram graph model from database metadata (issue #63).… (+12 more)

### Community 47 - "Database Switcher"
Cohesion: 0.15
Nodes (12): _dialog(), Tests for DbSwitcherDialog's click-outside-to-close behavior (issue #234). Uses…, test_escape_still_closes_the_popup(), test_internal_focus_change_does_not_select_or_close(), test_picking_an_item_still_emits_db_selected(), test_uses_popup_window_type(), Open the Cmd+K database switcher dialog., DbSwitcherDialog (+4 more)

### Community 48 - "Connection Panel Tab Management"
Cohesion: 0.10
Nodes (10): Open a table view; re-focus if already open. *silent* suppresses the Free-tier…, Guarantee this panel has at least one query tab. Called once right after a…, True if one more tab stays within Limit.MAX_QUERY_TABS — query tabs and table-…, Open a blank SQL query tab. Returns the new tab, or None if the tab cap (issue…, Open the consolidated Analyze Query dialog (Cost & Profile + Compare Queries),…, Place a visible × QPushButton on the tab at the given index., Open (or focus) *table_name*'s tab and switch it to the Structure sub-tab…, No dedicated CREATE VIEW UI exists — open a fresh SQL tab with a template so… (+2 more)

### Community 49 - "Connection Panel Query Lifecycle"
Cohesion: 0.12
Nodes (9): Restore the Run button to its default ready state., Load FK map for *table_name* into the result grid and ensure the navigate_fk…, Receives worker `done` signal via bridge — guaranteed main thread., Receives worker `errored` signal via bridge — guaranteed main thread., Receives worker `cancelled` signal via bridge — guaranteed main thread., Called at the end of every query-completion handler. Decides whether this tab's…, Manually reconnect to the database and reload the schema., Retry every open TableViewWidget currently stuck on a connection error, now… (+1 more)

### Community 50 - "Editable Grid Row Batch Ops"
Cohesion: 0.13
Nodes (9): Physically insert a new row at *row* with the given per-column text, marking it…, Mark selected rows for deletion (Ctrl/Cmd+Z steps back through them one row at…, Paste from clipboard, walking *visually* adjacent columns from the anchor cell…, Duplicate current row, A new row was inserted at row with the given per-column text values., Duplicate all selected rows (Cmd+D), Record cmd as the next undoable step, or fold it into the batch currently being…, Start grouping subsequent _push_history calls into one undo step (e.g. a paste… (+1 more)

### Community 51 - "Editable Grid Frozen Columns"
Cohesion: 0.10
Nodes (9): Draw the active sort column/direction directly into the header text. Qt's…, Inner (non-reentrant) implementation of _display_data., Re-apply colour to all rows that have a known dirty state. Called after every…, Freeze the leading *count* (current visual order) columns so they stay visible…, Arrange the frozen view's header sections in the same left-to-right visual…, Show only the frozen columns in the overlay view; the main view keeps showing…, Position/size the overlay so it exactly covers the frozen columns' header +…, Right-click a column header: freeze up through that column, or unfreeze if a… (+1 more)

### Community 52 - "Row Stream Writers Tests"
Cohesion: 0.14
Nodes (14): Tests for the CSV/XML row writers (issue #159)., Issue #115: a cell value starting with =, +, -, or @ is read as a formula by…, test_csv_writer_decodes_blobs_when_hex_disabled(), test_csv_writer_hex_encodes_blobs_by_default(), test_csv_writer_neutralizes_leading_formula_characters(), test_csv_writer_none_becomes_empty_cell(), test_csv_writer_writes_header_then_rows(), test_xml_writer_escapes_special_characters() (+6 more)

### Community 53 - "Run All Statements Tests"
Cohesion: 0.14
Nodes (12): _MultiDonePanelStub, _PanelStub, Regression tests for Run's default multi-statement behavior: with no text…, Just enough of ConnectionPanel for _on_query_multi_done() to run against a real…, Just enough of ConnectionPanel for _run_query_in_tab()'s query- resolution step…, _tab_with_cursor_in_first_statement(), test_a_single_statement_still_runs_normally_with_no_selection(), test_multi_done_mixes_selects_writes_and_errors_in_their_own_tabs() (+4 more)

### Community 54 - "License Activation Dialog"
Cohesion: 0.16
Nodes (7): _LicenseActionWorker, LicenseDialog, _mask_email(), QDialog, QThread, ui/license_dialog.py — view current edition, activate or deactivate a Pro…, Runs a single license_manager call (activate or deactivate) off the UI thread…

### Community 55 - "Entitlement Config Tests"
Cohesion: 0.19
Nodes (17): _clean_limits_section(), parse_remote_override(), services/entitlement_config.py — the single place every Free/Pro tunable value…, Validate an untrusted remote JSON payload against the shape of the bundled…, test_blank_price_label_is_dropped(), test_bool_is_not_accepted_as_a_limit_value(), test_explicit_empty_feature_list_is_preserved(), test_full_valid_payload_passes_through() (+9 more)

### Community 56 - "Query Analyzer Cost Estimate Tab"
Cohesion: 0.20
Nodes (5): CostEstimate, Pre-run, plan-only estimate — never executes the user's query., _CostProfileTab, Single-query cost estimate (plan-only, never executes) plus an opt-in post-run…, Section-11 safety gate: EXPLAIN ANALYZE genuinely runs the statement, so a…

### Community 57 - "Connection Panel Query Navigation Tests"
Cohesion: 0.16
Nodes (11): _panel_with_table_view_active_and_sql_tab_in_background(), _PanelStub, Regression tests for issue #175 — opening a saved query (or history entry)…, Just enough of ConnectionPanel for _active_sql_tab()/_use_saved_query()/…, Mirrors the bug repro: a table Data/Structure view (any non-SqlTab widget) is…, test_active_sql_tab_brings_a_background_sql_tab_to_front(), test_use_history_item_does_not_force_sidebar_back_to_schema(), test_use_saved_query_does_not_force_sidebar_back_to_schema() (+3 more)

### Community 58 - "Sql Tab Result Viewer 178 Tests"
Cohesion: 0.18
Nodes (18): Tests for issue #178's SqlTab-level pieces: the empty-state illustration for a…, Rows/time now live only in the bottom status bar — showing them a second time…, The one thing status_label is still for: a warning the bottom bar has no room…, The row-action icon toolbar was removed from the success path entirely per…, _shown_tab(), test_cursor_position_label_tracks_the_editor_live(), test_download_icon_triggers_export_data(), test_filter_icon_triggers_toggle_filter() (+10 more)

### Community 59 - "Connection Dialog Core Actions"
Cohesion: 0.12
Nodes (4): ConnectionDialog, QDialog, Deselect tree and clear form to create a new connection., Auto-suggest Read-only when the user picks Staging/Production — one-directional…

### Community 60 - "Connection Panel Schema Loading"
Cohesion: 0.16
Nodes (7): QTreeWidgetItem, Populate the tree/autocomplete from a cached snapshot (issue #71) immediately;…, Attach a live elapsed-time ticker to *item* (already inserted in the tree) for…, Fetch schema on a daemon thread using a *dedicated* connection (see…, Push tables/columns to autocomplete as soon as they're fetched — ahead of the…, Main-thread: populate the schema tree from background result., notify=True (explicit "Refresh Schema" actions only, not the initial connect /…

### Community 61 - "Db Service Mysql Lenient Decoding Tests"
Cohesion: 0.19
Nodes (14): _ensure_lenient_mysql_decoding(), _lenient_read_row_from_packet(), Drop-in replacement for pymysql.connections.MySQLResult's own row-decoder…, Idempotently patch pymysql to survive non-UTF-8-clean string columns (issue…, _FakePacket, _FakeResult, Regression tests for issue #150 — a non-UTF-8-clean byte in a MySQL string…, Stands in for pymysql's MysqlPacket — read_length_coded_string() is the only… (+6 more)

### Community 62 - "Schema Diff Data Model"
Cohesion: 0.27
Nodes (16): ColumnDiff, ColumnInfo, TableDiff, _alter_table_sql(), _column_def_sql(), _col(), Tests for the schema-compare migration SQL generator (issue #69)., Issue #114: a crafted/compromised source database could report an 'int'-typed… (+8 more)

### Community 63 - "Ddl Toggles Tests"
Cohesion: 0.17
Nodes (15): Tests for the auto-increment-value and generated-column DDL text transforms…, test_strip_auto_increment_value_is_noop_without_clause(), test_strip_auto_increment_value_removes_clause(), test_strip_generated_column_clauses_handles_multiple_columns(), test_strip_generated_column_clauses_handles_nested_parens(), test_strip_generated_column_clauses_is_noop_without_clause(), test_strip_generated_column_clauses_removes_simple_expression(), test_drop_table_statement_quotes_per_dialect() (+7 more)

### Community 64 - "Install Source Tests"
Cohesion: 0.14
Nodes (13): issue #79: installation-source detection must correctly tell a Homebrew-managed…, test_detect_direct_when_brew_missing(), test_detect_direct_when_cask_not_installed(), test_detect_homebrew_when_cask_listed(), test_detect_unknown_when_brew_errors(), HomebrewUpdateInstaller, QThread, Runs `brew upgrade --cask qforge` on a worker thread for a Homebrew-managed… (+5 more)

### Community 65 - "Connection List Load & Migration"
Cohesion: 0.13
Nodes (7): Rebuild self.connections order to match the new drag-dropped tree order., Show context menu on right-click over a connection item., connections.json can hold plaintext passwords (the OS-keychain fallback — see…, Rebuild the group combo items from all saved connections., Assign a stable id to every connection, migrating any legacy plaintext password…, Fetch a single credential from the OS keychain on demand, caching the result…, Write this dialog session's known value of a credential to the keychain,…

### Community 66 - "SQL Snippet Manager"
Cohesion: 0.15
Nodes (7): snippet_manager.py — Persistent SQL snippet store. Snippets are keyed by a…, Return a copy of all snippets., Create or update a snippet., Restore built-in snippets (keeps user-added ones)., Merge snippets from *path* and return the number imported., CRUD + persistence for user-defined SQL snippets., SnippetManager

### Community 67 - "SQL Completer Suggestion Scoring"
Cohesion: 0.17
Nodes (8): Return snippet SuggestionItems whose trigger matches prefix., Return table names actually referenced in FROM/JOIN/UPDATE/INTO., Type/PK/FK badge info for one column, from set_schema()'s optional…, Like _score() but for real table columns — attaches the type/PK/FK badge info…, Score each name against prefix: exact match → base + 200 starts-with (short) →…, Suggest alias.col format for all known aliases., When the table/view right after the most recent JOIN is already fully typed,…, SuggestionItem

### Community 68 - "Code Editor Input Handling"
Cohesion: 0.11
Nodes (9): Resolve the identifier under the mouse against the completer's schema…, Route key events: popup navigation first, then auto-trigger., Insert a matching closing quote and place the cursor between them. If the…, Return True if the editor cursor is currently inside a quoted string literal., Toggle -- comment on each selected line (or current line)., Cmd+D: expand selection to current word, then find and select next match., Delete the entire line the cursor is on (Cmd+Backspace / ⌘⌫)., Update autocomplete with schema information (+1 more)

### Community 69 - "Table Structure Panels"
Cohesion: 0.16
Nodes (11): _fill_columns_table(), _fill_fk_table(), _fill_indexes_table(), _filter_table_rows(), _new_readonly_table(), QTableWidget, QWidget, Live, in-memory, case-insensitive row filter (issue #53). (+3 more)

### Community 70 - "Ui Design"
Cohesion: 0.15
Nodes (15): Accessibility and quality checks, Component rules, Connection Tab/Title Label Line, Dark — graphite blue, Dark Palette — Graphite Blue, Design direction, Environment safety indicators, Implementation notes (+7 more)

### Community 71 - "Self-Update Detection"
Cohesion: 0.16
Nodes (6): Detected once per session and cached (issue #79) \u2014 a local `brew list`…, Self-update needs an actual .dmg asset and a running .app bundle to replace…, What clicking Update will actually do, for the banner/dialog text \u2014 a…, Triggered by Help → Check for Updates. Shows a dialog with result., Path to the .app bundle currently running, or None if not frozen., running_app_bundle_path()

### Community 72 - "Dot Export Tests"
Cohesion: 0.22
Nodes (11): _FakeDb, Tests for the Dot schema/FK diagram export (issue #159)., test_build_dot_graph_draws_edge_for_foreign_key_within_selection(), test_build_dot_graph_empty_table_list(), test_build_dot_graph_includes_every_selected_table_as_a_node(), test_build_dot_graph_skips_fk_pointing_outside_the_selection(), build_dot_graph(), _column_names() (+3 more)

### Community 73 - "Query History Dialog"
Cohesion: 0.15
Nodes (8): QDialog, QueryHistoryDialog, Filter history based on search text, Handle selection change, Use the selected query, Get the selected query, Dialog to view and select from query history, Load history into the list

### Community 74 - "SQL Tab Result Grid Paging"
Cohesion: 0.12
Nodes (7): Tell result_table the real primary-key column(s) of current_table_name (from…, Display the current page of _result_view_df in result_table. Only 500 rows are…, Sort the full result dataset by column *col* then refresh page 0., Pre-populate the filter bar with the clicked cell value and show it., Toggle filter visibility, Apply all filter conditions to the dataframe, Clear all filters and show original data

### Community 75 - "Launch Plan"
Cohesion: 0.13
Nodes (15): AI Flush Context, Production Safety Slices Roadmap, Build & Release Workflow, Launch Plan, 1. Pricing, 2. Investment to build the launch pieces, 3. Apple Developer ID + notarization timeline, Personal License Pricing (+7 more)

### Community 76 - "Database & Table Export"
Cohesion: 0.14
Nodes (6): Event, Optimistic open (previously-visited remote/SSH connection with a warm schema…, Export chosen tables' structure and/or data to a single SQL dump (issue #39:…, Prompt for a save path whose filter/extension matches *scope*'s selected format…, Stream *table_opts* to *file_path* on a background QThread via _ExportWorker…, Export a single table without needing an open query tab (issue #39), via the…

### Community 77 - "Column Filter Dialog"
Cohesion: 0.16
Nodes (6): QComboBox, ColumnFilterDialog, QDialog, Get the current filters, Dialog for filtering table data by column and value, Update the display of active filters

### Community 78 - "SQL Completer Popup Window"
Cohesion: 0.15
Nodes (7): QPoint, QFrame, QListWidgetItem, Frameless floating window shown below the cursor. Never steals keyboard focus…, Construct the popup on first real use — see __init__ for why., Route a key event to the popup. Returns True if the event was consumed (caller…, SqlCompletePopup

### Community 79 - "Schema Migration"
Cohesion: 0.17
Nodes (13): IndexDiff, _default_clause(), _index_sql(), _quoted_column_list(), Generates a reviewable SQL migration script from a SchemaDiff (issue #69): DDL…, *cols* is DbService.get_indexes()'s comma-joined 'columns' field — raw,…, Emits DEFAULT <val> unquoted only when *val* itself parses as a number — not,…, Issue #114: DbService.get_indexes()'s 'columns' field is a raw, unquoted,… (+5 more)

### Community 80 - "Performance Metrics"
Cohesion: 0.17
Nodes (14): Calling it repeatedly (every MainWindow construction in tests that build a real…, test_active_tasks_tracks_started_and_finished(), test_start_suspend_watchdog_is_idempotent(), test_task_finished_without_started_does_not_go_negative(), Regression guard for issue #171: no in-flight background-operation indicator…, test_refresh_shows_background_task_count(), active_tasks(), In-process runtime performance registry (issue #43). Shared by every live… (+6 more)

### Community 81 - "Sql Completer Schema Awareness Tests"
Cohesion: 0.29
Nodes (14): _find(), _make_completer(), Tests for the richer schema payload SqlCompleter.set_schema() now accepts…, A real SqlCompleter over a throwaway editor, schema pre-loaded with…, test_column_suggestion_carries_type_badge(), test_fk_aware_join_on_suggestion_uses_real_fk_relationship(), test_fk_join_suggestion_absent_without_matching_relationship(), test_foreign_key_column_is_flagged_fk() (+6 more)

### Community 82 - "Column Selection Dialog"
Cohesion: 0.14
Nodes (9): ColumnSelectionDialog, QDialog, Lets the user pick which columns to include before exporting (issue #142's…, _ClickableRow, A QWidget that behaves like a button for the schema sidebar's category rows…, QDialog, QueryAnalyzerDialog, Consolidated entry point: Cost & Profile + Compare Queries tabs. (+1 more)

### Community 83 - "Dangerous Query Guard"
Cohesion: 0.28
Nodes (14): _env_badge_label(), _header_row(), mock_data_generation_allowed(), QLabel, Query guard dialogs — shown before a write reaches the database when the…, Informational hard block, same visual shape as show_read_only_blocked — used…, Gate for the whole Mock Data Generation flow (issue #77) — called before the…, Informational only — this connection is read-only, nothing to confirm. (+6 more)

### Community 84 - "SQL Tab Multi-Result Display"
Cohesion: 0.15
Nodes (8): DataFrame, Show multiple SELECT results as a horizontal tab bar above the grid., Restore the splitter to its pre-collapse size once a real result grid is being…, Display a SQL error as a structured card — title, message, best- effort…, Import data from CSV, JSON, or Excel files, Update column options in all filter rows, Best-effort (line, column) — both 1-indexed — of the error within *query*, or…, _sql_error_location()

### Community 85 - "Self Updater"
Cohesion: 0.19
Nodes (7): QThread, Downloads, verifies, and installs a QForge update DMG in place over the running…, Downloads the release DMG, verifies it against the published SHA256SUMS.txt,…, UpdateInstaller, Ed25519 signature verification for release SHA256SUMS.txt (issue #113). SHA256…, True iff signature_b64 (base64-encoded ed25519 signature) validates against…, verify_signature()

### Community 86 - "Editable Grid Header & Theming"
Cohesion: 0.18
Nodes (7): QHeaderView, _NoEditDelegate, QStyledItemDelegate, Blocks edit-mode outright — assigned to the filler rows/columns that pad the…, Lazily create the overlay view the first time a freeze is requested., QHeaderView that hand-paints the active sort column instead of relying on the…, _SortHighlightHeader

### Community 87 - "Editable Table Formula Tests"
Cohesion: 0.18
Nodes (11): Tests for the cell-formula evaluator (issue #161): replaced a raw eval() with…, Mirrors the bulk-column-edit path: {value} substitution can splice database-…, test_evaluate_formula_string_blocks_injected_call_via_value_placeholder(), test_evaluate_formula_string_runs_arithmetic_through_safe_evaluator(), test_safe_eval_arithmetic_computes_basic_expressions(), test_safe_eval_arithmetic_rejects_attribute_access(), test_safe_eval_arithmetic_rejects_call_expressions(), Evaluate formula in cell (e.g., =NOW(), =UPPER(text)) (+3 more)

### Community 88 - "Licensing Client Tests"
Cohesion: 0.21
Nodes (8): _FakeResponse, _mock_urlopen(), test_activate_online_network_failure_returns_network_error(), test_activate_online_passes_through_device_limit_reached(), test_activate_online_success(), test_activate_online_timeout_returns_network_error(), test_deactivate_online_never_raises_on_network_failure(), test_deactivate_online_posts_to_deactivate_endpoint()

### Community 89 - "Database Management (Create/Drop)"
Cohesion: 0.16
Nodes (7): Blocking DB-list fetch used by refresh/create/drop-database flows. Returns True…, Issue #138: previously ran _load_databases() with no visual feedback at all — a…, Slide-in notification from the right when a background query finishes., QWidget, Non-blocking slide-in notification, for status that shouldn't interrupt the…, Slide a small notification in from the bottom-right corner of *parent* and back…, show_toast()

### Community 90 - "Editable Grid Composite Undo"
Cohesion: 0.18
Nodes (4): _CompositeCommand, Physically remove *row* (undo of a row insert) and shift every row-indexed…, Renumber every row index this widget tracks — the dirty-state sets, the cell…, Groups several commands (e.g. a paste or a multi-row duplicate) into one…

### Community 91 - "Editable Grid Filtering & Load"
Cohesion: 0.14
Nodes (7): DataFrame, Sort the currently displayed data by the clicked column (client-side)., Check if there are any uncommitted changes, Load data from DataFrame, Display dataframe in the table, Apply filter to a specific column, Apply all active column filters

### Community 92 - "Updater"
Cohesion: 0.15
Nodes (10): EntitlementConfigFetcher, QThread, Background fetch of the remote entitlement-config override — mirrors…, Runs a single HTTP GET on a worker thread; emits config_loaded only on success.…, QThread, Background update checker — hits GitHub releases API, emits a signal when a…, v1.2.3' or '1.2.3' → (1, 2, 3), Runs a single HTTP request on a worker thread; never blocks the UI. (+2 more)

### Community 93 - "ER Diagram Relationship Curves"
Cohesion: 0.22
Nodes (10): QGraphicsPathItem, QPainterPath, QPointF, _append_cardinality_marks(), _build_curved_path(), _curve_crosses_obstacles(), Cubic-bezier connector, bowed perpendicular to the straight line so it reads…, Appends a crow's-foot ("many") or a single tick ("one") to *path* at endpoint… (+2 more)

### Community 94 - "Schema Diff Tests"
Cohesion: 0.42
Nodes (12): build_schema_diff(), Connect to *source_config* then *target_config* (each its own dedicated…, _make_sqlite_config(), Tests for the schema-compare diff model (issue #68) — pure data, no UI., _run(), test_added_and_removed_tables(), test_added_removed_and_modified_columns(), test_foreign_key_added_and_removed() (+4 more)

### Community 95 - "Schema Migration SQL Generation"
Cohesion: 0.22
Nodes (12): Count only — kept for existing callers/tests. The names themselves are what the…, SchemaDiff, _diff_for_table(), generate_migration_sql(), DDL, as one string, that transforms *target* toward *source*. table_name: if…, A SchemaDiff containing only *table_name*'s change, whichever bucket it's in —…, _make_sqlite_config(), _run() (+4 more)

### Community 96 - "Db Service Select Db Tests"
Cohesion: 0.23
Nodes (8): _db_with_dead_connection(), _DeadConnection, _LiveConnection, _MonkeyPatch, Regression test: DbService.select_db() must self-heal a dead connection.…, test_select_db_propagates_non_connection_errors(), test_select_db_reconnects_when_connection_is_dead(), test_select_db_reuses_live_connection_without_reconnecting()

### Community 97 - "Sql Completer Multiword Insert Tests"
Cohesion: 0.26
Nodes (11): _insert(), Regression tests for issue #153 — accepting a multi-word keyword suggestion…, Runs the real SqlCompleter._insert against a plain QPlainTextEdit seeded with…, test_multiword_completion_does_not_eat_an_unrelated_preceding_word(), test_multiword_completion_is_case_insensitive_and_normalizes_casing(), test_multiword_completion_replaces_already_typed_leading_word(), test_multiword_completion_with_no_leading_word_typed_just_inserts_it(), test_multiword_snippet_body_is_unaffected_by_unrelated_trigger_text() (+3 more)

### Community 98 - "Connection Dialog Status Feedback"
Cohesion: 0.21
Nodes (6): QColor, Show a status pill next to the buttons that clears itself after a few seconds…, Tint the selected connection tree item with the given color., Whether the app's active theme is dark. The status/field tint colors below need…, Turn all connection form fields green (success) or red (failure) like TablePlus., (background, text, border) hex colors for a normalized environment key (see…

### Community 99 - "Connection Panel Tab Actions"
Cohesion: 0.15
Nodes (5): Execute inline-edit SQL statements against the live connection., Return unique {{param}} names found in *query*, in order of appearance., If *query* contains {{params}}, show an inline dialog and substitute. Returns…, Execute the SQL in `tab` on a background thread; Cancel actually stops it.…, Handler for the tab's Begin/Commit/Rollback buttons — runs *stmt* through the…

### Community 100 - "Table View Sort & Save"
Cohesion: 0.15
Nodes (6): Handle column header click to sort the table by the clicked column Args:…, Reset to the first page and load it, Apply all filter conditions and reset data loading, Clear all filters and reset, Reload current page (reset and reload first page), Save all changes to the database (Cmd+S)

### Community 101 - "Database Transaction Control"
Cohesion: 0.21
Nodes (7): Raised on transaction-state misuse (double BEGIN, COMMIT/ROLLBACK with nothing…, Start a manual transaction, turning off this connection's per-statement…, Commit the open manual transaction and restore autocommit., Roll back the open manual transaction and restore autocommit., Undo the autocommit=False set by begin_transaction() for drivers that need it…, Execute a detected transaction-control statement and return a status DataFrame,…, TransactionError

### Community 102 - "Database Connection Setup"
Cohesion: 0.18
Nodes (6): Connect to database based on type, Connect to MySQL database, Connect to PostgreSQL database, Setup SSH tunnel and return local host/port, Best-effort: kill the running query on the server side. MySQL — opens a second…, Check if connected by opening a *separate* short-lived connection. Never…

### Community 103 - "Table Organization"
Cohesion: 0.33
Nodes (11): _entry(), get_favorites(), get_pinned(), _key(), Persist per-table "pinned" and "favorite" flags for the schema tree's…, Flip *table_name*'s pinned flag and persist it. Returns the new state., Flip *table_name*'s favorite flag and persist it. Returns the new state., _read_all() (+3 more)

### Community 104 - "Saved Queries Tests"
Cohesion: 0.32
Nodes (11): _store(), test_add_defaults_untitled_name_when_blank(), test_add_persists_and_reloads(), test_delete_removes_entry(), test_get_favorites_and_get_saved_partition_the_list(), test_load_missing_file_starts_empty(), test_search_matches_name_or_query_case_insensitively(), test_toggle_favorite_flips_and_persists() (+3 more)

### Community 105 - "Table View Reconnect Reload Tests"
Cohesion: 0.23
Nodes (7): _FlakyDbService, Regression tests for issue #176 — a TableViewWidget created before the…, Fails every execute_query() call until connected=True — simulates a…, test_connection_panel_reloads_only_the_errored_tabs(), test_reload_if_errored_is_a_noop_for_an_already_loaded_tab(), test_reload_if_errored_retries_and_recovers_once_connected(), test_tab_created_before_connect_starts_in_error_state()

### Community 106 - "Export Worker (DOT/SQL)"
Cohesion: 0.32
Nodes (6): _ExportWorker, _open_export_file(), QObject, Sanitize a table name before using it as a zip member filename (issue #163).…, Runs export_database()/_export_table()'s write loop on a QThread (issue #158),…, _safe_zip_entry_name()

### Community 107 - "SQL Go-to-Definition"
Cohesion: 0.20
Nodes (6): Word under `position` plus, for a dot-notation reference (alias.col /…, The table/view whose structure `word` should navigate to, or None if it doesn't…, Ctrl/Cmd-click handler (CodeEditor.identifier_clicked): jump to the clicked…, The validation issue (see _update_schema_validation) covering `position`, if…, Standard edit menu plus a "Go to Definition" entry when the right-clicked…, Route show-structure request to ConnectionPanel parent.

### Community 108 - "Connection Panel Status Bar 178 Tests"
Cohesion: 0.24
Nodes (7): parametrize, _FakeHealthSignal, _PanelStub, Tests for issue #178's ConnectionPanel-level wiring: health_changed now goes…, Stands in for the real Signal(str) — .emit() calls the same slot…, test_dialect_display_name_maps_known_types_and_falls_back_for_unknown(), test_emit_health_updates_last_health_and_fans_out_to_open_sql_tabs()

### Community 109 - "Connection Panel Focus Restore Tests"
Cohesion: 0.29
Nodes (8): _panel_with_tabs(), _PanelStub, Regression test for issue #149 — session restore left focus on the last…, Just enough of ConnectionPanel for focus_first_tab() to operate on — avoids…, test_focus_first_tab_focuses_tab_zeros_editor_not_the_last_ones(), test_focus_first_tab_is_a_noop_on_an_empty_panel(), test_focus_first_tab_makes_tab_zero_current_after_restore_loop(), Make tab 0 the active tab and focus its editor. Call once after all of a…

### Community 110 - "DataFrame Export"
Cohesion: 0.22
Nodes (9): Issue #115: export_dataframe()'s CSV/XLSX paths go through…, test_guarded_for_spreadsheet_neutralizes_string_cells_only(), _cell_str(), _csv_formula_guard(), _guarded_for_spreadsheet(), DataFrame, Shared CSV/JSON/Excel/SQL export helper for pandas DataFrames., Copy of *df* with every string cell passed through _csv_formula_guard (issue… (+1 more)

### Community 111 - "Table View Widget Suspend Guard Tests"
Cohesion: 0.24
Nodes (7): test_counter_get_unknown_name_returns_empty_dict(), _FakeDbService, Regression test for issue #174: a page load that overlaps a detected system…, setup_function(), test_page_load_overlapping_suspend_window_is_filtered_not_recorded(), test_page_load_without_suspend_window_is_recorded_normally(), counter_get()

### Community 112 - "Connection Dialog Save & Connect"
Cohesion: 0.22
Nodes (4): Return the currently selected connection QTreeWidgetItem, or None., Record a just-submitted form's password(s) as this session's known value for…, Return the typed group name — free text; any name not matching an existing…, require_name=False for actions that don't persist the connection (test, one-off…

### Community 114 - "SSH Key Auth Compatibility Shim"
Cohesion: 0.33
Nodes (8): _dsskey_stub_class(), _ensure_dsskey_stub(), A stand-in for paramiko.DSSKey that raises SSHException instead of being bare…, Idempotently patch paramiko.DSSKey with the stub above if this paramiko version…, End-to-end reproduction: a real, valid private key that is NOT RSA (so…, test_dsskey_stub_raises_sshexception_not_attributeerror(), test_ensure_dsskey_stub_is_idempotent_and_never_leaves_it_none(), test_key_fallback_reaches_ecdsa_after_dsskey_slot_without_crashing()

### Community 115 - "Connection Dialog First Run Hint Tests"
Cohesion: 0.29
Nodes (9): isolated_dialog_factory(), fixture, Regression tests for the annotated first-run empty-state hint (issue #164):…, Builds ConnectionDialog instances isolated to tmp_path — patches…, Regression guard: load_connections() has an early return when CONNECTION_FILE…, test_dismiss_hides_hint_immediately(), test_dismissal_persists_across_a_fresh_dialog_instance(), test_hint_hidden_when_a_connection_exists() (+1 more)

### Community 116 - "Environment Tests"
Cohesion: 0.29
Nodes (7): test_normalize_accepts_known_values(), test_normalize_defaults_empty_string_to_unclassified(), test_normalize_defaults_none_to_unclassified(), test_normalize_defaults_unknown_string_to_unclassified(), test_normalize_is_case_and_whitespace_insensitive(), normalize(), Coerce any stored/legacy value to a known tier; unrecognized, missing, or…

### Community 117 - "Sql Tab Go To Definition Tests"
Cohesion: 0.33
Nodes (9): Tests for Ctrl/Cmd-click go-to-definition (issue #206) — resolving a table,…, CodeEditor.identifier_clicked (emitted by CodeEditor's own mousePressEvent on a…, _tab_with_schema(), test_alias_resolves_to_its_table(), test_bare_table_name_resolves_to_itself(), test_ctrl_click_signal_reaches_go_to_definition(), test_dot_notation_column_resolves_to_owning_table(), test_go_to_definition_routes_to_show_structure() (+1 more)

### Community 118 - "Sql Tab Quick Fixes Tests"
Cohesion: 0.33
Nodes (9): Tests for "Did you mean …?" quick-fixes on schema-validation squiggles (issue…, The identifier the cursor is currently sitting inside is left unflagged (still…, test_apply_quick_fix_replaces_span_and_revalidates(), test_no_issue_at_cursor_position_itself_still_typing(), test_quick_fix_at_finds_issue_covering_position(), test_quick_fix_at_returns_none_outside_any_issue(), test_unknown_column_flagged_with_owning_tables_columns(), test_unknown_table_flagged_with_table_candidates() (+1 more)

### Community 119 - "Table View Widget Primary Keys Tests"
Cohesion: 0.27
Nodes (5): FakeDbService, TableViewWidget.load_table_data() must pass the table's real primary key(s) to…, test_declared_primary_key_is_applied_to_data_table(), test_no_primary_key_leaves_data_table_with_empty_list(), test_primary_keys_fetched_once_and_reused_across_page_loads()

### Community 120 - "Schema Diff Engine"
Cohesion: 0.33
Nodes (8): _diff_columns(), _diff_foreign_keys(), _diff_indexes(), _diff_table(), _fetch_side(), ForeignKeyDiff, Build a read-only structural diff between two database schemas (issue #68).…, Returns {table_name: (columns_by_name, indexes, foreign_keys)}.

### Community 121 - "PostgreSQL Durability Tests"
Cohesion: 0.22
Nodes (9): The Postgres-specific case the SQLite suite can't exercise: with a real second…, A fully independent DbService to the same database — proves durability from a…, _second_connection(), test_begin_commit_persists_change(), test_begin_rollback_discards_change(), test_default_autocommit_behavior_unchanged(), test_raw_typed_begin_commit_via_execute_query(), test_raw_typed_rollback_via_execute_query() (+1 more)

### Community 122 - "Generated Columns Tests"
Cohesion: 0.22
Nodes (3): db(), fixture, Tests for DbService.get_generated_columns()/content_select_list() (issue #160):…

### Community 123 - "Sql Tab Status Banner Tests"
Cohesion: 0.36
Nodes (8): Regression tests for issue #147 (the SQL error banner clipped long messages…, The old plain-text banner sized itself by counting literal '\\n's, so a long…, _shown_tab(), test_error_with_hint_populates_message_location_and_details_sections(), test_long_wrapping_single_line_error_is_shown_in_full_not_truncated(), test_short_error_does_not_grow_the_plain_status_label(), test_show_error_hides_the_normal_status_label_and_empty_state(), test_very_long_error_stays_reachable_via_the_card_scroll_area()

### Community 124 - "Table View Widget Pagination Tests"
Cohesion: 0.33
Nodes (5): FakeDbService, Regression test for issue #111: table data present but not shown. Two bugs in…, test_filtered_load_sets_total_rows_without_crashing(), test_unfiltered_load_keeps_real_rows_despite_stale_zero_count(), _widget()

### Community 125 - "Connection Dialog Selection Handling"
Cohesion: 0.22
Nodes (3): Reset form fields to default stylesheet., Load connection form when a connection item (not a group) is clicked., Walk the tree and select the item whose UserRole data equals idx.

### Community 126 - "Editable Grid Row Export"
Cohesion: 0.22
Nodes (5): Export visible table data to CSV / JSON / Excel / SQL — despite the name, this…, Export only the currently-selected rows (issue #123). Reads from the same typed…, Export data in multiple formats: CSV, JSON, Excel, SQL, export_dataframe(), Prompt for a save file and export `df` as CSV/JSON/Excel/SQL. Shows an…

### Community 127 - "Licensing Client"
Cohesion: 0.32
Nodes (7): activate_online(), deactivate_online(), _post(), Network calls to the qforge-licensing activation service — the only place…, POST JSON to the licensing service, return the parsed JSON response, or None on…, {"ok": True, "receipt": ..., "device_limit": ..., "seats_used": ...} on…, Best-effort — mirrors POST /deactivate's own "best-effort cleanup, not a…

### Community 128 - "PostgreSQL Test Fixtures"
Cohesion: 0.25
Nodes (8): _config(), db(), pg_database(), fixture, Defense-in-depth beyond the client-side guard: connect() itself should ask the…, A fresh, uniquely-named database — created before the test, dropped after.…, test_get_tables_empty_database_returns_empty_list(), test_read_only_session_is_set_on_the_connection()

### Community 129 - "Stream Table Rows Tests"
Cohesion: 0.25
Nodes (4): db(), fixture, Tests for DbService.stream_table_rows() (issue #158): reads a table via…, test_stream_table_rows_requires_connection()

### Community 130 - "SQL Completer Item Rendering"
Cohesion: 0.29
Nodes (4): QStyledItemDelegate, sql_completer.py — Professional context-aware SQL autocomplete…, Paints each row: bold-highlighted prefix on the left, type badge on the right., SuggestionDelegate

### Community 131 - "Table View Reload & Pagination"
Cohesion: 0.29
Nodes (3): Every reload path (refresh, page change, sort, filter) overwrites the grid with…, Load the current page for current filter and sort, refreshing row count, Retry the current page if the last load attempt failed (issue #176) — e.g. this…

### Community 132 - "Deploy"
Cohesion: 0.52
Nodes (6): die(), ok(), deploy.sh script, step(), substep(), warn()

### Community 133 - "Sql Highlighter"
Cohesion: 0.29
Nodes (4): QSyntaxHighlighter, Simple SQL syntax highlighter, Apply syntax highlighting to a block of text, SqlHighlighter

### Community 134 - "Editable Grid Copy & Context Menu"
Cohesion: 0.29
Nodes (3): Show comprehensive context menu like TablePlus, Return (headers, [[row values], ...]) for currently selected rows, in the…, Copy selected rows to clipboard in the requested format.

### Community 135 - "ER Diagram Layout"
Cohesion: 0.57
Nodes (6): clear(), _key(), load(), Persist per-connection ER diagram layouts (table positions + collapsed state)…, _read_all(), save()

### Community 137 - "Sql Tab Result Primary Keys Tests"
Cohesion: 0.53
Nodes (5): SqlTab's editable query-result grid must also key UPDATE/DELETE off the real…, _tab_with_schema(), test_no_table_name_leaves_primary_keys_empty(), test_result_grid_gets_real_primary_key_on_load(), test_result_grid_update_sql_keys_off_primary_key_not_column_zero()

### Community 139 - "Sql Tab Escape Dismisses Popup Tests"
Cohesion: 0.40
Nodes (4): Regression test for issue #152 — pressing Esc while the autocomplete popup was…, The pre-existing esc_shortcut -> hide_filter() behavior (for the table-view…, test_escape_hides_popup_when_visible(), test_escape_still_hides_filter_panel_when_popup_not_visible()

### Community 143 - "Memory"
Cohesion: 0.50
Nodes (4): Project Memory Index, Secure Credential Storage Memory, UI/UX Improvements Memory, Keyring-Only Credential Storage

### Community 144 - "Conftest"
Cohesion: 0.50
Nodes (3): _never_block_on_messagebox(), fixture, Shared pytest fixtures. Every QMessageBox.* static call…

### Community 145 - "PostgreSQL Reconnect Tests"
Cohesion: 0.50
Nodes (4): _admin_cursor(), Simulates a real dropped connection (not just closing it locally) by having the…, test_reconnect_mid_transaction_raises_transaction_error(), test_reconnects_after_server_terminates_the_connection()

### Community 150 - "Docker Compose"
Cohesion: 0.67
Nodes (3): Docker Compose Test Fixtures, Tests Workflow (CI), Tests README

## Ambiguous Edges - Review These
- `Keyring-Only Credential Storage` → `Secure Credential Storage Memory`  [AMBIGUOUS]
  SECURITY.md · relation: conceptually_related_to

## Knowledge Gaps
- **33 isolated node(s):** `1. Pricing`, `2. Investment to build the launch pieces`, `3. Apple Developer ID + notarization timeline`, `Read-only mode`, `Control a transaction` (+28 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **39 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What is the exact relationship between `Keyring-Only Credential Storage` and `Secure Credential Storage Memory`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **Why does `DbService` connect `Database Service Core` to `PostgreSQL Test Fixtures`, `Stream Table Rows Tests`, `Db Service Sqlite Tests`, `Schema Compare Dialog`, `Schema Snapshot Tests`, `Main Window Core`, `Connection Panel Core`, `Benchmark Harness`, `PostgreSQL Integration Tests`, `Query Cost Tests`, `Data Compare Dialog`, `Data Diff Engine`, `Theme Manager`, `Query Verifier`, `Database Query Execution`, `Upgrade to Pro Dialog`, `Onboarding`, `Query Analyzer Compare Tab`, `Export Worker Tests`, `Query Analyzer Profile Sections`, `ER Diagram Model`, `Query Analyzer Cost Estimate Tab`, `Connection Dialog Core Actions`, `Schema Diff Data Model`, `Database & Table Export`, `Schema Migration`, `Column Selection Dialog`, `Schema Diff Tests`, `Schema Migration SQL Generation`, `Db Service Select Db Tests`, `Connection Dialog Status Feedback`, `Connection Panel Tab Actions`, `Database Transaction Control`, `Database Connection Setup`, `Export Worker (DOT/SQL)`, `SSH Key Auth Compatibility Shim`, `Schema Diff Engine`, `PostgreSQL Durability Tests`, `Generated Columns Tests`?**
  _High betweenness centrality (0.280) - this node is a cross-community bridge._
- **Why does `SqlTab` connect `SQL Tab Toolbar & Dialogs` to `SQL Code Editor`, `Sql Tab Result Primary Keys Tests`, `Main Window Core`, `Sql Tab Escape Dismisses Popup Tests`, `Connection Panel Core`, `Query Cost Status Badge`, `Editable Grid PK Tests`, `SQL Result Diff Highlighting`, `SQL Tab Theming`, `Table View Streaming Load`, `Theme Manager`, `SQL Tab Save Shortcut`, `SQL Tab Query Extraction`, `Upgrade to Pro Dialog`, `SQL Completer Core Engine`, `Code Editor Find & Replace`, `Onboarding`, `Query Library Dialog`, `Connection Panel Tab Management`, `Run All Statements Tests`, `Connection Panel Query Navigation Tests`, `Sql Tab Result Viewer 178 Tests`, `SQL Snippet Manager`, `Code Editor Input Handling`, `SQL Tab Result Grid Paging`, `Column Selection Dialog`, `SQL Tab Multi-Result Display`, `Export Worker (DOT/SQL)`, `SQL Go-to-Definition`, `Connection Panel Status Bar 178 Tests`, `Connection Panel Focus Restore Tests`, `Sql Tab Go To Definition Tests`, `Sql Tab Quick Fixes Tests`, `Sql Tab Status Banner Tests`, `Editable Grid Row Export`?**
  _High betweenness centrality (0.203) - this node is a cross-community bridge._
- **Why does `ConnectionPanel` connect `Connection Panel Core` to `Schema Compare Dialog`, `Export Scope Dialog Tests`, `SQL Tab Toolbar & Dialogs`, `Database Service Core`, `Main Window Core`, `Table View Streaming Load`, `Connection Dialog UI Layout`, `Data Compare Dialog`, `Theme Manager`, `ER Diagram Dialog Core`, `Upgrade to Pro Dialog`, `Mock Data Dialog`, `Query History`, `Onboarding`, `Query Library Dialog`, `Table Context Menu Actions`, `Saved Queries`, `Table Structure Editor`, `Quick Search`, `Df Export Tests`, `Database Switcher`, `Connection Panel Tab Management`, `Connection Panel Query Lifecycle`, `Row Stream Writers Tests`, `Run All Statements Tests`, `Connection Panel Query Navigation Tests`, `Connection Dialog Core Actions`, `Connection Panel Schema Loading`, `Ddl Toggles Tests`, `SQL Snippet Manager`, `Query History Dialog`, `Database & Table Export`, `Schema Migration`, `Column Selection Dialog`, `Database Management (Create/Drop)`, `Connection Panel Tab Actions`, `Table View Reconnect Reload Tests`, `Connection Panel Status Bar 178 Tests`, `Connection Panel Focus Restore Tests`, `Saved Queries & History Filtering`, `Editable Grid Row Export`?**
  _High betweenness centrality (0.141) - this node is a cross-community bridge._
- **Are the 34 inferred relationships involving `ConnectionPanel` (e.g. with `MainWindow` and `_PanelStub`) actually correct?**
  _`ConnectionPanel` has 34 INFERRED edges - model-reasoned connections that need verification._
- **Are the 35 inferred relationships involving `DbService` (e.g. with `MainWindow` and `DataDiff`) actually correct?**
  _`DbService` has 35 INFERRED edges - model-reasoned connections that need verification._
- **Are the 18 inferred relationships involving `SqlTab` (e.g. with `MainWindow` and `_PanelStub`) actually correct?**
  _`SqlTab` has 18 INFERRED edges - model-reasoned connections that need verification._