"""Regression tests for the annotated first-run empty-state hint (issue
#164): shown only when there are zero saved connections and the hint
hasn't been dismissed, hidden by adding a connection or by dismissing,
and the dismissal persists across a fresh dialog instance."""
import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from ui.connection_dialog import ConnectionDialog, onboarding

_app = QApplication.instance() or QApplication([])


@pytest.fixture
def isolated_dialog_factory(tmp_path, monkeypatch):
    """Builds ConnectionDialog instances isolated to tmp_path — patches
    app_data_dir()-derived class/module attributes directly
    (CONNECTION_FILE, LAST_CONNECTION_FILE, onboarding._FILE) rather than
    app_data_dir() itself, since each is computed once at import time and
    patching the function afterward wouldn't reach an already-imported
    value. Also patches the legacy-migration lookup paths
    (os.path.expanduser/os.getcwd), so a real connections.json or
    ~/connections.json on the dev machine can never leak into these tests
    (it does, on at least one real dev machine)."""
    fake_home = tmp_path / "home"
    fake_cwd = tmp_path / "cwd"
    fake_home.mkdir()
    fake_cwd.mkdir()

    connection_file = str(tmp_path / "connections.json")
    monkeypatch.setattr(ConnectionDialog, "CONNECTION_FILE", connection_file)
    monkeypatch.setattr(ConnectionDialog, "LAST_CONNECTION_FILE", str(tmp_path / "last_connection.json"))
    monkeypatch.setattr(onboarding, "_FILE", str(tmp_path / "onboarding.json"))
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(fake_home))
    monkeypatch.setattr(os, "getcwd", lambda: str(fake_cwd))

    def _make():
        return ConnectionDialog()

    return _make


def test_hint_shown_on_fresh_install_with_no_connections_file(isolated_dialog_factory):
    dlg = isolated_dialog_factory()
    assert dlg.connections == []
    assert dlg._first_run_hint.isHidden() is False


def test_hint_hidden_when_a_connection_exists(isolated_dialog_factory, tmp_path):
    conn_file = str(tmp_path / "connections.json")
    with open(conn_file, "w") as f:
        json.dump([{"id": "x", "name": "test", "type": "mysql"}], f)

    dlg = isolated_dialog_factory()
    assert len(dlg.connections) == 1
    assert dlg._first_run_hint.isHidden() is True


def test_dismiss_hides_hint_immediately(isolated_dialog_factory):
    dlg = isolated_dialog_factory()
    assert dlg._first_run_hint.isHidden() is False

    dlg._dismiss_first_run_hint()
    assert dlg._first_run_hint.isHidden() is True


def test_dismissal_persists_across_a_fresh_dialog_instance(isolated_dialog_factory):
    """Regression guard: load_connections() has an early return when
    CONNECTION_FILE doesn't exist yet (the actual fresh-install case) —
    that path must still update hint visibility, not skip it."""
    dlg = isolated_dialog_factory()
    dlg._dismiss_first_run_hint()

    dlg2 = isolated_dialog_factory()
    assert dlg2.connections == []
    assert dlg2._first_run_hint.isHidden() is True
