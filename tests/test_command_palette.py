"""Tests for the Command Palette (issue #232) shortcut display (issue #245)."""
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QApplication, QMenuBar

from ui import command_palette

_app = QApplication.instance() or QApplication([])


def _make_menu_bar():
    menu_bar = QMenuBar()
    file_menu = menu_bar.addMenu("File")
    refresh = file_menu.addAction("Refresh")
    refresh.setShortcut(QKeySequence("Ctrl+R"))
    file_menu.addAction("Export…")  # no shortcut
    return menu_bar, refresh


def _make_menu_bar_with_disabled_action():
    menu_bar = QMenuBar()
    db_menu = menu_bar.addMenu("Database")
    compare = db_menu.addAction("Compare Schemas…")
    compare.setEnabled(False)
    compare.setStatusTip("requires an active connection")
    unlabeled = db_menu.addAction("Refresh Databases")
    unlabeled.setEnabled(False)  # no statusTip set — generic fallback
    return menu_bar


def test_action_with_shortcut_gets_it_in_extra(monkeypatch):
    menu_bar, refresh = _make_menu_bar()
    captured = {}

    class _StubDialog:
        def __init__(self, items, parent):
            captured["items"] = items
            self.item_selected = SimpleNamespace(connect=lambda fn: None)
            self.setWindowTitle = lambda *a: None
            self.search_input = SimpleNamespace(setPlaceholderText=lambda *a: None)

        def exec(self):
            return None

    monkeypatch.setattr(command_palette, "QuickSearchDialog", _StubDialog)

    command_palette.show_command_palette(menu_bar)

    by_label = {label: extra for (_type, label, _key, _idx, extra) in captured["items"]}
    assert by_label["Refresh"] == {"shortcut": "Ctrl+R"}
    assert by_label["Export…"] == {}


def _capture_items(menu_bar, monkeypatch):
    captured = {}

    class _StubDialog:
        def __init__(self, items, parent):
            captured["items"] = items
            self.item_selected = SimpleNamespace(connect=lambda fn: None)
            self.setWindowTitle = lambda *a: None
            self.search_input = SimpleNamespace(setPlaceholderText=lambda *a: None)

        def exec(self):
            return None

    monkeypatch.setattr(command_palette, "QuickSearchDialog", _StubDialog)
    command_palette.show_command_palette(menu_bar)
    return captured["items"]


def test_disabled_actions_are_included_not_skipped(monkeypatch):
    # issue #246: previously _collect_actions() dropped disabled actions
    # entirely — they must now still appear (for the palette to grey them
    # out with a reason) instead of looking like they don't exist.
    menu_bar = _make_menu_bar_with_disabled_action()
    items = _capture_items(menu_bar, monkeypatch)

    by_label = {label: extra for (_type, label, _key, _idx, extra) in items}
    assert by_label["Compare Schemas…"] == {
        "disabled": True, "reason": "requires an active connection",
    }
    assert by_label["Refresh Databases"] == {
        "disabled": True, "reason": command_palette._GENERIC_DISABLED_REASON,
    }
