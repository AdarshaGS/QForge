from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QLabel,
    QCheckBox,
    QStyle,
    QStyledItemDelegate,
)
from PySide6.QtCore import Qt, Signal, QRect, QSize
from PySide6.QtGui import QKeyEvent, QShortcut, QKeySequence, QColor, QFont, QFontMetrics


class QuickSearchItemDelegate(QStyledItemDelegate):
    """Paints each row's type as a colored badge (and a command's keyboard
    shortcut, when it has one) on the right, instead of the old "[Type]"
    text prefix (issue #245). Modeled on ui/sql_completer.py's
    SuggestionDelegate badge-drawing approach for a consistent visual
    language across the app's two popups — not the same class, since that
    delegate is tightly coupled to SuggestionItem's PK/FK glyph data, which
    doesn't apply here. A full custom paint() bypasses this dialog's QSS
    item styling, so backgrounds are hardcoded to match it, same as
    SuggestionDelegate already does for its own dialog."""

    ROW_H = 32
    BADGE_H = 18
    BADGE_W_PAD = 7
    LEFT_PAD = 10

    # (background, foreground) per item_type, reusing SuggestionDelegate's
    # palette where the type overlaps; history/command are new here.
    _BADGE = {
        "table": (QColor("#1e4a3a"), QColor("#4ec9b0")),
        "view": (QColor("#1e3a4a"), QColor("#6ab7ff")),
        "column": (QColor("#1e3a4a"), QColor("#9cdcfe")),
        "function": (QColor("#4a3a1e"), QColor("#dcdcaa")),
        "snippet": (QColor("#1a3a1a"), QColor("#89d185")),
        "history": (QColor("#2a2a3a"), QColor("#9aa5ce")),
        "command": (QColor("#3a1e4a"), QColor("#c586c0")),
    }
    _DEFAULT_BADGE = (QColor("#333333"), QColor("#cccccc"))
    # issue #246: badge colors for a contextually-disabled row, overriding
    # whatever its item_type's own badge color would otherwise be.
    _DISABLED_BADGE = (QColor("#3a3a3a"), QColor("#777777"))

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), self.ROW_H)

    def paint(self, painter, option, index):
        painter.save()

        entry = index.data(Qt.UserRole) or ("", "", "", 0, {})
        item_type, display_text, payload, source_idx, extra = entry
        # issue #246: a contextually-unavailable command stays in the list
        # (rather than being hidden, as before) but reads as unselectable —
        # greyed badge/text, plus its reason appended to the label.
        disabled = bool(extra.get("disabled"))

        rect = option.rect
        is_selected = bool(option.state & QStyle.State_Selected) and not disabled
        bg = QColor("#0066cc") if is_selected else QColor("#2b2b2b")
        painter.fillRect(rect, bg)

        # ── Badge (right) ──────────────────────────────────────────────
        badge_font = QFont(option.font)
        badge_font.setPointSize(10)
        badge_font.setBold(False)
        painter.setFont(badge_font)
        bfm = QFontMetrics(badge_font)

        badge_text = QuickSearchDialog.TYPE_LABELS.get(item_type, item_type.title())
        badge_total_w = bfm.horizontalAdvance(badge_text) + self.BADGE_W_PAD * 2
        badge_x = rect.right() - badge_total_w - 10
        badge_y = rect.top() + (rect.height() - self.BADGE_H) // 2
        badge_rect = QRect(badge_x, badge_y, badge_total_w, self.BADGE_H)

        bg_c, fg_c = self._DISABLED_BADGE if disabled else self._BADGE.get(item_type, self._DEFAULT_BADGE)
        painter.setPen(Qt.NoPen)
        painter.setBrush(bg_c)
        painter.drawRoundedRect(badge_rect, 3, 3)
        painter.setPen(fg_c)
        painter.drawText(badge_rect, Qt.AlignCenter, badge_text)

        # ── Shortcut, just left of the badge (Command Palette rows only) ──
        text_right = badge_x
        shortcut = extra.get("shortcut")
        if shortcut:
            shortcut_w = bfm.horizontalAdvance(shortcut) + 12
            shortcut_rect = QRect(badge_x - shortcut_w, badge_y, shortcut_w, self.BADGE_H)
            painter.setPen(QColor("#555555") if disabled else QColor("#999999"))
            painter.drawText(shortcut_rect, Qt.AlignCenter, shortcut)
            text_right = shortcut_rect.left()

        # ── Label text (left) — a disabled row appends its reason ────────
        text_font = QFont(option.font)
        text_font.setPointSize(13)
        painter.setFont(text_font)
        painter.setPen(QColor("#777777") if disabled else QColor("#ffffff"))
        text_rect = QRect(
            rect.left() + self.LEFT_PAD, rect.top(),
            text_right - rect.left() - self.LEFT_PAD - 8, rect.height(),
        )
        label = index.data(Qt.DisplayRole) or ""
        if disabled and extra.get("reason"):
            label = f"{label}  —  {extra['reason']}"
        fm = QFontMetrics(text_font)
        elided = fm.elidedText(label, Qt.ElideRight, text_rect.width())
        painter.drawText(text_rect, Qt.AlignVCenter, elided)

        painter.restore()


class QuickSearchDialog(QDialog):
    """Command palette: search tables, columns, views, functions/procedures,
    query history, and SQL snippets, then act on the selected item."""

    # item_type -> label shown before each result
    TYPE_LABELS = {
        "table": "Table",
        "view": "View",
        "function": "Function",
        "column": "Column",
        "history": "History",
        "snippet": "Snippet",
    }

    # (item_type, display_text, payload, source_idx) — source_idx is which
    # entry in `sources` this item came from (issue #243: cross-connection
    # search). Existing single-source callers pass plain 3-tuples, which
    # _normalize() below fills in as source_idx 0.
    item_selected = Signal(str, str, str, int)

    # Prefix that switches Quick Search to column-only results (issue #241)
    COLUMN_FILTER_PREFIX = "c:"

    def __init__(self, all_items, parent=None, column_items=None,
                 recency_scores=None, recent_items=None, sources=None,
                 empty_state_label="Recent", empty_state_limit=15,
                 default_source_idx=None):
        super().__init__(parent)

        # Add Cmd+W shortcut to close dialog
        close_shortcut = QShortcut(QKeySequence("Ctrl+W"), self)
        close_shortcut.activated.connect(self.reject)


        # issue #243: which connection each item came from, by index — only
        # meaningful (and only shown) when a caller searches more than one
        # connection at once. Single-connection callers leave this empty.
        self.sources = sources or []
        # issue #275: cross-connection search (#243) used to always mix in
        # every open connection's tables/columns, so a name that exists in
        # more than one connection (a common case — staging mirrors prod's
        # schema) showed up once per connection even though only the
        # active one usually matters. Default to just the connection the
        # user was already on ("this connection only") — the caller passes
        # which one via default_source_idx — with an opt-in checkbox
        # (added in init_ui, only when there's actually more than one
        # source) to broaden back out to every connection when that's
        # genuinely what's wanted.
        self.default_source_idx = default_source_idx
        self.search_all_connections = (
            default_source_idx is None or len(sources or []) <= 1
        )
        self.all_items = self._normalize(all_items)
        # Columns are kept out of the default result set (issue #241) but
        # stay searchable via the explicit "c:" prefix below.
        self.column_items = self._normalize(column_items or [])

        # issue #242: {(item_type, display_text): score}, higher = more
        # recently used — breaks ties within a match tier. Items absent
        # from this dict sort last within their tier (score treated as 0).
        self.recency_scores = recency_scores or {}
        # (item_type, display_text, payload) tuples shown before the user
        # has typed anything — originally just "recently opened" (issue
        # #242), generalized so Command Palette can list every available
        # command up front instead (issue: palette showed nothing until
        # you typed, so there was no way to browse what's there). Label
        # and how many to show without a query are caller-controlled.
        self.recent_items = self._normalize(recent_items or [])
        self.empty_state_label = empty_state_label
        self.empty_state_limit = empty_state_limit
        self.setWindowTitle("Quick Search")
        self.setMinimumWidth(700)
        self.setMinimumHeight(500)
        
        # Modern styling like Spotlight
        self.setStyleSheet("""
            QDialog {
                background-color: #2b2b2b;
                border-radius: 12px;
            }
            QLineEdit {
                background-color: #3a3a3a;
                border: 2px solid #4a4a4a;
                border-radius: 8px;
                padding: 12px;
                font-size: 16px;
                color: white;
            }
            QLineEdit:focus {
                border: 2px solid #0066cc;
            }
            QListWidget {
                background-color: #2b2b2b;
                border: none;
                font-size: 14px;
                color: white;
                outline: none;
            }
            QListWidget::item {
                padding: 8px;
                border-radius: 6px;
                margin: 2px 4px;
            }
            QListWidget::item:selected {
                background-color: #0066cc;
            }
            QListWidget::item:hover {
                background-color: #3a3a3a;
            }
            QLabel {
                color: #999;
            }
            QCheckBox {
                color: #999;
                font-size: 12px;
                spacing: 6px;
            }
        """)
        
        self.init_ui()

    @staticmethod
    def _normalize(items):
        """Pads every entry out to the full (item_type, display_text,
        payload, source_idx, extra) shape: plain 3-tuples (item_type,
        display_text, payload) get source_idx 0 (issue #243) and extra {}
        (issue #245); 4-tuples get extra {}."""
        normalized = []
        for e in items:
            if len(e) == 3:
                normalized.append((*e, 0, {}))
            elif len(e) == 4:
                normalized.append((*e, {}))
            else:
                normalized.append(e)
        return normalized

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        
        # Search input
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(
            "Search tables, views, functions, history, snippets... "
            f"({self.COLUMN_FILTER_PREFIX} to search columns)")
        self.search_input.textChanged.connect(self.filter_items)
        self.search_input.installEventFilter(self)  # Install event filter for arrow keys
        layout.addWidget(self.search_input)

        # Scope checkbox (issue #275) — only relevant, and only shown, when
        # this dialog actually has more than one connection to search and a
        # caller-chosen default (the active one) to scope down to.
        self.scope_checkbox = None
        if self.default_source_idx is not None and len(self.sources) > 1:
            active_label = (
                self.sources[self.default_source_idx]
                if 0 <= self.default_source_idx < len(self.sources) else ""
            )
            self.scope_checkbox = QCheckBox(
                f"Search all {len(self.sources)} connections "
                f"(unchecked: {active_label} only)"
            )
            self.scope_checkbox.setChecked(self.search_all_connections)
            self.scope_checkbox.setStyleSheet("color: #999; font-size: 12px;")
            self.scope_checkbox.toggled.connect(self._on_scope_toggled)
            layout.addWidget(self.scope_checkbox)

        # Results count
        self.count_label = QLabel()
        self.count_label.setStyleSheet("color: #888; font-size: 12px; margin: 0 5px;")
        layout.addWidget(self.count_label)
        
        # Results list
        self.results_list = QListWidget()
        self.results_list.setItemDelegate(QuickSearchItemDelegate(self.results_list))
        self.results_list.itemDoubleClicked.connect(self.on_item_selected)
        self.results_list.itemActivated.connect(self.on_item_selected)  # Enter key
        layout.addWidget(self.results_list)
        
        # Help text
        help_text = QLabel(
            "⏎ Enter to open  |  Esc to close  |  ↑↓ to navigate  |  "
            f"{self.COLUMN_FILTER_PREFIX} for columns")
        help_text.setStyleSheet("color: #666; font-size: 11px; margin: 5px; text-align: center;")
        help_text.setAlignment(Qt.AlignCenter)
        layout.addWidget(help_text)

        # Render the initial (empty-query) state right away — textChanged
        # only fires on an actual edit, so without this the list stayed
        # blank until the first keystroke even when there were default
        # items (e.g. Command Palette's "All Commands") to show up front.
        self.filter_items("")

    def eventFilter(self, obj, event):
        """Handle arrow key navigation from search input"""
        if obj == self.search_input and event.type() == event.Type.KeyPress:
            if event.key() == Qt.Key_Down:
                # filter_items() already selects row 0 as soon as there's a
                # match, so this first Down press should land on row 1, not
                # "confirm" row 0 a second time (issue: it previously took
                # two presses to reach the second result).
                if self.results_list.count() > 0:
                    self.results_list.setFocus()
                    current = self.results_list.currentRow()
                    if current < 0:
                        self.results_list.setCurrentRow(0)
                    else:
                        next_row = min(current + 1, self.results_list.count() - 1)
                        self.results_list.setCurrentRow(next_row)
                return True
            elif event.key() == Qt.Key_Up:
                # Move focus to results list and select last item
                if self.results_list.count() > 0:
                    self.results_list.setFocus()
                    self.results_list.setCurrentRow(self.results_list.count() - 1)
                return True
            elif event.key() in (Qt.Key_Return, Qt.Key_Enter):
                # Select current item
                if self.results_list.currentItem():
                    self.on_item_selected(self.results_list.currentItem())
                return True
        return super().eventFilter(obj, event)

    def _on_scope_toggled(self, checked: bool):
        self.search_all_connections = checked
        self.filter_items(self.search_input.text())

    def _scoped(self, items):
        """Restrict *items* to self.default_source_idx unless the "search
        all connections" checkbox is checked (issue #275)."""
        if self.search_all_connections or self.default_source_idx is None:
            return items
        return [e for e in items if e[3] == self.default_source_idx]

    def filter_items(self, search_text):
        """Filter items based on search text"""
        self.results_list.clear()
        search_text = search_text.lower().strip()

        # "c:<text>" searches columns only (issue #241) — they're excluded
        # from the default result set below since they otherwise swamp
        # table/view matches by sheer volume.
        if search_text.startswith(self.COLUMN_FILTER_PREFIX):
            search_text = search_text[len(self.COLUMN_FILTER_PREFIX):].strip()
            source_items = self._scoped(self.column_items)
        else:
            source_items = self._scoped(self.all_items)

        # Nothing typed yet: show the caller's default items (recently-used
        # for Quick Search issue #242, every command for the Command
        # Palette) instead of an empty "type to search" prompt, when the
        # caller supplied any.
        if len(search_text) < 1:
            recent = self._scoped(self.recent_items)
            if recent:
                self._render_results(
                    recent, self.empty_state_label, limit=self.empty_state_limit)
            else:
                self.count_label.setText("Type to search...")
            return

        exact_matches = []
        starts_with_matches = []
        contains_matches = []
        fuzzy_matches = []

        for entry in source_items:
            item_type, display_text, payload, source_idx, extra = entry
            display_lower = display_text.lower()

            # Prioritize exact matches
            if search_text == display_lower:
                exact_matches.append(entry)
            # Then starts with matches
            elif display_lower.startswith(search_text):
                starts_with_matches.append(entry)
            # Then contains matches
            elif search_text in display_lower:
                contains_matches.append(entry)
            # Finally fuzzy matches
            elif self.fuzzy_match(search_text, display_lower):
                fuzzy_matches.append(entry)

        # issue #276: within the starts-with/contains/fuzzy tiers, a longer
        # (or, for fuzzy, more spread-out) match is a worse match — without
        # this, "ftask" ranked "archive_f_task" above the obviously-better
        # "f_task" purely by alphabetical luck, since every match in a tier
        # was otherwise treated as equally good. Sort by match tightness
        # first so ties (including "no recency data at all", the common
        # case for a table never opened before) fall back to something
        # more useful than insertion order.
        starts_with_matches.sort(key=lambda e: len(e[1]))
        contains_matches.sort(key=lambda e: len(e[1]))
        fuzzy_matches.sort(key=lambda e: self._fuzzy_match_score(search_text, e[1].lower()))

        # Within each tier, break ties by recency (issue #242) — the tier
        # itself (exact > starts-with > contains > fuzzy) still dominates;
        # this (a stable sort) only reorders items that already matched
        # equally well, on top of the tightness ordering just above.
        tiers = [exact_matches, starts_with_matches, contains_matches, fuzzy_matches]
        if self.recency_scores:
            for tier in tiers:
                tier.sort(key=self._recency_of, reverse=True)

        matching_items = [e for tier in tiers for e in tier]
        self._render_results(matching_items, None)

    def _recency_of(self, entry):
        item_type, display_text, payload, source_idx, extra = entry
        return self.recency_scores.get((item_type, display_text), 0)

    def _render_results(self, matching_items, section_label, limit=15):
        """Populate results_list from *matching_items* (already ordered),
        capped to *limit*, and update count_label. *section_label* (e.g.
        "Recent", or Command Palette's "All Commands") is shown instead of
        the usual result count when given. Each row's type/shortcut badge
        is painted by QuickSearchItemDelegate (issue #245) from the full
        tuple stored in Qt.UserRole; item text is just the plain label
        (+ connection suffix)."""
        for item_type, display_text, payload, source_idx, extra in matching_items[:limit]:
            # issue #243/#266: disambiguate which connection a result came
            # from — only meaningful (and only shown) while actually
            # searching more than one, i.e. the "Search all connections"
            # checkbox is on.
            suffix = ""
            if self.search_all_connections and len(self.sources) > 1 and 0 <= source_idx < len(self.sources):
                suffix = f"  ({self.sources[source_idx]})"
            item = QListWidgetItem(f"{display_text}{suffix}")
            item.setData(Qt.UserRole, (item_type, display_text, payload, source_idx, extra))
            self.results_list.addItem(item)

        total_count = len(matching_items)
        shown_count = min(total_count, limit)
        if section_label:
            self.count_label.setText(section_label if total_count else "Type to search...")
        elif total_count > limit:
            self.count_label.setText(f"Showing top {shown_count} of {total_count} results")
        elif total_count > 0:
            self.count_label.setText(f"{total_count} result{'s' if total_count != 1 else ''}")
        else:
            self.count_label.setText("No results found")

        # Select first item
        if self.results_list.count() > 0:
            self.results_list.setCurrentRow(0)
    
    def fuzzy_match(self, search, text):
        """Check if search characters appear in order in text"""
        return self._fuzzy_match_score(search, text) is not None

    def _fuzzy_match_score(self, search, text):
        """Greedy left-to-right subsequence match of *search* in *text*.
        Returns a (span, len(text), first_idx) tuple where a smaller value
        (lexicographic) is a tighter/better match, or None when *search*
        doesn't match as a subsequence at all — same predicate fuzzy_match
        already used, just keeping the span/position it found instead of
        throwing them away, so equally-tiered fuzzy results can be ranked
        by how good a match they actually are (issue #276)."""
        if not search:
            return None
        search_idx = 0
        first_idx = last_idx = None
        for i, char in enumerate(text):
            if search_idx < len(search) and char == search[search_idx]:
                if first_idx is None:
                    first_idx = i
                last_idx = i
                search_idx += 1
        if search_idx != len(search):
            return None
        return (last_idx - first_idx + 1, len(text), first_idx)
    
    def get_icon(self, item_type):
        """Get icon for item type - removed, no icons"""
        return ""
    
    def on_item_selected(self, item):
        """Handle item selection"""
        item_type, display_text, payload, source_idx, extra = item.data(Qt.UserRole)
        if extra.get("disabled"):
            # issue #246: a contextually-unavailable command stays visible
            # (greyed out, with its reason) but can't actually be triggered
            # from here — Enter/double-click on it is a no-op, dialog stays
            # open, same as pressing it would do nothing if it were hidden.
            return
        self.item_selected.emit(item_type, display_text, payload or "", source_idx)
        self.accept()
    
    def keyPressEvent(self, event: QKeyEvent):
        """Handle key presses"""
        if event.key() == Qt.Key_Return or event.key() == Qt.Key_Enter:
            # Open selected item
            current_item = self.results_list.currentItem()
            if current_item:
                self.on_item_selected(current_item)
        elif event.key() == Qt.Key_Escape:
            # Close dialog
            self.reject()
        elif event.key() in (Qt.Key_Down, Qt.Key_Up):
            # Let list handle navigation
            self.results_list.keyPressEvent(event)
        else:
            # Pass to search input
            self.search_input.keyPressEvent(event)
            self.search_input.setFocus()
