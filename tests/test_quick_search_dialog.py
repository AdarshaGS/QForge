"""Tests for QuickSearchDialog's default-results scoping (issue #241) and
recency ranking (issue #242), plus a large-schema latency spot-check
(issue #244)."""
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from ui.quick_search_dialog import QuickSearchDialog

_app = QApplication.instance() or QApplication([])


def _labels(dialog):
    """Plain row text (issue #245 moved the type label into a delegate-
    painted badge, out of the item's own text)."""
    return [dialog.results_list.item(i).text() for i in range(dialog.results_list.count())]


def _user_data(dialog, row):
    """The full (item_type, display_text, payload, source_idx, extra)
    tuple a row was stored with, for asserting on badge/shortcut data the
    delegate reads but that isn't part of the visible text."""
    return dialog.results_list.item(row).data(Qt.UserRole)


def test_default_search_excludes_columns():
    all_items = [("table", "orders", None), ("view", "order_totals", None)]
    column_items = [("column", "orders.id", "id"), ("column", "orders.total", "total")]
    dialog = QuickSearchDialog(all_items, column_items=column_items)

    dialog.filter_items("order")

    labels = _labels(dialog)
    assert any("orders" in l for l in labels)
    assert not any("orders.id" in l or "orders.total" in l for l in labels)


def test_column_filter_prefix_searches_columns_only():
    all_items = [("table", "orders", None)]
    column_items = [("column", "orders.id", "id"), ("column", "users.id", "id")]
    dialog = QuickSearchDialog(all_items, column_items=column_items)

    dialog.filter_items("c:id")

    labels = _labels(dialog)
    assert len(labels) == 2
    assert all("id" in l for l in labels)
    assert "orders" not in labels  # the table itself, not a column


def test_no_column_items_is_safe():
    dialog = QuickSearchDialog([("table", "orders", None)])
    dialog.filter_items("c:anything")
    assert dialog.results_list.count() == 0


def test_recency_breaks_ties_within_a_match_tier():
    # Both "order_items" and "order_totals" are starts-with matches for
    # "order" — recency should decide which comes first.
    all_items = [("table", "order_items", None), ("table", "order_totals", None)]
    recency_scores = {("table", "order_items"): 1, ("table", "order_totals"): 99}
    dialog = QuickSearchDialog(all_items, recency_scores=recency_scores)

    dialog.filter_items("order")

    labels = _labels(dialog)
    assert labels.index("order_totals") < labels.index("order_items")


def test_recency_does_not_override_match_tier():
    # Exact match should still win over a more "recent" starts-with match.
    all_items = [("table", "orders", None), ("table", "orders_archive", None)]
    recency_scores = {("table", "orders_archive"): 99, ("table", "orders"): 1}
    dialog = QuickSearchDialog(all_items, recency_scores=recency_scores)

    dialog.filter_items("orders")

    labels = _labels(dialog)
    assert labels[0] == "orders"


def test_empty_query_shows_recent_items():
    recent_items = [("table", "orders", None), ("table", "users", None)]
    dialog = QuickSearchDialog([("table", "orders", None)], recent_items=recent_items)

    dialog.filter_items("")

    labels = _labels(dialog)
    assert labels == ["orders", "users"]
    assert dialog.count_label.text() == "Recent"


def test_empty_query_without_recent_items_shows_prompt():
    dialog = QuickSearchDialog([("table", "orders", None)])
    dialog.filter_items("")
    assert dialog.results_list.count() == 0
    assert dialog.count_label.text() == "Type to search..."


def test_cross_connection_results_are_labeled_and_source_is_returned():
    # issue #243: items from two connections, disambiguated in the row
    # text, and the selected item's source_idx must round-trip correctly.
    all_items = [
        ("table", "orders", None, 0),
        ("table", "orders", None, 1),
    ]
    dialog = QuickSearchDialog(all_items, sources=["staging", "prod"])

    dialog.filter_items("orders")

    labels = _labels(dialog)
    assert labels == ["orders  (staging)", "orders  (prod)"]

    captured = []
    dialog.item_selected.connect(lambda *args: captured.append(args))
    dialog.on_item_selected(dialog.results_list.item(1))
    assert captured == [("table", "orders", "", 1)]


def test_single_source_has_no_suffix():
    dialog = QuickSearchDialog([("table", "orders", None)], sources=["only_one"])
    dialog.filter_items("orders")
    assert _labels(dialog) == ["orders"]


def test_legacy_three_tuple_callers_default_to_source_zero():
    # command_palette.py and ConnectionPanel's single-connection path still
    # pass plain 3-tuples — must keep working unmodified.
    dialog = QuickSearchDialog([("command", "Refresh", "db:Refresh")])
    captured = []
    dialog.item_selected.connect(lambda *args: captured.append(args))

    dialog.filter_items("refresh")
    dialog.on_item_selected(dialog.results_list.item(0))

    assert captured == [("command", "Refresh", "db:Refresh", 0)]


def test_results_list_uses_the_badge_delegate():
    from ui.quick_search_dialog import QuickSearchItemDelegate
    dialog = QuickSearchDialog([("table", "orders", None)])
    assert isinstance(dialog.results_list.itemDelegate(), QuickSearchItemDelegate)


def test_item_text_carries_no_type_prefix_type_is_in_user_data():
    # issue #245: the type now lives in the delegate-painted badge, read
    # from Qt.UserRole — not the visible text.
    dialog = QuickSearchDialog([("column", "orders.id", "id")])
    dialog.filter_items("id")

    assert _labels(dialog) == ["orders.id"]
    item_type, display_text, payload, source_idx, extra = _user_data(dialog, 0)
    assert item_type == "column"


def test_command_shortcut_reaches_extra_for_the_delegate():
    dialog = QuickSearchDialog([("command", "Refresh", "db:Refresh", 0, {"shortcut": "Ctrl+R"})])
    dialog.filter_items("refresh")

    _, _, _, _, extra = _user_data(dialog, 0)
    assert extra["shortcut"] == "Ctrl+R"


def test_disabled_item_is_not_triggered_and_dialog_stays_open():
    # issue #246: selecting a disabled row must not emit item_selected or
    # close the dialog.
    extra = {"disabled": True, "reason": "requires an active connection"}
    dialog = QuickSearchDialog([("command", "Compare Schemas…", "db:Compare", 0, extra)])
    dialog.filter_items("compare")

    captured = []
    dialog.item_selected.connect(lambda *args: captured.append(args))
    accepted = []
    dialog.accept = lambda: accepted.append(True)

    dialog.on_item_selected(dialog.results_list.item(0))

    assert captured == []
    assert accepted == []


def test_enabled_item_still_triggers_normally():
    dialog = QuickSearchDialog([("command", "Refresh", "db:Refresh", 0, {})])
    dialog.filter_items("refresh")

    captured = []
    dialog.item_selected.connect(lambda *args: captured.append(args))
    accepted = []
    dialog.accept = lambda: accepted.append(True)

    dialog.on_item_selected(dialog.results_list.item(0))

    assert captured == [("command", "Refresh", "db:Refresh", 0)]
    assert accepted == [True]


def test_filter_items_latency_stays_low_with_large_schema():
    """Issue #244, re-run after #241: columns no longer inflate the default
    result set, so this is a regression guard rather than a fix."""
    n = 5000
    all_items = [("table", f"table_{i:05d}", None) for i in range(n)]
    all_items += [("history", f"SELECT * FROM table_{i:05d}"[:80], "q") for i in range(100)]
    dialog = QuickSearchDialog(all_items)

    # Worst case: a short prefix that forces the fuzzy-match fallback
    # across every item (nothing starts-with/contains "tb2" cleanly other
    # than by fuzzy character-order matching), same convention as
    # tests/test_sql_completer_cte_derived.py's equivalent check.
    t0 = time.perf_counter()
    dialog.filter_items("tb2")
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert dialog.results_list.count() > 0
    assert elapsed_ms < 50, f"filter_items took {elapsed_ms:.1f}ms for {n} items"
