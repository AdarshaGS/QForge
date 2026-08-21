"""Regression test for issue #173: _prompt_new_connection() must
accumulate dialog.exec()'s own blocking time (real human think-time, not
app overhead) into self._dialog_wait_ms across multiple loop iterations
(an invalid/empty selection re-prompts) — not overwrite it with just the
last one — so MainWindow.__init__ can subtract it back out of the
startup-stage timings.

The single-exec() case (does dialog_wait move at all) is covered in
test_main_window_focus_after_connect.py instead of here: that test is
already the one site in the suite that constructs a real
ConnectionPanel/SqlTab via this stub pattern, and a second such site
anywhere else in the suite crashes with a shiboken "object already
deleted" lifecycle error, regardless of which two tests they are. This
file's dialog never reaches a real config, so it never constructs a real
panel."""
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QDialog, QWidget

import main as main_mod
from services.query_history import QueryHistory
from services.saved_queries import SavedQueries

_app = QApplication.instance() or QApplication([])


class _RepromptThenCancelDialog(QDialog):
    """First call: accepted but with no connection selected
    (get_selected_connection() -> None), forcing _prompt_new_connection's
    while-loop to reopen the dialog. Second call: rejected (Cancel),
    ending the loop."""

    _DELAY_S = 0.03
    _calls = 0

    def __init__(self, auto_connect_last=False, parent=None):
        super().__init__(parent)

    def exec(self):
        time.sleep(self._DELAY_S)
        type(self)._calls += 1
        return QDialog.Rejected if type(self)._calls >= 2 else QDialog.Accepted

    def get_selected_connection(self):
        return None


class _MainWindowStub(QWidget):
    def __init__(self):
        super().__init__()
        self._panels = []
        self.query_history = QueryHistory()
        self.saved_queries = SavedQueries()
        self.current_theme = "dark"
        self._dialog_wait_ms = 0.0

    def _add_panel(self, panel):
        self._panels.append(panel)


def test_dialog_wait_accumulates_across_reprompt_loop_iterations(monkeypatch):
    _RepromptThenCancelDialog._calls = 0
    monkeypatch.setattr(main_mod, "ConnectionDialog", _RepromptThenCancelDialog)

    win = _MainWindowStub()
    main_mod.MainWindow._prompt_new_connection(win, allow_cancel_quit=False)

    # Two exec() calls happened (empty selection, then Cancel) — both
    # delays must be counted, not just the last one.
    assert _RepromptThenCancelDialog._calls == 2
    assert win._dialog_wait_ms >= _RepromptThenCancelDialog._DELAY_S * 1000 * 2
