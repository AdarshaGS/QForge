import json
import os
from datetime import datetime


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
        except Exception:
            self.queries = []

    def save_history(self):
        """Save query history to file"""
        try:
            with open(self.HISTORY_FILE, "w") as file:
                json.dump(self.queries, file, indent=2)
        except Exception as ex:
            print(f"Failed to save history: {ex}")

    def add_query(self, query, connection_name, rows=0, execution_time=0):
        """Add a query to history"""
        query = query.strip()
        
        if not query:
            return

        entry = {
            "query": query,
            "connection": connection_name,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "rows": rows,
            "execution_time": execution_time
        }

        self.queries.insert(0, entry)  # Add to beginning

        # Keep only last MAX_HISTORY queries
        if len(self.queries) > self.MAX_HISTORY:
            self.queries = self.queries[:self.MAX_HISTORY]

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
