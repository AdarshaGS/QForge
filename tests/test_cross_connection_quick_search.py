"""Tests for cross-connection Quick Search (issue #243): MainWindow.
show_quick_search() and _on_quick_search_cross_panel() dispatch, using
duck-typed fake panels rather than a real (heavy) ConnectionPanel/DbService,
matching this suite's existing MainWindow-stub pattern
(tests/test_main_window_focus_after_connect.py)."""
import os
import types
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QWidget

import main as main_mod

_app = QApplication.instance() or QApplication([])


class _FakePanel:
    def __init__(self, label, items, columns=None):
        self.label = label
        self._items = items
        self._columns = columns or []
        self.dispatched = []

    def _gather_quick_search_items(self):
        return self._items

    def _gather_column_items(self):
        return self._columns

    def _on_quick_search(self, item_type, display_text, payload):
        self.dispatched.append((item_type, display_text, payload))


class _FakeMainWindow(QWidget):
    def __init__(self, panels):
        super().__init__()
        self._panels = panels
        self.switched_to = []
        self.conn_tab_bar = SimpleNamespace(setCurrentIndex=self.switched_to.append)
        self._on_quick_search_cross_panel = types.MethodType(
            main_mod.MainWindow._on_quick_search_cross_panel, self)


def test_single_panel_delegates_to_its_own_quick_search():
    calls = []
    panel = _FakePanel("only", [("table", "orders", None)])
    panel.show_quick_search = lambda: calls.append(True)
    fake = _FakeMainWindow([panel])

    main_mod.MainWindow.show_quick_search(fake)

    assert calls == [True]


def test_multi_panel_gathers_all_panels_tagged_by_source(monkeypatch):
    captured = {}

    class _StubDialog:
        def __init__(self, items, parent, column_items=None, sources=None):
            captured["items"] = items
            captured["column_items"] = column_items
            captured["sources"] = sources
            self.item_selected = SimpleNamespace(connect=lambda fn: None)

        def exec(self):
            return None

    monkeypatch.setattr(main_mod, "QuickSearchDialog", _StubDialog)

    p1 = _FakePanel("staging", [("table", "orders", None)],
                     columns=[("column", "orders.id", "id")])
    p2 = _FakePanel("prod", [("table", "users", None)])
    fake = _FakeMainWindow([p1, p2])

    main_mod.MainWindow.show_quick_search(fake)

    assert captured["sources"] == ["staging", "prod"]
    assert ("table", "orders", None, 0) in captured["items"]
    assert ("table", "users", None, 1) in captured["items"]
    assert ("column", "orders.id", "id", 0) in captured["column_items"]


def test_multi_panel_with_nothing_to_search_shows_message(monkeypatch):
    monkeypatch.setattr(main_mod, "QuickSearchDialog", lambda *a, **k: pytest.fail("should not open"))
    fake = _FakeMainWindow([_FakePanel("a", []), _FakePanel("b", [])])

    main_mod.MainWindow.show_quick_search(fake)  # must not raise / must not open a dialog


def test_cross_panel_dispatch_switches_tab_then_calls_target_panel():
    p1 = _FakePanel("staging", [])
    p2 = _FakePanel("prod", [])
    fake = _FakeMainWindow([p1, p2])

    main_mod.MainWindow._on_quick_search_cross_panel(fake, "table", "orders", "", 1)

    assert fake.switched_to == [1]
    assert p2.dispatched == [("table", "orders", "")]
    assert p1.dispatched == []


def test_cross_panel_dispatch_ignores_out_of_range_source():
    p1 = _FakePanel("staging", [])
    fake = _FakeMainWindow([p1])

    main_mod.MainWindow._on_quick_search_cross_panel(fake, "table", "orders", "", 5)

    assert fake.switched_to == []
    assert p1.dispatched == []
