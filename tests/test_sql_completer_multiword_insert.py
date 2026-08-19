"""Regression tests for issue #153 — accepting a multi-word keyword
suggestion (e.g. "SHOW PROCESSLIST") duplicated the already-typed leading
word(s) instead of replacing the whole phrase."""
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QPlainTextEdit

from ui.sql_completer import SqlCompleter

_app = QApplication.instance() or QApplication([])


def _insert(text: str, completion: str) -> str:
    """Runs the real SqlCompleter._insert against a plain QPlainTextEdit
    seeded with *text* (cursor at the end) — avoids constructing a full
    SqlCompleter (schema, popup wiring, ...) for what's a pure text-editing
    operation on self._editor/self._popup."""
    editor = QPlainTextEdit()
    editor.setPlainText(text)
    cursor = editor.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    editor.setTextCursor(cursor)

    stub = SimpleNamespace(_editor=editor, _popup=SimpleNamespace(hide=lambda: None))
    SqlCompleter._insert(stub, completion)
    return editor.toPlainText()


def test_multiword_completion_replaces_already_typed_leading_word():
    assert _insert("SHOW PROCESSLIS", "SHOW PROCESSLIST") == "SHOW PROCESSLIST"


def test_three_word_completion_replaces_all_typed_leading_words():
    assert _insert("SHOW CREATE TABL", "SHOW CREATE TABLE") == "SHOW CREATE TABLE"


def test_multiword_completion_is_case_insensitive_and_normalizes_casing():
    assert _insert("show processlis", "SHOW PROCESSLIST") == "SHOW PROCESSLIST"


def test_multiword_completion_with_no_leading_word_typed_just_inserts_it():
    assert _insert("PROCESSLIS", "SHOW PROCESSLIST") == "SHOW PROCESSLIST"


def test_multiword_completion_does_not_eat_an_unrelated_preceding_word():
    # The preceding word doesn't match the completion's leading word, so
    # only the word actually being typed gets replaced — same safe
    # fallback as a single-word completion.
    assert _insert("WHERE PROCESSLIS", "SHOW PROCESSLIST") == "WHERE SHOW PROCESSLIST"


def test_single_word_completion_is_unaffected():
    assert _insert("SELEC", "SELECT") == "SELECT"


def test_order_by_two_word_completion():
    assert _insert("ORDER B", "ORDER BY") == "ORDER BY"


def test_multiword_snippet_body_is_unaffected_by_unrelated_trigger_text():
    # Snippets pass their (unrelated, multi-word) body to _insert, keyed
    # off a short trigger word (e.g. "ssel") — the backward word-matching
    # above must not eat previously-typed text just because the body
    # happens to be multi-word; it only extends the replacement span on an
    # actual case-insensitive word match.
    assert _insert("SELECT id FROM t WHERE ssel", "SELECT * FROM table_name") == \
        "SELECT id FROM t WHERE SELECT * FROM table_name"
