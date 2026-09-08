"""Tests for issue #275: cross-connection Quick Search (#243) defaulting to
just the active connection instead of always mixing in every open one."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from ui.quick_search_dialog import QuickSearchDialog

_app = QApplication.instance() or QApplication([])


def _labels(dialog):
    return [dialog.results_list.item(i).text() for i in range(dialog.results_list.count())]


_ITEMS = [
    ("table", "m_staff", None, 0),   # Local
    ("table", "m_staff", None, 1),   # Staging — same name, different connection
]


def test_defaults_to_active_connection_only():
    dialog = QuickSearchDialog(
        _ITEMS, sources=["Local", "Staging"], default_source_idx=0,
    )
    dialog.search_input.setText("m_staff")

    assert dialog.results_list.count() == 1
    # Scoped to one connection: no "(Local)"/"(Staging)" disambiguation
    # suffix needed since there's nothing to disambiguate from.
    assert _labels(dialog) == ["m_staff"]


def test_checkbox_present_only_with_multiple_sources_and_a_default():
    multi = QuickSearchDialog(_ITEMS, sources=["Local", "Staging"], default_source_idx=0)
    assert multi.scope_checkbox is not None
    assert multi.scope_checkbox.isChecked() is False

    single_source = QuickSearchDialog(_ITEMS, sources=["Local"], default_source_idx=0)
    assert single_source.scope_checkbox is None

    no_default = QuickSearchDialog(_ITEMS, sources=["Local", "Staging"])
    assert no_default.scope_checkbox is None


def test_checking_the_box_broadens_to_all_connections():
    dialog = QuickSearchDialog(_ITEMS, sources=["Local", "Staging"], default_source_idx=0)

    dialog.scope_checkbox.setChecked(True)
    dialog.search_input.setText("m_staff")

    assert dialog.results_list.count() == 2
    labels = _labels(dialog)
    assert any("(Local)" in l for l in labels)
    assert any("(Staging)" in l for l in labels)


def test_unchecking_returns_to_scoped_results():
    dialog = QuickSearchDialog(_ITEMS, sources=["Local", "Staging"], default_source_idx=1)
    dialog.scope_checkbox.setChecked(True)
    dialog.search_input.setText("m_staff")
    assert dialog.results_list.count() == 2

    dialog.scope_checkbox.setChecked(False)

    assert dialog.results_list.count() == 1
    assert _labels(dialog) == ["m_staff"]


def test_single_connection_caller_is_unaffected():
    """A plain single-connection Quick Search (no sources/default_source_idx
    at all — ui.connection_panel.ConnectionPanel.show_quick_search's own
    call shape) keeps showing everything, same as before this issue."""
    dialog = QuickSearchDialog([("table", "orders", None)])
    dialog.filter_items("orders")
    assert dialog.results_list.count() == 1
    assert dialog.scope_checkbox is None
