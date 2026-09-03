"""Tests for ConnectionPanel's Quick Search recency tracking (issue #242)."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from services import entitlements as entitlements_module
from services.db_service import DbService
from services.query_history import QueryHistory
from services.saved_queries import SavedQueries
from ui import upgrade_dialog
from ui.connection_panel import ConnectionPanel

_app = QApplication.instance() or QApplication([])


def _make_panel(monkeypatch):
    monkeypatch.setattr(entitlements_module.license_manager, "current_edition", lambda: "free")
    monkeypatch.setattr(upgrade_dialog.UpgradeDialog, "exec", lambda self: None)
    config = {"id": "t", "name": "t", "type": "sqlite", "database": ":memory:"}
    return ConnectionPanel(config, DbService(), QueryHistory(), SavedQueries(), already_connected=True)


def test_recent_open_bumps_score_each_call(monkeypatch):
    panel = _make_panel(monkeypatch)
    panel._record_recent_table_open("orders")
    panel._record_recent_table_open("users")
    panel._record_recent_table_open("orders")

    assert panel._recent_table_opens["orders"] > panel._recent_table_opens["users"]


def test_recent_table_opens_are_capped(monkeypatch):
    panel = _make_panel(monkeypatch)
    cap = panel._MAX_RECENT_TABLE_OPENS
    for i in range(cap + 10):
        panel._record_recent_table_open(f"table_{i}")

    assert len(panel._recent_table_opens) == cap
    # The earliest-opened tables should have been pruned first.
    assert "table_0" not in panel._recent_table_opens
    assert f"table_{cap + 9}" in panel._recent_table_opens


def test_gather_recency_scores_ranks_views_and_tables(monkeypatch):
    panel = _make_panel(monkeypatch)
    panel.all_views = ["order_totals"]
    panel._record_recent_table_open("orders")
    panel._record_recent_table_open("order_totals")

    scores = panel._gather_recency_scores()

    assert scores[("table", "orders")] < scores[("view", "order_totals")]


def test_gather_recent_items_orders_most_recent_first(monkeypatch):
    panel = _make_panel(monkeypatch)
    panel._record_recent_table_open("orders")
    panel._record_recent_table_open("users")
    panel._record_recent_table_open("products")

    items = panel._gather_recent_items(limit=2)

    assert [name for _, name, _ in items] == ["products", "users"]
