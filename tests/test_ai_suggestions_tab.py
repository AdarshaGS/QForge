"""Tests for ui/query_analyzer_dialog.py's _AiSuggestionsTab (issues #341,
#342, #343) — explain/optimize using services/ai_client.py, mocked so no
real `claude` CLI is required.

Widgets here are built once per module (not once per test) and reused —
_AiSuggestionsTab constructs a CodeEditor + SqlHighlighter + background
QThread, and churning through many of those across separate test functions
in one pytest session is a known source of Qt-teardown flakiness unrelated
to the feature itself (a pending highlightBlock event outliving the widget
it was posted for). Tests instead mutate the shared tab's already-fetched
availability state directly and call _update_gate()/reset the results
layout between cases.
"""
import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from services import ai_client, preferences
from services.query_cost import Issue
from ui.query_analyzer_dialog import QueryAnalyzerDialog, _AiSuggestionsTab

_app = QApplication.instance() or QApplication([])


class _FakeDb:
    db_type = "mysql"
    connection = True


def _set_state(tab, monkeypatch, tmp_path, *, enabled=True, authenticated=True):
    monkeypatch.setattr(preferences, "_FILE", str(tmp_path / "preferences.json"))
    preferences.set("ai.enabled", enabled)
    tab._availability._last = ai_client.AiAvailability(
        installed=True, authenticated=authenticated,
        auth_email="adarsh@m2pfintech.com" if authenticated else None,
        detail="" if authenticated else "Claude Code is installed but not logged in.",
    )
    tab._update_gate()


def _wait_until_idle(tab, tries=50):
    for _ in range(tries):
        QTest.qWait(20)
        if not tab._call_mgr.busy:
            break


@pytest.fixture(scope="module")
def ai_tab():
    with patch("services.ai_client.check_availability",
               return_value=ai_client.AiAvailability(installed=True, authenticated=True)):
        tab = _AiSuggestionsTab(_FakeDb(), initial_query="SELECT * FROM orders")
        for _ in range(20):
            QTest.qWait(20)
            if tab._availability.availability is not None:
                break
    yield tab
    tab.close()
    tab.deleteLater()
    QTest.qWait(50)


def test_dialog_includes_ai_suggestions_tab():
    # Every tab's AiAvailabilityWidget shells a real `claude auth status`
    # call on construction unless patched — mocked here so this test needs
    # no `claude` CLI installed and doesn't depend on real subprocess timing.
    with patch("services.ai_client.check_availability",
               return_value=ai_client.AiAvailability(installed=False, authenticated=False)):
        dlg = QueryAnalyzerDialog(_FakeDb(), initial_query="SELECT 1")
        titles = [dlg._tabs.tabText(i) for i in range(dlg._tabs.count())]
        assert "AI Suggestions" in titles
        dlg.show_ai_tab()
        assert dlg._tabs.currentWidget() is dlg._ai_tab
        for _ in range(20):
            QTest.qWait(20)
            if dlg._ai_tab._availability.availability is not None:
                break
    dlg.close()
    dlg.deleteLater()
    QTest.qWait(50)


def test_cost_tab_ask_ai_button_switches_tab_and_runs_optimize(monkeypatch, tmp_path):
    """Cost & Profile's "✨ Ask AI" button (issue: AI available wherever
    Cost/Profile results are) should switch to the AI Suggestions tab,
    carry over the query, and kick off the same optimize flow its own
    button would — without the user re-entering/re-pasting the query."""
    monkeypatch.setattr(preferences, "_FILE", str(tmp_path / "preferences.json"))
    preferences.set("ai.enabled", True)
    availability = ai_client.AiAvailability(installed=True, authenticated=True)
    with patch("services.ai_client.check_availability", return_value=availability):
        dlg = QueryAnalyzerDialog(_FakeDb(), initial_query="SELECT * FROM orders")
        for _ in range(20):
            QTest.qWait(20)
            if dlg._ai_tab._availability.availability is not None:
                break

        fake_result = ai_client.AiResult(ok=True, data={"suggestions": []})
        with patch("ui.ai_async.ai_client.run_prompt", return_value=fake_result) as mock_run:
            dlg._cost_tab.request_ai_optimize.emit()
            for _ in range(50):
                QTest.qWait(20)
                if not dlg._ai_tab._call_mgr.busy:
                    break

        assert dlg._tabs.currentWidget() is dlg._ai_tab
        assert dlg._ai_tab._editor.toPlainText() == "SELECT * FROM orders"
        mock_run.assert_called_once()
    dlg.close()
    dlg.deleteLater()
    QTest.qWait(50)


def test_buttons_disabled_when_ai_not_enabled(ai_tab, monkeypatch, tmp_path):
    _set_state(ai_tab, monkeypatch, tmp_path, enabled=False)
    assert ai_tab._explain_btn.isEnabled() is False
    assert ai_tab._optimize_btn.isEnabled() is False
    assert "Preferences" in ai_tab._status_lbl.text()


def test_buttons_disabled_when_not_authenticated(ai_tab, monkeypatch, tmp_path):
    _set_state(ai_tab, monkeypatch, tmp_path, enabled=True, authenticated=False)
    assert ai_tab._explain_btn.isEnabled() is False


def test_buttons_enabled_when_ready(ai_tab, monkeypatch, tmp_path):
    _set_state(ai_tab, monkeypatch, tmp_path, enabled=True, authenticated=True)
    assert ai_tab._explain_btn.isEnabled() is True
    assert ai_tab._optimize_btn.isEnabled() is True


def test_explain_renders_result_text(ai_tab, monkeypatch, tmp_path):
    _set_state(ai_tab, monkeypatch, tmp_path)
    fake_result = ai_client.AiResult(ok=True, text="This query selects everything from orders.")
    with patch("ui.ai_async.ai_client.run_prompt", return_value=fake_result):
        ai_tab._start_explain()
        _wait_until_idle(ai_tab)
    assert ai_tab._results_layout.count() == 1
    card = ai_tab._results_layout.itemAt(0).widget()
    labels = card.findChildren(type(ai_tab._status_lbl))
    assert any("selects everything" in lbl.text() for lbl in labels)


def test_explain_shows_failure_card_on_error(ai_tab, monkeypatch, tmp_path):
    _set_state(ai_tab, monkeypatch, tmp_path)
    fake_result = ai_client.AiResult(ok=False, error="not logged in", error_kind="not_authenticated")
    with patch("ui.ai_async.ai_client.run_prompt", return_value=fake_result):
        ai_tab._start_explain()
        _wait_until_idle(ai_tab)
    assert ai_tab._results_layout.count() >= 1


def test_optimize_renders_suggestion_cards(ai_tab, monkeypatch, tmp_path):
    _set_state(ai_tab, monkeypatch, tmp_path)
    fake_result = ai_client.AiResult(ok=True, data={
        "suggestions": [
            {"title": "Add an index", "detail": "customer_id is unindexed",
             "severity": "important", "suggested_sql": "CREATE INDEX ..."},
        ]
    })
    with patch("ui.ai_async.ai_client.run_prompt", return_value=fake_result):
        ai_tab._start_optimize()
        _wait_until_idle(ai_tab)
    assert ai_tab._results_layout.count() == 1


def test_optimize_passes_existing_issues_from_cost_tab(ai_tab, monkeypatch, tmp_path):
    _set_state(ai_tab, monkeypatch, tmp_path)
    cost_tab = MagicMock()
    cost_tab.last_issues.return_value = [
        Issue("HIGH", "FULL_TABLE_SCAN", "full scan on orders", "add an index"),
    ]
    ai_tab._cost_tab = cost_tab
    try:
        captured = {}

        def _fake_run_prompt(prompt, **kwargs):
            captured["prompt"] = prompt
            return ai_client.AiResult(ok=True, data={"suggestions": []})

        with patch("ui.ai_async.ai_client.run_prompt", side_effect=_fake_run_prompt):
            ai_tab._start_optimize()
            _wait_until_idle(ai_tab)

        assert "FULL_TABLE_SCAN" in captured["prompt"]
        cost_tab.last_issues.assert_called_once()
    finally:
        ai_tab._cost_tab = None


def test_double_click_does_not_start_a_second_call(ai_tab, monkeypatch, tmp_path):
    _set_state(ai_tab, monkeypatch, tmp_path)
    call_count = {"n": 0}

    def _slow_run_prompt(prompt, **kwargs):
        call_count["n"] += 1
        return ai_client.AiResult(ok=True, text="done")

    with patch("ui.ai_async.ai_client.run_prompt", side_effect=_slow_run_prompt):
        ai_tab._start_explain()
        ai_tab._start_explain()  # should be ignored — a call is already in flight
        _wait_until_idle(ai_tab)
    assert call_count["n"] == 1
