"""Regression tests for issue #282: window geometry/state was never saved
or restored — every launch hard-reset to resize(1600, 900), dropping a
previous maximize/fullscreen/custom size or position.

MainWindow._save_window_geometry()/_restore_window_geometry() are exercised
directly against a bare QMainWindow subclass (not a real MainWindow, whose
__init__ opens a connection-prompt dialog and is far more than this needs)
with services.preferences monkeypatched to an in-memory dict, so no real
app_data_dir() file is touched.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QMainWindow

import main
from main import MainWindow

_app = QApplication.instance() or QApplication([])


class _GeometryHost(QMainWindow):
    """Just the geometry save/restore behavior, without MainWindow's heavy
    __init__ (connection prompt, panels, session restore, ...)."""
    _GEOMETRY_PREF_KEY = MainWindow._GEOMETRY_PREF_KEY
    _save_window_geometry = MainWindow._save_window_geometry
    _restore_window_geometry = MainWindow._restore_window_geometry


@pytest.fixture
def fake_preferences(monkeypatch):
    store = {}
    monkeypatch.setattr(main.preferences, "get", lambda key, default=None: store.get(key, default))
    monkeypatch.setattr(main.preferences, "set", lambda key, value: store.__setitem__(key, value))
    return store


def test_restore_returns_false_when_nothing_saved(fake_preferences):
    win = _GeometryHost()
    assert win._restore_window_geometry() is False


def test_restore_returns_false_for_unparseable_saved_value(fake_preferences):
    fake_preferences[MainWindow._GEOMETRY_PREF_KEY] = "not-valid-hex!!"
    win = _GeometryHost()
    assert win._restore_window_geometry() is False


def test_save_then_restore_round_trips_geometry(fake_preferences):
    # Kept well within the offscreen QPA's 800x800 virtual screen (used in
    # this test environment) — a larger size gets clamped by Qt's own
    # keep-on-screen constraint in restoreGeometry(), which isn't what
    # this test is checking.
    saver = _GeometryHost()
    saver.resize(700, 500)
    saver.move(20, 20)
    saver._save_window_geometry()

    assert MainWindow._GEOMETRY_PREF_KEY in fake_preferences
    saved_hex = fake_preferences[MainWindow._GEOMETRY_PREF_KEY]
    assert isinstance(saved_hex, str)
    bytes.fromhex(saved_hex)  # must be valid hex

    restorer = _GeometryHost()
    assert restorer._restore_window_geometry() is True
    assert restorer.size() == saver.size()


def test_restore_falls_back_and_recenters_when_saved_position_is_off_screen(fake_preferences, monkeypatch):
    saver = _GeometryHost()
    saver.resize(800, 600)
    saver.move(50, 50)
    saver._save_window_geometry()

    restorer = _GeometryHost()

    class _FakeScreen:
        def geometry(self):
            from PySide6.QtCore import QRect
            return QRect(5000, 5000, 100, 100)  # nowhere near the saved position

    monkeypatch.setattr(QApplication.instance(), "screens", lambda: [_FakeScreen()])

    assert restorer._restore_window_geometry() is False
    assert restorer.pos().x() == 100 and restorer.pos().y() == 100
