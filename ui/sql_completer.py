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

# ─── Vocabulary ───────────────────────────────────────────────────────────────

SQL_KEYWORDS = [
    "SELECT", "FROM", "WHERE", "INSERT", "INTO", "UPDATE", "DELETE",
    "CREATE", "ALTER", "DROP", "TABLE", "DATABASE", "INDEX", "VIEW",
    "PROCEDURE", "FUNCTION", "TRIGGER",
    "JOIN", "INNER JOIN", "LEFT JOIN", "RIGHT JOIN",
    "OUTER JOIN", "FULL JOIN", "CROSS JOIN",
    "ON", "USING", "ORDER BY", "GROUP BY", "HAVING",
    "LIMIT", "OFFSET", "DISTINCT", "AS",
    "AND", "OR", "NOT", "IN", "LIKE", "ILIKE", "BETWEEN",
    "IS", "IS NOT", "NULL", "IS NULL", "IS NOT NULL",
    "ASC", "DESC", "VALUES", "SET",
    "UNION", "UNION ALL", "EXCEPT", "INTERSECT",
    "EXISTS", "ANY", "ALL", "SOME",
    "PRIMARY KEY", "FOREIGN KEY", "REFERENCES",
    "DEFAULT", "CHECK", "UNIQUE", "AUTO_INCREMENT",
    "WITH", "RECURSIVE",
    "CASE", "WHEN", "THEN", "ELSE", "END",
    "CAST", "CONVERT",
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
    ("ORDER BY",   "AFTER_ORDER_BY"),
    ("GROUP BY",   "AFTER_GROUP_BY"),
    ("LEFT JOIN",  "AFTER_JOIN"),
    ("RIGHT JOIN", "AFTER_JOIN"),
    ("INNER JOIN", "AFTER_JOIN"),
    ("FULL JOIN",  "AFTER_JOIN"),
    ("CROSS JOIN", "AFTER_JOIN"),
    ("JOIN",       "AFTER_JOIN"),
    ("INSERT INTO","AFTER_FROM"),
    ("FROM",       "AFTER_FROM"),
    ("UPDATE",     "AFTER_FROM"),
    ("HAVING",     "AFTER_WHERE"),
    ("WHERE",      "AFTER_WHERE"),
    ("ON",         "AFTER_ON"),
    ("SET",        "AFTER_SET"),
    ("SELECT",     "AFTER_SELECT"),
]


# ─── Suggestion item ──────────────────────────────────────────────────────────

class SuggestionItem:
    __slots__ = ("text", "kind", "score", "extra")

    TABLE   = "TABLE"
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
        SuggestionItem.COLUMN:  (QColor("#1e3a4a"), QColor("#9cdcfe")),
        SuggestionItem.KEYWORD: (QColor("#3a1e4a"), QColor("#c586c0")),
        SuggestionItem.FUNC:    (QColor("#4a3a1e"), QColor("#dcdcaa")),
        SuggestionItem.SNIPPET: (QColor("#1a3a1a"), QColor("#89d185")),
    }

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

        text = index.data(Qt.UserRole + 1) or ""
        kind = index.data(Qt.UserRole + 2) or SuggestionItem.KEYWORD

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

        badge_text  = kind
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

        # ── Text (left side, prefix highlighted) ─────────────────────────
        text_area = QRect(
            rect.left() + self.LEFT_PAD,
            rect.top(),
            badge_x - rect.left() - self.LEFT_PAD - 6,
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
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
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
        self._popup   = SqlCompletePopup(editor.window())
        self._popup.item_activated.connect(self._insert)

        self._tables:   list[str]            = []
        self._columns:  dict[str, list[str]] = {}   # {table: [col, ...]}
        self._aliases:  dict[str, str]       = {}   # {alias_lower: table}
        self._snippets: dict[str, dict]      = {}   # {trigger: {name, body, ...}}

    # ── Public ──────────────────────────────────────────────────

    def set_snippets(self, snippets: dict[str, dict]):
        """Replace the snippet cache (called after SnippetManager is updated)."""
        self._snippets = dict(snippets)

    def set_schema(self, tables: list[str], columns_dict: dict):
        """Refresh schema cache (called on connect / db switch)."""
        self._tables = list(tables or [])
        self._columns = {}
        for table, cols in (columns_dict or {}).items():
            if not isinstance(cols, list) or not cols:
                continue
            first = cols[0]
            if isinstance(first, dict):
                self._columns[table] = [c.get("Field", "") for c in cols if c.get("Field")]
            else:
                self._columns[table] = [c for c in cols if c]

    @property
    def popup_visible(self) -> bool:
        return self._popup.visible

    def hide_popup(self):
        self._popup.hide()

    def handle_key(self, event) -> bool:
        """
        Route a key event to the popup.
        Returns True if the event was consumed (caller must not pass it to editor).
        """
        if not self._popup.visible:
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
            self._popup.hide()
            return

        # Refresh alias map from current query text
        self._aliases = self._extract_aliases(query)

        context = self._parse_context(query, pos)
        items   = self._build_suggestions(prefix, context, query)

        if not items:
            self._popup.hide()
            return

        # Auto-hide when the single remaining suggestion is an exact match
        if len(items) == 1 and items[0].text.lower() == prefix.lower():
            self._popup.hide()
            return

        cur_rect   = editor.cursorRect()
        global_pos = editor.mapToGlobal(cur_rect.bottomLeft())
        global_pos.setX(global_pos.x() - 2)
        self._popup.show_suggestions(items, prefix, global_pos)

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
            # Make sure the keyword is followed by a word boundary (space/end)
            end = p + len(kw)
            if p > best_pos and (end >= len(before) or not before[end].isalpha()):
                best_pos = p
                best_ctx = ctx
        return best_ctx

    def _extract_aliases(self, query: str) -> dict[str, str]:
        """Build {alias_lower: real_table_name} from the query."""
        aliases: dict[str, str] = {}
        for m in re.finditer(
                r'(?:FROM|JOIN|UPDATE)\s+[`"]?(\w+)[`"]?'
                r'(?:\s+(?:AS\s+)?[`"]?(\w+)[`"]?)?',
                query, re.IGNORECASE):
            table = m.group(1)
            alias = m.group(2)
            if table in self._tables:
                aliases[table.lower()] = table
                if alias and alias.upper() not in SQL_KEYWORDS:
                    aliases[alias.lower()] = table
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
            if tbl in self._tables and tbl not in seen:
                seen.add(tbl)
                found.append(tbl)
        # Also include tables behind known aliases
        for alias_tbl in self._aliases.values():
            if alias_tbl not in seen:
                seen.add(alias_tbl)
                found.append(alias_tbl)
        return found

    def _columns_for_tables(self, tables: list[str]) -> list[str]:
        """Return deduplicated column names for the given tables."""
        seen: set[str]  = set()
        cols: list[str] = []
        for tbl in tables:
            for col in self._columns.get(tbl, []):
                if col and col not in seen:
                    seen.add(col)
                    cols.append(col)
        return cols

    # ── Suggestion building ───────────────────────────────────────────────────

    def _build_suggestions(self, prefix: str, context: str,
                           query: str) -> list[SuggestionItem]:

        # Dot notation (alias.col or table.col)
        if '.' in prefix:
            obj, partial = prefix.rsplit('.', 1)
            return self._dot_suggestions(obj, partial)

        pl = prefix.lower()

        # Columns only for tables already written in the query
        query_tables  = self._tables_in_query(query)
        query_columns = self._columns_for_tables(query_tables)

        results: list[SuggestionItem] = []

        if context in ("AFTER_FROM", "AFTER_JOIN"):
            # Tables first, then all keywords (so WHERE/ON/LIMIT/JOIN always reachable)
            results += self._score(pl, self._tables,   SuggestionItem.TABLE,   1000, fuzzy=True)
            results += self._score(pl, SQL_KEYWORDS,   SuggestionItem.KEYWORD,  600)

        elif context == "AFTER_SELECT":
            # Columns from query tables first, then functions, then all keywords
            results += self._score(pl, query_columns,  SuggestionItem.COLUMN,  1000)
            results += self._score(pl, SQL_FUNCTIONS,  SuggestionItem.FUNC,     850)
            results += self._score(pl, self._tables,   SuggestionItem.TABLE,    800, fuzzy=True)
            results += self._score(pl, SQL_KEYWORDS,   SuggestionItem.KEYWORD,  750)

        elif context in ("AFTER_WHERE", "AFTER_ON"):
            results += self._score(pl, query_columns, SuggestionItem.COLUMN,  1000)
            results += self._alias_col_items(pl)
            results += self._score(pl,
                ["AND", "OR", "NOT", "IN", "LIKE", "ILIKE", "BETWEEN",
                 "IS NULL", "IS NOT NULL", "EXISTS", "NULL",
                 "ORDER BY", "GROUP BY", "HAVING", "LIMIT"],
                SuggestionItem.KEYWORD, 900)
            results += self._score(pl, SQL_KEYWORDS, SuggestionItem.KEYWORD, 650)

        elif context == "AFTER_ORDER_BY":
            results += self._score(pl, query_columns,         SuggestionItem.COLUMN,  1000)
            results += self._score(pl, ["ASC", "DESC"],       SuggestionItem.KEYWORD,  950)
            results += self._score(pl, SQL_KEYWORDS,          SuggestionItem.KEYWORD,  650)

        elif context == "AFTER_GROUP_BY":
            results += self._score(pl, query_columns, SuggestionItem.COLUMN,  1000)
            results += self._score(pl,
                ["HAVING", "ORDER BY", "LIMIT"],
                SuggestionItem.KEYWORD, 900)
            results += self._score(pl, SQL_KEYWORDS, SuggestionItem.KEYWORD, 650)

        elif context == "AFTER_SET":
            results += self._score(pl, query_columns, SuggestionItem.COLUMN,  1000)
            results += self._score(pl, SQL_KEYWORDS,  SuggestionItem.KEYWORD,  650)

        else:  # GENERAL — start of query or unknown position
            # Keywords first — user is most likely typing SELECT/INSERT/UPDATE/…
            results += self._score(pl, SQL_KEYWORDS,  SuggestionItem.KEYWORD, 1000)
            results += self._score(pl, self._tables,  SuggestionItem.TABLE,    900, fuzzy=True)
            results += self._score(pl, SQL_FUNCTIONS, SuggestionItem.FUNC,     800)
            # Only show columns if the query already references some tables
            if query_tables:
                results += self._score(pl, query_columns, SuggestionItem.COLUMN, 850)

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
        if not table and obj in self._tables:
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
            out.append(SuggestionItem(f"{obj}.{col}", SuggestionItem.COLUMN, score))

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
                    f"{alias}.{col}", SuggestionItem.COLUMN, 870))
        return out

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
