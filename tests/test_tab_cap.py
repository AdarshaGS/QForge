"""Tests for the tab cap (issue #154): query tabs and table-data tabs must
count together per connection panel against Limit.MAX_QUERY_TABS, not query
tabs alone — the single choke point being services/entitlements.py +
ui/upgrade_dialog.require_under_limit(), the same gate every other Free/Pro
limit in the app goes through (issue #82)."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from services import entitlements as entitlements_module
from services.entitlements import Limit
from services.db_service import DbService
from services.query_history import QueryHistory
from services.saved_queries import SavedQueries
from ui import upgrade_dialog
from ui.connection_panel import ConnectionPanel
from ui.sql_tab import SqlTab

_app = QApplication.instance() or QApplication([])


def _make_panel(monkeypatch):
    # Force Free edition regardless of any real license on the machine
    # running these tests (test_entitlements.py's own pattern).
    monkeypatch.setattr(entitlements_module.license_manager, "current_edition", lambda: "free")
    # ALL_FEATURES_FREE (the Pro-for-everyone kill switch, v1.5.0) is on by
    # default and would otherwise make every limit unlimited regardless of
    # edition — this suite exercises the underlying Free/Pro cap logic
    # itself, so it needs gating back on (test_entitlements.py's own
    # _with_gating_enabled pattern).
    monkeypatch.setattr(entitlements_module.config, "ALL_FEATURES_FREE", False)
    # UpgradeDialog.exec() is a real blocking modal — nothing can click it
    # offscreen, so stub it out rather than the require_under_limit() logic
    # itself, which is exactly what's under test.
    monkeypatch.setattr(upgrade_dialog.UpgradeDialog, "exec", lambda self: None)

    config = {"id": "t", "name": "t", "type": "sqlite", "database": ":memory:"}
    return ConnectionPanel(config, DbService(), QueryHistory(), SavedQueries(), already_connected=True)


def _cap() -> int:
    return entitlements_module.entitlements.limit(Limit.MAX_QUERY_TABS)


def test_query_tabs_alone_are_capped(monkeypatch):
    panel = _make_panel(monkeypatch)
    for _ in range(_cap()):
        assert panel.add_new_tab() is not None
    assert panel.tabs.count() == _cap()

    assert panel.add_new_tab() is None
    assert panel.tabs.count() == _cap()


def test_table_tabs_alone_are_capped(monkeypatch):
    panel = _make_panel(monkeypatch)
    for i in range(_cap()):
        panel.open_table_view(f"table_{i}")
    assert panel.tabs.count() == _cap()

    panel.open_table_view("one_too_many")
    assert panel.tabs.count() == _cap()


def test_query_and_table_tabs_count_together(monkeypatch):
    """The exact bug #154 warns against: a naive cap that only counts query
    tabs would let table tabs open without limit."""
    panel = _make_panel(monkeypatch)
    panel.add_new_tab()
    panel.add_new_tab()
    for i in range(_cap() - 2):
        panel.open_table_view(f"t{i}")
    assert panel.tabs.count() == _cap()

    # A 6th tab of either kind is blocked by the combined count.
    assert panel.add_new_tab() is None
    panel.open_table_view("one_more")
    assert panel.tabs.count() == _cap()


def test_pro_edition_has_no_tab_cap(monkeypatch):
    monkeypatch.setattr(entitlements_module.license_manager, "current_edition", lambda: "pro")
    monkeypatch.setattr(upgrade_dialog.UpgradeDialog, "exec", lambda self: None)
    config = {"id": "t", "name": "t", "type": "sqlite", "database": ":memory:"}
    panel = ConnectionPanel(config, DbService(), QueryHistory(), SavedQueries(), already_connected=True)

    opened = 8  # comfortably past the Free cap (5) — Pro has none
    for i in range(opened):
        panel.add_new_tab()

    assert panel.tabs.count() == opened


def test_reopening_an_already_open_table_does_not_count_twice(monkeypatch):
    panel = _make_panel(monkeypatch)
    for i in range(_cap()):
        panel.open_table_view(f"table_{i}")

    panel.open_table_view("table_0")  # re-focus, not a new tab
    assert panel.tabs.count() == _cap()


def test_force_new_always_opens_a_fresh_tab(monkeypatch):
    """Issue #179: "Open in New Tab" must not raise TypeError and must
    open a second tab for the same table instead of re-focusing the first."""
    panel = _make_panel(monkeypatch)
    panel.open_table_view("orders")
    assert panel.tabs.count() == 1

    panel.open_table_view("orders", force_new=True)
    assert panel.tabs.count() == 2


def test_restore_session_tabs_stops_silently_at_cap(monkeypatch):
    panel = _make_panel(monkeypatch)
    saved = [{"type": "query", "name": f"Tab {i}", "query": f"SELECT {i}"} for i in range(_cap() + 2)]

    panel.restore_session_tabs(saved)

    assert panel.tabs.count() == _cap()
    # Restored tabs keep their saved query text and label.
    assert isinstance(panel.tabs.widget(0), SqlTab)
    assert panel.tabs.widget(0).get_query() == "SELECT 0"


def test_restore_pinned_tabs_stops_silently_at_cap(monkeypatch):
    panel = _make_panel(monkeypatch)
    for _ in range(_cap() - 1):
        panel.add_new_tab()

    from utils import pinned_tabs as _pt
    # Keyed by panel.label, not the bare config name — issue #281 folded the
    # database name into the label so same-server tabs stay distinguishable.
    monkeypatch.setattr(_pt, "load", lambda: {panel.label: [{"name": "p1", "query": "SELECT 1"},
                                                             {"name": "p2", "query": "SELECT 2"}]})

    panel.restore_pinned_tabs()

    assert panel.tabs.count() == _cap()


def test_active_sql_tab_falls_back_to_existing_sql_tab_when_capped(monkeypatch):
    panel = _make_panel(monkeypatch)
    panel.add_new_tab()
    for i in range(_cap() - 1):
        panel.open_table_view(f"table_{i}")
    assert panel.tabs.count() == _cap()
    panel.tabs.setCurrentIndex(panel.tabs.count() - 1)  # a table tab is active

    tab = panel._active_sql_tab()

    assert isinstance(tab, SqlTab)  # reused the one existing SQL tab, not None


def test_active_sql_tab_returns_none_when_no_sql_tab_and_capped(monkeypatch):
    panel = _make_panel(monkeypatch)
    for i in range(_cap()):
        panel.open_table_view(f"table_{i}")
    assert panel.tabs.count() == _cap()

    assert panel._active_sql_tab() is None
