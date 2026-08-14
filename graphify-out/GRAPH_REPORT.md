# Graph Report - qforge  (2026-08-13)

## Corpus Check
- 72 files · ~87,841 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1557 nodes · 2845 edges · 100 communities (78 shown, 22 thin omitted)
- Extraction: 94% EXTRACTED · 6% INFERRED · 0% AMBIGUOUS · INFERRED: 175 edges (avg confidence: 0.64)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `ccd2352e`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- QueryVerifierDialog
- DbSwitcherDialog
- ConnectionDialog
- SqlCompleter
- MainWindow
- test_query_classifier.py
- DataFilterDialog
- SnippetManager
- query_analyzer.py
- CodeEditor
- StructureEditorDialog
- SqlTab
- DbService
- QForge
- EditableTableWidget
- QuickSearchDialog
- ._find_live_update
- Exception
- QueryHistoryDialog
- QueryHistory
- .open_table_view
- ConnectionPanel
- export_dataframe
- TableViewWidget
- .reset_and_load_first_page
- app_data_dir
- ._set_status
- _RowInsertCommand
- .eventFilter
- load
- FilterHeaderWidget
- test_schema_snapshot.py
- test_db_service_sqlite.py
- ui-ux-improvements.md
- test_environment.py
- .bulk_edit_dialog
- ._refresh_result_view
- .load_connection_by_id
- _ensure_dsskey_stub
- ExportScopeDialog
- .load_schema
- ._display_data
- .apply_all_filters
- .update_theme
- AdvancedFilterDialog
- QPlainTextEdit
- ColumnFilterDialog
- deploy.sh
- .get_query
- .commit_changes
- QForge — AI Load Context
- schema_diff.py
- QForge — AI Flush Context
- Security Policy
- erd_dialog.py
- .set_frozen_columns
- _CellEditCommand
- _ClickableRow
- connection_panel.py
- .execute_query
- .init_ui
- build.sh
- main.py
- _ErdView
- ErdDialog
- .__init__
- TransactionError
- qforge
- ._repaint_row
- QForge UI Design System
- ._on_query_done
- _TableNodeItem
- ._warn_and_discard_changes
- ._update_loading_overlay_geometry
- _RelationshipLineItem
- .open_table_view
- SchemaCompareDialog
- _CollapseToggle
- _QueryWorker
- .show_alter_table_editor
- _CompositeCommand
- QComboBox
- .filter_tables
- ._apply_pill_style
- erd_layout.py
- .restore_session_tabs
- UpdateInstaller
- ._show_schema_tab_context_menu
- show_toast
- QForge — Launch Plan (macOS, individual/consumer sales)
- ._prompt_params
- ._apply_diff_highlights
- .label
- .disconnect
- .hide_filter
- .show_structure_tab
- .show_quick_search

## God Nodes (most connected - your core abstractions)
1. `ConnectionPanel` - 109 edges
2. `DbService` - 95 edges
3. `SqlTab` - 89 edges
4. `EditableTableWidget` - 68 edges
5. `ConnectionDialog` - 58 edges
6. `TableViewWidget` - 52 edges
7. `MainWindow` - 50 edges
8. `ErdDialog` - 33 edges
9. `QueryVerifierDialog` - 27 edges
10. `CodeEditor` - 26 edges

## Surprising Connections (you probably didn't know these)
- `MainWindow` --uses--> `DbService`  [INFERRED]
  main.py → services/db_service.py
- `MainWindow` --uses--> `QueryHistory`  [INFERRED]
  main.py → services/query_history.py
- `MainWindow` --uses--> `ConnectionDialog`  [INFERRED]
  main.py → ui/connection_dialog.py
- `MainWindow` --uses--> `ConnectionPanel`  [INFERRED]
  main.py → ui/connection_panel.py
- `MainWindow` --uses--> `SqlTab`  [INFERRED]
  main.py → ui/sql_tab.py

## Import Cycles
- None detected.

## Communities (100 total, 22 thin omitted)

### Community 0 - "QueryVerifierDialog"
Cohesion: 0.06
Nodes (40): QHBoxLayout, QSyntaxHighlighter, AggRow, ColDiffRow, ExplainRow, QueryVerifier, QueryVerifier ───────────── Runs two SQL queries (original vs optimised) and…, Compare two SQL queries using an existing DbService connection. (+32 more)

### Community 1 - "DbSwitcherDialog"
Cohesion: 0.20
Nodes (6): Open the Cmd+K database switcher dialog., DbSwitcherDialog, QDialog, QListWidgetItem, DbSwitcherDialog ──────────────── Cmd+K spotlight-style popup for switching…, Cmd+K database switcher — shows all databases, type to filter.

### Community 2 - "ConnectionDialog"
Cohesion: 0.05
Nodes (25): QLineEdit, ConnectionDialog, QColor, QDialog, Tint the selected connection tree item with the given color., Whether the app's active theme is dark. The status/field tint colors below need…, Turn all connection form fields green (success) or red (failure) like TablePlus., Reset form fields to default stylesheet. (+17 more)

### Community 3 - "SqlCompleter"
Cohesion: 0.06
Nodes (25): QPoint, QStyledItemDelegate, QFrame, QListWidgetItem, sql_completer.py — Professional context-aware SQL autocomplete…, Paints each row: bold-highlighted prefix on the left, type badge on the right., Frameless floating window shown below the cursor. Never steals keyboard focus…, Drives autocomplete for a QTextEdit. Design goals ───────────── • GENERAL… (+17 more)

### Community 4 - "MainWindow"
Cohesion: 0.06
Nodes (19): _is_remote_connection(), MainWindow, Self-update needs an actual .dmg asset and a running .app bundle to replace…, Close the connection panel at *index*. No confirmation unless one of its tabs…, True for connections where establishing db_service.connect() is slow enough…, Close all connection tabs except the one at *keep_index*., Cmd+W: close current content tab; if that was the last tab for this connection,…, Right-click menu on a connection tab. (+11 more)

### Community 5 - "test_query_classifier.py"
Cohesion: 0.14
Nodes (28): Classification, classify(), is_dangerous(), Classifies SQL statements for the read-only guard and dangerous-query guard…, True if this statement should trigger the dangerous-query confirmation…, Single source of truth for splitting a script into statements — replaces the…, split_statements(), test_classify_falls_back_for_types_get_type_reports_unknown() (+20 more)

### Community 6 - "DataFilterDialog"
Cohesion: 0.12
Nodes (8): DataFilterDialog, QDialog, Disable value input for NULL checks, Add a filter to the list, Remove selected filter, Generate SQL WHERE clause from filters, Dialog for building data filters, Load existing filters

### Community 7 - "SnippetManager"
Cohesion: 0.08
Nodes (13): QDialog, QLabel, QListWidgetItem, snippet_editor_dialog.py — Full CRUD UI for SQL snippets. Layout ──────…, Full CRUD dialog for managing SQL snippets., SnippetEditorDialog, Return a copy of all snippets., Create or update a snippet. (+5 more)

### Community 8 - "query_analyzer.py"
Cohesion: 0.10
Nodes (37): analyze_explain_rows(), analyze_sql_text(), generate_optimized_sql(), html_escape(), _int(), Issue, list_sql_files(), load_connections() (+29 more)

### Community 9 - "CodeEditor"
Cohesion: 0.10
Nodes (11): QPainter, QRect, QSize, QStyleOptionViewItem, CodeEditor, _Gutter, QWidget, code_editor.py — QPlainTextEdit with line numbers and current-line highlight.… (+3 more)

### Community 10 - "StructureEditorDialog"
Cohesion: 0.10
Nodes (12): QDialog, Enable/disable length input based on type, Enable/disable auto increment when primary key is toggled, Add column to the table, Dialog for creating/editing table structure, Remove selected column, Generate CREATE TABLE or ALTER TABLE SQL, Generate CREATE TABLE SQL (+4 more)

### Community 11 - "SqlTab"
Cohesion: 0.07
Nodes (13): QWidget, Enable commit/revert buttons when changes are made, Minify/compress SQL query., Update status when filters change, Route show-structure request to ConnectionPanel parent., Export data in multiple formats: CSV, JSON, Excel, SQL, Set the editor content., Insert text at the cursor (replacing any selection), placing the cursor at the… (+5 more)

### Community 12 - "DbService"
Cohesion: 0.08
Nodes (11): DbService, Return index definitions for *table_name*. Each dict has: name, columns,…, Return FK definitions for *table_name*. Each dict has keys: column, ref_table,…, Return the primary-key column name(s) for *table_name*, in key order. Empty…, Return a short version string like 'MySQL 8.0.41' or 'PostgreSQL 15.3'., Get list of tables based on database type, Get list of views based on database type, Get list of functions/procedures based on database type (+3 more)

### Community 13 - "QForge"
Cohesion: 0.08
Nodes (23): Build a macOS app locally, Classify a connection's environment, Compare schemas, Control a transaction, Core workflows, Dependencies, Edit and move data, Explore a database (+15 more)

### Community 14 - "EditableTableWidget"
Cohesion: 0.07
Nodes (16): EditableTableWidget, QTableWidget, Show comprehensive context menu like TablePlus, Store FK metadata: list of {column, ref_table, ref_column} dicts., Return (headers, [[row values], ...]) for currently selected rows, in the…, Copy selected rows to clipboard in the requested format., Copy selected cells to clipboard, in visual column order (issue #122 — plain…, Copy current cell value (+8 more)

### Community 15 - "QuickSearchDialog"
Cohesion: 0.14
Nodes (9): QKeyEvent, QDialog, QuickSearchDialog, Handle arrow key navigation from search input, Command palette: search tables, columns, views, functions/procedures, query…, Filter items based on search text, Check if search characters appear in order in text, Get icon for item type - removed, no icons (+1 more)

### Community 16 - "._find_live_update"
Cohesion: 0.13
Nodes (8): QTextCursor, Cmd+F: open Quick Filter when the result grid has focus (issue #2), otherwise…, Ctrl+Alt+F (Cmd+Option+F on macOS): show find+replace bar., The visible 'Replace' toggle button in the find bar (issue #112) — lets Replace…, Single place that shows/hides the replace row and keeps the toggle button's…, Build a regex pattern from the current search text + toggles. Smart-case: if…, Remove all orange match highlights from the editor., Re-run search on every keystroke or toggle change.

### Community 17 - "Exception"
Cohesion: 0.13
Nodes (11): Exception, Connect to database based on type, Connect to MySQL database, Connect to PostgreSQL database, Connect to SQLite database, Setup SSH tunnel and return local host/port, Best-effort: kill the running query on the server side. MySQL — opens a second…, Re-establish the connection using *config*, or the previously stored config if… (+3 more)

### Community 18 - "QueryHistoryDialog"
Cohesion: 0.10
Nodes (19): QTextEdit, _env_badge_label(), _header_row(), QLabel, Query guard dialogs — shown before a write reaches the database when the…, Informational only — this connection is read-only, nothing to confirm., Shows the exact statement(s) and why they were flagged. Returns True only if…, show_dangerous_confirmation() (+11 more)

### Community 19 - "QueryHistory"
Cohesion: 0.19
Nodes (6): QueryHistory, Simple query history manager, Load query history from file, Save query history to file, Add a query to history, Search queries by keyword

### Community 20 - ".open_table_view"
Cohesion: 0.12
Nodes (6): Open a table view; re-focus if already open., Open (or focus) *table_name*'s tab and switch it to the Structure sub-tab…, Read a CSV file and INSERT all rows into *table_name*., Fuzzy-match the active category's item names (tables, views, or functions —…, Reopen tabs from saved session data., Classify *sql* (one statement or a whole script) and show whatever dialog is…

### Community 21 - "ConnectionPanel"
Cohesion: 0.09
Nodes (10): ConnectionPanel, QListWidgetItem, One database connection panel (sidebar + content tabs)., True if any tab in this connection has an open manual transaction — used to…, Build the full (item_type, display_text, payload) list for the command palette:…, Return the current tab if it's a SQL editor, else open a new one., Return serialisable list of open tabs., Called every 30s — runs the actual ping on a daemon thread so the UI never… (+2 more)

### Community 22 - "export_dataframe"
Cohesion: 0.20
Nodes (11): test_sql_value_literal_leaves_numbers_unquoted(), test_sql_value_literal_null_for_none_and_nan(), test_sql_value_literal_quotes_and_escapes_strings(), test_to_sql_inserts_builds_one_statement_per_row(), Export visible table data to CSV / JSON / Excel / SQL — despite the name, this…, Export only the currently-selected rows (issue #123). Reads from the same typed…, export_dataframe(), Shared CSV/JSON/Excel/SQL export helper for pandas DataFrames. (+3 more)

### Community 23 - "TableViewWidget"
Cohesion: 0.10
Nodes (10): QWidget, Toggle filter visibility with Cmd+F, Open a blank SQL query tab., Execute the SQL in `tab` using this connection's db_service., Show structure editor dialog., Handle quick search selection., Show query history dialog., Return serialisable list of open tabs. (+2 more)

### Community 24 - ".reset_and_load_first_page"
Cohesion: 0.17
Nodes (6): Handle column header click to sort the table by the clicked column Args:…, Reset to the first page and load it, Apply all filter conditions and reset data loading, Clear all filters and reset, Reload current page (reset and reload first page), Save all changes to the database (Cmd+S)

### Community 25 - "app_data_dir"
Cohesion: 0.18
Nodes (6): Persist per-table column widths across sessions., app_data_dir(), Path, Single source of truth for QForge's per-user application data directory (issue…, OS-appropriate directory for QForge's persisted app data (connections,…, Persist pinned/favourite SQL tabs across sessions. Format: { connection_name: […

### Community 26 - "._set_status"
Cohesion: 0.17
Nodes (7): Show multiple SELECT results as a horizontal tab bar above the grid., Helper: show text in the status_label (QPlainTextEdit)., Shrink the splitter's bottom pane to just fit the status line, giving the…, Display a SQL error inline with actionable hints — always selectable., Show a neutral 'query cancelled' status., Return a short actionable hint for a SQL error message, or empty string., _sql_error_hint()

### Community 27 - "_RowInsertCommand"
Cohesion: 0.11
Nodes (9): Duplicate current row, Duplicate all selected rows (Cmd+D), Record cmd as the next undoable step, or fold it into the batch currently being…, A new row was inserted at row with the given per-column text values., Physically insert a new row at *row* with the given per-column text, marking it…, Physically remove *row* (undo of a row insert) and shift every row-indexed…, Renumber every row index this widget tracks — the dirty-state sets, the cell…, _RowInsertCommand (+1 more)

### Community 28 - ".eventFilter"
Cohesion: 0.17
Nodes (6): Route key events: popup navigation first, then auto-trigger., Insert a matching closing quote and place the cursor between them. If the…, Return True if the editor cursor is currently inside a quoted string literal., Toggle -- comment on each selected line (or current line)., Cmd+D: expand selection to current word, then find and select next match., Delete the entire line the cursor is on (Cmd+Backspace / ⌘⌫).

### Community 29 - "load"
Cohesion: 0.17
Nodes (23): _connected_db(), Issue #72: a successful schema-changing statement must drop the on-disk schema…, test_alter_table_invalidates_cache(), test_connection_without_id_never_touches_cache_file(), test_create_table_invalidates_cache(), test_drop_table_invalidates_cache(), test_row_level_writes_do_not_invalidate_cache(), test_corrupt_cache_file_is_swallowed_not_raised() (+15 more)

### Community 30 - "FilterHeaderWidget"
Cohesion: 0.18
Nodes (6): FilterHeaderWidget, QWidget, Emit signal when filter text changes, Clear the filter input, Custom header widget with inline filter, Add filter input boxes to column headers

### Community 31 - "test_schema_snapshot.py"
Cohesion: 0.13
Nodes (16): fetch_schema_snapshot(), Connect using `config`, gather schema metadata, then disconnect. Returns a dict…, _FakeMysqlConnection, _FakeMysqlDbService, _make_sqlite_config(), Regression tests for the shared-connection corruption bug: a single DB-API…, Simulates pymysql's real failure mode: get_tables()/get_all_columns() raise "No…, Regression test: a MySQL connection profile with no database configured used to… (+8 more)

### Community 32 - "test_db_service_sqlite.py"
Cohesion: 0.09
Nodes (14): fixture, db(), db_path(), Regression test for the bug this slice fixes: a write statement inside a manual…, A user can type BEGIN/COMMIT directly in the editor instead of clicking the…, A fully independent DbService to the same file — proves durability from a…, No begin_transaction() call — every statement still persists immediately,…, _second_connection() (+6 more)

### Community 33 - "ui-ux-improvements.md"
Cohesion: 0.17
Nodes (9): Changes Made:, Security Benefits:, Testing:, Usage:, 1. Improved Table Search/Filtering (Fuzzy Matching), 2. Query Execution Cancellation, 3. Enhanced Error Messages with Actionable Information, Technical Details (+1 more)

### Community 34 - "test_environment.py"
Cohesion: 0.29
Nodes (7): test_normalize_accepts_known_values(), test_normalize_defaults_empty_string_to_unclassified(), test_normalize_defaults_none_to_unclassified(), test_normalize_defaults_unknown_string_to_unclassified(), test_normalize_is_case_and_whitespace_insensitive(), normalize(), Coerce any stored/legacy value to a known tier; unrecognized or missing values…

### Community 35 - ".bulk_edit_dialog"
Cohesion: 0.33
Nodes (3): Paste from clipboard, walking *visually* adjacent columns from the anchor cell…, Show dialog to edit multiple rows at once, Start grouping subsequent _push_history calls into one undo step (e.g. a paste…

### Community 36 - "._refresh_result_view"
Cohesion: 0.13
Nodes (7): DataFrame, Display the current page of _result_view_df in result_table. Only 500 rows are…, Sort the full result dataset by column *col* then refresh page 0., Restore the splitter to its pre-collapse size once a real result grid is being…, Import data from CSV, JSON, or Excel files, Clear all filters and show original data, Update column options in all filter rows

### Community 38 - "_ensure_dsskey_stub"
Cohesion: 0.33
Nodes (8): _dsskey_stub_class(), _ensure_dsskey_stub(), A stand-in for paramiko.DSSKey that raises SSHException instead of being bare…, Idempotently patch paramiko.DSSKey with the stub above if this paramiko version…, End-to-end reproduction: a real, valid private key that is NOT RSA (so…, test_dsskey_stub_raises_sshexception_not_attributeerror(), test_ensure_dsskey_stub_is_idempotent_and_never_leaves_it_none(), test_key_fallback_reaches_ecdsa_after_dsskey_slot_without_crashing()

### Community 39 - "ExportScopeDialog"
Cohesion: 0.15
Nodes (7): Export chosen tables' structure and/or data to a single SQL dump (issue #39:…, Write *table*'s structure and/or data to the already-open file handle *fh*, per…, Export a single table without needing an open query tab (issue #39). 'Data…, ExportScopeDialog, QDialog, Lets the user choose which tables and how much of each (structure only / data…, structure' | 'data' | 'both

### Community 40 - ".load_schema"
Cohesion: 0.12
Nodes (9): QTreeWidgetItem, Manually reconnect to the database and reload the schema., Rasterize *emoji* to a small QIcon (cached) — this app has no icon image…, Rebuild schema_tree as a flat list of just the active category's items — no…, Populate the tree/autocomplete from a cached snapshot (issue #71) immediately;…, Attach a live elapsed-time ticker to *item* (already inserted in the tree) for…, Fetch schema on a daemon thread using a *dedicated* connection (see…, Push tables/columns to autocomplete as soon as they're fetched — ahead of the… (+1 more)

### Community 41 - "._display_data"
Cohesion: 0.14
Nodes (7): DataFrame, Sort the currently displayed data by the clicked column (client-side)., Check if there are any uncommitted changes, Load data from DataFrame, Display dataframe in the table, Apply filter to a specific column, Apply all active column filters

### Community 42 - ".apply_all_filters"
Cohesion: 0.33
Nodes (3): Pre-populate the filter bar with the clicked cell value and show it., Toggle filter visibility, Apply all filter conditions to the dataframe

### Community 44 - "AdvancedFilterDialog"
Cohesion: 0.25
Nodes (5): AdvancedFilterDialog, QDialog, Build and return the WHERE condition, Advanced filter dialog with column, operator, and value selection, Update value input based on selected operator

### Community 45 - "QPlainTextEdit"
Cohesion: 0.29
Nodes (4): QPlainTextEdit, Open a resizable text viewer for the current cell value., Cmd+Enter opens a detail popup for the current cell value., Show a resizable read-only popup with the full cell value.

### Community 46 - "ColumnFilterDialog"
Cohesion: 0.18
Nodes (6): ColumnFilterDialog, QDialog, Get the current filters, Dialog for filtering table data by column and value, Update the display of active filters, Show filter dialog for current results

### Community 47 - "deploy.sh"
Cohesion: 0.52
Nodes (6): die(), ok(), deploy.sh script, step(), substep(), warn()

### Community 50 - "QForge — AI Load Context"
Cohesion: 0.13
Nodes (14): 1. Environment classification and visible status, 2. Read-only mode, 3. Dangerous-query guard, 4. Transaction controls, 5. Query limits, timeout, and cancellation, 6. Audit trail, Before changing code, Current behaviour to preserve (+6 more)

### Community 51 - "schema_diff.py"
Cohesion: 0.16
Nodes (25): build_schema_diff(), ColumnDiff, ColumnInfo, _diff_columns(), _diff_foreign_keys(), _diff_indexes(), _diff_table(), _fetch_side() (+17 more)

### Community 52 - "QForge — AI Flush Context"
Cohesion: 0.29
Nodes (6): Current state (as of 2026-08-13), Exact next step, Milestone status, Open threads not yet started, QForge — AI Flush Context, Where the detail lives

### Community 53 - "Security Policy"
Cohesion: 0.33
Nodes (5): Known Limitations (not vulnerabilities, but worth knowing), Reporting a Vulnerability, Scope, Security Policy, Supported Versions

### Community 54 - "erd_dialog.py"
Cohesion: 0.16
Nodes (21): build_erd_graph(), ErdColumn, ErdGraph, ErdRelationship, ErdTable, fetch_table_indexes(), _is_unique_single_column(), Build a read-only ER-diagram graph model from database metadata (issue #63).… (+13 more)

### Community 55 - ".set_frozen_columns"
Cohesion: 0.14
Nodes (7): Inner (non-reentrant) implementation of _display_data., Re-apply colour to all rows that have a known dirty state. Called after every…, Freeze the leading *count* (current visual order) columns so they stay visible…, Arrange the frozen view's header sections in the same left-to-right visual…, Show only the frozen columns in the overlay view; the main view keeps showing…, Right-click a column header: freeze up through that column, or unfreeze if a…, Set column widths: sample the first 50 rows to pick a sensible width, clamped…

### Community 56 - "_CellEditCommand"
Cohesion: 0.13
Nodes (8): _CellEditCommand, Evaluate formula in cell (e.g., =NOW(), =UPPER(text)), Evaluate a formula string and return result, One cell's text changed from old_text to new_text., Track when an item is modified, push an undo step, and paint changed cell + row., (at_row, delta) this command's own undo/redo causes to every OTHER row index —…, Update modified_rows/modified_cells for one cell against its original DB value…, Set a cell's text without going through on_item_changed (so restoring it…

### Community 57 - "_ClickableRow"
Cohesion: 0.24
Nodes (4): _ClickableRow, QWidget, A QWidget that behaves like a button for the schema sidebar's category rows…, Optimistic open (previously-visited remote/SSH connection with a warm schema…

### Community 58 - "connection_panel.py"
Cohesion: 0.17
Nodes (8): ConnectionPanel ═══════════════ A self-contained widget that owns one database…, Schema Compare — read-only structural diff between two saved connections (issue…, snippet_manager.py — Persistent SQL snippet store. Snippets are keyed by a…, ThemeManager — QForge design system (see ai/ui-design.md for the source of…, Render a 16x16 'x' close-tab icon in *color_hex* to a cached PNG and return its…, *hex_color* ("#RRGGBB") at *alpha_hex* opacity ("00"-"ff"), as a Qt stylesheet…, ThemeManager, Connection environment tiers — shared by the connection dialog, workspace, and…

### Community 59 - ".execute_query"
Cohesion: 0.12
Nodes (9): Return True if the exception looks like a dropped/lost connection., Return 'BEGIN'/'COMMIT'/'ROLLBACK' if *query* is exactly one transaction-…, Execute a SELECT query and return results as DataFrame, Split *script* into statements, execute each. Returns list of (label,…, Internal: run a SQL statement without reconnect logic. For statements that…, Issue #72: every write in the app funnels through here, so this is the single…, Internal: run DML without reconnect logic. The explicit commit() is a no-op for…, Raise ReadOnlyViolation if *sql* contains a write statement and this connection… (+1 more)

### Community 60 - ".init_ui"
Cohesion: 0.21
Nodes (9): _fill_columns_table(), _fill_fk_table(), _fill_indexes_table(), _filter_table_rows(), _new_readonly_table(), QTableWidget, Live, in-memory, case-insensitive row filter (issue #53)., A search box above *tbl* that filters its rows as the user types. (+1 more)

### Community 62 - "main.py"
Cohesion: 0.18
Nodes (12): _asset_path(), Resolve a bundled asset both when running from source and when frozen by…, Fetch a read-only snapshot of a database's schema. Uses its own dedicated,…, get_logger(), Get existing logger or create new one, Setup and configure logger for the application Args: name: Logger name level:…, setup_logger(), Cache database schema metadata locally (issue #71) so a previously visited… (+4 more)

### Community 63 - "_ErdView"
Cohesion: 0.14
Nodes (6): QGraphicsView, QRectF, _ErdView, _MinimapView, Wheel-to-zoom; left-drag on empty canvas (or empty space between nodes) pans,…, Small always-fit overview sharing the main scene. Draws the main view's visible…

### Community 64 - "ErdDialog"
Cohesion: 0.21
Nodes (3): ErdDialog, QDialog, Opens against a connection profile's config dict (same shape passed to…

### Community 65 - ".__init__"
Cohesion: 0.18
Nodes (3): _InspectorPanel, QWidget, Toggleable drawer showing a selected table's columns, indexes, and…

### Community 66 - "TransactionError"
Cohesion: 0.21
Nodes (7): Commit the open manual transaction and restore autocommit., Roll back the open manual transaction and restore autocommit., Undo the autocommit=False set by begin_transaction() for drivers that need it…, Execute a detected transaction-control statement and return a status DataFrame,…, Raised on transaction-state misuse (double BEGIN, COMMIT/ROLLBACK with nothing…, Start a manual transaction, turning off this connection's per-statement…, TransactionError

### Community 70 - "._repaint_row"
Cohesion: 0.21
Nodes (6): QColor, row was toggled into/out of the pending-deletion set., Apply the correct colour to every cell in *row* based on its state., Legacy helper — delegates to _repaint_row when state is already set., Mark selected rows for deletion (Ctrl/Cmd+Z steps back through them one row at…, _RowDeleteMarkCommand

### Community 72 - "QForge UI Design System"
Cohesion: 0.15
Nodes (15): Accessibility and quality checks, Component rules, Connection Tab/Title Label Line, Dark — graphite blue, Dark Palette — Graphite Blue, Design direction, Environment safety indicators, Implementation notes (+7 more)

### Community 73 - "._on_query_done"
Cohesion: 0.14
Nodes (8): Restore the Run button to its default ready state., Load FK map for *table_name* into the result grid and ensure the navigate_fk…, Receives worker `done` signal via bridge — guaranteed main thread., Multi-statement result handler — shows each SELECT in its own sub-tab., Receives worker `errored` signal via bridge — guaranteed main thread., Receives worker `cancelled` signal via bridge — guaranteed main thread., Called at the end of every query-completion handler. Decides whether this tab's…, Slide-in notification from the right when a background query finishes.

### Community 77 - "_RelationshipLineItem"
Cohesion: 0.22
Nodes (10): QGraphicsPathItem, QPainterPath, QPointF, _append_cardinality_marks(), _build_curved_path(), _curve_crosses_obstacles(), Cubic-bezier connector, bowed perpendicular to the straight line so it reads…, Appends a crow's-foot ("many") or a single tick ("one") to *path* at endpoint… (+2 more)

### Community 79 - "SchemaCompareDialog"
Cohesion: 0.32
Nodes (4): QDialog, QTreeWidgetItem, Opens standalone — the caller only supplies which connection id to preselect as…, SchemaCompareDialog

### Community 80 - "_CollapseToggle"
Cohesion: 0.24
Nodes (6): QGraphicsRectItem, QGraphicsSimpleTextItem, _CollapseToggle, _header_color(), QColor, Small header glyph that collapses/expands its parent table node, intercepting…

### Community 81 - "_QueryWorker"
Cohesion: 0.09
Nodes (11): QObject, _QueryWorker, Guarantee this panel has at least one query tab. Called once right after a…, Open a blank SQL query tab., Execute inline-edit SQL statements against the live connection., Execute the SQL in `tab` on a background thread; Cancel actually stops it.…, Handler for the tab's Begin/Commit/Rollback buttons — runs *stmt* through the…, Open the Query Verifier dialog pre-populated with the current tab's query. (+3 more)

### Community 84 - "QComboBox"
Cohesion: 0.29
Nodes (4): QComboBox, _load_connection_profiles(), _profile_label(), Raw (credential-free) connection list for populating the source/ target pickers…

### Community 87 - "erd_layout.py"
Cohesion: 0.57
Nodes (6): clear(), _key(), load(), Persist per-connection ER diagram layouts (table positions + collapsed state)…, _read_all(), save()

### Community 89 - "UpdateInstaller"
Cohesion: 0.25
Nodes (5): QThread, Path to the .app bundle currently running, or None if not frozen., Downloads the release DMG, verifies it against the published SHA256SUMS.txt,…, running_app_bundle_path(), UpdateInstaller

### Community 90 - "._show_schema_tab_context_menu"
Cohesion: 0.33
Nodes (3): Right-click menu on the "Schema" sidebar tab — schema-level actions that aren't…, Open a read-only ER diagram of the current database (issue #62). Builds its own…, Open the read-only Schema Compare dialog (issue #68), preselecting this…

### Community 91 - "show_toast"
Cohesion: 0.40
Nodes (4): QWidget, Non-blocking slide-in notification, for status that shouldn't interrupt the…, Slide a small notification in from the bottom-right corner of *parent* and back…, show_toast()

### Community 92 - "QForge — Launch Plan (macOS, individual/consumer sales)"
Cohesion: 0.40
Nodes (4): 1. Pricing, 2. Investment to build the launch pieces, 3. Apple Developer ID + notarization timeline, QForge — Launch Plan (macOS, individual/consumer sales)

## Knowledge Gaps
- **60 isolated node(s):** `build.sh script`, `qforge`, `1. Pricing`, `2. Investment to build the launch pieces`, `3. Apple Developer ID + notarization timeline` (+55 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **22 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `SqlTab` connect `SqlTab` to `QueryVerifierDialog`, `SqlCompleter`, `MainWindow`, `SnippetManager`, `CodeEditor`, `EditableTableWidget`, `._find_live_update`, `ConnectionPanel`, `TableViewWidget`, `._set_status`, `.eventFilter`, `FilterHeaderWidget`, `._refresh_result_view`, `.apply_all_filters`, `.update_theme`, `ColumnFilterDialog`, `.get_query`, `.commit_changes`, `_ClickableRow`, `connection_panel.py`, `main.py`, `_QueryWorker`, `._apply_diff_highlights`?**
  _High betweenness centrality (0.213) - this node is a cross-community bridge._
- **Why does `DbService` connect `DbService` to `test_db_service_sqlite.py`, `QueryVerifierDialog`, `TransactionError`, `ConnectionDialog`, `MainWindow`, `Exception`, `_QueryWorker`, `schema_diff.py`, `ConnectionPanel`, `erd_dialog.py`, `_ClickableRow`, `connection_panel.py`, `.execute_query`, `load`, `main.py`, `test_schema_snapshot.py`?**
  _High betweenness centrality (0.166) - this node is a cross-community bridge._
- **Why does `ConnectionPanel` connect `ConnectionPanel` to `QueryVerifierDialog`, `DbSwitcherDialog`, `ConnectionDialog`, `MainWindow`, `SnippetManager`, `StructureEditorDialog`, `SqlTab`, `DbService`, `QuickSearchDialog`, `QueryHistoryDialog`, `QueryHistory`, `.open_table_view`, `TableViewWidget`, `ExportScopeDialog`, `.load_schema`, `_ClickableRow`, `connection_panel.py`, `main.py`, `ErdDialog`, `._on_query_done`, `SchemaCompareDialog`, `_QueryWorker`, `._show_schema_tab_context_menu`, `._prompt_params`?**
  _High betweenness centrality (0.158) - this node is a cross-community bridge._
- **Are the 16 inferred relationships involving `ConnectionPanel` (e.g. with `MainWindow` and `DbService`) actually correct?**
  _`ConnectionPanel` has 16 INFERRED edges - model-reasoned connections that need verification._
- **Are the 19 inferred relationships involving `DbService` (e.g. with `MainWindow` and `ErdColumn`) actually correct?**
  _`DbService` has 19 INFERRED edges - model-reasoned connections that need verification._
- **Are the 14 inferred relationships involving `SqlTab` (e.g. with `MainWindow` and `_ClickableRow`) actually correct?**
  _`SqlTab` has 14 INFERRED edges - model-reasoned connections that need verification._
- **Are the 2 inferred relationships involving `EditableTableWidget` (e.g. with `SqlTab` and `TableViewWidget`) actually correct?**
  _`EditableTableWidget` has 2 INFERRED edges - model-reasoned connections that need verification._