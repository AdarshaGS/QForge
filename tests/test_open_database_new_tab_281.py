"""Regression tests for issue #281: picking a database from the Cmd+K
switcher now opens it as its own connection tab instead of switching this
panel's database in place. Two problems this fixes: (1) TableViewWidget
tabs share ConnectionPanel.config by reference, so mutating
config["database"] in place used to silently repoint an already-open
tab's next query at a different database with no indication; (2) with
several databases on the same server open at once, there was no way to
tell which tab was which. Both fixed by never mutating an existing panel's
database at all — a database switch always becomes a distinct panel/tab,
refocusing one already open for the same connection+database instead of
duplicating it.
"""
import os
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

import main as main_mod
from ui.connection_panel import ConnectionPanel

_app = QApplication.instance() or QApplication([])


class _PanelSignalStub(QObject):
    """Just enough of ConnectionPanel for _open_selected_database to run:
    a real QObject (so its Signal actually works) carrying only .config."""
    open_database_in_new_tab = Signal(dict)

    def __init__(self, config):
        super().__init__()
        self.config = config


def test_open_selected_database_emits_a_copy_with_new_db_and_no_schema():
    original = {"id": "conn-1", "name": "Local", "database": "old_db", "schema": "public"}
    stub = _PanelSignalStub(original)
    captured = []
    stub.open_database_in_new_tab.connect(captured.append)

    ConnectionPanel._open_selected_database(stub, "new_db")

    assert len(captured) == 1
    new_config = captured[0]
    assert new_config["database"] == "new_db"
    assert "schema" not in new_config
    # The original config is untouched — a copy went out, not a mutation
    # of the dict every already-open TableViewWidget tab also holds.
    assert stub.config["database"] == "old_db"
    assert stub.config["schema"] == "public"


def test_open_selected_database_is_a_no_op_for_the_current_database():
    stub = _PanelSignalStub({"id": "conn-1", "database": "same_db"})
    captured = []
    stub.open_database_in_new_tab.connect(captured.append)

    ConnectionPanel._open_selected_database(stub, "same_db")

    assert captured == []


class _LabelStub:
    def __init__(self, config):
        self.config = config


def test_label_includes_the_database_name():
    stub = _LabelStub({"name": "Local CIM", "database": "light_uat"})
    assert ConnectionPanel.label.fget(stub) == "Local CIM  ·  light_uat"


def test_label_omits_database_suffix_when_none_set():
    stub = _LabelStub({"name": "Local CIM"})
    assert ConnectionPanel.label.fget(stub) == "Local CIM"


class _FakePanel:
    def __init__(self, config):
        self.config = config


class _FakeMainWindow:
    def __init__(self, panels):
        self._panels = panels
        self.connected = []
        self.conn_tab_bar = types.SimpleNamespace(setCurrentIndex=self._focus)
        self.focused = []

    def _focus(self, idx):
        self.focused.append(idx)

    def _connect_and_add_panel(self, config):
        self.connected.append(config)
        return True


def test_reuses_an_already_open_tab_for_the_same_connection_and_database():
    p0 = _FakePanel({"id": "conn-1", "database": "db_a"})
    p1 = _FakePanel({"id": "conn-1", "database": "db_b"})
    fake = _FakeMainWindow([p0, p1])

    main_mod.MainWindow._open_database_in_new_tab(fake, {"id": "conn-1", "database": "db_b"})

    assert fake.focused == [1]
    assert fake.connected == []


def test_opens_a_new_panel_when_not_already_open():
    p0 = _FakePanel({"id": "conn-1", "database": "db_a"})
    fake = _FakeMainWindow([p0])

    new_config = {"id": "conn-1", "database": "db_c"}
    main_mod.MainWindow._open_database_in_new_tab(fake, new_config)

    assert fake.focused == []
    assert fake.connected == [new_config]


def test_same_database_name_on_a_different_connection_is_not_treated_as_open():
    p0 = _FakePanel({"id": "conn-1", "database": "shared_name"})
    fake = _FakeMainWindow([p0])

    new_config = {"id": "conn-2", "database": "shared_name"}
    main_mod.MainWindow._open_database_in_new_tab(fake, new_config)

    assert fake.focused == []
    assert fake.connected == [new_config]
