"""Regression tests for the redesigned connection-list sidebar: quick
filters (All/Recent/Favorites, no "Environments" section), no per-row
colored dot, and the setItemWidget rows this introduced — including the
orphaned-widget bug those rows turned out to have (issue found live: a
stray colored block appears over/behind unrelated rows after a second
load_connections() on the same tree, e.g. from toggling a favorite)."""
import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QApplication, QWidget

from services import preferences
from ui.connection_dialog import ConnectionDialog

_app = QApplication.instance() or QApplication([])


@pytest.fixture
def isolated_dialog_factory(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_cwd = tmp_path / "cwd"
    fake_home.mkdir()
    fake_cwd.mkdir()

    connection_file = str(tmp_path / "connections.json")
    monkeypatch.setattr(ConnectionDialog, "CONNECTION_FILE", connection_file)
    monkeypatch.setattr(ConnectionDialog, "LAST_CONNECTION_FILE", str(tmp_path / "last_connection.json"))
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(fake_home))
    monkeypatch.setattr(os, "getcwd", lambda: str(fake_cwd))
    monkeypatch.setattr(preferences, "_FILE", str(tmp_path / "preferences.json"))

    def _write(conns):
        with open(connection_file, "w") as f:
            json.dump(conns, f)

    def _make(conns=None):
        if conns is not None:
            _write(conns)
        return ConnectionDialog()

    return _make


_CONNS = [
    {"name": "CIM UAT 0", "type": "mysql", "host": "10.127.0.0", "group": "LMS UAT"},
    {"name": "CIM UAT 1", "type": "mysql", "host": "10.127.0.1", "group": "LMS UAT"},
    {"name": "CIM UAT 2", "type": "postgresql", "host": "10.127.0.2", "group": "LMS UAT"},
]


def _row_widget_count(dlg) -> int:
    return len([c for c in dlg.connection_tree.viewport().children() if isinstance(c, QWidget)])


def test_quick_filters_are_all_recent_favorites_only(isolated_dialog_factory):
    dlg = isolated_dialog_factory(_CONNS)
    assert set(dlg._quick_filter_rows.keys()) == {"all", "recent", "favorites"}


def test_no_environments_section_or_env_dot_icon(isolated_dialog_factory):
    """Both explicitly out of scope for this redesign: no smart-folder
    section for environments, and no colored dot (env text badge in the
    row subtitle is still fine and unrelated — see ai/ui-design.md)."""
    dlg = isolated_dialog_factory(_CONNS)
    assert not hasattr(dlg, "environment_filter_box")
    group_item = dlg.connection_tree.topLevelItem(0)
    child = group_item.child(0)
    assert child.icon(0).isNull()


def test_toggle_favorite_updates_star_and_favorites_count(isolated_dialog_factory):
    dlg = isolated_dialog_factory(_CONNS)
    assert dlg._quick_filter_rows["favorites"].count_label.text() == "0"

    dlg._toggle_favorite(0)

    assert dlg.connections[0]["favorite"] is True
    assert dlg._quick_filter_rows["favorites"].count_label.text() == "1"


def test_reloading_the_tree_does_not_leave_orphaned_row_widgets(isolated_dialog_factory):
    """The actual bug: QTreeWidget.clear() doesn't clean up setItemWidget
    widgets on its own, so repeated reloads (e.g. one _toggle_favorite per
    row) used to double the sidebar's stray widget count each time,
    rendering as ghost text/color blocks over unrelated rows."""
    dlg = isolated_dialog_factory(_CONNS)
    # One widget per connection row plus one per group header (all three
    # _CONNS share the "LMS UAT" group).
    before = _row_widget_count(dlg)
    assert before == len(_CONNS) + 1

    dlg._toggle_favorite(0)
    _app.sendPostedEvents(None, QEvent.DeferredDelete)
    dlg._toggle_favorite(1)
    _app.sendPostedEvents(None, QEvent.DeferredDelete)

    assert _row_widget_count(dlg) == before


def test_selecting_a_row_highlights_it_and_deselecting_clears_it(isolated_dialog_factory):
    dlg = isolated_dialog_factory(_CONNS)
    dlg._select_connection_by_index(0)
    dlg._select_connection_by_index(1)

    item0 = dlg._find_tree_item(0)
    item1 = dlg._find_tree_item(1)
    style0 = dlg.connection_tree.itemWidget(item0, 0).styleSheet()
    style1 = dlg.connection_tree.itemWidget(item1, 0).styleSheet()

    assert "transparent" in style0
    assert "transparent" not in style1


def test_search_matches_against_stored_row_text_not_visible_label(isolated_dialog_factory):
    """Column 0's own text is intentionally left blank for display (the
    row widget covers it) but still carries the searchable string in a
    custom data role — _apply_filters must read that, not child.text(0)."""
    dlg = isolated_dialog_factory(_CONNS)
    dlg.connection_search.setText("uat 2")
    visible = dlg._visible_connection_items()
    assert len(visible) == 1
    assert visible[0].data(0, Qt.UserRole) == 2
