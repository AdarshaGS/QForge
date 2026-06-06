from PySide6.QtCore import QRegularExpression
from PySide6.QtGui import QSyntaxHighlighter, QTextCharFormat, QColor, QFont


class SqlHighlighter(QSyntaxHighlighter):
    """Simple SQL syntax highlighter"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.highlighting_rules = []

        # SQL Keywords
        keyword_format = QTextCharFormat()
        keyword_format.setForeground(QColor("#569cd6"))  # Brighter blue for better visibility
        keyword_format.setFontWeight(QFont.Bold)

        keywords = [
            "SELECT", "FROM", "WHERE", "INSERT", "UPDATE", "DELETE",
            "CREATE", "ALTER", "DROP", "TABLE", "DATABASE", "INDEX",
            "JOIN", "INNER", "LEFT", "RIGHT", "OUTER", "ON", "AS",
            "ORDER", "BY", "GROUP", "HAVING", "LIMIT", "OFFSET",
            "AND", "OR", "NOT", "IN", "LIKE", "BETWEEN", "IS", "NULL",
            "DISTINCT", "COUNT", "SUM", "AVG", "MAX", "MIN",
            "SET", "VALUES", "INTO", "TRUNCATE", "PRIMARY", "KEY",
            "FOREIGN", "REFERENCES", "UNIQUE", "DEFAULT", "CHECK",
            "CONSTRAINT", "AUTO_INCREMENT", "CASCADE", "GRANT", "REVOKE"
        ]

        for word in keywords:
            pattern = QRegularExpression(f"\\b{word}\\b", QRegularExpression.CaseInsensitiveOption)
            self.highlighting_rules.append((pattern, keyword_format))

        # Data Types
        datatype_format = QTextCharFormat()
        datatype_format.setForeground(QColor("#4ec9b0"))  # Bright cyan
        datatype_format.setFontWeight(QFont.Bold)

        datatypes = [
            "INT", "INTEGER", "VARCHAR", "CHAR", "TEXT", "DATE",
            "DATETIME", "TIMESTAMP", "FLOAT", "DOUBLE", "DECIMAL",
            "BOOLEAN", "BOOL", "BIGINT", "SMALLINT", "TINYINT",
            "BLOB", "JSON", "ENUM", "TIME", "YEAR"
        ]

        for dtype in datatypes:
            pattern = QRegularExpression(f"\\b{dtype}\\b", QRegularExpression.CaseInsensitiveOption)
            self.highlighting_rules.append((pattern, datatype_format))

        # String literals (single quotes)
        string_format = QTextCharFormat()
        string_format.setForeground(QColor("#ce9178"))  # Bright orange
        pattern = QRegularExpression("'([^'\\\\]|\\\\.)*'")
        self.highlighting_rules.append((pattern, string_format))

        # String literals (double quotes)
        pattern = QRegularExpression('"([^"\\\\]|\\\\.)*"')
        self.highlighting_rules.append((pattern, string_format))

        # Numbers
        number_format = QTextCharFormat()
        number_format.setForeground(QColor("#b5cea8"))  # Bright light green
        pattern = QRegularExpression("\\b[0-9]+\\.?[0-9]*\\b")
        self.highlighting_rules.append((pattern, number_format))

        # Comments (-- style)
        comment_format = QTextCharFormat()
        comment_format.setForeground(QColor("#6a9955"))  # Bright green
        comment_format.setFontItalic(True)
        pattern = QRegularExpression("--[^\n]*")
        self.highlighting_rules.append((pattern, comment_format))

        # Comments (/* */ style)
        self.multiline_comment_format = comment_format
        self.comment_start_pattern = QRegularExpression("/\\*")
        self.comment_end_pattern = QRegularExpression("\\*/")

    def highlightBlock(self, text):
        """Apply syntax highlighting to a block of text"""
        
        # Apply single-line rules
        for pattern, format in self.highlighting_rules:
            iterator = pattern.globalMatch(text)
            while iterator.hasNext():
                match = iterator.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), format)

        # Handle multi-line comments
        self.setCurrentBlockState(0)
        start_index = 0
        
        if self.previousBlockState() != 1:
            match = self.comment_start_pattern.match(text)
            start_index = match.capturedStart() if match.hasMatch() else -1

        while start_index >= 0:
            match = self.comment_end_pattern.match(text, start_index)
            end_index = match.capturedStart() if match.hasMatch() else -1
            
            if end_index == -1:
                self.setCurrentBlockState(1)
                comment_length = len(text) - start_index
            else:
                comment_length = end_index - start_index + match.capturedLength()
            
            self.setFormat(start_index, comment_length, self.multiline_comment_format)
            
            match = self.comment_start_pattern.match(text, start_index + comment_length)
            start_index = match.capturedStart() if match.hasMatch() else -1
