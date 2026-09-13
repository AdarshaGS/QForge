"""
saved_visual_queries.py — Persistent store for saved Visual Query Builder
definitions (VQB.6, issue #196).

Sibling of services/saved_queries.py (same JSON-file CRUD pattern), but
each entry holds the full builder *state* (tables, joins, SELECT list,
WHERE/HAVING trees, GROUP BY/ORDER BY/LIMIT, and canvas node positions) so
reopening a saved visual query restores the canvas and can be edited
visually again — not just a flattened SQL string like a saved query
(services/saved_queries.py) or a snippet (ui/snippet_manager.py). The
`sql` field is the last-generated string, kept only so list views can show
a preview; it is never re-parsed back into state.
"""
import json
import os
import uuid
from datetime import datetime

from utils.logger import get_logger
from utils.paths import app_data_dir

logger = get_logger()

_FILE = os.path.join(app_data_dir(), "saved_visual_queries.json")


class SavedVisualQueries:
    """CRUD + persistence for saved Visual Query Builder definitions."""

    def __init__(self):
        self.queries: list[dict] = []
        self.load()

    def load(self):
        try:
            if os.path.exists(_FILE):
                with open(_FILE) as f:
                    self.queries = json.load(f)
                    return
        except Exception as ex:
            logger.warning(f"Failed to load saved visual queries from {_FILE}: {ex}")
        self.queries = []

    def save(self):
        try:
            os.makedirs(os.path.dirname(_FILE), exist_ok=True)
            with open(_FILE, "w") as f:
                json.dump(self.queries, f, indent=2)
        except Exception as ex:
            logger.warning(f"Failed to save visual queries to {_FILE}: {ex}")

    def add(self, name: str, connection_id: str, database: str,
            builder_state: dict, positions: dict, sql: str) -> dict:
        entry = {
            "id": uuid.uuid4().hex,
            "name": (name or "").strip() or "Untitled Visual Query",
            "connection_id": connection_id,
            "database": database,
            "builder_state": builder_state,
            "positions": positions,
            "sql": sql,
            "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.queries.insert(0, entry)
        self.save()
        return entry

    def get(self, query_id: str) -> dict | None:
        return next((q for q in self.queries if q["id"] == query_id), None)

    def update(self, query_id: str, **fields) -> dict | None:
        entry = self.get(query_id)
        if entry is None:
            return None
        entry.update(fields)
        self.save()
        return entry

    def delete(self, query_id: str):
        self.queries = [q for q in self.queries if q["id"] != query_id]
        self.save()

    def for_connection(self, connection_id: str) -> list:
        return [q for q in self.queries if q.get("connection_id") == connection_id]


saved_visual_queries = SavedVisualQueries()
