# QForge

QForge is a desktop SQL client for exploring and working with **MySQL**,
**PostgreSQL**, and **SQLite** databases. Built with Python and PySide6, it
combines a SQL editor, schema browser, editable data grids, connection
profiles, and query-analysis tools in a focused native interface.

It is currently macOS-oriented: the included build script produces a macOS
app and user settings are stored under `~/Library/Application Support/QForge`.

## What you can do

- Connect to MySQL, PostgreSQL, and SQLite; keep several database connections
  open at once in separate top-level tabs.
- Classify connection profiles as local, staging, or production; QForge shows
  a persistent environment indicator for every open connection.
- Connect to MySQL or PostgreSQL through an SSH tunnel using a password or
  private key.
- Save, group, search, reorder, test, and reconnect connection profiles.
- Browse tables, views, functions, columns, indexes, and foreign keys.
- Open tables in paginated data views, sort columns, filter data, and follow
  foreign-key relationships.
- Write and run single statements or SQL scripts with multiple statements.
- Use syntax highlighting, schema-aware completion, SQL formatting, snippets,
  find/replace, line comments, and editor zoom.
- Edit result-grid rows inline, add or delete rows, preview generated changes,
  then commit or revert them.
- Create or alter tables from the UI.
- Import data into a query tab from CSV, JSON, or Excel, or import CSV/TSV
  rows directly into a table.
- Export query results as CSV, JSON, Excel, or SQL `INSERT` statements.
- Keep query history, pin query tabs for later sessions, and restore the last
  workspace.
- Compare two query results with the built-in verifier, including row/column
  differences, aggregates, timings, and available `EXPLAIN` plans.
- Switch between light and dark themes, cancel running queries, and check for
  application updates.

## Quick start

### Prerequisites

- Python 3
- Access to a MySQL, PostgreSQL, or SQLite database
- `pip`

### Install and run

```bash
git clone https://github.com/AdarshaGS/QForge.git
cd QForge

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

python main.py
```

On first launch, create a connection profile in the Connection Manager, test
it if desired, then select **Connect**.

Choose an environment for each profile: **Local**, **Staging**, or
**Production**. QForge makes the active environment visible in both the
connection tab and workspace; this label is an accident-prevention aid and
does not replace database permissions.

For SQLite, choose **SQLite** and provide the database file path. Host, port,
username, and password are not required.

## Core workflows

### Explore a database

Use the **Schema** sidebar to browse database objects. Double-click a table or
view to open it in a paginated data tab. Right-click a table to open it, edit
its structure, import CSV data into it, or refresh the schema.

Use **Ctrl+P** for quick search across schema items, and **Ctrl+R** or **F5**
to refresh the current view.

### Run SQL

Open a query tab with **Ctrl+T**, write SQL, and click **Run**. QForge runs the
selected text when there is a selection; otherwise it runs the statement at
the cursor, falling back to the editor contents. Scripts with several
semicolon-separated statements produce a result tab for each statement.

The editor provides completion from the connected schema and supports reusable
SQL snippets. Enable **Auto-Format** to format SQL before it runs. The
**Diff** control highlights changes between the last two result sets, while
**Verify** compares the current query with an alternative query for equivalent
results and performance information.

### Edit and move data

Run a `SELECT`, then edit cells in the result grid or use its context menu to
add or delete rows. QForge tracks unsaved changes. Choose **Commit Changes**
to review and apply them, or **Revert** to discard them.

Use **File → Export Data…** to save query results as CSV, JSON, Excel, or SQL
inserts. **File → Import Data…** loads CSV, JSON, or Excel data into the
current query tab. To insert a CSV/TSV into an existing table, right-click the
table in the schema browser and choose **Import CSV into Table…**.

## Keyboard shortcuts

| Shortcut | Action |
| --- | --- |
| `Ctrl+N` | Open a connection |
| `Ctrl+T` | New query tab |
| `Ctrl+W` | Close current tab / dialog |
| `Ctrl+R` or `F5` | Refresh current view |
| `Ctrl+P` | Search database objects |
| `Ctrl+E` | Export current query results |
| `Ctrl+Shift+I` | Import data into the current query tab |
| `Ctrl+Q` | Quit |
| `Ctrl++` / `Ctrl+-` / `Ctrl+0` | Zoom in / out / reset |

On macOS, Qt may display or accept the matching Command-key shortcuts,
depending on the system key mapping.

## Supported databases

| Database | Driver | SSH tunnel |
| --- | --- | --- |
| MySQL | `pymysql` | Yes |
| PostgreSQL | `psycopg2-binary` | Yes |
| SQLite | Python standard library | Not applicable |

## Local data and credentials

QForge keeps its application data in:

```text
~/Library/Application Support/QForge/
```

This includes saved connection metadata, snippets, pinned tabs, and session
state. Query history is stored as `query_history.json` in the directory from
which QForge is launched. Logs are created in `logs/`.

Passwords are not written into connection profiles. QForge uses the platform
keyring when it is available and falls back to an encrypted local credential
file with owner-only permissions. Treat exported profiles, logs, and query
history appropriately: SQL text can still contain sensitive information.

## Project layout

```text
.
├── main.py                 # Application entry point and main window
├── services/
│   ├── db_service.py       # Database connections, queries, schema access
│   ├── query_history.py    # Persistent query history
│   └── query_verifier.py   # Query-result and EXPLAIN comparison
├── ui/
│   ├── connection_dialog.py  # Connection profile manager
│   ├── connection_panel.py   # Per-connection workspace
│   ├── sql_tab.py            # SQL editor and result grid
│   ├── table_view_widget.py  # Paginated table browser
│   └── structure_editor.py   # Create/alter table UI
├── utils/                  # Credentials, logging, update and session helpers
├── query_analyzer.py       # Standalone MySQL query-plan analysis CLI
├── requirements.txt
├── build.sh                # macOS app/DMG build script
└── deploy.sh               # GitHub release and Homebrew-tap helper
```

## Standalone query analyzer

`query_analyzer.py` is a separate, MySQL-focused command-line tool. It reads
SQL files, runs `EXPLAIN` (and `EXPLAIN ANALYZE` when supported), identifies
common performance concerns, and writes an HTML report plus optimized-query
output.

```bash
python query_analyzer.py --conn "My connection" --queries ./queries --out ./optimized_queries
```

It expects a connection profile in `connections.json`; use `--help` for all
options. Review its output before running any generated SQL in production.

## Build a macOS app

The repository includes a PyInstaller-based build script that creates
`dist/QForge.app` and a `QForge.dmg` installer.

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
./build.sh
```

The script installs PyInstaller into the active virtual environment if needed.
It uses macOS utilities such as `hdiutil`, so run it on macOS.

## Dependencies

- PySide6
- pandas
- pymysql
- psycopg2-binary
- sshtunnel
- sqlparse
- openpyxl
- cryptography

See [requirements.txt](requirements.txt) for version constraints.

## License

This project is intended for personal and educational use.
