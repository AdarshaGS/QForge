"""
code_editor.py — QPlainTextEdit with line numbers and current-line highlight.

Drop-in for QTextEdit in sql_tab.py:
    from ui.code_editor import CodeEditor
    self.editor = CodeEditor()
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRect, QSize, Signal
from PySide6.QtGui import (
    QColor, QPainter, QTextFormat, QTextCursor, QFont, QFontMetrics,
    QKeySequence, QPalette,
)
from PySide6.QtWidgets import QPlainTextEdit, QWidget, QTextEdit


# ─── Gutter (line-number area) ────────────────────────────────────────────────

class _Gutter(QWidget):
    """Narrow sidebar painted by CodeEditor."""

    def __init__(self, editor: "CodeEditor"):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self._editor._gutter_width(), 0)

    def paintEvent(self, event):
        self._editor._paint_gutter(event)


# ─── Main editor ──────────────────────────────────────────────────────────────

class CodeEditor(QPlainTextEdit):
    """
    QPlainTextEdit with:
      • Line-number gutter (auto-width, dim colour)
      • Current-line highlight (subtle tint)
      • Matching-bracket highlight (green when paired, red when unmatched)
      • Ctrl/Cmd-click go-to-definition hook (identifier_clicked signal)
    """

    identifier_clicked = Signal(int)   # document position of a Ctrl/Cmd-click

    def __init__(self, parent=None):
        super().__init__(parent)

        self._gutter = _Gutter(self)
        self._current_line_color = QColor("#282828")   # very subtle
        self._gutter_fg          = QColor("#4a4a4a")
        self._gutter_bg          = QColor("#1a1a1a")
        self._gutter_active_fg   = QColor("#858585")

        # Font
        font = QFont("Menlo", 18)
        if not font.exactMatch():
            font = QFont("Monaco", 18)
        if not font.exactMatch():
            font.setFamily("Courier New")
        font.setFixedPitch(True)
        self.setFont(font)

        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setTabStopDistance(QFontMetrics(font).horizontalAdvance(" ") * 4)

        self._bracket_match_color   = QColor("#4a6b4a")
        self._bracket_nomatch_color = QColor("#6b4a4a")

        # Signals
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter)
        self.cursorPositionChanged.connect(self._update_extra_selections)

        self._update_gutter_width(0)
        self._update_extra_selections()

    # ── API expected by sql_tab (originally QTextEdit) ───────────────────────

    def toPlainText(self) -> str:                       # same as base
        return super().toPlainText()

    def setPlainText(self, text: str):                  # same as base
        super().setPlainText(text)

    # QTextEdit compatibility shims
    def textCursor(self) -> QTextCursor:
        return super().textCursor()

    def setTextCursor(self, cursor: QTextCursor):
        super().setTextCursor(cursor)

    def cursorRect(self, cursor: QTextCursor | None = None) -> QRect:
        if cursor is None:
            return super().cursorRect()
        return super().cursorRect(cursor)

    def insertPlainText(self, text: str):
        super().insertPlainText(text)

    # map QTextEdit.setPlaceholderText → QPlainTextEdit already has it ✓

    # ── Gutter width ──────────────────────────────────────────────────────────

    def _gutter_width(self) -> int:
        digits = max(len(str(self.blockCount())), 3)
        return 10 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_gutter_width(self, _):
        self.setViewportMargins(self._gutter_width(), 0, 0, 0)

    def _update_gutter(self, rect: QRect, dy: int):
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_gutter_width(0)

    # ── Gutter paint ─────────────────────────────────────────────────────────

    def _paint_gutter(self, event):
        painter = QPainter(self._gutter)
        painter.fillRect(event.rect(), self._gutter_bg)

        block       = self.firstVisibleBlock()
        block_num   = block.blockNumber()
        top         = round(self.blockBoundingGeometry(block)
                            .translated(self.contentOffset()).top())
        bottom      = top + round(self.blockBoundingRect(block).height())
        cur_line    = self.textCursor().blockNumber()

        font = QFont(self.font())
        font.setPointSize(max(self.font().pointSize() - 1, 9))
        painter.setFont(font)

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                is_current = block_num == cur_line
                painter.setPen(
                    self._gutter_active_fg if is_current else self._gutter_fg)
                painter.drawText(
                    0, top,
                    self._gutter.width() - 4, self.fontMetrics().height(),
                    Qt.AlignRight | Qt.AlignVCenter,
                    str(block_num + 1),
                )

            block     = block.next()
            top       = bottom
            bottom    = top + round(self.blockBoundingRect(block).height())
            block_num += 1

        painter.end()

    # ── Current-line highlight + matching-bracket underline ──────────────────

    _OPEN_BRACKETS  = "([{"
    _CLOSE_BRACKETS = ")]}"
    _BRACKET_PAIRS  = dict(zip("([{", ")]}"))
    _BRACKET_PAIRS.update({v: k for k, v in dict(zip("([{", ")]}")).items()})

    def _update_extra_selections(self):
        extras = []
        if not self.isReadOnly():
            sel = QTextEdit.ExtraSelection()
            sel.format.setBackground(self._current_line_color)
            sel.format.setProperty(QTextFormat.FullWidthSelection, True)
            sel.cursor = self.textCursor()
            sel.cursor.clearSelection()
            extras.append(sel)
        extras.extend(self._bracket_match_selections())
        self._own_extra_selections = extras
        self.setExtraSelections(extras)

    def own_extra_selections(self) -> list:
        """The line-highlight + bracket-match selections this editor owns,
        as last computed. Callers layering their own selections on top
        (e.g. sql_tab's schema-validation squiggles) should rebuild from
        this rather than slicing extraSelections(), since the count here
        varies (0-3) with bracket-match state."""
        return list(getattr(self, "_own_extra_selections", []))

    def _code_mask(self, text: str) -> list[bool]:
        """Per-character mask: True where the character is plain SQL code,
        False inside string literals, quoted identifiers, or comments."""
        mask = [True] * len(text)
        i, n = 0, len(text)
        while i < n:
            ch = text[i]
            if ch in ("'", '"', "`"):
                quote = ch
                mask[i] = False
                i += 1
                while i < n:
                    mask[i] = False
                    if text[i] == quote:
                        if i + 1 < n and text[i + 1] == quote:  # doubled-quote escape
                            mask[i + 1] = False
                            i += 2
                            continue
                        i += 1
                        break
                    i += 1
                continue
            if ch == "-" and i + 1 < n and text[i + 1] == "-":
                while i < n and text[i] != "\n":
                    mask[i] = False
                    i += 1
                continue
            if ch == "/" and i + 1 < n and text[i + 1] == "*":
                mask[i] = mask[i + 1] = False
                i += 2
                while i < n:
                    mask[i] = False
                    if text[i] == "*" and i + 1 < n and text[i + 1] == "/":
                        mask[i + 1] = False
                        i += 2
                        break
                    i += 1
                continue
            i += 1
        return mask

    def _find_matching_bracket(self, text: str, mask: list[bool], pos: int) -> tuple[int, int] | None:
        """Given a bracket at `pos`, return (pos, match_pos) or (pos, -1) if unmatched."""
        ch = text[pos]
        other = self._BRACKET_PAIRS[ch]
        if ch in self._OPEN_BRACKETS:
            depth = 0
            for j in range(pos, len(text)):
                if not mask[j]:
                    continue
                if text[j] == ch:
                    depth += 1
                elif text[j] == other:
                    depth -= 1
                    if depth == 0:
                        return (pos, j)
            return (pos, -1)
        else:
            depth = 0
            for j in range(pos, -1, -1):
                if not mask[j]:
                    continue
                if text[j] == ch:
                    depth += 1
                elif text[j] == other:
                    depth -= 1
                    if depth == 0:
                        return (pos, j)
            return (pos, -1)

    def _bracket_match_selections(self) -> list:
        text = self.toPlainText()
        pos = self.textCursor().position()

        candidates = []
        if pos < len(text) and (text[pos] in self._OPEN_BRACKETS or text[pos] in self._CLOSE_BRACKETS):
            candidates.append(pos)
        if pos - 1 >= 0 and (text[pos - 1] in self._OPEN_BRACKETS or text[pos - 1] in self._CLOSE_BRACKETS):
            candidates.append(pos - 1)
        if not candidates:
            return []

        mask = self._code_mask(text)
        bracket_pos = None
        for c in candidates:
            if mask[c]:
                bracket_pos = c
                break
        if bracket_pos is None:
            return []

        found = self._find_matching_bracket(text, mask, bracket_pos)
        if found is None:
            return []
        first, second = found

        def _selection_at(index: int, matched: bool) -> QTextEdit.ExtraSelection:
            sel = QTextEdit.ExtraSelection()
            sel.format.setBackground(
                self._bracket_match_color if matched else self._bracket_nomatch_color)
            cursor = QTextCursor(self.document())
            cursor.setPosition(index)
            cursor.setPosition(index + 1, QTextCursor.KeepAnchor)
            sel.cursor = cursor
            return sel

        matched = second != -1
        selections = [_selection_at(first, matched)]
        if matched:
            selections.append(_selection_at(second, matched))
        return selections

    # ── Go-to-definition hook ─────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if (event.button() == Qt.LeftButton
                and event.modifiers() & Qt.ControlModifier):
            cursor = self.cursorForPosition(event.pos())
            self.identifier_clicked.emit(cursor.position())
            return   # don't let the modifier-click also start a text selection
        super().mousePressEvent(event)

    # ── Resize: keep gutter in sync ───────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._gutter.setGeometry(
            QRect(cr.left(), cr.top(), self._gutter_width(), cr.height()))
    # ── Font change: update gutter width and tab stop ─────────────────────

    def changeEvent(self, event):
        super().changeEvent(event)
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.Type.FontChange:
            self.setTabStopDistance(
                QFontMetrics(self.font()).horizontalAdvance(" ") * 4)
            self._update_gutter_width(0)
            cr = self.contentsRect()
            self._gutter.setGeometry(
                QRect(cr.left(), cr.top(), self._gutter_width(), cr.height()))
            self._gutter.update()
    # ── Theme update ─────────────────────────────────────────────────────────

    def apply_dark_palette(self):
        self._current_line_color   = QColor("#282828")
        self._gutter_fg            = QColor("#4a4a4a")
        self._gutter_bg            = QColor("#1a1a1a")
        self._gutter_active_fg     = QColor("#858585")
        self._bracket_match_color  = QColor("#4a6b4a")
        self._bracket_nomatch_color = QColor("#6b4a4a")
        self._update_extra_selections()
        self._gutter.update()

    def apply_light_palette(self):
        self._current_line_color   = QColor("#f0f0f0")
        self._gutter_fg            = QColor("#aaaaaa")
        self._gutter_bg            = QColor("#f5f5f5")
        self._gutter_active_fg     = QColor("#333333")
        self._bracket_match_color  = QColor("#c9e6c9")
        self._bracket_nomatch_color = QColor("#f0c9c9")
        self._update_extra_selections()
        self._gutter.update()
