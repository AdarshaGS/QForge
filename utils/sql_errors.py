"""SQL error classification (issue #178, extended for #143).

Turns a raw driver exception string into a short title (for a badge/heading)
and a human-readable hint (for the primary message) — shared by
ui/sql_tab.py's query-error card and ui/edit_error_dialog.py's grid-save
error dialog, so the same failure reads the same way whether it came from
running a query or saving an edited row. Pure string logic, no Qt.
"""
import re as _re

_ERROR_HINTS = [
    # Syntax errors
    (r"you have an error in your sql syntax",
     "Check for missing commas, unmatched parentheses, or typos near the marked position."),
    (r"syntax error at or near",
     "Check for missing commas, unmatched parentheses, or incorrect keyword usage."),
    # Unknown column / table
    (r"unknown column '(.+?)'",
     lambda m: f"Column '{m.group(1)}' doesn't exist — check the table schema or your alias."),
    (r"table '(.+?)' doesn't exist",
     lambda m: f"Table '{m.group(1)}' not found — verify the table name and active database."),
    (r"relation \"(.+?)\" does not exist",
     lambda m: f"Table '{m.group(1)}' not found — check spelling and current schema."),
    # Access denied
    (r"access denied",
     "Permission denied — your user lacks privileges for this operation."),
    (r"permission denied",
     "Permission denied — your user lacks privileges for this operation."),
    # Duplicate / constraint
    (r"duplicate entry '(.+?)' for key '(.+?)'",
     lambda m: f"Duplicate value '{m.group(1)}' on key '{m.group(2)}' — value must be unique."),
    (r"unique constraint",
     "A unique constraint was violated — the value already exists in that column."),
    (r"foreign key constraint",
     "Foreign key violation — the referenced row doesn't exist or a dependent row blocks deletion."),
    (r"cannot be null|null value in column",
     "A required (NOT NULL) column has no value — provide a value for all required fields."),
    # Connection
    (r"lost connection|server has gone away|broken pipe",
     "The database connection dropped — try running the query again."),
    (r"connection refused|could not connect",
     "Cannot reach the database server — check host, port, and firewall settings."),
    # Timeout / cancel
    (r"query was cancelled|canceling statement",
     "The query was cancelled by the user."),
    (r"lock wait timeout|deadlock",
     "A lock timeout or deadlock occurred — another process may be holding a lock on this table."),
    # Disk / space
    (r"disk full|no space left",
     "The server disk is full — contact your DBA."),
    # Data too long
    (r"data too long for column '(.+?)'",
     lambda m: f"The value for '{m.group(1)}' exceeds the column's maximum length."),
]


def sql_error_hint(message: str, query: str = "") -> str:
    """Return a short actionable hint for a SQL error message, or empty string."""
    ml = message.lower()
    for pattern, hint in _ERROR_HINTS:
        m = _re.search(pattern, ml)
        if m:
            return hint(m) if callable(hint) else hint
    return ""


_MYSQL_CODE_RE = _re.compile(r"^\(?(\d{3,5}),")


def sql_error_title(message: str) -> str:
    """Best-effort short title for an error badge/heading, e.g. 'SQL Syntax
    Error (1064)'. Falls back to a generic title when nothing matches."""
    ml = message.lower()
    code_m = _MYSQL_CODE_RE.match(message.strip())
    code_suffix = f" ({code_m.group(1)})" if code_m else ""

    if "syntax" in ml:
        return f"SQL Syntax Error{code_suffix}"
    if "unknown column" in ml:
        return "Unknown Column"
    if "doesn't exist" in ml or "does not exist" in ml:
        return "Table Not Found"
    if "access denied" in ml or "permission denied" in ml:
        return "Permission Denied"
    if "duplicate entry" in ml:
        return "Duplicate Entry"
    if "foreign key" in ml:
        return "Foreign Key Constraint Error"
    if "constraint" in ml:
        return "Constraint Violation"
    if "lost connection" in ml or "gone away" in ml or "broken pipe" in ml \
            or "connection refused" in ml or "could not connect" in ml:
        return "Connection Error"
    if "lock wait" in ml or "deadlock" in ml:
        return "Lock Timeout"
    if "cannot be null" in ml or "null value" in ml:
        return "Missing Required Value"
    if "cancelled" in ml or "canceling" in ml:
        return "Query Cancelled"
    return f"SQL Error{code_suffix}" if code_suffix else "Query Error"
