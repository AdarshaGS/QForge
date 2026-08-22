"""
sql_completer.py — Professional context-aware SQL autocomplete
══════════════════════════════════════════════════════════════

Architecture:
  • SqlCompleter      — owns schema cache, context/ranking logic, popup
  • SqlCompletePopup  — frameless floating QFrame that never steals focus
  • SuggestionDelegate— custom painter: prefix-highlight + type badge
"""
from __future__ import annotations

import re
from time import perf_counter
from typing import Optional

from PySide6.QtCore import Qt, Signal, QPoint, QSize, QRect, QTimer
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QListWidget, QListWidgetItem,
    QStyledItemDelegate, QStyleOptionViewItem, QAbstractItemView,
    QApplication, QStyle,
)
from PySide6.QtGui import (
    QPainter, QColor, QFont, QFontMetrics, QTextCursor,
)

from utils import perf_metrics

# ─── Vocabulary ───────────────────────────────────────────────────────────────

SQL_KEYWORDS = [
    # ── Query starters ──────────────────────────────────────────────────────
    "SELECT", "INSERT", "UPDATE", "DELETE", "REPLACE",
    "CREATE", "ALTER", "DROP", "TRUNCATE", "RENAME",
    "EXPLAIN", "EXPLAIN ANALYZE", "EXPLAIN FORMAT=JSON",
    "EXPLAIN FORMAT=TREE", "EXPLAIN FORMAT=TRADITIONAL",
    "DESCRIBE", "DESC", "SHOW",
    "SHOW TABLES", "SHOW DATABASES", "SHOW COLUMNS FROM",
    "SHOW CREATE TABLE", "SHOW INDEX FROM", "SHOW PROCESSLIST",
    "SHOW VARIABLES", "SHOW STATUS",
    "USE", "CALL",
    "WITH", "WITH RECURSIVE",
    # ── Clauses ──────────────────────────────────────────────────────────────
    "FROM", "WHERE", "HAVING", "ON", "USING",
    "ORDER BY", "GROUP BY", "PARTITION BY",
    "LIMIT", "OFFSET", "FETCH FIRST",
    "DISTINCT", "ALL",
    "AS",
    # ── Joins ────────────────────────────────────────────────────────────────
    "JOIN", "INNER JOIN", "LEFT JOIN", "RIGHT JOIN",
    "LEFT OUTER JOIN", "RIGHT OUTER JOIN",
    "FULL JOIN", "FULL OUTER JOIN", "CROSS JOIN", "STRAIGHT_JOIN",
    "NATURAL JOIN", "NATURAL LEFT JOIN", "NATURAL RIGHT JOIN",
    # ── Logical / comparison ─────────────────────────────────────────────────
    "AND", "OR", "NOT", "XOR",
    "IN", "NOT IN", "LIKE", "NOT LIKE", "ILIKE",
    "BETWEEN", "NOT BETWEEN",
    "IS NULL", "IS NOT NULL", "IS TRUE", "IS FALSE",
    "IS", "IS NOT",
    "EXISTS", "NOT EXISTS",
    "ANY", "ALL", "SOME",
    "REGEXP", "NOT REGEXP", "RLIKE",
    # ── Set ops ──────────────────────────────────────────────────────────────
    "UNION", "UNION ALL", "INTERSECT", "EXCEPT", "MINUS",
    # ── DML extras ──────────────────────────────────────────────────────────
    "INTO", "VALUES", "SET", "DEFAULT",
    "ON DUPLICATE KEY UPDATE",
    "INSERT IGNORE", "INSERT INTO",
    # ── DDL ──────────────────────────────────────────────────────────────────
    "TABLE", "DATABASE", "SCHEMA", "INDEX", "VIEW",
    "PROCEDURE", "FUNCTION", "TRIGGER", "EVENT",
    "PRIMARY KEY", "FOREIGN KEY", "REFERENCES",
    "UNIQUE", "CHECK", "AUTO_INCREMENT", "NOT NULL", "NULL",
    "DEFAULT", "COMMENT", "CHARSET", "COLLATE",
    "IF EXISTS", "IF NOT EXISTS",
    "ADD COLUMN", "DROP COLUMN", "MODIFY COLUMN", "CHANGE COLUMN",
    "ADD INDEX", "DROP INDEX",
    # ── Control flow ─────────────────────────────────────────────────────────
    "CASE", "WHEN", "THEN", "ELSE", "END",
    "IF", "IFNULL", "NULLIF", "COALESCE",
    # ── Sort / window ────────────────────────────────────────────────────────
    "ASC", "DESC", "NULLS FIRST", "NULLS LAST",
    "OVER", "ROWS BETWEEN", "RANGE BETWEEN",
    "UNBOUNDED PRECEDING", "CURRENT ROW", "UNBOUNDED FOLLOWING",
    # ── Transaction ──────────────────────────────────────────────────────────
    "BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE SAVEPOINT",
    "START TRANSACTION", "SET AUTOCOMMIT",
    "LOCK TABLES", "UNLOCK TABLES",
    # ── Misc ─────────────────────────────────────────────────────────────────
    "CAST", "CONVERT", "INTERVAL",
    "FORCE INDEX", "USE INDEX", "IGNORE INDEX",
    "SQL_CALC_FOUND_ROWS", "FOUND_ROWS",
    "ROW_COUNT", "LAST_INSERT_ID",
]

SQL_FUNCTIONS = [
    "COUNT", "SUM", "AVG", "MAX", "MIN",
    "ROUND", "CEIL", "FLOOR", "ABS", "MOD",
    "CONCAT", "CONCAT_WS", "SUBSTRING", "SUBSTR", "LEFT", "RIGHT",
    "UPPER", "LOWER", "TRIM", "LTRIM", "RTRIM", "LENGTH", "CHAR_LENGTH",
    "REPLACE", "REGEXP", "REGEXP_REPLACE",
    "COALESCE", "NULLIF", "IFNULL", "NVL", "IF",
    "NOW", "CURDATE", "CURTIME", "SYSDATE",
    "DATE", "TIME", "TIMESTAMP",
    "YEAR", "MONTH", "DAY", "HOUR", "MINUTE", "SECOND",
    "DATE_FORMAT", "STR_TO_DATE", "TO_DATE", "TO_CHAR",
    "DATEDIFF", "DATE_ADD", "DATE_SUB", "DATEADD",
    "GROUP_CONCAT", "STRING_AGG", "ARRAY_AGG",
    "JSON_EXTRACT", "JSON_OBJECT", "JSON_ARRAY", "JSON_VALUE",
    "ROW_NUMBER", "RANK", "DENSE_RANK", "NTILE",
    "LAG", "LEAD", "FIRST_VALUE", "LAST_VALUE",
    "OVER", "PARTITION BY",
]

# (keyword, context_name) — checked from most-specific to least
_CONTEXT_MARKERS = [
    ("EXPLAIN ANALYZE", "AFTER_EXPLAIN"),
    ("EXPLAIN FORMAT",  "AFTER_EXPLAIN"),
    ("EXPLAIN",         "AFTER_EXPLAIN"),
    ("ORDER BY",        "AFTER_ORDER_BY"),
    ("GROUP BY",        "AFTER_GROUP_BY"),
    ("HAVING",          "AFTER_HAVING"),
    ("LEFT JOIN",       "AFTER_JOIN"),
    ("RIGHT JOIN",      "AFTER_JOIN"),
    ("INNER JOIN",      "AFTER_JOIN"),
    ("FULL JOIN",       "AFTER_JOIN"),
    ("CROSS JOIN",      "AFTER_JOIN"),
    ("STRAIGHT_JOIN",   "AFTER_JOIN"),
    ("JOIN",            "AFTER_JOIN"),
    ("INSERT INTO",     "AFTER_INSERT"),
    ("INSERT IGNORE",   "AFTER_INSERT"),
    ("INSERT",          "AFTER_INSERT"),
    ("FROM",            "AFTER_FROM"),
    ("UPDATE",          "AFTER_FROM"),
    ("WHERE",           "AFTER_WHERE"),
    ("ON",              "AFTER_ON"),
    ("SET",             "AFTER_SET"),
    ("SELECT",          "AFTER_SELECT"),
    ("WITH",            "AFTER_WITH"),
    ("LIMIT",           "AFTER_LIMIT"),
]


# ─── Suggestion item ──────────────────────────────────────────────────────────

class SuggestionItem:
    __slots__ = ("text", "kind", "score", "extra")

    TABLE   = "TABLE"
    VIEW    = "VIEW"
    COLUMN  = "COLUMN"
    KEYWORD = "KEYWORD"
    FUNC    = "FUNC"
    SNIPPET = "SNIPPET"   # expandable snippet

    def __init__(self, text: str, kind: str, score: int = 0,
                 extra: dict | None = None):
        self.text  = text
        self.kind  = kind
        self.score = score
        self.extra = extra or {}   # for SNIPPET: {"body": str, "name": str}


# ─── Custom item delegate ─────────────────────────────────────────────────────

class SuggestionDelegate(QStyledItemDelegate):
    """Paints each row: bold-highlighted prefix on the left, type badge on the right."""

    ROW_H   = 26
    BADGE_W_PAD = 7    # horizontal padding inside badge
    BADGE_H = 16
    LEFT_PAD = 10

    # badge colours (bg, fg)
    _BADGE = {
        SuggestionItem.TABLE:   (QColor("#1e4a3a"), QColor("#4ec9b0")),
        SuggestionItem.VIEW:    (QColor("#1e3a4a"), QColor("#6ab7ff")),
        SuggestionItem.COLUMN:  (QColor("#1e3a4a"), QColor("#9cdcfe")),
        SuggestionItem.KEYWORD: (QColor("#3a1e4a"), QColor("#c586c0")),
        SuggestionItem.FUNC:    (QColor("#4a3a1e"), QColor("#dcdcaa")),
        SuggestionItem.SNIPPET: (QColor("#1a3a1a"), QColor("#89d185")),
    }

    # PK/FK key-glyph colours, drawn immediately left of the badge.
    _KEY_GLYPH = {"PRI": ("\U0001F511", QColor("#e5c07b")),   # 🔑 primary key
                  "FK":  ("→",      QColor("#7aa2f7"))}  # → foreign key

    def __init__(self, parent=None):
        super().__init__(parent)
        self._prefix = ""
        self._base_font: Optional[QFont] = None

    def set_prefix(self, prefix: str):
        self._prefix = prefix.lower()

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), self.ROW_H)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index):
        painter.save()

        text  = index.data(Qt.UserRole + 1) or ""
        kind  = index.data(Qt.UserRole + 2) or SuggestionItem.KEYWORD
        extra = index.data(Qt.UserRole + 5) or {}

        # Background
        from PySide6.QtWidgets import QStyle
        is_selected = bool(option.state & QStyle.State_Selected)
        bg = QColor("#094771") if is_selected else QColor("#1e1e1e")
        painter.fillRect(option.rect, bg)

        rect = option.rect

        # ── Badge (right side) ────────────────────────────────────────────
        badge_font = QFont(option.font)
        badge_font.setPointSize(10)
        badge_font.setBold(False)
        painter.setFont(badge_font)
        bfm = QFontMetrics(badge_font)

        badge_text  = extra.get("badge") or kind
        badge_text_w = bfm.horizontalAdvance(badge_text)
        badge_total_w = badge_text_w + self.BADGE_W_PAD * 2
        badge_x = rect.right() - badge_total_w - 6
        badge_y = rect.top() + (rect.height() - self.BADGE_H) // 2
        badge_rect = QRect(badge_x, badge_y, badge_total_w, self.BADGE_H)

        bg_c, fg_c = self._BADGE.get(kind, (QColor("#333"), QColor("#ccc")))
        painter.setPen(Qt.NoPen)
        painter.setBrush(bg_c)
        painter.drawRoundedRect(badge_rect, 3, 3)
        painter.setPen(fg_c)
        painter.drawText(badge_rect, Qt.AlignCenter, badge_text)

        # ── Key glyph (PK/FK columns) — drawn just left of the badge ─────
        key_right = badge_x
        key = extra.get("key")
        glyph_spec = self._KEY_GLYPH.get(key)
        if glyph_spec:
            glyph, glyph_c = glyph_spec
            glyph_w = bfm.horizontalAdvance(glyph) + 4
            glyph_rect = QRect(badge_x - glyph_w, badge_y, glyph_w, self.BADGE_H)
            painter.setPen(glyph_c)
            painter.drawText(glyph_rect, Qt.AlignCenter, glyph)
            key_right = glyph_rect.left()

        # ── Text (left side, prefix highlighted) ─────────────────────────
        text_area = QRect(
            rect.left() + self.LEFT_PAD,
            rect.top(),
            key_right - rect.left() - self.LEFT_PAD - 6,
            rect.height(),
        )
        base_font = QFont(option.font)
        base_font.setPointSize(13)

        prefix = self._prefix
        text_lower = text.lower()

        if prefix and text_lower.startswith(prefix):
            matched = text[:len(prefix)]
            rest    = text[len(prefix):]

            # Bold matched part
            bold_font = QFont(base_font)
            bold_font.setBold(True)
            painter.setFont(bold_font)
            bfm2 = QFontMetrics(bold_font)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(text_area, Qt.AlignVCenter | Qt.AlignLeft, matched)
            matched_px = bfm2.horizontalAdvance(matched)

            # Normal dim rest
            painter.setFont(base_font)
            painter.setPen(QColor("#aaaaaa"))
            rest_area = text_area.adjusted(matched_px, 0, 0, 0)
            painter.drawText(rest_area, Qt.AlignVCenter | Qt.AlignLeft, rest)
        else:
            painter.setFont(base_font)
            painter.setPen(QColor("#cccccc"))
            painter.drawText(text_area, Qt.AlignVCenter | Qt.AlignLeft, text)

        # For snippets: show name in dim colour after the trigger
        if kind == SuggestionItem.SNIPPET:
            snip_name = index.data(Qt.UserRole + 4) or ""
            if snip_name:
                fm = QFontMetrics(base_font)
                trigger_px = fm.horizontalAdvance(text)
                sep_area = text_area.adjusted(trigger_px + 12, 0, 0, 0)
                painter.setFont(base_font)
                painter.setPen(QColor("#505050"))
                painter.drawText(sep_area, Qt.AlignVCenter | Qt.AlignLeft,
                                 "—  " + snip_name)

        painter.restore()


# ─── Floating popup ───────────────────────────────────────────────────────────

class SqlCompletePopup(QFrame):
    """
    Frameless floating window shown below the cursor.
    Never steals keyboard focus from the editor.
    """

    item_activated = Signal(str)   # emits plain text of accepted item

    MAX_ROWS = 12

    def __init__(self, parent=None):
        # WindowDoesNotAcceptFocus (on top of the existing NoFocus policy /
        # WA_ShowWithoutActivating below) tells the OS window server itself
        # that this window can never become key/active — on macOS, that's
        # what actually determines whether showing a new window can trigger
        # a full-screen-Space switch. WA_ShowWithoutActivating alone stops
        # Qt from *requesting* activation, but apparently wasn't enough to
        # stop macOS from still treating this as an activatable window and
        # sliding to a new Space to reveal it while typing in a full-screen
        # SQL tab (GitHub issue #15, follow-up: still occurred every time
        # autocomplete showed, not just when opening a new tab).
        super().__init__(
            parent,
            Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedWidth(420)

        self.setStyleSheet("""
            QFrame {
                background-color: #1e1e1e;
                border: 1px solid #454545;
                border-radius: 4px;
            }
            QListWidget {
                background-color: #1e1e1e;
                border: none;
                outline: none;
                color: #cccccc;
            }
            QScrollBar:vertical {
                background: #1e1e1e;
                width: 6px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical {
                background: #555;
                border-radius: 3px;
                min-height: 20px;
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical { height: 0; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(0)

        self.list_widget = QListWidget()
        self.list_widget.setFocusPolicy(Qt.NoFocus)
        self.list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list_widget.setUniformItemSizes(True)
        self.list_widget.itemClicked.connect(self._on_click)

        self.delegate = SuggestionDelegate(self.list_widget)
        self.list_widget.setItemDelegate(self.delegate)
        layout.addWidget(self.list_widget)

    # ── Public ────────────────────────────────────────────────────────────────

    def show_suggestions(self, items: list[SuggestionItem], prefix: str,
                         global_pos: QPoint):
        self.delegate.set_prefix(prefix)
        self.list_widget.clear()

        for item in items:
            lw = QListWidgetItem()
            lw.setData(Qt.UserRole + 1, item.text)           # display text / trigger
            lw.setData(Qt.UserRole + 2, item.kind)           # badge kind
            lw.setData(Qt.UserRole + 3, item.extra.get("body", ""))  # snippet body
            lw.setData(Qt.UserRole + 4, item.extra.get("name", ""))  # snippet name
            lw.setData(Qt.UserRole + 5, item.extra)                  # raw extra (badge/key)
            lw.setText(item.text)
            self.list_widget.addItem(lw)

        if not items:
            self.hide()
            return

        self.list_widget.setCurrentRow(0)

        # Resize to content
        row_h   = SuggestionDelegate.ROW_H
        visible = min(len(items), self.MAX_ROWS)
        self.list_widget.setFixedHeight(visible * row_h + 4)
        self.adjustSize()

        # Position: flip above cursor if too close to screen bottom
        screen = QApplication.primaryScreen().availableGeometry()
        pos = QPoint(global_pos)
        if pos.y() + self.height() > screen.bottom() - 20:
            pos.setY(pos.y() - self.height() - row_h)
        if pos.x() + self.width() > screen.right():
            pos.setX(screen.right() - self.width() - 4)

        self.move(pos)
        if not self.isVisible():
            self.show()
        self.update()

    def navigate(self, direction: int):
        n = self.list_widget.count()
        if n == 0:
            return
        row = self.list_widget.currentRow()
        self.list_widget.setCurrentRow(max(0, min(n - 1, row + direction)))

    def accept_current(self) -> bool:
        item = self.list_widget.currentItem()
        if item:
            # Snippets: emit body; others: emit display text
            body = item.data(Qt.UserRole + 3)
            text = item.data(Qt.UserRole + 1)
            self.item_activated.emit(body if body else text)
            self.hide()
            return True
        return False

    @property
    def visible(self) -> bool:
        return self.isVisible()

    # ── Private ───────────────────────────────────────────────────────────────

    def _on_click(self, item: QListWidgetItem):
        body = item.data(Qt.UserRole + 3)
        text = item.data(Qt.UserRole + 1)
        self.item_activated.emit(body if body else text)
        self.hide()


# ─── Main completer class ─────────────────────────────────────────────────────

class SqlCompleter:
    """
    Drives autocomplete for a QTextEdit.

    Design goals
    ─────────────
    • GENERAL context (start of query): KEYWORDS first, then tables — no columns
    • After FROM/JOIN: tables only
    • After SELECT/WHERE/ORDER BY: columns ONLY from tables already in the query
    • Exact prefix always ranked above starts-with, starts-with above contains
    • Dot notation (alias.col / table.col) → columns of that table
    • All SQL keywords (FROM, WHERE, ORDER BY …) always reachable everywhere
    """

    def __init__(self, editor):
        self._editor  = editor
        # Built lazily (see _ensure_popup) rather than here. At this point
        # `editor` has usually just been constructed and isn't attached to
        # the real window hierarchy yet (a new SqlTab isn't added to its
        # QTabWidget until after __init__ returns), so `editor.window()`
        # would resolve to the editor itself, not the actual main window.
        # Eagerly creating this Qt.Tool-flagged top-level popup against that
        # dangling ancestor — once per new SQL tab — was intermittently
        # kicking a fullscreen macOS window to a different Space the instant
        # a tab was opened, before the user ever touched autocomplete
        # (GitHub issue #15). Deferring construction to first real use means
        # it's always built once the tab is fully part of the real window.
        self._popup   = None

        self._tables:   list[str]            = []
        self._views:    list[str]            = []
        self._user_functions: list[str]      = []
        self._columns:  dict[str, list[str]] = {}   # {table: [col, ...]}
        self._column_meta: dict[str, dict[str, dict]] = {}   # {table: {col_lower: {type,nullable,default,key}}}
        self._foreign_keys: dict[str, list[dict]]     = {}   # {table: [{column, ref_table, ref_column}, ...]}
        self._fk_columns:   dict[str, set[str]]        = {}   # {table: {col_lower that is an FK source}}
        self._aliases:  dict[str, str]       = {}   # {alias_lower: table}
        self._snippets: dict[str, dict]      = {}   # {trigger: {name, body, ...}}

    # ── Public ──────────────────────────────────────────────────

    def set_snippets(self, snippets: dict[str, dict]):
        """Replace the snippet cache (called after SnippetManager is updated)."""
        self._snippets = dict(snippets)

    def set_schema(self, tables: list[str], columns_dict: dict, column_details: dict = None,
                   foreign_keys: dict = None, views: list[str] = None, functions: list[str] = None):
        """Refresh schema cache (called on connect / db switch).

        `column_details`/`foreign_keys` are optional bulk metadata from
        db_service.get_all_column_details()/get_all_foreign_keys() (same
        shape schema_snapshot.fetch_schema_snapshot() returns) — when
        omitted, type/PK/FK badges and FK-aware JOIN completion simply don't
        activate, everything else behaves as before.
        """
        self._tables = list(tables or [])
        self._views = list(views or [])
        self._user_functions = [f for f in (functions or []) if f]
        self._columns = {}
        for table, cols in (columns_dict or {}).items():
            if not isinstance(cols, list) or not cols:
                continue
            first = cols[0]
            if isinstance(first, dict):
                self._columns[table] = [c.get("Field", "") for c in cols if c.get("Field")]
            else:
                self._columns[table] = [c for c in cols if c]

        self._column_meta = {}
        for table, cols in (column_details or {}).items():
            meta: dict[str, dict] = {}
            for c in cols:
                name = c.get("name")
                if name:
                    meta[name.lower()] = c
            if meta:
                self._column_meta[table] = meta

        self._foreign_keys = dict(foreign_keys or {})
        self._fk_columns = {
            table: {fk["column"].lower() for fk in fks if fk.get("column")}
            for table, fks in self._foreign_keys.items()
        }

    # ── Public schema accessors (read-only views for callers like SqlTab's
    #    hover tooltip / inline-validation, so they don't reach into the
    #    scoring internals above) ──────────────────────────────────────────

    def known_tables(self) -> set[str]:
        """Every table and view name the active connection reported."""
        return set(self._tables) | set(self._views)

    def is_view(self, name: str) -> bool:
        return name in self._views

    def resolve_table(self, name_or_alias: str) -> Optional[str]:
        """A bare table/view name, or an alias already used in the current
        query (via update()'s _extract_aliases), to its real table name."""
        table = self._aliases.get(name_or_alias.lower())
        if table:
            return table
        return name_or_alias if name_or_alias in self._tables or name_or_alias in self._views else None

    def alias_target(self, alias: str) -> Optional[str]:
        return self._aliases.get(alias.lower())

    def table_columns(self, table: str) -> list[str]:
        return list(self._columns.get(table, []))

    def column_meta(self, table: str, column: str) -> Optional[dict]:
        return self._column_meta.get(table, {}).get(column.lower())

    def foreign_key_for(self, table: str, column: str) -> Optional[dict]:
        return next((fk for fk in self._foreign_keys.get(table, [])
                     if fk.get("column", "").lower() == column.lower()), None)

    def _ensure_popup(self) -> SqlCompletePopup:
        """Construct the popup on first real use — see __init__ for why."""
        if self._popup is None:
            self._popup = SqlCompletePopup(self._editor.window())
            self._popup.item_activated.connect(self._insert)
        return self._popup

    @property
    def popup_visible(self) -> bool:
        return self._popup.visible if self._popup else False

    def hide_popup(self):
        if self._popup:
            self._popup.hide()

    def handle_key(self, event) -> bool:
        """
        Route a key event to the popup.
        Returns True if the event was consumed (caller must not pass it to editor).
        """
        if not self._popup or not self._popup.visible:
            return False
        key = event.key()
        if key == Qt.Key_Down:
            self._popup.navigate(+1)
            return True
        if key == Qt.Key_Up:
            self._popup.navigate(-1)
            return True
        if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Tab):
            return self._popup.accept_current()
        if key == Qt.Key_Escape:
            self._popup.hide()
            return True
        return False

    def update(self, force: bool = False):
        """
        Recompute and display suggestions for the current editor state.
        force=True → show even with empty prefix (Ctrl+Space).
        """
        editor = self._editor
        cursor = editor.textCursor()
        query  = editor.toPlainText()
        pos    = cursor.position()

        prefix = self._current_prefix(query, pos)

        if not prefix and not force:
            self.hide_popup()
            return

        # Refresh alias map from current query text
        self._aliases = self._extract_aliases(query)

        context = self._parse_context(query, pos)
        _t0 = perf_counter()
        items   = self._build_suggestions(prefix, context, query, pos)
        perf_metrics.record("sql_editor", "autocomplete_latency", (perf_counter() - _t0) * 1000)

        if not items:
            self.hide_popup()
            return

        # Auto-hide when the single remaining suggestion is an exact match
        if len(items) == 1 and items[0].text.lower() == prefix.lower():
            self.hide_popup()
            return

        cur_rect   = editor.cursorRect()
        global_pos = editor.mapToGlobal(cur_rect.bottomLeft())
        global_pos.setX(global_pos.x() - 2)
        self._ensure_popup().show_suggestions(items, prefix, global_pos)

    # ── Prefix extraction ─────────────────────────────────────────────────────

    @staticmethod
    def _current_prefix(text: str, pos: int) -> str:
        """Return the token (incl. alias.col dot notation) ending at pos."""
        i = pos
        while i > 0 and (text[i - 1].isalnum() or text[i - 1] in ('_', '.')):
            i -= 1
        return text[i:pos]

    # ── Context detection ─────────────────────────────────────────────────────

    @staticmethod
    def _parse_context(query: str, cursor_pos: int) -> str:
        # Strip the word being typed so we don't confuse it for a keyword
        i = cursor_pos
        while i > 0 and (query[i - 1].isalnum() or query[i - 1] in '_.'):
            i -= 1
        before = query[:i].upper().rstrip()

        best_pos = -1
        best_ctx = "GENERAL"
        for kw, ctx in _CONTEXT_MARKERS:
            p = before.rfind(kw)
            if p < 0:
                continue
            end = p + len(kw)
            # Keyword must be at end of `before` or followed by whitespace/non-alpha
            if end < len(before) and before[end].isalpha():
                continue
            # Must be preceded by start or non-alpha (word boundary)
            if p > 0 and before[p - 1].isalpha():
                continue
            if p > best_pos:
                best_pos = p
                best_ctx = ctx
        return best_ctx

    def _extract_aliases(self, query: str) -> dict[str, str]:
        """Build {alias_lower: real_table_name} from the query."""
        aliases: dict[str, str] = {}
        for m in re.finditer(
                r'(?:FROM|JOIN|UPDATE)\s+[`"]?(\w+)[`"]?',
                query, re.IGNORECASE):
            table = m.group(1)
            if table not in self._tables and table not in self._views:
                continue
            aliases[table.lower()] = table
            # Matched as a *separate* lookahead (re.match on the tail, not
            # part of the finditer pattern above) so an unaliased table
            # (e.g. bare "FROM t JOIN ...", no alias on t) never lets this
            # optional-alias check swallow the next JOIN keyword — which
            # would hide that second table from this same finditer scan.
            am = re.match(r'\s+(?:AS\s+)?[`"]?(\w+)[`"]?', query[m.end():], re.IGNORECASE)
            if am and am.group(1).upper() not in SQL_KEYWORDS:
                aliases[am.group(1).lower()] = table
        return aliases

    # ── Tables mentioned in the query ────────────────────────────────────────

    def _tables_in_query(self, query: str) -> list[str]:
        """Return table names actually referenced in FROM/JOIN/UPDATE/INTO."""
        found: list[str] = []
        seen:  set[str]  = set()
        for m in re.finditer(
                r'(?:FROM|JOIN|UPDATE|INTO)\s+[`"]?(\w+)[`"]?',
                query, re.IGNORECASE):
            tbl = m.group(1)
            if (tbl in self._tables or tbl in self._views) and tbl not in seen:
                seen.add(tbl)
                found.append(tbl)
        # Also include tables behind known aliases
        for alias_tbl in self._aliases.values():
            if alias_tbl not in seen:
                seen.add(alias_tbl)
                found.append(alias_tbl)
        return found

    def _column_badge_extra(self, table: str, col_lower: str) -> dict:
        """Type/PK/FK badge info for one column, from set_schema()'s optional
        column_details/foreign_keys metadata — empty dict when unavailable."""
        extra: dict = {}
        meta = self._column_meta.get(table, {}).get(col_lower)
        if meta and meta.get("type"):
            extra["badge"] = str(meta["type"]).split("(")[0].upper()[:12]
        if meta and meta.get("key") == "PRI":
            extra["key"] = "PRI"
        elif col_lower in self._fk_columns.get(table, ()):
            extra["key"] = "FK"
        return extra

    def _score_columns(self, prefix: str, tables: list[str], base: int) -> list[SuggestionItem]:
        """Like _score() but for real table columns — attaches the type/PK/FK
        badge info _score() has no per-item source for."""
        pl = prefix.lower()
        seen: set[str] = set()
        out: list[SuggestionItem] = []
        for tbl in tables:
            for col in self._columns.get(tbl, []):
                cl = col.lower()
                if cl in seen:
                    continue
                if not pl:
                    s = base
                elif cl == pl:
                    s = base + 200
                elif cl.startswith(pl):
                    s = base + max(0, 50 - len(col))
                elif pl in cl:
                    s = base - 300
                else:
                    continue
                seen.add(cl)
                out.append(SuggestionItem(col, SuggestionItem.COLUMN, s,
                                          extra=self._column_badge_extra(tbl, cl)))
        return out

    # ── Suggestion building ───────────────────────────────────────────────────

    def _build_suggestions(self, prefix: str, context: str,
                           query: str, pos: int = 0) -> list[SuggestionItem]:

        # Dot notation (alias.col or table.col)
        if '.' in prefix:
            obj, partial = prefix.rsplit('.', 1)
            return self._dot_suggestions(obj, partial)

        pl = prefix.lower()

        # Columns only for tables already written in the query
        query_tables  = self._tables_in_query(query)

        results: list[SuggestionItem] = []

        if context in ("AFTER_FROM", "AFTER_JOIN"):
            # Tables/views first, then all keywords (so WHERE/ON/LIMIT/JOIN always reachable)
            results += self._score(pl, self._tables,   SuggestionItem.TABLE,   1000, fuzzy=True)
            results += self._score(pl, self._views,    SuggestionItem.VIEW,     990, fuzzy=True)
            results += self._score(pl, SQL_KEYWORDS,   SuggestionItem.KEYWORD,  600)
            if context == "AFTER_JOIN":
                results += self._fk_join_on_suggestion(pl, query, pos)

        elif context == "AFTER_SELECT":
            # Columns from query tables first, then functions, then tables (for subquery)
            results += self._score_columns(pl, query_tables, 1000)
            results += self._alias_col_items(pl)
            results += self._score(pl, SQL_FUNCTIONS,  SuggestionItem.FUNC,     850)
            results += self._score(pl, self._user_functions, SuggestionItem.FUNC, 855)
            results += self._score(pl, self._tables,   SuggestionItem.TABLE,    780, fuzzy=True)
            results += self._score(pl, self._views,    SuggestionItem.VIEW,     770, fuzzy=True)
            results += self._score(pl, SQL_KEYWORDS,   SuggestionItem.KEYWORD,  700)

        elif context in ("AFTER_WHERE", "AFTER_ON", "AFTER_HAVING"):
            results += self._score_columns(pl, query_tables, 1000)
            results += self._alias_col_items(pl)
            results += self._score(pl, SQL_FUNCTIONS, SuggestionItem.FUNC,     900)
            results += self._score(pl, self._user_functions, SuggestionItem.FUNC, 905)
            results += self._score(pl,
                ["AND", "OR", "NOT", "IN", "NOT IN", "LIKE", "NOT LIKE",
                 "ILIKE", "BETWEEN", "NOT BETWEEN",
                 "IS NULL", "IS NOT NULL", "IS TRUE", "IS FALSE",
                 "EXISTS", "NOT EXISTS", "REGEXP", "RLIKE",
                 "ORDER BY", "GROUP BY", "HAVING", "LIMIT"],
                SuggestionItem.KEYWORD, 900)
            results += self._score(pl, SQL_KEYWORDS, SuggestionItem.KEYWORD, 650)

        elif context == "AFTER_ORDER_BY":
            results += self._score_columns(pl, query_tables, 1000)
            results += self._alias_col_items(pl)
            results += self._score(pl, ["ASC", "DESC", "NULLS FIRST", "NULLS LAST",
                                        "LIMIT"],
                                   SuggestionItem.KEYWORD, 950)
            results += self._score(pl, SQL_KEYWORDS,           SuggestionItem.KEYWORD,  650)

        elif context == "AFTER_GROUP_BY":
            results += self._score_columns(pl, query_tables, 1000)
            results += self._alias_col_items(pl)
            results += self._score(pl,
                ["HAVING", "ORDER BY", "LIMIT", "WITH ROLLUP"],
                SuggestionItem.KEYWORD, 900)
            results += self._score(pl, SQL_KEYWORDS, SuggestionItem.KEYWORD, 650)

        elif context == "AFTER_SET":
            results += self._score_columns(pl, query_tables, 1000)
            results += self._score(pl, SQL_FUNCTIONS, SuggestionItem.FUNC,     850)
            results += self._score(pl, self._user_functions, SuggestionItem.FUNC, 855)
            results += self._score(pl, SQL_KEYWORDS,  SuggestionItem.KEYWORD,  650)

        elif context == "AFTER_EXPLAIN":
            # After EXPLAIN, suggest SELECT / WITH / table names
            results += self._score(pl,
                ["SELECT", "WITH", "INSERT", "UPDATE", "DELETE",
                 "FORMAT=JSON", "FORMAT=TREE", "FORMAT=TRADITIONAL",
                 "ANALYZE"],
                SuggestionItem.KEYWORD, 1000)
            results += self._score(pl, self._tables, SuggestionItem.TABLE, 900, fuzzy=True)
            results += self._score(pl, self._views,  SuggestionItem.VIEW,  890, fuzzy=True)

        elif context == "AFTER_INSERT":
            # After INSERT INTO, suggest table names
            results += self._score(pl, self._tables,   SuggestionItem.TABLE,   1000, fuzzy=True)
            results += self._score(pl, SQL_KEYWORDS,   SuggestionItem.KEYWORD,  600)

        elif context == "AFTER_WITH":
            # CTE name being defined or SELECT after WITH block
            results += self._score(pl, ["SELECT", "RECURSIVE"],
                                   SuggestionItem.KEYWORD, 1000)
            results += self._score(pl, SQL_KEYWORDS, SuggestionItem.KEYWORD, 700)

        elif context == "AFTER_LIMIT":
            results += self._score(pl, ["OFFSET"], SuggestionItem.KEYWORD, 1000)
            results += self._score(pl, SQL_KEYWORDS, SuggestionItem.KEYWORD, 600)

        else:  # GENERAL — start of query or unknown position
            # Keywords first — user is most likely typing SELECT/EXPLAIN/INSERT/…
            results += self._score(pl, SQL_KEYWORDS,  SuggestionItem.KEYWORD, 1000)
            results += self._score(pl, self._tables,  SuggestionItem.TABLE,    900, fuzzy=True)
            results += self._score(pl, self._views,   SuggestionItem.VIEW,     890, fuzzy=True)
            results += self._score(pl, SQL_FUNCTIONS, SuggestionItem.FUNC,     800)
            results += self._score(pl, self._user_functions, SuggestionItem.FUNC, 805)
            # Only show columns if the query already references some tables
            if query_tables:
                results += self._score_columns(pl, query_tables, 850)

        # Snippets — shown in every context when the prefix matches a trigger
        results += self._score_snippets(pl)

        # De-duplicate (keep highest score per text)
        seen: dict[str, SuggestionItem] = {}
        for item in results:
            key = item.text.lower()
            if key not in seen or item.score > seen[key].score:
                seen[key] = item

        ranked = sorted(seen.values(), key=lambda x: (-x.score, x.text.lower()))
        return ranked[:60]

    # ── Dot-notation column suggestions ──────────────────────────────────────

    def _dot_suggestions(self, obj: str, partial: str) -> list[SuggestionItem]:
        table = self._aliases.get(obj.lower())
        if not table and (obj in self._tables or obj in self._views):
            table = obj
        if not table or table not in self._columns:
            return []

        pl  = partial.lower()
        out = []
        for col in self._columns[table]:
            cl = col.lower()
            if cl.startswith(pl):
                score = 1000 if cl == pl else (950 - len(col))
            elif pl and pl in cl:
                score = 750
            elif not pl:
                score = 900
            else:
                continue
            out.append(SuggestionItem(f"{obj}.{col}", SuggestionItem.COLUMN, score,
                                      extra=self._column_badge_extra(table, cl)))

        out.sort(key=lambda x: (-x.score, x.text.lower()))
        return out[:40]

    # ── Scoring helper ────────────────────────────────────────────────────────

    @staticmethod
    def _fuzzy_match(prefix: str, name: str) -> bool:
        """Characters of prefix appear in order inside name."""
        idx = 0
        for ch in name:
            if idx < len(prefix) and ch == prefix[idx]:
                idx += 1
        return idx == len(prefix)

    @staticmethod
    def _score(prefix: str, names: list[str],
               kind: str, base: int,
               fuzzy: bool = False) -> list[SuggestionItem]:
        """
        Score each name against prefix:
          exact match          → base + 200
          starts-with (short)  → base + (50 … 0)  shorter name = higher bonus
          contains             → base - 300        always below starts-with
          fuzzy (TABLE only)   → base - 450        characters appear in order
        """
        if not prefix:
            return [SuggestionItem(n, kind, base) for n in names]
        pl  = prefix.lower()
        out = []
        for name in names:
            nl = name.lower()
            if nl == pl:
                s = base + 200
            elif nl.startswith(pl):
                length_bonus = max(0, 50 - len(name))
                s = base + length_bonus
            elif pl in nl:
                s = base - 300
            elif fuzzy and SqlCompleter._fuzzy_match(pl, nl):
                s = base - 450
            else:
                continue
            out.append(SuggestionItem(name, kind, s))
        return out

    def _alias_col_items(self, prefix: str) -> list[SuggestionItem]:
        """Suggest alias.col format for all known aliases."""
        out = []
        for alias, table in self._aliases.items():
            if not alias.startswith(prefix):
                continue
            for col in self._columns.get(table, []):
                out.append(SuggestionItem(
                    f"{alias}.{col}", SuggestionItem.COLUMN, 870,
                    extra=self._column_badge_extra(table, col.lower())))
        return out

    def _fk_join_on_suggestion(self, prefix: str, query: str, pos: int) -> list[SuggestionItem]:
        """When the table/view right after the most recent JOIN is already
        fully typed, offer the whole ON clause built from a known FK
        relationship between it and an earlier table in the query — e.g.
        typing 'FROM users JOIN orders ' + 'o' suggests
        'ON users.id = orders.user_id'. Gated on the typed prefix actually
        being a candidate for "ON" so it doesn't appear while typing
        something unrelated."""
        if prefix and not "on".startswith(prefix):
            return []
        before = query[:pos]
        joined = None
        for m in re.finditer(r'\bJOIN\s+[`"]?(\w+)[`"]?\s', before, re.IGNORECASE):
            joined = m.group(1)
        if not joined or (joined not in self._tables and joined not in self._views):
            return []

        query_tables = self._tables_in_query(query)
        if joined not in query_tables:
            return []
        earlier = [t for t in query_tables if t != joined]
        if not earlier:
            return []
        prev = earlier[-1]

        fk = next((f for f in self._foreign_keys.get(joined, [])
                   if f.get("ref_table") == prev), None)
        if fk:
            left_col, right_col = fk["ref_column"], fk["column"]
        else:
            fk = next((f for f in self._foreign_keys.get(prev, [])
                       if f.get("ref_table") == joined), None)
            if not fk:
                return []
            left_col, right_col = fk["column"], fk["ref_column"]

        def display_name(table: str) -> str:
            for alias, tbl in self._aliases.items():
                if tbl == table and alias != table.lower():
                    return alias
            return table

        left, right = display_name(prev), display_name(joined)
        clause = f"ON {left}.{left_col} = {right}.{right_col}"
        return [SuggestionItem("ON", SuggestionItem.KEYWORD, 1300,
                               extra={"body": clause})]

    def _score_snippets(self, prefix: str) -> list[SuggestionItem]:
        """Return snippet SuggestionItems whose trigger matches prefix."""
        if not prefix or not self._snippets:
            return []
        pl  = prefix.lower()
        out = []
        for trigger, data in self._snippets.items():
            tl = trigger.lower()
            if tl == pl:
                score = 1100          # exact trigger — always first
            elif tl.startswith(pl):
                score = 850           # prefix match
            else:
                continue
            out.append(SuggestionItem(
                trigger, SuggestionItem.SNIPPET, score,
                extra={"body": data.get("body", ""),
                       "name": data.get("name", "")},
            ))
        return out

    # ── Insertion ─────────────────────────────────────────────────────────────

    def _insert(self, completion: str):
        editor = self._editor
        cursor = editor.textCursor()
        query  = editor.toPlainText()
        pos    = cursor.position()

        # Find start of current word (including dot)
        i = pos
        while i > 0 and (query[i - 1].isalnum() or query[i - 1] in '_.'):
            i -= 1

        # A multi-word completion (e.g. "SHOW PROCESSLIST", "ORDER BY") can
        # match on a substring of just its *last* word (_score's `pl in nl`
        # contains-check) even though the user already typed the leading
        # word(s) themselves — see issue #153. Walk backward past
        # already-typed, whitespace-separated words that case-insensitively
        # match the completion's own leading words, so the whole phrase
        # gets replaced instead of only the word being typed (which would
        # otherwise leave "SHOW " on the line and insert "SHOW PROCESSLIST"
        # right after it, duplicating "SHOW ").
        words = completion.split(' ')
        if len(words) > 1:
            word_start = i
            for w in reversed(words[:-1]):
                j = word_start
                while j > 0 and query[j - 1] == ' ':
                    j -= 1
                if j == word_start:  # no separating space — no earlier word here
                    break
                k = j
                while k > 0 and (query[k - 1].isalnum() or query[k - 1] in '_.'):
                    k -= 1
                if query[k:j].lower() != w.lower():
                    break
                word_start = k
            i = word_start

        cursor.setPosition(i)
        cursor.setPosition(pos, QTextCursor.KeepAnchor)

        _MARKER = "{cursor}"
        if _MARKER in completion:
            marker_offset = completion.index(_MARKER)
            body = completion.replace(_MARKER, "")
            cursor.insertText(body)
            # Move cursor to where {cursor} placeholder was
            cursor.setPosition(i + marker_offset)
        else:
            cursor.insertText(completion)

        editor.setTextCursor(cursor)
        self._popup.hide()
