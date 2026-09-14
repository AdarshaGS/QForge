"""Regression tests for issue #294: verify behavior when the macOS
Keychain ownership-conflict recovery (credential_store._force_delete_
stale_item, used after an ad-hoc-signed rebuild makes an older build's
stored password un-overwritable — errSecInvalidOwnerEdit) itself fails,
or the keychain is otherwise unreachable (locked, no Secret Service, a
denied OS authorization prompt, ...).

credential_store.set_password() already returns False rather than
raising in every failure case. Traced every caller: they all funnel
through ConnectionDialog.save_connections() -> _sync_password(), which
already degrades gracefully — a failed set_password() falls back to
keeping the plaintext password in connections.json (so the connection
still works next launch, exactly as it did before credential_store
existed) and collects a label for _warn_keyring_unavailable(), which
shows a clear, actionable QMessageBox (naming the affected connection
and suggesting the Keychain Access fix) rather than connecting hanging
or silently proceeding with no password. No production code change
needed — these tests lock the already-correct behavior in, since it
had zero prior test coverage for this exact failure path.
"""
import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QMessageBox

from ui.connection_dialog import ConnectionDialog
from utils import credential_store

_app = QApplication.instance() or QApplication([])


@pytest.fixture
def isolated_dialog_factory(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_cwd = tmp_path / "cwd"
    fake_home.mkdir()
    fake_cwd.mkdir()

    monkeypatch.setattr(ConnectionDialog, "CONNECTION_FILE", str(tmp_path / "connections.json"))
    monkeypatch.setattr(ConnectionDialog, "LAST_CONNECTION_FILE", str(tmp_path / "last_connection.json"))
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(fake_home))
    monkeypatch.setattr(os, "getcwd", lambda: str(fake_cwd))

    return lambda: ConnectionDialog()


def test_sync_password_falls_back_to_plaintext_when_keychain_write_fails(monkeypatch, isolated_dialog_factory):
    dlg = isolated_dialog_factory()
    monkeypatch.setattr(credential_store, "set_password", lambda *a, **k: False)

    dlg._resolved_passwords[("conn-1", "db")] = "hunter2"
    failures = []

    result = dlg._sync_password("conn-1", "db", "My Connection", failures)

    assert result == "hunter2"  # plaintext kept, not silently dropped
    assert failures == ["My Connection"]


def test_sync_password_returns_blank_when_keychain_write_succeeds(monkeypatch, isolated_dialog_factory):
    dlg = isolated_dialog_factory()
    monkeypatch.setattr(credential_store, "set_password", lambda *a, **k: True)

    dlg._resolved_passwords[("conn-1", "db")] = "hunter2"
    failures = []

    result = dlg._sync_password("conn-1", "db", "My Connection", failures)

    assert result == ""  # not duplicated into connections.json when the keychain has it
    assert failures == []


def test_save_connections_keeps_plaintext_and_warns_when_keychain_unreachable(
    monkeypatch, isolated_dialog_factory
):
    dlg = isolated_dialog_factory()
    monkeypatch.setattr(credential_store, "set_password", lambda *a, **k: False)

    warnings = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        lambda self, title, text: warnings.append((title, text)),
    )

    dlg.connections = [{"id": "conn-1", "name": "Prod DB", "host": "db.internal"}]
    dlg._resolved_passwords[("conn-1", "db")] = "hunter2"

    dlg.save_connections()  # must not raise/hang

    with open(dlg.CONNECTION_FILE) as f:
        saved = json.load(f)
    assert saved[0]["password"] == "hunter2"  # connection still works next launch

    assert len(warnings) == 1
    title, text = warnings[0]
    assert title == "Password Not Stored Securely"
    assert "Prod DB" in text
    assert "Keychain Access" in text  # actionable, not a generic error


def test_save_connections_does_not_warn_when_keychain_write_succeeds(monkeypatch, isolated_dialog_factory):
    dlg = isolated_dialog_factory()
    monkeypatch.setattr(credential_store, "set_password", lambda *a, **k: True)

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a))

    dlg.connections = [{"id": "conn-1", "name": "Prod DB", "host": "db.internal"}]
    dlg._resolved_passwords[("conn-1", "db")] = "hunter2"

    dlg.save_connections()

    with open(dlg.CONNECTION_FILE) as f:
        saved = json.load(f)
    assert "password" not in saved[0] or saved[0]["password"] == ""
    assert warnings == []
