"""Tests for SqlTab's proactive-AI-suggestions status-bar badge (opt-in via
Preferences -> AI Assistance -> "Automatically suggest optimizations after
running a query") -- set_ai_suggestions()/clear_ai_suggestions() and the
open_ai_suggestions signal the badge emits when clicked."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from ui.sql_tab import SqlTab

_app = QApplication.instance() or QApplication([])


def _tab():
    tab = SqlTab()
    tab.show()
    return tab


def test_badge_hidden_by_default():
    tab = _tab()
    assert tab._ai_suggest_badge_btn.isVisible() is False


def test_set_ai_suggestions_shows_badge_with_count():
    tab = _tab()
    tab.set_ai_suggestions([
        {"title": "Add an index", "detail": "x", "severity": "important"},
        {"title": "Avoid SELECT *", "detail": "y", "severity": "info"},
    ])
    assert tab._ai_suggest_badge_btn.isVisible() is True
    assert "2 AI suggestions" in tab._ai_suggest_badge_btn.text()
    assert tab._last_ai_suggestions is not None
    assert len(tab._last_ai_suggestions) == 2


def test_set_ai_suggestions_singular_count():
    tab = _tab()
    tab.set_ai_suggestions([{"title": "Add an index", "detail": "x", "severity": "important"}])
    assert "1 AI suggestion" in tab._ai_suggest_badge_btn.text()
    assert "suggestions" not in tab._ai_suggest_badge_btn.text()


def test_set_ai_suggestions_empty_list_does_nothing():
    tab = _tab()
    tab.set_ai_suggestions([])
    assert tab._ai_suggest_badge_btn.isVisible() is False
    assert tab._last_ai_suggestions is None


def test_clear_ai_suggestions_hides_badge():
    tab = _tab()
    tab.set_ai_suggestions([{"title": "x", "detail": "y", "severity": "info"}])
    tab.clear_ai_suggestions()
    assert tab._ai_suggest_badge_btn.isVisible() is False
    assert tab._last_ai_suggestions is None


def test_clicking_badge_emits_open_ai_suggestions_signal():
    tab = _tab()
    tab.set_ai_suggestions([{"title": "x", "detail": "y", "severity": "info"}])
    received = []
    tab.open_ai_suggestions.connect(lambda: received.append(True))
    tab._ai_suggest_badge_btn.click()
    assert received == [True]
