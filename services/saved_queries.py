"""
saved_queries.py — Persistent store for user-saved SQL queries.

Backs the "Queries" sidebar tab (issue #130): a flat list of named,
optionally-favorited saved queries, independent of query history/snippets.
"""
import json
import os
import uuid
from datetime import datetime

from utils.logger import get_logger
from utils.paths import app_data_dir

logger = get_logger()

_FILE = os.path.join(app_data_dir(), "saved_queries.json")


class SavedQueries:
    """CRUD + persistence for named, favoritable saved SQL queries."""

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
            logger.warning(f"Failed to load saved queries from {_FILE}: {ex}")
        self.queries = []

    def save(self):
        try:
            os.makedirs(os.path.dirname(_FILE), exist_ok=True)
            with open(_FILE, "w") as f:
                json.dump(self.queries, f, indent=2)
        except Exception as ex:
            logger.warning(f"Failed to save saved queries to {_FILE}: {ex}")

    def add(self, name: str, sql: str, favorite: bool = False) -> dict:
        entry = {
            "id": uuid.uuid4().hex,
            "name": (name or "").strip() or "Untitled Query",
            "query": sql,
            "favorite": favorite,
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

    def toggle_favorite(self, query_id: str) -> bool:
        entry = self.get(query_id)
        if entry is None:
            return False
        entry["favorite"] = not entry.get("favorite", False)
        self.save()
        return entry["favorite"]

    def get_favorites(self) -> list:
        return [q for q in self.queries if q.get("favorite")]

    def get_saved(self) -> list:
        return [q for q in self.queries if not q.get("favorite")]

    def search(self, keyword: str) -> list:
        keyword = keyword.lower()
        return [q for q in self.queries
                if keyword in q.get("name", "").lower()
                or keyword in q.get("query", "").lower()]
