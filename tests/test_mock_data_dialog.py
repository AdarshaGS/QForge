"""Dialog-level regressions for the Mock Data Generator (issue #180):
switching a column's generator to "Omit" must actually drop it from the
generated INSERT, a NOT NULL column set to "Always NULL" must warn, and the
Preview grid must never render more rows than the row count that actually
freezes the UI, regardless of how many rows were requested."""
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from services.mock_data_generator import build_dependency_chain
from ui.mock_data_dialog import (
    _BACKGROUND_ROW_THRESHOLD, _MAX_ROW_COUNT, _PREVIEW_ROW_CAP, MockDataDialog,
)

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


# ─── Dependency-ordered multi-table generation (issue #215) ───────────────

def test_no_ancestor_panel_when_table_has_no_dependencies():
    dlg = _make_dialog()
    assert dlg._include_deps_check is None
    assert dlg._ancestor_panel is None


def test_ancestor_checkbox_and_panel_appear_when_dependencies_exist():
    chain = build_dependency_chain(
        {"orders": [{"column": "customer_id", "ref_table": "customers", "ref_column": "id"}],
         "customers": []},
        "orders",
    )
    dlg = MockDataDialog(
        "orders",
        [_col("id", "int(11)", nullable="NO"), _col("customer_id", "int(11)")],
        ["id"], [{"column": "customer_id", "ref_table": "customers", "ref_column": "id"}], [],
        dialect="mysql", dependency_chain=chain,
        schema_fetcher=lambda t: ([_col("id", "int(11)", nullable="NO")], ["id"], [], [], set(), {}),
        existing_row_count_fetcher=lambda t: 0,
        pk_offset_fetcher=lambda t, c: 1,
        parent=None,
    )
    assert dlg._include_deps_check is not None
    assert dlg._ancestor_panel is not None
    assert dlg._ancestor_panel.isVisible() is False  # opt-in, unchecked by default
    assert dlg.generated_pk_columns() == []  # single-table path until the box is checked


def test_checking_include_dependents_generates_parent_rows_and_wires_fk_pool():
    chain = build_dependency_chain(
        {"orders": [{"column": "customer_id", "ref_table": "customers", "ref_column": "id"}],
         "customers": []},
        "orders",
    )
    dlg = MockDataDialog(
        "orders",
        [_col("id", "int(11)", nullable="NO"), _col("customer_id", "int(11)")],
        ["id"], [{"column": "customer_id", "ref_table": "customers", "ref_column": "id"}], [],
        dialect="mysql", dependency_chain=chain,
        schema_fetcher=lambda t: ([_col("id", "int(11)", nullable="NO")], ["id"], [], [], set(), {}),
        existing_row_count_fetcher=lambda t: 0,  # empty — will generate
        pk_offset_fetcher=lambda t, c: 1,
        parent=None,
    )
    dlg._row_count_spin.setValue(5)
    dlg._include_deps_check.setChecked(True)

    sql = dlg.get_sql()
    assert sql.index("`customers`") < sql.index("`orders`")  # parent INSERT precedes child's
    # Both tables were generated with a lone integer PK — bumping both
    # sequences post-insert is idempotent/harmless even for the root,
    # which nothing in this chain actually references.
    assert dlg.generated_pk_columns() == [("customers", "id"), ("orders", "id")]


def test_ancestor_with_existing_rows_defaults_to_reuse_not_generate():
    chain = build_dependency_chain(
        {"orders": [{"column": "customer_id", "ref_table": "customers", "ref_column": "id"}],
         "customers": []},
        "orders",
    )
    sampled = []

    def fk_sampler(ref_table, ref_column, limit=200):
        sampled.append((ref_table, ref_column))
        return [7, 8, 9]

    dlg = MockDataDialog(
        "orders",
        [_col("id", "int(11)", nullable="NO"), _col("customer_id", "int(11)")],
        ["id"], [{"column": "customer_id", "ref_table": "customers", "ref_column": "id"}], [],
        dialect="mysql", fk_sampler=fk_sampler, dependency_chain=chain,
        schema_fetcher=lambda t: ([_col("id", "int(11)", nullable="NO")], ["id"], [], [], set(), {}),
        existing_row_count_fetcher=lambda t: 42,  # already has real data — reuse it
        pk_offset_fetcher=lambda t, c: 1,
        parent=None,
    )
    dlg._include_deps_check.setChecked(True)

    sql = dlg.get_sql()
    assert "`customers`" not in sql  # no fresh rows generated for the reused ancestor
    # Sampled once and cached — the root's own initial regenerate (at
    # dialog construction, before the checkbox is checked) and chain mode's
    # fallback for the reused ancestor share one cache entry, not two queries.
    assert sampled == [("customers", "id")]
    assert dlg.generated_pk_columns() == [("orders", "id")]  # root only — customers was reused, not generated


# ─── Background generation + progress UI (issue #217) ─────────────────────

def test_large_row_count_generates_on_background_thread():
    dlg = _make_dialog(columns=[_col("id", "int(11)", nullable="NO"), _col("name", "varchar(50)")],
                        primary_keys=["id"])
    requested = _BACKGROUND_ROW_THRESHOLD + 50
    dlg._row_count_spin.setValue(requested)
    dlg._regenerate()

    assert dlg._gen_thread is not None  # dispatched to a background thread, not run inline
    assert dlg._regen_btn.isEnabled() is False
    assert dlg._progress_bar.isVisible() is True

    deadline = time.time() + 10
    while dlg._gen_thread is not None and time.time() < deadline:
        QApplication.processEvents()

    assert dlg._gen_thread is None
    assert dlg._regen_btn.isEnabled() is True
    assert dlg._progress_bar.isVisible() is False
    assert dlg.get_sql().count("INSERT INTO") == requested


def test_chain_mode_warns_when_not_null_fk_column_has_empty_pool():
    chain = build_dependency_chain(
        {"orders": [{"column": "customer_id", "ref_table": "customers", "ref_column": "id"}],
         "customers": []},
        "orders",
    )
    dlg = MockDataDialog(
        "orders",
        [_col("id", "int(11)", nullable="NO"), _col("customer_id", "int(11)", nullable="NO")],
        ["id"], [{"column": "customer_id", "ref_table": "customers", "ref_column": "id"}], [],
        dialect="mysql", dependency_chain=chain,
        schema_fetcher=lambda t: ([_col("id", "int(11)", nullable="NO")], ["id"], [], [], set(), {}),
        existing_row_count_fetcher=lambda t: 0,
        pk_offset_fetcher=lambda t, c: 1,
        parent=None,
    )
    # Force the ancestor's own row count to 0 via its spinner — nothing to
    # populate the child's FK pool with, and the column is NOT NULL.
    dlg._include_deps_check.setChecked(True)
    dlg._ancestor_row_spins["customers"].setValue(0)

    assert "customer_id" in dlg._warning_label.text()
    assert "NOT NULL" in dlg._warning_label.text()
