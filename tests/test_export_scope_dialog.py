"""Tests for the per-table Structure/Content/Drop export grid (issue #157),
replacing the old single global structure/data/both radio group."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from ui.export_scope_dialog import ExportScopeDialog

_app = QApplication.instance() or QApplication([])


def test_defaults_are_structure_and_content_but_not_drop():
    dlg = ExportScopeDialog(["users", "orders"])
    opts = dlg.table_options()
    assert opts["users"] == {"structure": True, "content": True, "drop": False}
    assert opts["orders"] == {"structure": True, "content": True, "drop": False}


def test_single_table_still_shows_one_row():
    dlg = ExportScopeDialog(["users"])
    assert dlg.table_options() == {"users": {"structure": True, "content": True, "drop": False}}


def test_table_with_all_boxes_unchecked_is_excluded():
    dlg = ExportScopeDialog(["users", "orders"])
    for cb in dlg._table_checks["users"].values():
        cb.setChecked(False)
    opts = dlg.table_options()
    assert "users" not in opts
    assert "orders" in opts


def test_per_column_all_none_toggle_applies_to_every_table():
    dlg = ExportScopeDialog(["users", "orders", "products"])
    dlg._set_column("drop", True)
    assert all(cb.isChecked() for t in dlg._table_checks.values() for k, cb in t.items() if k == "drop")

    dlg._set_column("structure", False)
    assert not any(cb.isChecked() for t in dlg._table_checks.values() for k, cb in t.items() if k == "structure")


def test_advanced_options_defaults():
    dlg = ExportScopeDialog(["users"])
    assert dlg.blob_as_hex() is True
    assert dlg.use_bom() is False
    assert dlg.gzip_output() is False
    assert dlg.batch_kib() is None


def test_batch_kib_only_set_when_toggle_checked():
    dlg = ExportScopeDialog(["users"])
    dlg._batch_spin.setValue(128)
    assert dlg.batch_kib() is None  # checkbox still off

    dlg._batch_cb.setChecked(True)
    assert dlg.batch_kib() == 128


def test_defaults_to_sql_tab():
    dlg = ExportScopeDialog(["users"])
    assert dlg.export_format() == "sql"


def test_csv_tab_only_content_is_relevant():
    dlg = ExportScopeDialog(["users"])
    dlg._tab_bar.setCurrentIndex(1)
    assert dlg.export_format() == "csv"
    assert dlg.table_options() == {"users": {"structure": False, "content": True, "drop": False}}
    assert not dlg._table_checks["users"]["structure"].isEnabled()
    assert not dlg._table_checks["users"]["drop"].isEnabled()
    assert dlg._table_checks["users"]["content"].isEnabled()


def test_xml_tab_only_content_is_relevant():
    dlg = ExportScopeDialog(["users"])
    dlg._tab_bar.setCurrentIndex(2)
    assert dlg.export_format() == "xml"
    assert dlg.table_options() == {"users": {"structure": False, "content": True, "drop": False}}


def test_dot_tab_only_structure_is_relevant():
    dlg = ExportScopeDialog(["users"])
    dlg._tab_bar.setCurrentIndex(3)
    assert dlg.export_format() == "dot"
    assert dlg.table_options() == {"users": {"structure": True, "content": False, "drop": False}}
    assert dlg._table_checks["users"]["structure"].isEnabled()
    assert not dlg._table_checks["users"]["content"].isEnabled()
    assert not dlg._table_checks["users"]["drop"].isEnabled()


def test_switching_back_to_sql_tab_restores_defaults():
    dlg = ExportScopeDialog(["users"])
    dlg._tab_bar.setCurrentIndex(1)  # csv: structure/drop forced off
    dlg._tab_bar.setCurrentIndex(0)  # back to sql
    assert dlg.table_options() == {"users": {"structure": True, "content": True, "drop": False}}


def test_non_sql_tabs_disable_batching_and_only_sql_keeps_it():
    dlg = ExportScopeDialog(["users"])
    dlg._batch_cb.setChecked(True)
    assert dlg.batch_kib() == 64

    dlg._tab_bar.setCurrentIndex(1)  # csv
    assert dlg.batch_kib() is None
    assert not dlg._batch_cb.isEnabled()

    dlg._tab_bar.setCurrentIndex(3)  # dot
    assert not dlg._blob_hex_cb.isEnabled()
    assert not dlg._bom_cb.isEnabled()
    assert dlg._gzip_cb.isEnabled()


def test_auto_increment_and_strip_generated_default_on_mysql():
    dlg = ExportScopeDialog(["users"], dialect="mysql")
    assert dlg.include_auto_increment() is True
    assert dlg.strip_generated_columns() is False
    assert dlg._auto_increment_cb.isEnabled()
    assert dlg._strip_generated_cb.isEnabled()


def test_auto_increment_disabled_on_postgresql_and_sqlite():
    for dialect in ("postgresql", "sqlite"):
        dlg = ExportScopeDialog(["users"], dialect=dialect)
        assert not dlg._auto_increment_cb.isEnabled()
        assert dlg.include_auto_increment() is False


def test_strip_generated_disabled_only_on_postgresql():
    dlg = ExportScopeDialog(["users"], dialect="postgresql")
    assert not dlg._strip_generated_cb.isEnabled()

    dlg = ExportScopeDialog(["users"], dialect="sqlite")
    assert dlg._strip_generated_cb.isEnabled()


def test_auto_increment_and_strip_generated_disabled_outside_sql_tab():
    dlg = ExportScopeDialog(["users"], dialect="mysql")
    dlg._tab_bar.setCurrentIndex(1)  # csv
    assert not dlg._auto_increment_cb.isEnabled()
    assert not dlg._strip_generated_cb.isEnabled()
    assert dlg.include_auto_increment() is False

    dlg._tab_bar.setCurrentIndex(0)  # back to sql: dialect still supports both
    assert dlg._auto_increment_cb.isEnabled()
    assert dlg.include_auto_increment() is True


def test_search_hides_non_matching_table_rows():
    dlg = ExportScopeDialog(["users", "orders", "order_items"])
    dlg._search_edit.setText("order")
    assert dlg._table_labels["users"].isHidden() is True
    assert dlg._table_labels["orders"].isHidden() is False
    assert dlg._table_labels["order_items"].isHidden() is False

    dlg._search_edit.setText("")
    assert dlg._table_labels["users"].isHidden() is False


def test_select_all_checks_relevant_columns_for_visible_rows_only():
    dlg = ExportScopeDialog(["users", "orders"])
    for cb in dlg._table_checks["users"].values():
        cb.setChecked(False)
    for cb in dlg._table_checks["orders"].values():
        cb.setChecked(False)

    dlg._search_edit.setText("order")
    dlg._bulk_select(True)

    opts = dlg.table_options()
    assert "users" not in opts  # left unchecked, filtered out of the bulk action
    assert opts["orders"] == {"structure": True, "content": True, "drop": True}


def test_select_none_unchecks_relevant_columns_for_visible_rows_only():
    dlg = ExportScopeDialog(["users", "orders"])
    dlg._search_edit.setText("users")
    dlg._bulk_select(False)

    opts = dlg.table_options()
    assert "users" not in opts
    assert opts["orders"] == {"structure": True, "content": True, "drop": False}  # untouched default


def test_invert_selection_flips_relevant_columns_for_visible_rows_only():
    dlg = ExportScopeDialog(["users", "orders"])
    dlg._search_edit.setText("users")
    dlg._invert_selection()

    opts = dlg.table_options()
    assert opts["users"] == {"structure": False, "content": False, "drop": True}
    assert opts["orders"] == {"structure": True, "content": True, "drop": False}  # untouched


def test_bulk_select_only_touches_columns_relevant_to_current_format():
    dlg = ExportScopeDialog(["users"])
    dlg._tab_bar.setCurrentIndex(1)  # csv: only "content" is relevant
    dlg._bulk_select(False)

    checks = dlg._table_checks["users"]
    assert checks["content"].isChecked() is False
    # structure/drop are disabled (unchecked by _on_format_changed) and
    # untouched by the bulk action itself, which only iterates csv's
    # relevant column set.
    assert checks["structure"].isEnabled() is False
