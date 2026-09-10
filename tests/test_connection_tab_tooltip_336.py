"""Regression tests for issue #336 follow-up: the server version used to be
appended to the connection tab label (e.g. "Local  ·  db  [MySQL 8.0.40]"),
crowding the tab bar. It's dropped from the label and moved into
connection_details_text(), which drives the tab's hover tooltip and the
"Copy Connection Details" context-menu action instead.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from ui.connection_panel import ConnectionPanel

_app = QApplication.instance() or QApplication([])


class _PanelStub:
    def __init__(self, config, server_version=""):
        self.config = config
        self._server_version = server_version


def test_label_omits_server_version():
    stub = _PanelStub({"name": "Local CIM", "database": "light_uat"}, server_version="MySQL 8.0.40")
    assert ConnectionPanel.label.fget(stub) == "Local CIM  ·  light_uat"


def test_connection_details_text_includes_everything_the_label_dropped():
    stub = _PanelStub(
        {
            "name": "Local CIM",
            "type": "mysql",
            "host": "127.0.0.1",
            "port": 3306,
            "user": "root",
            "database": "light_uat",
        },
        server_version="MySQL 8.0.40",
    )
    text = ConnectionPanel.connection_details_text(stub)
    assert "Local CIM" in text
    assert "Type: MYSQL" in text
    assert "Host: 127.0.0.1:3306" in text
    assert "User: root" in text
    assert "Database: light_uat" in text
    assert "Version: MySQL 8.0.40" in text


def test_connection_details_text_flags_read_only_and_environment():
    stub = _PanelStub(
        {"name": "Prod", "environment": "production", "read_only": True},
    )
    text = ConnectionPanel.connection_details_text(stub)
    assert "Environment: PRODUCTION DATABASE" in text
    assert "Read-only" in text


def test_connection_details_text_omits_blank_fields():
    stub = _PanelStub({"name": "Bare"})
    text = ConnectionPanel.connection_details_text(stub)
    assert text == "Bare"
