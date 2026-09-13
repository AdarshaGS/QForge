"""Tests for the "AI Assistance" section of ui/preferences_dialog.py,
specifically the proactive-optimize checkbox added alongside the main
opt-in toggle."""
import os
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from services import ai_client, preferences
from ui.preferences_dialog import PreferencesDialog

_app = QApplication.instance() or QApplication([])


class _FakeMainWindow:
    current_theme = "dark"


def _make_dialog(monkeypatch, tmp_path):
    monkeypatch.setattr(preferences, "_FILE", str(tmp_path / "preferences.json"))
    availability = ai_client.AiAvailability(installed=True, authenticated=True)
    with patch("services.ai_client.check_availability", return_value=availability):
        dlg = PreferencesDialog(_FakeMainWindow())
    return dlg


def test_proactive_checkbox_disabled_when_ai_disabled(monkeypatch, tmp_path):
    dlg = _make_dialog(monkeypatch, tmp_path)
    assert dlg.ai_enabled_check.isChecked() is False
    assert dlg.ai_proactive_check.isEnabled() is False


def test_enabling_ai_enables_proactive_checkbox(monkeypatch, tmp_path):
    dlg = _make_dialog(monkeypatch, tmp_path)
    dlg.ai_enabled_check.setChecked(True)
    assert dlg.ai_proactive_check.isEnabled() is True


def test_proactive_checkbox_persists_preference(monkeypatch, tmp_path):
    dlg = _make_dialog(monkeypatch, tmp_path)
    dlg.ai_enabled_check.setChecked(True)
    dlg.ai_proactive_check.setChecked(True)
    assert preferences.get("ai.proactive_optimize") is True


def test_disabling_ai_disables_but_does_not_uncheck_proactive(monkeypatch, tmp_path):
    dlg = _make_dialog(monkeypatch, tmp_path)
    dlg.ai_enabled_check.setChecked(True)
    dlg.ai_proactive_check.setChecked(True)
    dlg.ai_enabled_check.setChecked(False)
    assert dlg.ai_proactive_check.isEnabled() is False
    # Preference itself is untouched — re-enabling AI restores the same choice.
    assert preferences.get("ai.proactive_optimize") is True
