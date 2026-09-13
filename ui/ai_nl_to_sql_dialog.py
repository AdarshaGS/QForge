"""
AiNlToSqlDialog
===============
"✨ Ask AI" — natural-language-to-SQL (issue #344). Opened from SqlTab's
toolbar; asks the user's local Claude Code CLI (services/ai_client.py) to
turn a plain-English request into SQL, using the connection's known
table/view names plus narrow schema context for any of them the request
seems to mention. Free for everyone — gated only by whether the CLI is
installed and authenticated (ui/ai_availability_widget.py), never
services/entitlements.py.

The generated SQL is always previewed here first; "Insert" inserts it at
the caller's editor cursor, "Discard" just closes the dialog — it never
silently overwrites anything.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QVBoxLayout,
)

from services import ai_client, ai_prompts
from ui.ai_async import AiCallManager
from ui.ai_availability_widget import AiAvailabilityWidget
from ui.code_editor import CodeEditor
from ui.sql_highlighter import SqlHighlighter

_MUTED = "#8e8e93"
_FAIL_COLOR = "#f48771"


class AiNlToSqlDialog(QDialog):
    # Emitted when the user clicks Insert — the caller (ConnectionPanel)
    # inserts *sql* into the requesting tab's editor at the cursor. Never
    # emitted on Discard/close, and the dialog never touches the editor
    # itself — it doesn't hold a reference to it.
    sql_accepted = Signal(str)

    def __init__(self, db_service, dialect: str, known_tables: list,
                 column_details: dict = None, foreign_keys: dict = None, parent=None):
        super().__init__(parent)
        self._db = db_service
        self._dialect = dialect or ""
        # ConnectionPanel's already-loaded autocomplete caches — used to
        # build full schema context for every known table (not just ones a
        # name-matching heuristic spots in the free-text request; see
        # services.ai_prompts.build_schema_context_from_cache). A plain-
        # English request routinely doesn't say a table name verbatim at
        # all, and without this the model previously had nothing to go on
        # but guessing plausible-sounding column names.
        self._column_details = column_details or {}
        self._foreign_keys = foreign_keys or {}
        self._known_tables = known_tables or []
        self._call_mgr = AiCallManager(self)
        self._pending_sql = None

        self.setWindowTitle("Ask AI")
        self.setMinimumWidth(520)
        self.setModal(False)

        root = QVBoxLayout(self)

        self._availability = AiAvailabilityWidget(compact=True)
        self._availability.availability_changed.connect(lambda _a: self._update_gate())
        root.addWidget(self._availability)

        root.addWidget(QLabel("What SQL do you need?"))
        self._request_edit = QLineEdit()
        self._request_edit.setPlaceholderText(
            "e.g. show me the 10 most recent orders over $100")
        self._request_edit.returnPressed.connect(self._start_generate)
        root.addWidget(self._request_edit)

        btn_row = QHBoxLayout()
        self._status_lbl = QLabel("")
        self._status_lbl.setWordWrap(True)
        self._status_lbl.setStyleSheet(f"color:{_MUTED}; font-size:12px;")
        btn_row.addWidget(self._status_lbl, 1)
        self._generate_btn = QPushButton("Generate")
        self._generate_btn.setObjectName("primaryBtn")
        self._generate_btn.setToolTip("Uses your own Claude account/usage.")
        self._generate_btn.clicked.connect(self._start_generate)
        btn_row.addWidget(self._generate_btn)
        root.addLayout(btn_row)

        self._preview_editor = CodeEditor()
        self._preview_editor.setReadOnly(True)
        self._preview_editor.setPlaceholderText("Generated SQL will appear here for review…")
        self._preview_editor.setMinimumHeight(100)
        SqlHighlighter(self._preview_editor.document())
        root.addWidget(self._preview_editor)

        self._explanation_lbl = QLabel("")
        self._explanation_lbl.setWordWrap(True)
        self._explanation_lbl.setStyleSheet(f"color:{_MUTED}; font-size:12px;")
        root.addWidget(self._explanation_lbl)

        action_row = QHBoxLayout()
        self._insert_btn = QPushButton("Insert")
        self._insert_btn.setEnabled(False)
        self._insert_btn.clicked.connect(self._insert)
        self._discard_btn = QPushButton("Discard")
        self._discard_btn.clicked.connect(self.reject)
        action_row.addStretch()
        action_row.addWidget(self._discard_btn)
        action_row.addWidget(self._insert_btn)
        root.addLayout(action_row)

        self._update_gate()

    def _update_gate(self):
        avail = self._availability.availability
        ready = ai_client.is_enabled() and bool(avail and avail.authenticated)
        if not self._call_mgr.busy:
            self._generate_btn.setEnabled(ready)
        if not ai_client.is_enabled():
            self._status_lbl.setText("Enable AI features in Preferences to use this.")
        elif avail and not avail.authenticated:
            self._status_lbl.setText(avail.detail)
        else:
            self._status_lbl.setText("")

    def _start_generate(self):
        request = self._request_edit.text().strip()
        if not request or self._call_mgr.busy or not self._generate_btn.isEnabled():
            return
        self._generate_btn.setEnabled(False)
        self._generate_btn.setText("Generating…")
        self._status_lbl.setText("Asking Claude…")
        self._status_lbl.setStyleSheet(f"color:{_MUTED}; font-size:12px;")
        self._insert_btn.setEnabled(False)
        self._pending_sql = None

        schema_ctx = ai_prompts.build_schema_context_from_cache(
            self._column_details, self._foreign_keys)
        prompt, schema = ai_prompts.build_nl_to_sql_prompt(
            request, self._dialect, schema_ctx, known_tables=self._known_tables)
        self._call_mgr.start(prompt, on_done=self._on_generate_done, json_schema=schema, timeout=45.0)

    def _on_generate_done(self, result):
        self._generate_btn.setText("Generate")
        self._update_gate()

        if not result.ok or not result.data or not result.data.get("sql"):
            self._status_lbl.setText(f"Couldn't generate SQL: {result.error or 'unknown error'}")
            self._status_lbl.setStyleSheet(f"color:{_FAIL_COLOR}; font-size:12px;")
            self._preview_editor.clear()
            self._explanation_lbl.setText("")
            return

        self._pending_sql = result.data["sql"]
        self._preview_editor.setPlainText(self._pending_sql)
        caveats = result.data.get("caveats") or []
        explanation = result.data.get("explanation") or ""
        if caveats:
            explanation = explanation + "\n\n" + "\n".join(f"• {c}" for c in caveats)
        self._explanation_lbl.setText(explanation)
        self._status_lbl.setText("Review the SQL below, then Insert or Discard.")
        self._status_lbl.setStyleSheet(f"color:{_MUTED}; font-size:12px;")
        self._insert_btn.setEnabled(True)

    def _insert(self):
        if not self._pending_sql:
            return
        self.sql_accepted.emit(self._pending_sql)
        self.close()
