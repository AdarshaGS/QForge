"""Regression tests for issue #276: within a match tier (starts-with/
contains/fuzzy), a tighter match should outrank a looser one instead of
falling back to whatever order items happened to be gathered in (which
was effectively alphabetical for table lists)."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from ui.quick_search_dialog import QuickSearchDialog

_app = QApplication.instance() or QApplication([])


def _labels(dialog):
    return [dialog.results_list.item(i).text() for i in range(dialog.results_list.count())]


def test_fuzzy_search_ranks_the_tighter_match_first():
    # "ftask" isn't a literal substring of either name (both have an
    # underscore between f and t), so both fall into the fuzzy tier —
    # alphabetically "archive_f_task" used to win purely by luck.
    tables = ["archive_f_task", "f_task"]
    dialog = QuickSearchDialog([("table", t, None) for t in tables])

    dialog.filter_items("ftask")

    assert _labels(dialog) == ["f_task", "archive_f_task"]


def test_fuzzy_search_ranks_shorter_candidate_first_on_tied_span(monkeypatch=None):
    tables = ["m_loan", "m_loan_account", "crm_loan_offer", "small_loan_disbursement"]
    dialog = QuickSearchDialog([("table", t, None) for t in tables])

    dialog.filter_items("mloan")

    assert _labels(dialog)[0] == "m_loan"


def test_contains_tier_ranks_shorter_candidate_first():
    tables = ["archive_f_task", "f_task"]
    dialog = QuickSearchDialog([("table", t, None) for t in tables])

    dialog.filter_items("task")  # literal substring of both -> contains tier

    assert _labels(dialog) == ["f_task", "archive_f_task"]


def test_fuzzy_match_score_orders_tighter_matches_lower():
    dialog = QuickSearchDialog([])
    tight = dialog._fuzzy_match_score("ftask", "f_task")
    loose = dialog._fuzzy_match_score("ftask", "archive_f_task")
    no_match = dialog._fuzzy_match_score("zzz", "f_task")

    assert tight < loose
    assert no_match is None


def test_recency_still_wins_over_match_tightness():
    # Recency (issue #242) is a stronger, more personal signal than raw
    # string tightness — it should still be able to override the
    # tightness-based pre-sort, not just tie-break on top of it.
    tables = ["f_task", "archive_f_task"]
    dialog = QuickSearchDialog(
        [("table", t, None) for t in tables],
        recency_scores={("table", "archive_f_task"): 10},
    )

    dialog.filter_items("ftask")

    assert _labels(dialog)[0] == "archive_f_task"
