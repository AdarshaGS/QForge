"""Tests for ConnectionPanel's opt-in proactive AI-optimization trigger
(Preferences -> AI Assistance -> "Automatically suggest optimizations after
running a query") -- fires from _on_query_cost_ready using the CostEstimate
already computed for the just-run query, never a second EXPLAIN round-trip.
services/ai_client.py is mocked, no real `claude` CLI required."""
import os
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from services import ai_client, preferences
from services.db_service import DbService
from services.query_cost import CostEstimate, Issue
from services.query_history import QueryHistory
from services.saved_queries import SavedQueries
from ui import upgrade_dialog
from services import entitlements as entitlements_module
from ui.connection_panel import ConnectionPanel

_app = QApplication.instance() or QApplication([])


def _make_panel(monkeypatch):
    monkeypatch.setattr(entitlements_module.license_manager, "current_edition", lambda: "free")
    monkeypatch.setattr(entitlements_module.config, "ALL_FEATURES_FREE", False)
    monkeypatch.setattr(upgrade_dialog.UpgradeDialog, "exec", lambda self: None)
    config = {"id": "t", "name": "t", "type": "sqlite", "database": ":memory:"}
    panel = ConnectionPanel(config, DbService(), QueryHistory(), SavedQueries(), already_connected=True)
    panel.db_service.connection = object()  # looks "connected" without a real DB
    return panel


def _estimate(issues=None, error=""):
    return CostEstimate(dialect="mysql", score=10, label="ok", issues=issues or [], error=error)


def _enable_ai(monkeypatch, tmp_path, *, proactive=True):
    monkeypatch.setattr(preferences, "_FILE", str(tmp_path / "preferences.json"))
    preferences.set("ai.enabled", True)
    preferences.set("ai.proactive_optimize", proactive)


def _wait_until_idle(tab, tries=50):
    for _ in range(tries):
        QTest.qWait(20)
        if not tab._ai_call_mgr.busy:
            break


def test_skips_when_ai_not_enabled(monkeypatch, tmp_path):
    monkeypatch.setattr(preferences, "_FILE", str(tmp_path / "preferences.json"))
    preferences.set("ai.enabled", False)
    panel = _make_panel(monkeypatch)
    tab = panel.add_new_tab()
    tab.set_query("SELECT * FROM orders")

    with patch("ui.ai_async.ai_client.run_prompt") as mock_run:
        panel._on_query_cost_ready(tab, _estimate())
        _wait_until_idle(tab)
    mock_run.assert_not_called()


def test_skips_when_proactive_toggle_off(monkeypatch, tmp_path):
    _enable_ai(monkeypatch, tmp_path, proactive=False)
    panel = _make_panel(monkeypatch)
    tab = panel.add_new_tab()
    tab.set_query("SELECT * FROM orders")

    with patch("ui.ai_async.ai_client.run_prompt") as mock_run:
        panel._on_query_cost_ready(tab, _estimate())
        _wait_until_idle(tab)
    mock_run.assert_not_called()


def test_skips_on_estimate_error(monkeypatch, tmp_path):
    _enable_ai(monkeypatch, tmp_path)
    panel = _make_panel(monkeypatch)
    tab = panel.add_new_tab()
    tab.set_query("SELECT * FROM orders")

    with patch("ui.ai_async.ai_client.run_prompt") as mock_run:
        panel._on_query_cost_ready(tab, _estimate(error="explain failed"))
        _wait_until_idle(tab)
    mock_run.assert_not_called()


def test_triggers_when_ready_and_populates_badge(monkeypatch, tmp_path):
    _enable_ai(monkeypatch, tmp_path)
    panel = _make_panel(monkeypatch)
    tab = panel.add_new_tab()
    tab.set_query("SELECT * FROM orders")

    issues = [Issue("HIGH", "FULL_TABLE_SCAN", "full scan on orders", "add an index")]
    fake_result = ai_client.AiResult(ok=True, data={
        "suggestions": [{"title": "Add an index", "detail": "x", "severity": "important"}]
    })
    with patch("ui.ai_async.ai_client.run_prompt", return_value=fake_result) as mock_run:
        panel._on_query_cost_ready(tab, _estimate(issues=issues))
        _wait_until_idle(tab)

    mock_run.assert_called_once()
    assert tab._ai_suggest_badge_btn.isHidden() is False
    assert tab._last_ai_suggestions == [{"title": "Add an index", "detail": "x", "severity": "important"}]


def test_does_not_retrigger_for_unchanged_query(monkeypatch, tmp_path):
    _enable_ai(monkeypatch, tmp_path)
    panel = _make_panel(monkeypatch)
    tab = panel.add_new_tab()
    tab.set_query("SELECT * FROM orders")

    fake_result = ai_client.AiResult(ok=True, data={"suggestions": []})
    with patch("ui.ai_async.ai_client.run_prompt", return_value=fake_result) as mock_run:
        panel._on_query_cost_ready(tab, _estimate())
        _wait_until_idle(tab)
        panel._on_query_cost_ready(tab, _estimate())  # same query text again
        _wait_until_idle(tab)

    assert mock_run.call_count == 1


def test_retriggers_when_query_changes(monkeypatch, tmp_path):
    _enable_ai(monkeypatch, tmp_path)
    panel = _make_panel(monkeypatch)
    tab = panel.add_new_tab()
    tab.set_query("SELECT * FROM orders")

    fake_result = ai_client.AiResult(ok=True, data={"suggestions": []})
    with patch("ui.ai_async.ai_client.run_prompt", return_value=fake_result) as mock_run:
        panel._on_query_cost_ready(tab, _estimate())
        _wait_until_idle(tab)
        tab.set_query("SELECT * FROM customers")
        panel._on_query_cost_ready(tab, _estimate())
        _wait_until_idle(tab)

    assert mock_run.call_count == 2


def test_prompt_includes_existing_issues(monkeypatch, tmp_path):
    _enable_ai(monkeypatch, tmp_path)
    panel = _make_panel(monkeypatch)
    tab = panel.add_new_tab()
    tab.set_query("SELECT * FROM orders")
    issues = [Issue("HIGH", "FULL_TABLE_SCAN", "full scan on orders", "add an index")]

    captured = {}

    def _fake_run_prompt(prompt, **kwargs):
        captured["prompt"] = prompt
        return ai_client.AiResult(ok=True, data={"suggestions": []})

    with patch("ui.ai_async.ai_client.run_prompt", side_effect=_fake_run_prompt):
        panel._on_query_cost_ready(tab, _estimate(issues=issues))
        _wait_until_idle(tab)

    assert "FULL_TABLE_SCAN" in captured["prompt"]
