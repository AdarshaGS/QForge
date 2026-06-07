"""
snippet_manager.py — Persistent SQL snippet store.

Snippets are keyed by a short trigger word (e.g. "sel").
The body may contain ``{cursor}`` which marks where the text-cursor
should land after the snippet is expanded.
"""
from __future__ import annotations

import json
from pathlib import Path

# ── Storage location ──────────────────────────────────────────────────────────
_DATA_DIR     = Path.home() / "Library" / "Application Support" / "QForge"
_SNIPPET_FILE = _DATA_DIR / "snippets.json"

# ── Built-in defaults (written on first run) ──────────────────────────────────
DEFAULT_SNIPPETS: dict[str, dict] = {
    "sel": {
        "name": "SELECT *",
        "description": "Basic SELECT query",
        "body": "SELECT *\nFROM {cursor}\nWHERE ;",
    },
    "selc": {
        "name": "SELECT columns",
        "description": "SELECT specific columns",
        "body": "SELECT {cursor}\nFROM \nWHERE ;",
    },
    "join": {
        "name": "INNER JOIN",
        "description": "JOIN … ON …",
        "body": "JOIN {cursor}\n  ON .id = .id",
    },
    "ljoin": {
        "name": "LEFT JOIN",
        "description": "LEFT JOIN … ON …",
        "body": "LEFT JOIN {cursor}\n  ON .id = .id",
    },
    "ins": {
        "name": "INSERT INTO",
        "description": "Insert one row",
        "body": "INSERT INTO {cursor} (\n  \n)\nVALUES (\n  \n);",
    },
    "upd": {
        "name": "UPDATE … SET",
        "description": "Update rows",
        "body": "UPDATE {cursor}\nSET  = \nWHERE  = ;",
    },
    "del": {
        "name": "DELETE FROM",
        "description": "Delete rows",
        "body": "DELETE FROM {cursor}\nWHERE  = ;",
    },
    "cnt": {
        "name": "COUNT(*)",
        "description": "Count rows",
        "body": "SELECT COUNT(*)\nFROM {cursor};",
    },
    "cte": {
        "name": "CTE (WITH …)",
        "description": "Common Table Expression",
        "body": "WITH {cursor} AS (\n  SELECT *\n  FROM \n)\nSELECT *\nFROM cte;",
    },
    "case": {
        "name": "CASE WHEN",
        "description": "CASE … WHEN … END",
        "body": "CASE\n  WHEN {cursor} THEN \n  ELSE \nEND",
    },
    "grp": {
        "name": "GROUP BY … HAVING",
        "description": "Aggregation pattern",
        "body": "SELECT {cursor}, COUNT(*)\nFROM \nGROUP BY \nHAVING COUNT(*) > 1;",
    },
    "exist": {
        "name": "EXISTS subquery",
        "description": "Correlated EXISTS check",
        "body": "WHERE EXISTS (\n  SELECT 1\n  FROM {cursor}\n  WHERE .id = .id\n)",
    },
}


class SnippetManager:
    """CRUD + persistence for user-defined SQL snippets."""

    def __init__(self):
        self._data: dict[str, dict] = {}
        self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self):
        if _SNIPPET_FILE.exists():
            try:
                with open(_SNIPPET_FILE, "r", encoding="utf-8") as fh:
                    self._data = json.load(fh)
                return
            except Exception:
                pass
        # First run — seed with defaults and persist
        self._data = {k: dict(v) for k, v in DEFAULT_SNIPPETS.items()}
        self._save()

    def _save(self):
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(_SNIPPET_FILE, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh, indent=2, ensure_ascii=False)

    # ── Read ──────────────────────────────────────────────────────────────────

    def get_all(self) -> dict[str, dict]:
        """Return a copy of all snippets."""
        return dict(self._data)

    def get(self, trigger: str) -> dict | None:
        return self._data.get(trigger)

    # ── Write ─────────────────────────────────────────────────────────────────

    def upsert(self, trigger: str, name: str, body: str, description: str = ""):
        """Create or update a snippet."""
        key = trigger.strip().lower()
        if not key:
            raise ValueError("Trigger must not be empty")
        self._data[key] = {
            "name":        name.strip(),
            "body":        body,
            "description": description.strip(),
        }
        self._save()

    def delete(self, trigger: str):
        self._data.pop(trigger, None)
        self._save()

    def reset_defaults(self):
        """Restore built-in snippets (keeps user-added ones)."""
        for k, v in DEFAULT_SNIPPETS.items():
            self._data.setdefault(k, dict(v))
        self._save()

    # ── Import / Export ───────────────────────────────────────────────────────

    def export_to_file(self, path: str):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh, indent=2, ensure_ascii=False)

    def import_from_file(self, path: str) -> int:
        """Merge snippets from *path* and return the number imported."""
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("File must contain a JSON object")
        self._data.update(data)
        self._save()
        return len(data)
