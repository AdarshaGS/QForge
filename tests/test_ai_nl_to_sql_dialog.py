"""Tests for ui/ai_nl_to_sql_dialog.py (issue #344) — services/ai_client.py
mocked, no real `claude` CLI required."""
import os
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from services import ai_client, preferences
from ui.ai_nl_to_sql_dialog import AiNlToSqlDialog

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
        dlg = AiNlToSqlDialog(_FakeDb(), "mysql", ["orders", "customers"],
                               column_details=_COLUMN_DETAILS, foreign_keys=_FOREIGN_KEYS)
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


def test_generate_disabled_when_not_ready(monkeypatch, tmp_path):
    dlg = _make_dialog(monkeypatch, tmp_path, enabled=False)
    assert dlg._generate_btn.isEnabled() is False


def test_generate_enabled_when_ready(monkeypatch, tmp_path):
    dlg = _make_dialog(monkeypatch, tmp_path, enabled=True, authenticated=True)
    assert dlg._generate_btn.isEnabled() is True


def test_generate_populates_preview_and_enables_insert(monkeypatch, tmp_path):
    dlg = _make_dialog(monkeypatch, tmp_path)
    dlg._request_edit.setText("show all orders over 100")
    fake_result = ai_client.AiResult(ok=True, data={
        "sql": "SELECT * FROM orders WHERE total > 100",
        "explanation": "Filters orders by total.",
        "caveats": ["Assumes 'total' is the order amount column."],
    })
    with patch("ui.ai_async.ai_client.run_prompt", return_value=fake_result):
        dlg._start_generate()
        _wait_until_idle(dlg)
    assert dlg._preview_editor.toPlainText() == "SELECT * FROM orders WHERE total > 100"
    assert dlg._insert_btn.isEnabled() is True
    assert "Filters orders" in dlg._explanation_lbl.text()
    assert "Assumes" in dlg._explanation_lbl.text()


def test_generate_failure_disables_insert_and_shows_error(monkeypatch, tmp_path):
    dlg = _make_dialog(monkeypatch, tmp_path)
    dlg._request_edit.setText("do something")
    fake_result = ai_client.AiResult(ok=False, error="not logged in", error_kind="not_authenticated")
    with patch("ui.ai_async.ai_client.run_prompt", return_value=fake_result):
        dlg._start_generate()
        _wait_until_idle(dlg)
    assert dlg._insert_btn.isEnabled() is False
    assert "Couldn't generate" in dlg._status_lbl.text()


def test_insert_emits_signal_with_pending_sql(monkeypatch, tmp_path):
    dlg = _make_dialog(monkeypatch, tmp_path)
    dlg._request_edit.setText("show all orders")
    fake_result = ai_client.AiResult(ok=True, data={"sql": "SELECT * FROM orders", "explanation": "x"})
    with patch("ui.ai_async.ai_client.run_prompt", return_value=fake_result):
        dlg._start_generate()
        _wait_until_idle(dlg)

    received = []
    dlg.sql_accepted.connect(lambda sql: received.append(sql))
    dlg._insert()
    assert received == ["SELECT * FROM orders"]


def test_prompt_includes_mentioned_known_table(monkeypatch, tmp_path):
    dlg = _make_dialog(monkeypatch, tmp_path)
    dlg._request_edit.setText("show all orders")
    captured = {}

    def _fake_run_prompt(prompt, **kwargs):
        captured["prompt"] = prompt
        return ai_client.AiResult(ok=True, data={"sql": "SELECT 1", "explanation": ""})

    with patch("ui.ai_async.ai_client.run_prompt", side_effect=_fake_run_prompt):
        dlg._start_generate()
        _wait_until_idle(dlg)

    assert "orders" in captured["prompt"]
    assert "show all orders" in captured["prompt"]


def test_prompt_includes_full_schema_even_when_no_table_named(monkeypatch, tmp_path):
    """Regression test: the prompt used to only get column info for a
    table whose exact name appeared as a substring in the free-text
    request — a request like this one (no table name at all) used to send
    no schema context, leaving the model to guess column names."""
    dlg = _make_dialog(monkeypatch, tmp_path)
    dlg._request_edit.setText("show me the 10 biggest spenders")
    captured = {}

    def _fake_run_prompt(prompt, **kwargs):
        captured["prompt"] = prompt
        return ai_client.AiResult(ok=True, data={"sql": "SELECT 1", "explanation": ""})

    with patch("ui.ai_async.ai_client.run_prompt", side_effect=_fake_run_prompt):
        dlg._start_generate()
        _wait_until_idle(dlg)

    assert "customer_id" in captured["prompt"]
    assert "decimal(10,2)" in captured["prompt"]
    assert "No schema context available" not in captured["prompt"]
