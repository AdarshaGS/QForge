"""Issue #280: session.json must never carry plaintext DB/SSH passwords.

MainWindow.save_session() writes panel.config (which holds the resolved
plaintext password(s) for the live connection) straight to session.json —
a file with no keychain protection, unlike connections.json. These tests
exercise MainWindow._sanitized_session_config() directly, since it's a
pure @staticmethod and doesn't require building a full MainWindow/QApplication.
"""
import pytest

pytest.importorskip("PySide6")

from main import MainWindow


def test_strips_top_level_db_password():
    config = {"id": "c1", "host": "db.internal", "password": "hunter2"}
    sanitized = MainWindow._sanitized_session_config(config)

    assert "password" not in sanitized
    assert config["password"] == "hunter2"  # original untouched


def test_strips_ssh_tunnel_password_without_dropping_other_ssh_fields():
    config = {
        "id": "c1",
        "password": "hunter2",
        "ssh_tunnel": {
            "enabled": True,
            "host": "bastion.internal",
            "password": "s3cr3t",
        },
    }
    sanitized = MainWindow._sanitized_session_config(config)

    assert "password" not in sanitized
    assert sanitized["ssh_tunnel"]["password"] is None
    assert sanitized["ssh_tunnel"]["enabled"] is True
    assert sanitized["ssh_tunnel"]["host"] == "bastion.internal"
    # Original config (and its nested dict) must be untouched.
    assert config["ssh_tunnel"]["password"] == "s3cr3t"


def test_no_password_fields_is_a_no_op():
    config = {"id": "c1", "host": "db.internal"}
    sanitized = MainWindow._sanitized_session_config(config)

    assert sanitized == config


def test_ssh_tunnel_without_password_key_is_left_alone():
    config = {"id": "c1", "ssh_tunnel": {"enabled": False}}
    sanitized = MainWindow._sanitized_session_config(config)

    assert sanitized["ssh_tunnel"] == {"enabled": False}
