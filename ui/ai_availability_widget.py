"""
ai_availability_widget
=======================
Small reusable "is AI available right now" status chip + Recheck button.
Used by the Preferences dialog's AI Assistance section and, compactly, by
any feature entry point that needs to show a disabled/gated state inline
instead of an upgrade dialog (this feature is free for everyone — gating
is a runtime capability check, not services/entitlements.py).

The check itself shells out to `claude auth status` (see
services/ai_client.check_availability), so even this goes through a worker
thread rather than blocking the widget's paint/show event — and, since
this widget starts that check immediately on construction (before any
user action), it's especially important that closing/destroying it before
the check finishes can't crash the app; see ui/ai_async.run_worker's
docstring for why the thread isn't Qt-parented to this widget.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from services import ai_client
from ui.ai_async import run_worker

_READY_COLOR = "#30d158"
_WARN_COLOR = "#ff9f0a"
_MUTED = "#8e8e93"


class _AvailabilityWorker(QObject):
    done = Signal(object)  # AiAvailability

    def run(self):
        self.done.emit(ai_client.check_availability())


class AiAvailabilityWidget(QWidget):
    availability_changed = Signal(object)  # AiAvailability

    def __init__(self, parent=None, compact: bool = False):
        super().__init__(parent)
        self._compact = compact
        self._thread = None
        self._worker = None
        self._last: ai_client.AiAvailability | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._status_lbl = QLabel("Checking Claude CLI…")
        self._status_lbl.setStyleSheet(f"color:{_MUTED}; font-size:12px;")
        layout.addWidget(self._status_lbl, 1)

        self._recheck_btn = QPushButton("Recheck")
        self._recheck_btn.setFixedHeight(24)
        self._recheck_btn.clicked.connect(self.recheck)
        layout.addWidget(self._recheck_btn)

        self.recheck()

    @property
    def availability(self) -> "ai_client.AiAvailability | None":
        return self._last

    def recheck(self):
        if self._thread is not None:
            return
        self._recheck_btn.setEnabled(False)
        self._status_lbl.setText("Checking Claude CLI…")
        self._status_lbl.setStyleSheet(f"color:{_MUTED}; font-size:12px;")

        self._thread, self._worker = run_worker(_AvailabilityWorker(), self._on_done)

    def _on_done(self, availability: "ai_client.AiAvailability"):
        self._thread = None
        self._worker = None
        self._last = availability
        self._recheck_btn.setEnabled(True)

        if availability.authenticated:
            email = f" — logged in as {availability.auth_email}" if availability.auth_email else ""
            self._status_lbl.setText(f"●  Claude CLI ready{email}")
            self._status_lbl.setStyleSheet(f"color:{_READY_COLOR}; font-size:12px;")
        elif availability.installed:
            self._status_lbl.setText("●  Claude CLI installed, not logged in")
            self._status_lbl.setStyleSheet(f"color:{_WARN_COLOR}; font-size:12px;")
        else:
            self._status_lbl.setText("●  Claude Code CLI not found")
            self._status_lbl.setStyleSheet(f"color:{_WARN_COLOR}; font-size:12px;")

        self.availability_changed.emit(availability)
