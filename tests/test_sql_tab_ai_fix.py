"""Tests for SqlTab's error-card "Ask AI to Fix This" affordance (issue
#342) — services/ai_client.py is mocked, no real `claude` CLI required."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from services import ai_client, preferences
from ui.sql_tab import SqlTab

_app = QApplication.instance() or QApplication([])


def _tab_with_ai(monkeypatch, tmp_path, enabled=True):
    monkeypatch.setattr(preferences, "_FILE", str(tmp_path / "preferences.json"))
    preferences.set("ai.enabled", enabled)
    tab = SqlTab()
    tab.show()  # isVisible() reflects actual on-screen visibility, which
    # requires the top-level widget itself to be shown — see
    # test_mock_data_dialog.py's identical note.
    return tab


def test_show_error_records_message_and_query(monkeypatch, tmp_path):
    tab = _tab_with_ai(monkeypatch, tmp_path)
    tab.show_error("Table 'ordrs' doesn't exist", query="SELECT * FROM ordrs")
    assert tab._last_error_message == "Table 'ordrs' doesn't exist"
    assert tab._last_error_query == "SELECT * FROM ordrs"


def test_ai_fix_button_visible_when_enabled(monkeypatch, tmp_path):
    tab = _tab_with_ai(monkeypatch, tmp_path, enabled=True)
    tab.show_error("boom", query="SELECT 1")
    assert tab._ai_fix_btn.isVisible() is True


def test_ai_fix_button_hidden_when_disabled(monkeypatch, tmp_path):
    tab = _tab_with_ai(monkeypatch, tmp_path, enabled=False)
    tab.show_error("boom", query="SELECT 1")
    assert tab._ai_fix_btn.isVisible() is False


def test_clicking_button_emits_signal(monkeypatch, tmp_path):
    tab = _tab_with_ai(monkeypatch, tmp_path, enabled=True)
    tab.show_error("boom", query="SELECT 1")
    received = []
    tab.ai_fix_requested.connect(lambda: received.append(True))
    tab._ai_fix_btn.click()
    assert received == [True]


def test_show_ai_fix_loading_disables_button_and_shows_status(monkeypatch, tmp_path):
    tab = _tab_with_ai(monkeypatch, tmp_path)
    tab.show_error("boom", query="SELECT 1")
    tab.show_ai_fix_loading()
    assert tab._ai_fix_btn.isEnabled() is False
    assert tab._ai_fix_section.isVisible() is True
    assert "Asking Claude" in tab._ai_fix_status_lbl.text()


def test_show_ai_fix_result_success_populates_snippet_and_explanation(monkeypatch, tmp_path):
    tab = _tab_with_ai(monkeypatch, tmp_path)
    tab.show_error("Unknown column 'ordrs.total'", query="SELECT total FROM ordrs")
    tab.show_ai_fix_loading()
    result = ai_client.AiResult(ok=True, data={
        "corrected_sql": "SELECT total FROM orders",
        "explanation": "The table is named orders, not ordrs.",
    })
    tab.show_ai_fix_result(result)
    assert tab._ai_fix_btn.isEnabled() is True
    assert tab._pending_ai_fix_sql == "SELECT total FROM orders"
    assert tab._ai_fix_snippet.toPlainText() == "SELECT total FROM orders"
    assert tab._ai_fix_snippet.isVisible() is True
    assert "not ordrs" in tab._ai_fix_explanation_lbl.text()
    assert tab._ai_fix_btn_row_widget.isVisible() is True


def test_show_ai_fix_result_failure_shows_error_no_snippet(monkeypatch, tmp_path):
    tab = _tab_with_ai(monkeypatch, tmp_path)
    tab.show_error("boom", query="SELECT 1")
    tab.show_ai_fix_loading()
    result = ai_client.AiResult(ok=False, error="Claude CLI not found on PATH.",
                                 error_kind="not_installed")
    tab.show_ai_fix_result(result)
    assert tab._pending_ai_fix_sql is None
    assert "Claude CLI not found" in tab._ai_fix_status_lbl.text()
    assert tab._ai_fix_snippet.isVisible() is False
    assert tab._ai_fix_btn_row_widget.isVisible() is False


def test_insert_ai_fix_replaces_editor_text_and_hides_section(monkeypatch, tmp_path):
    tab = _tab_with_ai(monkeypatch, tmp_path)
    tab.show_error("Unknown column", query="SELECT total FROM ordrs")
    tab.show_ai_fix_loading()
    tab.show_ai_fix_result(ai_client.AiResult(ok=True, data={
        "corrected_sql": "SELECT total FROM orders", "explanation": "fixed",
    }))
    tab._insert_ai_fix()
    assert tab.editor.toPlainText() == "SELECT total FROM orders"
    assert tab._ai_fix_section.isVisible() is False


def test_insert_ai_fix_does_nothing_without_pending_sql(monkeypatch, tmp_path):
    tab = _tab_with_ai(monkeypatch, tmp_path)
    tab.editor.setPlainText("original")
    tab._insert_ai_fix()
    assert tab.editor.toPlainText() == "original"


def test_new_error_resets_previous_ai_fix_state(monkeypatch, tmp_path):
    tab = _tab_with_ai(monkeypatch, tmp_path)
    tab.show_error("first error", query="SELECT 1")
    tab.show_ai_fix_result(ai_client.AiResult(ok=True, data={
        "corrected_sql": "SELECT 2", "explanation": "x",
    }))
    assert tab._pending_ai_fix_sql == "SELECT 2"

    tab.show_error("second, unrelated error", query="SELECT 3")
    assert tab._pending_ai_fix_sql is None
    assert tab._ai_fix_section.isVisible() is False
