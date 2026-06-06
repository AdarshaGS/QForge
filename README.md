# SQL Workbench

A lightweight, cross-platform SQL database client built with Python and PySide6. A simple alternative to TablePlus for managing your databases.

## Features

✨ **Multi-Database Support**
- MySQL
- PostgreSQL  
- SQLite

🎨 **Modern UI**
- **Syntax highlighting** for SQL queries with color-coded keywords, strings, and comments
- **Multi-tab editor** for running multiple queries
- **Schema browser** with tables and columns
- **Sortable result grids** with alternating row colors

✏️ **Inline Data Editing** ⭐ NEW
- Double-click cells to edit data directly
- Add new rows with right-click menu
- Delete rows
- Visual indicators for modified/new/deleted rows
- Commit/revert changes with preview

🔍 **Query Features** ⭐ NEW
- **Run selected query** - Execute only highlighted SQL (Ctrl+R)
- **IntelliSense autocomplete** - Table names, columns, and SQL keywords
- **SQL auto-formatting** - Uppercase keywords, proper indentation
- **Query history** - Search and reuse past queries

🔧 **Data Management** ⭐ NEW
- **Advanced filtering UI** - Filter without writing WHERE clauses
- **Structure Editor** - GUI for creating tables with columns, types, and constraints
- **Export to CSV**
- **Right-click table actions** - View data, generate SELECT

📜 **Developer Tools**
- Comprehensive logging system
- Connection management with saved profiles
- Query execution timing
- Transaction-ready architecture

## Installation

1. Clone or download this repository

2. Install dependencies:
```bash
pip install -r requirements.txt
```

## Usage

Run the application:
```bash
python main.py
```

### First Time Setup

1. On first launch, you'll see the Connection Manager
2. Click "Add" to create a new connection
3. Fill in the connection details:
   - **Type**: Choose MySQL, PostgreSQL, or SQLite
   - **Name**: A friendly name for this connection
   - **Host/Port**: Database server details (not needed for SQLite)
   - **Database/Path**: Database name or file path (for SQLite)
   - **User/Password**: Credentials (not needed for SQLite)
4. Click "Connect" to establish the connection

### Working with Queries

- **Run Query**: Click "▶ Run" or press **Ctrl+R**
- **Run Selected**: Highlight SQL text and press **Ctrl+R** to run only the selected portion
- **Autocomplete**: Start typing table/column names - suggestions appear automatically
- **Format SQL**: Click "Format SQL" to auto-format your query
- **View History**: Click "History" to see and reuse previous queries
- **Filter Results**: Click "🔍 Filter" to add filters without writing WHERE clauses
- **Export Results**: Click "Export CSV" to save query results
- **New Tab**: Click "+ New Tab" to open additional query tabs

### Editing Data

1. Run a SELECT query to display data
2. **Double-click any cell** to edit its value
3. **Right-click** in the result grid for options:
   - Add Row - Insert a new row
   - Delete Selected Rows - Mark rows for deletion
   - Revert Changes - Undo all edits
4. Click **"💾 Commit Changes"** to save (shows SQL preview)
5. Click **"↶ Revert"** to undo all changes

### Creating Tables

1. Click **"📋 Create Table"** button
2. Enter table name
3. Add columns:
   - Set name, type, length
   - Choose nullable, primary key, auto-increment
   - Set default value (optional)
4. Click **"+ Add Column"** for each column
5. Click **"Preview SQL"** to see the CREATE TABLE statement
6. Click **"Create Table"** to execute

### Schema Browser

- **Double-click** a table to view its data (100 rows)
- **Right-click** a table for options:
  - 📊 View Data (100 rows)
  - 📊 View All Data
  - 🔍 Generate SELECT
  - 🔄 Refresh Schema
- **Browse Schema**: Double-click a table in the left panel to generate a SELECT query

## Project Structure

```
SQL-WorkBench/
├── main.py                          # Main application
├── requirements.txt                 # Python dependencies
├── connections.json                 # Saved database connections
├── query_history.json              # Query execution history
├── services/
│   ├── db_service.py               # Database connection layer
│   └── query_history.py            # Query history management
├── ui/
│   ├── connection_dialog.py        # Connection manager UI
│   ├── sql_tab.py                  # SQL editor tab widget
│   ├── sql_highlighter.py          # Syntax highlighting
│   └── query_history_dialog.py     # Query history viewer
├── utils/
│   └── logger.py                   # Logging configuration
└── logs/                            # Application logs (auto-created)
```

## Keyboard Shortcuts

- **Ctrl+R**: Run query (or run selected query if text is highlighted)
- **Ctrl+N**: New tab (when available)
- **Ctrl+W**: Close tab
- **Ctrl+/**: Comment/uncomment (in development)
- **Double-click** table name: View table data
- **Double-click** cell: Edit value

## Logging

Application logs are automatically saved to the `logs/` directory with daily rotation. Each log file is named `app_YYYYMMDD.log`.

## Dependencies

- **PySide6**: Qt 6 for Python (UI framework)
- **pymysql**: MySQL database connector
- **psycopg2-binary**: PostgreSQL database connector
- **pandas**: Data manipulation and analysis
- **sqlparse**: SQL query formatting

## Notes

- Passwords are stored in plain text in `connections.json`. Keep this file secure.
- Query history is limited to the last 100 queries
- SQLite connections don't require host, port, user, or password

## License

This project is for personal and educational use.
