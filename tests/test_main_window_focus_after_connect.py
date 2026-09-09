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
import time
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QDialog, QWidget

import main as main_mod
from services.db_service import DbService
from ui.connection_panel import ConnectionPanel
from services.query_history import QueryHistory
from services.saved_queries import SavedQueries

_app = QApplication.instance() or QApplication([])


def _fake_connect(self, config):
    """Stands in for DbService.connect() — sets exactly the state a real
    connect() would (db_type/connection/connection_name/_config) without
    touching a real socket, so this test (about focus/activate/raise call
    sequencing, not connectivity) never depends on a live database."""
    self.db_type = config.get("type", "mysql").lower()
    self.connection = object()
    self.connection_name = config["name"]
    self._config = config
    self.read_only = bool(config.get("read_only"))


class _StubDialog(QDialog):
    """Stands in for ConnectionDialog — a real QDialog so .exec() still
    goes through genuine Qt modal open/close, just pre-accepted.

    _DELAY_S is a deliberate, measurable exec() delay: this is the one
    place in the suite that constructs a real ConnectionPanel/SqlTab via
    this stub pattern — a second such site elsewhere in the suite crashes
    with a shiboken "object already deleted" lifecycle error regardless
    of which two tests they are, so issue #173's dialog_wait accumulation
    is verified here too rather than in a separate test."""
    _DELAY_S = 0.05

    def __init__(self, auto_connect_last=False, parent=None):
        super().__init__(parent)

    def exec(self):
        time.sleep(self._DELAY_S)
        return QDialog.Accepted

    def get_selected_connection(self):
        return {"type": "mysql", "name": "stub"}


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
        self._dialog_wait_ms = 0.0  # normally set in MainWindow.__init__ (issue #173)
        # issue #281: _prompt_new_connection() now delegates the actual
        # connect+add-panel work to this real MainWindow method (shared
        # with opening a database as a new tab), so the stub needs it too.
        self._connect_and_add_panel = types.MethodType(
            main_mod.MainWindow._connect_and_add_panel, self)

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
    monkeypatch.setattr(DbService, "connect", _fake_connect)

    win = _MainWindowStub()
    main_mod.MainWindow._prompt_new_connection(win, allow_cancel_quit=False)

    assert len(win._panels) == 1
    assert win.activate_calls >= 1
    assert win.raise_calls >= 1
    # issue #173: the modal dialog's own exec() time (real human
    # think-time) must be accumulated for MainWindow.__init__ to subtract
    # back out of the startup-stage timings.
    assert win._dialog_wait_ms >= _StubDialog._DELAY_S * 1000


def test_prompt_new_connection_realizes_first_tab_before_panel_is_shown(monkeypatch):
    """Regression guard for issue #25's un-mitigated case: opening a new
    connection (not just the app's very first one) must realize the
    panel's first tab BEFORE _add_panel ever makes it a visible page of
    the main window's stack. Unlike startup/session-restore, this can
    happen at any point in the app's life — including while the main
    window is already native full-screen — so it can't rely on "before
    the window can be full screen" timing; it needs this call order
    instead (see ConnectionPanel.ensure_at_least_one_tab)."""
    monkeypatch.setattr(main_mod, "ConnectionDialog", _StubDialog)
    monkeypatch.setattr(DbService, "connect", _fake_connect)

    calls = []
    real_ensure = ConnectionPanel.ensure_at_least_one_tab

    def _tracked_ensure(self):
        calls.append("ensure_at_least_one_tab")
        return real_ensure(self)

    monkeypatch.setattr(ConnectionPanel, "ensure_at_least_one_tab", _tracked_ensure)

    win = _MainWindowStub()
    real_add_panel = win._add_panel

    def _tracked_add_panel(panel):
        calls.append("_add_panel")
        return real_add_panel(panel)

    win._add_panel = _tracked_add_panel

    main_mod.MainWindow._prompt_new_connection(win, allow_cancel_quit=False)

    assert calls == ["ensure_at_least_one_tab", "_add_panel"]
