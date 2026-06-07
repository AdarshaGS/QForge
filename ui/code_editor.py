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
      • Matching-bracket underline
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        self._gutter = _Gutter(self)
        self._current_line_color = QColor("#282828")   # very subtle
        self._gutter_fg          = QColor("#4a4a4a")
        self._gutter_bg          = QColor("#1a1a1a")
        self._gutter_active_fg   = QColor("#858585")

        # Font
        font = QFont("Menlo", 15)
        if not font.exactMatch():
            font = QFont("Monaco", 15)
        if not font.exactMatch():
            font.setFamily("Courier New")
        font.setFixedPitch(True)
        self.setFont(font)

        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setTabStopDistance(QFontMetrics(font).horizontalAdvance(" ") * 4)

        # Signals
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter)
        self.cursorPositionChanged.connect(self._highlight_current_line)

        self._update_gutter_width(0)
        self._highlight_current_line()

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

    # ── Current-line highlight ────────────────────────────────────────────────

    def _highlight_current_line(self):
        extras = []
        if not self.isReadOnly():
            sel = QTextEdit.ExtraSelection()
            sel.format.setBackground(self._current_line_color)
            sel.format.setProperty(QTextFormat.FullWidthSelection, True)
            sel.cursor = self.textCursor()
            sel.cursor.clearSelection()
            extras.append(sel)
        self.setExtraSelections(extras)

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
        self._current_line_color = QColor("#282828")
        self._gutter_fg          = QColor("#4a4a4a")
        self._gutter_bg          = QColor("#1a1a1a")
        self._gutter_active_fg   = QColor("#858585")
        self._highlight_current_line()
        self._gutter.update()

    def apply_light_palette(self):
        self._current_line_color = QColor("#f0f0f0")
        self._gutter_fg          = QColor("#aaaaaa")
        self._gutter_bg          = QColor("#f5f5f5")
        self._gutter_active_fg   = QColor("#333333")
        self._highlight_current_line()
        self._gutter.update()
