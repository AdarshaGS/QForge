"""Regression guard: after a fresh connection's modal ConnectionDialog
closes, the main window must be explicitly reactivated before the new
tab's editor grabs focus.

Verified live (not just here) against a real Cocoa window: closing a
modal QDialog.exec() leaves QApplication.focusWidget() permanently None
on macOS unless something calls activateWindow()/raise_() afterward —
editor.setFocus() deep inside ensure_at_least_one_tab() is a silent no-op
without it. QT_QPA_PLATFORM=offscreen (what CI runs under) doesn't
reproduce that native race, so this test only guards that the call
sequence stays in place — it can't re-verify the underlying OS behavior.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QDialog, QWidget

import main as main_mod
from services.query_history import QueryHistory
from services.saved_queries import SavedQueries

_app = QApplication.instance() or QApplication([])


class _StubDialog(QDialog):
    """Stands in for ConnectionDialog — a real QDialog so .exec() still
    goes through genuine Qt modal open/close, just pre-accepted."""
    def __init__(self, auto_connect_last=False, parent=None):
        super().__init__(parent)

    def exec(self):
        return QDialog.Accepted

    def get_selected_connection(self):
        return {"type": "sqlite", "name": "stub", "database": ":memory:"}


class _MainWindowStub(QWidget):
    """Just enough of MainWindow for _prompt_new_connection() to run —
    a real QWidget (ConnectionDialog/ConnectionPanel both get parented to
    it) but skips constructing the real window (menu bar, update checker,
    theme, ...), matching the duck-typed style already used for
    ConnectionPanel in test_connection_panel_focus_restore.py."""
    def __init__(self):
        super().__init__()
        self._panels = []
        self.query_history = QueryHistory()
        self.saved_queries = SavedQueries()
        self.current_theme = "dark"
        self.activate_calls = 0
        self.raise_calls = 0

    def activateWindow(self):
        self.activate_calls += 1
        super().activateWindow()

    def raise_(self):
        self.raise_calls += 1
        super().raise_()

    def _add_panel(self, panel):
        self._panels.append(panel)


def test_prompt_new_connection_reactivates_window_after_connecting(monkeypatch):
    monkeypatch.setattr(main_mod, "ConnectionDialog", _StubDialog)

    win = _MainWindowStub()
    main_mod.MainWindow._prompt_new_connection(win, allow_cancel_quit=False)

    assert len(win._panels) == 1
    assert win.activate_calls >= 1
    assert win.raise_calls >= 1
