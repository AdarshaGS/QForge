from PySide6.QtCore import Qt, QStringListModel, QSortFilterProxyModel
from PySide6.QtWidgets import QCompleter
from PySide6.QtGui import QTextCursor
import re


class SmartFilterProxyModel(QSortFilterProxyModel):
    """Proxy model for smart filtering with ranking"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.search_term = ""
        self.scored_items = []  # List of (text, type, score) tuples
    
    def set_search_term(self, term):
        self.search_term = term.lower()
        self.invalidateFilter()
    
    def set_scored_items(self, items):
        """Set items with their scores for ranking"""
        self.scored_items = items
    
    def filterAcceptsRow(self, source_row, source_parent):
        if not self.search_term:
            return True
        
        index = self.sourceModel().index(source_row, 0, source_parent)
        text = self.sourceModel().data(index, Qt.DisplayRole)
        
        if not text:
            return False
        
        # Extract actual text (before type marker)
        actual_text = text.split("    [")[0].lower()
        search_lower = self.search_term
        
        # Fast filtering - prefix match first (most common)
        if actual_text.startswith(search_lower):
            return True
        
        # Contains match
        if search_lower in actual_text:
            return True
        
        # Fuzzy match - all chars in order (last resort, slowest)
        search_idx = 0
        for char in actual_text:
            if search_idx < len(search_lower) and char == search_lower[search_idx]:
                search_idx += 1
        return search_idx == len(search_lower)
    
    def _fuzzy_match(self, search, target):
        """Fast fuzzy matching - REMOVED, now inline in filterAcceptsRow for performance"""
        pass
    
    def lessThan(self, left, right):
        """Sort by relevance score"""
        left_text = self.sourceModel().data(left, Qt.DisplayRole)
        right_text = self.sourceModel().data(right, Qt.DisplayRole)
        
        if not left_text or not right_text:
            return False
        
        # Calculate scores for sorting
        left_actual = left_text.split("    [")[0].lower()
        right_actual = right_text.split("    [")[0].lower()
        
        left_score = self._calculate_score(self.search_term, left_actual)
        right_score = self._calculate_score(self.search_term, right_actual)
        
        return left_score > right_score  # Higher score first
    
    def _calculate_score(self, search, target):
        """Calculate relevance score"""
        if not search:
            return 50
        if search == target:
            return 1000
        if target.startswith(search):
            return 900 - len(target)
        if search in target:
            return 800 - target.find(search)
        return 500 - len(target)


class SqlCompleter(QCompleter):
    """Intelligent SQL autocompletion with context awareness"""

    SQL_KEYWORDS = [
        "SELECT", "FROM", "WHERE", "INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP",
        "TABLE", "DATABASE", "INDEX", "VIEW", "PROCEDURE", "FUNCTION", "TRIGGER",
        "JOIN", "INNER JOIN", "LEFT JOIN", "RIGHT JOIN", "OUTER JOIN", "FULL JOIN", "CROSS JOIN",
        "ON", "USING", "ORDER BY", "GROUP BY", "HAVING", "LIMIT", "OFFSET", "DISTINCT",
        "AND", "OR", "NOT", "IN", "LIKE", "BETWEEN", "IS", "NULL", "AS",
        "ASC", "DESC", "VALUES", "SET", "INTO", "UNION", "EXCEPT", "INTERSECT",
        "EXISTS", "ANY", "ALL", "SOME", "PRIMARY KEY", "FOREIGN KEY", "REFERENCES",
        "DEFAULT", "CHECK", "UNIQUE", "AUTO_INCREMENT", "WITH", "RECURSIVE"
    ]
    
    SQL_FUNCTIONS = [
        "COUNT", "SUM", "AVG", "MAX", "MIN", "ROUND", "CONCAT", "SUBSTRING",
        "UPPER", "LOWER", "TRIM", "LENGTH", "COALESCE", "CAST", "NOW", "CURDATE",
        "CASE", "WHEN", "THEN", "ELSE", "END", "IF", "IFNULL", "NULLIF",
        "DATE", "TIME", "TIMESTAMP", "YEAR", "MONTH", "DAY", "HOUR", "MINUTE",
        "DATE_FORMAT", "STR_TO_DATE", "DATEDIFF", "DATE_ADD", "DATE_SUB",
        "GROUP_CONCAT", "JSON_EXTRACT", "JSON_OBJECT", "REPLACE", "REGEXP"
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        
        # Use string list model with proxy for filtering
        self.source_model = QStringListModel()
        self.proxy_model = SmartFilterProxyModel()
        self.proxy_model.setSourceModel(self.source_model)
        self.setModel(self.proxy_model)
        
        self.setCaseSensitivity(Qt.CaseInsensitive)
        self.setCompletionMode(QCompleter.PopupCompletion)
        self.setMaxVisibleItems(15)
        self.setFilterMode(Qt.MatchContains)
        
        self.tables = []
        self.columns = {}  # {table_name: [column_names]}
        self.aliases = {}  # {alias: table_name}
        self.current_context = "GENERAL"

    def set_schema(self, tables, columns_dict):
        """Update schema information for autocomplete"""
        self.tables = tables
        self.columns = {}
        
        # Extract column names from column info dicts
        for table, cols in columns_dict.items():
            if isinstance(cols, list) and cols:
                if isinstance(cols[0], dict):
                    self.columns[table] = [col.get("Field", "") for col in cols]
                else:
                    self.columns[table] = cols
    
    def update_suggestions(self, query_text, cursor_position, search_term):
        """Update suggestions based on context - this rebuilds the suggestion list"""
        suggestions = []
        
        # Parse context
        self.current_context = self._parse_context(query_text, cursor_position)
        
        # Extract aliases from query
        self.aliases = self._extract_aliases(query_text)
        
        # Check for dot notation (alias.column or table.column)
        if '.' in search_term:
            parts = search_term.split('.')
            if len(parts) == 2:
                prefix, partial_col = parts
                suggestions = self._get_dot_notation_suggestions(prefix)
        else:
            # Context-aware suggestions (build full list, filtering happens in proxy)
            if self.current_context == "AFTER_FROM" or self.current_context == "AFTER_JOIN":
                suggestions.extend(self._get_all_tables())
            
            elif self.current_context == "AFTER_SELECT":
                suggestions.extend(self._get_all_columns())
                suggestions.extend(self._get_all_functions())
                suggestions.extend(self._get_keywords_subset(["DISTINCT", "COUNT", "SUM", "AVG", "MAX", "MIN"]))
            
            elif self.current_context == "AFTER_WHERE" or self.current_context == "AFTER_ON":
                suggestions.extend(self._get_all_columns_with_aliases())
                suggestions.extend(self._get_keywords_subset(["AND", "OR", "NOT", "IN", "LIKE", "BETWEEN", "IS", "NULL"]))
            
            elif self.current_context == "AFTER_ORDER_BY" or self.current_context == "AFTER_GROUP_BY":
                suggestions.extend(self._get_all_columns_with_aliases())
                if self.current_context == "AFTER_ORDER_BY":
                    suggestions.extend(self._get_keywords_subset(["ASC", "DESC"]))
            
            else:
                suggestions.extend(self._get_all_keywords())
                suggestions.extend(self._get_all_tables())
                suggestions.extend(self._get_all_columns())
                suggestions.extend(self._get_all_functions())
        
        # Update model
        formatted_suggestions = [f"{text}    [{item_type}]" for text, item_type in suggestions]
        self.source_model.setStringList(formatted_suggestions)
        
        # Set search term for filtering
        self.proxy_model.set_search_term(search_term)
        
        return len(formatted_suggestions)
    
    def _parse_context(self, query_text, cursor_pos):
        """Determine the SQL context at cursor position"""
        text_before = query_text[:cursor_pos].upper()
        
        keywords_positions = []
        for keyword in ["SELECT", "FROM", "WHERE", "JOIN", "ON", "ORDER BY", "GROUP BY", "HAVING"]:
            pos = text_before.rfind(keyword)
            if pos != -1:
                keywords_positions.append((pos, keyword))
        
        if not keywords_positions:
            return "GENERAL"
        
        last_keyword = max(keywords_positions, key=lambda x: x[0])[1]
        
        if last_keyword == "FROM":
            return "AFTER_FROM"
        elif "JOIN" in last_keyword:
            return "AFTER_JOIN"
        elif last_keyword == "SELECT":
            return "AFTER_SELECT"
        elif last_keyword == "WHERE":
            return "AFTER_WHERE"
        elif last_keyword == "ON":
            return "AFTER_ON"
        elif last_keyword == "ORDER BY":
            return "AFTER_ORDER_BY"
        elif last_keyword == "GROUP BY":
            return "AFTER_GROUP_BY"
        
        return "GENERAL"
    
    def _extract_aliases(self, query_text):
        """Extract table aliases from the query"""
        aliases = {}
        pattern = r'(?:FROM|JOIN)\s+(\w+)(?:\s+(?:AS\s+)?(\w+))?'
        matches = re.finditer(pattern, query_text, re.IGNORECASE)
        
        for match in matches:
            table_name = match.group(1)
            alias = match.group(2)
            
            if alias and table_name in self.tables:
                aliases[alias.lower()] = table_name
        
        return aliases
    
    def _get_dot_notation_suggestions(self, prefix):
        """Get suggestions for alias.column or table.column"""
        suggestions = []
        prefix_lower = prefix.lower()
        
        # Check if prefix is an alias
        if prefix_lower in self.aliases:
            table_name = self.aliases[prefix_lower]
            if table_name in self.columns:
                for col in self.columns[table_name]:
                    suggestions.append((f"{prefix}.{col}", "Column"))
        
        # Check if prefix is a table name
        elif prefix in self.tables and prefix in self.columns:
            for col in self.columns[prefix]:
                suggestions.append((f"{prefix}.{col}", "Column"))
        
        return suggestions
    
    def _get_all_keywords(self):
        """Get all SQL keywords"""
        return [(kw, "Keyword") for kw in self.SQL_KEYWORDS]
    
    def _get_keywords_subset(self, keyword_subset):
        """Get a subset of keywords"""
        return [(kw, "Keyword") for kw in keyword_subset]
    
    def _get_all_tables(self):
        """Get all table names"""
        return [(table, "Table") for table in self.tables]
    
    def _get_all_columns(self):
        """Get all column names with table context for disambiguation"""
        column_counts = {}  # Count how many tables have each column
        column_tables = {}  # Map column to tables
        
        for table, cols in self.columns.items():
            for col in cols:
                if col not in column_counts:
                    column_counts[col] = 0
                    column_tables[col] = []
                column_counts[col] += 1
                column_tables[col].append(table)
        
        suggestions = []
        for col, count in column_counts.items():
            if count == 1:
                # Unique column - just show column name
                suggestions.append((col, "Column"))
            else:
                # Ambiguous - show with all tables
                suggestions.append((col, "Column"))
                # Also add table.column for each table
                for table in column_tables[col][:3]:  # Limit to first 3 tables
                    suggestions.append((f"{table}.{col}", f"Column ({table})"))
        
        return suggestions
    
    def _get_all_columns_with_aliases(self):
        """Get columns including alias.column format"""
        suggestions = self._get_all_columns()
        
        # Add alias.column format
        for alias, table in self.aliases.items():
            if table in self.columns:
                for col in self.columns[table]:
                    suggestions.append((f"{alias}.{col}", "Column"))
        
        return suggestions
    
    def _get_all_functions(self):
        """Get all SQL functions"""
        return [(f"{func}()", "Function") for func in self.SQL_FUNCTIONS]
