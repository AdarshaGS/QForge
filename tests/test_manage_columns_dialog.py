"""Tests for the bulk column hide/show flow (issue #252 follow-up):
ColumnSelectionDialog's checked_columns/disabled_columns params, and
EditableTableWidget.manage_columns() wiring them up.
"""
import os

import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QDialog

from ui.column_selection_dialog import ColumnSelectionDialog
from ui.editable_table import EditableTableWidget

_app = QApplication.instance() or QApplication([])


def _table(df=None):
    df = df if df is not None else pd.DataFrame({"a": [1], "b": [2], "c": [3]})
    t = EditableTableWidget()
    t.load_data(df, table_name="t")
    return t


# ── ColumnSelectionDialog: new params, existing behavior preserved ──────

def test_default_behavior_unchanged_everything_checked():
    """The original export call site passes neither checked_columns nor
    disabled_columns — must keep starting every box checked."""
    dlg = ColumnSelectionDialog(["a", "b", "c"])
    assert set(dlg.selected_columns()) == {"a", "b", "c"}
    assert dlg.windowTitle() == "Select Columns to Export"


def test_checked_columns_controls_initial_state():
    dlg = ColumnSelectionDialog(["a", "b", "c"], checked_columns=["a", "c"])
    assert dlg._checks["a"].isChecked()
    assert not dlg._checks["b"].isChecked()
    assert dlg._checks["c"].isChecked()


def test_disabled_columns_cannot_be_unchecked_by_the_user():
    dlg = ColumnSelectionDialog(
        ["a", "b"], checked_columns=["a", "b"], disabled_columns=["a"],
        disabled_tooltip="pinned",
    )
    assert not dlg._checks["a"].isEnabled()
    assert dlg._checks["a"].toolTip() == "pinned"
    assert dlg._checks["b"].isEnabled()


def test_custom_title_and_label():
    dlg = ColumnSelectionDialog(["a"], title="Manage Columns", label="Visible columns:")
    assert dlg.windowTitle() == "Manage Columns"


# ── EditableTableWidget.manage_columns() ─────────────────────────────────

def test_manage_columns_hides_unchecked_and_keeps_checked_visible(monkeypatch):
    t = _table()

    def fake_exec(self):
        self._checks["b"].setChecked(False)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(ColumnSelectionDialog, "exec", fake_exec)

    t.manage_columns()

    assert t._hidden_columns == {"b"}
    assert t.isColumnHidden(t._logical_index_for_column_name("b"))
    assert not t.isColumnHidden(t._logical_index_for_column_name("a"))


def test_manage_columns_can_unhide_multiple_at_once(monkeypatch):
    t = _table(pd.DataFrame({"a": [1], "b": [2], "c": [3], "d": [4]}))
    t.hide_column("b")
    t.hide_column("c")

    def fake_exec(self):
        self._checks["b"].setChecked(True)
        self._checks["c"].setChecked(True)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(ColumnSelectionDialog, "exec", fake_exec)

    t.manage_columns()

    assert t._hidden_columns == set()


def test_manage_columns_cancel_makes_no_changes(monkeypatch):
    t = _table()

    def fake_exec(self):
        self._checks["b"].setChecked(False)
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(ColumnSelectionDialog, "exec", fake_exec)

    t.manage_columns()

    assert t._hidden_columns == set()


def test_manage_columns_disables_frozen_columns_in_the_dialog(monkeypatch):
    t = _table(pd.DataFrame({"a": [1], "b": [2], "c": [3]}))
    t.set_frozen_columns(1)  # freezes "a"

    captured = {}

    def fake_exec(self):
        captured["a_enabled"] = self._checks["a"].isEnabled()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(ColumnSelectionDialog, "exec", fake_exec)

    t.manage_columns()
    assert captured["a_enabled"] is False


def test_manage_columns_ignores_unchecking_a_frozen_column(monkeypatch):
    """Even if the checkbox were somehow unchecked, a frozen column must
    not actually be hidden — same rule hide_column() already enforces."""
    t = _table(pd.DataFrame({"a": [1], "b": [2], "c": [3]}))
    t.set_frozen_columns(1)

    def fake_exec(self):
        self._checks["a"].setChecked(False)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(ColumnSelectionDialog, "exec", fake_exec)

    t.manage_columns()
    assert "a" not in t._hidden_columns
