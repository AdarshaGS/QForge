"""
snippet_editor_dialog.py — Full CRUD UI for SQL snippets.

Layout
──────
┌─────────────────┬───────────────────────────────────────┐
│  [Search…]      │  Trigger  [sel     ]                  │
│  ─────────────  │  Name     [SELECT *]                  │
│  sel  SELECT*   │  Desc     [Basic SELECT query]        │
│  ins  INSERT    │  ─────────────────────────────────── │
│  upd  UPDATE    │  Body  (use {cursor} for cursor pos)  │
│  del  DELETE    │  ┌─────────────────────────────────┐  │
│  …              │  │ SELECT *                        │  │
│                 │  │ FROM {cursor}                   │  │
│                 │  │ WHERE ;                         │  │
│ [+Add] [Delete] │  └─────────────────────────────────┘  │
│                 │                      [Save Snippet]   │
├─────────────────┴───────────────────────────────────────┤
│  [Export…]   [Import…]   [Restore Defaults]   [Close]   │
└─────────────────────────────────────────────────────────┘
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui  import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QVBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPlainTextEdit, QListWidget, QListWidgetItem,
    QPushButton, QWidget, QFileDialog, QMessageBox, QSplitter,
    QFrame,
)

from ui.snippet_manager import SnippetManager

_DARK = """
QDialog, QWidget {
    background: #1c1c1e;
    color: #e5e5ea;
}
QLineEdit, QPlainTextEdit {
    background: #2c2c2e;
    color: #e5e5ea;
    border: 1px solid #3a3a3c;
    border-radius: 5px;
    padding: 4px 8px;
    selection-background-color: #0A84FF55;
}
QListWidget {
    background: #2c2c2e;
    color: #e5e5ea;
    border: 1px solid #3a3a3c;
    border-radius: 5px;
    outline: none;
}
QListWidget::item {
    padding: 6px 10px;
    border-radius: 4px;
}
QListWidget::item:selected {
    background: #0A84FF33;
    color: #e5e5ea;
}
QListWidget::item:hover:!selected {
    background: #3a3a3c;
}
QLabel {
    color: #8e8e93;
    font-size: 12px;
}
QLabel#section {
    color: #e5e5ea;
    font-size: 13px;
    font-weight: 600;
}
QPushButton {
    background: #2c2c2e;
    color: #e5e5ea;
    border: 1px solid #3a3a3c;
    border-radius: 5px;
    padding: 5px 14px;
    font-size: 12px;
    min-height: 26px;
}
QPushButton:hover  { background: #3a3a3c; }
QPushButton:pressed { background: #0A84FF44; }
QPushButton#primary {
    background: #0A84FF;
    border-color: #0A84FF;
    color: #fff;
    font-weight: 600;
}
QPushButton#primary:hover  { background: #228BFF; }
QPushButton#primary:pressed { background: #0066CC; }
QPushButton#danger {
    color: #ff453a;
    border-color: #3a3a3c;
}
QPushButton#danger:hover { background: #3a1a1a; }
QFrame#divider {
    background: #3a3a3c;
    max-height: 1px;
    min-height: 1px;
}
"""


class SnippetEditorDialog(QDialog):
    """Full CRUD dialog for managing SQL snippets."""

    snippets_changed = Signal()   # emitted whenever snippets are saved/deleted/imported

    def __init__(self, snippet_manager: SnippetManager, parent=None):
        super().__init__(parent)
        self.sm = snippet_manager
        self._current_trigger: str | None = None
        self._dirty = False

        self.setWindowTitle("SQL Snippets")
        self.setMinimumSize(760, 520)
        self.setStyleSheet(_DARK)

        self._build_ui()
        self._refresh_list()

        # Global Esc → close
        QShortcut(QKeySequence("Escape"), self).activated.connect(self.close)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Title bar ─────────────────────────────────────────────────────────
        title_bar = QWidget()
        title_bar.setStyleSheet("background: #2c2c2e; border-bottom: 1px solid #3a3a3c;")
        tl = QHBoxLayout(title_bar)
        tl.setContentsMargins(16, 10, 16, 10)
        lbl = QLabel("SQL Snippets")
        lbl.setStyleSheet("color:#e5e5ea; font-size:15px; font-weight:600;")
        tl.addWidget(lbl)
        tl.addStretch()
        hint = QLabel("Type a trigger in the editor to expand")
        hint.setStyleSheet("color:#636366; font-size:11px;")
        tl.addWidget(hint)
        root.addWidget(title_bar)

        # ── Main splitter ──────────────────────────────────────────────────────
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet("QSplitter::handle { background: #3a3a3c; }")

        # LEFT panel
        left = QWidget()
        left.setStyleSheet("background: #1c1c1e;")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(12, 12, 6, 12)
        ll.setSpacing(8)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter snippets…")
        self._search.textChanged.connect(self._filter_list)
        self._search.setClearButtonEnabled(True)
        ll.addWidget(self._search)

        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.SingleSelection)
        self._list.currentItemChanged.connect(self._on_item_selected)
        ll.addWidget(self._list)

        add_del = QHBoxLayout()
        add_del.setSpacing(6)
        add_btn = QPushButton("+ New")
        add_btn.clicked.connect(self._new_snippet)
        del_btn = QPushButton("Delete")
        del_btn.setObjectName("danger")
        del_btn.clicked.connect(self._delete_snippet)
        add_del.addWidget(add_btn)
        add_del.addWidget(del_btn)
        ll.addLayout(add_del)

        left.setMinimumWidth(200)
        left.setMaximumWidth(260)
        splitter.addWidget(left)

        # RIGHT panel
        right = QWidget()
        right.setStyleSheet("background: #1c1c1e;")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(12, 12, 16, 12)
        rl.setSpacing(10)

        # Form
        form = QGridLayout()
        form.setSpacing(8)
        form.setColumnMinimumWidth(0, 80)

        form.addWidget(self._label("Trigger"), 0, 0)
        self._trigger = QLineEdit()
        self._trigger.setPlaceholderText("e.g.  sel  (what you type in editor)")
        form.addWidget(self._trigger, 0, 1)

        form.addWidget(self._label("Name"), 1, 0)
        self._name = QLineEdit()
        self._name.setPlaceholderText("e.g.  SELECT * FROM …")
        form.addWidget(self._name, 1, 1)

        form.addWidget(self._label("Description"), 2, 0)
        self._desc = QLineEdit()
        self._desc.setPlaceholderText("Optional short note")
        form.addWidget(self._desc, 2, 1)

        rl.addLayout(form)

        # Divider
        div = QFrame()
        div.setObjectName("divider")
        rl.addWidget(div)

        # Body editor
        body_lbl = QLabel("Body")
        body_lbl.setObjectName("section")
        hint2 = QLabel("Use  {cursor}  to mark where the cursor will land after expansion")
        hint2.setStyleSheet("color:#636366; font-size:11px;")
        rl.addWidget(body_lbl)
        rl.addWidget(hint2)

        self._body = QPlainTextEdit()
        font = QFont("Menlo", 14)
        if not font.exactMatch():
            font = QFont("Monaco", 14)
        font.setFixedPitch(True)
        self._body.setFont(font)
        self._body.setPlaceholderText("SELECT *\nFROM {cursor}\nWHERE ;")
        self._body.setTabChangesFocus(False)
        rl.addWidget(self._body)

        # Save
        save_row = QHBoxLayout()
        save_row.addStretch()
        save_btn = QPushButton("Save Snippet")
        save_btn.setObjectName("primary")
        save_btn.clicked.connect(self._save_snippet)
        save_row.addWidget(save_btn)
        rl.addLayout(save_row)

        splitter.addWidget(right)
        splitter.setSizes([220, 540])
        root.addWidget(splitter)

        # ── Bottom bar ────────────────────────────────────────────────────────
        bottom = QWidget()
        bottom.setStyleSheet("background: #2c2c2e; border-top: 1px solid #3a3a3c;")
        bl = QHBoxLayout(bottom)
        bl.setContentsMargins(12, 8, 12, 8)
        bl.setSpacing(8)

        exp_btn = QPushButton("Export…")
        exp_btn.clicked.connect(self._export)
        imp_btn = QPushButton("Import…")
        imp_btn.clicked.connect(self._import)
        rst_btn = QPushButton("Restore Defaults")
        rst_btn.clicked.connect(self._restore_defaults)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)

        bl.addWidget(exp_btn)
        bl.addWidget(imp_btn)
        bl.addWidget(rst_btn)
        bl.addStretch()
        bl.addWidget(close_btn)
        root.addWidget(bottom)

        # Disable right panel when nothing selected
        self._right_widgets = [
            self._trigger, self._name, self._desc, self._body, save_btn,
        ]
        self._set_form_enabled(False)

    @staticmethod
    def _label(text: str) -> QLabel:
        lbl = QLabel(text + ":")
        lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        return lbl

    # ── List management ───────────────────────────────────────────────────────

    def _refresh_list(self, keep_selection: str | None = None):
        self._list.blockSignals(True)
        self._list.clear()
        query = self._search.text().lower().strip()
        snippets = self.sm.get_all()
        for trigger in sorted(snippets):
            if query and query not in trigger.lower() and query not in snippets[trigger].get("name", "").lower():
                continue
            name = snippets[trigger].get("name", "")
            item = QListWidgetItem(f"{trigger}  —  {name}")
            item.setData(Qt.UserRole, trigger)
            self._list.addItem(item)
            if trigger == keep_selection:
                self._list.setCurrentItem(item)
        self._list.blockSignals(False)
        if keep_selection is None and self._list.count():
            self._list.setCurrentRow(0)

    def _filter_list(self, _):
        self._refresh_list(keep_selection=self._current_trigger)

    def _on_item_selected(self, current: QListWidgetItem, _previous):
        if not current:
            self._current_trigger = None
            self._set_form_enabled(False)
            return
        trigger = current.data(Qt.UserRole)
        self._load_snippet(trigger)

    def _load_snippet(self, trigger: str):
        data = self.sm.get(trigger)
        if not data:
            return
        self._current_trigger = trigger
        self._trigger.setText(trigger)
        self._name.setText(data.get("name", ""))
        self._desc.setText(data.get("description", ""))
        self._body.setPlainText(data.get("body", ""))
        self._set_form_enabled(True)

    def _set_form_enabled(self, enabled: bool):
        for w in self._right_widgets:
            w.setEnabled(enabled)
        if not enabled:
            self._trigger.clear()
            self._name.clear()
            self._desc.clear()
            self._body.clear()

    # ── CRUD actions ──────────────────────────────────────────────────────────

    def _new_snippet(self):
        # Clear form and let the user fill in a new snippet
        self._list.clearSelection()
        self._current_trigger = None
        self._trigger.clear()
        self._name.clear()
        self._desc.clear()
        self._body.clear()
        self._set_form_enabled(True)
        self._trigger.setFocus()

    def _save_snippet(self):
        trigger = self._trigger.text().strip().lower()
        name    = self._name.text().strip()
        body    = self._body.toPlainText()

        if not trigger:
            QMessageBox.warning(self, "Missing Trigger",
                                "Please enter a trigger word (e.g. sel).")
            self._trigger.setFocus()
            return
        if not body.strip():
            QMessageBox.warning(self, "Missing Body",
                                "Please enter the snippet body.")
            self._body.setFocus()
            return

        self.sm.upsert(trigger, name or trigger, body, self._desc.text())
        self._current_trigger = trigger
        self._refresh_list(keep_selection=trigger)
        self.snippets_changed.emit()

    def _delete_snippet(self):
        if not self._current_trigger:
            return
        reply = QMessageBox.question(
            self, "Delete Snippet",
            f"Delete snippet \"{self._current_trigger}\"?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self.sm.delete(self._current_trigger)
        self._current_trigger = None
        self._refresh_list()
        self.snippets_changed.emit()

    # ── Import / Export ───────────────────────────────────────────────────────

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Snippets", "qforge_snippets.json",
            "JSON files (*.json)")
        if not path:
            return
        try:
            self.sm.export_to_file(path)
            QMessageBox.information(self, "Export OK",
                                    f"Snippets exported to:\n{path}")
        except Exception as ex:
            QMessageBox.critical(self, "Export Failed", str(ex))

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Snippets", "",
            "JSON files (*.json)")
        if not path:
            return
        try:
            count = self.sm.import_from_file(path)
            self._refresh_list(keep_selection=self._current_trigger)
            self.snippets_changed.emit()
            QMessageBox.information(self, "Import OK",
                                    f"Imported {count} snippet(s).")
        except Exception as ex:
            QMessageBox.critical(self, "Import Failed", str(ex))

    def _restore_defaults(self):
        reply = QMessageBox.question(
            self, "Restore Defaults",
            "This will restore all built-in snippets (custom snippets are kept).\nContinue?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self.sm.reset_defaults()
        self._refresh_list(keep_selection=self._current_trigger)
        self.snippets_changed.emit()
