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


def _capture_dialog_args(menu_bar, monkeypatch):
    """Stub out QuickSearchDialog and capture every arg show_command_palette()
    constructed it with — items plus the empty-state kwargs."""
    captured = {}

    class _StubDialog:
        def __init__(self, items, parent, **kwargs):
            captured["items"] = items
            captured.update(kwargs)
            self.item_selected = SimpleNamespace(connect=lambda fn: None)
            self.setWindowTitle = lambda *a: None
            self.search_input = SimpleNamespace(setPlaceholderText=lambda *a: None)

        def exec(self):
            return None

    monkeypatch.setattr(command_palette, "QuickSearchDialog", _StubDialog)
    command_palette.show_command_palette(menu_bar)
    return captured


def test_action_with_shortcut_gets_it_in_extra(monkeypatch):
    menu_bar, refresh = _make_menu_bar()
    captured = _capture_dialog_args(menu_bar, monkeypatch)

    by_label = {label: extra for (_type, label, _key, _idx, extra) in captured["items"]}
    assert by_label["Refresh"] == {"shortcut": "Ctrl+R"}
    assert by_label["Export…"] == {}


def test_all_commands_shown_by_default_not_just_on_search():
    # The palette should list everything up front — otherwise there's no
    # way to browse what's available without already knowing what to type.
    menu_bar, refresh = _make_menu_bar()
    action_by_key = command_palette._collect_actions(menu_bar)
    items = []
    for key, act in action_by_key.items():
        items.append(("command", key.split(":", 1)[1], key, 0, {}))

    from ui.quick_search_dialog import QuickSearchDialog
    dialog = QuickSearchDialog(
        items, recent_items=items, empty_state_label="All Commands",
        empty_state_limit=len(items),
    )
    dialog.filter_items("")

    labels = [dialog.results_list.item(i).text() for i in range(dialog.results_list.count())]
    assert set(labels) == {"Refresh", "Export…"}
    assert dialog.count_label.text() == "All Commands"


def test_show_command_palette_wires_up_the_empty_state(monkeypatch):
    menu_bar, refresh = _make_menu_bar()
    captured = _capture_dialog_args(menu_bar, monkeypatch)

    assert captured["recent_items"] == captured["items"]
    assert captured["empty_state_label"] == "All Commands"
    assert captured["empty_state_limit"] == len(captured["items"])


def _capture_items(menu_bar, monkeypatch):
    return _capture_dialog_args(menu_bar, monkeypatch)["items"]


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
