"""Tests for ui/ai_schema_chat_dialog.py (issue #345) — services/ai_client.py
mocked, no real `claude` CLI required."""
import os
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from services import ai_client, preferences
from ui.ai_schema_chat_dialog import AiSchemaChatDialog

_app = QApplication.instance() or QApplication([])


class _FakeDb:
    db_type = "mysql"
    connection = True


_COLUMN_DETAILS = {
    "orders": [
        {"name": "id", "type": "int", "nullable": False, "key": "PRI"},
        {"name": "customer_id", "type": "int", "nullable": False, "key": ""},
        {"name": "total", "type": "decimal(10,2)", "nullable": False, "key": ""},
    ],
    "customers": [
        {"name": "id", "type": "int", "nullable": False, "key": "PRI"},
        {"name": "name", "type": "varchar(100)", "nullable": False, "key": ""},
    ],
}
_FOREIGN_KEYS = {
    "orders": [{"column": "customer_id", "ref_table": "customers", "ref_column": "id"}],
}


def _make_dialog(monkeypatch, tmp_path, enabled=True, authenticated=True):
    monkeypatch.setattr(preferences, "_FILE", str(tmp_path / "preferences.json"))
    preferences.set("ai.enabled", enabled)
    availability = ai_client.AiAvailability(installed=True, authenticated=authenticated)
    with patch("services.ai_client.check_availability", return_value=availability):
        dlg = AiSchemaChatDialog(_FakeDb(), column_details=_COLUMN_DETAILS,
                                  foreign_keys=_FOREIGN_KEYS)
        for _ in range(20):
            QTest.qWait(20)
            if dlg._availability.availability is not None:
                break
    return dlg


def _wait_until_idle(dlg, tries=50):
    for _ in range(tries):
        QTest.qWait(20)
        if not dlg._call_mgr.busy:
            break


def test_input_disabled_when_ai_not_enabled(monkeypatch, tmp_path):
    dlg = _make_dialog(monkeypatch, tmp_path, enabled=False)
    assert dlg._send_btn.isEnabled() is False
    assert dlg._input_edit.isEnabled() is False


def test_input_enabled_when_ready(monkeypatch, tmp_path):
    dlg = _make_dialog(monkeypatch, tmp_path)
    assert dlg._send_btn.isEnabled() is True
    assert dlg._input_edit.isEnabled() is True


def test_send_adds_user_bubble_immediately(monkeypatch, tmp_path):
    dlg = _make_dialog(monkeypatch, tmp_path)
    dlg._input_edit.setText("what tables exist?")
    with patch("ui.ai_async.ai_client.run_prompt",
               return_value=ai_client.AiResult(ok=True, text="orders and customers")):
        dlg._send()
        assert dlg._input_edit.text() == ""  # cleared immediately, not after the response
        _wait_until_idle(dlg)
    # 2 bubbles: user question + assistant answer
    assert dlg._transcript_layout.count() == 3  # 2 bubbles + trailing stretch


def test_response_appends_to_history_for_next_turn(monkeypatch, tmp_path):
    dlg = _make_dialog(monkeypatch, tmp_path)
    dlg._input_edit.setText("what tables exist?")
    with patch("ui.ai_async.ai_client.run_prompt",
               return_value=ai_client.AiResult(ok=True, text="orders and customers")):
        dlg._send()
        _wait_until_idle(dlg)
    assert dlg._history == [("what tables exist?", "orders and customers")]

    captured = {}

    def _fake_run_prompt(prompt, **kwargs):
        captured["prompt"] = prompt
        return ai_client.AiResult(ok=True, text="it has an id and total column")

    dlg._input_edit.setText("tell me more about orders")
    with patch("ui.ai_async.ai_client.run_prompt", side_effect=_fake_run_prompt):
        dlg._send()
        _wait_until_idle(dlg)

    assert "what tables exist?" in captured["prompt"]
    assert "orders and customers" in captured["prompt"]
    assert len(dlg._history) == 2


def test_failure_shows_error_bubble_not_added_to_history(monkeypatch, tmp_path):
    dlg = _make_dialog(monkeypatch, tmp_path)
    dlg._input_edit.setText("what tables exist?")
    with patch("ui.ai_async.ai_client.run_prompt",
               return_value=ai_client.AiResult(ok=False, error="timed out", error_kind="timeout")):
        dlg._send()
        _wait_until_idle(dlg)
    assert dlg._history == []


def test_schema_context_seeded_up_front_without_naming_tables(monkeypatch, tmp_path):
    """Regression test: schema context used to only get fetched for a
    table whose exact name appeared in the question text — a schema
    question like this one (never says "orders" or "customers") used to
    get zero column info, leaving the model to guess."""
    dlg = _make_dialog(monkeypatch, tmp_path)
    dlg._input_edit.setText("which tables reference each other?")
    captured = {}

    def _fake_run_prompt(prompt, **kwargs):
        captured["prompt"] = prompt
        return ai_client.AiResult(ok=True, text="orders references customers")

    with patch("ui.ai_async.ai_client.run_prompt", side_effect=_fake_run_prompt):
        dlg._send()
        _wait_until_idle(dlg)

    assert "customer_id" in captured["prompt"]
    assert "decimal(10,2)" in captured["prompt"]


def test_double_send_does_not_start_second_call(monkeypatch, tmp_path):
    dlg = _make_dialog(monkeypatch, tmp_path)
    dlg._input_edit.setText("question one")
    call_count = {"n": 0}

    def _slow(prompt, **kwargs):
        call_count["n"] += 1
        return ai_client.AiResult(ok=True, text="answer")

    with patch("ui.ai_async.ai_client.run_prompt", side_effect=_slow):
        dlg._send()
        dlg._input_edit.setText("question two")
        dlg._send()  # ignored — a call is already in flight
        _wait_until_idle(dlg)
    assert call_count["n"] == 1
