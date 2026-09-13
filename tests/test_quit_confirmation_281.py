"""Regression tests for issue #281: quitting the app used to unconditionally
accept the close event, with no check against an open manual transaction
(silently rolled back) or a tab whose query was still running in the
background.

MainWindow._confirm_quit_with_pending_work()/closeEvent() are exercised
against a lightweight stub (not a real MainWindow, whose __init__ opens a
connection-prompt dialog) carrying just `_panels` and the bound methods
under test.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QMessageBox, QTabWidget

from main import MainWindow
from ui.connection_panel import ConnectionPanel
from ui.sql_tab import SqlTab

_app = QApplication.instance() or QApplication([])


class _TabsHost:
    has_running_query = ConnectionPanel.has_running_query

    def __init__(self):
        self.tabs = QTabWidget()


def test_has_running_query_false_with_no_tabs_running():
    host = _TabsHost()
    for _ in range(2):
        tab = SqlTab()
        tab._query_running = False
        host.tabs.addTab(tab, "q")
    assert host.has_running_query() is False


def test_has_running_query_true_when_any_tab_is_running():
    host = _TabsHost()
    idle_tab = SqlTab()
    idle_tab._query_running = False
    host.tabs.addTab(idle_tab, "idle")
    running_tab = SqlTab()
    running_tab._query_running = True
    host.tabs.addTab(running_tab, "running")

    assert host.has_running_query() is True


class _PanelStub:
    def __init__(self, has_tx=False, has_running=False):
        self._has_tx = has_tx
        self._has_running = has_running

    def has_open_transactions(self):
        return self._has_tx

    def has_running_query(self):
        return self._has_running


class _MainWindowStub:
    _confirm_quit_with_pending_work = MainWindow._confirm_quit_with_pending_work

    def __init__(self, panels):
        self._panels = panels


def test_no_pending_work_returns_true_without_prompting(monkeypatch):
    def _fail_if_called(*a, **k):
        raise AssertionError("QMessageBox.question must not be shown when there's nothing to warn about")
    monkeypatch.setattr(QMessageBox, "question", _fail_if_called)

    win = _MainWindowStub([_PanelStub(), _PanelStub()])
    assert win._confirm_quit_with_pending_work() is True


def test_open_transaction_prompts_and_a_no_answer_blocks_quit(monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)

    win = _MainWindowStub([_PanelStub(has_tx=True)])
    assert win._confirm_quit_with_pending_work() is False


def test_running_query_prompts_and_a_yes_answer_allows_quit(monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)

    win = _MainWindowStub([_PanelStub(has_running=True)])
    assert win._confirm_quit_with_pending_work() is True


class _FakeCloseEvent:
    def __init__(self):
        self.accepted = False
        self.ignored = False

    def accept(self):
        self.accepted = True

    def ignore(self):
        self.ignored = True


class _CloseEventWindowStub:
    closeEvent = MainWindow.closeEvent

    def __init__(self, confirm_result):
        self._confirm_result = confirm_result
        self.geometry_saved = False
        self.session_saved = False

    def _confirm_quit_with_pending_work(self):
        return self._confirm_result

    def _save_window_geometry(self):
        self.geometry_saved = True

    def save_session(self):
        self.session_saved = True


def test_close_event_ignores_and_skips_saving_when_user_declines():
    win = _CloseEventWindowStub(confirm_result=False)
    event = _FakeCloseEvent()

    win.closeEvent(event)

    assert event.ignored is True
    assert event.accepted is False
    assert win.geometry_saved is False
    assert win.session_saved is False


def test_close_event_saves_and_accepts_when_confirmed():
    win = _CloseEventWindowStub(confirm_result=True)
    event = _FakeCloseEvent()

    win.closeEvent(event)

    assert event.accepted is True
    assert event.ignored is False
    assert win.geometry_saved is True
    assert win.session_saved is True
