import json
import os
from datetime import datetime

from services.entitlements import Limit, entitlements
from utils.logger import get_logger

logger = get_logger()


class QueryHistory:
    """Simple query history manager"""

    HISTORY_FILE = "query_history.json"
    MAX_HISTORY = 100  # Keep last 100 queries

    def __init__(self):
        self.queries = []
        self.load_history()

    def load_history(self):
        """Load query history from file"""
        if not os.path.exists(self.HISTORY_FILE):
            self.queries = []
            return

        try:
            with open(self.HISTORY_FILE, "r") as file:
                self.queries = json.load(file)
        except Exception as ex:
            logger.warning(f"Failed to load query history: {ex}")
            self.queries = []

    def save_history(self):
        """Save query history to file"""
        try:
            with open(self.HISTORY_FILE, "w") as file:
                json.dump(self.queries, file, indent=2)
        except Exception as ex:
            logger.warning(f"Failed to save history: {ex}")

    def add_query(self, query, connection_name, rows=0, execution_time=0,
                  cost_score=None, cost_label=None):
        """Add a query to history. cost_score/cost_label are the optional
        pre-run estimate from services/query_cost.py — omitted (None) for
        writes, multi-statement scripts, or when no estimate was
        computed; existing history entries predate these fields and read
        back with them as None, same as a query that skipped them."""
        query = query.strip()

        if not query:
            return

        entry = {
            "query": query,
            "connection": connection_name,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "rows": rows,
            "execution_time": execution_time,
            "cost_score": cost_score,
            "cost_label": cost_label,
        }

        self.queries.insert(0, entry)  # Add to beginning

        # Keep only the current edition's allowance (Free: entitlement_config
        # default 20; Pro: 100 — a storage-sanity cap, not a monetization
        # limit). Silent trim, no upgrade prompt: history overflowing just
        # drops the oldest entries, it never blocks the query that ran.
        cap = entitlements.limit(Limit.QUERY_HISTORY) or self.MAX_HISTORY
        if len(self.queries) > cap:
            self.queries = self.queries[:cap]

        self.save_history()

    def get_recent_queries(self, limit=20):
        """Get recent queries"""
        return self.queries[:limit]

    def clear_history(self):
        """Clear all history"""
        self.queries = []
        self.save_history()

    def search_queries(self, keyword):
        """Search queries by keyword"""
        keyword = keyword.lower()
        return [q for q in self.queries if keyword in q["query"].lower()]
