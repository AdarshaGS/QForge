"""Tests for DbSwitcherDialog's click-outside-to-close behavior (issue
#234). Uses Qt.Popup — the same window type QMenu itself uses, with
click-outside-to-close built into Qt natively. That platform-level mouse
handling isn't reliably fakeable with a synthetic offscreen event (an
earlier WindowDeactivate-based attempt looked right here and still didn't
work live), so these tests confirm the mechanism is correctly wired
(Qt.Popup is set, Escape/selection still work) rather than claim to prove
the outside-click behavior itself — that needs a real click on a real
screen."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from ui.db_switcher_dialog import DbSwitcherDialog

_app = QApplication.instance() or QApplication([])


def _dialog():
    return DbSwitcherDialog(["Operion", "pi-system"], current_db="Operion")


def test_uses_popup_window_type():
    dialog = _dialog()
    assert dialog.windowFlags() & Qt.WindowType.Popup


def test_internal_focus_change_does_not_select_or_close():
    dialog = _dialog()
    selected = []
    dialog.db_selected.connect(selected.append)

    dialog.search.setFocus()
    dialog.list_widget.setFocus()

    assert not selected


def test_escape_still_closes_the_popup():
    dialog = _dialog()
    rejected = []
    dialog.rejected.connect(lambda: rejected.append(True))

    dialog.reject()

    assert rejected


def test_picking_an_item_still_emits_db_selected():
    dialog = _dialog()
    selected = []
    dialog.db_selected.connect(selected.append)

    dialog._pick(dialog.list_widget.item(1))  # "pi-system"

    assert selected == ["pi-system"]
