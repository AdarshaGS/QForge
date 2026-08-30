"""Shared pytest fixtures.

Every QMessageBox.* static call (critical/warning/information/question) is
a genuinely *blocking* modal — even under QT_QPA_PLATFORM=offscreen, which
still runs a real (if invisible) nested event loop with nothing able to
click it closed. A test that unexpectedly hits one (e.g. an unhandled
exception in application code surfacing as an error dialog, on a path the
test didn't intend to exercise) hangs the *entire* suite forever instead
of failing with a readable message — this happened once with
test_main_window_focus_after_connect.py's connection flow (a QMessageBox.
critical from an unexpected exception during ConnectionPanel construction,
not reproducible on demand — a rare race, not this test's own bug).

This autouse fixture replaces all four for the duration of every test
with non-blocking stand-ins that print what would have been shown and
return a sensible default, so any future unexpected dialog call surfaces
as fast, readable test output instead of a silent, indefinite freeze.
A test that explicitly wants to verify real QMessageBox behavior can
still monkeypatch it itself — that monkeypatch, applied later in the same
test, simply overrides this fixture's for its duration.
"""
import pytest


@pytest.fixture(autouse=True)
def _never_block_on_messagebox(monkeypatch):
    try:
        from PySide6.QtWidgets import QMessageBox
    except ImportError:
        # Mirrors the pytest.importorskip("PySide6") guard individual test
        # files already use — a non-Qt test shouldn't require PySide6 just
        # because this fixture is autouse.
        yield
        return

    def _stub(default):
        def _call(*args, **kwargs):
            title = args[1] if len(args) > 1 else kwargs.get("title", "")
            text = args[2] if len(args) > 2 else kwargs.get("text", "")
            print(f"\n[QMessageBox suppressed in test] {title}: {text}")
            return default
        return staticmethod(_call)

    monkeypatch.setattr(QMessageBox, "critical", _stub(QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "warning", _stub(QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "information", _stub(QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "question", _stub(QMessageBox.Yes))
    yield
