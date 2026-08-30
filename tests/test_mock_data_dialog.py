"""Dialog-level regressions for the Mock Data Generator (issue #180):
switching a column's generator to "Omit" must actually drop it from the
generated INSERT, a NOT NULL column set to "Always NULL" must warn, and the
Preview grid must never render more rows than the row count that actually
freezes the UI, regardless of how many rows were requested."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from ui.mock_data_dialog import _MAX_ROW_COUNT, _PREVIEW_ROW_CAP, MockDataDialog

_app = QApplication.instance() or QApplication([])


def _col(field, sql_type, nullable="YES"):
    return {"Field": field, "Type": sql_type, "Null": nullable, "Default": None}


def _make_dialog(columns=None, primary_keys=None, dialect="mysql"):
    columns = columns or [
        _col("id", "int(11)", nullable="NO"),
        _col("name", "varchar(50)"),
        _col("email", "varchar(255)"),
    ]
    return MockDataDialog(
        "users", columns, primary_keys or ["id"], [], [], dialect=dialect, parent=None,
    )


def test_switching_generator_to_omit_excludes_column_and_locks_checkbox():
    dlg = _make_dialog()
    assert dlg._specs["name"].include is True  # sanity: starts included

    dlg._generator_combos["name"].setCurrentIndex(
        dlg._generator_combos["name"].findData("omit")
    )

    assert dlg._specs["name"].include is False
    assert dlg._include_checks["name"].isChecked() is False
    assert dlg._include_checks["name"].isEnabled() is False

    dlg._regenerate()
    sql = dlg.get_sql()
    assert "`name`" not in sql
    assert "`email`" in sql  # the other column is still generated


def test_switching_away_from_omit_restores_included_state():
    dlg = _make_dialog()
    name_combo = dlg._generator_combos["name"]

    name_combo.setCurrentIndex(name_combo.findData("omit"))
    assert dlg._specs["name"].include is False

    name_combo.setCurrentIndex(name_combo.findData("full_name"))
    assert dlg._specs["name"].include is True
    assert dlg._include_checks["name"].isChecked() is True
    assert dlg._include_checks["name"].isEnabled() is True


def test_switching_away_from_omit_does_not_override_a_deliberate_exclude():
    dlg = _make_dialog()
    name_combo = dlg._generator_combos["name"]

    # User deliberately excludes the column first, for their own reason...
    dlg._include_checks["name"].setChecked(False)
    assert dlg._specs["name"].include is False
    # ...then picks "omit" (still excluded, now locked)...
    name_combo.setCurrentIndex(name_combo.findData("omit"))
    assert dlg._include_checks["name"].isEnabled() is False
    # ...then changes their mind about the generator. Should stay excluded,
    # not be forced back on just because "omit" is no longer selected.
    name_combo.setCurrentIndex(name_combo.findData("full_name"))
    assert dlg._specs["name"].include is False
    assert dlg._include_checks["name"].isChecked() is False
    assert dlg._include_checks["name"].isEnabled() is True


def test_not_null_column_set_to_always_null_warns():
    columns = [
        _col("id", "int(11)", nullable="NO"),
        _col("required_field", "varchar(50)", nullable="NO"),
    ]
    dlg = _make_dialog(columns=columns, primary_keys=["id"])
    combo = dlg._generator_combos["required_field"]
    combo.setCurrentIndex(combo.findData("null"))

    dlg._regenerate()
    assert "required_field" in dlg._warning_label.text()
    assert "NOT NULL" in dlg._warning_label.text() or "Always NULL" in dlg._warning_label.text()


def test_row_count_is_capped_to_a_safe_maximum():
    dlg = _make_dialog()
    assert dlg._row_count_spin.maximum() == _MAX_ROW_COUNT


def test_preview_grid_never_renders_more_than_the_cap():
    dlg = _make_dialog(columns=[_col("id", "int(11)", nullable="NO"), _col("name", "varchar(50)")],
                        primary_keys=["id"])
    requested = _PREVIEW_ROW_CAP + 50
    dlg._row_count_spin.setValue(requested)
    dlg._regenerate()

    assert dlg._preview_table.rowCount() == _PREVIEW_ROW_CAP
    # The full row count still makes it into the generated SQL — only the
    # on-screen grid is capped.
    assert dlg.get_sql().count("INSERT INTO") == requested
