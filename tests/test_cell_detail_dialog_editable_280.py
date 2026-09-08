"""Regression test for issue #280: the "Cell value" popup (Ctrl+Enter on a
grid cell — EditableTableWidget._open_cell_detail) was read-only, so a long
value (a report's SQL text, in the reported case) could be viewed in the
bigger text box but not edited there — only by going back to the tiny
inline cell editor. Save should now write the edited text back to the cell
through the same path (item.setText() -> itemChanged -> on_item_changed) a
normal in-grid edit uses, so dirty-tracking/undo/get_changes() all pick it
up identically."""
import os

import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QPlainTextEdit

from ui.editable_table import EditableTableWidget

_app = QApplication.instance() or QApplication([])


def _table():
    df = pd.DataFrame({"id": [1], "report_sql": ["SELECT 1"]})
    table = EditableTableWidget()
    table.load_data(df, table_name="stretchy_report")
    table.set_primary_key_columns(["id"])
    return table


def test_editing_the_popup_and_saving_updates_the_cell(monkeypatch):
    table = _table()
    item = table.item(0, 1)  # report_sql

    def fake_exec(self):
        te = self.findChild(QPlainTextEdit)
        te.setPlainText("SELECT 2")
        self.findChild(QDialogButtonBox).accepted.emit()
        return QDialog.Accepted

    monkeypatch.setattr(QDialog, "exec", fake_exec)

    table._open_cell_detail(item)

    assert item.text() == "SELECT 2"
    assert table.has_changes()
    changes = table.get_changes()
    assert any("SELECT 2" in sql for sql in changes["updates"])


def test_canceling_the_popup_leaves_the_cell_unchanged(monkeypatch):
    table = _table()
    item = table.item(0, 1)

    def fake_exec(self):
        te = self.findChild(QPlainTextEdit)
        te.setPlainText("something else entirely")
        self.reject()
        return QDialog.Rejected

    monkeypatch.setattr(QDialog, "exec", fake_exec)

    table._open_cell_detail(item)

    assert item.text() == "SELECT 1"
    assert not table.has_changes()


def test_saving_with_no_actual_change_does_not_mark_dirty(monkeypatch):
    table = _table()
    item = table.item(0, 1)

    def fake_exec(self):
        self.findChild(QDialogButtonBox).accepted.emit()
        return QDialog.Accepted

    monkeypatch.setattr(QDialog, "exec", fake_exec)

    table._open_cell_detail(item)

    assert item.text() == "SELECT 1"
    assert not table.has_changes()
