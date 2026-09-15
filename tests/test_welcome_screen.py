"""Regression tests for the startup landing screen (ui/welcome_screen.py):
its four exit actions (connect / add_new / skip / quit), the "show on
launch" preference toggle, and the MySQL/PostgreSQL-only scope."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from services import preferences
from ui.welcome_screen import WelcomeScreen, _PREF_KEY, _SUPPORTED_DBS

_app = QApplication.instance() or QApplication([])

_SAMPLE_CONNECTIONS = [
    {"name": "Local Dev", "type": "mysql", "host": "localhost", "environment": "local"},
    {"name": "Prod Reports", "type": "postgresql", "host": "db.internal", "environment": "production"},
]


@pytest.fixture
def isolated_screen_factory(tmp_path, monkeypatch):
    monkeypatch.setattr(preferences, "_FILE", str(tmp_path / "preferences.json"))

    def _make(connections=None):
        return WelcomeScreen(connections=connections if connections is not None else list(_SAMPLE_CONNECTIONS))

    return _make


def test_recent_connection_click_sets_connect_action_and_index(isolated_screen_factory):
    screen = isolated_screen_factory()
    screen._on_connect(1)
    assert screen.action == "connect"
    assert screen.selected_index == 1


def test_add_new_connection_sets_add_new_action_with_no_db_type(isolated_screen_factory):
    screen = isolated_screen_factory()
    screen._on_add_new(None)
    assert screen.action == "add_new"
    assert screen.chosen_db_type is None


def test_quick_create_pill_sets_add_new_action_with_db_type(isolated_screen_factory):
    screen = isolated_screen_factory()
    screen._on_add_new("PostgreSQL")
    assert screen.action == "add_new"
    assert screen.chosen_db_type == "PostgreSQL"


def test_skip_for_now_sets_skip_action(isolated_screen_factory):
    screen = isolated_screen_factory()
    screen._on_skip()
    assert screen.action == "skip"


def test_close_button_sets_quit_action(isolated_screen_factory):
    screen = isolated_screen_factory()
    screen._on_quit()
    assert screen.action == "quit"


def test_native_close_with_no_prior_action_defaults_to_quit(isolated_screen_factory):
    """The X button and 'Skip for now' both set `.action` before closing the
    dialog; the native window-close / Escape path doesn't, so closeEvent
    must default it to "quit" rather than leaving it None (issue: closing
    via the native controls used to silently fall through to the old
    connection-picker flow instead of quitting)."""
    screen = isolated_screen_factory()
    assert screen.action is None
    screen.close()
    assert screen.action == "quit"


def test_skip_is_not_overridden_by_close_event(isolated_screen_factory):
    """closeEvent only fills in a *missing* action — it must not clobber
    "skip" (or any other button-set action) with "quit"."""
    screen = isolated_screen_factory()
    screen._on_skip()
    screen.close()
    assert screen.action == "skip"


def test_only_mysql_and_postgresql_are_supported(isolated_screen_factory):
    assert _SUPPORTED_DBS == ["MySQL", "PostgreSQL"]


def test_show_on_launch_defaults_to_checked_when_no_preference_saved(isolated_screen_factory):
    screen = isolated_screen_factory()
    assert screen.show_on_launch_check.isChecked() is True


def test_unchecking_show_on_launch_persists_the_preference(isolated_screen_factory):
    screen = isolated_screen_factory()
    screen.show_on_launch_check.setChecked(False)
    assert preferences.get(_PREF_KEY) is False


def test_empty_connections_list_does_not_crash(isolated_screen_factory):
    screen = isolated_screen_factory(connections=[])
    assert screen.selected_index is None
