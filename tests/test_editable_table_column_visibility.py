"""Tests for issue #252: column visibility toggle (hide/show columns)."""
import os

import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from ui.editable_table import EditableTableWidget

_app = QApplication.instance() or QApplication([])


def _table(df=None):
    df = df if df is not None else pd.DataFrame({"a": [1], "b": [2], "c": [3]})
    t = EditableTableWidget()
    t.load_data(df, table_name="t")
    return t


# ── Basic hide/show ──────────────────────────────────────────────────────

def test_hide_column_hides_it_and_tracks_it():
    t = _table()
    t.hide_column("b")
    logical = t._logical_index_for_column_name("b")
    assert t.isColumnHidden(logical)
    assert "b" in t._hidden_columns


def test_show_column_reveals_it_again():
    t = _table()
    t.hide_column("b")
    t.show_column("b")
    logical = t._logical_index_for_column_name("b")
    assert not t.isColumnHidden(logical)
    assert "b" not in t._hidden_columns


def test_show_all_columns_reveals_everything():
    t = _table()
    t.hide_column("a")
    t.hide_column("c")
    t.show_all_columns()
    assert t._hidden_columns == set()
    assert not t.isColumnHidden(t._logical_index_for_column_name("a"))
    assert not t.isColumnHidden(t._logical_index_for_column_name("c"))


def test_hiding_unknown_column_is_a_noop():
    t = _table()
    t.hide_column("does_not_exist")
    assert t._hidden_columns == set()


def test_hide_is_display_only_does_not_affect_data_or_changes():
    t = _table()
    t.hide_column("b")
    t.item(0, 1).setText("99")  # still editable/addressable by index
    changes = t.get_changes()
    assert "b = '99'" in changes["updates"][0]


def test_copy_of_a_row_selection_skips_a_hidden_column():
    """Matches spreadsheet convention (Excel/Sheets): copying a row
    selection copies only the *visible* cells — Qt's own
    selectedItems() already excludes hidden items, so this falls out for
    free rather than needing separate handling."""
    t = _table()
    t.hide_column("b")
    from PySide6.QtWidgets import QApplication as QA
    t.selectRow(0)
    t.copy_to_clipboard()
    text = QA.clipboard().text()
    assert text.split("\t") == ["1", "3"]


# ── Reset/reapply across reloads ─────────────────────────────────────────

def test_hidden_state_resets_on_fresh_load_data_call():
    t = _table()
    t.hide_column("b")
    t.load_data(pd.DataFrame({"a": [1], "b": [2], "c": [3]}), table_name="t")
    assert t._hidden_columns == set()
    assert not t.isColumnHidden(t._logical_index_for_column_name("b"))


def test_stale_qt_hidden_flag_does_not_leak_across_reload():
    """The Qt-level per-section hidden flag on a given logical index
    otherwise survives clear()/setColumnCount() — must be explicitly reset,
    not just this widget's own _hidden_columns bookkeeping."""
    t = _table()
    t.hide_column("b")  # logical index 1
    t.load_data(pd.DataFrame({"x": [1], "y": [2], "z": [3]}), table_name="t2")
    assert not t.isColumnHidden(1)


# ── Persistence round-trip (get_layout_state / apply_layout_state) ──────

def test_layout_state_round_trips_hidden_columns():
    t = _table()
    t.hide_column("b")
    state = t.get_layout_state()
    assert state["hidden"] == ["b"]

    t2 = _table()
    t2.apply_layout_state(state)
    assert t2._hidden_columns == {"b"}
    assert t2.isColumnHidden(t2._logical_index_for_column_name("b"))


def test_hidden_column_width_is_not_recorded_as_zero():
    """A hidden column's Qt sectionSize() reads 0 — get_layout_state()
    must not persist that as its width, or re-showing it later would
    collapse it to 0px."""
    t = _table()
    hdr = t.horizontalHeader()
    hdr.resizeSection(1, 200)
    t.hide_column("b")
    state = t.get_layout_state()
    assert "b" not in state["widths"]


def test_layout_state_skips_hidden_columns_no_longer_present():
    t = _table()
    state = {"order": ["a", "b", "c"], "widths": {}, "frozen": 0, "hidden": ["b", "gone"]}
    t.apply_layout_state(state)  # must not raise
    assert t._hidden_columns == {"b"}


# ── Interaction with freeze/pin ──────────────────────────────────────────

def test_hide_action_refuses_a_currently_frozen_column():
    t = _table()
    t.set_frozen_columns(2)  # freezes a, b
    t.hide_column("a")
    assert "a" not in t._hidden_columns
    assert not t.isColumnHidden(t._logical_index_for_column_name("a"))


def test_hidden_column_stays_hidden_in_frozen_overlay_too():
    """A column hidden while NOT frozen, then swept into a later freeze
    range, must not silently reappear in the pinned overlay (issue #252 —
    the overlay is a second QTableView with independent hidden state)."""
    t = _table(pd.DataFrame({"a": [1], "b": [2], "c": [3], "d": [4]}))
    t.hide_column("b")               # b hidden, not yet frozen
    t.set_frozen_columns(3)          # freeze visual 0..2 — includes b's slot
    logical_b = t._logical_index_for_column_name("b")
    assert t._frozen_view.isColumnHidden(logical_b)


def test_unfreezing_does_not_reveal_a_hidden_column():
    t = _table()
    t.hide_column("a")
    t.set_frozen_columns(0)
    assert t.isColumnHidden(t._logical_index_for_column_name("a"))


# ── Interaction with drag-reorder ────────────────────────────────────────

def test_hide_survives_a_column_reorder():
    t = _table()
    t.hide_column("b")
    hdr = t.horizontalHeader()
    logical_a = t._logical_index_for_column_name("a")
    hdr.moveSection(hdr.visualIndex(logical_a), 2)  # move "a" to the end
    logical_b = t._logical_index_for_column_name("b")
    assert t.isColumnHidden(logical_b)
    assert "b" in t._hidden_columns
