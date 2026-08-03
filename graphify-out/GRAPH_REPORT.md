# Graph Report - QForge  (2026-08-01)

## Corpus Check
- 56 files · ~66,290 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1047 nodes · 1800 edges · 89 communities (64 shown, 25 thin omitted)
- Extraction: 93% EXTRACTED · 7% INFERRED · 0% AMBIGUOUS · INFERRED: 119 edges (avg confidence: 0.65)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `d2a6927d`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- [[_COMMUNITY_PySide6 Widget Imports|PySide6 Widget Imports]]
- [[_COMMUNITY_Shared Utilities & App Bootstrap|Shared Utilities & App Bootstrap]]
- [[_COMMUNITY_Connection Manager Dialog|Connection Manager Dialog]]
- [[_COMMUNITY_Connection Panel UI Widgets|Connection Panel UI Widgets]]
- [[_COMMUNITY_Main Window Lifecycle|Main Window Lifecycle]]
- [[_COMMUNITY_Production-Safety Roadmap & Guard Rationale|Production-Safety Roadmap & Guard Rationale]]
- [[_COMMUNITY_Advanced Filter Dialog|Advanced Filter Dialog]]
- [[_COMMUNITY_SQL Snippet Management|SQL Snippet Management]]
- [[_COMMUNITY_Credential Store & Query Analyzer|Credential Store & Query Analyzer]]
- [[_COMMUNITY_Code Editor Qt Internals|Code Editor Qt Internals]]
- [[_COMMUNITY_Table Structure Editor|Table Structure Editor]]
- [[_COMMUNITY_SQL Tab Utilities|SQL Tab Utilities]]
- [[_COMMUNITY_DbService Schema Introspection|DbService Schema Introspection]]
- [[_COMMUNITY_Connection Panel Tab Management|Connection Panel Tab Management]]
- [[_COMMUNITY_Editable Table Clipboard & Changes|Editable Table Clipboard & Changes]]
- [[_COMMUNITY_Quick Search Dialog|Quick Search Dialog]]
- [[_COMMUNITY_Editor Find & Replace|Editor Find & Replace]]
- [[_COMMUNITY_DbService Connection Setup|DbService Connection Setup]]
- [[_COMMUNITY_Query History Dialog|Query History Dialog]]
- [[_COMMUNITY_Connection Panel Tabs & Pill UI|Connection Panel Tabs & Pill UI]]
- [[_COMMUNITY_Connection Panel Write Guard & Reconnect|Connection Panel Write Guard & Reconnect]]
- [[_COMMUNITY_Connection Panel Query Callbacks|Connection Panel Query Callbacks]]
- [[_COMMUNITY_DataFrame Export & SQL Literal Tests|DataFrame Export & SQL Literal Tests]]
- [[_COMMUNITY_Table View Streaming Widget|Table View Streaming Widget]]
- [[_COMMUNITY_Table View Paging & Filtering|Table View Paging & Filtering]]
- [[_COMMUNITY_Database Switcher Dialog|Database Switcher Dialog]]
- [[_COMMUNITY_SQL Tab Result Status & Errors|SQL Tab Result Status & Errors]]
- [[_COMMUNITY_Editable Table Row Operations|Editable Table Row Operations]]
- [[_COMMUNITY_SQL Editor Keybindings|SQL Editor Keybindings]]
- [[_COMMUNITY_DbService Query Execution|DbService Query Execution]]
- [[_COMMUNITY_Filter Header Widget|Filter Header Widget]]
- [[_COMMUNITY_Schema Snapshot & Regression Tests|Schema Snapshot & Regression Tests]]
- [[_COMMUNITY_SQLite DbService Tests|SQLite DbService Tests]]
- [[_COMMUNITY_Editable Table Filtering & Revert|Editable Table Filtering & Revert]]
- [[_COMMUNITY_Editable Table Formula Evaluation|Editable Table Formula Evaluation]]
- [[_COMMUNITY_SQL Tab Result Paging|SQL Tab Result Paging]]
- [[_COMMUNITY_Editable Table Copy & Context Menu|Editable Table Copy & Context Menu]]
- [[_COMMUNITY_SQL Tab Data Import|SQL Tab Data Import]]
- [[_COMMUNITY_DbService Read-Only Guard|DbService Read-Only Guard]]
- [[_COMMUNITY_Database Management Menu|Database Management Menu]]
- [[_COMMUNITY_Editable Table Dirty-State Rendering|Editable Table Dirty-State Rendering]]
- [[_COMMUNITY_SQL Tab Filter Chips|SQL Tab Filter Chips]]
- [[_COMMUNITY_Theme Update Propagation|Theme Update Propagation]]
- [[_COMMUNITY_SQL Tab Diff Highlighting|SQL Tab Diff Highlighting]]
- [[_COMMUNITY_SQL Query Parameter Substitution|SQL Query Parameter Substitution]]
- [[_COMMUNITY_Editable Table Change Tracking|Editable Table Change Tracking]]
- [[_COMMUNITY_Editable Table Cell Detail Popup|Editable Table Cell Detail Popup]]
- [[_COMMUNITY_SQL Tab Query Extraction|SQL Tab Query Extraction]]
- [[_COMMUNITY_SQL Tab Commit Changes|SQL Tab Commit Changes]]
- [[_COMMUNITY_Before-Changing-Code Process & Definition of Done|Before-Changing-Code Process & Definition of Done]]
- [[_COMMUNITY_Table View Loading Overlay|Table View Loading Overlay]]
- [[_COMMUNITY_Editable Table Copy Cell|Editable Table Copy Cell]]
- [[_COMMUNITY_Editable Table FK Metadata|Editable Table FK Metadata]]
- [[_COMMUNITY_Table View Filter Toggle|Table View Filter Toggle]]
- [[_COMMUNITY_Table View Hide Filter|Table View Hide Filter]]
- [[_COMMUNITY_Table View OpenRefocus|Table View Open/Refocus]]
- [[_COMMUNITY_Table View Show Structure|Table View Show Structure]]
- [[_COMMUNITY_Table View Alter Table|Table View Alter Table]]
- [[_COMMUNITY_Table View Quick Search Handler|Table View Quick Search Handler]]
- [[_COMMUNITY_Table View Pill Style Stub|Table View Pill Style Stub]]
- [[_COMMUNITY_Table View Session Tabs|Table View Session Tabs]]
- [[_COMMUNITY_Community 63|Community 63]]
- [[_COMMUNITY_Table View Label|Table View Label]]
- [[_COMMUNITY_Table View Disconnect|Table View Disconnect]]
- [[_COMMUNITY_Navigation & Tabs Design Rules|Navigation & Tabs Design Rules]]
- [[_COMMUNITY_SQL Workspace Design Rules|SQL Workspace Design Rules]]
- [[_COMMUNITY_Community 70|Community 70]]
- [[_COMMUNITY_Community 72|Community 72]]
- [[_COMMUNITY_Community 73|Community 73]]
- [[_COMMUNITY_Community 74|Community 74]]
- [[_COMMUNITY_Community 75|Community 75]]
- [[_COMMUNITY_Community 76|Community 76]]
- [[_COMMUNITY_Community 77|Community 77]]
- [[_COMMUNITY_Community 78|Community 78]]
- [[_COMMUNITY_Community 79|Community 79]]
- [[_COMMUNITY_Community 80|Community 80]]
- [[_COMMUNITY_Community 81|Community 81]]
- [[_COMMUNITY_Community 82|Community 82]]
- [[_COMMUNITY_Community 83|Community 83]]
- [[_COMMUNITY_Community 84|Community 84]]
- [[_COMMUNITY_Community 85|Community 85]]
- [[_COMMUNITY_Community 86|Community 86]]
- [[_COMMUNITY_Community 87|Community 87]]
- [[_COMMUNITY_Community 88|Community 88]]

## God Nodes (most connected - your core abstractions)
1. `SqlTab` - 82 edges
2. `ConnectionPanel` - 74 edges
3. `DbService` - 53 edges
4. `ConnectionDialog` - 51 edges
5. `EditableTableWidget` - 48 edges
6. `TableViewWidget` - 47 edges
7. `MainWindow` - 43 edges
8. `CodeEditor` - 26 edges
9. `QueryVerifierDialog` - 26 edges
10. `SqlCompleter` - 24 edges

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

## Hyperedges (group relationships)
- **Production Safety Recommended Scope (six slices)** — ai_load_context_environment_classification, ai_load_context_read_only_mode, ai_load_context_dangerous_query_guard, ai_load_context_transaction_controls, ai_load_context_query_limits_timeout_cancellation, ai_load_context_audit_trail [EXTRACTED 1.00]
- **Cross-doc Environment and Read-only Visual Language** — ai_load_context_environment_classification, ai_load_context_read_only_mode, ai_ui_design_environment_safety_indicators, ai_ui_design_connection_label_line [INFERRED 0.85]
- **Bypass-prevention Across Guard Entry Points** — ai_load_context_non_negotiable_design_principles, ai_load_context_dangerous_query_guard, ai_load_context_read_only_mode, ai_load_context_current_behaviour_to_preserve [INFERRED 0.85]

## Communities (89 total, 25 thin omitted)

### Community 0 - "PySide6 Widget Imports"
Cohesion: 0.12
Nodes (15): QSyntaxHighlighter, AggRow, ColDiffRow, ExplainRow, QueryVerifier, Compare two SQL queries using an existing DbService connection., Run EXPLAIN and return list[ExplainRow]. Non-fatal on any error., One row from EXPLAIN output — key fields only. (+7 more)

### Community 1 - "Shared Utilities & App Bootstrap"
Cohesion: 0.20
Nodes (5): QListWidgetItem, Open the Cmd+K database switcher dialog., DbSwitcherDialog, DbSwitcherDialog ──────────────── Cmd+K spotlight-style popup for switching data, Cmd+K database switcher — shows all databases, type to filter.

### Community 2 - "Connection Manager Dialog"
Cohesion: 0.05
Nodes (21): QColor, ConnectionDialog, Deselect tree and clear form to create a new connection., Rebuild self.connections order to match the new drag-dropped tree order., Show context menu on right-click over a connection item., Load connection form when a connection item (not a group) is clicked., Return the currently selected connection QTreeWidgetItem, or None., Walk the tree and select the item whose UserRole data equals idx. (+13 more)

### Community 3 - "Connection Panel UI Widgets"
Cohesion: 0.06
Nodes (22): QPoint, QStyledItemDelegate, sql_completer.py — Professional context-aware SQL autocomplete ═════════════════, Paints each row: bold-highlighted prefix on the left, type badge on the right., Frameless floating window shown below the cursor.     Never steals keyboard focu, Drives autocomplete for a QTextEdit.      Design goals     ─────────────     • G, Replace the snippet cache (called after SnippetManager is updated)., Refresh schema cache (called on connect / db switch). (+14 more)

### Community 4 - "Main Window Lifecycle"
Cohesion: 0.07
Nodes (15): MainWindow, Close the connection panel at *index* without confirmation., Close all connection tabs except the one at *keep_index*., Cmd+W: close current content tab; if none, close the connection., Right-click menu on a connection tab., Top-level window.      A QTabBar at the top holds one tab per open database conn, Idle connections show their environment's semantic colour (the         safety cu, Briefly show a green banner at the top when a dropped connection recovers. (+7 more)

### Community 5 - "Production-Safety Roadmap & Guard Rationale"
Cohesion: 0.17
Nodes (22): Classification, classify(), is_dangerous(), Classifies SQL statements for the read-only guard and dangerous-query guard (ai/, Single source of truth for splitting a script into statements —     replaces the, True if this statement should trigger the dangerous-query     confirmation (ai/l, split_statements(), test_classify_falls_back_for_types_get_type_reports_unknown() (+14 more)

### Community 6 - "Advanced Filter Dialog"
Cohesion: 0.13
Nodes (7): DataFilterDialog, Disable value input for NULL checks, Add a filter to the list, Remove selected filter, Generate SQL WHERE clause from filters, Dialog for building data filters, Load existing filters

### Community 7 - "SQL Snippet Management"
Cohesion: 0.09
Nodes (10): snippet_editor_dialog.py — Full CRUD UI for SQL snippets.  Layout ────── ┌──────, Full CRUD dialog for managing SQL snippets., SnippetEditorDialog, snippet_manager.py — Persistent SQL snippet store.  Snippets are keyed by a shor, Return a copy of all snippets., Create or update a snippet., Restore built-in snippets (keeps user-added ones)., Merge snippets from *path* and return the number imported. (+2 more)

### Community 8 - "Credential Store & Query Analyzer"
Cohesion: 0.15
Nodes (26): Path, analyze_explain_rows(), analyze_sql_text(), generate_optimized_sql(), html_escape(), _int(), Issue, list_sql_files() (+18 more)

### Community 9 - "Code Editor Qt Internals"
Cohesion: 0.11
Nodes (8): QPainter, QRect, QSize, QStyleOptionViewItem, CodeEditor, _Gutter, Narrow sidebar painted by CodeEditor., QPlainTextEdit with:       • Line-number gutter (auto-width, dim colour)       •

### Community 10 - "Table Structure Editor"
Cohesion: 0.11
Nodes (11): Enable/disable length input based on type, Enable/disable auto increment when primary key is toggled, Add column to the table, Dialog for creating/editing table structure, Remove selected column, Generate CREATE TABLE or ALTER TABLE SQL, Generate CREATE TABLE SQL, Pre-fill the columns grid with the table's current columns. (+3 more)

### Community 11 - "SQL Tab Utilities"
Cohesion: 0.10
Nodes (9): Minify/compress SQL query., Update status when filters change, Route show-structure request to ConnectionPanel parent., Set the editor content., Highlight cells that differ between _prev_df and current_df., Remove diff highlighting (restore normal theme colours)., Get the main window by traversing up the parent hierarchy, Enable commit/revert buttons when changes are made (+1 more)

### Community 12 - "DbService Schema Introspection"
Cohesion: 0.09
Nodes (9): DbService, Return FK definitions for *table_name*.         Each dict has keys: column, ref_, Return a short version string like 'MySQL 8.0.41' or 'PostgreSQL 15.3'., Get list of tables based on database type, Get list of views based on database type, Get list of functions/procedures based on database type, Get columns for a table based on database type, Return {table_name: [col_name, ...]} for all tables in one query.         Used t (+1 more)

### Community 13 - "Connection Panel Tab Management"
Cohesion: 0.10
Nodes (19): Build a macOS app locally, Classify a connection's environment, Core workflows, Dependencies, Edit and move data, Explore a database, Install and run, Keyboard shortcuts (+11 more)

### Community 14 - "Editable Table Clipboard & Changes"
Cohesion: 0.11
Nodes (9): EditableTableWidget, Get current filter status, Enhanced table widget with inline editing capabilities, Get all changes as SQL statements         Returns dict with 'updates', 'inserts', Store FK metadata: list of {column, ref_table, ref_column} dicts., Copy selected cells to clipboard, Copy all values from selected column, Set current cell to NULL (+1 more)

### Community 15 - "Quick Search Dialog"
Cohesion: 0.14
Nodes (8): QKeyEvent, QuickSearchDialog, Handle arrow key navigation from search input, Filter items based on search text, Quick search dialog for searching tables, databases, functions, views, Check if search characters appear in order in text, Get icon for item type - removed, no icons, Handle item selection

### Community 16 - "Editor Find & Replace"
Cohesion: 0.17
Nodes (6): QTextCursor, Cmd+F: show find bar (replace row hidden)., Cmd+H: show find+replace bar., Build a regex pattern from the current search text + toggles.         Smart-case, Remove all orange match highlights from the editor., Re-run search on every keystroke or toggle change.

### Community 17 - "DbService Connection Setup"
Cohesion: 0.15
Nodes (9): Exception, Connect to PostgreSQL database, Connect to SQLite database, Setup SSH tunnel and return local host/port, Best-effort: kill the running query on the server side.          MySQL  — opens, Re-establish the connection using the stored config., Check if connected by opening a *separate* short-lived connection.         Never, Connect to database based on type (+1 more)

### Community 18 - "Query History Dialog"
Cohesion: 0.08
Nodes (13): QTextEdit, ColumnFilterDialog, Get the current filters, Dialog for filtering table data by column and value, Update the display of active filters, QueryHistoryDialog, Filter history based on search text, Handle selection change (+5 more)

### Community 19 - "Connection Panel Tabs & Pill UI"
Cohesion: 0.19
Nodes (6): QueryHistory, Simple query history manager, Load query history from file, Save query history to file, Add a query to history, Search queries by keyword

### Community 20 - "Connection Panel Write Guard & Reconnect"
Cohesion: 0.27
Nodes (3): Read a CSV file and INSERT all rows into *table_name*., Manually reconnect to the database and reload the schema., Classify *sql* (one statement or a whole script) and show         whatever dialo

### Community 21 - "Connection Panel Query Callbacks"
Cohesion: 0.12
Nodes (8): ConnectionPanel, Place a visible × QPushButton on the tab at the given index., One database connection panel (sidebar + content tabs)., Show a read-only structure popup for *table_name*: columns, indexes, FKs., Return serialisable list of open tabs., Called every 30s — runs the actual ping on a daemon thread so the         UI nev, Persist all pinned SQL tabs to pinned_tabs.json., Open a table view; re-focus if already open.

### Community 22 - "DataFrame Export & SQL Literal Tests"
Cohesion: 0.20
Nodes (11): test_sql_value_literal_leaves_numbers_unquoted(), test_sql_value_literal_null_for_none_and_nan(), test_sql_value_literal_quotes_and_escapes_strings(), test_to_sql_inserts_builds_one_statement_per_row(), Export visible table data to CSV / JSON / Excel / SQL., Export data in multiple formats: CSV, JSON, Excel, SQL, export_dataframe(), Shared CSV/JSON/Excel/SQL export helper for pandas DataFrames. (+3 more)

### Community 23 - "Table View Streaming Widget"
Cohesion: 0.18
Nodes (5): Widget that shows a table with streaming data loading, Open a blank SQL query tab., Return the table label, Disconnect from database, TableViewWidget

### Community 24 - "Table View Paging & Filtering"
Cohesion: 0.15
Nodes (6): Handle column header click to sort the table by the clicked column          Args, Reset to the first page and load it, Apply all filter conditions and reset data loading, Clear all filters and reset, Reload current page (reset and reload first page), Save all changes to the database (Cmd+S)

### Community 25 - "Database Switcher Dialog"
Cohesion: 0.19
Nodes (8): QueryVerifier ───────────── Runs two SQL queries (original vs optimised) and com, Fetch a read-only snapshot of a database's schema.  Uses its own dedicated, thro, ConnectionPanel ═══════════════ A self-contained widget that owns one database c, Return a short actionable hint for a SQL error message, or empty string., _sql_error_hint(), get_logger(), Get existing logger or create new one, Persist pinned/favourite SQL tabs across sessions.  Format: { connection_name: [

### Community 26 - "SQL Tab Result Status & Errors"
Cohesion: 0.20
Nodes (5): Show multiple SELECT results as a horizontal tab bar above the grid., Show an empty Excel-like placeholder grid., Helper: show text in the status_label (QPlainTextEdit)., Display a SQL error inline with actionable hints — always selectable., Show a neutral 'query cancelled' status.

### Community 27 - "Editable Table Row Operations"
Cohesion: 0.18
Nodes (5): Apply the correct colour to every cell in *row* based on its state., Legacy helper — delegates to _repaint_row when state is already set., Mark selected rows for deletion, Duplicate current row, Duplicate all selected rows (Cmd+D)

### Community 28 - "SQL Editor Keybindings"
Cohesion: 0.14
Nodes (7): Called when snippets are changed in the editor dialog., Route key events: popup navigation first, then auto-trigger., Insert a matching closing quote and place the cursor between them.         If th, Return True if the editor cursor is currently inside a quoted string literal., Toggle -- comment on each selected line (or current line)., Cmd+D: expand selection to current word, then find and select next match., Delete the entire line the cursor is on (Cmd+Backspace / ⌘⌫).

### Community 29 - "DbService Query Execution"
Cohesion: 0.20
Nodes (5): Return True if the exception looks like a dropped/lost connection., Execute a SELECT query and return results as DataFrame, Split *script* into statements, execute each. Returns list of         (label, Da, Internal: run a SQL statement without reconnect logic.         For statements th, Internal: run DML without reconnect logic.

### Community 30 - "Filter Header Widget"
Cohesion: 0.20
Nodes (5): FilterHeaderWidget, Emit signal when filter text changes, Clear the filter input, Custom header widget with inline filter, Add filter input boxes to column headers

### Community 31 - "Schema Snapshot & Regression Tests"
Cohesion: 0.39
Nodes (8): fetch_schema_snapshot(), Connect using `config`, gather schema metadata, then disconnect.      Returns a, _make_sqlite_config(), Regression tests for the shared-connection corruption bug: a single DB-API conne, The exact regression this was built to prevent: schema loading must     not read, test_fetch_schema_snapshot_disconnects_its_own_connection(), test_fetch_schema_snapshot_lists_tables_and_views(), test_fetch_schema_snapshot_never_touches_a_primary_connection()

### Community 33 - "Editable Table Filtering & Revert"
Cohesion: 0.17
Nodes (9): Changes Made:, Security Benefits:, Testing:, Usage:, 1. Improved Table Search/Filtering (Fuzzy Matching), 2. Query Execution Cancellation, 3. Enhanced Error Messages with Actionable Information, Technical Details (+1 more)

### Community 35 - "Editable Table Formula Evaluation"
Cohesion: 0.25
Nodes (4): Evaluate formula in cell (e.g., =NOW(), =UPPER(text)), Evaluate a formula string and return result, Track when an item is modified and paint changed cell + row., Show dialog to edit multiple rows at once

### Community 36 - "SQL Tab Result Paging"
Cohesion: 0.25
Nodes (3): Display the current page of _result_view_df in result_table.          Only 500 r, Sort the full result dataset by column *col* then refresh page 0., Clear all filters and show original data

### Community 37 - "Editable Table Copy & Context Menu"
Cohesion: 0.29
Nodes (3): Show comprehensive context menu like TablePlus, Return (headers, [[row values], ...]) for currently selected rows., Copy selected rows to clipboard in the requested format.

### Community 38 - "SQL Tab Data Import"
Cohesion: 0.33
Nodes (8): _dsskey_stub_class(), _ensure_dsskey_stub(), A stand-in for paramiko.DSSKey that raises SSHException instead of     being bar, Idempotently patch paramiko.DSSKey with the stub above if this     paramiko vers, End-to-end reproduction: a real, valid private key that is NOT RSA     (so RSAKe, test_dsskey_stub_raises_sshexception_not_attributeerror(), test_ensure_dsskey_stub_is_idempotent_and_never_leaves_it_none(), test_key_fallback_reaches_ecdsa_after_dsskey_slot_without_crashing()

### Community 39 - "DbService Read-Only Guard"
Cohesion: 0.33
Nodes (4): Raised when a write statement is attempted on a read-only connection.     This i, Run `query` via executemany, in batches, committing once at the end.          on, Raise ReadOnlyViolation if *sql* contains a write statement and         this con, ReadOnlyViolation

### Community 41 - "Editable Table Dirty-State Rendering"
Cohesion: 0.22
Nodes (4): Load data from DataFrame, Display dataframe in the table, Apply filter to a specific column, Apply all active column filters

### Community 42 - "SQL Tab Filter Chips"
Cohesion: 0.33
Nodes (3): Pre-populate the filter bar with the clicked cell value and show it., Toggle filter visibility, Apply all filter conditions to the dataframe

### Community 44 - "SQL Tab Diff Highlighting"
Cohesion: 0.25
Nodes (4): AdvancedFilterDialog, Build and return the WHERE condition, Advanced filter dialog with column, operator, and value selection, Update value input based on selected operator

### Community 45 - "SQL Query Parameter Substitution"
Cohesion: 0.29
Nodes (4): QDialog, Cmd+Enter opens a detail popup for the current cell value., Show a resizable read-only popup with the full cell value., Open a resizable text viewer for the current cell value.

### Community 47 - "Editable Table Cell Detail Popup"
Cohesion: 0.52
Nodes (6): die(), ok(), step(), substep(), warn(), deploy.sh script

### Community 50 - "Before-Changing-Code Process & Definition of Done"
Cohesion: 0.13
Nodes (14): 1. Environment classification and visible status, 2. Read-only mode, 3. Dangerous-query guard, 4. Transaction controls, 5. Query limits, timeout, and cancellation, 6. Audit trail, Before changing code, Current behaviour to preserve (+6 more)

### Community 51 - "Table View Loading Overlay"
Cohesion: 0.22
Nodes (6): QHBoxLayout, QPlainTextEdit, QWidget, _check_row(), One row of the results report: icon + main message + optional subtext., Side-by-side column diff: Row | Column | Original (red) | Optimised (green).

### Community 52 - "Editable Table Copy Cell"
Cohesion: 0.29
Nodes (6): App icon / branding (uncommitted), Current handoff, Exact next step, QForge — AI Flush Context, Slice 1: Environment classification & visible status (done, verified), Slice 2 + 3: Read-only mode & dangerous-query guard (done, verified)

### Community 53 - "Editable Table FK Metadata"
Cohesion: 0.33
Nodes (5): Known Limitations (not vulnerabilities, but worth knowing), Reporting a Vulnerability, Scope, Security Policy, Supported Versions

### Community 55 - "Table View Hide Filter"
Cohesion: 0.33
Nodes (3): Inner (non-reentrant) implementation of _display_data., Re-apply colour to all rows that have a known dirty state.         Called after, Set column widths: sample the first 50 rows to pick a sensible         width, cl

### Community 56 - "Table View Open/Refocus"
Cohesion: 0.35
Nodes (10): QLabel, _env_badge_label(), _header_row(), Query guard dialogs — shown before a write reaches the database when the connect, Informational only — this connection is read-only, nothing to confirm., Shows the exact statement(s) and why they were flagged. Returns True     only if, show_dangerous_confirmation(), show_read_only_blocked() (+2 more)

### Community 58 - "Table View Show Structure"
Cohesion: 0.21
Nodes (5): QThread, QueryVerifierDialog, Substitute parameter values into sql; raise if any are blank., Replace all parameter placeholders with their literal values., _substitute_params()

### Community 62 - "Table View Session Tabs"
Cohesion: 0.21
Nodes (9): _asset_path(), Resolve a bundled asset both when running from source and when frozen     by PyI, ThemeManager — QForge design system (see ai/ui-design.md for the source of truth, ThemeManager, normalize(), Connection environment tiers — shared by the connection dialog, workspace, and t, Coerce any stored/legacy value to a known tier; unrecognized or     missing valu, Setup and configure logger for the application          Args:         name: Logg (+1 more)

### Community 63 - "Community 63"
Cohesion: 0.24
Nodes (7): QFrame, QTableWidget, VerifyResult, _divider(), Render EXPLAIN plan rows as a compact table., Render a DataFrame as a styled table with copy button + context menu.          d, _section_label()

### Community 65 - "Table View Disconnect"
Cohesion: 0.18
Nodes (5): Open the Query Verifier dialog pre-populated with the current tab's query., Reopen tabs from saved session data., Reopen pinned tabs from pinned_tabs.json (called on startup)., Open a blank SQL query tab., Execute inline-edit SQL statements against the live connection.

### Community 66 - "Navigation & Tabs Design Rules"
Cohesion: 0.33
Nodes (3): DataFrame, Import data from CSV, JSON, or Excel files, Update column options in all filter rows

### Community 70 - "Community 70"
Cohesion: 0.53
Nodes (4): test_maybe_limit_coerces_string_limit_to_int(), test_maybe_limit_passthrough_when_limit_not_positive(), test_maybe_limit_strips_trailing_semicolon(), test_maybe_limit_wraps_query_with_limit()

### Community 72 - "Community 72"
Cohesion: 0.15
Nodes (15): Accessibility and quality checks, Component rules, Connection Tab/Title Label Line, Dark — graphite blue, Dark Palette — Graphite Blue, Design direction, Environment safety indicators, Implementation notes (+7 more)

### Community 73 - "Community 73"
Cohesion: 0.13
Nodes (7): Slide-in notification from the right when a background query finishes., Restore the Run button to its default ready state., Load FK map for *table_name* into the result grid and ensure the         navigat, Receives worker `done` signal via bridge — guaranteed main thread., Multi-statement result handler — shows each SELECT in its own sub-tab., Receives worker `errored` signal via bridge — guaranteed main thread., Receives worker `cancelled` signal via bridge — guaranteed main thread.

### Community 74 - "Community 74"
Cohesion: 0.50
Nodes (3): _extract_params(), Return unique parameter names found in sql, preserving order., Scan both editors for parameter placeholders and rebuild the form.

### Community 81 - "Community 81"
Cohesion: 0.18
Nodes (6): QObject, _QueryWorker, Runs one or more SQL statements on a QThread and emits the result.     Receives, Return unique {{param}} names found in *query*, in order of appearance., If *query* contains {{params}}, show an inline dialog and substitute.         Re, Execute the SQL in `tab` on a background thread; Cancel actually stops it.

## Knowledge Gaps
- **53 isolated node(s):** `build.sh script`, `qforge`, `What you can do`, `Prerequisites`, `Install and run` (+48 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **25 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `SqlTab` connect `SQL Tab Utilities` to `PySide6 Widget Imports`, `Connection Panel UI Widgets`, `Main Window Lifecycle`, `SQL Snippet Management`, `Code Editor Qt Internals`, `Editable Table Clipboard & Changes`, `Editor Find & Replace`, `Query History Dialog`, `Connection Panel Query Callbacks`, `DataFrame Export & SQL Literal Tests`, `Table View Streaming Widget`, `Database Switcher Dialog`, `SQL Tab Result Status & Errors`, `SQL Editor Keybindings`, `Filter Header Widget`, `SQL Tab Result Paging`, `SQL Tab Filter Chips`, `Theme Update Propagation`, `SQL Tab Query Extraction`, `SQL Tab Commit Changes`, `Table View Loading Overlay`, `Table View Session Tabs`, `Table View Label`, `Table View Disconnect`, `Navigation & Tabs Design Rules`, `Community 81`?**
  _High betweenness centrality (0.268) - this node is a cross-community bridge._
- **Why does `ConnectionPanel` connect `Connection Panel Query Callbacks` to `Shared Utilities & App Bootstrap`, `Connection Manager Dialog`, `Main Window Lifecycle`, `Table Structure Editor`, `SQL Tab Utilities`, `DbService Schema Introspection`, `Quick Search Dialog`, `Query History Dialog`, `Connection Panel Tabs & Pill UI`, `Connection Panel Write Guard & Reconnect`, `Table View Streaming Widget`, `Database Switcher Dialog`, `Database Management Menu`, `Table View Loading Overlay`, `Table View New Tab`, `Table View Show Structure`, `Table View Session Tabs`, `Table View Disconnect`, `Community 73`, `Community 81`?**
  _High betweenness centrality (0.158) - this node is a cross-community bridge._
- **Why does `DbService` connect `DbService Schema Introspection` to `SQLite DbService Tests`, `PySide6 Widget Imports`, `Connection Manager Dialog`, `Main Window Lifecycle`, `SQL Tab Data Import`, `DbService Read-Only Guard`, `DbService Connection Setup`, `Community 81`, `Connection Panel Query Callbacks`, `Database Switcher Dialog`, `Table View Show Structure`, `Table View New Tab`, `DbService Query Execution`, `Table View Session Tabs`, `Schema Snapshot & Regression Tests`?**
  _High betweenness centrality (0.144) - this node is a cross-community bridge._
- **Are the 13 inferred relationships involving `SqlTab` (e.g. with `MainWindow` and `ConnectionPanel`) actually correct?**
  _`SqlTab` has 13 INFERRED edges - model-reasoned connections that need verification._
- **Are the 12 inferred relationships involving `ConnectionPanel` (e.g. with `MainWindow` and `DbService`) actually correct?**
  _`ConnectionPanel` has 12 INFERRED edges - model-reasoned connections that need verification._
- **Are the 6 inferred relationships involving `DbService` (e.g. with `MainWindow` and `ConnectionDialog`) actually correct?**
  _`DbService` has 6 INFERRED edges - model-reasoned connections that need verification._
- **Are the 5 inferred relationships involving `ConnectionDialog` (e.g. with `MainWindow` and `DbService`) actually correct?**
  _`ConnectionDialog` has 5 INFERRED edges - model-reasoned connections that need verification._