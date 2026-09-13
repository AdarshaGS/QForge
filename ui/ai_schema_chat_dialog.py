"""
AiSchemaChatDialog
===================
"Ask About Schema…" (issue #345) — a persistent chat panel for asking
questions about the connected database's schema/data model, backed by the
user's local Claude Code CLI (services/ai_client.py). Free for everyone —
gated only by whether the CLI is installed and authenticated
(ui/ai_availability_widget.py), never services/entitlements.py.

One instance per ConnectionPanel, cached and reopened rather than recreated
each time (see ConnectionPanel.open_schema_chat) so the conversation
survives closing and reopening the window within a connection session.
Each turn is still a stateless one-shot `claude -p` call — prior turns are
replayed as prompt text (services/ai_prompts.build_schema_chat_prompt), not
CLI session/--resume state — keeping the subprocess contract identical
across every AI Assistance capability rather than adding a second,
session-stateful code path.

Schema context is seeded up front from ConnectionPanel's already-loaded
autocomplete caches (services.ai_prompts.build_schema_context_from_cache),
not accumulated by matching table names against each question — a schema
question routinely doesn't name every table it's really about ("what
references orders?" never says "customers"), and without real context the
model was filling gaps with guesses instead of reporting the real schema.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from services import ai_client, ai_prompts
from ui.ai_async import AiCallManager
from ui.ai_availability_widget import AiAvailabilityWidget

_MUTED = "#8e8e93"
_FAIL_COLOR = "#f48771"
_USER_BG = "#0A84FF"
_ASSISTANT_BG = "#2c2c2e"


def _bubble(text: str, *, is_user: bool) -> QWidget:
    row = QWidget()
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 2, 0, 2)

    bubble = QFrame()
    bg = _USER_BG if is_user else _ASSISTANT_BG
    fg = "#ffffff" if is_user else "#e5e5ea"
    bubble.setStyleSheet(f"QFrame {{ background:{bg}; border-radius:10px; }}")
    v = QVBoxLayout(bubble)
    v.setContentsMargins(10, 8, 10, 8)
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet(f"color:{fg}; font-size:12px; background:transparent;")
    lbl.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
    v.addWidget(lbl)

    if is_user:
        h.addStretch(1)
        h.addWidget(bubble, 4)
    else:
        h.addWidget(bubble, 4)
        h.addStretch(1)
    return row


class AiSchemaChatDialog(QDialog):
    def __init__(self, db_service, column_details: dict = None,
                 foreign_keys: dict = None, parent=None):
        super().__init__(parent)
        self._db = db_service
        self._call_mgr = AiCallManager(self)
        self._history: list = []          # [(question, answer), ...]
        # Seeded up front from ConnectionPanel's already-loaded autocomplete
        # caches (services.ai_prompts.build_schema_context_from_cache),
        # rather than accumulated turn-by-turn by matching table names
        # against the user's question text — a schema-exploration question
        # ("what references orders?") routinely doesn't name every
        # relevant table, and without real schema context up front the
        # model was guessing plausible-sounding columns instead of
        # reporting the real ones.
        self._schema_ctx: dict = (
            ai_prompts.build_schema_context_from_cache(column_details, foreign_keys) or {})

        self.setWindowTitle("Ask About Schema")
        self.setMinimumSize(480, 560)
        self.setModal(False)

        root = QVBoxLayout(self)

        self._availability = AiAvailabilityWidget(compact=True)
        self._availability.availability_changed.connect(lambda _a: self._update_gate())
        root.addWidget(self._availability)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.NoFrame)
        self._transcript_widget = QWidget()
        self._transcript_layout = QVBoxLayout(self._transcript_widget)
        self._transcript_layout.setContentsMargins(4, 4, 4, 4)
        self._transcript_layout.setSpacing(6)
        self._transcript_layout.addStretch(1)
        self._scroll.setWidget(self._transcript_widget)
        root.addWidget(self._scroll, 1)

        self._status_lbl = QLabel("")
        self._status_lbl.setWordWrap(True)
        self._status_lbl.setStyleSheet(f"color:{_MUTED}; font-size:11px;")
        root.addWidget(self._status_lbl)

        input_row = QHBoxLayout()
        self._input_edit = QLineEdit()
        self._input_edit.setPlaceholderText("Ask about the schema, e.g. \"what references orders?\"")
        self._input_edit.returnPressed.connect(self._send)
        input_row.addWidget(self._input_edit, 1)
        self._send_btn = QPushButton("Send")
        self._send_btn.setObjectName("primaryBtn")
        self._send_btn.setToolTip("Uses your own Claude account/usage.")
        self._send_btn.clicked.connect(self._send)
        input_row.addWidget(self._send_btn)
        root.addLayout(input_row)

        self._update_gate()

    def _update_gate(self):
        avail = self._availability.availability
        ready = ai_client.is_enabled() and bool(avail and avail.authenticated)
        if not self._call_mgr.busy:
            self._send_btn.setEnabled(ready)
            self._input_edit.setEnabled(ready)
        if not ai_client.is_enabled():
            self._status_lbl.setText("Enable AI features in Preferences to use this.")
        elif avail and not avail.authenticated:
            self._status_lbl.setText(avail.detail)
        else:
            self._status_lbl.setText("")

    def _add_bubble(self, text: str, *, is_user: bool):
        # insert before the trailing stretch so new bubbles append at the
        # bottom instead of after it
        self._transcript_layout.insertWidget(
            self._transcript_layout.count() - 1, _bubble(text, is_user=is_user))
        self._scroll.verticalScrollBar().setValue(self._scroll.verticalScrollBar().maximum())

    def _send(self):
        question = self._input_edit.text().strip()
        if not question or self._call_mgr.busy or not self._send_btn.isEnabled():
            return
        self._input_edit.clear()
        self._add_bubble(question, is_user=True)
        self._send_btn.setEnabled(False)
        self._input_edit.setEnabled(False)
        self._status_lbl.setText("Asking Claude…")
        self._status_lbl.setStyleSheet(f"color:{_MUTED}; font-size:11px;")

        prompt = ai_prompts.build_schema_chat_prompt(
            question, self._schema_ctx or None, history=self._history)
        self._call_mgr.start(
            prompt, on_done=lambda result, q=question: self._on_response(q, result), timeout=45.0)

    def _on_response(self, question: str, result):
        self._update_gate()
        if not result.ok:
            self._add_bubble(f"Couldn't get a response: {result.error or 'unknown error'}",
                              is_user=False)
            self._status_lbl.setText("")
            return
        answer = result.text or "(empty response)"
        self._add_bubble(answer, is_user=False)
        self._history.append((question, answer))
        self._status_lbl.setText("")
