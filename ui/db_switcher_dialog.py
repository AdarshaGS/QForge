"""
DbSwitcherDialog
────────────────
Cmd+K spotlight-style popup for switching databases.
Type to filter, Enter/click to switch.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QLabel
)
from PySide6.QtGui import QKeySequence, QShortcut


class DbSwitcherDialog(QDialog):
    """Cmd+K database switcher — shows all databases, type to filter."""

    db_selected = Signal(str)   # emitted with the chosen database name

    STYLE = """
        QDialog {
            background-color: #252526;
            border: 1px solid #454545;
            border-radius: 10px;
        }
        QLineEdit {
            background-color: #1e1e1e;
            border: none;
            border-bottom: 1px solid #454545;
            padding: 12px 14px;
            font-size: 15px;
            color: #cccccc;
        }
        QListWidget {
            background-color: #252526;
            border: none;
            font-size: 13px;
            color: #cccccc;
            outline: none;
        }
        QListWidget::item {
            padding: 9px 14px;
            border-radius: 4px;
            margin: 1px 4px;
        }
        QListWidget::item:selected {
            background-color: #094771;
            color: #ffffff;
        }
        QListWidget::item:hover:!selected {
            background-color: #2a2d2e;
        }
        QLabel#hint {
            color: #555;
            font-size: 11px;
            padding: 4px 14px 6px;
        }
    """

    def __init__(self, databases: list[str], current_db: str = "", parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet(self.STYLE)
        self.setFixedWidth(420)
        self.setMaximumHeight(420)

        self._all_dbs = databases
        self._current = current_db

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Search field
        self.search = QLineEdit()
        self.search.setPlaceholderText("Switch database…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        self.search.installEventFilter(self)
        layout.addWidget(self.search)

        # List
        self.list_widget = QListWidget()
        self.list_widget.itemActivated.connect(self._pick)
        self.list_widget.itemClicked.connect(self._pick)
        self.list_widget.installEventFilter(self)
        layout.addWidget(self.list_widget)

        # Hint
        hint = QLabel("↑↓ navigate   ⏎ switch   Esc cancel")
        hint.setObjectName("hint")
        hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(hint)

        QShortcut(QKeySequence("Escape"), self).activated.connect(self.reject)

        self._populate(databases)
        self._select_current()
        self.search.setFocus()

    # ── Population ────────────────────────────────────────────────────────────

    def _populate(self, dbs: list[str]):
        self.list_widget.clear()
        for db in dbs:
            item = QListWidgetItem(("  ✓  " if db == self._current else "      ") + db)
            item.setData(Qt.UserRole, db)
            self.list_widget.addItem(item)
        self._resize_to_content()

    def _select_current(self):
        for i in range(self.list_widget.count()):
            if self.list_widget.item(i).data(Qt.UserRole) == self._current:
                self.list_widget.setCurrentRow(i)
                return
        if self.list_widget.count():
            self.list_widget.setCurrentRow(0)

    def _resize_to_content(self):
        rows = min(self.list_widget.count(), 12)
        row_h = self.list_widget.sizeHintForRow(0) if self.list_widget.count() else 30
        self.list_widget.setFixedHeight(max(rows * (row_h + 2), 40))
        self.adjustSize()

    # ── Filtering ─────────────────────────────────────────────────────────────

    def _filter(self, text: str):
        text = text.lower().strip()
        if not text:
            self._populate(self._all_dbs)
            self._select_current()
            return

        filtered = [db for db in self._all_dbs if text in db.lower()]
        self._populate(filtered)
        if self.list_widget.count():
            self.list_widget.setCurrentRow(0)

    # ── Selection ─────────────────────────────────────────────────────────────

    def _pick(self, item: QListWidgetItem):
        db = item.data(Qt.UserRole)
        if db:
            self.db_selected.emit(db)
            self.accept()

    # ── Keyboard nav from search field ────────────────────────────────────────

    def eventFilter(self, obj, event):
        if event.type() == event.Type.KeyPress:
            key = event.key()
            if key == Qt.Key_Down:
                n = self.list_widget.count()
                if n:
                    self.list_widget.setFocus()
                    row = self.list_widget.currentRow()
                    self.list_widget.setCurrentRow(
                        min(row + 1, n - 1) if row >= 0 else 0)
                return True
            if key == Qt.Key_Up:
                n = self.list_widget.count()
                if n:
                    self.list_widget.setFocus()
                    row = self.list_widget.currentRow()
                    self.list_widget.setCurrentRow(
                        max(row - 1, 0) if row >= 0 else n - 1)
                return True
            if key in (Qt.Key_Return, Qt.Key_Enter):
                item = self.list_widget.currentItem()
                if item:
                    self._pick(item)
                return True
        return super().eventFilter(obj, event)
