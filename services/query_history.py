import json
import os
import uuid
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
                  cost_score=None, cost_label=None, cost_detail=None, profile_detail=None):
        """Add a query to history, returning its new entry id (or None if
        *query* was blank and nothing was added). cost_score/cost_label
        are the optional pre-run estimate from services/query_cost.py —
        omitted (None) for writes, multi-statement scripts, or when no
        estimate was computed; existing history entries predate these
        fields and read back with them as None, same as a query that
        skipped them. cost_detail is that same estimate's full issues
        list (via query_cost.estimate_to_dict) — kept separate from
        cost_score/cost_label so a caller/reader that only wants the
        summary never has to load it. profile_detail is the equivalent
        for a post-run EXPLAIN ANALYZE profile (query_cost.
        profile_to_dict) — always None at write time; it's filled in
        later via update_entry(), either by an auto-profile run or by the
        user manually running Profile on this entry from the Analyzer."""
        query = query.strip()

        if not query:
            return None

        entry_id = uuid.uuid4().hex[:12]
        entry = {
            "id": entry_id,
            "query": query,
            "connection": connection_name,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "rows": rows,
            "execution_time": execution_time,
            "cost_score": cost_score,
            "cost_label": cost_label,
            "cost_detail": cost_detail,
            "profile_detail": profile_detail,
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
        return entry_id

    def update_entry(self, entry_id: str, **fields) -> bool:
        """Merge *fields* into the entry with this id, if it's still
        around — used to persist a Profile (or a refreshed Estimate) run
        from the Analyze Query dialog after the fact, so reopening that
        history entry shows it without re-running. Returns False (a
        no-op, not an error) if the entry has since aged out of the
        history cap — e.g. profile_ready arriving for a query that fell
        off the end of history in the time it took EXPLAIN ANALYZE to
        run."""
        for entry in self.queries:
            if entry.get("id") == entry_id:
                entry.update(fields)
                self.save_history()
                return True
        return False

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
